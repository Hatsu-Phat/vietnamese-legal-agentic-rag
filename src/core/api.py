"""HTTP-facing contracts and ports shared by FastAPI routes, mocks and the frontend."""

from typing import Literal, Protocol
from uuid import UUID

from pydantic import UUID4

from src.core.access import PUBLIC_ONLY, AccessScope
from src.core.agent import AgentErrorCode, AskRequest, AskResponse
from src.core.enums import LegalValidityStatus
from src.core.search import Contract

ApiErrorCode = AgentErrorCode | Literal["SOURCE_NOT_FOUND", "NOT_FOUND", "UNAUTHORIZED"]


class ApiErrorResponse(Contract):
    """One error envelope for every route; same fields as SearchErrorResponse."""

    request_id: UUID4
    code: ApiErrorCode
    message: str


class SourceResponse(Contract):
    """Full text and metadata of one canonical chunk, for opening a citation."""

    request_id: UUID4
    chunk_id: str
    document_id: str
    provision_id: str
    article_id: str
    document_number: str
    title: str
    breadcrumb: str
    text: str
    document_type: str | None
    issued_year: int | None
    status: LegalValidityStatus
    source_url: str | None


class HealthResponse(Contract):
    """Liveness only: the process answers HTTP."""

    status: Literal["ok"] = "ok"


class ReadinessResponse(Contract):
    """Which capabilities can take requests right now."""

    search: bool
    ask: bool
    sources: bool


class AskPort(Protocol):
    """The /ask dependency; AskService and test fakes implement it."""

    async def ask(
        self, request: AskRequest, request_id: UUID, access: AccessScope = PUBLIC_ONLY
    ) -> AskResponse:
        """Answer from evidence within `access` only; raise only AgentError."""
        ...


class SourcePort(Protocol):
    """Read-only lookup of canonical chunks by their public chunk ID."""

    def get_source(
        self, chunk_id: str, request_id: UUID, access: AccessScope = PUBLIC_ONLY
    ) -> SourceResponse | None:
        """None when the chunk is not in the active release or outside `access`."""
        ...
