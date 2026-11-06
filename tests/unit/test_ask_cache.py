"""P5/T-0502: /ask cache keys, isolation by access scope, PII bypass, TTL and eviction."""

from uuid import UUID, uuid4

import pytest

from src.agents.ask_cache import (
    CACHE_HIT_TRACE,
    CachedAskService,
    ask_cache_key,
    normalize_question,
    pipeline_signature,
)
from src.agents.graph import AskService
from src.core.access import FULL_ACCESS, PUBLIC_ONLY, AccessScope
from src.core.agent import AskRequest, AskResponse
from src.core.enums import AccessLevel
from tests.unit.test_agent_graph import FakeGenerator, FakeSearch, answer, hit

MANAGER: AccessScope = frozenset({AccessLevel.PUBLIC, AccessLevel.INTERNAL})


class CountingAsk:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AccessScope]] = []

    async def ask(
        self, request: AskRequest, request_id: UUID, access: AccessScope = PUBLIC_ONLY
    ) -> AskResponse:
        self.calls.append((request.question, access))
        return AskResponse(
            request_id=request_id,
            decision="answer",
            answer=f"trả lời {len(self.calls)} cho {sorted(access)}",
            retrieval_rounds=1,
            elapsed_ms=10.0,
            trace=["plan", "finalize"],
        )


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def cached(inner: CountingAsk, **kwargs: object) -> CachedAskService:
    return CachedAskService(inner, "release-a", "pipe-1", **kwargs)  # type: ignore[arg-type]


def test_key_changes_with_every_answer_dependency() -> None:
    req = AskRequest(question="Thời hiệu khởi kiện?")
    base = ask_cache_key(req, PUBLIC_ONLY, "rel", "pipe")
    assert base == ask_cache_key(
        AskRequest(question="  thời HIỆU   khởi kiện? "), PUBLIC_ONLY, "rel", "pipe"
    )
    variants = [
        ask_cache_key(req, MANAGER, "rel", "pipe"),
        ask_cache_key(req, FULL_ACCESS, "rel", "pipe"),
        ask_cache_key(req, PUBLIC_ONLY, "rel-2", "pipe"),
        ask_cache_key(req, PUBLIC_ONLY, "rel", "pipe-2"),
        ask_cache_key(req.model_copy(update={"top_k": 3}), PUBLIC_ONLY, "rel", "pipe"),
        ask_cache_key(
            AskRequest(question="Thời hiệu khởi kiện?", as_of_date="2026-01-01"),  # type: ignore[arg-type]
            PUBLIC_ONLY,
            "rel",
            "pipe",
        ),
    ]
    assert len({base, *variants}) == len(variants) + 1


def test_normalize_and_pipeline_signature_are_stable() -> None:
    assert normalize_question("Á  B ") == normalize_question("Á b")
    assert pipeline_signature({"a": 1, "b": "x"}) == pipeline_signature({"b": "x", "a": 1})
    assert pipeline_signature({"prompt": "cot"}) != pipeline_signature({"prompt": "zero_shot"})


@pytest.mark.asyncio
async def test_repeat_hits_cache_with_fresh_request_id() -> None:
    inner = CountingAsk()
    service = cached(inner)
    first_id, second_id = uuid4(), uuid4()
    first = await service.ask(AskRequest(question="q"), first_id)
    second = await service.ask(AskRequest(question="Q "), second_id)
    assert len(inner.calls) == 1
    assert second.request_id == second_id and first.request_id == first_id
    assert second.answer == first.answer
    assert second.trace == [*first.trace, CACHE_HIT_TRACE]
    assert (service.stats.hits, service.stats.misses) == (1, 1)


@pytest.mark.asyncio
async def test_roles_never_share_entries() -> None:
    inner = CountingAsk()
    service = cached(inner)
    legal = await service.ask(AskRequest(question="q"), uuid4(), FULL_ACCESS)
    employee = await service.ask(AskRequest(question="q"), uuid4(), PUBLIC_ONLY)
    assert [scope for _, scope in inner.calls] == [FULL_ACCESS, PUBLIC_ONLY]
    assert employee.answer != legal.answer
    assert service.stats.hits == 0


@pytest.mark.asyncio
async def test_pii_questions_bypass_cache() -> None:
    inner = CountingAsk()
    service = cached(inner)
    question = "Số CCCD 012345678901 của tôi có bị lộ không?"
    await service.ask(AskRequest(question=question), uuid4())
    await service.ask(AskRequest(question=question), uuid4())
    assert len(inner.calls) == 2 and len(service) == 0
    assert service.stats.bypassed == 2


@pytest.mark.asyncio
async def test_ttl_expiry_eviction_and_disable() -> None:
    clock, inner = Clock(), CountingAsk()
    service = cached(inner, ttl_seconds=10.0, max_entries=2, clock=clock)
    for q in ("a", "b", "c"):  # "a" evicted (least recently used)
        await service.ask(AskRequest(question=q), uuid4())
    assert len(service) == 2 and service.stats.evictions == 1
    await service.ask(AskRequest(question="a"), uuid4())
    assert len(inner.calls) == 4
    clock.now = 11.0
    await service.ask(AskRequest(question="c"), uuid4())
    assert len(inner.calls) == 5  # expired entry is recomputed

    off = cached(CountingAsk(), ttl_seconds=0.0)
    await off.ask(AskRequest(question="a"), uuid4())
    await off.ask(AskRequest(question="a"), uuid4())
    assert off.stats.bypassed == 2 and len(off) == 0


@pytest.mark.asyncio
async def test_cached_graph_answer_makes_no_second_llm_call() -> None:
    gen = FakeGenerator([answer("S1")])
    search = FakeSearch([[hit(1), hit(2)]])
    service = CachedAskService(AskService(search, gen, 2), "release-a", "pipe-1")
    first = await service.ask(AskRequest(question="Thời hiệu?"), uuid4())
    calls = (len(gen.seen), gen.reviews, len(search.queries))
    second = await service.ask(AskRequest(question="thời hiệu?"), uuid4())
    assert (len(gen.seen), gen.reviews, len(search.queries)) == calls
    assert second.decision == first.decision == "answer"
    assert second.citations == first.citations
