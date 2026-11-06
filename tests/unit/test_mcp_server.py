"""P5/T-0501: MCP tool contract and role isolation, through an in-process MCP client."""

from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from mcp import Client

from src.agents.graph import AskService
from src.api.dependencies import AccessResolver, ApiServices
from src.core.access import FULL_ACCESS, PUBLIC_ONLY, AccessPolicy, AccessScope
from src.mcp_server.server import McpUnauthorized, build_mcp_server, resolve_role
from src.observability.metrics import METRICS
from src.retrieval.search_catalog import CatalogSources
from tests.unit.test_agent_graph import FakeGenerator, FakeSearch, answer, hit
from tests.unit.test_authz import KEYS, mixed_catalog

CONFIDENTIAL_CHUNK = "internal_conf__art_1__c0"
PUBLIC_CHUNK = "law_new__art_1__c0"


@pytest.fixture
def resolver() -> AccessResolver:
    return AccessResolver(AccessPolicy(), KEYS)


@pytest.fixture
def parts(tmp_path: Path) -> tuple[FakeSearch, ApiServices]:
    search = FakeSearch([[hit(1), hit(2)]])
    services = ApiServices(
        search=search,
        sources=CatalogSources(mixed_catalog(tmp_path / "chunks.jsonl")),
        ask=AskService(search, FakeGenerator([answer("S1")]), 2),
    )
    return search, services


class Connector:
    """Opens in-process MCP clients, one per role key, closed in the test's own task."""

    def __init__(self, resolver: AccessResolver, services: ApiServices) -> None:
        self.resolver, self.services = resolver, services
        self.stack = AsyncExitStack()

    async def __aenter__(self) -> "Connector":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stack.aclose()

    async def __call__(self, role: str) -> Client:
        resolved, scope = resolve_role(self.resolver, KEYS[role].get_secret_value())  # type: ignore[index]
        assert resolved == role
        server = build_mcp_server(self.services, resolved, scope)
        return await self.stack.enter_async_context(Client(server, raise_exceptions=True))


@pytest.fixture
def connector(resolver: AccessResolver, parts: tuple[FakeSearch, ApiServices]) -> Connector:
    return Connector(resolver, parts[1])


def test_credentials_are_required_and_checked(resolver: AccessResolver) -> None:
    for bad in (None, "", "not-a-key-0123456789"):
        with pytest.raises(McpUnauthorized):
            resolve_role(resolver, bad)
    role, scope = resolve_role(resolver, KEYS["legal"].get_secret_value())
    assert (role, scope) == ("legal", FULL_ACCESS)


@pytest.mark.asyncio
async def test_three_tools_with_schemas(connector: Connector) -> None:
    async with connector as connect:
        client = await connect("employee")
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == {"search_legal", "get_source", "ask"}
        assert tools["search_legal"].input_schema["required"] == ["query"]
        assert tools["get_source"].input_schema["required"] == ["chunk_id"]
        assert tools["ask"].input_schema["required"] == ["question"]
        for tool in tools.values():
            assert tool.output_schema is not None  # structured output from the Pydantic contracts


@pytest.mark.asyncio
async def test_search_and_ask_run_with_the_role_scope(
    connector: Connector, parts: tuple[FakeSearch, ApiServices]
) -> None:
    async with connector as connect:
        search, _ = parts
        employee, legal = await connect("employee"), await connect("legal")
        found = await employee.call_tool("search_legal", {"query": "lương thưởng", "top_k": 2})
        assert not found.is_error and found.structured_content["returned_count"] == 2
        result = await legal.call_tool("ask", {"question": "Thời hiệu khởi kiện?"})
        assert not result.is_error
        assert result.structured_content["decision"] == "answer"
        assert result.structured_content["citations"][0]["citation_id"] == "S1"
        scopes: list[AccessScope] = search.scopes
        assert scopes[0] == PUBLIC_ONLY and scopes[-1] == FULL_ACCESS


@pytest.mark.asyncio
async def test_low_role_cannot_open_confidential_source(connector: Connector) -> None:
    async with connector as connect:
        employee, manager, legal = (
            await connect("employee"),
            await connect("manager"),
            await connect("legal"),
        )
        for client in (employee, manager):
            denied = await client.call_tool("get_source", {"chunk_id": CONFIDENTIAL_CHUNK})
            missing = await client.call_tool("get_source", {"chunk_id": "no_such__art_1__c0"})
            assert denied.is_error and missing.is_error
            # Same answer as a missing chunk: existence is not revealed.
            assert denied.content[0].text == missing.content[0].text
            assert "SOURCE_NOT_FOUND" in denied.content[0].text
        opened = await legal.call_tool("get_source", {"chunk_id": CONFIDENTIAL_CHUNK})
        assert not opened.is_error and opened.structured_content["chunk_id"] == CONFIDENTIAL_CHUNK
        public = await employee.call_tool("get_source", {"chunk_id": PUBLIC_CHUNK})
        assert not public.is_error


@pytest.mark.asyncio
async def test_invalid_input_and_pii_and_metrics(
    connector: Connector, parts: tuple[FakeSearch, ApiServices]
) -> None:
    async with connector as connect:
        METRICS.reset()
        search, _ = parts
        client = await connect("employee")
        bad = await client.call_tool("get_source", {"chunk_id": "../etc/passwd"})
        assert bad.is_error and "INVALID_REQUEST" in bad.content[0].text
        empty = await client.call_tool("search_legal", {"query": ""})
        assert empty.is_error
        await client.call_tool("search_legal", {"query": "email tôi là an.nguyen@example.com"})
        assert "an.nguyen@example.com" not in search.queries[-1]
        assert METRICS.mcp_calls.value("get_source", "error") == 1
        assert METRICS.mcp_calls.value("search_legal", "ok") == 1
        METRICS.reset()
