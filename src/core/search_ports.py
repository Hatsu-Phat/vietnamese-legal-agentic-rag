"""Interfaces for retrieval adapters and the teammate's FastAPI dependency."""

from typing import Protocol
from uuid import UUID

from src.core.access import PUBLIC_ONLY, AccessScope
from src.core.search import RetrievalCandidate, SearchFilters, SearchRequest, SearchResponse


class SearchPort(Protocol):
    """The HTTP layer depends on this protocol, including mock implementations."""

    @property
    def ready(self) -> bool:
        """Whether startup completed and shutdown has not begun."""
        ...

    async def search(
        self, request: SearchRequest, request_id: UUID, access: AccessScope = PUBLIC_ONLY
    ) -> SearchResponse:
        """Return grounded chunks within `access` or raise a safe domain SearchError."""
        ...


class QueryEncoder(Protocol):
    """Token validation and encoding share the same pinned tokenizer/model."""

    def token_count(self, query: str) -> int:
        """Count input tokens including model special tokens."""
        ...

    def encode_query(self, query: str) -> list[float]:
        """Return one normalized dense vector."""
        ...


class DenseSearch(Protocol):
    """A bounded read-only dense query with a monotonic deadline."""

    def retrieve(
        self,
        vector: list[float],
        filters: SearchFilters,
        limit: int,
        deadline: float,
        access: AccessScope = PUBLIC_ONLY,
    ) -> list[RetrievalCandidate]:
        """Apply filters and the access scope before top-k and validate source payloads."""
        ...

    def close(self) -> None:
        """Close the underlying client."""
        ...


class SparseSearch(Protocol):
    """A lexical query over the same canonical chunk release."""

    def retrieve(
        self, query: str, filters: SearchFilters, limit: int, access: AccessScope = PUBLIC_ONLY
    ) -> list[RetrievalCandidate]:
        """Apply metadata eligibility and the access scope before selecting top-k."""
        ...
