"""LangGraph /ask workflow, citation audit, validity screening and bounded retry."""

from datetime import date
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from src.agents.gemini import GeminiAnswerGenerator
from src.agents.graph import AskService, merge_evidence
from src.citation.temporal import screen_evidence
from src.citation.validator import audit_citations
from src.common.agent_errors import AgentError, LLMUnavailable
from src.common.search_errors import SearchTimeout
from src.core.access import PUBLIC_ONLY, AccessScope
from src.core.agent import (
    AskRequest,
    ClaimReview,
    ClaimVerdict,
    DraftAnswer,
    DraftClaim,
    Evidence,
)
from src.core.enums import LegalValidityStatus
from src.core.search import SearchHit, SearchRequest, SearchResponse


def hit(i: int, status: LegalValidityStatus = LegalValidityStatus.UNKNOWN) -> SearchHit:
    return SearchHit(
        point_uuid=UUID(int=i + 1),
        chunk_id=f"law_demo__art_{i}__c0",
        document_id="law_demo",
        provision_id=f"law_demo__art_{i}",
        article_id=str(i),
        document_number="91/2015/QH13",
        title=f"Điều {i}",
        text=f"nội dung điều {i}",
        breadcrumb=f"BLDS > Điều {i}",
        document_type="Luật",
        issued_year=2015,
        status=status,
        source_url=None,
        rank=1,
        score=0.5,
        matched_by=["dense"],
    )


class FakeSearch:
    def __init__(self, rounds: list[list[SearchHit]]) -> None:
        self.rounds = rounds
        self.queries: list[str] = []
        self.scopes: list[AccessScope] = []

    @property
    def ready(self) -> bool:
        return True

    async def search(
        self, request: SearchRequest, request_id: UUID, access: AccessScope = PUBLIC_ONLY
    ) -> SearchResponse:
        self.queries.append(request.query)
        self.scopes.append(access)
        hits = self.rounds[min(len(self.queries), len(self.rounds)) - 1]
        ranked = [h.model_copy(update={"rank": r}) for r, h in enumerate(hits, 1)]
        return SearchResponse(
            request_id=request_id, results=ranked, returned_count=len(ranked), elapsed_ms=1.0
        )


class FakeGenerator:
    def __init__(self, drafts: list[DraftAnswer], support: list[list[bool]] | None = None) -> None:
        self.drafts = drafts
        self.support = support
        self.seen: list[list[str]] = []
        self.rewrites = 0
        self.reviews = 0

    async def draft(self, question: str, evidence: list[Evidence]) -> DraftAnswer:
        self.seen.append([e.citation_id for e in evidence])
        return self.drafts[min(len(self.seen), len(self.drafts)) - 1]

    async def rewrite_query(self, question: str, feedback: str) -> str:
        self.rewrites += 1
        return f"truy vấn mới {self.rewrites}"

    async def review(self, claims: list[DraftClaim], evidence: list[Evidence]) -> ClaimReview:
        self.reviews += 1
        flags = (
            self.support[min(self.reviews, len(self.support)) - 1]
            if self.support
            else [True] * len(claims)
        )
        return ClaimReview(
            verdicts=[ClaimVerdict(claim_index=i, supported=f) for i, f in enumerate(flags)]
        )


def answer(*cids: str) -> DraftAnswer:
    return DraftAnswer(
        decision="answer",
        answer="Thời hiệu khởi kiện là 03 năm.",
        claims=[DraftClaim(text="Thời hiệu là 03 năm.", citation_ids=list(cids))],
    )


@pytest.mark.asyncio
async def test_grounded_answer_maps_citations_from_metadata() -> None:
    search, gen = FakeSearch([[hit(1), hit(2)]]), FakeGenerator([answer("S2")])
    response = await AskService(search, gen, 2).ask(AskRequest(question="q"), uuid4())
    assert response.decision == "answer" and response.retrieval_rounds == 1
    assert [c.citation_id for c in response.citations] == ["S2"]
    assert response.citations[0].breadcrumb == "BLDS > Điều 2"
    assert response.citations[0].chunk_id == "law_demo__art_2__c0"
    assert {lim.code for lim in response.limitations} == {
        "VALIDITY_UNVERIFIED",
        "SOURCE_URL_UNAVAILABLE",
    }
    assert response.trace == [
        "plan",
        "retrieve:1",
        "temporal_check",
        "draft",
        "audit:pass",
        "verify:1/1",
        "finalize",
    ]


@pytest.mark.asyncio
async def test_invented_citation_retries_then_abstains_within_budget() -> None:
    search, gen = FakeSearch([[hit(1)], [hit(2)]]), FakeGenerator([answer("S9")])
    response = await AskService(search, gen, 2).ask(AskRequest(question="q"), uuid4())
    assert response.decision == "abstain" and response.abstain_reason == "CITATION_INVALID"
    assert response.retrieval_rounds == 2 and search.queries == ["q", "truy vấn mới 1"]
    assert gen.seen == [["S1"], ["S1", "S2"]]
    assert response.claims == [] and response.citations == []


