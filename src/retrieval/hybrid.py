"""Hybrid retrieval combining FTS5 BM25 keyword search and semantic vector similarity with SQLite ACL checks."""

import re
import sqlite3
from typing import Any

from src.retrieval.embedder import EmbeddingClient, extract_concept_ids, has_known_concept
from src.retrieval.vector_store import get_vector_store
from src.security import (
    _get_forgotten_entries,
    _text_matches_forgotten,
    can_view,
    get_current_day,
    get_person_org,
    visible_records_params,
    visible_records_where_clause,
)
from src.usage import UsageTracker

_HINGLISH_STOPWORDS = frozenset(
    {
        "kya", "hai", "hain", "ho", "tha", "thi", "ka", "ki", "ke", "ko", "se",
        "pe", "par", "mein", "mai", "aur", "ya", "kuch", "koi", "sab", "abhi",
        "aaj", "kal", "parso", "chal", "raha", "rahi", "rahe", "hua", "hui",
        "hue", "batao", "bata", "bataiye", "dekho", "kahan", "kab", "kaun",
        "kisne", "kisko", "kiske", "paas", "kitna", "kitne", "kyun", "kaise",
        "kaisa", "kaisi", "baat", "wala", "wali", "wale", "kar", "karo",
        "karna", "kiya", "diya", "diye", "dekhta", "dekhti", "sambhal",
        "sambhalta", "aaya", "aayi", "aaye", "mila", "mili", "mile", "bhi",
        "baad", "pehle", "please", "pls", "naya", "nayi", "naye", "purana",
        "purani", "bada", "badi", "bade", "chota", "choti", "zaroori", "jaruri",
        "khas", "khaas", "ab", "toh", "hi", "mat", "nahi", "nahin", "haan",
        "han", "kaha", "lagta", "hota", "hoti", "hote", "rahega", "rahegi",
        "hoga", "hogi", "honge", "dekh", "dikha", "dikhao", "batana", "bataye",
        "chalna", "chalta", "chalti", "aajkal", "abtak", "bilkul", "thoda",
        "zyada", "jyada", "bahut", "bohot", "bhai", "yaar",
    }
)

_DEFAULT_STOPWORDS = frozenset(
    {
        "the", "and", "for", "per", "via", "into", "onto", "over", "under",
        "after", "before", "between", "through", "during", "but", "not", "now",
        "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
        "did", "does", "do", "is", "are", "was", "were", "will", "would", "can",
        "could", "should", "have", "has", "had", "been", "being", "about", "with",
        "from", "that", "this", "these", "those", "there", "their", "they", "them",
        "me", "my", "our", "your", "any", "all", "some", "other", "arrangement",
    }
) | _HINGLISH_STOPWORDS

ANALYTICAL_META_WORDS = frozenset(
    {
        "write", "draft", "prepare", "executive", "memo", "memorandum", "summarize",
        "summarizing", "summary", "synthesize", "synthesizing", "synthesis", "analyze",
        "analyzing", "analysis", "infer", "inference", "recommend", "recommendation",
        "recommendations", "recommended", "suggest", "suggestion", "suggestions",
        "actionable", "prioritize", "priorities", "priority", "strategy", "strategic",
        "assess", "assessment", "evaluate", "evaluation", "overview", "report",
        "briefing", "takeaway", "takeaways", "insight", "insights", "bottleneck",
        "bottlenecks", "implication", "implications", "leadership", "stakeholder",
        "stakeholders", "based", "available", "information", "evidence", "records",
        "communications", "messages", "projects", "project", "accounts", "account",
        "current", "recent", "currently", "overall", "comprehensive", "detailed",
        "happen", "next", "week", "today", "across", "give", "key", "main", "notes",
        "scene", "haal", "kaam", "atka", "atki", "ruka", "ruki", "dikkat", "lafda",
        "panga", "masla", "mamla", "hisab", "hisaab", "chalu", "khatam", "poora",
        "pura", "baaki", "baki", "sahi", "theek", "progress", "progressing",
        "blocker", "blockers", "blocked", "blocking", "major", "minor", "issue",
        "issues", "problem", "problems", "delay", "delayed", "delays", "stuck",
        "hold", "holding", "pending", "ongoing", "going", "happening", "looking",
        "things", "rundown", "picture", "state", "situation", "health", "track",
        "behind", "ahead", "moving", "working", "work", "tasks", "task",
        "deliverable", "deliverables", "items", "item", "anything", "everything",
        "something", "update", "updates", "status", "team", "teams", "client",
        "clients", "deliver", "delivery", "risk", "risks", "deadline", "deadlines",
        "right", "else", "still", "already", "actually", "really", "around",
        "here", "well", "filhaal", "presently", "new", "newest", "latest",
        "fresh", "urgent", "critical", "important", "top", "big", "biggest",
        "remaining", "open", "active", "existing", "known", "potential",
        "possible", "likely", "upcoming", "future", "past", "prior", "previous",
        "high", "low", "action", "actions", "step", "steps", "plan", "plans",
        "concern", "concerns", "challenge", "challenges", "hurdle", "hurdles",
        "obstacle", "obstacles", "warning", "warnings", "alert", "alerts",
    }
)


