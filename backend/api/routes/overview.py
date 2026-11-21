"""Person-scoped dashboard overview endpoint."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from backend.api.dependencies.db import (
    get_available_dates,
    get_db,
    get_person_or_error,
    resolve_default_date,
)
from backend.api.schemas.models import (
    ActivityItemSchema,
    ClientSchema,
    DeadlineItemSchema,
    MeetingSummarySchema,
    OverviewMetricsSchema,
    OverviewResponse,
    PersonSchema,
)
from src.audit import audit_for_person
from src.security import (
    _get_forgotten_entries,
    _tainted_phrases,
    _text_echoes_tainted_phrase,
    _text_matches_forgotten,
    can_view,
    get_current_day,
    visible_records_params,
    visible_records_where_clause,
)
from src.time_utils import utc_to_local_date

router = APIRouter(tags=["overview"])


@router.get("/overview", response_model=OverviewResponse)
def get_overview(
    person_id: str = Query(..., min_length=1),
    date: Optional[str] = Query(None),
    conn: sqlite3.Connection = Depends(get_db),
) -> OverviewResponse:
    """Return authorized summary metrics, activity feed, upcoming deadlines, and meetings on date."""
    person = get_person_or_error(conn, person_id)
    person_org = person["org_id"]
    person_zone = person["zone"]
    current_day = get_current_day(conn)
    selected_date = (date or "").strip() or resolve_default_date(conn)

    people_map = {
        str(r["person_id"]): str(r["name"])
        for r in conn.execute("SELECT person_id, name FROM people").fetchall()
    }
    clients_rows = conn.execute(
        """
        SELECT DISTINCT c.client_id, ? AS org_id, c.name
        FROM clients c
        WHERE c.org_id = ?
           OR EXISTS (
               SELECT 1 FROM records r
               WHERE r.org = ? AND r.client = c.client_id AND r.is_forgotten = 0
           )
        ORDER BY c.name ASC
        """,
        (person_org, person_org, person_org),
    ).fetchall()
    clients_map = {str(c["client_id"]): str(c["name"]) for c in clients_rows}

    kw_groups, forgotten_ids = _get_forgotten_entries(conn, person_id)
    tainted = _tainted_phrases(conn)

    # Audit state gives strictly authorized tasks, promises, claims
    audit_state = audit_for_person(conn, person_id)

    # Upcoming deadlines: active tasks + open promises
    deadlines: list[DeadlineItemSchema] = []
    active_task_count = 0
    blocked_task_count = 0

    for t in audit_state.get("tasks") or []:
        st = str(t.get("status") or "")
        if st not in ("done", "cancelled"):
            active_task_count += 1
        if st == "blocked":
            blocked_task_count += 1
        if st in ("done", "cancelled"):
            continue
        owner_id = str(t.get("owner") or "")
        cid = t.get("client")
        t_row = conn.execute("SELECT blocked_reason FROM tasks_state WHERE task_id = ?", (t["task_id"],)).fetchone()
        raw_blocker = str(t_row["blocked_reason"]) if (t_row and t_row["blocked_reason"]) else None
        if raw_blocker and (
            _text_matches_forgotten(raw_blocker, kw_groups)
            or _text_echoes_tainted_phrase(raw_blocker, tainted)
        ):
            raw_blocker = None
        deadlines.append(
            DeadlineItemSchema(
                kind="task",
                item_id=str(t["task_id"]),
                title=str(t["title"]),
                owner_id=owner_id,
                owner_name=people_map.get(owner_id, owner_id),
                client_id=cid,
                client_name=clients_map.get(cid, cid) if cid else None,
                due_date=str(t.get("due") or ""),
                status=st,
                blocker=raw_blocker,
                sources=list(t.get("sources") or []),
            )
        )

    promises_list = audit_state.get("promises") or []
    for idx, p in enumerate(promises_list):
        owner_id = str(p.get("owner") or "")
        cid = p.get("client")
        deadlines.append(
            DeadlineItemSchema(
                kind="promise",
                item_id=f"promise-{idx + 1}",
                title=str(p.get("what") or ""),
                owner_id=owner_id,
                owner_name=people_map.get(owner_id, owner_id),
                client_id=cid,
                client_name=clients_map.get(cid, cid) if cid else None,
                due_date=str(p.get("due") or ""),
                status="open",
                blocker=None,
                sources=list(p.get("sources") or []),
            )
        )
    deadlines.sort(key=lambda d: (d.due_date or "9999-99-99", d.kind))

    # Visible records count & recent activity feed
    where = visible_records_where_clause("r", with_utc_cutoff=False)
    params = visible_records_params(person_id, person_org, current_day)

    visible_total = int(
        conn.execute(f"SELECT COUNT(*) FROM records r WHERE {where}", params).fetchone()[0]
    )

    rec_rows = conn.execute(
        f"""
        SELECT r.*
        FROM records r
        WHERE {where}
          AND r.is_boilerplate = 0
          AND r.is_newsletter = 0
          AND r.type != 'event'
          AND TRIM(COALESCE(r.sanitized_body, '')) != ''
        ORDER BY r.at_utc DESC, r.line_no DESC
        LIMIT 80
        """,
        params,
    ).fetchall()

    recent_activity: list[ActivityItemSchema] = []
    for r in rec_rows:
        rid = str(r["id"])
        if rid in forgotten_ids or not can_view(r, person_id, person_org):
            continue
        body = str(r["sanitized_body"] or "").strip()
        title_or_subj = r["title"] or r["subject"]
        blob = f"{title_or_subj or ''} {body}"
        if _text_matches_forgotten(blob, kw_groups) or _text_echoes_tainted_phrase(body, tainted):
            continue
        author_id = str(r["author"] or "")
        cid = r["client"]
        recent_activity.append(
            ActivityItemSchema(
                record_id=rid,
                type=str(r["type"]),
                at_utc=str(r["at_utc"]),
                local_date=str(r["local_date"] or ""),
                author_id=author_id,
                author_name=people_map.get(author_id, author_id),
                client_id=cid,
                client_name=clients_map.get(cid, cid) if cid else None,
                visibility=str(r["visibility"]),
                channel=r["channel"],
                title_or_subject=title_or_subj,
                preview=body[:220] + ("..." if len(body) > 220 else ""),
                has_injection_span=bool(int(r["has_injection_span"])),
            )
        )
        if len(recent_activity) >= 15:
            break

    # Meetings attended by person_id on selected_date (deduplicating rescheduled events)
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
    latest_by_mid: dict[str, sqlite3.Row] = {}
    for row in ev_rows:
        mid = str(row["event_id"] or row["id"])
        latest_by_mid[mid] = row

    cached_brief_mids: set[str] = set()
    cached_row = conn.execute(
        "SELECT response_json FROM brief_cache WHERE person = ? AND date = ? AND day = ?",
        (person_id, selected_date, current_day),
    ).fetchone()
    if cached_row:
        try:
            for item in json.loads(cached_row["response_json"] or "[]"):
                if isinstance(item, dict) and item.get("meeting_id"):
                    cached_brief_mids.add(str(item["meeting_id"]))
        except ValueError:
            pass

    meetings_on_date: list[MeetingSummarySchema] = []
    for mid, ev in latest_by_mid.items():
        if utc_to_local_date(str(ev["start_utc"]), person_zone) != selected_date:
            continue
        try:
            attendees = [str(x) for x in json.loads(ev["attendees_json"] or "[]")]
        except ValueError:
            attendees = []
        if person_id not in attendees and str(ev["author"]) != person_id:
            continue
        cid = ev["client"]
        meetings_on_date.append(
            MeetingSummarySchema(
                meeting_id=mid,
                title=str(ev["title"] or ev["subject"] or mid),
                client_id=cid,
                client_name=clients_map.get(cid, cid) if cid else None,
                start_utc=str(ev["start_utc"]),
                attendees=attendees,
                has_cached_brief=(mid in cached_brief_mids),
            )
        )
    meetings_on_date.sort(key=lambda m: m.start_utc)

    return OverviewResponse(
        person=PersonSchema(**person),
        current_day=current_day,
        selected_date=selected_date,
        available_dates=get_available_dates(conn),
        metrics=OverviewMetricsSchema(
            active_tasks=active_task_count,
            blocked_tasks=blocked_task_count,
            pending_commitments=len(promises_list),
            tracked_claims=len(audit_state.get("claims") or []),
            meetings_on_date=len(meetings_on_date),
            visible_records=visible_total,
        ),
        clients=[
            ClientSchema(client_id=str(c["client_id"]), org_id=str(c["org_id"]), name=str(c["name"]))
            for c in clients_rows
        ],
        recent_activity=recent_activity,
        upcoming_deadlines=deadlines[:20],
        meetings_on_date=meetings_on_date,
    )
