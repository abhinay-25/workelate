"""Persistent memory: Day N-1 feedback processing, preferences, corrections, verdicts, and cascading forget."""

import json
import re
import sqlite3
from datetime import date, timedelta
from typing import Any

from src.claims import (
    _normalize_client_and_topic,
    _resolve_person_in_org,
    extract_correction_from_clause,
    is_unverified_or_hedged_clause,
    rebuild_claims,
)
from src.docs_engine import rebuild_doc_versions
from src.entities import extract_entities_for_day
from src.llm import LLMClient
from src.promises import rebuild_promises
from src.retrieval import delete_vectors_for_records
from src.security import get_current_day, get_person_org
from src.tasks_engine import rebuild_tasks_state
from src.time_utils import parse_explicit_date
from src.usage import ASK_TOKEN_LIMIT, UsageTracker

_FORGET_STOP_WORDS = frozenset(
    {
        "forget", "what", "i", "said", "wrote", "mentioned", "about", "the",
        "my", "a", "an", "to", "of", "for", "on", "in", "please", "remove",
        "delete", "everything", "anything", "me", "that", "this", "is", "was",
    }
)

_MAX_CHANGED_RE = re.compile(r"\b(?:max|at\s+most|up\s+to|limit\s+to)\s+(\d+)\s+changes?\b", re.IGNORECASE)

_OWNERSHIP_CORR_RE = re.compile(
    r"(?:no,?\s*)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:now\s+)?(?:owns|handles)\s+"
    r"([A-Za-z0-9 _-]+?)(?:\s+now)?(?:\s*,?\s*not\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?))?(?:[.!]|$)",
    re.IGNORECASE,
)


def _day_start_utc(day_num: int) -> str:
    """Day 1 = 2026-11-02; return canonical start UTC for feedback ingested on day_num."""
    dt = date(2026, 11, 1) + timedelta(days=max(1, day_num))
    return f"{dt.isoformat()}T00:00:00Z"


