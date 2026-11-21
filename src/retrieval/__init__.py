"""Modular vector and hybrid (FTS5 + dense vector) semantic retrieval subsystem."""

from src.retrieval.embedder import EmbeddingClient, LocalSemanticEmbedder
from src.retrieval.hybrid import search_fts5, search_hybrid
from src.retrieval.vector_store import (
    QdrantLocalVectorStore,
    SQLiteVectorStore,
    chunk_record_for_embedding,
    delete_vectors_for_records,
    get_vector_store,
    index_records_for_day,
    rebuild_all_vectors,
    search_semantic,
)

__all__ = [
    "EmbeddingClient",
    "LocalSemanticEmbedder",
    "SQLiteVectorStore",
    "QdrantLocalVectorStore",
    "get_vector_store",
    "chunk_record_for_embedding",
    "index_records_for_day",
    "rebuild_all_vectors",
    "delete_vectors_for_records",
    "search_fts5",
    "search_semantic",
    "search_hybrid",
]
