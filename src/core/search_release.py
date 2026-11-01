"""Reproducible BM25 release metadata and search runtime configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BM25Release(BaseModel):
    """Integrity checks are verified before deserializing a trusted local pickle."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    canonical_chunks_sha256: str | None
    corpus_fingerprint: str
    index_sha256: str
    metadata_sha256: str
    stopwords_sha256: str
    k1: float = Field(gt=0, allow_inf_nan=False)
    b: float = Field(ge=0, le=1, allow_inf_nan=False)
    segment_vietnamese: bool
    versions: dict[str, str]


class SearchRuntime(BaseModel):
    """Safe execution defaults; dependency timeouts fit within the outer deadline."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    timeout_seconds: float = Field(default=30, gt=0, le=30)
    dense_candidates: int = Field(default=10, ge=1)
    sparse_candidates: int = Field(default=10, ge=1)
    fusion_candidates: int = Field(default=10, ge=1)
    rrf_k: int = Field(default=60, gt=0)
    max_query_tokens: int = Field(default=512, gt=0)
    vector_size: int = Field(default=1024, gt=0)
