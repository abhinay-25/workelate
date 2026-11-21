"""Mandatory token counting, cost accounting, execution timing, and context budgeting."""

import time
from typing import Any

from src.filters import strip_boilerplate_lines

BRIEF_TOKEN_LIMIT = 8000
ASK_TOKEN_LIMIT = 4000
BRIEF_SAFE_BUDGET = 6000
ASK_SAFE_BUDGET = 3000

# USD per 1M tokens (input_rate, output_rate)
MODEL_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
    "qwen/qwen3.8-27b": (0.20, 0.60),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.0-flash-lite": (0.075, 0.30),
    "gpt-4o-mini": (0.15, 0.60),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-004": (0.0, 0.0),
    "local-semantic-256d": (0.0, 0.0),
}


def estimate_tokens(text: str | None) -> int:
    """Roughly 4 characters of English per token (matches organizer README)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def compute_cost_usd(model: str | None, input_tokens: int, output_tokens: int) -> float:
    """Compute USD cost for a single model call from known per-1M rates."""
    if not model:
        return 0.0
    in_rate, out_rate = MODEL_PRICING_PER_1M.get(model, (0.59, 0.79))
    raw = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000.0
    return round(raw, 8)


def _field(rec: Any, key: str, default: Any = None) -> Any:
    if isinstance(rec, dict):
        return rec.get(key, default)
    if hasattr(rec, "keys") and key in rec.keys():
        val = rec[key]
        return default if val is None else val
    return default


def _esc_attr(val: Any) -> str:
    return str(val or "").replace('"', "'").replace("<", "&lt;").replace(">", "&gt;")


def format_record_xml(record: Any) -> str:
    """Render a workplace record into an XML data block using sanitized_body only."""
    body = str(_field(record, "sanitized_body", "") or "").strip()
    if not body:
        return ""
    cleaned_lines = strip_boilerplate_lines(body)
    effective_body = "\n".join(cleaned_lines).strip() if cleaned_lines else body
    safe_body = effective_body.replace("</workplace_record>", "< /workplace_record>")
    rid = _esc_attr(_field(record, "id", ""))
    rtype = _esc_attr(_field(record, "type", ""))
    author = _esc_attr(_field(record, "author", ""))
    at_utc = _esc_attr(_field(record, "at_utc", _field(record, "at", "")))
    client = _esc_attr(_field(record, "client", ""))
    subj = str(_field(record, "subject", "") or _field(record, "title", "") or "").strip()
    header_line = f"Header: {subj}\n" if subj else ""
    return (
        f'<workplace_record id="{rid}" type="{rtype}" author="{author}" at="{at_utc}" client="{client}">\n'
        f"{header_line}{safe_body}\n"
        f"</workplace_record>"
    )


def pack_records_within_budget(
    records: list[Any],
    system_prompt: str,
    user_prefix: str = "",
    user_suffix: str = "",
    hard_limit: int = ASK_TOKEN_LIMIT,
    safe_ratio: float = 0.75,
) -> tuple[str, int]:
    """Pack ranked records into XML blocks while keeping total estimated tokens within safe budget.

    Returns (assembled_user_prompt, count_included).
    """
    safe_budget = int(hard_limit * safe_ratio)
    fixed_tokens = estimate_tokens(system_prompt) + estimate_tokens(user_prefix) + estimate_tokens(user_suffix)
    remaining = max(0, safe_budget - fixed_tokens)

    blocks: list[str] = []
    used = 0
    for rec in records:
        xml = format_record_xml(rec)
        if not xml:
            continue
        tok = estimate_tokens(xml) + 1
        if used + tok > remaining:
            continue
        blocks.append(xml)
        used += tok

    middle = "\n".join(blocks)
    parts = [p for p in (user_prefix.strip(), middle, user_suffix.strip()) if p]
    return "\n\n".join(parts), len(blocks)


class UsageTracker:
    """Accumulate input/output tokens, USD cost, per-call metadata, and wall-clock ms."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0
        self.calls: list[dict[str, Any]] = []
        self.last_provider: str | None = None
        self.last_model: str | None = None
        self.embedding_calls = 0
        self.embedding_tokens = 0
        self.embedding_cost_usd = 0.0
        self.embedding_ms = 0
        self.embedding_events: list[dict[str, Any]] = []

    def add_embedding_call(
        self,
        input_tokens: int,
        *,
        provider: str | None = None,
        model: str | None = None,
        latency_ms: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        in_tok = max(0, int(input_tokens))
        added_cost = compute_cost_usd(model, in_tok, 0) if cost_usd is None else float(cost_usd)
        self.embedding_calls += 1
        self.embedding_tokens += in_tok
        self.embedding_cost_usd = round(self.embedding_cost_usd + added_cost, 8)
        if latency_ms is not None:
            self.embedding_ms += max(0, int(latency_ms))
        self.embedding_events.append(
            {
                "provider": provider,
                "model": model,
                "input_tokens": in_tok,
                "cost_usd": round(added_cost, 8),
                "latency_ms": latency_ms,
            }
        )

    def add_call(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str | None = None,
        cost_usd: float | None = None,
        provider: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        in_tok = max(0, int(input_tokens))
        out_tok = max(0, int(output_tokens))
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        added_cost = compute_cost_usd(model, in_tok, out_tok) if cost_usd is None else float(cost_usd)
        self.cost_usd = round(self.cost_usd + added_cost, 8)
        if provider is not None:
            self.last_provider = provider
        if model is not None:
            self.last_model = model
        self.calls.append(
            {
                "provider": provider,
                "model": model,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cost_usd": round(added_cost, 8),
                "latency_ms": latency_ms,
            }
        )

    def elapsed_ms(self) -> int:
        return max(0, int((time.monotonic() - self._t0) * 1000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "ms": self.elapsed_ms(),
        }
