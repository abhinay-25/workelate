"""Tiered semantic embedding client (local dense concept+hashed projector + optional Gemini/OpenAI APIs)."""

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable

import numpy as np

from src.config import is_placeholder_value, load_dotenv
from src.usage import UsageTracker, estimate_tokens

DEFAULT_LOCAL_DIM = 256
LOCAL_CONCEPT_DIMS = 32
DEFAULT_LOCAL_MODEL = "local-semantic-256d"
DEFAULT_OPENAI_EMBED_MODEL = "text-embedding-3-small"
DEFAULT_GEMINI_EMBED_MODEL = "text-embedding-004"

OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"
GEMINI_EMBED_URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent?key={key}"

HttpPostFn = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]
EmbedFn = Callable[[list[str]], list[list[float]]]

# Curated domain-agnostic workplace semantic concept clusters mapped onto latent concept axes [0..31].
# Note: intentionally excludes dataset-specific client/person names.
_CONCEPT_CLUSTERS: tuple[tuple[int, frozenset[str]], ...] = (
    (0, frozenset({"backup", "failover", "contingency", "disaster", "recovery", "redundancy", "fallback", "restore", "arrangement"})),
    (1, frozenset({"upgrade", "upgraded", "runtime", "environment", "migration", "migrated", "patch", "version", "node", "infra", "infrastructure", "server", "deploy", "deployed"})),
    (2, frozenset({"staging", "sandbox", "uat", "qa", "testing", "test", "verification", "walkthrough"})),
    (3, frozenset({"billing", "invoice", "invoicing", "invoices", "rebate", "discount", "pricing", "rate", "fee", "charges", "payment", "accounts", "finance", "financial"})),
    (4, frozenset({"launch", "release", "golive", "live", "rollout", "ship", "shipping", "cutoff", "milestone"})),
    (5, frozenset({"blocked", "blocker", "stuck", "delay", "delayed", "slow", "latency", "timeout", "backlog", "error", "outage", "bottleneck", "issue", "incident"})),
    (6, frozenset({"leave", "ooo", "vacation", "pto", "absent", "absence", "holiday", "unavailable", "available", "availability"})),
    (7, frozenset({"travel", "travelling", "traveling", "trip", "flight", "hotel", "itinerary", "visit", "visiting"})),
    (8, frozenset({"owner", "owns", "owned", "handle", "handles", "handling", "assigned", "assignee", "responsible", "lead", "leads", "takeover", "manages"})),
    (9, frozenset({"due", "deadline", "schedule", "scheduled", "timeline", "eta", "date", "target", "eod", "tomorrow", "yesterday", "today"})),
    (10, frozenset({"security", "sso", "auth", "authentication", "login", "password", "credential", "credentials", "role", "permission", "access", "compliance", "audit"})),
    (11, frozenset({"sow", "contract", "agreement", "agreed", "vendor", "supplier", "procurement", "renewal", "terms", "proposal", "legal"})),
    (12, frozenset({"api", "webhook", "endpoint", "integration", "sync", "feed", "pipeline", "spec", "schema", "payload"})),
    (13, frozenset({"design", "ui", "layout", "copy", "banner", "homepage", "deck", "slides", "figma", "asset", "assets", "creative"})),
    (14, frozenset({"analytics", "metrics", "dashboard", "report", "reporting", "kpi", "tagging", "tracking", "export", "csv", "sheet"})),
    (15, frozenset({"approval", "approvals", "approve", "approved", "review", "reviewed", "signoff", "feedback", "comments", "revision", "revised"})),
    (16, frozenset({"scope", "dropped", "cancelled", "canceled", "removed", "deprecated", "phase", "changed", "decision"})),
    (17, frozenset({"churn", "churning", "attrition", "unhappy", "escalated", "escalation", "risk"})),
    (18, frozenset({"promise", "promised", "commit", "committed", "commitment", "deliver", "deliverable", "share", "send", "complete", "finish"})),
    (19, frozenset({"salary", "salaries", "compensation", "payroll", "bonus", "raise", "promotion"})),
    (20, frozenset({"hiring", "interview", "onboarding", "onboard", "training", "intern", "recruit"})),
    (21, frozenset({"checkout", "cart", "store", "locator", "loyalty", "catalog", "product", "order", "orders", "returns"})),
    (22, frozenset({"logistics", "fleet", "route", "driver", "dispatch", "delivery", "shipment", "warehouse", "tms"})),
    (23, frozenset({"email", "sequence", "campaign", "newsletter", "digest", "notice", "reminder", "followup"})),
)

_TOKEN_TO_CONCEPT: dict[str, int] = {}
for _cid, _toks in _CONCEPT_CLUSTERS:
    for _t in _toks:
        _TOKEN_TO_CONCEPT[_t] = _cid

_EMBED_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "have", "has", "had",
        "was", "were", "are", "been", "being", "will", "would", "could", "should",
        "what", "when", "where", "which", "who", "whom", "why", "how", "did", "does",
        "our", "your", "their", "they", "them", "its", "any", "all", "some", "about",
        "into", "onto", "over", "under", "after", "before", "there", "then",
    }
)


