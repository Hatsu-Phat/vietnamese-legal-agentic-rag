"""P3: local/cloud LLM router, fallback, and the Ollama provider.

The key guarantee: no cloud call is made when the question contained PII or any retrieved
evidence is internal/confidential, in every routing mode and on every fallback path.
"""

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from src.agents.factory import create_router
from src.agents.gemini import GeminiAnswerGenerator
from src.agents.graph import AskService, merge_evidence
from src.agents.ollama import OllamaAnswerGenerator
from src.agents.router import ModelRouter, RoutingMode, SimpleRule, parse_mode
from src.api.dependencies import ApiServices
from src.common.agent_errors import AgentError, LLMTimeout, LLMUnavailable
from src.common.config import AppSettings
from src.core.agent import (
    AskRequest,
    ClaimReview,
    DraftAnswer,
    DraftClaim,
    Evidence,
)
from src.core.enums import AccessLevel
from src.core.search import SearchHit
from src.retrieval.search_catalog import CatalogSources
from tests.search_support import write_catalog
from tests.unit.test_agent_graph import FakeGenerator, FakeSearch, answer, hit
from tests.unit.test_api import client_for

PII_QUESTION = "Tôi CCCD 001203004567, lương thử việc tối thiểu bao nhiêu?"
LONG_QUESTION = " ".join(["điều kiện"] * 20) + "?"


def restricted(i: int, level: AccessLevel = AccessLevel.CONFIDENTIAL) -> SearchHit:
    return hit(i).model_copy(
        update={"access_level": level, "document_id": "tnt_qc_luong", "chunk_id": f"tnt__{i}"}
    )


def other_doc(i: int) -> SearchHit:
    return hit(i).model_copy(update={"document_id": f"law_other_{i}", "chunk_id": f"o__{i}"})


class CountingGenerator(FakeGenerator):
    """Counts every call; optionally fails every call with the given error."""

    def __init__(self, error: AgentError | None = None, drafts: list[DraftAnswer] | None = None):
        super().__init__(drafts or [answer("S1")])
        self.error = error
        self.calls = 0

    def _count(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error

    async def draft(self, question: str, evidence: list[Evidence]) -> DraftAnswer:
        self._count()
        return await super().draft(question, evidence)

    async def rewrite_query(self, question: str, feedback: str) -> str:
        self._count()
        return await super().rewrite_query(question, feedback)

    async def review(self, claims: list[DraftClaim], evidence: list[Evidence]) -> ClaimReview:
        self._count()
        return await super().review(claims, evidence)


def evidence(*hits: SearchHit) -> list[Evidence]:
    return merge_evidence([], list(hits))


# --- decision table ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "question", "hits", "pii", "expected"),
    [
        ("auto", "q", [hit(1)], True, "local:pii"),
        ("cloud", "q", [hit(1)], True, "local:pii"),
        ("auto", "q", [hit(1), restricted(2)], False, "local:restricted_evidence"),
        ("cloud", "q", [restricted(1, AccessLevel.INTERNAL)], False, "local:restricted_evidence"),
        ("auto", "q", [hit(1), hit(2)], False, "local:simple"),
        ("auto", LONG_QUESTION, [hit(1)], False, "cloud:complex"),
        ("auto", "a? b?", [hit(1)], False, "cloud:complex"),
        ("auto", "q", [hit(1), other_doc(2)], False, "cloud:complex"),
        ("local", LONG_QUESTION, [hit(1)], False, "local:mode_local"),
        ("cloud", "q", [hit(1)], False, "cloud:mode_cloud"),
        # Pre-P1 chunks carry no access_level: public law.
        ("auto", "q", [hit(1).model_copy(update={"access_level": None})], False, "local:simple"),
    ],
)
def test_decision_table(
    mode: RoutingMode, question: str, hits: list[SearchHit], pii: bool, expected: str
) -> None:
    router = ModelRouter(CountingGenerator(), CountingGenerator(), mode)
    decision = router.decide(question, evidence(*hits), pii)
    assert decision.label == expected
    assert decision.sensitive == (expected.split(":")[1] in ("pii", "restricted_evidence"))


