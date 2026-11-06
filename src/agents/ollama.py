"""Local AnswerGenerator over the Ollama HTTP API (P3/T-0301); no extra SDK dependency.

Uses /api/chat with `format` set to a JSON schema (structured outputs), the same prompts as
the Gemini provider, and the same strict validation. Provider errors never leave this class.
"""

import json
from collections import Counter
from typing import Any

import httpx
from pydantic import ValidationError

from src.agents.gemini import coerce_draft
from src.agents.prompts import (
    REVIEW_SYSTEM,
    REWRITE_SYSTEM,
    PromptVariant,
    draft_system,
    draft_user_message,
    review_user_message,
    rewrite_user_message,
)
from src.common.agent_errors import LLMTimeout, LLMUnavailable
from src.common.logger import logger
from src.core.agent import ClaimReview, DraftAnswer, DraftClaim, Evidence

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["answer", "clarify", "abstain"]},
        "answer": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "citation_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "citation_ids"],
            },
        },
        "clarifying_question": {"type": ["string", "null"]},
    },
    "required": ["decision", "answer", "claims"],
}
# CoT: "reasoning" is listed first so the model writes it before deciding; coerce_draft drops it.
COT_DRAFT_SCHEMA: dict[str, Any] = {
    **DRAFT_SCHEMA,
    "properties": {"reasoning": {"type": "string"}, **DRAFT_SCHEMA["properties"]},
    "required": ["reasoning", *DRAFT_SCHEMA["required"]],
}
REWRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}
REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_index": {"type": "integer"},
                    "supported": {"type": "boolean"},
                },
                "required": ["claim_index", "supported"],
            },
        }
    },
    "required": ["verdicts"],
}


class OllamaAnswerGenerator:
    """Structured-output chat calls to a local Ollama server."""

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        temperature: float = 0.0,
        timeout_seconds: float = 120.0,
        num_ctx: int = 8192,
        client: httpx.AsyncClient | None = None,
        prompt_variant: PromptVariant = "zero_shot",
    ) -> None:
        self._model = model
        # Ollama's default context (4096) silently truncates the start of long prompts, which
        # drops the system rules; draft/review prompts with 8 Vietnamese chunks reach ~6.7k.
        self._num_ctx = num_ctx
        self._temperature = temperature
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient(base_url=base_url)
        self._draft_system = draft_system(prompt_variant)
        self._draft_schema = COT_DRAFT_SCHEMA if prompt_variant == "cot" else DRAFT_SCHEMA
        # calls / prompt_tokens / output_tokens / schema_errors, for P4 cost reports.
        self.usage: Counter[str] = Counter()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _generate_json(self, system: str, user: str, schema: dict[str, Any]) -> Any:
        body = {
            "model": self._model,
            "stream": False,
            "format": schema,
            "options": {"temperature": self._temperature, "num_ctx": self._num_ctx},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            response = await self._client.post("/api/chat", json=body, timeout=self._timeout)
        except httpx.TimeoutException:
            raise LLMTimeout() from None
        except httpx.HTTPError as exc:
            logger.warning("Ollama request failed: {}", type(exc).__name__)
            raise LLMUnavailable() from None
        if response.status_code != 200:
            logger.warning("Ollama returned HTTP {}", response.status_code)
            raise LLMUnavailable()
        self.usage["calls"] += 1
        try:
            data = response.json()
            self.usage["prompt_tokens"] += int(data.get("prompt_eval_count") or 0)
            self.usage["output_tokens"] += int(data.get("eval_count") or 0)
            return json.loads(data["message"]["content"])
        except (KeyError, TypeError, ValueError, AttributeError):
            logger.warning("Ollama response was not the expected JSON shape")
            self.usage["schema_errors"] += 1
            raise LLMUnavailable() from None

    async def draft(self, question: str, evidence: list[Evidence]) -> DraftAnswer:
        raw = await self._generate_json(
            self._draft_system, draft_user_message(question, evidence), self._draft_schema
        )
        try:
            return DraftAnswer.model_validate(coerce_draft(raw))
        except ValidationError as exc:
            # Log field locations only; values may contain evidence or question text.
            locations = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
            logger.warning("Ollama draft failed schema validation at {}", locations)
            self.usage["schema_errors"] += 1
            raise LLMUnavailable() from None

    async def rewrite_query(self, question: str, feedback: str) -> str:
        raw = await self._generate_json(
            REWRITE_SYSTEM, rewrite_user_message(question, feedback), REWRITE_SCHEMA
        )
        query = raw.get("query") if isinstance(raw, dict) else None
        return query if isinstance(query, str) else question

    async def review(self, claims: list[DraftClaim], evidence: list[Evidence]) -> ClaimReview:
        raw = await self._generate_json(
            REVIEW_SYSTEM, review_user_message(claims, evidence), REVIEW_SCHEMA
        )
        try:
            return ClaimReview.model_validate(raw)
        except ValidationError:
            logger.warning("Ollama review failed schema validation")
            self.usage["schema_errors"] += 1
            raise LLMUnavailable() from None
