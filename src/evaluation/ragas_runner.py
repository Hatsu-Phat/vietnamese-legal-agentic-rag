"""RAGAS faithfulness for /ask answers, judged by Gemini through its OpenAI-compatible API.

Faithfulness = share of atomic statements in the answer that the cited chunk texts support.
It is an LLM judge: report it next to human review (roadmap §6.3), never instead of it.
"""

import statistics
import sys
import types
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic import SecretStr

from src.core.answer_evaluation import AskCaseResult, FaithfulnessCase

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

ScoreFn = Callable[[str, str, list[str]], Awaitable[float]]


def patch_langchain_community() -> None:
    """ragas 0.4.3 imports a module removed from the locked langchain-community 0.4.2.

    It only uses ChatVertexAI for isinstance checks, so an empty placeholder class is enough.
    Remove once the lockfile pairs ragas with a compatible langchain-community.
    """
    name = "langchain_community.chat_models.vertexai"
    if name not in sys.modules:
        module = types.ModuleType(name)
        module.ChatVertexAI = type("ChatVertexAI", (), {})  # type: ignore[attr-defined]
        sys.modules[name] = module


def gemini_faithfulness(api_key: SecretStr, model: str) -> ScoreFn:
    """Build a scorer; imports stay lazy so the API and unit tests never need the eval extra."""
    patch_langchain_community()
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import Faithfulness

    client = AsyncOpenAI(api_key=api_key.get_secret_value(), base_url=GEMINI_OPENAI_BASE_URL)
    metric: Any = Faithfulness(
        llm=llm_factory(model, provider="openai", client=client, temperature=0)
    )

    async def score(question: str, answer: str, contexts: list[str]) -> float:
        result = await metric.ascore(
            user_input=question, response=answer, retrieved_contexts=contexts
        )
        return float(result.value)

    return score


async def score_answers(
    results: Sequence[AskCaseResult],
    questions: dict[str, str],
    text_of: Callable[[str], str | None],
    score: ScoreFn,
) -> list[FaithfulnessCase]:
    """Score answered cases only; a judge failure is recorded per case, not hidden."""
    cases: list[FaithfulnessCase] = []
    for r in results:
        if r.decision != "answer":
            continue
        contexts = [t for t in (text_of(cid) for cid in r.cited_chunk_ids) if t]

        def case(
            value: float | None = None, error: str | None = None, r: AskCaseResult = r
        ) -> FaithfulnessCase:
            return FaithfulnessCase(
                case_id=r.case_id,
                question_type=r.question_type,
                answer=r.answer,
                cited_chunk_ids=r.cited_chunk_ids,
                faithfulness=value,
                error=error,
            )

        if not contexts or not r.answer:
            cases.append(case(error="NO_CONTEXT_OR_ANSWER"))
            continue
        try:
            value = await score(questions[r.case_id], r.answer, contexts)
        except Exception as exc:  # judge/provider failures are recorded as data
            cases.append(case(error=type(exc).__name__))
            continue
        cases.append(case(value))
    return cases


def summarize_faithfulness(cases: Sequence[FaithfulnessCase]) -> dict[str, float | int | None]:
    values = [c.faithfulness for c in cases if c.faithfulness is not None]
    return {
        "answered_cases": len(cases),
        "scored_cases": len(values),
        "mean_faithfulness": statistics.fmean(values) if values else None,
        "min_faithfulness": min(values) if values else None,
        "below_one": sum(v < 1.0 for v in values),
    }
