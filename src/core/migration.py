"""Typed contracts for importing an existing dense index without re-embedding."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EmbeddingSpec(BaseModel):
    """The original embedding contract; a historical revision can be unknown."""

    model_id: str = "BAAI/bge-m3"
    vector_size: int = 1024
    distance: Literal["Cosine"] = "Cosine"
    normalize: bool = True
    max_seq_length: int = 512
    revision: str | None = None


class ImportReport(BaseModel):
    """Provenance and integrity of a prepared import, without credentials."""

    model_config = ConfigDict(extra="forbid")
    version: int = 1
    source_fingerprint: str
    checksums: dict[str, str]
    embedding: EmbeddingSpec
    input_chunks: int
    unique_chunks: int
    merged_rows: int
    source_vectors: int
    reused_vectors: int
    encoded_vectors: int = 0
    missing_vector_ids: list[str] = Field(default_factory=list)
    canonical_chunks_sha256: str = ""
    alias_map_sha256: str = ""


class MigrationCheckpoint(BaseModel):
    """Durable progress and target identity; never inferred from point count alone."""

    model_config = ConfigDict(extra="forbid")
    source_fingerprint: str
    collection: str
    embedding: EmbeddingSpec
    confirmed_points: int = 0
    verified: bool = False
    alias_activated: bool = False


class VerificationReport(BaseModel):
    """Actual remote evidence recorded after a complete comparison."""

    source_fingerprint: str
    collection: str
    expected_points: int
    actual_points: int
    compared_payloads: int
    compared_vectors: int
    vector_queries_passed: int
    alias_activated: bool = False
    model_revision: str | None = None
    compatibility_min_cosine: float | None = None
    text_queries: list[dict[str, object]] = Field(default_factory=list)
    truncation_count: int | None = None
    max_input_tokens: int | None = None
