"""P5/T-0503: /metrics counts what the tests sent; JSON logs carry the request ID."""

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from loguru import logger

from src.agents.ask_cache import CachedAskService
from src.agents.graph import AskService
from src.agents.router import ModelRouter
from src.api.app import create_app
from src.api.dependencies import ApiServices
from src.observability.metrics import METRICS, Counter, Histogram
from src.retrieval.search_catalog import CatalogSources
from tests.search_support import write_catalog
from tests.unit.test_agent_graph import FakeGenerator, answer
from tests.unit.test_api import StubSearch, client_for


@pytest.fixture(autouse=True)
def fresh_metrics() -> Iterator[None]:
    METRICS.reset()
    yield
    METRICS.reset()


@pytest.fixture
def cached_client(tmp_path: Path) -> Iterator[tuple[TestClient, FakeGenerator]]:
    search = StubSearch()
    gen = FakeGenerator([answer("S1")])
    ask = CachedAskService(AskService(search, ModelRouter(cloud=gen, mode="cloud"), 2), "r", "p")
    ask.publish_metrics()
    sources = CatalogSources(write_catalog(tmp_path / "chunks.jsonl", count=3))
    for client in client_for(ApiServices(search=search, sources=sources, ask=ask)):
        yield client, gen


def sample(text: str, name: str) -> float:
    """Value of one exact series line, e.g. 'x_total{a="b"}'."""
    for line in text.splitlines():
        if line.startswith(name + " "):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"{name} not in metrics")


def test_metrics_reflect_requests_decisions_cache_and_llm_calls(
    cached_client: tuple[TestClient, FakeGenerator],
) -> None:
    client, gen = cached_client
    for _ in range(3):
        assert client.get("/health").status_code == 200
    for _ in range(2):
        assert client.post("/ask", json={"question": "Thời hiệu khởi kiện?"}).status_code == 200
    assert client.get("/sources/missing_chunk__c0").status_code == 404
    assert client.get("/nope").status_code == 404

    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert sample(text, 'legal_rag_http_requests_total{route="/health",status="200"}') == 3
    assert sample(text, 'legal_rag_http_requests_total{route="/ask",status="200"}') == 2
    assert sample(text, 'legal_rag_http_requests_total{route="unmatched",status="404"}') == 1
    assert sample(text, 'legal_rag_http_request_seconds_count{route="/ask"}') == 2
    assert sample(text, 'legal_rag_ask_decisions_total{decision="answer",cached="false"}') == 1
    assert sample(text, 'legal_rag_ask_decisions_total{decision="answer",cached="true"}') == 1
    assert sample(text, "legal_rag_ask_cache_hits_total") == 1
    assert sample(text, "legal_rag_ask_cache_misses_total") == 1
    assert sample(text, "legal_rag_ask_cache_entries") == 1
    # One graph run: one draft and one review call, both on the cloud route.
    llm_ok = 'legal_rag_llm_calls_total{route="cloud",provider="FakeGenerator",outcome="ok"}'
    assert sample(text, llm_ok) == len(gen.seen) + gen.reviews == 2
    assert sample(text, 'legal_rag_ask_node_seconds_count{node="draft"}') == 1
    assert sample(text, 'legal_rag_ask_node_seconds_count{node="retrieve"}') == 1
    # /metrics scrapes do not count themselves; no request content leaks into labels.
    assert 'route="/metrics"' not in text and "Thời hiệu" not in text


def test_metrics_route_can_be_disabled(tmp_path: Path) -> None:
    search = StubSearch()
    sources = CatalogSources(write_catalog(tmp_path / "chunks.jsonl", count=1))

    @asynccontextmanager
    async def opener() -> AsyncIterator[ApiServices]:
        yield ApiServices(search=search, sources=sources)

    app = create_app(opener, metrics_enabled=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/metrics").status_code == 404


def test_histogram_and_counter_render_prometheus_text() -> None:
    hist = Histogram("h_seconds", "doc", ("node",), buckets=(0.1, 1.0))
    hist.observe(0.05, "a")
    hist.observe(0.5, "a")
    hist.observe(5.0, "a")
    lines = list(hist.render())
    assert 'h_seconds_bucket{node="a",le="0.1"} 1' in lines
    assert 'h_seconds_bucket{node="a",le="1"} 2' in lines
    assert 'h_seconds_bucket{node="a",le="+Inf"} 3' in lines
    assert 'h_seconds_count{node="a"} 3' in lines
    counter = Counter("c_total", "doc", ("k",))
    counter.inc('x"y')
    assert 'c_total{k="x\\"y"} 1' in list(counter.render())
    with pytest.raises(ValueError):
        counter.inc()


def test_json_log_lines_carry_request_id(cached_client: tuple[TestClient, FakeGenerator]) -> None:
    client, _ = cached_client
    lines: list[str] = []
    sink = logger.add(lines.append, serialize=True, level="INFO")
    try:
        response = client.post("/ask", json={"question": "Thời hiệu khởi kiện?"})
    finally:
        logger.remove(sink)
    rid = response.headers["X-Request-ID"]
    records = [json.loads(line)["record"] for line in lines]
    completed = [r for r in records if r["message"].startswith("Ask completed")]
    assert completed and completed[0]["extra"]["request_id"] == rid
    assert all("Thời hiệu" not in r["message"] for r in records)
