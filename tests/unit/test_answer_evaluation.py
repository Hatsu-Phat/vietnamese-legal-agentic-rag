"""End-to-end /ask metrics are deterministic and never invent rates."""

from pathlib import Path
from uuid import UUID

import pytest

from src.common.agent_errors import AgentError, LLMTimeout
from src.core.access import PUBLIC_ONLY, AccessScope
from src.core.agent import AskRequest, AskResponse, Citation, Claim
from src.core.answer_evaluation import AskCaseResult, AskEvalCase
from src.core.enums import LegalValidityStatus, QuestionType
from src.core.models import QAEvaluationItem
from src.evaluation.answer import (
    load_cases,
    percentile,
    run_cases,
    sample_answerable,
    summarize,
    summarize_by_type,
)

RID = UUID("00000000-0000-4000-8000-000000000001")


def citation(chunk_id: str) -> Citation:
    return Citation(
        citation_id="S1",
        chunk_id=chunk_id,
        document_id="law_demo",
        document_number="DEMO",
        title="t",
        breadcrumb="b",
        status=LegalValidityStatus.UNKNOWN,
        issued_year=None,
        source_url=None,
    )


def response(decision: str, chunk_ids: tuple[str, ...] = ()) -> AskResponse:
    return AskResponse.model_validate(
        {
            "request_id": RID,
            "decision": decision,
            "answer": "a",
            "claims": [Claim(text="c", citation_ids=["S1"])] if chunk_ids else [],
            "citations": [citation(c) for c in chunk_ids],
            "retrieval_rounds": 1,
            "elapsed_ms": 10.0,
        }
    )


def case(
    cid: str, qtype: QuestionType, expected: list[str], gold: tuple[str, ...] = ()
) -> AskEvalCase:
    return AskEvalCase.model_validate(
        {
            "case_id": cid,
            "question": f"câu hỏi {cid}",
            "question_type": qtype,
            "expected_decisions": expected,
            "gold_provision_ids": list(gold),
            "annotation_source": "test",
        }
    )


class ScriptedAsk:
    def __init__(self, outcomes: dict[str, AskResponse | AgentError]) -> None:
        self.outcomes = outcomes

    async def ask(
        self, request: AskRequest, request_id: UUID, access: AccessScope = PUBLIC_ONLY
    ) -> AskResponse:
        outcome = self.outcomes[request.question]
        if isinstance(outcome, AgentError):
            raise outcome
        return outcome


PROVISIONS = {"p1__c0": "p1", "p2__c0": "p2"}


@pytest.mark.asyncio
async def test_run_and_summarize() -> None:
    cases = [
        case("a1", QuestionType.DIRECT, ["answer"], ("p1",)),
        case("a2", QuestionType.DIRECT, ["answer"], ("p1",)),
        case("a3", QuestionType.DIRECT, ["answer"], ("p1",)),
        case("u1", QuestionType.UNANSWERABLE, ["abstain"]),
        case("m1", QuestionType.AMBIGUOUS, ["clarify"]),
        case("e1", QuestionType.DIRECT, ["answer"], ("p1",)),
    ]
    ask = ScriptedAsk(
        {
            "câu hỏi a1": response("answer", ("p1__c0", "p2__c0")),
            "câu hỏi a2": response("answer", ("p2__c0", "ghost__c0")),
            "câu hỏi a3": response("abstain"),
            "câu hỏi u1": response("answer", ("p2__c0",)),
            "câu hỏi m1": response("clarify"),
            "câu hỏi e1": LLMTimeout(),
        }
    )
    results = await run_cases(ask, cases, PROVISIONS.get)
    by_id = {r.case_id: r for r in results}
    assert by_id["a1"].gold_hit is True and by_id["a1"].gold_precision == 0.5
    assert by_id["a2"].gold_hit is False and by_id["a2"].unresolved_citations == 1
    assert by_id["a3"].gold_hit is None
    assert by_id["e1"].error_code == "LLM_TIMEOUT" and by_id["e1"].decision is None

    m = summarize(results)
    assert (m.cases, m.errors) == (6, 1)
    assert m.decision_accuracy == pytest.approx(3 / 5)
    assert m.answer_rate_on_answerable == pytest.approx(2 / 3)
    assert m.false_abstention_rate == pytest.approx(1 / 3)
    # Denominator: every case where answering is wrong (unanswerable + ambiguous).
    assert m.false_answer_rate_on_unanswerable == 0.5
    assert m.clarify_rate_on_ambiguous == 1.0
    assert m.citation_validity == pytest.approx(4 / 5)
    assert m.gold_hit_rate == 0.5
    assert m.citation_gold_precision == pytest.approx(0.5 * (0.5 + 0.0))
    assert set(summarize_by_type(results)) == {"direct", "unanswerable", "ambiguous"}


def test_empty_denominators_are_none() -> None:
    only_error = AskCaseResult(
        case_id="x",
        question_type=QuestionType.DIRECT,
        expected_decisions=["answer"],
        error_code="LLM_UNAVAILABLE",
    )
    m = summarize([only_error])
    assert m.decision_accuracy is None and m.citation_validity is None and m.p95_ms is None
    assert percentile([5.0, 1.0, 3.0, 2.0], 0.95) == 5.0


def test_sample_is_seeded_and_skips_items_without_gold(tmp_path: Path) -> None:
    path = tmp_path / "qa_dev.jsonl"
    items = [
        QAEvaluationItem(
            question_id=f"q{i}",
            question_text=f"câu {i}",
            gold_provision_ids=[f"p{i}"] if i % 3 else [],
        )
        for i in range(12)
    ]
    path.write_text("\n".join(i.model_dump_json() for i in items) + "\n", encoding="utf-8")
    first = sample_answerable(path, 5, seed=42)
    assert [c.case_id for c in first] == [c.case_id for c in sample_answerable(path, 5, seed=42)]
    assert len(first) == 5 and all(c.gold_provision_ids for c in first)
    assert [c.case_id for c in first] != [c.case_id for c in sample_answerable(path, 5, seed=7)]


def test_authored_cases_parse() -> None:
    cases = load_cases(Path("tests/eval/ask_dev_authored.jsonl"))
    assert len({c.case_id for c in cases}) == len(cases) >= 20
    assert all(c.gold_provision_ids for c in cases if c.expected_decisions == ["answer"])
