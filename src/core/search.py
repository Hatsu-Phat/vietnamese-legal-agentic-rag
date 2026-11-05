"""Framework-independent contracts for Search v1 and offline retrieval evaluation."""

import unicodedata
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from src.core.enums import LegalValidityStatus

Branch = Literal["dense", "bm25"]
ErrorCode = Literal["INVALID_REQUEST", "SEARCH_UNAVAILABLE", "SEARCH_TIMEOUT", "INTERNAL_ERROR"]


class Contract(BaseModel):
    """Reject unexpected fields; contracts are immutable at attribute level."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def normalize_string(value: object) -> object:
    """Normalize text without coercing non-string input."""
    return unicodedata.normalize("NFC", value).strip() if isinstance(value, str) else value


class SearchFilters(Contract):
    """Exact, AND-combined metadata filters; null disables a condition."""

    document_id: Annotated[StrictStr, Field(min_length=1)] | None = None
    document_type: Annotated[StrictStr, Field(min_length=1)] | None = None
    issued_year: Annotated[StrictInt, Field(ge=1, le=9999)] | None = None

    @field_validator("document_id", "document_type", mode="before")
    @classmethod
    def normalize(cls, value: object) -> object:
        """Reject blank strings after NFC/whitespace normalization."""
        return normalize_string(value)


class SearchRequest(Contract):
    """One Hybrid Search request, independent of HTTP and model providers."""

    # Swagger example omits ``filters``: placeholder filter values match nothing.
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"query": "Thời hiệu khởi kiện tranh chấp hợp đồng là bao lâu?"}]
        }
    )

    query: Annotated[StrictStr, Field(min_length=1, max_length=2000)]
    top_k: Annotated[StrictInt, Field(ge=1, le=20)] = 10
    filters: SearchFilters = Field(default_factory=SearchFilters)

    @field_validator("query", mode="before")
    @classmethod
    def normalize(cls, value: object) -> object:
        """Preserve query meaning and line breaks; normalize NFC and outer space."""
        return normalize_string(value)


class SearchHit(Contract):
    """A source chunk ranked by RRF, without vectors or embedding input."""

    point_uuid: UUID
    chunk_id: str
    document_id: str
    provision_id: str
    article_id: str
    document_number: str
    title: str
    text: str
    breadcrumb: str
    document_type: str | None
    issued_year: int | None
    status: LegalValidityStatus
    source_url: str | None
    rank: int = Field(ge=1)
    score: float = Field(gt=0, allow_inf_nan=False)
    matched_by: list[Branch] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def branch_order(self) -> Self:
        """Keep branch attribution canonical and duplicate-free."""
        if self.matched_by != [b for b in ("dense", "bm25") if b in self.matched_by]:
            raise ValueError("matched_by must be unique, with dense before bm25")
        return self


class SearchWarning(Contract):
    """A limitation of the returned evidence, not a retrieval failure."""

    code: Literal["VALIDITY_UNVERIFIED", "SOURCE_URL_UNAVAILABLE"]
    message: str


class SearchResponse(Contract):
    """Stable response shared by a service, CLI, mocks and HTTP adapters."""

    request_id: UUID4
    results: list[SearchHit]
    returned_count: int = Field(ge=0, le=20)
    score_method: Literal["rrf"] = "rrf"
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    warnings: list[SearchWarning] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistency(self) -> Self:
        """Reject inconsistent counts, duplicate hits and non-contiguous ranks."""
        if self.returned_count != len(self.results):
            raise ValueError("returned_count differs from results length")
        if [h.rank for h in self.results] != list(range(1, len(self.results) + 1)):
            raise ValueError("Result ranks must be contiguous from 1")
        if len({h.point_uuid for h in self.results}) != len(self.results):
            raise ValueError("Duplicate result UUID")
        if len({w.code for w in self.warnings}) != len(self.warnings):
            raise ValueError("Duplicate warning code")
        if not self.results and self.warnings:
            raise ValueError("Empty results must not carry result warnings")
        return self


class SearchErrorResponse(Contract):
    """Safe HTTP/CLI error envelope; never includes an input or SDK exception."""

    request_id: UUID4
    code: ErrorCode
    message: str


class RetrievalCandidate(Contract):
    """A branch score before RRF; raw scores can be negative for cosine."""

    point_uuid: UUID
    score: float = Field(allow_inf_nan=False)


class FusedCandidate(Contract):
    """An internal RRF result, retaining ranks for deterministic tie breaking."""

    point_uuid: UUID
    score: float = Field(gt=0, allow_inf_nan=False)
    best_rank: int = Field(ge=1)
    matched_by: list[Branch]


class SearchRun(Contract):
    """Offline diagnostics, deliberately separate from the public API response."""

    response: SearchResponse
    dense: list[RetrievalCandidate]
    bm25: list[RetrievalCandidate]
    dense_ms: float = Field(ge=0)
    bm25_ms: float = Field(ge=0)
