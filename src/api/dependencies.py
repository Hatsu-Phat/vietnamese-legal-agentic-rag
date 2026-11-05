"""Shared request-scoped helpers and the service container owned by the app lifespan."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import Request

from src.core.api import AskPort, SourcePort
from src.core.search_ports import SearchPort

REQUEST_ID_HEADER = "X-Request-ID"


@dataclass(frozen=True)
class ApiServices:
    """Everything a route may call; `ask` is None when no LLM is configured."""

    search: SearchPort
    sources: SourcePort
    ask: AskPort | None = None


ServicesOpener = Callable[[], AbstractAsyncContextManager[ApiServices]]


def request_id_of(request: Request) -> UUID:
    """The server-generated ID; client-supplied IDs are never trusted."""
    rid = getattr(request.state, "request_id", None)
    if isinstance(rid, UUID):
        return rid
    rid = uuid4()
    request.state.request_id = rid
    return rid


def services_of(request: Request) -> ApiServices:
    """Services exist only after startup; routes never construct them."""
    services: ApiServices = request.app.state.services
    return services
