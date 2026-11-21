"""FastAPI application entry point for BuildSprint R3 — WorkMate Web UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from backend.api.dependencies.db import ensure_db_initialized  # noqa: E402
from backend.api.routes import (  # noqa: E402
    ask,
    audit,
    brief,
    health,
    ingest,
    memory,
    overview,
    records,
    usage,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    conn = ensure_db_initialized()
    conn.close()
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title="WorkMate — BuildSprint R3 API",
        description="Thin FastAPI adapter over the BuildSprint R3 workplace memory and reasoning engine.",
        version="1.0.0",
        lifespan=lifespan,
    )

    allowed_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ]
    extra_origin = (os.environ.get("R3_FRONTEND_ORIGIN") or "").strip()
    if extra_origin:
        allowed_origins.append(extra_origin)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @application.exception_handler(Exception)
    async def _safe_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        # Never expose internal tracebacks, file paths, or secrets in HTTP responses
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal error occurred while processing the request."},
        )

    application.include_router(health.router, prefix="/api")
    application.include_router(overview.router, prefix="/api")
    application.include_router(brief.router, prefix="/api")
    application.include_router(ask.router, prefix="/api")
    application.include_router(records.router, prefix="/api")
    application.include_router(memory.router, prefix="/api")
    application.include_router(audit.router, prefix="/api")
    application.include_router(usage.router, prefix="/api")
    application.include_router(ingest.router, prefix="/api")

    return application


app = create_app()
