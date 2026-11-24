"""Meeting brief generator with per-attendee watermarks, preferences, and selective LLM fallback."""

import json
import re
import sqlite3
from datetime import date, datetime
from typing import Any

from src.claims import get_claim_changes_in_window, is_unverified_or_hedged_clause
from src.llm import LLMClient
from src.memory import get_person_preferences
from src.promises import get_fulfilled_promises_in_window, get_open_promises
from src.retrieval import search_hybrid
from src.security import (
    _get_forgotten_entries,
    _text_matches_forgotten,
    get_current_day,
    get_person_org,
    get_person_row,
    validate_brief_list,
    visible_records_params,
    visible_records_where_clause,
)
from src.tasks_engine import get_task_changes_in_window
from src.time_utils import parse_explicit_date, utc_to_local_date
from src.usage import BRIEF_TOKEN_LIMIT, UsageTracker, pack_records_within_budget

_STOPWORDS = frozenset(
    {
        "the", "a", "an", "is", "are", "was", "were", "from", "to", "for",
        "of", "in", "on", "at", "by", "with", "and", "or", "per", "phase",
    }
)

_BRIEF_SYSTEM_PROMPT = (
    "You are extracting concise factual changes for a workplace client meeting brief. "
    "Given <workplace_record> items since the attendee's last meeting for that client, "
    "identify only concrete substantive changes (blockers, status updates, deadline shifts, "
    "ownership changes, scope decisions). Ignore casual chatter, small talk, greetings, newsletters, "
    "tentative speculation/hedges, and restatements of already-known baseline docs. "
    "Every claim must be strictly grounded in the provided XML text and cite exact record IDs. "
    'Return JSON only: {"changed": [{"text": "...", "kind": "blocked"|"decision"|"owner"|"date"|"status", "sources": ["record_id"]}]} '
    "or {\"changed\": []} if nothing substantive changed."
)


def _load_people_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT person_id, name FROM people").fetchall()
    return {str(r["person_id"]): str(r["name"]) for r in rows}


def _load_clients_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT client_id, name FROM clients").fetchall()
    return {str(r["client_id"]): str(r["name"]) for r in rows}


def _format_person_name(raw_val: str | None, people_map: dict[str, str]) -> str:
    txt = str(raw_val or "").strip()
    if not txt:
        return ""
    if txt in people_map:
        return people_map[txt]
    if ":" in txt:
        prefix, rest = txt.split(":", 1)
        if prefix.strip() in people_map:
            return people_map[prefix.strip()]
        return rest.strip()
    return txt


def _format_short_date(raw_val: str | None, default_year: int | None = None) -> str:
    txt = str(raw_val or "").strip()
    if not txt:
        return ""
    fallback_y = default_year if default_year is not None else date.today().year
    iso = txt if re.fullmatch(r"\d{4}-\d{2}-\d{2}", txt) else parse_explicit_date(txt, fallback_y)
    if iso:
        try:
            d = date.fromisoformat(iso)
            return f"{d.day} {d.strftime('%b')}"
        except ValueError:
            pass
    return txt


def _clean_blocked_reason(body: str) -> str:
    txt = re.sub(r"^\s*blocked\s*[:\-]?\s*", "", body or "", flags=re.IGNORECASE).strip()
    return txt.rstrip(".")


def _content_tokens(text: str) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(w) >= 3 and w not in _STOPWORDS
    }


def _day1_anchor_date(conn: sqlite3.Connection) -> str:
    """Return the canonical UTC start date of Day 1 using majority voting across day=1 records."""
    row = conn.execute(
        """
        SELECT SUBSTR(at_utc, 1, 10) AS d
        FROM records
        WHERE day = 1 AND at_utc != ''
        GROUP BY d
        ORDER BY COUNT(*) DESC, d ASC
        LIMIT 1
        """
    ).fetchone()
    if row and row[0]:
        return str(row[0])
    return "2026-11-02"