def test_single_provider_and_missing_local() -> None:
    only_cloud = ModelRouter(cloud=CountingGenerator())
    assert only_cloud.decide("q", evidence(hit(1)), False).label == "cloud:only_cloud"
    with pytest.raises(LLMUnavailable):
        only_cloud.decide("q", evidence(restricted(1)), False)
    with pytest.raises(LLMUnavailable):
        only_cloud.decide("q", evidence(hit(1)), True)
    assert ModelRouter(local=CountingGenerator()).decide("q", [], False).label == (
        "local:only_local"
    )
    with pytest.raises(ValueError):
        ModelRouter()
    with pytest.raises(ValueError):
        parse_mode("gemini")
    assert parse_mode(" ") == "auto" and SimpleRule().is_simple("q", [])
    assert parse_mode("Router") == "auto"


# --- end to end through the graph ------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "local", "cloud"])
async def test_no_cloud_call_with_confidential_evidence(mode: RoutingMode) -> None:
    local, cloud = CountingGenerator(), CountingGenerator()
    service = AskService(FakeSearch([[restricted(1), hit(2)]]), ModelRouter(local, cloud, mode), 2)
    response = await service.ask(AskRequest(question=LONG_QUESTION), uuid4())
    assert response.decision == "answer"
    assert cloud.calls == 0 and local.calls >= 2  # draft + review
    assert "route:local:restricted_evidence" in response.trace


@pytest.mark.asyncio
async def test_restricted_evidence_keeps_later_rounds_local() -> None:
    # Round 1 retrieves a confidential chunk; the draft cites nothing valid, so the graph
    # rewrites (an LLM call) and retrieves public law. Every call must stay local.
    local = CountingGenerator(drafts=[answer("S9"), answer("S2")])
    cloud = CountingGenerator()
    search = FakeSearch([[restricted(1)], [other_doc(2)]])
    service = AskService(search, ModelRouter(local, cloud, "cloud"), 2)
    response = await service.ask(AskRequest(question="q"), uuid4())
    assert local.rewrites == 1 and response.retrieval_rounds == 2
    assert cloud.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "cloud"])
async def test_no_cloud_call_for_pii_even_without_evidence(mode: RoutingMode) -> None:
    local, cloud = CountingGenerator(), CountingGenerator()
    # No usable evidence at first: the graph rewrites the query before drafting.
    search = FakeSearch([[], [hit(1)]])
    response = await AskService(search, ModelRouter(local, cloud, mode), 2).ask(
        AskRequest(question=PII_QUESTION), uuid4()
    )
    assert cloud.calls == 0 and local.rewrites == 1
    assert "PII_MASKED" in [lim.code for lim in response.limitations]


@pytest.mark.asyncio
async def test_sensitive_request_fails_rather_than_use_cloud() -> None:
    cloud = CountingGenerator()
    # No local model configured.
    with pytest.raises(AgentError) as info:
        await AskService(FakeSearch([[restricted(1)]]), ModelRouter(cloud=cloud), 2).ask(
            AskRequest(question="q"), uuid4()
        )
    assert info.value.code == "LLM_UNAVAILABLE" and cloud.calls == 0
    # Local configured but down: no fallback to the cloud either.
    local = CountingGenerator(error=LLMTimeout())
    with pytest.raises(AgentError) as info:
        await AskService(FakeSearch([[restricted(1)]]), ModelRouter(local, cloud), 2).ask(
            AskRequest(question="q"), uuid4()
        )
    assert info.value.code == "LLM_TIMEOUT" and cloud.calls == 0


@pytest.mark.asyncio
async def test_cloud_failure_falls_back_to_local() -> None:
    local, cloud = CountingGenerator(), CountingGenerator(error=LLMUnavailable())
    response = await AskService(FakeSearch([[hit(1)]]), ModelRouter(local, cloud, "cloud"), 2).ask(
        AskRequest(question="q"), uuid4()
    )
    assert response.decision == "answer"
    assert "fallback:local:cloud_failed" in response.trace
    # The review reuses the provider that drafted, so the failing cloud is tried only once.
    assert cloud.calls == 1 and local.calls == 2


@pytest.mark.asyncio
async def test_public_local_failure_falls_back_to_cloud() -> None:
    local, cloud = CountingGenerator(error=LLMUnavailable()), CountingGenerator()
    response = await AskService(FakeSearch([[hit(1)]]), ModelRouter(local, cloud, "auto"), 2).ask(
        AskRequest(question="q"), uuid4()
    )
    assert response.decision == "answer"
    assert response.trace[3:5] == ["route:local:simple", "fallback:cloud:local_failed"]


