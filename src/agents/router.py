"""Deterministic local/cloud LLM router with fallback (P3/T-0302).

Rules, in order:
1. The question contained personal identifiers, or any retrieved evidence is internal or
   confidential -> local only. If no local model is configured the request fails; it is
   never sent to the cloud, not even as a fallback.
2. Only one provider configured -> that provider.
3. Routing mode `local` / `cloud` (LLM_ROUTING_MODE) -> that provider.
4. Mode `auto`: a "simple" question -> local, otherwise cloud.

Fallback: a failed cloud call goes to the local model. A failed local call goes to the cloud
only when rule 1 did not apply.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict

from src.common.agent_errors import AgentError, LLMUnavailable
from src.common.logger import logger
from src.core.agent import AnswerGenerator, Evidence
from src.core.enums import AccessLevel

Route = Literal["local", "cloud"]
RoutingMode = Literal["auto", "local", "cloud"]
RouteReason = Literal[
    "pii",
    "restricted_evidence",
    "only_local",
    "only_cloud",
    "mode_local",
    "mode_cloud",
    "simple",
    "complex",
    "cloud_failed",
    "local_failed",
]
ROUTING_MODES: tuple[RoutingMode, ...] = ("auto", "local", "cloud")
RESTRICTED = frozenset({AccessLevel.INTERNAL, AccessLevel.CONFIDENTIAL})

T = TypeVar("T")


class RouteDecision(BaseModel):
    """Which provider serves an LLM call, and why; `sensitive` forbids the cloud."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    route: Route
    reason: RouteReason
    sensitive: bool = False

    @property
    def label(self) -> str:
        return f"{self.route}:{self.reason}"


@dataclass(frozen=True)
class SimpleRule:
    """Measurable 'simple question' rule; thresholds are to be calibrated on P3 numbers.

    Simple = short, at most one question mark, and the top evidence comes from at most
    `max_documents` documents.
    """

    max_question_words: int = 30
    max_question_marks: int = 1
    max_documents: int = 1
    top_evidence: int = 3

    def is_simple(self, question: str, evidence: list[Evidence]) -> bool:
        documents = {e.document_id for e in evidence[: self.top_evidence]}
        return (
            len(question.split()) <= self.max_question_words
            and question.count("?") <= self.max_question_marks
            and len(documents) <= self.max_documents
        )


def has_restricted(evidence: list[Evidence]) -> bool:
    """Evidence without access_level comes from pre-P1 releases (public law)."""
    return any((e.access_level or AccessLevel.PUBLIC) in RESTRICTED for e in evidence)


class ModelRouter:
    """Holds the two providers and applies the routing and fallback rules."""

    def __init__(
        self,
        local: AnswerGenerator | None = None,
        cloud: AnswerGenerator | None = None,
        mode: RoutingMode = "auto",
        rule: SimpleRule | None = None,
    ) -> None:
        if local is None and cloud is None:
            raise ValueError("At least one LLM provider is required")
        if mode not in ROUTING_MODES:
            raise ValueError(f"Unknown routing mode: {mode}")
        self.local = local
        self.cloud = cloud
        self.mode = mode
        self.rule = rule or SimpleRule()

    @property
    def providers(self) -> dict[str, str]:
        """Provider class per route, for logs and reports."""
        return {
            name: type(llm).__name__
            for name, llm in (("local", self.local), ("cloud", self.cloud))
            if llm is not None
        }

    async def aclose(self) -> None:
        for llm in (self.local, self.cloud):
            close = getattr(llm, "aclose", None)
            if close is not None:
                await close()

    def decide(self, question: str, evidence: list[Evidence], contains_pii: bool) -> RouteDecision:
        """Pass every piece of evidence retrieved so far, not only what will be shown."""
        sensitive: RouteReason | None = (
            "pii" if contains_pii else "restricted_evidence" if has_restricted(evidence) else None
        )
        if sensitive is not None:
            if self.local is None:
                logger.warning("Sensitive request but no local LLM; refusing to use the cloud")
                raise LLMUnavailable()
            return RouteDecision(route="local", reason=sensitive, sensitive=True)
        if self.cloud is None:
            return RouteDecision(route="local", reason="only_local")
        if self.local is None:
            return RouteDecision(route="cloud", reason="only_cloud")
        if self.mode == "local":
            return RouteDecision(route="local", reason="mode_local")
        if self.mode == "cloud":
            return RouteDecision(route="cloud", reason="mode_cloud")
        if self.rule.is_simple(question, evidence):
            return RouteDecision(route="local", reason="simple")
        return RouteDecision(route="cloud", reason="complex")

    def _provider(self, decision: RouteDecision) -> AnswerGenerator:
        if decision.route == "cloud":
            # Defence in depth: a sensitive decision must never reach this branch.
            if decision.sensitive or self.cloud is None:
                raise LLMUnavailable()
            return self.cloud
        if self.local is None:
            raise LLMUnavailable()
        return self.local

    async def run(
        self, decision: RouteDecision, call: Callable[[AnswerGenerator], Awaitable[T]]
    ) -> tuple[T, RouteDecision]:
        """Run one LLM call; return its result and the decision actually used."""
        try:
            return await call(self._provider(decision)), decision
        except AgentError as exc:
            fallback = self._fallback(decision)
            if fallback is None:
                raise
            logger.warning(
                "LLM {} failed with {}; falling back to {}",
                decision.route,
                exc.code,
                fallback.route,
            )
            return await call(self._provider(fallback)), fallback

    def _fallback(self, decision: RouteDecision) -> RouteDecision | None:
        if decision.route == "cloud" and self.local is not None:
            return RouteDecision(route="local", reason="cloud_failed")
        if decision.route == "local" and not decision.sensitive and self.cloud is not None:
            return RouteDecision(route="cloud", reason="local_failed")
        return None


def parse_mode(value: str) -> RoutingMode:
    """Validate LLM_ROUTING_MODE / --routing."""
    mode = value.strip().lower() or "auto"
    # PROJECT_PLAN.md called the auto mode "router"; existing .env files may still say so.
    if mode == "router":
        mode = "auto"
    if mode not in ROUTING_MODES:
        raise ValueError(f"LLM_ROUTING_MODE must be one of {ROUTING_MODES}, got {value!r}")
    return mode
