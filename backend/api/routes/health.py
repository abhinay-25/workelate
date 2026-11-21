"""Health check and challenge persona listing endpoints."""

from __future__ import annotations

import sqlite3
from fastapi import APIRouter, Depends

from backend.api.dependencies.db import (
    get_available_dates,
    get_db,
    resolve_default_date,
)
from backend.api.schemas.models import HealthResponse, PeopleResponse, PersonSchema
from src.db import get_ingested_days
from src.security import get_current_day

router = APIRouter(tags=["health", "people"])


@router.get("/health", response_model=HealthResponse)
def health_check(conn: sqlite3.Connection = Depends(get_db)) -> HealthResponse:
    """Return backend health, DB initialization state, ingested days, and record/vector counts."""
    current_day = get_current_day(conn)
    ingested_days = get_ingested_days(conn)
    total_records = int(
        conn.execute("SELECT COUNT(*) FROM records WHERE is_forgotten = 0").fetchone()[0]
    )
    indexed_vectors = int(conn.execute("SELECT COUNT(*) FROM record_vectors").fetchone()[0])
    return HealthResponse(
        status="ok",
        db_connected=True,
        current_day=current_day,
        ingested_days=ingested_days,
        total_records=total_records,
        indexed_vectors=indexed_vectors,
        default_date=resolve_default_date(conn),
        available_dates=get_available_dates(conn),
        version="1.0.0",
    )


@router.get("/people", response_model=PeopleResponse)
def list_people(conn: sqlite3.Connection = Depends(get_db)) -> PeopleResponse:
    """List all challenge personas and organizations for UI persona switching."""
    orgs_rows = conn.execute("SELECT org_id, name FROM orgs ORDER BY org_id ASC").fetchall()
    people_rows = conn.execute(
        """
        SELECT p.person_id, p.org_id, o.name AS org_name, p.name, p.first_name,
               p.last_name, p.role, p.team, p.zone
        FROM people p
        JOIN orgs o ON o.org_id = p.org_id
        ORDER BY p.org_id DESC, p.person_id ASC
        """
    ).fetchall()
    return PeopleResponse(
        people=[
            PersonSchema(
                person_id=str(r["person_id"]),
                org_id=str(r["org_id"]),
                org_name=str(r["org_name"]),
                name=str(r["name"]),
                first_name=str(r["first_name"]),
                last_name=str(r["last_name"]),
                role=str(r["role"]),
                team=r["team"],
                zone=str(r["zone"]),
            )
            for r in people_rows
        ],
        orgs=[{"org_id": str(o["org_id"]), "name": str(o["name"])} for o in orgs_rows],
    )
