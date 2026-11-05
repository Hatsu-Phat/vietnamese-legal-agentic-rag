"""HTTP routes: validation and delegation only; no retrieval or legal reasoning here."""

from typing import Annotated

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse

from src.api.dependencies import REQUEST_ID_HEADER, access_of, request_id_of, services_of
from src.common.agent_errors import LLMUnavailable
from src.common.search_errors import SearchUnavailable
from src.core.agent import AskErrorResponse, AskRequest, AskResponse
from src.core.api import ApiErrorResponse, HealthResponse, ReadinessResponse, SourceResponse
from src.core.search import SearchErrorResponse, SearchRequest, SearchResponse

router = APIRouter()

CHUNK_ID_PATTERN = r"^[A-Za-z0-9_.\-]{1,200}$"


@router.get("/health")
async def health() -> HealthResponse:
    """Liveness: does not touch models, Qdrant or the LLM."""
    return HealthResponse()


@router.get("/ready", responses={503: {"model": ReadinessResponse}})
async def ready(request: Request) -> JSONResponse:
    """Readiness gates /search; /ask additionally needs an LLM key."""
    services = services_of(request)
    body = ReadinessResponse(
        search=services.search.ready,
        ask=services.ask is not None and services.search.ready,
        sources=True,
    )
    return JSONResponse(body.model_dump(), status_code=200 if body.search else 503)


@router.post(
    "/search",
    responses={
        422: {"model": SearchErrorResponse},
        503: {"model": SearchErrorResponse},
        504: {"model": SearchErrorResponse},
    },
)
async def search(body: SearchRequest, request: Request) -> SearchResponse:
    """Hybrid evidence search; results are sources, not a verified legal answer."""
    services = services_of(request)
    scope = access_of(request)
    if not services.search.ready:
        raise SearchUnavailable()
    return await services.search.search(body, request_id_of(request), scope)


@router.post(
    "/ask",
    responses={
        422: {"model": AskErrorResponse},
        503: {"model": AskErrorResponse},
        504: {"model": AskErrorResponse},
    },
)
async def ask(body: AskRequest, request: Request) -> AskResponse:
    """Grounded answer, clarification or abstention from the LangGraph workflow."""
    services = services_of(request)
    scope = access_of(request)
    if services.ask is None:
        raise LLMUnavailable()
    if not services.search.ready:
        raise SearchUnavailable()
    return await services.ask.ask(body, request_id_of(request), scope)


@router.get(
    "/sources/{chunk_id}",
    response_model=SourceResponse,
    responses={404: {"model": ApiErrorResponse}, 422: {"model": ApiErrorResponse}},
)
async def source(
    chunk_id: Annotated[str, Path(pattern=CHUNK_ID_PATTERN)], request: Request
) -> SourceResponse | JSONResponse:
    """Open a citation: full canonical text and metadata of one chunk."""
    rid = request_id_of(request)
    # Out-of-scope chunks answer 404 like missing ones, so their existence is not revealed.
    found = services_of(request).sources.get_source(chunk_id, rid, access_of(request))
    if found is None:
        body = ApiErrorResponse(
            request_id=rid, code="SOURCE_NOT_FOUND", message="Không tìm thấy nguồn được yêu cầu."
        )
        return JSONResponse(
            body.model_dump(mode="json"), status_code=404, headers={REQUEST_ID_HEADER: str(rid)}
        )
    return found
