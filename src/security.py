"""Authorization, visibility enforcement, private-existence checks, and output firewall."""

import json
import re
import sqlite3
from typing import Any

from src.db import get_meta

# Phrases commonly injected by adversarial records that must never slip into outputs
# unless independently backed by a non-injected factual record.
_INJECTION_GUARD_PHRASES = (
    "AT RISK",
    "approved by client",
)


def get_current_day(conn: sqlite3.Connection) -> int:
    """Return the latest ingested day index, or 0 if none ingested yet."""
    raw = get_meta(conn, "current_day", "0")
    try:
        return int(raw or 0)
    except (ValueError, TypeError):
        return 0


def get_person_row(conn: sqlite3.Connection, person_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM people WHERE person_id = ?", (person_id,)).fetchone()


def get_person_org(conn: sqlite3.Connection, person_id: str) -> str | None:
    row = conn.execute("SELECT org_id FROM people WHERE person_id = ?", (person_id,)).fetchone()
    return str(row["org_id"]) if row is not None else None


def _parse_readers(record_row: Any) -> list[str]:
    if hasattr(record_row, "keys"):
        raw = record_row["readers_json"] if "readers_json" in record_row.keys() else record_row.get("readers")
    else:
        raw = None
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return [str(x) for x in parsed] if isinstance(parsed, list) else []
        except ValueError:
            return []
    return []


def check_leak(record_row: Any, person_id: str, person_org: str | None) -> str | None:
    """Return a diagnostic string if person_id must not view record_row, else None."""
    if not person_org:
        return "unknown person org"
    rec_org = record_row["org"]
    if rec_org != person_org:
        return f"cross-org ({rec_org} != {person_org})"

    vis = record_row["visibility"] or "team"
    if vis == "team":
        return None
    if vis == "private":
        if record_row["author"] == person_id:
            return None
        return f"private to {record_row['author']}"
    if vis == "restricted":
        readers = _parse_readers(record_row)
        if person_id in readers:
            return None
        return f"restricted (readers={readers})"
    return f"unknown visibility ({vis})"


def can_view(record_row: Any, person_id: str, person_org: str | None) -> bool:
    """Return True iff person_id in person_org is authorized to view record_row."""
    return check_leak(record_row, person_id, person_org) is None


def visible_records_where_clause(alias: str = "r", with_utc_cutoff: bool = False) -> str:
    """SQL WHERE predicate enforcing org, visibility, day/time boundaries, forget, and pure-injection exclusion."""
    a = f"{alias}." if alias else ""
    parts = [
        f"{a}org = :person_org",
        f"{a}day <= :upto_day",
        f"{a}is_forgotten = 0",
        f"({a}has_injection_span = 0 OR TRIM(COALESCE({a}sanitized_body, '')) != '')",
        f"""(
            {a}visibility = 'team'
            OR ({a}visibility = 'private' AND {a}author = :person_id)
            OR (
                {a}visibility = 'restricted'
                AND EXISTS (
                    SELECT 1 FROM json_each(COALESCE({a}readers_json, '[]')) AS je
                    WHERE je.value = :person_id
                )
            )
        )""",
    ]
    if with_utc_cutoff:
        parts.append(f"{a}at_utc <= :upto_utc")
    return "\n  AND ".join(parts)


def visible_records_params(
    person_id: str,
    person_org: str,
    upto_day: int,
    upto_utc: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "person_id": person_id,
        "person_org": person_org,
        "upto_day": upto_day,
    }
    if upto_utc is not None:
        params["upto_utc"] = upto_utc
    return params


def has_inaccessible_record_about_person(
    conn: sqlite3.Connection,
    asker_id: str,
    author_id: str,
    target_person_id: str,
    upto_day: int | None = None,
    upto_utc: str | None = None,
) -> bool:
    """Boolean-only check: does an invisible private/restricted record from author_id mention target_person_id?

    Never returns content or record IDs. Used strictly to decide `refused: {'reason': 'private'}`.
    """
    asker_org = get_person_org(conn, asker_id)
    if not asker_org:
        return False
    target_row = get_person_row(conn, target_person_id)
    if target_row is None:
        return False

    day_limit = get_current_day(conn) if upto_day is None else upto_day
    sql = """
        SELECT org, visibility, author, readers_json, sanitized_body
        FROM records
        WHERE org = ? AND author = ? AND day <= ? AND is_forgotten = 0
          AND visibility IN ('private', 'restricted')
    """
    args: list[Any] = [asker_org, author_id, day_limit]
    if upto_utc is not None:
        sql += " AND at_utc <= ?"
        args.append(upto_utc)

    rows = conn.execute(sql, args).fetchall()
    terms = [t for t in (target_person_id, target_row["name"], target_row["first_name"]) if t]
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b", re.IGNORECASE)

    for r in rows:
        if can_view(r, asker_id, asker_org):
            continue
        if pattern.search(r["sanitized_body"] or ""):
            return True
    return False


def has_inaccessible_record_on_topic(
    conn: sqlite3.Connection,
    asker_id: str,
    subject_person_id: str,
    topic_terms: list[str],
    upto_day: int | None = None,
    upto_utc: str | None = None,
) -> bool:
    """Boolean-only check: does an invisible private/restricted record by subject_person_id match topic_terms?"""
    asker_org = get_person_org(conn, asker_id)
    if not asker_org or not topic_terms:
        return False
    day_limit = get_current_day(conn) if upto_day is None else upto_day
    sql = """
        SELECT org, visibility, author, readers_json, sanitized_body
        FROM records
        WHERE org = ? AND author = ? AND day <= ? AND is_forgotten = 0
          AND visibility IN ('private', 'restricted')
    """
    args: list[Any] = [asker_org, subject_person_id, day_limit]
    if upto_utc is not None:
        sql += " AND at_utc <= ?"
        args.append(upto_utc)

    pattern = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in topic_terms if t) + r")\b", re.IGNORECASE)
    for r in conn.execute(sql, args).fetchall():
        if can_view(r, asker_id, asker_org):
            continue
        if pattern.search(r["sanitized_body"] or ""):
            return True
    return False


