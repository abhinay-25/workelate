"""Authorized state introspection (`assist audit --person P`)."""

import sqlite3
from typing import Any

from src.claims import get_winning_claim
from src.memory import get_person_preferences
from src.promises import get_open_promises
from src.security import (
    _get_forgotten_entries,
    _tainted_phrases,
    _text_echoes_tainted_phrase,
    _text_matches_forgotten,
    can_view,
    get_current_day,
    get_person_row,
    visible_records_params,
    visible_records_where_clause,
)
from src.tasks_engine import get_visible_tasks_at
from src.time_utils import utc_to_local_date


def _filter_visible_record_ids(
    conn: sqlite3.Connection,
    raw_ids: list[str],
    person_id: str,
    person_org: str,
    upto_day: int,
    forgotten_ids: set[str],
) -> list[str]:
    clean: list[str] = []
    for rid in raw_ids:
        sid = str(rid or "").strip()
        if not sid or sid in forgotten_ids or sid in clean:
            continue
        rec = conn.execute("SELECT * FROM records WHERE id = ?", (sid,)).fetchone()
        if rec is None:
            continue
        if int(rec["day"]) > upto_day or int(rec["is_forgotten"]) != 0:
            continue
        if int(rec["has_injection_span"]) == 1 and not (rec["sanitized_body"] or "").strip():
            continue
        if can_view(rec, person_id, person_org):
            clean.append(sid)
    return clean


def audit_for_person(conn: sqlite3.Connection, person_id: str) -> dict[str, Any]:
    """Return authorized, non-forgotten, untainted state currently remembered for person_id."""
    current_day = get_current_day(conn)
    person_row = get_person_row(conn, person_id)
    if person_row is None or current_day <= 0:
        return {
            "person": person_id,
            "current_day": current_day,
            "profile": None,
            "preferences": {},
            "claims": [],
            "tasks": [],
            "promises": [],
            "personal_notes": [],
        }

    person_org = str(person_row["org_id"])
    kw_groups, forgotten_ids = _get_forgotten_entries(conn, person_id)
    tainted = _tainted_phrases(conn)

    latest_row = conn.execute(
        "SELECT MAX(at_utc) AS max_utc FROM records WHERE day <= ?",
        (current_day,),
    ).fetchone()
    current_local_date = (
        utc_to_local_date(str(latest_row["max_utc"]), str(person_row["zone"]))
        if latest_row and latest_row["max_utc"]
        else None
    )

    profile = {
        "person_id": person_id,
        "name": str(person_row["name"]),
        "role": str(person_row["role"]),
        "team": person_row["team"],
        "org": person_org,
        "zone": str(person_row["zone"]),
    }

    preferences = get_person_preferences(conn, person_id, upto_day=current_day)

    subj_rows = conn.execute(
        """
        SELECT DISTINCT subject_key
        FROM claims
        WHERE org = ? AND is_forgotten = 0 AND day <= ?
        ORDER BY subject_key ASC
        """,
        (person_org, current_day),
    ).fetchall()

    claims_out: list[dict[str, Any]] = []
    for row in subj_rows:
        sk = str(row["subject_key"])
        win = get_winning_claim(
            conn,
            person_id,
            sk,
            upto_day=current_day,
            query_date=current_local_date,
        )
        if not win:
            continue
        blob = f"{win['subject_key']} {win['object_val']}"
        if _text_matches_forgotten(blob, kw_groups) or _text_echoes_tainted_phrase(blob, tainted):
            continue
        srcs = _filter_visible_record_ids(
            conn, win.get("sources") or [], person_id, person_org, current_day, forgotten_ids
        )
        # Allow feedback corrections without record sources only if source_kind == feedback_correction
        if not srcs and win.get("source_kind") != "feedback_correction":
            continue
        claims_out.append(
            {
                "subject_key": win["subject_key"],
                "predicate": win["predicate"],
                "client": win["client"],
                "value": win["object_val"],
                "old_value": win["old_val"],
                "effective_at_utc": win["effective_at_utc"],
                "valid_from": win["valid_from"],
                "valid_until": win["valid_until"],
                "sources": srcs,
            }
        )

    raw_tasks = get_visible_tasks_at(conn, person_id, upto_day=current_day)
    tasks_out: list[dict[str, Any]] = []
    for t in raw_tasks:
        tblob = f"{t['title']} {t.get('blocked_reason') or ''}"
        if _text_matches_forgotten(tblob, kw_groups) or _text_echoes_tainted_phrase(tblob, tainted):
            continue
        cand_srcs = [
            str(x)
            for x in (
                t.get("last_status_record_id"),
                t.get("last_owner_record_id"),
                t.get("last_due_record_id"),
                t.get("created_record_id"),
            )
            if x
        ]
        srcs = _filter_visible_record_ids(
            conn, cand_srcs, person_id, person_org, current_day, forgotten_ids
        )
        if not srcs:
            continue
        tasks_out.append(
            {
                "task_id": t["task_id"],
                "title": t["title"],
                "client": t["client"],
                "owner": t["owner"],
                "due": t["due"],
                "status": t["status"],
                "sources": srcs,
            }
        )

    raw_promises = get_open_promises(conn, person_id, upto_day=current_day)
    promises_out: list[dict[str, Any]] = []
    for p in raw_promises:
        what = str(p["what"])
        if _text_matches_forgotten(what, kw_groups) or _text_echoes_tainted_phrase(what, tainted):
            continue
        srcs = _filter_visible_record_ids(
            conn, p.get("sources") or [], person_id, person_org, current_day, forgotten_ids
        )
        if not srcs:
            continue
        promises_out.append(
            {
                "owner": p["owner"],
                "what": what,
                "due": p["due"],
                "client": p["client"],
                "sources": srcs,
            }
        )

    where = visible_records_where_clause("r", with_utc_cutoff=False)
    params = visible_records_params(person_id, person_org, current_day)
    note_rows = conn.execute(
        f"""
        SELECT r.id, r.visibility, r.sanitized_body, r.at_utc
        FROM records r
        WHERE r.visibility IN ('private', 'restricted')
          AND r.is_boilerplate = 0
          AND r.is_newsletter = 0
          AND TRIM(COALESCE(r.sanitized_body, '')) != ''
          AND {where}
        ORDER BY r.at_utc ASC
        """,
        params,
    ).fetchall()

    personal_notes: list[dict[str, Any]] = []
    for nr in note_rows:
        rid = str(nr["id"])
        body = str(nr["sanitized_body"]).strip()
        if rid in forgotten_ids:
            continue
        if _text_matches_forgotten(body, kw_groups) or _text_echoes_tainted_phrase(body, tainted):
            continue
        personal_notes.append(
            {
                "text": body,
                "visibility": str(nr["visibility"]),
                "at_utc": str(nr["at_utc"]),
                "sources": [rid],
            }
        )

    return {
        "person": person_id,
        "current_day": current_day,
        "profile": profile,
        "preferences": preferences,
        "claims": claims_out,
        "tasks": tasks_out,
        "promises": promises_out,
        "personal_notes": personal_notes,
    }
