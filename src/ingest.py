"""Daily ingestion pipeline for reference data, prior-day feedback, and records."""

import json
import os
import sqlite3
from typing import Any

from src.config import db_path, pack_root
from src.claims import rebuild_claims
from src.db import get_connection, get_ingested_days, mark_day_ingested, reset_db, set_meta
from src.docs_engine import format_sheet_cells, rebuild_doc_versions
from src.entities import extract_entities_for_day
from src.filters import classify_noise, sanitize_text
from src.memory import process_feedback_for_day
from src.promises import rebuild_promises
from src.retrieval import index_records_for_day
from src.tasks_engine import ingest_tasks_for_day
from src.time_utils import DEFAULT_ZONE, utc_to_local_date
from src.usage import UsageTracker


def _split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def load_reference_data(conn: sqlite3.Connection, pack_dir: str) -> dict[str, str]:
    """Load/refresh people.json and clients.json into SQLite. Returns {person_id: zone}."""
    person_zones: dict[str, str] = {}
    people_path = os.path.join(pack_dir, "data", "people.json")
    clients_path = os.path.join(pack_dir, "data", "clients.json")

    default_org: str | None = None
    if os.path.isfile(people_path):
        with open(people_path, encoding="utf-8") as f:
            people_data = json.load(f)
        for org_id, org_info in people_data.items():
            if default_org is None:
                default_org = org_id
            org_name = org_info.get("name") or org_id
            conn.execute(
                "INSERT INTO orgs(org_id, name) VALUES (?, ?) "
                "ON CONFLICT(org_id) DO UPDATE SET name = excluded.name",
                (org_id, org_name),
            )
            for p in org_info.get("people") or []:
                pid = p["id"]
                name = p.get("name") or pid
                first_name, last_name = _split_name(name)
                role = p.get("role") or ""
                team = p.get("team")
                zone = p.get("zone") or DEFAULT_ZONE
                person_zones[pid] = zone
                conn.execute(
                    """
                    INSERT INTO people(person_id, org_id, name, first_name, last_name, role, team, zone)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        org_id = excluded.org_id,
                        name = excluded.name,
                        first_name = excluded.first_name,
                        last_name = excluded.last_name,
                        role = excluded.role,
                        team = excluded.team,
                        zone = excluded.zone
                    """,
                    (pid, org_id, name, first_name, last_name, role, team, zone),
                )

    if os.path.isfile(clients_path):
        with open(clients_path, encoding="utf-8") as f:
            clients_data = json.load(f)
        target_org = default_org or "org_kp"
        for c in clients_data:
            cid = c["id"]
            cname = c.get("name") or cid
            corg = c.get("org") or target_org
            conn.execute(
                "INSERT OR IGNORE INTO orgs(org_id, name) VALUES (?, ?)",
                (corg, corg),
            )
            conn.execute(
                """
                INSERT INTO clients(client_id, org_id, name) VALUES (?, ?, ?)
                ON CONFLICT(client_id) DO UPDATE SET
                    org_id = excluded.org_id,
                    name = excluded.name
                """,
                (cid, corg, cname),
            )

    return person_zones


