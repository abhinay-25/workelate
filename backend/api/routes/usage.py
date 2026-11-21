"""System usage, provider readiness, token guardrails, and telemetry history endpoint."""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, Depends

from backend.api.dependencies.db import get_db
from backend.api.schemas.models import (
    EmbeddingStatusSchema,
    OperationBreakdownSchema,
    ProviderStatusSchema,
    UsageLogEntrySchema,
    UsageResponse,
    UsageTotalsSchema,
)
from src.config import is_placeholder_value, load_dotenv
from src.llm import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GROQ_MODEL,
    DEFAULT_OPENAI_MODEL,
)
from src.retrieval.embedder import EmbeddingClient
from src.usage import (
    ASK_SAFE_BUDGET,
    ASK_TOKEN_LIMIT,
    BRIEF_SAFE_BUDGET,
    BRIEF_TOKEN_LIMIT,
)

router = APIRouter(tags=["usage"])


@router.get("/usage", response_model=UsageResponse)
def get_system_usage(conn: sqlite3.Connection = Depends(get_db)) -> UsageResponse:
    """Return configured model tiers (no API keys), vector index stats, and telemetry log."""
    load_dotenv()

    groq_model = (os.environ.get("R3_GROQ_MODEL") or os.environ.get("R3_LLM_MODEL") or DEFAULT_GROQ_MODEL).strip()
    gemini_model = (os.environ.get("R3_GEMINI_MODEL") or DEFAULT_GEMINI_MODEL).strip()
    openai_model = (os.environ.get("R3_OPENAI_MODEL") or DEFAULT_OPENAI_MODEL).strip()

    providers = [
        ProviderStatusSchema(
            tier="Primary",
            provider="groq",
            model=groq_model,
            configured=not is_placeholder_value(os.environ.get("GROQ_API_KEY")),
            role="Primary LLM fallback for ambiguous briefs, QA synthesis, and nuanced feedback",
        ),
        ProviderStatusSchema(
            tier="Secondary",
            provider="gemini",
            model=gemini_model,
            configured=not is_placeholder_value(os.environ.get("GEMINI_API_KEY")),
            role="Automatic failover if Groq hits rate limits or transient outage",
        ),
        ProviderStatusSchema(
            tier="Fallback",
            provider="openai",
            model=openai_model,
            configured=not is_placeholder_value(os.environ.get("OPENAI_API_KEY")),
            role="Final resilience tier ensuring evaluation stability",
        ),
    ]

    emb_prov, emb_model = EmbeddingClient().resolved_provider()
    vec_row = conn.execute(
        """
        SELECT COUNT(DISTINCT record_id) AS rec_cnt,
               COUNT(*) AS chunk_cnt,
               MAX(model) AS stored_model,
               MAX(dim) AS dim
        FROM record_vectors
        """
    ).fetchone()

    embedding_status = EmbeddingStatusSchema(
        provider=emb_prov,
        model=str((vec_row["stored_model"] if vec_row and vec_row["stored_model"] else None) or emb_model),
        indexed_records=int(vec_row["rec_cnt"] or 0) if vec_row else 0,
        indexed_chunks=int(vec_row["chunk_cnt"] or 0) if vec_row else 0,
        vector_dim=int(vec_row["dim"] or 256) if vec_row else 256,
        backend=(os.environ.get("R3_VECTOR_BACKEND") or "sqlite").strip().lower(),
    )

    tot_row = conn.execute(
        """
        SELECT COUNT(*) AS total_req,
               SUM(CASE WHEN mode = 'deterministic' THEN 1 ELSE 0 END) AS det_cnt,
               SUM(CASE WHEN mode != 'deterministic' THEN 1 ELSE 0 END) AS llm_cnt,
               COALESCE(SUM(input_tokens), 0) AS sum_in,
               COALESCE(SUM(output_tokens), 0) AS sum_out,
               COALESCE(SUM(cost_usd), 0.0) AS sum_cost,
               COALESCE(AVG(latency_ms), 0.0) AS avg_ms
        FROM usage_log
        """
    ).fetchone()

    totals = UsageTotalsSchema(
        total_requests=int(tot_row["total_req"] or 0),
        deterministic_requests=int(tot_row["det_cnt"] or 0),
        llm_assisted_requests=int(tot_row["llm_cnt"] or 0),
        total_input_tokens=int(tot_row["sum_in"] or 0),
        total_output_tokens=int(tot_row["sum_out"] or 0),
        total_cost_usd=round(float(tot_row["sum_cost"] or 0.0), 6),
        avg_latency_ms=round(float(tot_row["avg_ms"] or 0.0), 2),
    )

    op_rows = conn.execute(
        """
        SELECT operation,
               COUNT(*) AS cnt,
               SUM(CASE WHEN mode = 'deterministic' THEN 1 ELSE 0 END) AS det_cnt,
               SUM(CASE WHEN mode != 'deterministic' THEN 1 ELSE 0 END) AS llm_cnt,
               COALESCE(SUM(input_tokens), 0) AS sum_in,
               COALESCE(SUM(output_tokens), 0) AS sum_out,
               COALESCE(SUM(cost_usd), 0.0) AS sum_cost,
               COALESCE(AVG(latency_ms), 0.0) AS avg_ms
        FROM usage_log
        GROUP BY operation
        ORDER BY cnt DESC, operation ASC
        """
    ).fetchall()

    by_op = [
        OperationBreakdownSchema(
            operation=str(r["operation"]),
            count=int(r["cnt"]),
            deterministic_count=int(r["det_cnt"] or 0),
            llm_count=int(r["llm_cnt"] or 0),
            input_tokens=int(r["sum_in"] or 0),
            output_tokens=int(r["sum_out"] or 0),
            cost_usd=round(float(r["sum_cost"] or 0.0), 6),
            avg_latency_ms=round(float(r["avg_ms"] or 0.0), 2),
        )
        for r in op_rows
    ]

    log_rows = conn.execute(
        """
        SELECT * FROM usage_log
        ORDER BY id DESC
        LIMIT 100
        """
    ).fetchall()

    recent_calls = [
        UsageLogEntrySchema(
            id=int(r["id"]),
            created_at_utc=str(r["created_at_utc"]),
            operation=str(r["operation"]),
            person_id=r["person_id"],
            org_id=r["org_id"],
            mode=str(r["mode"]),
            provider=r["provider"],
            model=r["model"],
            input_tokens=int(r["input_tokens"]),
            output_tokens=int(r["output_tokens"]),
            cost_usd=round(float(r["cost_usd"]), 6),
            latency_ms=int(r["latency_ms"]),
            status=str(r["status"]),
            detail=r["detail"],
        )
        for r in log_rows
    ]

    return UsageResponse(
        providers=providers,
        embedding=embedding_status,
        guardrails={
            "brief_token_limit": BRIEF_TOKEN_LIMIT,
            "brief_safe_budget": BRIEF_SAFE_BUDGET,
            "ask_token_limit": ASK_TOKEN_LIMIT,
            "ask_safe_budget": ASK_SAFE_BUDGET,
            "temperature": 0.0,
            "xml_prompt_sandboxing": True,
        },
        totals=totals,
        by_operation=by_op,
        recent_calls=recent_calls,
    )
