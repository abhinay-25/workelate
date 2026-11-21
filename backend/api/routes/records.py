"""Authorized single-record inspection endpoint for citation drill-down."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies.db import get_db, get_person_or_error
from backend.api.schemas.models import RecordDetailResponse
from src.security import (
    _get_forgotten_entries,
    _tainted_phrases,
    _text_echoes_tainted_phrase,
    _text_matches_forgotten,
    can_view,
    get_current_day,
)

router = APIRouter(tags=["records"])


@router.get("/records/{record_id}", response_model=RecordDetailResponse)
def get_authorized_record(
    record_id: str,
    person_id: str = Query(..., min_length=1),
    conn: sqlite3.Connection = Depends(get_db),
) -> RecordDetailResponse:
    """Return sanitized record details ONLY if authorized for person_id and not forgotten/tainted."""
    person = get_person_or_error(conn, person_id)
    person_org = person["org_id"]
    current_day = get_current_day(conn)

    rec = conn.execute("SELECT * FROM records WHERE id = ?", (record_id.strip(),)).fetchone()
    if rec is None or int(rec["day"]) > current_day:
        raise HTTPException(status_code=404, detail=f"Record '{record_id}' was not found.")

    if int(rec["is_forgotten"]) != 0:
        raise HTTPException(status_code=404, detail="This record has been forgotten and is no longer available.")

    if not can_view(rec, person_id, person_org):
        raise HTTPException(
            status_code=403,
            detail="Access denied: this record is private, restricted, or belongs to another organization.",
        )

    kw_groups, forgotten_ids = _get_forgotten_entries(conn, person_id)
    if str(rec["id"]) in forgotten_ids:
        raise HTTPException(status_code=404, detail="This record has been forgotten and is no longer available.")

    sanitized_body = str(rec["sanitized_body"] or "").strip()
    if int(rec["has_injection_span"]) == 1 and not sanitized_body:
        raise HTTPException(
            status_code=403,
            detail="This record was suppressed by the security filter due to prompt-injection directives.",
        )

    tainted = _tainted_phrases(conn)
    blob = f"{rec['title'] or ''} {rec['subject'] or ''} {sanitized_body}"
    if _text_matches_forgotten(blob, kw_groups) or _text_echoes_tainted_phrase(sanitized_body, tainted):
        raise HTTPException(status_code=404, detail="This content is unavailable due to memory suppression rules.")

    author_id = str(rec["author"] or "")
    author_row = conn.execute("SELECT name FROM people WHERE person_id = ?", (author_id,)).fetchone()
    author_name = str(author_row["name"]) if author_row else author_id

    cid = rec["client"]
    client_name = None
    if cid:
        crow = conn.execute("SELECT name FROM clients WHERE client_id = ?", (cid,)).fetchone()
        client_name = str(crow["name"]) if crow else str(cid)

    return RecordDetailResponse(
        id=str(rec["id"]),
        day=int(rec["day"]),
        org=str(rec["org"]),
        type=str(rec["type"]),
        at_utc=str(rec["at_utc"]),
        local_date=str(rec["local_date"] or ""),
        author_id=author_id,
        author_name=author_name,
        client_id=cid,
        client_name=client_name,
        visibility=str(rec["visibility"]),
        channel=rec["channel"],
        subject=rec["subject"],
        title=rec["title"],
        sanitized_body=sanitized_body,
        has_injection_span=bool(int(rec["has_injection_span"])),
        is_boilerplate=bool(int(rec["is_boilerplate"])),
        is_newsletter=bool(int(rec["is_newsletter"])),
    )
