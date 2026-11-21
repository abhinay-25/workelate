"""Pre-meeting briefs endpoint wrapping src/brief.py."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, Depends

from backend.api.dependencies.db import (
    get_db,
    get_person_or_error,
    log_usage_event,
)
from backend.api.schemas.models import (
    BriefChangeItem,
    BriefOpenRecordItem,
    BriefPromiseItem,
    BriefQuestionItem,
    BriefRequest,
    BriefResponse,
    EnrichedBriefItem,
    UsageMetricsSchema,
)
from src.brief import generate_briefs_for_person_date
from src.security import (
    get_current_day,
    visible_records_params,
    visible_records_where_clause,
)
from src.usage import UsageTracker

router = APIRouter(tags=["brief"])


@router.post("/brief", response_model=BriefResponse)
def generate_brief(
    req: BriefRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> BriefResponse:
    """Generate (or return cached) ACL-safe pre-meeting briefs for a person and local date."""
    person = get_person_or_error(conn, req.person_id)
    person_id = person["person_id"]
    person_org = person["org_id"]
    current_day = get_current_day(conn)

    if req.regenerate:
        conn.execute(
            "DELETE FROM brief_cache WHERE person = ? AND date = ?",
            (person_id, req.date),
        )
        conn.commit()

    t0 = time.monotonic()
    tracker = UsageTracker()
    raw_briefs = generate_briefs_for_person_date(conn, person_id, req.date, tracker=tracker)
    elapsed_ms = max(0, int((time.monotonic() - t0) * 1000))

    people_map = {
        str(r["person_id"]): str(r["name"])
        for r in conn.execute("SELECT person_id, name FROM people").fetchall()
    }
    clients_map = {
        str(c["client_id"]): str(c["name"])
        for c in conn.execute("SELECT client_id, name FROM clients").fetchall()
    }

    # Fetch authorized meeting metadata keyed by event_id
    where = visible_records_where_clause("r", with_utc_cutoff=False)
    params = visible_records_params(person_id, person_org, current_day)
    ev_rows = conn.execute(
        f"""
        SELECT r.*
        FROM records r
        WHERE r.type = 'event' AND {where}
        ORDER BY r.at_utc ASC, r.day ASC
        """,
        params,
    ).fetchall()
    ev_meta_by_mid: dict[str, sqlite3.Row] = {}
    for row in ev_rows:
        mid = str(row["event_id"] or row["id"])
        ev_meta_by_mid[mid] = row

    enriched: list[EnrichedBriefItem] = []
    tot_in = 0
    tot_out = 0
    tot_cost = 0.0
    tot_ms = 0

    for item in raw_briefs:
        mid = str(item.get("meeting_id") or "")
        ev = ev_meta_by_mid.get(mid)
        cid = str(ev["client"]) if (ev is not None and ev["client"]) else None
        if req.client_id and cid != req.client_id:
            continue

        attendees: list[str] = []
        if ev is not None and ev["attendees_json"]:
            try:
                attendees = [str(x) for x in json.loads(ev["attendees_json"] or "[]")]
            except ValueError:
                attendees = []

        u = item.get("usage") or {}
        tot_in += int(u.get("input_tokens") or 0)
        tot_out += int(u.get("output_tokens") or 0)
        tot_cost += float(u.get("cost_usd") or 0.0)
        tot_ms += int(u.get("ms") or 0)

        promises_enriched = [
            BriefPromiseItem(
                owner=str(p.get("owner") or ""),
                owner_name=people_map.get(str(p.get("owner") or ""), str(p.get("owner") or "")),
                what=str(p.get("what") or ""),
                due=str(p.get("due") or ""),
                sources=[str(s) for s in (p.get("sources") or [])],
            )
            for p in (item.get("promises") or [])
        ]

        q_item = item.get("question_to_ask")
        q_schema = (
            BriefQuestionItem(
                text=str(q_item.get("text") or ""),
                sources=[str(s) for s in (q_item.get("sources") or [])],
            )
            if isinstance(q_item, dict)
            else None
        )

        enriched.append(
            EnrichedBriefItem(
                meeting_id=mid,
                meeting_title=str(ev["title"] or ev["subject"] or mid) if ev is not None else mid,
                client_id=cid,
                client_name=clients_map.get(cid, cid) if cid else None,
                start_utc=str(ev["start_utc"]) if (ev is not None and ev["start_utc"]) else None,
                attendees=attendees,
                status="brief" if item.get("status") == "brief" else "nothing_new",
                changed=[
                    BriefChangeItem(
                        text=str(c.get("text") or ""),
                        sources=[str(s) for s in (c.get("sources") or [])],
                    )
                    for c in (item.get("changed") or [])
                ],
                promises=promises_enriched,
                open_these=[
                    BriefOpenRecordItem(record=str(o.get("record") or ""), why=str(o.get("why") or ""))
                    for o in (item.get("open_these") or [])
                ],
                question_to_ask=q_schema,
                usage=UsageMetricsSchema(
                    input_tokens=int(u.get("input_tokens") or 0),
                    output_tokens=int(u.get("output_tokens") or 0),
                    cost_usd=float(u.get("cost_usd") or 0.0),
                    ms=int(u.get("ms") or 0),
                ),
            )
        )

    is_llm = tot_in > 0 or tot_out > 0
    used_provider = tracker.last_provider or ("deterministic" if not is_llm else "llm")
    used_model = tracker.last_model or ("rule-engine" if not is_llm else "unknown")
    total_usage = UsageMetricsSchema(
        input_tokens=tot_in,
        output_tokens=tot_out,
        cost_usd=round(tot_cost, 6),
        ms=max(tot_ms, elapsed_ms),
        mode="llm_assisted" if is_llm else "deterministic",
        provider=used_provider,
        model=used_model,
    )
    log_usage_event(
        conn,
        operation="brief",
        person_id=person_id,
        org_id=person_org,
        usage=total_usage.model_dump(),
        latency_ms=elapsed_ms,
        status="ok",
        detail=f"date={req.date} meetings={len(enriched)}",
        provider=used_provider,
        model=used_model,
    )

    return BriefResponse(
        person_id=person_id,
        date=req.date,
        client_id=req.client_id,
        briefs=enriched,
        total_usage=total_usage,
    )
