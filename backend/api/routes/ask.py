"""Conversational QA endpoint wrapping src/ask.py."""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from backend.api.dependencies.db import (
    get_db,
    get_person_or_error,
    log_usage_event,
    resolve_default_date,
)
from backend.api.schemas.models import (
    AskAnswerBlock,
    AskClarifyBlock,
    AskMatchedItem,
    AskRefusedBlock,
    AskRequest,
    AskResponse,
    UsageMetricsSchema,
)
from src.ask import answer_question
from src.time_utils import get_zone, parse_utc
from src.usage import UsageTracker

router = APIRouter(tags=["ask"])


def _normalize_at_utc(conn: sqlite3.Connection, raw_at: str | None, person_zone: str) -> str:
    txt = (raw_at or "").strip()
    if not txt:
        def_date = resolve_default_date(conn)
        txt = f"{def_date}T23:59:59Z"

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", txt):
        try:
            y, m, d = (int(x) for x in txt.split("-"))
            local_end = datetime(y, m, d, 23, 59, 59, tzinfo=get_zone(person_zone))
            return local_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid date in 'at': {txt}") from exc

    try:
        dt = parse_utc(txt)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ISO-8601 timestamp in 'at': {txt}") from exc


@router.post("/ask", response_model=AskResponse)
def ask_assistant(
    req: AskRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> AskResponse:
    """Answer a question strictly as of at_utc while enforcing org/ACL, clarify, and forget rules."""
    person = get_person_or_error(conn, req.person_id)
    person_id = person["person_id"]
    person_org = person["org_id"]

    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question cannot be blank.")

    at_utc = _normalize_at_utc(conn, req.at, person["zone"])

    t0 = time.monotonic()
    tracker = UsageTracker()
    res = answer_question(conn, person_id, at_utc, question, tracker=tracker)
    elapsed_ms = max(0, int((time.monotonic() - t0) * 1000))

    # Build authorized display labels for matched entities within person_org
    people_map = {
        str(r["person_id"]): str(r["name"])
        for r in conn.execute("SELECT person_id, name FROM people WHERE org_id = ?", (person_org,)).fetchall()
    }
    clients_map = {
        str(c["client_id"]): str(c["name"])
        for c in conn.execute("SELECT client_id, name FROM clients").fetchall()
    }
    tasks_map = {
        str(t["task_id"]): str(t["title"])
        for t in conn.execute("SELECT task_id, title FROM tasks_state WHERE org = ?", (person_org,)).fetchall()
    }

    matched_items: list[AskMatchedItem] = []
    for m in res.get("matched") or []:
        mid = str(m.get("id") or "")
        label = people_map.get(mid) or clients_map.get(mid) or tasks_map.get(mid)
        matched_items.append(
            AskMatchedItem(
                phrase=str(m.get("phrase") or ""),
                id=mid,
                label=label,
                sources=[str(s) for s in (m.get("sources") or [])],
            )
        )

    ans_raw = res.get("answer")
    clarify_raw = res.get("clarify")
    refused_raw = res.get("refused")

    if isinstance(refused_raw, dict):
        status = "refused"
    elif isinstance(clarify_raw, dict):
        status = "clarify"
    elif isinstance(ans_raw, dict):
        atext = str(ans_raw.get("text") or "").strip()
        asrcs = list(ans_raw.get("sources") or [])
        status = "nothing" if (atext == "I have nothing on that." and not asrcs) else "answer"
    else:
        status = "nothing"

    u = res.get("usage") or {}
    in_tok = int(u.get("input_tokens") or 0)
    out_tok = int(u.get("output_tokens") or 0)
    is_llm = in_tok > 0 or out_tok > 0
    used_mode = "llm_assisted" if is_llm else "deterministic"
    used_provider = tracker.last_provider or ("deterministic" if not is_llm else "llm")
    used_model = tracker.last_model or ("rule-engine" if not is_llm else "unknown")

    usage_schema = UsageMetricsSchema(
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=float(u.get("cost_usd") or 0.0),
        ms=max(int(u.get("ms") or 0), elapsed_ms),
        mode=used_mode,
        provider=used_provider,
        model=used_model,
    )

    log_usage_event(
        conn,
        operation="ask",
        person_id=person_id,
        org_id=person_org,
        usage=usage_schema.model_dump(),
        latency_ms=elapsed_ms,
        status=status,
        detail=question[:140],
        provider=used_provider,
        model=used_model,
    )

    return AskResponse(
        person_id=person_id,
        at_utc=at_utc,
        status=status,
        answer=(
            AskAnswerBlock(
                text=str(ans_raw.get("text") or ""),
                sources=[str(s) for s in (ans_raw.get("sources") or [])],
            )
            if isinstance(ans_raw, dict)
            else None
        ),
        matched=matched_items,
        clarify=(
            AskClarifyBlock(
                question=str(clarify_raw.get("question") or ""),
                options=[str(o) for o in (clarify_raw.get("options") or [])],
            )
            if isinstance(clarify_raw, dict)
            else None
        ),
        refused=(
            AskRefusedBlock(reason=str(refused_raw.get("reason") or "private"))
            if isinstance(refused_raw, dict)
            else None
        ),
        usage=usage_schema,
    )
