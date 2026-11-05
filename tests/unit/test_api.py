"""FastAPI contract: request IDs, delegation to services and safe error mapping."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from src.agents.graph import AskService
from src.api.app import create_app
from src.api.dependencies import ApiServices
from src.common.search_errors import SearchError, SearchTimeout
from src.core.access import PUBLIC_ONLY, AccessScope
from src.core.search import SearchRequest, SearchResponse
from src.retrieval.search_catalog import CatalogSources
from tests.search_support import write_catalog
from tests.unit.test_agent_graph import FakeGenerator, FakeSearch, answer, hit


class StubSearch(FakeSearch):
    def __init__(self) -> None:
        super().__init__([[hit(1), hit(2)]])
        self.is_ready = True
        self.failure: Exception | None = None

    @property
    def ready(self) -> bool:
        return self.is_ready

    async def search(
        self, request: SearchRequest, request_id: UUID, access: AccessScope = PUBLIC_ONLY
    ) -> SearchResponse:
        if self.failure is not None:
            raise self.failure
        return await super().search(request, request_id, access)


@pytest.fixture
def search() -> StubSearch:
    return StubSearch()


def client_for(services: ApiServices) -> Iterator[TestClient]:
    @asynccontextmanager
    async def opener() -> AsyncIterator[ApiServices]:
        yield services

    with TestClient(create_app(opener), raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def client(search: StubSearch, tmp_path: Path) -> Iterator[TestClient]:
    sources = CatalogSources(write_catalog(tmp_path / "chunks.jsonl", count=3))
    ask = AskService(search, FakeGenerator([answer("S1")]), 2)
    yield from client_for(ApiServices(search=search, sources=sources, ask=ask))


def assert_same_id(response: object) -> str:
    rid = response.headers["X-Request-ID"]  # type: ignore[attr-defined]
    assert str(UUID(rid)) == rid
    assert response.json()["request_id"] == rid  # type: ignore[attr-defined]
    return rid


def test_health_and_ready(client: TestClient, search: StubSearch) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"search": True, "ask": True, "sources": True}
    search.is_ready = False
    assert client.get("/ready").status_code == 503


def test_search_returns_service_response_with_server_request_id(client: TestClient) -> None:
    supplied = "00000000-0000-4000-8000-000000000009"
    response = client.post(
        "/search", json={"query": "thời hiệu", "top_k": 2}, headers={"X-Request-ID": supplied}
    )
    assert response.status_code == 200
    assert assert_same_id(response) != supplied
    assert response.json()["returned_count"] == 2


@pytest.mark.parametrize(
    "body",
    [
        {"query": "", "top_k": 2},
        {"query": "thời hiệu", "top_k": "5"},
        {"query": "thời hiệu", "unexpected": True},
        {"query": "thời hiệu", "top_k": 21},
    ],
)
def test_invalid_search_is_422_without_echo(client: TestClient, body: dict[str, object]) -> None:
    response = client.post("/search", json=body)
    assert response.status_code == 422
    assert_same_id(response)
    assert response.json()["code"] == "INVALID_REQUEST"
    assert "thời hiệu" not in response.text


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (SearchTimeout(), 504, "SEARCH_TIMEOUT"),
        (RuntimeError("secret path"), 500, "INTERNAL_ERROR"),
    ],
)
def test_search_errors_are_mapped_safely(
    client: TestClient, search: StubSearch, failure: Exception, status: int, code: str
) -> None:
    search.failure = failure
    response = client.post("/search", json={"query": "thời hiệu"})
    assert response.status_code == status
    assert_same_id(response)
    assert response.json()["code"] == code
    assert "secret" not in response.text


def test_search_unavailable_when_not_ready(client: TestClient, search: StubSearch) -> None:
    search.is_ready = False
    for path, body in (("/search", {"query": "q"}), ("/ask", {"question": "q"})):
        response = client.post(path, json=body)
        assert response.status_code == 503
        assert response.json()["code"] == "SEARCH_UNAVAILABLE"


def test_ask_returns_grounded_answer_with_openable_citation(client: TestClient) -> None:
    response = client.post("/ask", json={"question": "Thời hiệu khởi kiện là bao lâu?"})
    assert response.status_code == 200
    assert_same_id(response)
    data = response.json()
    assert data["decision"] == "answer"
    assert data["citations"][0]["chunk_id"] == "law_demo__art_1__c0"


def test_ask_search_failure_keeps_error_contract(client: TestClient, search: StubSearch) -> None:
    search.failure = SearchError("SEARCH_UNAVAILABLE", "x")
    response = client.post("/ask", json={"question": "q"})
    assert response.status_code == 503
    assert response.json()["code"] == "SEARCH_UNAVAILABLE"


def test_ask_disabled_without_llm(search: StubSearch, tmp_path: Path) -> None:
    sources = CatalogSources(write_catalog(tmp_path / "chunks.jsonl", count=1))
    for client in client_for(ApiServices(search=search, sources=sources)):
        assert client.get("/ready").json()["ask"] is False
        response = client.post("/ask", json={"question": "q"})
        assert response.status_code == 503
        assert_same_id(response)
        assert response.json()["code"] == "LLM_UNAVAILABLE"


def test_sources_lookup(client: TestClient) -> None:
    found = client.get("/sources/law_demo__art_2__c0")
    assert found.status_code == 200
    assert_same_id(found)
    assert found.json()["breadcrumb"] == "DEMO > Điều 2"
    assert found.json()["source_url"] is None
    missing = client.get("/sources/law_demo__art_99__c0")
    assert missing.status_code == 404
    assert_same_id(missing)
    assert missing.json()["code"] == "SOURCE_NOT_FOUND"
    assert client.get("/sources/bad%20id").status_code == 422


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/nope")
    assert response.status_code == 404
    assert_same_id(response)
    assert response.json()["code"] == "NOT_FOUND"