def test_ask_route_survives_gemini_503(tmp_path: Path) -> None:
    """Acceptance 3: with Gemini returning 503, /ask answers through the local model."""
    gemini_calls = 0

    def unavailable(request: httpx.Request) -> httpx.Response:
        nonlocal gemini_calls
        gemini_calls += 1
        return httpx.Response(503, json={"error": {"code": 503}})

    gemini = GeminiAnswerGenerator(
        SecretStr("k"),
        "m",
        client=httpx.AsyncClient(base_url="https://t", transport=httpx.MockTransport(unavailable)),
        retry_delay_seconds=0,
    )
    local = CountingGenerator()
    search = FakeSearch([[hit(1)]])
    services = ApiServices(
        search=search,
        sources=CatalogSources(write_catalog(tmp_path / "chunks.jsonl", count=3)),
        ask=AskService(search, ModelRouter(local, gemini, "cloud"), 2),
    )
    for client in client_for(services):
        body = client.post("/ask", json={"question": "Lương thử việc tối thiểu bao nhiêu?"})
        assert body.status_code == 200
        assert body.json()["decision"] == "answer"
        assert "fallback:local:cloud_failed" in body.json()["trace"]
    assert gemini_calls == 3  # first try + 2 retries, then local
    assert local.calls == 2


# --- Ollama provider -------------------------------------------------------------------------


def ollama_with(handler: httpx.MockTransport) -> OllamaAnswerGenerator:
    client = httpx.AsyncClient(base_url="http://ollama", transport=handler)
    return OllamaAnswerGenerator("qwen2.5:3b", client=client)


@pytest.mark.asyncio
async def test_ollama_sends_schema_and_parses_structured_output() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "verdicts" in json.dumps(body["format"]):
            content = '{"verdicts": [{"claim_index": 0, "supported": true}]}'
        elif "query" in body["format"]["properties"]:
            content = '{"query": "thời gian thử việc"}'
        else:
            content = json.dumps(
                {
                    "decision": "answer",
                    "answer": "Không quá 180 ngày.",
                    "claims": [{"text": "c", "citation_ids": ["S1"]}],
                    "clarifying_question": None,
                },
                ensure_ascii=False,
            )
        return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})

    gen = ollama_with(httpx.MockTransport(handler))
    draft = await gen.draft("q", evidence(hit(1)))
    assert draft.decision == "answer" and draft.claims[0].citation_ids == ["S1"]
    assert await gen.rewrite_query("q", "f") == "thời gian thử việc"
    review = await gen.review(draft.claims, evidence(hit(1)))
    assert review.verdicts[0].supported
    assert all(b["model"] == "qwen2.5:3b" and b["stream"] is False for b in bodies)
    assert all(b["options"]["num_ctx"] == 8192 for b in bodies)  # type: ignore[index]
    await gen.aclose()


@pytest.mark.asyncio
async def test_ollama_errors_map_to_agent_errors() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "not json"}})

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    for handler, error in (
        (down, LLMUnavailable),
        (slow, LLMTimeout),
        (broken, LLMUnavailable),
        (missing, LLMUnavailable),
    ):
        with pytest.raises(error):
            await ollama_with(httpx.MockTransport(handler)).draft("q", evidence(hit(1)))


# --- wiring -----------------------------------------------------------------------------------


def settings(**env: str) -> AppSettings:
    return AppSettings(_env_file=None, **env)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_create_router_from_settings() -> None:
    both = create_router(settings(GEMINI_API_KEY="k", LLM_ROUTING_MODE="cloud"))
    assert both.providers == {"local": "OllamaAnswerGenerator", "cloud": "GeminiAnswerGenerator"}
    assert both.mode == "cloud"
    await both.aclose()
    local_only = create_router(settings(LOCAL_LLM_PROVIDER="ollama"), mode="local")
    assert local_only.cloud is None and local_only.mode == "local"
    await local_only.aclose()
    with pytest.raises(LLMUnavailable):
        create_router(settings(LOCAL_LLM_PROVIDER="none"))
    with pytest.raises(ValueError):
        create_router(settings(LOCAL_LLM_PROVIDER="vllm"))