def _compute_since_day(
    conn: sqlite3.Connection,
    person_id: str,
    person_org: str,
    person_zone: str,
    client_id: str | None,
    meeting_date: str,
    current_day: int,
) -> int:
    """Determine first unbriefed record day for (person_id, client_id) prior to meeting_date."""
    if not client_id:
        return 1

    where = visible_records_where_clause("r", with_utc_cutoff=False)
    params = visible_records_params(person_id, person_org, current_day)
    params["cid"] = client_id
    rows = conn.execute(
        f"""
        SELECT r.id, r.event_id, r.start_utc, r.attendees_json, r.author
        FROM records r
        WHERE r.type = 'event'
          AND r.client = :cid
          AND r.start_utc IS NOT NULL
          AND {where}
        ORDER BY r.start_utc ASC
        """,
        params,
    ).fetchall()

    prev_local_dates: list[str] = []
    for row in rows:
        att = []
        if row["attendees_json"]:
            try:
                att = json.loads(row["attendees_json"])
            except ValueError:
                att = []
        if person_id not in att and str(row["author"]) != person_id:
            continue
        local_d = utc_to_local_date(str(row["start_utc"]), person_zone)
        if local_d < meeting_date:
            prev_local_dates.append(local_d)

    if not prev_local_dates:
        return 1

    last_prev_date = max(prev_local_dates)
    wm = conn.execute(
        """
        SELECT MAX(prepared_day)
        FROM brief_watermarks
        WHERE person = ? AND client = ? AND meeting_date = ?
        """,
        (person_id, client_id, last_prev_date),
    ).fetchone()
    if wm and wm[0] is not None:
        return max(1, int(wm[0]) + 1)

    cached_prev = conn.execute(
        "SELECT MAX(day) FROM brief_cache WHERE person = ? AND date = ?",
        (person_id, last_prev_date),
    ).fetchone()
    if cached_prev and cached_prev[0] is not None:
        return max(1, int(cached_prev[0]) + 1)

    anchor = _day1_anchor_date(conn)
    try:
        prev_prepared_day = (date.fromisoformat(last_prev_date) - date.fromisoformat(anchor)).days
    except ValueError:
        return 1
    return max(1, prev_prepared_day + 1)


def _format_task_change(tc: dict[str, Any], people_map: dict[str, str]) -> dict[str, Any]:
    title = str(tc["title"]).strip()
    field = str(tc["field"])
    from_val = tc.get("from_val")
    to_val = str(tc.get("to_val") or "").strip()
    rid = str(tc["record_id"])
    at_utc = str(tc["at_utc"])

    if tc.get("kind") == "blocked":
        reason = _clean_blocked_reason(str(tc.get("body") or ""))
        text = f"{title} is blocked ({reason})" if reason else f"{title} is blocked"
        return {
            "text": text,
            "sources": [rid],
            "prio": 1,
            "kind": "blocked",
            "at_utc": at_utc,
            "title": title,
            "new_val": title,
        }

    if field == "status":
        if to_val == "done":
            text = f"{title} is done"
        elif to_val == "cancelled":
            text = f"{title} was cancelled"
        else:
            text = f"{title} status changed to {to_val}"
        return {
            "text": text,
            "sources": [rid],
            "prio": 2,
            "kind": "status",
            "at_utc": at_utc,
            "title": title,
            "new_val": to_val,
        }

    if field == "due":
        old_d = _format_short_date(from_val)
        new_d = _format_short_date(to_val)
        text = (
            f"{title} due date moved from {old_d} to {new_d}"
            if old_d and old_d != new_d
            else f"{title} due date is {new_d}"
        )
        return {
            "text": text,
            "sources": [rid],
            "prio": 2,
            "kind": "date",
            "at_utc": at_utc,
            "title": title,
            "new_val": new_d,
        }

    # field == "owner"
    old_name = _format_person_name(from_val, people_map)
    new_name = _format_person_name(to_val, people_map)
    text = (
        f"{title} now owned by {new_name} (was {old_name})"
        if old_name and old_name != new_name
        else f"{title} now owned by {new_name}"
    )
    return {
        "text": text,
        "sources": [rid],
        "prio": 2,
        "kind": "owner",
        "at_utc": at_utc,
        "title": title,
        "new_val": new_name,
    }


def _topic_label(subject_key: str, client_id: str | None) -> str:
    slug = subject_key.split(":", 1)[-1]
    if slug.startswith("doc_field:"):
        slug = slug.split(":", 1)[-1]
    if slug.endswith("_owner"):
        slug = slug[:-6]
    elif slug.endswith("_date"):
        slug = slug[:-5]
    return slug.replace("_", " ").strip() or "item"


