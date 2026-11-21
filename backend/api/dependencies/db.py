"""Database session dependency, auto-seeding of Days 1-5, persona validation, and usage telemetry persistence."""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Generator

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fastapi import Depends, HTTPException  # noqa: E402

from src.config import db_path, load_dotenv, pack_root  # noqa: E402
from src.db import get_ingested_days, init_schema  # noqa: E402
from src.ingest import ingest_day  # noqa: E402
from src.llm import LLMClient  # noqa: E402
from src.security import get_current_day  # noqa: E402

load_dotenv()

_SEED_LOCK = threading.Lock()

USAGE_LOG_SQL = """
CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at_utc TEXT NOT NULL,
    operation TEXT NOT NULL,
    person_id TEXT,
    org_id TEXT,
    mode TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_log_created ON usage_log(id DESC);
"""


def init_usage_log_table(conn: sqlite3.Connection) -> None:
    """Create the API/UI telemetry table without modifying core challenge schema invariants."""
    conn.executescript(USAGE_LOG_SQL)
    conn.commit()


def _open_raw_connection(target: str) -> sqlite3.Connection:
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    init_schema(conn)
    init_usage_log_table(conn)
    return conn


def _auto_seed_if_empty(target: str) -> None:
    """Automatically ingest Days 1..5 if DB has no ingested days yet and pack files exist."""
    if (os.environ.get("R3_DISABLE_AUTO_SEED") or "").strip() == "1":
        return

    with _SEED_LOCK:
        conn = _open_raw_connection(target)
        try:
            if get_current_day(conn) > 0:
                return
        finally:
            conn.close()

        pack_dir = pack_root()
        days_dir = os.path.join(pack_dir, "data", "days")
        if not os.path.isfile(os.path.join(days_dir, "day-01.jsonl")):
            return

        for d in range(1, 6):
            fpath = os.path.join(days_dir, f"day-{d:02d}.jsonl")
            if os.path.isfile(fpath):
                ingest_day(d, pack_dir=pack_dir, db_file=target)

        conn = _open_raw_connection(target)
        conn.close()


def ensure_db_initialized(target: str | None = None) -> sqlite3.Connection:
    """Ensure SQLite schema, usage_log table, and default challenge days exist; return open connection."""
    resolved = os.path.abspath(target or db_path())
    _auto_seed_if_empty(resolved)
    return _open_raw_connection(resolved)


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI dependency yielding a thread-safe per-request SQLite connection."""
    conn = ensure_db_initialized()
    try:
        yield conn
    finally:
        conn.close()


def get_person_or_error(conn: sqlite3.Connection, person_id: str) -> dict[str, Any]:
    """Validate active persona against SQLite `people` + `orgs`, raising clean HTTP errors."""
    cleaned = (person_id or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="person_id is required.")

    row = conn.execute(
        """
        SELECT p.person_id, p.org_id, p.name, p.first_name, p.last_name, p.role, p.team, p.zone,
               o.name AS org_name
        FROM people p
        JOIN orgs o ON o.org_id = p.org_id
        WHERE p.person_id = ?
        """,
        (cleaned,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown person_id '{cleaned}'.")
    return {
        "person_id": str(row["person_id"]),
        "org_id": str(row["org_id"]),
        "org_name": str(row["org_name"]),
        "name": str(row["name"]),
        "first_name": str(row["first_name"]),
        "last_name": str(row["last_name"]),
        "role": str(row["role"]),
        "team": row["team"],
        "zone": str(row["zone"]),
    }


def resolve_default_date(conn: sqlite3.Connection) -> str:
    """Return latest local calendar date present in ingested records, defaulting to 2026-11-06."""
    cur_day = get_current_day(conn)
    if cur_day <= 0:
        return "2026-11-06"
    row = conn.execute(
        "SELECT MAX(local_date) AS latest_date FROM records WHERE day = ? AND local_date != ''",
        (cur_day,),
    ).fetchone()
    if row and row["latest_date"]:
        return str(row["latest_date"])
    return "2026-11-06"


def get_available_dates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return ingested days with canonical calendar dates for UI date pickers."""
    ingested = get_ingested_days(conn)
    out: list[dict[str, Any]] = []
    for d in ingested:
        row = conn.execute(
            "SELECT MIN(local_date) AS dmin, MAX(local_date) AS dmax FROM records WHERE day = ? AND local_date != ''",
            (d,),
        ).fetchone()
        dt = str((row["dmin"] if row and row["dmin"] else "") or "")
        if dt:
            out.append({"day": int(d), "date": dt})
    return out


def log_usage_event(
    conn: sqlite3.Connection,
    *,
    operation: str,
    person_id: str | None = None,
    org_id: str | None = None,
    usage: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    status: str = "ok",
    detail: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Persist telemetry row in `usage_log` without failing the enclosing request."""
    try:
        u = usage or {}
        in_tok = max(0, int(u.get("input_tokens") or 0))
        out_tok = max(0, int(u.get("output_tokens") or 0))
        cost_usd = float(u.get("cost_usd") or 0.0)
        ms = int(latency_ms if latency_ms is not None else (u.get("ms") or 0))

        is_llm = in_tok > 0 or out_tok > 0
        mode = "llm_assisted" if is_llm else "deterministic"
        resolved_provider = provider or u.get("provider") or "deterministic"
        resolved_model = model or u.get("model") or "rule-engine"
        if is_llm and (resolved_provider == "deterministic" or resolved_model == "rule-engine"):
            configured = LLMClient().configured_providers()
            if configured:
                resolved_provider = configured[0][0]
                resolved_model = configured[0][2]
            else:
                resolved_provider = "llm"
                resolved_model = "unknown"

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        safe_detail = (detail or "").strip()[:180] or None
        conn.execute(
            """
            INSERT INTO usage_log(
                created_at_utc, operation, person_id, org_id, mode, provider, model,
                input_tokens, output_tokens, cost_usd, latency_ms, status, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_utc,
                operation,
                person_id,
                org_id,
                mode,
                resolved_provider,
                resolved_model,
                in_tok,
                out_tok,
                round(cost_usd, 8),
                max(0, ms),
                status,
                safe_detail,
            ),
        )
        conn.commit()
    except Exception:
        pass
