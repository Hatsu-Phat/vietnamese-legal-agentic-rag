"""Enterprise evaluation set (T-0103): role-aware cases over laws and internal documents."""

from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from src.core.agent import Decision
from src.core.enums import AccessLevel, QuestionType
from src.core.retrieval_evaluation import RetrievalMetrics
from src.core.search import Contract

Role = Literal["employee", "manager", "legal"]
ROLES: tuple[Role, ...] = ("employee", "manager", "legal")

# Access matrix agreed in PROJECT_PLAN (2026-11-05); P2 enforces it at retrieval time.
ROLE_ACCESS: dict[Role, frozenset[AccessLevel]] = {
    "employee": frozenset({AccessLevel.PUBLIC}),
    "manager": frozenset({AccessLevel.PUBLIC, AccessLevel.INTERNAL}),
    "legal": frozenset(AccessLevel),
}


class EnterpriseCategory(StrEnum):
    """What a case exercises; reported separately so one group cannot hide another."""

    INTERNAL_POLICY = "internal_policy"
    POLICY_AND_LAW = "policy_and_law"
    NEW_LAW = "new_law"
    ACCESS_PROBE = "access_probe"
    OUT_OF_SCOPE = "out_of_scope"


def expected_decisions(gold_levels: Iterable[AccessLevel | str]) -> dict[Role, list[Decision]]:
    """Answer when a role sees every gold provision, abstain when it sees none, either when partial."""
    levels = [AccessLevel(level) for level in gold_levels]
    expected: dict[Role, list[Decision]] = {}
    for role in ROLES:
        visible = sum(level in ROLE_ACCESS[role] for level in levels)
        if not levels or visible == 0:
            expected[role] = ["abstain"]
        elif visible == len(levels):
            expected[role] = ["answer"]
        else:
            expected[role] = ["answer", "abstain"]
    return expected


class EnterpriseEvalCase(Contract):
    """One question; expected behaviour depends on who asks."""

    case_id: Annotated[str, Field(min_length=1)]
    question: Annotated[str, Field(min_length=1)]
    category: EnterpriseCategory
    question_type: QuestionType
    gold_provision_ids: list[str] = Field(default_factory=list)
    expected_by_role: dict[Role, Annotated[list[Decision], Field(min_length=1)]]
    annotation_source: str
    review_status: str = "unreviewed"

    @model_validator(mode="after")
    def _consistent(self) -> "EnterpriseEvalCase":
        if set(self.expected_by_role) != set(ROLES):
            raise ValueError("expected_by_role must cover every role")
        if (self.category is EnterpriseCategory.OUT_OF_SCOPE) != (not self.gold_provision_ids):
            raise ValueError("Only out_of_scope cases may have no gold provisions")
        if len(set(self.gold_provision_ids)) != len(self.gold_provision_ids):
            raise ValueError("Duplicate gold provision")
        return self


class EnterpriseRetrievalReport(Contract):
    """Role-agnostic retrieval quality per category (no access filtering exists before P2)."""

    dataset: str
    collection: str
    release_fingerprint: str
    query_top_k: int
    cases_total: int
    cases_with_gold: int
    errors: int
    complete: bool
    by_category: dict[str, RetrievalMetrics]
    by_access_level: dict[str, RetrievalMetrics]
    overall: RetrievalMetrics
    parameters: dict[str, object]
    results: list[dict[str, object]]
