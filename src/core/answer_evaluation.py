"""Typed end-to-end /ask evaluation cases, per-case results and aggregate report."""

from datetime import date
from typing import Annotated

from pydantic import Field

from src.core.agent import AbstainReason, AgentErrorCode, Decision, LimitationCode
from src.core.enums import QuestionType
from src.core.search import Contract


class AskEvalCase(Contract):
    """One question with the decisions that count as correct behaviour."""

    case_id: Annotated[str, Field(min_length=1)]
    question: Annotated[str, Field(min_length=1)]
    question_type: QuestionType
    expected_decisions: Annotated[list[Decision], Field(min_length=1)]
    gold_provision_ids: list[str] = Field(default_factory=list)
    as_of_date: date | None = None
    annotation_source: str
    review_status: str = "unreviewed"


class AskCaseResult(Contract):
    """What the system did for one case; error cases carry no decision."""

    case_id: str
    question_type: QuestionType
    expected_decisions: list[Decision]
    decision: Decision | None = None
    abstain_reason: AbstainReason | None = None
    error_code: AgentErrorCode | None = None
    answer: str = ""
    claims: int = 0
    cited_chunk_ids: list[str] = Field(default_factory=list)
    cited_provision_ids: list[str] = Field(default_factory=list)
    unresolved_citations: int = 0
    limitations: list[LimitationCode] = Field(default_factory=list)
    gold_hit: bool | None = None
    gold_precision: float | None = None
    retrieval_rounds: int = 0
    elapsed_ms: float = 0.0


class AskMetrics(Contract):
    """Rates are None when their denominator is empty, never a made-up zero."""

    cases: int
    errors: int
    decision_accuracy: float | None
    answer_rate_on_answerable: float | None
    false_abstention_rate: float | None
    false_answer_rate_on_unanswerable: float | None
    clarify_rate_on_ambiguous: float | None
    citation_validity: float | None
    gold_hit_rate: float | None
    citation_gold_precision: float | None
    mean_ms: float | None
    p95_ms: float | None


class FaithfulnessCase(Contract):
    """RAGAS faithfulness of one answer against the text of the chunks it cites."""

    case_id: str
    question_type: QuestionType
    answer: str
    cited_chunk_ids: list[str]
    faithfulness: float | None = None
    error: str | None = None


class FaithfulnessReport(Contract):
    """LLM-judge metric; auxiliary to human review, never a replacement for it."""

    split: str
    llm_model: str
    judge_model: str
    run_date: date
    answered_cases: int
    scored_cases: int
    mean_faithfulness: float | None
    min_faithfulness: float | None
    below_one: int
    parameters: dict[str, object]
    cases: list[FaithfulnessCase]


class AskEvaluationReport(Contract):
    """Saved as-is; `complete` is false whenever any case failed with an error."""

    split: str
    llm_model: str
    release_fingerprint: str
    run_date: date
    seed: int
    parameters: dict[str, object]
    complete: bool
    overall: AskMetrics
    by_type: dict[str, AskMetrics]
    results: list[AskCaseResult]
