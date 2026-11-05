"""Deterministic end-to-end /ask metrics: decisions, abstention and citation grounding.

Citation support (does the cited text entail the claim?) is not measured here; it needs
human labels (roadmap §6.3). Gold hit only checks that a cited chunk lies in a gold provision.
"""

import asyncio
import hashlib
import math
from collections.abc import Callable, Iterable
from pathlib import Path
from uuid import uuid4

from src.common.agent_errors import AgentError
from src.core.agent import AskRequest, AskResponse
from src.core.answer_evaluation import AskCaseResult, AskEvalCase, AskMetrics
from src.core.api import AskPort
from src.core.enums import AnswerableStatus, QuestionType
from src.core.models import QAEvaluationItem

ProvisionOf = Callable[[str], str | None]


def load_cases(path: Path) -> list[AskEvalCase]:
    """One JSON case per non-empty line."""
    with path.open(encoding="utf-8") as stream:
        return [AskEvalCase.model_validate_json(line) for line in stream if line.strip()]


def sample_answerable(path: Path, size: int, seed: int) -> list[AskEvalCase]:
    """Seeded, order-independent sample of benchmark questions that have gold provisions."""
    with path.open(encoding="utf-8") as stream:
        items = [QAEvaluationItem.model_validate_json(line) for line in stream if line.strip()]
    usable = [
        i
        for i in items
        if i.gold_provision_ids and i.answerable_status == AnswerableStatus.ANSWERABLE
    ]
    usable.sort(key=lambda i: hashlib.sha256(f"{seed}:{i.question_id}".encode()).hexdigest())
    return [
        AskEvalCase(
            case_id=f"qa_dev:{i.question_id}",
            question=i.question_text,
            question_type=i.question_type,
            expected_decisions=["answer"],
            gold_provision_ids=i.gold_provision_ids,
            as_of_date=i.as_of_date,
            annotation_source=i.annotation_source,
            review_status=i.review_status,
        )
        for i in usable[:size]
    ]


def score_case(
    case: AskEvalCase, response: AskResponse, provision_of: ProvisionOf
) -> AskCaseResult:
    """Map citations back to provisions through the canonical catalog, never the LLM text."""
    chunk_ids = [c.chunk_id for c in response.citations]
    provisions = [provision_of(cid) for cid in chunk_ids]
    resolved = [p for p in provisions if p is not None]
    gold = set(case.gold_provision_ids)
    hit: bool | None = None
    precision: float | None = None
    if gold and response.decision == "answer" and resolved:
        hit = any(p in gold for p in resolved)
        precision = sum(p in gold for p in resolved) / len(resolved)
    elif gold and response.decision == "answer":
        hit, precision = False, 0.0
    return AskCaseResult(
        case_id=case.case_id,
        question_type=case.question_type,
        expected_decisions=case.expected_decisions,
        decision=response.decision,
        abstain_reason=response.abstain_reason,
        answer=response.answer,
        claims=len(response.claims),
        cited_chunk_ids=chunk_ids,
        cited_provision_ids=list(dict.fromkeys(resolved)),
        unresolved_citations=len(provisions) - len(resolved),
        limitations=[lim.code for lim in response.limitations],
        gold_hit=hit,
        gold_precision=precision,
        retrieval_rounds=response.retrieval_rounds,
        elapsed_ms=response.elapsed_ms,
    )


async def run_cases(
    ask: AskPort,
    cases: Iterable[AskEvalCase],
    provision_of: ProvisionOf,
    delay_seconds: float = 0.0,
    top_k: int = 8,
) -> list[AskCaseResult]:
    """Sequential to respect provider rate limits; failures are recorded, not hidden."""
    results: list[AskCaseResult] = []
    for index, case in enumerate(cases):
        if index and delay_seconds:
            await asyncio.sleep(delay_seconds)
        request = AskRequest(question=case.question, as_of_date=case.as_of_date, top_k=top_k)
        try:
            response = await ask.ask(request, uuid4())
        except AgentError as exc:
            results.append(
                AskCaseResult(
                    case_id=case.case_id,
                    question_type=case.question_type,
                    expected_decisions=case.expected_decisions,
                    error_code=exc.code,
                )
            )
            continue
        results.append(score_case(case, response, provision_of))
    return results


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def is_answerable(r: AskCaseResult) -> bool:
    return r.expected_decisions == ["answer"]


def summarize(results: list[AskCaseResult]) -> AskMetrics:
    """Aggregate only cases that ran; errors are counted separately."""
    ok = [r for r in results if r.error_code is None]
    # "Unanswerable" here means any case where answering is wrong, ambiguous ones included.
    answerable = [r for r in ok if is_answerable(r)]
    unanswerable = [r for r in ok if "answer" not in r.expected_decisions]
    ambiguous = [r for r in ok if r.question_type == QuestionType.AMBIGUOUS]
    graded = [r for r in ok if r.gold_hit is not None]
    precisions = [r.gold_precision for r in graded if r.gold_precision is not None]
    citations = sum(len(r.cited_chunk_ids) for r in ok)
    latencies = [r.elapsed_ms for r in ok]
    return AskMetrics(
        cases=len(results),
        errors=len(results) - len(ok),
        decision_accuracy=rate(sum(r.decision in r.expected_decisions for r in ok), len(ok)),
        answer_rate_on_answerable=rate(
            sum(r.decision == "answer" for r in answerable), len(answerable)
        ),
        false_abstention_rate=rate(
            sum(r.decision != "answer" for r in answerable), len(answerable)
        ),
        false_answer_rate_on_unanswerable=rate(
            sum(r.decision == "answer" for r in unanswerable), len(unanswerable)
        ),
        clarify_rate_on_ambiguous=rate(
            sum(r.decision == "clarify" for r in ambiguous), len(ambiguous)
        ),
        citation_validity=rate(citations - sum(r.unresolved_citations for r in ok), citations),
        gold_hit_rate=rate(sum(bool(r.gold_hit) for r in graded), len(graded)),
        citation_gold_precision=sum(precisions) / len(precisions) if precisions else None,
        mean_ms=sum(latencies) / len(latencies) if latencies else None,
        p95_ms=percentile(latencies, 0.95),
    )


def summarize_by_type(results: list[AskCaseResult]) -> dict[str, AskMetrics]:
    types = sorted({r.question_type.value for r in results})
    return {t: summarize([r for r in results if r.question_type.value == t]) for t in types}
