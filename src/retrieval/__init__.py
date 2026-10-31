"""Retrieval interfaces; importing storage does not import inference libraries."""

from typing import Any

__all__ = ["BM25Indexer", "QdrantIndexer"]


def __getattr__(name: str) -> Any:
    """Retain package imports without eagerly loading encoders or tokenizers."""
    if name == "BM25Indexer":
        from src.retrieval.bm25_indexer import BM25Indexer

        return BM25Indexer
    if name == "QdrantIndexer":
        from src.retrieval.qdrant_indexer import QdrantIndexer

        return QdrantIndexer
    raise AttributeError(name)