def has_inaccessible_org_topic_match(
    conn: sqlite3.Connection,
    asker_id: str,
    topic_terms: list[str],
    client_id: str | None = None,
    min_hits: int = 2,
    upto_day: int | None = None,
    upto_utc: str | None = None,
) -> bool:
    """Boolean-only check: does an invisible private/restricted org record match >= min_hits topic terms?"""
    asker_org = get_person_org(conn, asker_id)
    clean_terms = [t.lower() for t in topic_terms if len(t) >= 3]
    if not asker_org or not clean_terms:
        return False
    day_limit = get_current_day(conn) if upto_day is None else upto_day
    sql = """
        SELECT org, client, visibility, author, readers_json, subject, title, sanitized_body
        FROM records
        WHERE org = ? AND day <= ? AND is_forgotten = 0
          AND visibility IN ('private', 'restricted')
    """
    args: list[Any] = [asker_org, day_limit]
    if client_id is not None:
        sql += " AND client = ?"
        args.append(client_id)
    if upto_utc is not None:
        sql += " AND at_utc <= ?"
        args.append(upto_utc)

    needed = min(min_hits, len(clean_terms))
    for r in conn.execute(sql, args).fetchall():
        if can_view(r, asker_id, asker_org):
            continue
        blob = " ".join(str(x or "") for x in (r["title"], r["subject"], r["sanitized_body"])).lower()
        hits = sum(
            1 for t in clean_terms
            if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", blob)
        )
        if hits >= needed:
            return True
    return False


def _get_forgotten_entries(conn: sqlite3.Connection, person_id: str) -> tuple[list[list[str]], set[str]]:
    rows = conn.execute(
        "SELECT topic_keywords_json, affected_record_ids_json FROM forgotten_topics WHERE person = ?",
        (person_id,),
    ).fetchall()
    kw_groups: list[list[str]] = []
    rec_ids: set[str] = set()
    for r in rows:
        try:
            kws = [str(k).lower() for k in json.loads(r["topic_keywords_json"] or "[]") if str(k).strip()]
            if kws:
                kw_groups.append(kws)
        except ValueError:
            pass
        try:
            ids = json.loads(r["affected_record_ids_json"] or "[]")
            rec_ids.update(str(i) for i in ids if i)
        except ValueError:
            pass
    return kw_groups, rec_ids


