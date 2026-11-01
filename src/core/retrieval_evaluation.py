"""Typed offline reports; test-set tuning and HTTP concerns stay outside evaluation."""

from pydantic import BaseModel, Field


class RetrievalMetrics(BaseModel):
    """Macro means across questions; returned chunks collapse to unique provisions."""

    recall_at_k: dict[str, float]
    mrr: float
    mean_ms: float
    p95_ms: float


class QuestionEvaluation(BaseModel):
    """Audit trail without copying raw user question text into a report."""

    question_id: str
    gold_provision_ids: list[str]
    ranked_provisions: dict[str, list[str]]
    error_code: str | None = None


class RetrievalEvaluationReport(BaseModel):
    """Complete and partial runs are explicitly distinguishable."""

    split: str
    release_fingerprint: str
    query_top_k: int
    questions_total: int
    questions_evaluated: int
    errors: int
    skipped_without_gold: int
    metrics: dict[str, RetrievalMetrics]
    parameters: dict[str, object]
    results: list[QuestionEvaluation] = Field(default_factory=list)
    complete: bool