def ingest_feedback_file(
    conn: sqlite3.Connection,
    day_written: int,
    ingested_on_day: int,
    pack_dir: str,
) -> int:
    """Ingest data/feedback/day-(N-1).jsonl when N >= 2."""
    fb_path = os.path.join(pack_dir, "data", "feedback", f"day-{day_written:02d}.jsonl")
    if not os.path.isfile(fb_path):
        return 0
    count = 0
    with open(fb_path, encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            item = json.loads(raw)
            conn.execute(
                """
                INSERT INTO feedback(day, ingested_on_day, person, type, meeting, topic, verdict, words)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(item.get("day", day_written)),
                    ingested_on_day,
                    item.get("person") or "",
                    item.get("type") or "",
                    item.get("meeting"),
                    item.get("topic"),
                    item.get("verdict"),
                    item.get("words") or "",
                ),
            )
            count += 1
    return count


def _normalize_record(
    rec: dict[str, Any],
    day: int,
    line_no: int,
    person_zones: dict[str, str],
) -> tuple[Any, ...]:
    author = str(rec.get("author") or "")
    zone = person_zones.get(author, DEFAULT_ZONE)
    at_utc = str(rec.get("at") or "")
    local_date = utc_to_local_date(at_utc, zone) if at_utc else ""

    raw_body = str(rec.get("body") or "")
    sanitized_body, has_inj, stripped_spans = sanitize_text(raw_body)

    raw_subject = rec.get("subject")
    if raw_subject is not None:
        clean_subj, subj_has_inj, subj_spans = sanitize_text(str(raw_subject))
        if subj_has_inj:
            has_inj = 1
            stripped_spans.extend(subj_spans)
        subject = clean_subj
    else:
        subject = None

    is_boilerplate, is_newsletter = classify_noise(rec, sanitized_body)

    readers = rec.get("readers")
    to_list = rec.get("to")
    cells = rec.get("cells")
    attendees = rec.get("attendees")
    title_val = rec.get("title") or rec.get("name")
    if (rec.get("type") or "").strip().lower() == "sheet" and isinstance(cells, dict):
        sheet_lines, _ = format_sheet_cells(cells)
        if sheet_lines:
            joined = "; ".join(sheet_lines)
            sanitized_body = f"{sanitized_body.rstrip('.')}: {joined}." if sanitized_body else f"{joined}."

    return (
        rec["id"],
        day,
        line_no,
        rec.get("org") or "org_kp",
        rec.get("type") or "chat",
        at_utc,
        local_date,
        author,
        rec.get("client"),
        rec.get("visibility") or "team",
        json.dumps(readers, ensure_ascii=False) if readers is not None else None,
        rec.get("channel"),
        json.dumps(to_list, ensure_ascii=False) if to_list is not None else None,
        subject,
        title_val,
        rec.get("doc_id"),
        rec.get("sheet_id"),
        rec.get("version"),
        json.dumps(cells, ensure_ascii=False) if cells is not None else None,
        rec.get("task_id"),
        rec.get("event_id"),
        rec.get("start"),
        json.dumps(attendees, ensure_ascii=False) if attendees is not None else None,
        raw_body,
        sanitized_body,
        has_inj,
        json.dumps(stripped_spans, ensure_ascii=False) if stripped_spans else None,
        is_boilerplate,
        is_newsletter,
        0,
    )


def ingest_day(
    day: int,
    pack_dir: str | None = None,
    db_file: str | None = None,
) -> dict[str, Any]:
    """Ingest day N records and (for N >= 2) day N-1 feedback into SQLite."""
    resolved_pack = os.path.abspath(pack_dir or pack_root())
    resolved_db = os.path.abspath(db_file or db_path())

    if day == 1:
        reset_db(resolved_db)

    conn = get_connection(resolved_db)
    try:
        if day in get_ingested_days(conn):
            return {"ok": True, "day": day, "skipped": "already_ingested"}

        person_zones = load_reference_data(conn, resolved_pack)
        fb_count = ingest_feedback_file(conn, day - 1, day, resolved_pack) if day >= 2 else 0

        day_path = os.path.join(resolved_pack, "data", "days", f"day-{day:02d}.jsonl")
        if not os.path.isfile(day_path):
            raise FileNotFoundError(f"Missing day file: {day_path}")

        raw_records: list[dict[str, Any]] = []
        rows_to_insert: list[tuple[Any, ...]] = []
        with open(day_path, encoding="utf-8") as f:
            for idx, line in enumerate(f):
                raw = line.strip()
                if not raw:
                    continue
                rec = json.loads(raw)
                raw_records.append(rec)
                rows_to_insert.append(_normalize_record(rec, day, idx, person_zones))

        conn.executemany(
            """
            INSERT OR REPLACE INTO records(
                id, day, line_no, org, type, at_utc, local_date, author, client,
                visibility, readers_json, channel, to_json, subject, title,
                doc_id, sheet_id, version, cells_json, task_id, event_id,
                start_utc, attendees_json, body, sanitized_body,
                has_injection_span, injected_spans_json,
                is_boilerplate, is_newsletter, is_forgotten
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            rows_to_insert,
        )

        # Invalidate brief cache whenever new day data arrives.
        conn.execute("DELETE FROM brief_cache")

        extract_entities_for_day(conn, day)
        ingest_tasks_for_day(conn, raw_records, day)
        rebuild_promises(conn)
        rebuild_doc_versions(conn)
        rebuild_claims(conn)
        vec_stats: dict[str, Any] = {"indexed": 0, "skipped": 0}
        try:
            vec_stats = index_records_for_day(conn, day, tracker=UsageTracker())
        except Exception as exc:
            vec_stats = {"indexed": 0, "skipped": 0, "error": str(exc)}
        if day >= 2:
            process_feedback_for_day(conn, day)

        mark_day_ingested(conn, day)
        set_meta(conn, "pack_root", resolved_pack)
        conn.commit()
        return {"ok": True, "day": day, "records": len(rows_to_insert), "feedback": fb_count, "vectors": vec_stats}
    finally:
        conn.close()