def _text_matches_forgotten(text: str, kw_groups: list[list[str]]) -> bool:
    low = (text or "").lower()
    if not low or not kw_groups:
        return False
    for group in kw_groups:
        if all(re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", low) for kw in group):
            return True
    return False


def _tainted_phrases(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Collect phrases from stripped injection spans so firewall blocks echoed directive payloads."""
    phrases = set(_INJECTION_GUARD_PHRASES)
    rows = conn.execute(
        "SELECT injected_spans_json FROM records WHERE has_injection_span = 1 AND injected_spans_json IS NOT NULL"
    ).fetchall()
    for r in rows:
        try:
            for span in json.loads(r["injected_spans_json"] or "[]"):
                stxt = str(span)
                m = re.search(r"\bas\s+([A-Za-z][A-Za-z0-9 _-]{1,30})", stxt)
                if m:
                    phrases.add(m.group(1).strip())
                for qm in re.finditer(
                    r"\b(?:say|write|output|include|print)\s+[\"']([^\"']{3,40})[\"']",
                    stxt,
                    re.IGNORECASE,
                ):
                    phrases.add(qm.group(1).strip())
        except ValueError:
            pass
    return tuple(sorted(phrases))


def _text_echoes_tainted_phrase(text: str, tainted: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(p.lower() in low for p in tainted if p)


def _fetch_record_map(conn: sqlite3.Connection, ids: set[str]) -> dict[str, sqlite3.Row]:
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM records WHERE id IN ({placeholders})",
        list(ids),
    ).fetchall()
    return {str(r["id"]): r for r in rows}


def _classify_citation(
    rid: str,
    rec_map: dict[str, sqlite3.Row],
    person_id: str,
    person_org: str | None,
    upto_day: int,
    upto_utc: str | None,
    forgotten_ids: set[str],
) -> str:
    """Return 'ok', 'privacy_leak', or 'invalid' for a single cited record id."""
    if not rid or rid in forgotten_ids:
        return "invalid"
    rec = rec_map.get(rid)
    if rec is None:
        return "invalid"
    if check_leak(rec, person_id, person_org) is not None:
        return "privacy_leak"
    if int(rec["day"]) > upto_day:
        return "invalid"
    if upto_utc is not None and str(rec["at_utc"]) > upto_utc:
        return "invalid"
    if int(rec["is_forgotten"]) != 0:
        return "invalid"
    if int(rec["has_injection_span"]) == 1 and not (rec["sanitized_body"] or "").strip():
        return "invalid"
    return "ok"


def _filter_sources(
    sources: list[Any],
    rec_map: dict[str, sqlite3.Row],
    person_id: str,
    person_org: str | None,
    upto_day: int,
    upto_utc: str | None,
    forgotten_ids: set[str],
) -> tuple[list[str], bool]:
    """Filter source IDs, returning (clean_sources, saw_privacy_leak)."""
    clean: list[str] = []
    saw_leak = False
    for raw in sources or []:
        rid = str(raw)
        verdict = _classify_citation(rid, rec_map, person_id, person_org, upto_day, upto_utc, forgotten_ids)
        if verdict == "ok" and rid not in clean:
            clean.append(rid)
        elif verdict == "privacy_leak":
            saw_leak = True
    return clean, saw_leak


def validate_brief_list(
    conn: sqlite3.Connection,
    briefs: list[dict[str, Any]],
    person_id: str,
    current_day: int | None = None,
) -> list[dict[str, Any]]:
    """Output firewall for `brief` responses: enforce visibility, day bounds, forget, and injection guards."""
    upto_day = get_current_day(conn) if current_day is None else current_day
    person_org = get_person_org(conn, person_id)
    kw_groups, forgotten_ids = _get_forgotten_entries(conn, person_id)
    tainted = _tainted_phrases(conn)

    all_ids: set[str] = set()
    for b in briefs:
        for c in b.get("changed") or []:
            all_ids.update(str(x) for x in (c.get("sources") or []))
        for p in b.get("promises") or []:
            all_ids.update(str(x) for x in (p.get("sources") or []))
        for o in b.get("open_these") or []:
            if o.get("record"):
                all_ids.add(str(o["record"]))
        q = b.get("question_to_ask")
        if isinstance(q, dict):
            all_ids.update(str(x) for x in (q.get("sources") or []))

    rec_map = _fetch_record_map(conn, all_ids)
    validated: list[dict[str, Any]] = []

    for b in briefs:
        out = dict(b)
        clean_changed = []
        for c in out.get("changed") or []:
            text = str(c.get("text") or "")
            if _text_matches_forgotten(text, kw_groups) or _text_echoes_tainted_phrase(text, tainted):
                continue
            srcs, _ = _filter_sources(c.get("sources") or [], rec_map, person_id, person_org, upto_day, None, forgotten_ids)
            if not srcs:
                continue
            clean_changed.append({"text": text, "sources": srcs})

        if out.get("status") == "nothing_new" or not clean_changed:
            out["status"] = "nothing_new"
            out["changed"] = []
            out["promises"] = []
            out["open_these"] = []
            out["question_to_ask"] = None
            validated.append(out)
            continue

        clean_promises = []
        for p in out.get("promises") or []:
            what = str(p.get("what") or "")
            if _text_matches_forgotten(what, kw_groups) or _text_echoes_tainted_phrase(what, tainted):
                continue
            srcs, _ = _filter_sources(p.get("sources") or [], rec_map, person_id, person_org, upto_day, None, forgotten_ids)
            if not srcs:
                continue
            clean_promises.append({**p, "sources": srcs})

        clean_open = []
        for o in out.get("open_these") or []:
            why = str(o.get("why") or "")
            rid = str(o.get("record") or "")
            if _text_matches_forgotten(why, kw_groups) or _text_echoes_tainted_phrase(why, tainted):
                continue
            if _classify_citation(rid, rec_map, person_id, person_org, upto_day, None, forgotten_ids) != "ok":
                continue
            clean_open.append({"record": rid, "why": why})

        q = out.get("question_to_ask")
        clean_q = None
        if isinstance(q, dict):
            qtext = str(q.get("text") or "")
            if not _text_matches_forgotten(qtext, kw_groups) and not _text_echoes_tainted_phrase(qtext, tainted):
                qsrcs, _ = _filter_sources(q.get("sources") or [], rec_map, person_id, person_org, upto_day, None, forgotten_ids)
                if qsrcs:
                    clean_q = {"text": qtext, "sources": qsrcs}

        out["changed"] = clean_changed
        out["promises"] = clean_promises
        out["open_these"] = clean_open
        out["question_to_ask"] = clean_q
        validated.append(out)

    return validated


def validate_ask_response(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    person_id: str,
    current_day: int | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    """Output firewall for `ask` responses: enforce visibility, day/at bounds, forget, and injection guards."""
    upto_day = get_current_day(conn) if current_day is None else current_day
    person_org = get_person_org(conn, person_id)
    kw_groups, forgotten_ids = _get_forgotten_entries(conn, person_id)
    tainted = _tainted_phrases(conn)

    out = dict(payload)
    all_ids: set[str] = set()
    ans = out.get("answer")
    if isinstance(ans, dict):
        all_ids.update(str(x) for x in (ans.get("sources") or []))
    for m in out.get("matched") or []:
        if isinstance(m, dict):
            all_ids.update(str(x) for x in (m.get("sources") or []))

    rec_map = _fetch_record_map(conn, all_ids)

    clean_matched = []
    matched_leak = False
    for m in out.get("matched") or []:
        if not isinstance(m, dict):
            continue
        phrase = str(m.get("phrase") or "")
        if _text_matches_forgotten(phrase, kw_groups):
            continue
        srcs, saw_leak = _filter_sources(
            m.get("sources") or [], rec_map, person_id, person_org, upto_day, at_utc, forgotten_ids
        )
        if saw_leak:
            matched_leak = True
            continue
        if m.get("sources") and not srcs:
            continue
        clean_matched.append({**m, "sources": srcs})
    out["matched"] = clean_matched

    if isinstance(ans, dict):
        atext = str(ans.get("text") or "")
        orig_sources = ans.get("sources") or []
        srcs, saw_leak = _filter_sources(
            orig_sources, rec_map, person_id, person_org, upto_day, at_utc, forgotten_ids
        )
        if saw_leak or matched_leak:
            out["answer"] = None
            out["matched"] = []
            out["clarify"] = None
            out["refused"] = {"reason": "private"}
            return out
        if _text_matches_forgotten(atext, kw_groups) or _text_echoes_tainted_phrase(atext, tainted) or (orig_sources and not srcs):
            out["answer"] = {"text": "I have nothing on that.", "sources": []}
            return out
        out["answer"] = {"text": atext, "sources": srcs}

    cl = out.get("clarify")
    if isinstance(cl, dict):
        qtext = str(cl.get("question") or "")
        if _text_matches_forgotten(qtext, kw_groups) or _text_echoes_tainted_phrase(qtext, tainted):
            out["clarify"] = None
            out["answer"] = {"text": "I have nothing on that.", "sources": []}
            return out
        clean_opts = [
            o for o in (cl.get("options") or [])
            if not _text_matches_forgotten(str(o), kw_groups) and not _text_echoes_tainted_phrase(str(o), tainted)
        ]
        out["clarify"] = {"question": qtext, "options": clean_opts}

    return out