def _format_claim_change(
    cc: dict[str, Any],
    client_id: str | None,
    people_map: dict[str, str],
    clients_map: dict[str, str],
) -> dict[str, Any] | None:
    pred = str(cc["predicate"])
    old_val = cc.get("old_val")
    obj_val = str(cc["object_val"] or "").strip()
    sources = list(cc.get("sources") or [])
    if not sources:
        return None

    # Baseline initial document state without prior distinct value is not a change
    if pred in ("owner", "date") and not old_val and cc.get("source_kind") != "feedback_correction":
        return None

    client_name = clients_map.get(client_id or "", (client_id or "").capitalize())
    topic = _topic_label(str(cc["subject_key"]), client_id)
    eff_utc = str(cc.get("effective_at_utc") or "")

    if pred == "decision":
        sent = obj_val[:1].upper() + obj_val[1:] if obj_val else "Scope decision recorded"
        return {
            "text": sent,
            "sources": sources,
            "prio": 1,
            "kind": "decision",
            "at_utc": eff_utc,
            "title": obj_val,
            "new_val": obj_val,
        }

    if pred == "owner":
        new_name = _format_person_name(obj_val, people_map)
        old_name = _format_person_name(old_val, people_map)
        scope = f"{client_name} {topic}".strip() if client_name else topic
        text = (
            f"{new_name} now owns {scope} (was {old_name})"
            if old_name and old_name != new_name
            else f"{new_name} now owns {scope}"
        )
        return {
            "text": text,
            "sources": sources,
            "prio": 1,
            "kind": "owner",
            "at_utc": eff_utc,
            "title": scope,
            "new_val": new_name,
        }

    if pred == "field_update":
        label = topic.capitalize() if topic else "Field"
        text = (
            f"{label} updated from {old_val} to {obj_val}"
            if old_val and old_val != obj_val
            else f"{label} updated to {obj_val}"
        )
        return {
            "text": text,
            "sources": sources,
            "prio": 1,
            "kind": "status",
            "at_utc": eff_utc,
            "title": label,
            "new_val": obj_val,
        }

    # pred == "date"
    old_d = _format_short_date(old_val)
    new_d = _format_short_date(obj_val)
    label = topic.capitalize() if topic else "Date"
    text = (
        f"{label} moved from {old_d} to {new_d}"
        if old_d and old_d != new_d
        else f"{label} set to {new_d}"
    )
    return {
        "text": text,
        "sources": sources,
        "prio": 1,
        "kind": "date",
        "at_utc": eff_utc,
        "title": label,
        "new_val": new_d,
    }


