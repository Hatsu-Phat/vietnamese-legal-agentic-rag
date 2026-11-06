"""FastAPI application factory: request IDs, lifespan-owned services and safe error mapping."""

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.dependencies import REQUEST_ID_HEADER, ServicesOpener, Unauthorized, request_id_of
from src.api.routes import router
from src.common.agent_errors import AgentError
from src.common.search_errors import SearchError
from src.core.api import ApiErrorCode, ApiErrorResponse
from src.observability.metrics import METRICS

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def error_response(request: Request, status: int, code: ApiErrorCode, message: str) -> JSONResponse:
    """Fixed envelope; never echoes input, stack traces or provider errors."""
    rid = request_id_of(request)
    body = ApiErrorResponse(request_id=rid, code=code, message=message)
    return JSONResponse(
        body.model_dump(mode="json"), status_code=status, headers={REQUEST_ID_HEADER: str(rid)}
    )


def route_label(request: Request) -> str:
    """Route template (bounded cardinality); unmatched paths share one label."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def create_app(open_services: ServicesOpener, metrics_enabled: bool = True) -> FastAPI:
    """Build the app around an injectable opener so tests can run without models or keys."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_services() as services:
            app.state.services = services
            logger.info("API ready ask_enabled={}", services.ask is not None)
            yield

    app = FastAPI(
        title="Vietnamese Legal Agentic RAG",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def assign_request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = uuid4()
        started = time.perf_counter()
        status = 500
        try:
            with logger.contextualize(request_id=str(request.state.request_id)):
                response = await call_next(request)
            status = response.status_code
        finally:
            route = route_label(request)
            if route != "/metrics":
                METRICS.requests.inc(route, str(status))
                METRICS.request_latency.observe(time.perf_counter() - started, route)
        response.headers[REQUEST_ID_HEADER] = str(request.state.request_id)
        return response

    if metrics_enabled:

        @app.get("/metrics", include_in_schema=False)
        async def metrics() -> PlainTextResponse:
            """Prometheus text format; holds counts and timings only, no request content."""
            return PlainTextResponse(METRICS.render(), media_type=PROMETHEUS_CONTENT_TYPE)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(request, 422, "INVALID_REQUEST", "Yêu cầu không hợp lệ.")

    @app.exception_handler(Unauthorized)
    async def unauthorized(request: Request, exc: Unauthorized) -> JSONResponse:
        return error_response(request, 401, "UNAUTHORIZED", "Thông tin xác thực không hợp lệ.")

    @app.exception_handler(SearchError)
    async def search_failed(request: Request, exc: SearchError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(AgentError)
    async def agent_failed(request: Request, exc: AgentError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def http_failed(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return error_response(request, 404, "NOT_FOUND", "Không tìm thấy tài nguyên.")
        return error_response(request, exc.status_code, "INVALID_REQUEST", "Yêu cầu không hợp lệ.")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled error id={} type={}", request_id_of(request), type(exc).__name__)
        return error_response(request, 500, "INTERNAL_ERROR", "Lỗi hệ thống. Vui lòng thử lại sau.")

    app.include_router(router)
    return app
