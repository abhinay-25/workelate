"""Persistent local vector store over SQLite (`record_vectors`) with optional Qdrant local adapter."""

import hashlib
import os
import re
import sqlite3
import uuid
from typing import Any

import numpy as np

from src.retrieval.embedder import EmbeddingClient, extract_concept_ids
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

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    _HAS_QDRANT = True
except ImportError:
    QdrantClient = None
    _HAS_QDRANT = False


def _rec_field(rec: Any, key: str, default: Any = None) -> Any:
    if isinstance(rec, dict):
        return rec.get(key, default)
    if hasattr(rec, "keys") and key in rec.keys():
        val = rec[key]
        return default if val is None else val
    return default


def chunk_record_for_embedding(rec: Any, max_chars: int = 420, overlap: int = 60) -> list[str]:
    """Return clean sanitized chunks for an eligible workplace record, or [] if excluded."""
    if int(_rec_field(rec, "is_forgotten", 0) or 0) != 0:
        return []
    if int(_rec_field(rec, "is_boilerplate", 0) or 0) != 0:
        return []
    if int(_rec_field(rec, "is_newsletter", 0) or 0) != 0:
        return []

    body = str(_rec_field(rec, "sanitized_body", "") or "").strip()
    if not body:
        return []

    title = str(_rec_field(rec, "title", "") or "").strip()
    subject = str(_rec_field(rec, "subject", "") or "").strip()
    header = title or subject
    if header and header.lower() not in body.lower():
        full_text = f"{header}. {body}"
    else:
        full_text = body

    full_text = " ".join(full_text.split())
    if len(full_text) <= max_chars:
        return [full_text]

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", full_text) if s.strip()]
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for s in sentences:
        if cur and cur_len + len(s) + 1 > max_chars:
            joined = " ".join(cur)
            chunks.append(joined)
            tail = joined[-overlap:].strip() if overlap > 0 and len(joined) > overlap else ""
            cur = [tail, s] if tail else [s]
            cur_len = sum(len(x) for x in cur) + 1
        else:
            cur.append(s)
            cur_len += len(s) + 1
    if cur:
        chunks.append(" ".join(cur))
    return chunks or [full_text[:max_chars]]


def _chunk_hash(text: str, visibility: str, org: str, model: str) -> str:
    payload = f"{model}|{org}|{visibility}|{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _qdrant_point_id(record_id: str, chunk_index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"buildpsprint-r3:{record_id}:{chunk_index}"))


class QdrantLocalVectorStore:
    """Optional Qdrant persistent local storage adapter mirroring `record_vectors`."""

    def __init__(self, path: str, dim: int, collection_name: str = "workplace_records") -> None:
        if not _HAS_QDRANT:
            raise RuntimeError("qdrant_client is not installed")
        self.path = path
        self.dim = dim
        self.collection_name = collection_name
        self.client = QdrantClient(path=path)
        existing = [c.name for c in self.client.get_collections().collections]
        if collection_name not in existing:
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )

    def upsert_points(self, rows: list[tuple[str, int, np.ndarray, dict[str, Any]]]) -> None:
        if not rows:
            return
        points = [
            PointStruct(id=_qdrant_point_id(rid, cidx), vector=vec.tolist(), payload=meta)
            for rid, cidx, vec, meta in rows
        ]
        self.client.upsert(collection_name=self.collection_name, points=points)

    def delete_records(self, record_ids: list[str], max_chunks: int = 32) -> None:
        if not record_ids:
            return
        ids = [_qdrant_point_id(rid, i) for rid in record_ids for i in range(max_chunks)]
        self.client.delete(collection_name=self.collection_name, points_selector=ids)


