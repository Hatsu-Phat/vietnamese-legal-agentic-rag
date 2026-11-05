"""Reranking order, paired rerank ablation and RAGAS faithfulness bookkeeping."""

from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import pytest

from src.core.answer_evaluation import AskCaseResult
from src.core.enums import QuestionType
from src.core.models import QAEvaluationItem
from src.core.search import SearchRequest
from src.evaluation.ragas_runner import score_answers, summarize_faithfulness
from src.evaluation.rerank_ablation import evaluate_rerank
from src.retrieval.reranker import rerank
from src.retrieval.search_catalog import ChunkCatalog
from tests.search_support import catalog as catalog
from tests.search_support import service_for


def test_rerank_sorts_by_score_and_keeps_ties_stable() -> None:
    assert rerank(["a", "b", "c", "d"], [0.1, 0.9, 0.5, 0.9]) == ["b", "d", "c", "a"]
    with pytest.raises(ValueError):
        rerank(["a"], [])


@pytest.mark.asyncio
async def test_pool_reranking_recovers_a_deep_gold(catalog: ChunkCatalog, tmp_path: Path) -> None:
    service = service_for(catalog)
    fused = (await service.search(SearchRequest(query="môi trường", top_k=20), uuid4())).results
    deep = fused[14]
    dataset = tmp_path / "qa_dev.jsonl"
    item = QAEvaluationItem(
        question_id="q1", question_text="môi trường", gold_provision_ids=[deep.provision_id]
    )
    dataset.write_text(item.model_dump_json() + "\n", encoding="utf-8")
    gold_text = catalog.chunks[str(deep.point_uuid)].embedding_text

    def score(query: str, passages: Sequence[str]) -> list[float]:
        return [1.0 if p == gold_text else 0.0 for p in passages]

    report = await evaluate_rerank(service, score, dataset, pool=20)
    assert report.complete and report.questions_evaluated == 1
    assert report.metrics["hybrid@10"].recall_at_k["10"] == 0
    assert report.metrics["rerank_top10"].recall_at_k["10"] == 0
    assert report.metrics["rerank_pool20"].recall_at_k["1"] == 1
    assert report.metrics["rerank_pool20"].mrr == 1
    with pytest.raises(ValueError):
        await evaluate_rerank(service, score, dataset, pool=21)


def answered(
    case_id: str, chunks: list[str], answer: str = "Thời hiệu là 03 năm."
) -> AskCaseResult:
    return AskCaseResult(
        case_id=case_id,
        question_type=QuestionType.DIRECT,
        expected_decisions=["answer"],
        decision="answer",
        answer=answer,
        cited_chunk_ids=chunks,
    )


@pytest.mark.asyncio
async def test_faithfulness_scores_only_answers_and_records_failures() -> None:
    results = [
        answered("ok", ["c1"]),
        answered("missing", ["ghost"]),
        answered("boom", ["c1"]),
        AskCaseResult(
            case_id="abst",
            question_type=QuestionType.UNANSWERABLE,
            expected_decisions=["abstain"],
            decision="abstain",
        ),
    ]
    seen: list[list[str]] = []

    async def judge(question: str, answer: str, contexts: list[str]) -> float:
        if question == "q boom":
            raise RuntimeError("provider down")
        seen.append(contexts)
        return 0.5

    cases = await score_answers(
        results,
        {r.case_id: f"q {r.case_id}" for r in results},
        {"c1": "đoạn luật"}.get,
        judge,
    )
    by_id = {c.case_id: c for c in cases}
    assert set(by_id) == {"ok", "missing", "boom"}
    assert by_id["ok"].faithfulness == 0.5 and seen == [["đoạn luật"]]
    assert by_id["missing"].error == "NO_CONTEXT_OR_ANSWER"
    assert by_id["boom"].error == "RuntimeError"
    summary = summarize_faithfulness(cases)
    assert summary == {
        "answered_cases": 3,
        "scored_cases": 1,
        "mean_faithfulness": 0.5,
        "min_faithfulness": 0.5,
        "below_one": 1,
    }