def _match_client_record(rec: sqlite3.Row, client_id: str | None, alias_terms: set[str]) -> bool:
    if client_id is None:
        return True
    if rec["client"] is not None:
        return str(rec["client"]) == client_id
    blob = " ".join(str(x or "") for x in (rec["title"], rec["subject"], rec["sanitized_body"])).lower()
    return any(
        len(al) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(al) + r"(?![a-z0-9])", blob)
        for al in alias_terms
    )


def superseded_doc_sheet_ids(
    conn: sqlite3.Connection,
    person_id: str,
    person_org: str,
    upto_day: int,
    upto_utc: str | None = None,
) -> set[str]:
    """Return record IDs of older visible doc/sheet versions superseded by a newer visible version."""
    where = visible_records_where_clause("r", with_utc_cutoff=(upto_utc is not None))
    params = visible_records_params(person_id, person_org, upto_day, upto_utc)
    rows = conn.execute(
        f"""
        SELECT r.id, r.org, r.visibility, r.author, r.readers_json,
               COALESCE(r.doc_id, r.sheet_id) AS artifact_id,
               COALESCE(r.version, 1) AS ver, r.at_utc
        FROM records r
        WHERE r.type IN ('doc', 'sheet')
          AND COALESCE(r.doc_id, r.sheet_id) IS NOT NULL
          AND {where}
        """,
        params,
    ).fetchall()

    latest_by_art: dict[str, tuple[int, str, str]] = {}
    all_ids_by_art: dict[str, list[str]] = {}
    for row in rows:
        if not can_view(row, person_id, person_org):
            continue
        aid = str(row["artifact_id"])
        rid = str(row["id"])
        cand = (int(row["ver"]), str(row["at_utc"]), rid)
        all_ids_by_art.setdefault(aid, []).append(rid)
        cur = latest_by_art.get(aid)
        if cur is None or cand > cur:
            latest_by_art[aid] = cand

    superseded: set[str] = set()
    for aid, ids in all_ids_by_art.items():
        win_rid = latest_by_art[aid][2]
        for rid in ids:
            if rid != win_rid:
                superseded.add(rid)
    return superseded


