"""Chunking package for Vietnamese Legal Agentic RAG.

Provides legal-aware hierarchical chunking, fallback sliding-window chunking,
and chunk quality auditing.
"""

from src.chunking.chunk_audit import ChunkAuditor
from src.chunking.fallback_chunker import split_text_sliding_window
from src.chunking.legal_chunker import LegalChunker

__all__ = [
    "ChunkAuditor",
    "LegalChunker",
    "split_text_sliding_window",
]