def extract_forget_keywords(what_text: str) -> list[str]:
    """Extract normalized search keywords from a forget request string."""
    tokens = re.findall(r"[a-z0-9]+", (what_text or "").lower())
    kws = [t for t in tokens if len(t) >= 2 and t not in _FORGET_STOP_WORDS]
    # Deduplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for k in kws:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _record_matches_keywords(rec: sqlite3.Row, keywords: list[str]) -> bool:
    if not keywords:
        return False
    blob = " ".join(
        str(x or "")
        for x in (rec["title"], rec["subject"], rec["sanitized_body"], rec["body"])
    ).lower()
    return all(re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", blob) for kw in keywords)


def _parse_preference(
    conn: sqlite3.Connection,
    person: str,
    day: int,
    words: str,
    llm_client: LLMClient | None = None,
) -> list[tuple[str, str]]:
    low = (words or "").lower()
    prefs: list[tuple[str, str]] = []

    if re.search(r"\b(?:skip|no|ignore|exclude|hide)\s+newsletters?\b", low):
        prefs.append(("skip_newsletters", "true"))

    m_max = _MAX_CHANGED_RE.search(low)
    if m_max:
        prefs.append(("max_changed", m_max.group(1)))

    if "most important first" in low:
        prefs.append(("most_important_first", "true"))

    if "blockers first" in low or "blocker first" in low or "prioritize blockers" in low:
        prefs.append(("blockers_first", "true"))

    if not prefs:
        client = llm_client or LLMClient()
        if client.configured_providers():
            tracker = UsageTracker()
            parsed = client.call_json(
                "Extract brief preferences into JSON: "
                '{"skip_newsletters": bool|null, "max_changed": int|null, '
                '"blockers_first": bool|null, "most_important_first": bool|null, "ignore_topics": [str]}',
                f"User preference: {words}",
                tracker,
                max_input_tokens=ASK_TOKEN_LIMIT,
            )
            if isinstance(parsed, dict):
                if parsed.get("skip_newsletters") is True:
                    prefs.append(("skip_newsletters", "true"))
                if isinstance(parsed.get("max_changed"), int) and parsed["max_changed"] > 0:
                    prefs.append(("max_changed", str(parsed["max_changed"])))
                if parsed.get("blockers_first") is True:
                    prefs.append(("blockers_first", "true"))
                if parsed.get("most_important_first") is True:
                    prefs.append(("most_important_first", "true"))
                for t in parsed.get("ignore_topics") or []:
                    if str(t).strip():
                        prefs.append(("ignore_topic", str(t).strip().lower()))

    for pk, pv in prefs:
        conn.execute(
            """
            INSERT INTO preferences(person, day, pref_key, pref_val, source_words)
            VALUES (?, ?, ?, ?, ?)
            """,
            (person, day, pk, pv, words),
        )
    return prefs


def _infer_meeting_client(conn: sqlite3.Connection, meeting_id: str | None, org: str, words: str) -> str | None:
    if meeting_id:
        row = conn.execute(
            "SELECT client FROM records WHERE event_id = ? AND org = ? AND client IS NOT NULL LIMIT 1",
            (meeting_id, org),
        ).fetchone()
        if row and row["client"]:
            return str(row["client"])
    low = (words or "").lower()
    for crow in conn.execute("SELECT client_id, name FROM clients WHERE org_id = ?", (org,)).fetchall():
        cid = str(crow["client_id"])
        cname = str(crow["name"]).split()[0].lower()
        if cid in low or cname in low:
            return cid
    return None


def _find_supporting_team_record(
    conn: sqlite3.Connection,
    org: str,
    client_id: str | None,
    keywords: list[str],
    upto_day: int,
) -> str | None:
    sql = """
        SELECT id, sanitized_body FROM records
        WHERE org = ? AND is_forgotten = 0 AND visibility = 'team'
          AND day <= ? AND is_newsletter = 0 AND is_boilerplate = 0
    """
    params: list[Any] = [org, upto_day]
    if client_id:
        sql += " AND client = ?"
        params.append(client_id)
    sql += " ORDER BY at_utc DESC"
    for r in conn.execute(sql, params).fetchall():
        low = str(r["sanitized_body"] or "").lower()
        if all(re.search(r"(?<![a-z0-9])" + re.escape(k.lower()) + r"(?![a-z0-9])", low) for k in keywords if k):
            return str(r["id"])
    return None


def _process_correction(
    conn: sqlite3.Connection,
    fb_row: sqlite3.Row,
    org: str,
    llm_client: LLMClient | None = None,
) -> None:
    fb_id = int(fb_row["feedback_id"])
    words = str(fb_row["words"] or "")
    ingested_day = int(fb_row["ingested_on_day"])
    meeting_id = fb_row["meeting"]
    person = str(fb_row["person"])
    if is_unverified_or_hedged_clause(words):
        return
    client_id = _infer_meeting_client(conn, meeting_id, org, words)

    # Try shared deterministic natural-language extractor first (handles active, passive, switched, date)
    corr = extract_correction_from_clause(
        conn,
        org,
        client_id,
        "",
        words,
        local_year=2026,
        local_month=11,
        allow_unresolved_names=True,
    )
    if corr is not None:
        cid = corr["client"] or client_id
        kws = [str(corr["raw_new"]).split()[0], str(corr["topic_clean"])]
        support_id = _find_supporting_team_record(conn, org, cid, kws, ingested_day)
        conn.execute(
            """
            INSERT INTO claims(
                org, client, subject_key, predicate, object_val, old_val,
                source_kind, author_role, effective_at_utc, at_utc, day,
                source_record_id, feedback_id, visibility, author, readers_json, is_forgotten
            ) VALUES (?, ?, ?, ?, ?, ?, 'feedback_correction', 'feedback', ?, ?, ?, ?, ?, 'team', ?, NULL, 0)
            """,
            (
                org,
                cid,
                corr["subject_key"],
                corr["predicate"],
                corr["object_val"],
                corr["old_val"],
                _day_start_utc(ingested_day),
                _day_start_utc(ingested_day),
                ingested_day,
                support_id,
                fb_id,
                person,
            ),
        )
        return

    m = _OWNERSHIP_CORR_RE.search(words.strip())
    if m:
        new_name = m.group(1).strip()
        topic = m.group(2).strip().lower()
        old_name = m.group(3).strip() if m.group(3) else None

        # Strip optional leading client token from topic (e.g. "<client> billing" -> "billing")
        topic_clean = topic
        for crow in conn.execute("SELECT client_id, name FROM clients WHERE org_id = ?", (org,)).fetchall():
            cid = str(crow["client_id"])
            cfirst = str(crow["name"]).split()[0].lower()
            if topic_clean.startswith(cid + " "):
                client_id = client_id or cid
                topic_clean = topic_clean[len(cid) + 1 :].strip()
            elif topic_clean.startswith(cfirst + " "):
                client_id = client_id or cid
                topic_clean = topic_clean[len(cfirst) + 1 :].strip()

        new_pid, new_full = _resolve_person_in_org(conn, org, new_name, client_id, "", f"{topic} {words}")
        old_pid, old_full = (
            _resolve_person_in_org(conn, org, old_name, client_id, "", f"{topic} {words}")
            if old_name
            else (None, None)
        )
        pred_topic = "billing_owner" if ("billing" in topic_clean or "invoice" in topic_clean) else re.sub(r"[^a-z0-9]+", "_", topic_clean)
        subj = f"{client_id}:{pred_topic}" if client_id else pred_topic

        support_id = _find_supporting_team_record(
            conn,
            org,
            client_id,
            [new_name.split()[0], topic_clean],
            ingested_day,
        )
        obj_val = f"{new_pid}:{new_full}" if new_pid else new_full
        old_val = f"{old_pid}:{old_full}" if (old_pid and old_full) else old_full

        conn.execute(
            """
            INSERT INTO claims(
                org, client, subject_key, predicate, object_val, old_val,
                source_kind, author_role, effective_at_utc, at_utc, day,
                source_record_id, feedback_id, visibility, author, readers_json, is_forgotten
            ) VALUES (?, ?, ?, 'owner', ?, ?, 'feedback_correction', 'feedback', ?, ?, ?, ?, ?, 'team', ?, NULL, 0)
            """,
            (
                org,
                client_id,
                subj,
                obj_val,
                old_val,
                _day_start_utc(ingested_day),
                _day_start_utc(ingested_day),
                ingested_day,
                support_id,
                fb_id,
                person,
            ),
        )
        return

    client = llm_client or LLMClient()
    if client.configured_providers():
        tracker = UsageTracker()
        parsed = client.call_json(
            "Parse verified factual correction into JSON. Set is_hedged_or_unverified=true if uncertain/question/speculative: "
            '{"is_correction": bool, "is_hedged_or_unverified": bool, "subject_topic": str, '
            '"predicate": "owner"|"date"|"decision", "new_val": str, "old_val": str|null}',
            f"Meeting: {meeting_id or 'none'}\nCorrection: {words}",
            tracker,
            max_input_tokens=ASK_TOKEN_LIMIT,
        )
        if (
            isinstance(parsed, dict)
            and parsed.get("is_correction", True) is True
            and not parsed.get("is_hedged_or_unverified")
            and parsed.get("subject_topic")
            and parsed.get("new_val")
        ):
            cid, pred_topic, topic_clean = _normalize_client_and_topic(
                conn, org, client_id, str(parsed["subject_topic"])
            )
            pred = str(parsed.get("predicate") or ("owner" if pred_topic.endswith("_owner") else "decision"))
            raw_new = str(parsed["new_val"]).strip()
            raw_old = str(parsed.get("old_val") or "").strip() or None
            obj_val = raw_new
            old_val = raw_old
            if pred == "owner":
                npid, nfull = _resolve_person_in_org(conn, org, raw_new, cid, "", words)
                obj_val = f"{npid}:{nfull}" if npid else nfull
                if raw_old:
                    opid, ofull = _resolve_person_in_org(conn, org, raw_old, cid, "", words)
                    old_val = f"{opid}:{ofull}" if opid else ofull
            elif pred == "date":
                obj_val = parse_explicit_date(raw_new, 2026, 11) or raw_new
                if raw_old:
                    old_val = parse_explicit_date(raw_old, 2026, 11) or raw_old
            subj = f"{cid}:{pred_topic}" if cid else pred_topic
            support_id = _find_supporting_team_record(
                conn, org, cid, [raw_new.split()[0], topic_clean], ingested_day
            )
            conn.execute(
                """
                INSERT INTO claims(
                    org, client, subject_key, predicate, object_val, old_val,
                    source_kind, author_role, effective_at_utc, at_utc, day,
                    source_record_id, feedback_id, visibility, author, readers_json, is_forgotten
                ) VALUES (?, ?, ?, ?, ?, ?, 'feedback_correction', 'feedback', ?, ?, ?, ?, ?, 'team', ?, NULL, 0)
                """,
                (
                    org,
                    cid,
                    subj,
                    pred,
                    obj_val,
                    old_val,
                    _day_start_utc(ingested_day),
                    _day_start_utc(ingested_day),
                    ingested_day,
                    support_id,
                    fb_id,
                    person,
                ),
            )


def _process_verdict(conn: sqlite3.Connection, fb_row: sqlite3.Row) -> None:
    person = str(fb_row["person"])
    day = int(fb_row["ingested_on_day"])
    verdict = str(fb_row["verdict"] or "").lower()
    topic = str(fb_row["topic"] or "").strip()
    words = str(fb_row["words"] or "").strip()
    if verdict == "ignored" and topic:
        conn.execute(
            """
            INSERT INTO preferences(person, day, pref_key, pref_val, source_words)
            VALUES (?, ?, 'ignore_topic', ?, ?)
            """,
            (person, day, topic.lower(), words),
        )
    elif verdict == "kept" and topic:
        conn.execute(
            """
            INSERT INTO preferences(person, day, pref_key, pref_val, source_words)
            VALUES (?, ?, 'keep_topic', ?, ?)
            """,
            (person, day, topic.lower(), words),
        )


def process_feedback_for_day(
    conn: sqlite3.Connection,
    ingested_on_day: int,
    llm_client: LLMClient | None = None,
) -> None:
    """Process all unapplied feedback rows arriving on ingested_on_day."""
    # Ensure idempotency if re-invoked for the same day
    conn.execute("DELETE FROM preferences WHERE day = ?", (ingested_on_day,))
    conn.execute(
        "DELETE FROM claims WHERE source_kind = 'feedback_correction' AND day = ?",
        (ingested_on_day,),
    )

    rows = conn.execute(
        """
        SELECT * FROM feedback
        WHERE ingested_on_day = ? AND is_forgotten = 0
        ORDER BY feedback_id ASC
        """,
        (ingested_on_day,),
    ).fetchall()

    for row in rows:
        person = str(row["person"])
        org = get_person_org(conn, person) or "org_kp"
        ftype = str(row["type"] or "").lower()
        words = str(row["words"] or "")
        if ftype == "preference":
            _parse_preference(conn, person, ingested_on_day, words, llm_client=llm_client)
        elif ftype == "correction":
            _process_correction(conn, row, org, llm_client=llm_client)
        elif ftype == "verdict":
            _process_verdict(conn, row)
        elif ftype == "forget":
            forget_for_person(conn, person, words, day=ingested_on_day)


def get_person_preferences(
    conn: sqlite3.Connection,
    person_id: str,
    upto_day: int | None = None,
) -> dict[str, Any]:
    """Return active per-person brief preferences up to upto_day."""
    day_lim = get_current_day(conn) if upto_day is None else upto_day
    rows = conn.execute(
        """
        SELECT pref_key, pref_val FROM preferences
        WHERE person = ? AND day <= ?
        ORDER BY day ASC, pref_id ASC
        """,
        (person_id, day_lim),
    ).fetchall()

    prefs: dict[str, Any] = {
        "skip_newsletters": False,
        "max_changed": None,
        "most_important_first": False,
        "blockers_first": False,
        "ignore_topics": [],
        "keep_topics": [],
    }
    for r in rows:
        pk = str(r["pref_key"])
        pv = str(r["pref_val"])
        if pk == "skip_newsletters":
            prefs["skip_newsletters"] = pv.lower() == "true"
        elif pk == "max_changed":
            try:
                prefs["max_changed"] = int(pv)
            except ValueError:
                pass
        elif pk == "most_important_first":
            prefs["most_important_first"] = pv.lower() == "true"
        elif pk == "blockers_first":
            prefs["blockers_first"] = pv.lower() == "true"
        elif pk == "ignore_topic" and pv not in prefs["ignore_topics"]:
            prefs["ignore_topics"].append(pv)
        elif pk == "keep_topic" and pv not in prefs["keep_topics"]:
            prefs["keep_topics"].append(pv)

    return prefs


def forget_for_person(
    conn: sqlite3.Connection,
    person_id: str,
    what_text: str,
    day: int | None = None,
) -> dict[str, Any]:
    """Retract P-authored matching records, globally invalidate/recompute derived state,
    purge brief_cache, preserve independent shared facts authored by others, and filter P's outputs.
    """
    org = get_person_org(conn, person_id)
    cur_day = get_current_day(conn) if day is None else day
    keywords = extract_forget_keywords(what_text)

    affected_ids: list[str] = []
    if org and keywords:
        # Only retract records authored by person_id (preserving independent records by others)
        cand_recs = conn.execute(
            """
            SELECT * FROM records
            WHERE org = ? AND author = ? AND is_forgotten = 0
            """,
            (org, person_id),
        ).fetchall()
        for rec in cand_recs:
            if _record_matches_keywords(rec, keywords):
                rid = str(rec["id"])
                affected_ids.append(rid)
                # Clearing sanitized_body/title/subject triggers records_au so forgotten tokens vanish from FTS5
                conn.execute(
                    """
                    UPDATE records
                    SET is_forgotten = 1, sanitized_body = '', title = NULL, subject = NULL
                    WHERE id = ?
                    """,
                    (rid,),
                )
                conn.execute("UPDATE task_transitions SET is_forgotten = 1 WHERE record_id = ?", (rid,))
                conn.execute("UPDATE doc_versions SET is_forgotten = 1 WHERE record_id = ?", (rid,))
                conn.execute("DELETE FROM collab_edges WHERE source_record_id = ?", (rid,))
                conn.execute("DELETE FROM entity_aliases WHERE source_record_id = ?", (rid,))
                conn.execute("DELETE FROM claims WHERE source_record_id = ?", (rid,))

        # Also retract any non-forget feedback rows submitted by person_id matching those keywords
        fb_rows = conn.execute(
            """
            SELECT feedback_id, words, topic FROM feedback
            WHERE person = ? AND type != 'forget' AND is_forgotten = 0
            """,
            (person_id,),
        ).fetchall()
        for fb in fb_rows:
            fb_blob = f"{fb['topic'] or ''} {fb['words'] or ''}".lower()
            if all(re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", fb_blob) for kw in keywords):
                fid = int(fb["feedback_id"])
                conn.execute("UPDATE feedback SET is_forgotten = 1 WHERE feedback_id = ?", (fid,))
                conn.execute("DELETE FROM claims WHERE feedback_id = ?", (fid,))

    # Complete derived recomputation and stale vector purging from surviving active records
    if affected_ids:
        delete_vectors_for_records(conn, affected_ids)
        ingested_days = conn.execute("SELECT DISTINCT day FROM records WHERE is_forgotten = 0").fetchall()
        for drow in ingested_days:
            extract_entities_for_day(conn, int(drow["day"]))
        rebuild_tasks_state(conn)
        rebuild_promises(conn)
        rebuild_doc_versions(conn)
        rebuild_claims(conn)

    # Global brief_cache purge: no cached summary anywhere can retain retracted facts
    conn.execute("DELETE FROM brief_cache")

    # Record person-level suppression so person_id never sees residual mentions even from shared records
    if keywords:
        conn.execute(
            """
            INSERT INTO forgotten_topics(person, day, raw_words, topic_keywords_json, affected_record_ids_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                person_id,
                cur_day,
                what_text,
                json.dumps(keywords, ensure_ascii=False),
                json.dumps(affected_ids, ensure_ascii=False),
            ),
        )

    conn.commit()
    return {"ok": True, "forgotten_records": len(affected_ids), "keywords": keywords}