@pytest.mark.asyncio
async def test_retry_recovers_with_new_evidence() -> None:
    search = FakeSearch([[hit(1)], [hit(1), hit(2)]])
    gen = FakeGenerator([DraftAnswer(decision="abstain"), answer("S1", "S2")])
    response = await AskService(search, gen, 2).ask(AskRequest(question="q"), uuid4())
    assert response.decision == "answer" and response.retrieval_rounds == 2
    assert [c.citation_id for c in response.citations] == ["S1", "S2"]


@pytest.mark.asyncio
async def test_no_evidence_abstains_without_drafting() -> None:
    search, gen = FakeSearch([[]]), FakeGenerator([answer("S1")])
    response = await AskService(search, gen, 2).ask(AskRequest(question="q"), uuid4())
    assert response.decision == "abstain" and response.abstain_reason == "NO_EVIDENCE"
    assert gen.seen == [] and response.retrieval_rounds == 2


@pytest.mark.asyncio
async def test_only_expired_evidence_is_never_shown_to_llm() -> None:
    search = FakeSearch([[hit(1, LegalValidityStatus.EXPIRED)]])
    gen = FakeGenerator([answer("S1")])
    response = await AskService(search, gen, 1).ask(AskRequest(question="q"), uuid4())
    assert response.decision == "abstain" and response.abstain_reason == "ONLY_INVALID_EVIDENCE"
    assert gen.seen == []


@pytest.mark.asyncio
async def test_clarify_carries_no_source_caveats() -> None:
    gen = FakeGenerator(
        [DraftAnswer(decision="clarify", clarifying_question="Hợp đồng ký năm nào?")]
    )
    response = await AskService(FakeSearch([[hit(1)]]), gen, 2).ask(
        AskRequest(question="q", as_of_date=date(2020, 1, 1)), uuid4()
    )
    assert response.decision == "clarify"
    assert response.clarifying_question == "Hợp đồng ký năm nào?"
    assert response.limitations == []


@pytest.mark.asyncio
async def test_search_errors_become_agent_errors() -> None:
    class Failing(FakeSearch):
        async def search(
            self, request: SearchRequest, request_id: UUID, access: AccessScope = PUBLIC_ONLY
        ) -> SearchResponse:
            raise SearchTimeout()

    with pytest.raises(AgentError) as info:
        await AskService(Failing([]), FakeGenerator([]), 2).ask(AskRequest(question="q"), uuid4())
    assert info.value.code == "SEARCH_TIMEOUT"


@pytest.mark.asyncio
async def test_reviewer_removes_unsupported_claims_and_rebuilds_answer() -> None:
    draft = DraftAnswer(
        decision="answer",
        answer="Câu trả lời có cả nhận định không được hỗ trợ.",
        claims=[
            DraftClaim(text="Đúng theo nguồn.", citation_ids=["S1"]),
            DraftClaim(text="Suy diễn thêm.", citation_ids=["S2"]),
        ],
    )
    gen = FakeGenerator([draft], support=[[True, False]])
    response = await AskService(FakeSearch([[hit(1), hit(2)]]), gen, 2).ask(
        AskRequest(question="q"), uuid4()
    )
    assert response.decision == "answer" and response.answer == "Đúng theo nguồn."
    assert [c.citation_id for c in response.citations] == ["S1"]
    assert "UNSUPPORTED_CLAIMS_REMOVED" in {lim.code for lim in response.limitations}
    assert "verify:1/2" in response.trace


@pytest.mark.asyncio
async def test_all_claims_unsupported_retries_then_abstains() -> None:
    gen = FakeGenerator([answer("S1")], support=[[False]])
    search = FakeSearch([[hit(1)], [hit(2)]])
    response = await AskService(search, gen, 2).ask(AskRequest(question="q"), uuid4())
    assert response.decision == "abstain" and response.abstain_reason == "UNSUPPORTED_CLAIMS"
    assert gen.reviews == 2 and response.retrieval_rounds == 2 and response.claims == []


@pytest.mark.asyncio
async def test_verification_can_be_disabled() -> None:
    gen = FakeGenerator([answer("S1")], support=[[False]])
    response = await AskService(FakeSearch([[hit(1)]]), gen, 2, verify_claims=False).ask(
        AskRequest(question="q"), uuid4()
    )
    assert response.decision == "answer" and gen.reviews == 0


def test_audit_flags_missing_and_unknown_citations() -> None:
    evidence = merge_evidence([], [hit(1)])
    draft = DraftAnswer(
        decision="answer",
        claims=[DraftClaim(text="a"), DraftClaim(text="b", citation_ids=["S1", "X"])],
    )
    audit = audit_citations(draft, evidence)
    assert not audit.passed
    assert [(i.code, i.claim_index) for i in audit.issues] == [
        ("CLAIM_WITHOUT_CITATION", 0),
        ("UNKNOWN_CITATION", 1),
    ]
    assert not audit_citations(DraftAnswer(decision="answer"), evidence).passed