def _simple_stem(tok: str) -> str:
    w = tok.lower().strip()
    if len(w) <= 4:
        return w
    for suf in ("ation", "ment", "ing", "ied", "ies", "ed", "es", "ly"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            if suf == "ied" or suf == "ies":
                return w[:-len(suf)] + "y"
            return w[:-len(suf)]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 4:
        return w[:-1]
    return w


def _stable_slot_and_sign(feature: str, start: int, end: int) -> tuple[int, float]:
    span = max(1, end - start)
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    val = int.from_bytes(digest, "little")
    slot = start + (val % span)
    sign = 1.0 if ((val >> 32) & 1) == 0 else -1.0
    return slot, sign


def extract_concept_ids(text: str) -> set[int]:
    """Return latent concept axis IDs present in text."""
    out: set[int] = set()
    for tok in re.findall(r"[a-z0-9]+", (text or "").lower()):
        st = _simple_stem(tok)
        if tok in _TOKEN_TO_CONCEPT:
            out.add(_TOKEN_TO_CONCEPT[tok])
        elif st in _TOKEN_TO_CONCEPT:
            out.add(_TOKEN_TO_CONCEPT[st])
    return out


def has_known_concept(token: str) -> bool:
    """Return True if a token or its stem maps to a known semantic concept cluster."""
    tok = (token or "").lower().strip()
    if not tok:
        return False
    return tok in _TOKEN_TO_CONCEPT or _simple_stem(tok) in _TOKEN_TO_CONCEPT


class LocalSemanticEmbedder:
    """Fast deterministic 256-dim dense semantic + lexical projector using numpy."""

    def __init__(self, dim: int = DEFAULT_LOCAL_DIM) -> None:
        self.dim = max(64, int(dim))
        self.model = f"local-semantic-{self.dim}d"

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        raw_tokens = [
            t for t in re.findall(r"[a-z0-9]{2,}", (text or "").lower())
            if t not in _EMBED_STOPWORDS
        ]
        if not raw_tokens:
            return vec

        stems = [_simple_stem(t) for t in raw_tokens]
        # 1. Dedicated semantic concept clusters in [0 .. LOCAL_CONCEPT_DIMS)
        seen_concepts: set[int] = set()
        for tok, st in zip(raw_tokens, stems):
            cid = _TOKEN_TO_CONCEPT.get(tok)
            if cid is None:
                cid = _TOKEN_TO_CONCEPT.get(st)
            if cid is not None and cid < LOCAL_CONCEPT_DIMS and cid not in seen_concepts:
                vec[cid] += 3.4
                seen_concepts.add(cid)

        # 2. Signed hashed unigrams/stems + bigrams + 4-char subwords in [LOCAL_CONCEPT_DIMS .. dim)
        seen_feats: set[str] = set()
        for tok, st in zip(raw_tokens, stems):
            for feat, w in ((f"w:{tok}", 1.15), (f"s:{st}", 1.0)):
                if feat not in seen_feats:
                    seen_feats.add(feat)
                    idx, sign = _stable_slot_and_sign(feat, LOCAL_CONCEPT_DIMS, self.dim)
                    vec[idx] += sign * w
            if len(st) >= 5:
                for i in range(len(st) - 3):
                    sub = f"c:{st[i:i+4]}"
                    idx, sign = _stable_slot_and_sign(sub, LOCAL_CONCEPT_DIMS, self.dim)
                    vec[idx] += sign * 0.25

        for a, b in zip(stems, stems[1:]):
            bg = f"b:{a}_{b}"
            idx, sign = _stable_slot_and_sign(bg, LOCAL_CONCEPT_DIMS, self.dim)
            vec[idx] += sign * 0.55

        norm = float(np.linalg.norm(vec))
        if norm > 0.0:
            vec /= norm
        return vec

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        return [self.embed(t) for t in texts]


def _default_http_post(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw)


class EmbeddingClient:
    """Embedding client supporting local dense projection plus optional Gemini/OpenAI embeddings."""

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        http_post: HttpPostFn | None = None,
        embed_fn: EmbedFn | None = None,
        allow_local_fallback: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self._explicit_provider = provider
        self._explicit_model = model
        self._post = http_post or _default_http_post
        self._embed_fn = embed_fn
        self.allow_local_fallback = allow_local_fallback
        self._timeout = timeout
        self._local = LocalSemanticEmbedder(DEFAULT_LOCAL_DIM)

    def resolved_provider(self) -> tuple[str, str]:
        load_dotenv()
        prov = (self._explicit_provider or os.environ.get("R3_EMBEDDING_PROVIDER") or "local").strip().lower()
        if prov == "openai":
            key = (os.environ.get("OPENAI_API_KEY") or "").strip()
            if not is_placeholder_value(key) or self._embed_fn is not None:
                model = (self._explicit_model or os.environ.get("R3_EMBEDDING_MODEL") or DEFAULT_OPENAI_EMBED_MODEL).strip()
                return "openai", model
        elif prov == "gemini":
            key = (os.environ.get("GEMINI_API_KEY") or "").strip()
            if not is_placeholder_value(key) or self._embed_fn is not None:
                model = (self._explicit_model or os.environ.get("R3_EMBEDDING_MODEL") or DEFAULT_GEMINI_EMBED_MODEL).strip()
                return "gemini", model
        elif prov == "custom" and self._embed_fn is not None:
            return "custom", (self._explicit_model or "custom-embed")
        return "local", self._local.model

    def model_name(self) -> str:
        _, mod = self.resolved_provider()
        return mod

    def _redact_error(self, msg: str, api_key: str) -> str:
        out = str(msg)
        if api_key:
            out = out.replace(api_key, "[REDACTED]")
        out = re.sub(r"([?&]key=)[^&\s\"']+", r"\1[REDACTED]", out)
        return out

    def _normalize_vecs(self, raw_vecs: list[list[float]]) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for rv in raw_vecs:
            arr = np.asarray(rv, dtype=np.float32)
            norm = float(np.linalg.norm(arr))
            if norm > 0.0:
                arr = arr / norm
            out.append(arr)
        return out

    def embed_texts(
        self,
        texts: list[str],
        tracker: UsageTracker | None = None,
    ) -> tuple[list[np.ndarray] | None, dict[str, Any]]:
        """Embed `texts`, returning (list_of_unit_vectors, metadata) or (None, meta) if failed and no fallback."""
        if not texts:
            return [], {"provider": "local", "model": self._local.model, "fallback_used": False, "latency_ms": 0}

        prov, model = self.resolved_provider()
        t0 = time.monotonic()

        if self._embed_fn is not None:
            try:
                vecs = self._normalize_vecs(self._embed_fn(texts))
                ms = max(0, int(round((time.monotonic() - t0) * 1000)))
                toks = sum(estimate_tokens(t) for t in texts)
                if tracker is not None:
                    tracker.add_embedding_call(toks, provider=prov, model=model, latency_ms=ms)
                return vecs, {"provider": prov, "model": model, "fallback_used": False, "latency_ms": ms, "tokens": toks}
            except Exception as exc:
                print(f"[embed] custom embed_fn failed: {self._redact_error(str(exc), '')}", file=sys.stderr)
                if not self.allow_local_fallback:
                    return None, {"provider": prov, "model": model, "fallback_used": False, "error": True}

        if prov == "openai":
            api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
            try:
                data = self._post(
                    OPENAI_EMBED_URL,
                    {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    {"model": model, "input": texts},
                    self._timeout,
                )
                items = sorted(data["data"], key=lambda d: int(d.get("index", 0)))
                vecs = self._normalize_vecs([it["embedding"] for it in items])
                in_tok = int((data.get("usage") or {}).get("prompt_tokens") or sum(estimate_tokens(t) for t in texts))
                ms = max(0, int(round((time.monotonic() - t0) * 1000)))
                if tracker is not None:
                    tracker.add_embedding_call(in_tok, provider="openai", model=model, latency_ms=ms)
                return vecs, {"provider": "openai", "model": model, "fallback_used": False, "latency_ms": ms, "tokens": in_tok}
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                print(f"[embed] provider=openai model={model} failed: {self._redact_error(str(exc), api_key)}", file=sys.stderr)
                if not self.allow_local_fallback:
                    return None, {"provider": "openai", "model": model, "fallback_used": False, "error": True}

        if prov == "gemini":
            api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
            try:
                vecs_raw: list[list[float]] = []
                for txt in texts:
                    url = GEMINI_EMBED_URL_TMPL.format(model=model, key=api_key)
                    data = self._post(
                        url,
                        {"Content-Type": "application/json"},
                        {"content": {"parts": [{"text": txt}]}},
                        self._timeout,
                    )
                    vecs_raw.append(data["embedding"]["values"])
                vecs = self._normalize_vecs(vecs_raw)
                in_tok = sum(estimate_tokens(t) for t in texts)
                ms = max(0, int(round((time.monotonic() - t0) * 1000)))
                if tracker is not None:
                    tracker.add_embedding_call(in_tok, provider="gemini", model=model, latency_ms=ms)
                return vecs, {"provider": "gemini", "model": model, "fallback_used": False, "latency_ms": ms, "tokens": in_tok}
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                print(f"[embed] provider=gemini model={model} failed: {self._redact_error(str(exc), api_key)}", file=sys.stderr)
                if not self.allow_local_fallback:
                    return None, {"provider": "gemini", "model": model, "fallback_used": False, "error": True}

        # Default or graceful local fallback
        vecs = self._local.embed_batch(texts)
        ms = max(0, int(round((time.monotonic() - t0) * 1000)))
        toks = sum(estimate_tokens(t) for t in texts)
        if tracker is not None:
            tracker.add_embedding_call(toks, provider="local", model=self._local.model, latency_ms=ms, cost_usd=0.0)
        return vecs, {
            "provider": "local",
            "model": self._local.model,
            "fallback_used": prov != "local",
            "latency_ms": ms,
            "tokens": toks,
        }