class SQLiteVectorStore:
    """SQLite-persisted dense vector store integrated into canonical transactional state."""

    def __init__(self, qdrant_store: QdrantLocalVectorStore | None = None) -> None:
        self._qdrant = qdrant_store

    def upsert_records(
        self,
        conn: sqlite3.Connection,
        records: list[Any],
        embedder: EmbeddingClient | None = None,
        tracker: UsageTracker | None = None,
    ) -> dict[str, Any]:
        emb = embedder or EmbeddingClient()
        model_name = emb.model_name()

        to_embed_meta: list[tuple[Any, int, str, str]] = []
        skipped = 0
        for rec in records:
            rid = str(_rec_field(rec, "id"))
            org = str(_rec_field(rec, "org"))
            vis = str(_rec_field(rec, "visibility", "team"))
            chunks = chunk_record_for_embedding(rec)
            if not chunks:
                conn.execute("DELETE FROM record_vectors WHERE record_id = ?", (rid,))
                continue

            conn.execute("DELETE FROM record_vectors WHERE record_id = ? AND chunk_index >= ?", (rid, len(chunks)))
            existing = {
                int(r["chunk_index"]): (str(r["content_hash"]), str(r["model"]))
                for r in conn.execute(
                    "SELECT chunk_index, content_hash, model FROM record_vectors WHERE record_id = ?",
                    (rid,),
                ).fetchall()
            }
            for cidx, ctext in enumerate(chunks):
                chash = _chunk_hash(ctext, vis, org, model_name)
                if existing.get(cidx) == (chash, model_name):
                    skipped += 1
                    continue
                to_embed_meta.append((rec, cidx, ctext, chash))

        if not to_embed_meta:
            return {"indexed": 0, "skipped": skipped, "model": model_name}

        texts = [item[2] for item in to_embed_meta]
        vecs, emb_meta = emb.embed_texts(texts, tracker=tracker)
        if vecs is None or len(vecs) != len(to_embed_meta):
            return {"indexed": 0, "skipped": skipped, "failed": True, "meta": emb_meta}

        actual_model = str(emb_meta.get("model") or model_name)
        qdrant_rows: list[tuple[str, int, np.ndarray, dict[str, Any]]] = []
        db_rows: list[tuple[Any, ...]] = []
        for (rec, cidx, ctext, chash), vec in zip(to_embed_meta, vecs):
            rid = str(_rec_field(rec, "id"))
            arr = np.asarray(vec, dtype=np.float32)
            blob = arr.tobytes()
            dim = int(arr.shape[0])
            org = str(_rec_field(rec, "org"))
            day = int(_rec_field(rec, "day"))
            at_utc = str(_rec_field(rec, "at_utc"))
            client = _rec_field(rec, "client")
            author = str(_rec_field(rec, "author"))
            rtype = str(_rec_field(rec, "type"))
            vis = str(_rec_field(rec, "visibility", "team"))
            readers_json = _rec_field(rec, "readers_json")
            actual_hash = _chunk_hash(ctext, vis, org, actual_model)
            db_rows.append(
                (
                    rid, cidx, day, at_utc, org, client, author, rtype,
                    vis, readers_json, actual_hash, ctext, blob, dim, actual_model,
                )
            )
            if self._qdrant is not None:
                qdrant_rows.append(
                    (
                        rid,
                        cidx,
                        arr,
                        {
                            "record_id": rid,
                            "chunk_index": cidx,
                            "org": org,
                            "day": day,
                            "at_utc": at_utc,
                            "client": client,
                            "author": author,
                            "visibility": vis,
                        },
                    )
                )

        conn.executemany(
            """
            INSERT OR REPLACE INTO record_vectors(
                record_id, chunk_index, day, at_utc, org, client, author, type,
                visibility, readers_json, content_hash, chunk_text, embedding_blob, dim, model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            db_rows,
        )
        if self._qdrant is not None and qdrant_rows:
            self._qdrant.upsert_points(qdrant_rows)

        return {"indexed": len(db_rows), "skipped": skipped, "model": actual_model, "meta": emb_meta}

    def delete_records(self, conn: sqlite3.Connection, record_ids: list[str]) -> int:
        if not record_ids:
            return 0
        placeholders = ",".join("?" for _ in record_ids)
        cur = conn.execute(f"DELETE FROM record_vectors WHERE record_id IN ({placeholders})", list(record_ids))
        if self._qdrant is not None:
            self._qdrant.delete_records(list(record_ids))
        return int(cur.rowcount)

    def search(
        self,
        conn: sqlite3.Connection,
        person_id: str,
        query_text: str,
        *,
        client_id: str | None = None,
        alias_terms: set[str] | None = None,
        upto_day: int | None = None,
        upto_utc: str | None = None,
        top_k: int = 12,
        min_score: float = 0.20,
        embedder: EmbeddingClient | None = None,
        tracker: UsageTracker | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Run ACL/org/temporal-prefiltered cosine search and re-validate against SQLite records."""
        person_org = get_person_org(conn, person_id)
        if not person_org or not (query_text or "").strip():
            return [], {"ok": True, "count": 0}

        day_lim = get_current_day(conn) if upto_day is None else upto_day
        emb = embedder or EmbeddingClient()
        q_vecs, emb_meta = emb.embed_texts([query_text], tracker=tracker)
        if not q_vecs:
            return [], {"ok": False, "error": True, "meta": emb_meta}

        q_vec = np.asarray(q_vecs[0], dtype=np.float32)
        q_dim = int(q_vec.shape[0])
        q_concepts = extract_concept_ids(query_text)

        where = visible_records_where_clause("r", with_utc_cutoff=(upto_utc is not None))
        params = visible_records_params(person_id, person_org, day_lim, upto_utc)
        params["qdim"] = q_dim

        sql = f"""
            SELECT v.record_id, v.chunk_index, v.chunk_text, v.embedding_blob, v.model, r.*
            FROM record_vectors v
            JOIN records r ON r.id = v.record_id
            WHERE v.org = :person_org
              AND v.day <= :upto_day
              AND v.dim = :qdim
              AND r.is_boilerplate = 0
              AND r.is_newsletter = 0
              AND TRIM(COALESCE(r.sanitized_body, '')) != ''
              AND {where}
        """
        if upto_utc is not None:
            sql += " AND v.at_utc <= :upto_utc"
        if client_id is not None:
            # Allow records explicitly tagged for client_id or untagged records mentioning client alias
            sql += " AND (r.client = :cid OR r.client IS NULL)"
            params["cid"] = client_id

        rows = conn.execute(sql, params).fetchall()
        if not rows:
            return [], {"ok": True, "count": 0, "meta": emb_meta}

        # Filter untagged rows by client alias if a client constraint is active
        filtered_rows: list[sqlite3.Row] = []
        alias_list = [a for a in (alias_terms or set()) if len(a) >= 3]
        for row in rows:
            if not can_view(row, person_id, person_org):
                continue
            if client_id is not None and row["client"] is None and alias_list:
                blob = f"{row['title'] or ''} {row['subject'] or ''} {row['sanitized_body'] or ''}".lower()
                if not any(re.search(r"(?<![a-z0-9])" + re.escape(al) + r"(?![a-z0-9])", blob) for al in alias_list):
                    continue
            filtered_rows.append(row)

        if not filtered_rows:
            return [], {"ok": True, "count": 0, "meta": emb_meta}

        mat = np.stack([np.frombuffer(r["embedding_blob"], dtype=np.float32) for r in filtered_rows], axis=0)
        sims = mat @ q_vec

        kw_groups, forgotten_ids = _get_forgotten_entries(conn, person_id)
        best_by_rid: dict[str, dict[str, Any]] = {}
        for idx, row in enumerate(filtered_rows):
            score = float(sims[idx])
            if score < min_score:
                continue
            rid = str(row["record_id"])
            if rid in forgotten_ids:
                continue
            blob = f"{row['title'] or ''} {row['subject'] or ''} {row['sanitized_body'] or ''}"
            if _text_matches_forgotten(blob, kw_groups):
                continue

            c_concepts = extract_concept_ids(str(row["chunk_text"]))
            concept_overlap = len(q_concepts & c_concepts) if q_concepts else 0
            concept_cov = (concept_overlap / len(q_concepts)) if q_concepts else 0.0

            prev = best_by_rid.get(rid)
            if prev is None or score > prev["vector_score"]:
                best_by_rid[rid] = {
                    "record_id": rid,
                    "vector_score": score,
                    "concept_overlap": concept_overlap,
                    "concept_coverage": concept_cov,
                    "chunk_index": int(row["chunk_index"]),
                    "chunk_text": str(row["chunk_text"]),
                    "record": row,
                }

        ranked = sorted(
            best_by_rid.values(),
            key=lambda x: (-x["vector_score"], str(x["record"]["at_utc"])),
        )[:top_k]
        return ranked, {"ok": True, "count": len(ranked), "meta": emb_meta}


def get_vector_store(db_file: str | None = None, dim: int = 256) -> SQLiteVectorStore:
    """Return SQLiteVectorStore optionally mirrored to local Qdrant persistent storage."""
    backend = (os.environ.get("R3_VECTOR_BACKEND") or "sqlite").strip().lower()
    if backend == "qdrant" and _HAS_QDRANT:
        qpath = os.environ.get("R3_QDRANT_PATH") or ((db_file or ".r3_assist.db") + ".qdrant")
        return SQLiteVectorStore(qdrant_store=QdrantLocalVectorStore(qpath, dim=dim))
    return SQLiteVectorStore()


def index_records_for_day(
    conn: sqlite3.Connection,
    day: int,
    embedder: EmbeddingClient | None = None,
    tracker: UsageTracker | None = None,
) -> dict[str, Any]:
    """Index active non-noise records ingested on `day` into persistent vectors."""
    rows = conn.execute(
        """
        SELECT * FROM records
        WHERE day = ? AND is_forgotten = 0 AND is_boilerplate = 0 AND is_newsletter = 0
          AND TRIM(COALESCE(sanitized_body, '')) != ''
        """,
        (day,),
    ).fetchall()
    store = get_vector_store()
    return store.upsert_records(conn, rows, embedder=embedder, tracker=tracker)


def rebuild_all_vectors(
    conn: sqlite3.Connection,
    embedder: EmbeddingClient | None = None,
) -> dict[str, Any]:
    """Recompute all active record vectors (idempotent)."""
    conn.execute(
        """
        DELETE FROM record_vectors
        WHERE record_id IN (SELECT id FROM records WHERE is_forgotten = 1 OR TRIM(COALESCE(sanitized_body, '')) = '')
        """
    )
    rows = conn.execute(
        """
        SELECT * FROM records
        WHERE is_forgotten = 0 AND is_boilerplate = 0 AND is_newsletter = 0
          AND TRIM(COALESCE(sanitized_body, '')) != ''
        """
    ).fetchall()
    store = get_vector_store()
    return store.upsert_records(conn, rows, embedder=embedder)


def delete_vectors_for_records(conn: sqlite3.Connection, record_ids: list[str]) -> int:
    """Delete vectors for forgotten/retracted records across persistent storage."""
    store = get_vector_store()
    return store.delete_records(conn, record_ids)


def search_semantic(
    conn: sqlite3.Connection,
    person_id: str,
    query_text: str,
    *,
    client_id: str | None = None,
    alias_terms: set[str] | None = None,
    upto_day: int | None = None,
    upto_utc: str | None = None,
    top_k: int = 12,
    min_score: float = 0.20,
    embedder: EmbeddingClient | None = None,
    tracker: UsageTracker | None = None,
) -> list[dict[str, Any]]:
    """Run metadata-prefiltered semantic vector search returning ACL-validated records."""
    store = get_vector_store()
    hits, _ = store.search(
        conn,
        person_id,
        query_text,
        client_id=client_id,
        alias_terms=alias_terms,
        upto_day=upto_day,
        upto_utc=upto_utc,
        top_k=top_k,
        min_score=min_score,
        embedder=embedder,
        tracker=tracker,
    )
    return hits
