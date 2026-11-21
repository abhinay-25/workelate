"""Safe manual daily ingestion endpoint wrapping src/ingest.py."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, HTTPException

from backend.api.dependencies.db import ensure_db_initialized, log_usage_event
from backend.api.schemas.models import IngestRequest, IngestResponse
from src.config import db_path, pack_root
from src.ingest import ingest_day

router = APIRouter(tags=["ingest"])


@router.post("/ingest", response_model=IngestResponse)
def ingest_day_endpoint(req: IngestRequest) -> IngestResponse:
    """Ingest a valid challenge day-NN.jsonl pack file into SQLite + vectors."""
    pdir = pack_root()
    day_file = os.path.join(pdir, "data", "days", f"day-{req.day:02d}.jsonl")
    if not os.path.isfile(day_file):
        raise HTTPException(
            status_code=404,
            detail=f"Data pack file for day {req.day} was not found.",
        )

    t0 = time.monotonic()
    res = ingest_day(req.day, pack_dir=pdir, db_file=db_path())
    elapsed_ms = max(0, int((time.monotonic() - t0) * 1000))

    conn = ensure_db_initialized()
    try:
        log_usage_event(
            conn,
            operation="ingest",
            latency_ms=elapsed_ms,
            status="skipped" if res.get("skipped") else "ok",
            detail=f"day={req.day} records={res.get('records', 0)}",
        )
    finally:
        conn.close()

    return IngestResponse(
        ok=bool(res.get("ok", True)),
        day=int(res.get("day", req.day)),
        skipped=res.get("skipped"),
        records=int(res.get("records") or 0),
        feedback=int(res.get("feedback") or 0),
        vectors=res.get("vectors") or {},
    )
