"""Validated search configuration, separate from credentials and HTTP settings."""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class QdrantConfig(BaseModel):
    mode: Literal["local", "server", "memory"] = "local"
    url: str = "http://localhost:6333"
    collection_name: str
    embedding_model_id: Literal["BAAI/bge-m3"]
    embedding_revision: str = Field(min_length=1)
    vector_size: int = Field(gt=0)
    normalize_embeddings: Literal[True]
    distance: str
    max_seq_length: Literal[512]

    @model_validator(mode="after")
    def validate_distance(self) -> Self:
        if self.distance.lower() != "cosine":
            raise ValueError("Search v1 requires cosine distance")
        return self


class BM25Config(BaseModel):
    k1: float = Field(gt=0, allow_inf_nan=False)
    b: float = Field(ge=0, le=1, allow_inf_nan=False)
    segment_vietnamese: bool


class IndexingConfig(BaseModel):
    qdrant: QdrantConfig
    bm25: BM25Config


class BranchConfig(BaseModel):
    enabled: Literal[True]
    top_k: int = Field(ge=1)
    model_name: str | None = None


class FusionConfig(BaseModel):
    method: Literal["rrf"]
    rrf_k: int = Field(gt=0)
    top_candidates: int = Field(ge=1)


class RetrievalConfig(BaseModel):
    timeout_seconds: float = Field(default=30, gt=0, le=30)
    dense: BranchConfig
    sparse: BranchConfig
    fusion: FusionConfig


class SearchConfiguration(BaseModel):
    """Existing YAML may also contain future agent/LLM sections; search validates its own sections."""

    indexing: IndexingConfig
    retrieval: RetrievalConfig

    @model_validator(mode="after")
    def consistent_embedding(self) -> Self:
        name = self.retrieval.dense.model_name
        if name is not None and name != self.indexing.qdrant.embedding_model_id:
            raise ValueError("Dense model declarations disagree")
        return self