def _deduplicate_changes(
    claim_items: list[dict[str, Any]],
    task_items: list[dict[str, Any]],
    task_changes_raw: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    decisions = [c for c in claim_items if c["kind"] == "decision"]
    dec_tokens = [(_content_tokens(d["text"]), d["text"].lower()) for d in decisions]

    cancelled_dups: set[str] = set()
    for tc in task_changes_raw:
        if tc["field"] == "status" and str(tc.get("to_val") or "") == "cancelled":
            ttok = _content_tokens(str(tc["title"]))
            tlow = str(tc["title"]).lower()
            for dtok, dlow in dec_tokens:
                if (tlow and tlow in dlow) or (len(ttok & dtok) >= 2):
                    cancelled_dups.add(str(tc["record_id"]))
                    break

    filtered_tasks = [t for t in task_items if t["sources"][0] not in cancelled_dups]

    # Deduplicate same source citation if both claim and task somehow point identically
    seen_texts: set[str] = set()
    combined: list[dict[str, Any]] = []
    for item in claim_items + filtered_tasks:
        norm = item["text"].strip().lower()
        if norm in seen_texts:
            continue
        seen_texts.add(norm)
        combined.append(item)
    return combined


def _sort_changes(
    items: list[dict[str, Any]],
    blockers_first: bool,
    keep_topics: list[str] | None = None,
) -> list[dict[str, Any]]:
    kind_order = {"blocked": 0, "owner": 1, "decision": 2, "date": 3, "status": 4, "delivered": 5}
    keeps = [str(k).lower().strip() for k in (keep_topics or []) if str(k).strip()]

    def _key(it: dict[str, Any]) -> tuple[int, int, int, int, str]:
        # Float blocked items when blockers_first is active, then prio, then kept topics
        blocker_tier = 0 if (blockers_first and it["kind"] == "blocked") else 1
        low_txt = str(it.get("text") or "").lower()
        keep_tier = 0 if (keeps and any(k in low_txt for k in keeps)) else 1
        return (blocker_tier, int(it["prio"]), keep_tier, kind_order.get(it["kind"], 9), it.get("at_utc", ""))

    return sorted(items, key=_key)


def _is_change_grounded_in_records(
    text: str,
    srcs: list[str],
    rec_by_id: dict[str, sqlite3.Row],
) -> bool:
    """Ensure LLM-proposed change text shares substantive tokens with its cited records."""
    text_toks = _content_tokens(text)
    if not text_toks:
        return False
    rec_blob = " ".join(
        f"{rec_by_id[s]['title'] or ''} {rec_by_id[s]['subject'] or ''} {rec_by_id[s]['sanitized_body'] or ''}"
        for s in srcs
        if s in rec_by_id
    )
    rec_toks = _content_tokens(rec_blob)
    overlap = len(text_toks & rec_toks)
    needed = 2 if len(text_toks) >= 4 else 1
    return overlap >= needed


def _filter_by_preferences(items: list[dict[str, Any]], prefs: dict[str, Any]) -> list[dict[str, Any]]:
    ignores = [str(t).lower().strip() for t in prefs.get("ignore_topics") or [] if str(t).strip()]
    if not ignores:
        return list(items)

    kept: list[dict[str, Any]] = []
    for it in items:
        low = it["text"].lower()
        if any(ig in low for ig in ignores):
            continue
        kept.append(it)
    return kept


def _llm_fallback_changes(
    conn: sqlite3.Connection,
    person_id: str,
    person_org: str,
    client_id: str,
    since_day: int,
    upto_day: int,
    tracker: UsageTracker,
    llm_client: LLMClient,
    ignore_topics: list[str],
    exclude_ids: set[str] | None = None,
    is_supplement: bool = False,
) -> list[dict[str, Any]]:
    """Use configured LLM chain for uncovered substantive records after deterministic extraction."""
    if not llm_client.configured_providers():
        return []

    where = visible_records_where_clause("r", with_utc_cutoff=False)
    params = visible_records_params(person_id, person_org, upto_day)
    params["cid"] = client_id
    params["since_day"] = since_day

    rows = conn.execute(
        f"""
        SELECT r.*
        FROM records r
        WHERE r.client = :cid
          AND r.day >= :since_day
          AND r.type IN ('mail', 'chat', 'note')
          AND r.is_boilerplate = 0
          AND r.is_newsletter = 0
          AND TRIM(COALESCE(r.sanitized_body, '')) != ''
          AND r.id NOT IN (SELECT record_id FROM promises WHERE is_forgotten = 0)
          AND r.id NOT IN (
              SELECT fulfilled_by_record_id FROM promises
              WHERE fulfilled_by_record_id IS NOT NULL AND is_forgotten = 0
          )
          AND r.id NOT IN (
              SELECT source_record_id FROM claims
              WHERE source_record_id IS NOT NULL AND is_forgotten = 0
          )
          AND {where}
        ORDER BY r.at_utc DESC
        LIMIT 30
        """,
        params,
    ).fetchall()

    excluded = exclude_ids or set()
    kw_groups, forgotten_ids = _get_forgotten_entries(conn, person_id)
    candidate_rows: list[sqlite3.Row] = []
    for rr in rows:
        rid = str(rr["id"])
        if rid in excluded or rid in forgotten_ids:
            continue
        body_txt = str(rr["sanitized_body"] or "").strip()
        if len(body_txt.split()) < 5:
            continue
        blob = f"{rr['title'] or ''} {rr['subject'] or ''} {body_txt}"
        if kw_groups and _text_matches_forgotten(blob, kw_groups):
            continue
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", body_txt) if s.strip()]
        if sentences and all(is_unverified_or_hedged_clause(s) for s in sentences):
            continue
        candidate_rows.append(rr)

    if not candidate_rows:
        return []

    rec_by_id = {str(r["id"]): r for r in candidate_rows}
    valid_ids = set(rec_by_id.keys())

    # Prioritize semantically substantive client updates via hybrid retrieval before token-budget packing
    ordered_rows = list(candidate_rows)
    if len(candidate_rows) > 1:
        hyb = search_hybrid(
            conn,
            person_id,
            "blocked delay deadline launch billing owner scope decision approval issue",
            client_id=client_id,
            upto_day=upto_day,
            limit=len(candidate_rows),
            tracker=tracker,
        )
        ranked_ids = [h["record_id"] for h in hyb if h["record_id"] in valid_ids]
        if ranked_ids:
            seen_r = set(ranked_ids)
            ordered_rows = [rec_by_id[rid] for rid in ranked_ids] + [r for r in candidate_rows if str(r["id"]) not in seen_r]

    user_prompt, count = pack_records_within_budget(
        ordered_rows,
        _BRIEF_SYSTEM_PROMPT,
        user_prefix=f"Client: {client_id}\nRecords since day {since_day}:",
        hard_limit=BRIEF_TOKEN_LIMIT,
    )
    if count == 0:
        return []

    parsed = llm_client.call_json(
        _BRIEF_SYSTEM_PROMPT,
        user_prompt,
        tracker,
        max_input_tokens=BRIEF_TOKEN_LIMIT,
    )
    if not isinstance(parsed, dict):
        return []

    raw_changes = parsed.get("changed") or []
    out: list[dict[str, Any]] = []
    ignores = [t.lower().strip() for t in ignore_topics if t.strip()]
    for item in raw_changes:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        srcs = [str(s) for s in (item.get("sources") or []) if str(s) in valid_ids]
        if not text or not srcs:
            continue
        if ignores and any(ig in text.lower() for ig in ignores):
            continue
        if kw_groups and _text_matches_forgotten(text, kw_groups):
            continue
        if not _is_change_grounded_in_records(text, srcs, rec_by_id):
            continue
        kind = str(item.get("kind") or "status").lower()
        if kind not in ("blocked", "decision", "owner", "date", "status"):
            kind = "status"
        prio = 1 if kind in ("blocked", "decision", "owner", "date") else 2
        at_utc = max(str(rec_by_id[s]["at_utc"]) for s in srcs)
        out.append(
            {
                "text": text,
                "sources": srcs,
                "prio": prio,
                "kind": kind,
                "at_utc": at_utc,
                "title": text,
                "new_val": text,
            }
        )
    return out


def _build_question_to_ask(top_item: dict[str, Any]) -> dict[str, Any] | None:
    srcs = list(top_item.get("sources") or [])
    if not srcs:
        return None
    kind = top_item.get("kind")
    title = str(top_item.get("title") or "this item").strip()
    new_val = str(top_item.get("new_val") or "").strip()

    if kind == "blocked":
        q_text = f"What is needed to unblock {title}?"
    elif kind == "date":
        q_text = (
            f"Are downstream dependencies aligned with the updated {new_val} date?"
            if new_val
            else f"Are downstream dependencies aligned with the updated date?"
        )
    elif kind == "owner":
        q_text = (
            f"Should we align next steps directly with {new_val}?"
            if new_val
            else "Should we align next steps with the new owner?"
        )
    elif kind == "decision":
        q_text = f"Are any follow-up actions needed following: {top_item['text']}?"
    else:
        q_text = f"Are there any remaining action items for {title}?"

    return {"text": q_text, "sources": [srcs[0]]}


def generate_briefs_for_person_date(
    conn: sqlite3.Connection,
    person_id: str,
    date_str: str,
    llm_client: LLMClient | None = None,
    tracker: UsageTracker | None = None,
) -> list[dict[str, Any]]:
    """Generate validated meeting briefs for all meetings attended by person_id on local date_str."""
    current_day = get_current_day(conn)
    if current_day <= 0:
        return []

    person_row = get_person_row(conn, person_id)
    if person_row is None:
        return []
    person_org = str(person_row["org_id"])
    person_zone = str(person_row["zone"])

    # Return cached result for repeat calls on the same ingested day
    cached = conn.execute(
        "SELECT response_json FROM brief_cache WHERE person = ? AND date = ? AND day = ?",
        (person_id, date_str, current_day),
    ).fetchone()
    if cached is not None:
        try:
            loaded = json.loads(cached["response_json"])
            if isinstance(loaded, list):
                return validate_brief_list(conn, loaded, person_id, current_day)
        except ValueError:
            pass

    # Find visible calendar events attended by person_id on local date_str
    where = visible_records_where_clause("r", with_utc_cutoff=False)
    params = visible_records_params(person_id, person_org, current_day)
    ev_rows = conn.execute(
        f"""
        SELECT r.*
        FROM records r
        WHERE r.type = 'event'
          AND r.start_utc IS NOT NULL
          AND {where}
        ORDER BY r.at_utc ASC, r.day ASC
        """,
        params,
    ).fetchall()

    # Deduplicate rescheduled events by meeting_id keeping the latest record
    latest_by_meeting: dict[str, sqlite3.Row] = {}
    for row in ev_rows:
        mid = str(row["event_id"] or row["id"])
        latest_by_meeting[mid] = row

    meetings_today: list[tuple[str, sqlite3.Row]] = []
    for mid, row in latest_by_meeting.items():
        att: list[str] = []
        if row["attendees_json"]:
            try:
                att = [str(x) for x in json.loads(row["attendees_json"])]
            except ValueError:
                att = []
        if person_id not in att and str(row["author"]) != person_id:
            continue
        if utc_to_local_date(str(row["start_utc"]), person_zone) == date_str:
            meetings_today.append((mid, row))

    meetings_today.sort(key=lambda item: (str(item[1]["start_utc"]), item[0]))

    if not meetings_today:
        validated_empty = validate_brief_list(conn, [], person_id, current_day)
        conn.execute(
            "INSERT OR REPLACE INTO brief_cache(person, date, day, response_json) VALUES (?, ?, ?, ?)",
            (person_id, date_str, current_day, json.dumps(validated_empty)),
        )
        conn.commit()
        return validated_empty

    people_map = _load_people_map(conn)
    clients_map = _load_clients_map(conn)
    prefs = get_person_preferences(conn, person_id, upto_day=current_day)
    client = llm_client or LLMClient()

    outer_tracker = tracker
    raw_briefs: list[dict[str, Any]] = []
    for mid, ev in meetings_today:
        tracker = UsageTracker()
        cid = ev["client"]
        since_day = _compute_since_day(
            conn, person_id, person_org, person_zone, cid, date_str, current_day
        )

        raw_tasks = get_task_changes_in_window(
            conn, person_id, cid, upto_day=current_day, since_day=since_day
        )
        task_items = [_format_task_change(tc, people_map) for tc in raw_tasks]

        raw_claims = []
        claim_items: list[dict[str, Any]] = []
        delivered_items: list[dict[str, Any]] = []
        if cid is not None:
            raw_claims = get_claim_changes_in_window(
                conn,
                person_id,
                cid,
                upto_day=current_day,
                query_date=date_str,
                since_day=since_day,
            )
            for cc in raw_claims:
                fmt = _format_claim_change(cc, cid, people_map, clients_map)
                if fmt is not None:
                    claim_items.append(fmt)

            for dp in get_fulfilled_promises_in_window(conn, person_id, cid, since_day, current_day):
                owner_name = _format_person_name(dp["owner"], people_map)
                delivered_items.append(
                    {
                        "text": f"{owner_name} delivered {dp['what']}",
                        "sources": [dp["record_id"]],
                        "prio": 3,
                        "kind": "delivered",
                        "at_utc": dp["at_utc"],
                        "title": dp["what"],
                        "new_val": dp["what"],
                    }
                )

        merged_unfiltered = _deduplicate_changes(claim_items, task_items, raw_tasks)
        existing_sources = {str(s) for it in merged_unfiltered for s in it.get("sources") or []}
        existing_texts = {str(it["text"]).strip().lower() for it in merged_unfiltered}
        for di in delivered_items:
            if di["sources"][0] in existing_sources or di["text"].strip().lower() in existing_texts:
                continue
            merged_unfiltered.append(di)
            existing_sources.update(str(s) for s in di["sources"])
            existing_texts.add(di["text"].strip().lower())

        if cid is not None:
            llm_extra = _llm_fallback_changes(
                conn,
                person_id,
                person_org,
                cid,
                since_day,
                current_day,
                tracker,
                client,
                prefs.get("ignore_topics") or [],
                exclude_ids=existing_sources if merged_unfiltered else None,
                is_supplement=bool(merged_unfiltered),
            )
            for li in llm_extra:
                ltxt = str(li["text"]).strip().lower()
                if ltxt in existing_texts or all(str(s) in existing_sources for s in li.get("sources") or []):
                    continue
                merged_unfiltered.append(li)
                existing_sources.update(str(s) for s in li.get("sources") or [])
                existing_texts.add(ltxt)

        merged = _filter_by_preferences(merged_unfiltered, prefs)

        ordered = _sort_changes(
            merged,
            blockers_first=bool(prefs.get("blockers_first")),
            keep_topics=prefs.get("keep_topics"),
        )

        # Run output firewall BEFORE applying max_changed cap so forgotten/tainted items never crowd out real changes
        pre_check = {
            "meeting_id": mid,
            "status": "brief" if ordered else "nothing_new",
            "changed": [{"text": it["text"], "sources": it["sources"]} for it in ordered],
            "promises": [],
            "open_these": [],
            "question_to_ask": None,
            "usage": tracker.to_dict(),
        }
        pre_validated = validate_brief_list(conn, [pre_check], person_id, current_day)[0]
        clean_changed_dicts = pre_validated.get("changed") or []

        if cid is not None:
            conn.execute(
                """
                INSERT OR REPLACE INTO brief_watermarks(person, client, meeting_date, prepared_day)
                VALUES (?, ?, ?, ?)
                """,
                (person_id, cid, date_str, current_day),
            )

        if not clean_changed_dicts:
            raw_briefs.append(
                {
                    "meeting_id": mid,
                    "status": "nothing_new",
                    "changed": [],
                    "promises": [],
                    "open_these": [],
                    "question_to_ask": None,
                    "usage": tracker.to_dict(),
                }
            )
            continue

        # Map back metadata for surviving items in sorted order
        surviving_meta: list[dict[str, Any]] = []
        used_idx: set[int] = set()
        for cd in clean_changed_dicts:
            for idx, orig in enumerate(ordered):
                if idx not in used_idx and orig["text"] == cd["text"]:
                    surviving_meta.append({**orig, "sources": cd["sources"]})
                    used_idx.add(idx)
                    break

        max_ch = prefs.get("max_changed")
        if isinstance(max_ch, int) and max_ch >= 1:
            surviving_meta = surviving_meta[:max_ch]

        open_promises = get_open_promises(conn, person_id, client_id=cid, upto_day=current_day)
        promises_out = [
            {
                "owner": str(p["owner"]),
                "what": str(p["what"]),
                "due": str(p["due"]),
                "sources": list(p["sources"]),
            }
            for p in open_promises
        ]

        open_these: list[dict[str, str]] = []
        seen_open_rec: set[str] = set()
        for it in surviving_meta:
            rec_id = str(it["sources"][0])
            if rec_id in seen_open_rec:
                continue
            seen_open_rec.add(rec_id)
            open_these.append({"record": rec_id, "why": str(it["text"])})
            if len(open_these) >= 3:
                break

        question_to_ask = _build_question_to_ask(surviving_meta[0]) if surviving_meta else None

        raw_briefs.append(
            {
                "meeting_id": mid,
                "status": "brief",
                "changed": [{"text": it["text"], "sources": it["sources"]} for it in surviving_meta],
                "promises": promises_out,
                "open_these": open_these,
                "question_to_ask": question_to_ask,
                "usage": tracker.to_dict(),
            }
        )
        if outer_tracker is not None:
            for call in tracker.calls:
                outer_tracker.add_call(
                    call["input_tokens"],
                    call["output_tokens"],
                    model=call.get("model"),
                    cost_usd=call.get("cost_usd"),
                    provider=call.get("provider"),
                    latency_ms=call.get("latency_ms"),
                )

    final_briefs = validate_brief_list(conn, raw_briefs, person_id, current_day)
    conn.execute(
        "INSERT OR REPLACE INTO brief_cache(person, date, day, response_json) VALUES (?, ?, ?, ?)",
        (person_id, date_str, current_day, json.dumps(final_briefs, ensure_ascii=False)),
    )
    conn.commit()
    return final_briefs