def test_merge_keeps_ids_stable_and_deduplicates() -> None:
    first = merge_evidence([], [hit(1), hit(2)])
    merged = merge_evidence(first, [hit(2), hit(3)])
    assert [(e.citation_id, e.chunk_id) for e in merged] == [
        ("S1", "law_demo__art_1__c0"),
        ("S2", "law_demo__art_2__c0"),
        ("S3", "law_demo__art_3__c0"),
    ]


def test_screen_drops_superseded() -> None:
    evidence = merge_evidence([], [hit(1, LegalValidityStatus.SUPERSEDED), hit(2)])
    kept, limitations = screen_evidence(evidence, None)
    assert [e.citation_id for e in kept] == ["S2"]
    assert "EXCLUDED_INVALID_EVIDENCE" in {lim.code for lim in limitations}


@pytest.mark.asyncio
async def test_gemini_adapter_parses_json_and_hides_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "test-key"
        if b"QUESTION" in request.content and b"EVIDENCE" in request.content:
            text = '{"decision": "answer", "answer": "x", "claims": [{"text": "c", "citation_ids": ["S1"]}]}'
            return httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]}
            )
        return httpx.Response(400, json={"error": "boom"})

    client = httpx.AsyncClient(base_url="https://test", transport=httpx.MockTransport(handler))
    gen = GeminiAnswerGenerator(SecretStr("test-key"), "m", client=client)
    draft = await gen.draft("q", merge_evidence([], [hit(1)]))
    assert draft.claims[0].citation_ids == ["S1"]
    with pytest.raises(LLMUnavailable):
        await gen.rewrite_query("q", "feedback")
    await gen.aclose()


@pytest.mark.asyncio
async def test_gemini_retries_once_on_rate_limit() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "rate"})
        text = '{"verdicts": [{"claim_index": 0, "supported": true}]}'
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})

    client = httpx.AsyncClient(base_url="https://test", transport=httpx.MockTransport(handler))
    gen = GeminiAnswerGenerator(SecretStr("k"), "m", client=client, retry_delay_seconds=0)
    claims = [DraftClaim(text="c", citation_ids=["S1"])]
    review = await gen.review(claims, merge_evidence([], [hit(1)]))
    assert calls == 2 and review.verdicts[0].supported
    await gen.aclose()


def test_suggested_delay_reads_retry_info_and_caps() -> None:
    from src.agents.gemini import MAX_RETRY_WAIT_SECONDS, suggested_delay

    body = {"error": {"details": [{"@type": "x.RetryInfo", "retryDelay": "17s"}]}}
    assert suggested_delay(httpx.Response(429, json=body), 2.0) == 17.0
    long = {"error": {"details": [{"retryDelay": "500s"}]}}
    assert suggested_delay(httpx.Response(429, json=long), 2.0) is None
    edge = {"error": {"details": [{"retryDelay": f"{MAX_RETRY_WAIT_SECONDS}s"}]}}
    assert suggested_delay(httpx.Response(429, json=edge), 2.0) == MAX_RETRY_WAIT_SECONDS
    assert suggested_delay(httpx.Response(503, headers={"retry-after": "5"}), 2.0) == 5.0
    assert suggested_delay(httpx.Response(503, text="oops"), 2.0) == 2.0


@pytest.mark.asyncio
async def test_answer_with_as_of_date_is_flagged_unverified() -> None:
    gen = FakeGenerator([answer("S1")])
    response = await AskService(FakeSearch([[hit(1)]]), gen, 2).ask(
        AskRequest(question="q", as_of_date=date(2020, 1, 1)), uuid4()
    )
    assert response.decision == "answer" and response.as_of_date == date(2020, 1, 1)
    assert "AS_OF_DATE_UNVERIFIED" in {lim.code for lim in response.limitations}


@pytest.mark.asyncio
async def test_gemini_gives_up_after_bounded_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": {}})

    client = httpx.AsyncClient(base_url="https://test", transport=httpx.MockTransport(handler))
    gen = GeminiAnswerGenerator(SecretStr("k"), "m", client=client, retry_delay_seconds=0)
    with pytest.raises(LLMUnavailable):
        await gen.rewrite_query("q", "f")
    assert calls == 3
    await gen.aclose()


@pytest.mark.asyncio
async def test_gemini_does_not_retry_exhausted_daily_quota() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": {"details": [{"retryDelay": "44433s"}]}})

    client = httpx.AsyncClient(base_url="https://test", transport=httpx.MockTransport(handler))
    gen = GeminiAnswerGenerator(SecretStr("k"), "m", client=client, retry_delay_seconds=0)
    with pytest.raises(LLMUnavailable):
        await gen.rewrite_query("q", "f")
    assert calls == 1
    await gen.aclose()


def test_coerce_draft_drops_nulls_and_extra_keys() -> None:
    from src.agents.gemini import coerce_draft

    raw = {
        "decision": "clarify",
        "answer": None,
        "claims": None,
        "clarifying_question": "Hành vi nào?",
        "reasoning": "extra",
    }
    draft = DraftAnswer.model_validate(coerce_draft(raw))
    assert draft.decision == "clarify" and draft.claims == [] and draft.answer == ""
    with pytest.raises(ValueError):
        DraftAnswer.model_validate(coerce_draft({"decision": "maybe"}))