def search_fts5(
    conn: sqlite3.Connection,
    person_id: str,
    query_text: str,
    *,
    client_id: str | None = None,
    alias_terms: set[str] | None = None,
    ignore_terms: set[str] | None = None,
    synonyms: dict[str, list[str]] | None = None,
    upto_day: int | None = None,
    upto_utc: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Run FTS5 BM25 keyword retrieval constrained by SQLite visibility, cutoff, and forget guards."""
    person_org = get_person_org(conn, person_id)
    if not person_org:
        return []
    day_lim = get_current_day(conn) if upto_day is None else upto_day
    stop = _DEFAULT_STOPWORDS | (ignore_terms or set())
    syn_map = synonyms or {}
    al_terms = alias_terms or set()

    base_terms: list[str] = []
    for w in re.findall(r"[a-z0-9]{3,}", (query_text or "").lower()):
        if w not in stop and w not in base_terms:
            base_terms.append(w)
    if not base_terms:
        return []

    expanded_terms: list[str] = []
    for bt in base_terms:
        if bt not in expanded_terms:
            expanded_terms.append(bt)
        for s in syn_map.get(bt, ()):
            if s not in expanded_terms:
                expanded_terms.append(s)

    fts_expr = " OR ".join(f'"{t}"' for t in expanded_terms[:16])
    where = visible_records_where_clause("r", with_utc_cutoff=(upto_utc is not None))
    params = visible_records_params(person_id, person_org, day_lim, upto_utc)
    params["fts_expr"] = fts_expr

    rows = conn.execute(
        f"""
        SELECT r.*, bm25(records_fts) AS raw_bm25
        FROM records_fts
        JOIN records r ON r.id = records_fts.id
        WHERE records_fts MATCH :fts_expr
          AND r.is_boilerplate = 0
          AND r.is_newsletter = 0
          AND TRIM(COALESCE(r.sanitized_body, '')) != ''
          AND {where}
        ORDER BY raw_bm25 ASC, r.at_utc DESC
        LIMIT 80
        """,
        params,
    ).fetchall()

    kw_groups, forgotten_ids = _get_forgotten_entries(conn, person_id)
    superseded_ids = superseded_doc_sheet_ids(conn, person_id, person_org, day_lim, upto_utc)
    min_base_hits = 2 if len(base_terms) >= 2 else 1
    out: list[dict[str, Any]] = []
    for row in rows:
        if not can_view(row, person_id, person_org):
            continue
        if not _match_client_record(row, client_id, al_terms):
            continue
        rid = str(row["id"])
        if rid in forgotten_ids or rid in superseded_ids:
            continue
        blob = " ".join(str(x or "") for x in (row["title"], row["subject"], row["sanitized_body"])).lower()
        if _text_matches_forgotten(blob, kw_groups):
            continue

        base_hits = 0
        for bt in base_terms:
            syns = [bt] + list(syn_map.get(bt, ()))
            if any(re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", blob) for t in syns):
                base_hits += 1
        if base_hits < min_base_hits:
            continue

        hdr = f"{row['title'] or ''} {row['subject'] or ''}".lower()
        hdr_hits = sum(
            1 for t in expanded_terms
            if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", hdr)
        )
        bm25_mag = abs(float(row["raw_bm25"] or 0.0))
        fts_score = base_hits * 3.0 + hdr_hits * 1.5 + min(3.0, bm25_mag * 0.25)
        out.append(
            {
                "record_id": rid,
                "fts_score": fts_score,
                "base_hits": base_hits,
                "hdr_hits": hdr_hits,
                "record": row,
            }
        )

    out.sort(key=lambda item: (-item["fts_score"], str(item["record"]["at_utc"])), reverse=False)
    out.sort(key=lambda item: (item["fts_score"], str(item["record"]["at_utc"])), reverse=True)
    return out[:limit]


def search_hybrid(
    conn: sqlite3.Connection,
    person_id: str,
    query_text: str,
    *,
    client_id: str | None = None,
    alias_terms: set[str] | None = None,
    ignore_terms: set[str] | None = None,
    synonyms: dict[str, list[str]] | None = None,
    upto_day: int | None = None,
    upto_utc: str | None = None,
    limit: int = 8,
    target_author: str | None = None,
    asks_self: bool = False,
    embedder: EmbeddingClient | None = None,
    tracker: UsageTracker | None = None,
) -> list[dict[str, Any]]:
    """Combine FTS5 BM25 keyword retrieval with SQLite/Qdrant semantic vector search via RRF."""
    person_org = get_person_org(conn, person_id)
    if not person_org:
        return []
    day_lim = get_current_day(conn) if upto_day is None else upto_day
    stop = _DEFAULT_STOPWORDS | (ignore_terms or set())
    al_terms = alias_terms or set()

    base_terms: list[str] = []
    for w in re.findall(r"[a-z0-9]{3,}", (query_text or "").lower()):
        if w not in stop and w not in base_terms:
            base_terms.append(w)

    # 1. Exact FTS5 lexical hits (filtered by min_base_hits)
    fts_hits = search_fts5(
        conn,
        person_id,
        query_text,
        client_id=client_id,
        alias_terms=al_terms,
        ignore_terms=ignore_terms,
        synonyms=synonyms,
        upto_day=day_lim,
        upto_utc=upto_utc,
        limit=24,
    )

    # 2. Vector semantic hits (with graceful degradation to FTS5 if embedder fails)
    vec_hits: list[dict[str, Any]] = []
    try:
        store = get_vector_store()
        v_res, v_meta = store.search(
            conn,
            person_id,
            query_text,
            client_id=client_id,
            alias_terms=al_terms,
            upto_day=day_lim,
            upto_utc=upto_utc,
            top_k=24,
            min_score=0.22,
            embedder=embedder,
            tracker=tracker,
        )
        if v_meta.get("ok"):
            vec_hits = v_res
    except Exception:
        vec_hits = []

    q_concepts = extract_concept_ids(" ".join(base_terms) if base_terms else query_text)
    unmapped_terms = [
        t for t in base_terms
        if not has_known_concept(t) and t not in ANALYTICAL_META_WORDS
    ]
    fts_rank_by_id = {h["record_id"]: idx + 1 for idx, h in enumerate(fts_hits)}
    fts_by_id = {h["record_id"]: h for h in fts_hits}
    vec_rank_by_id = {h["record_id"]: idx + 1 for idx, h in enumerate(vec_hits)}
    vec_by_id = {h["record_id"]: h for h in vec_hits}

    all_ids = list(dict.fromkeys(list(fts_by_id.keys()) + list(vec_by_id.keys())))
    if not all_ids:
        return []

    kw_groups, forgotten_ids = _get_forgotten_entries(conn, person_id)
    superseded_ids = superseded_doc_sheet_ids(conn, person_id, person_org, day_lim, upto_utc)
    fused: list[dict[str, Any]] = []
    for rid in all_ids:
        if rid in forgotten_ids or rid in superseded_ids:
            continue
        fhit = fts_by_id.get(rid)
        vhit = vec_by_id.get(rid)
        rec = (fhit or vhit)["record"]

        # Re-verify canonical SQLite access controls, cutoff, non-forgotten state & sanitized body
        if not can_view(rec, person_id, person_org):
            continue
        if int(rec["is_forgotten"]) != 0 or int(rec["day"]) > day_lim:
            continue
        if upto_utc is not None and str(rec["at_utc"]) > upto_utc:
            continue
        if not (rec["sanitized_body"] or "").strip():
            continue
        if not _match_client_record(rec, client_id, al_terms):
            continue
        blob = " ".join(str(x or "") for x in (rec["title"], rec["subject"], rec["sanitized_body"])).lower()
        if _text_matches_forgotten(blob, kw_groups):
            continue

        # Accept if FTS5 matched strongly OR semantic vector similarity passes high-precision paraphrase gate:
        # every unmapped literal token (e.g. an unknown entity/project name) must exist in the record,
        # and either all mapped query concepts are covered in the record chunk or cosine sim is very high.
        accepted_semantic = False
        if vhit is not None:
            vscore = float(vhit["vector_score"])
            vcov = float(vhit.get("concept_coverage", 0.0))
            unmapped_ok = all(
                re.search(r"(?<![a-z0-9])" + re.escape(ut) + r"(?![a-z0-9])", blob)
                for ut in unmapped_terms
            )
            if unmapped_ok:
                if q_concepts and vcov >= 0.99 and vscore >= 0.24:
                    accepted_semantic = True
                elif vscore >= 0.52:
                    accepted_semantic = True

        if fhit is None and not accepted_semantic:
            continue

        fts_rank = fts_rank_by_id.get(rid)
        vec_rank = vec_rank_by_id.get(rid)
        rrf = (1.0 / (60.0 + fts_rank) if fts_rank else 0.0) + (1.0 / (60.0 + vec_rank) if vec_rank else 0.0)
        fts_sc = float(fhit["fts_score"]) if fhit else 0.0
        vec_sc = float(vhit["vector_score"]) if vhit else 0.0
        total = fts_sc + vec_sc * 5.0 + rrf * 30.0

        if asks_self and str(rec["author"]) == person_id:
            total += 4.0
        if target_author and str(rec["author"]) == target_author:
            total += 3.0
        if client_id and rec["client"] == client_id:
            total += 2.0

        fused.append(
            {
                "record_id": rid,
                "score": total,
                "fts_score": fts_sc,
                "vector_score": vec_sc,
                "rrf_score": rrf,
                "match_kind": "fts5" if fhit is not None else "semantic_only",
                "record": rec,
            }
        )

    fused.sort(key=lambda item: (item["score"], str(item["record"]["at_utc"])), reverse=True)
    return fused[:limit]
