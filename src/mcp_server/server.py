"""MCP server (P5/T-0501): search_legal, get_source and ask over the same services as the API.

One server process serves one role: the credential (MCP_API_KEY) is resolved once at startup
with the P2 AccessResolver, and every tool call runs with that role's access scope. A missing
or unknown credential refuses to start; there is no anonymous MCP mode. Errors are returned as
MCP tool errors with fixed messages (no stack traces, inputs or provider details).
"""

import re
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated
from uuid import uuid4

from loguru import logger
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError

from src.api.dependencies import AccessResolver, ApiServices, Unauthorized
from src.common.agent_errors import AgentError
from src.common.search_errors import SearchError
from src.core.access import AccessScope, Role
from src.core.agent import AskRequest, AskResponse
from src.core.api import SourceResponse
from src.core.search import SearchRequest, SearchResponse
from src.guardrails.pii import mask_pii
from src.observability.metrics import METRICS

CHUNK_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,200}$")
SERVER_INSTRUCTIONS = (
    "Tra cứu pháp luật Việt Nam và quy chế nội bộ (dữ liệu tổng hợp) có trích dẫn. "
    "Kết quả là nguồn tham khảo, không phải tư vấn pháp lý. Quyền truy cập theo vai trò của "
    "khoá API dùng để khởi động server."
)


class McpUnauthorized(Exception):
    """MCP_API_KEY is missing or matches no role."""


def resolve_role(access: AccessResolver, credential: str | None) -> tuple[Role, AccessScope]:
    """Credential → (role, scope); anonymous is not allowed over MCP."""
    if not credential:
        raise McpUnauthorized("MCP_API_KEY is required")
    try:
        role, scope = access.resolve(credential)
    except Unauthorized:  # never echo the credential
        raise McpUnauthorized("MCP_API_KEY matches no role") from None
    if role is None:
        raise McpUnauthorized("MCP_API_KEY matches no role")
    return role, scope


async def _guarded[T](tool: str, run: Callable[[], Awaitable[T]]) -> T:
    """Map service errors to fixed tool errors and count outcomes."""
    try:
        result = await run()
    except ToolError:
        METRICS.mcp_calls.inc(tool, "error")
        raise
    except (SearchError, AgentError) as exc:
        METRICS.mcp_calls.inc(tool, "error")
        raise ToolError(f"{exc.code}: {exc.message}") from None
    except ValidationError:
        METRICS.mcp_calls.inc(tool, "error")
        raise ToolError("INVALID_REQUEST: Yêu cầu không hợp lệ.") from None
    METRICS.mcp_calls.inc(tool, "ok")
    return result


def build_mcp_server(services: ApiServices, role: Role, scope: AccessScope) -> MCPServer:
    """Register the three tools; `scope` is fixed for the lifetime of the server."""
    server = MCPServer(
        name="vietnamese-legal-rag",
        title="Vietnamese Legal Agentic RAG",
        instructions=SERVER_INSTRUCTIONS,
        version="0.1.0",
    )
    logger.info("MCP server role={} scope={}", role, sorted(str(level) for level in scope))

    @server.tool(
        title="Tìm kiếm văn bản pháp luật",
        description=(
            "Tìm kiếm lai (dense + BM25) trong corpus luật và quy chế nội bộ trong phạm vi "
            "quyền của vai trò. Trả về các đoạn nguồn kèm chunk_id; không phải câu trả lời."
        ),
    )
    async def search_legal(
        query: Annotated[str, Field(min_length=1, max_length=2000)],
        top_k: Annotated[int, Field(ge=1, le=20)] = 5,
    ) -> SearchResponse:
        async def run() -> SearchResponse:
            if not services.search.ready:
                raise ToolError("SEARCH_UNAVAILABLE: Dịch vụ tìm kiếm chưa sẵn sàng.")
            masked = mask_pii(query)
            request = SearchRequest(query=masked.text, top_k=top_k)
            return await services.search.search(request, uuid4(), scope)

        return await _guarded("search_legal", run)

    @server.tool(
        title="Mở nguồn trích dẫn",
        description=(
            "Toàn văn và metadata của một đoạn theo chunk_id. Đoạn ngoài quyền được báo "
            "không tìm thấy như đoạn không tồn tại."
        ),
    )
    async def get_source(
        chunk_id: Annotated[str, Field(min_length=1, max_length=200)],
    ) -> SourceResponse:
        async def run() -> SourceResponse:
            if not CHUNK_ID.fullmatch(chunk_id):
                raise ToolError("INVALID_REQUEST: chunk_id không hợp lệ.")
            found = services.sources.get_source(chunk_id, uuid4(), scope)
            if found is None:
                raise ToolError("SOURCE_NOT_FOUND: Không tìm thấy nguồn được yêu cầu.")
            return found

        return await _guarded("get_source", run)

    @server.tool(
        title="Hỏi đáp pháp luật có trích dẫn",
        description=(
            "Chạy agent LangGraph: trả lời có trích dẫn, hỏi lại khi mơ hồ, hoặc từ chối khi "
            "thiếu căn cứ. as_of_date (YYYY-MM-DD) là thời điểm người dùng hỏi tới."
        ),
    )
    async def ask(
        question: Annotated[str, Field(min_length=1, max_length=2000)],
        as_of_date: date | None = None,
    ) -> AskResponse:
        async def run() -> AskResponse:
            if services.ask is None:
                raise ToolError("LLM_UNAVAILABLE: Chưa cấu hình LLM.")
            if not services.search.ready:
                raise ToolError("SEARCH_UNAVAILABLE: Dịch vụ tìm kiếm chưa sẵn sàng.")
            request = AskRequest(question=question, as_of_date=as_of_date)
            return await services.ask.ask(request, uuid4(), scope)

        return await _guarded("ask", run)

    return server
