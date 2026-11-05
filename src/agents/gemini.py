"""Gemini AnswerGenerator over the REST API using httpx (no extra SDK dependency)."""

import asyncio
import json
from typing import Any

import httpx
from pydantic import SecretStr, ValidationError

from src.agents.prompts import (
    DRAFT_SYSTEM,
    REVIEW_SYSTEM,
    REWRITE_SYSTEM,
    draft_user_message,
    review_user_message,
    rewrite_user_message,
)
from src.common.agent_errors import LLMTimeout, LLMUnavailable
from src.common.logger import logger
from src.core.agent import ClaimReview, DraftAnswer, DraftClaim, Evidence

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# Rate limits and transient overloads get a bounded number of delayed retries; others fail fast.
RETRYABLE_STATUS = frozenset({429, 500, 503})
MAX_RETRIES = 2
# Longer suggested waits mean an exhausted daily quota, where retrying cannot help.
MAX_RETRY_WAIT_SECONDS = 60.0


def suggested_delay(response: httpx.Response, fallback: float) -> float | None:
    """Read google.rpc.RetryInfo or Retry-After; None when the wait is not worth retrying."""
    delay: float | None = None
    try:
        for detail in response.json().get("error", {}).get("details", []):
            value = detail.get("retryDelay") if isinstance(detail, dict) else None
            if isinstance(value, str) and value.endswith("s"):
                delay = float(value[:-1])
                break
    except (ValueError, AttributeError):
        pass
    if delay is None:
        try:
            delay = float(response.headers.get("retry-after", ""))
        except ValueError:
            delay = fallback
    return None if delay > MAX_RETRY_WAIT_SECONDS else max(delay, fallback)


def coerce_draft(raw: Any) -> Any:
    """Tolerate nulls and extra keys from JSON mode; types are still validated strictly."""
    if not isinstance(raw, dict):
        return raw
    draft = {k: raw[k] for k in DraftAnswer.model_fields if raw.get(k) is not None}
    if isinstance(draft.get("claims"), list):
        draft["claims"] = [
            {k: c[k] for k in DraftClaim.model_fields if c.get(k) is not None}
            if isinstance(c, dict)
            else c
            for c in draft["claims"]
        ]
    return draft


class GeminiAnswerGenerator:
    """JSON-mode generateContent calls; provider errors never leave this class."""

    def __init__(
        self,
        api_key: SecretStr,
        model: str,
        temperature: float = 0.0,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._client = client or httpx.AsyncClient(base_url=GEMINI_BASE_URL)
        self._timeout = timeout_seconds
        self._retry_delay = retry_delay_seconds

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        try:
            return await self._client.post(
                f"/models/{self._model}:generateContent",
                json=body,
                headers={"x-goog-api-key": self._api_key.get_secret_value()},
                timeout=self._timeout,
            )
        except httpx.TimeoutException:
            raise LLMTimeout() from None
        except httpx.HTTPError as exc:
            logger.warning("Gemini request failed: {}", type(exc).__name__)
            raise LLMUnavailable() from None

    async def _generate_json(self, system: str, user: str) -> Any:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": self._temperature,
                "responseMimeType": "application/json",
            },
        }
        response = await self._post(body)
        for attempt in range(1, MAX_RETRIES + 1):
            if response.status_code not in RETRYABLE_STATUS:
                break
            delay = suggested_delay(response, self._retry_delay)
            if delay is None:
                logger.warning(
                    "Gemini quota exhausted (HTTP {}); not retrying", response.status_code
                )
                break
            logger.warning(
                "Gemini returned HTTP {}; retry {}/{} in {:.1f}s",
                response.status_code,
                attempt,
                MAX_RETRIES,
                delay,
            )
            await asyncio.sleep(delay)
            response = await self._post(body)
        if response.status_code != 200:
            logger.warning("Gemini returned HTTP {}", response.status_code)
            raise LLMUnavailable()
        try:
            parts = response.json()["candidates"][0]["content"]["parts"]
            return json.loads("".join(p.get("text", "") for p in parts))
        except (KeyError, IndexError, TypeError, ValueError):
            logger.warning("Gemini response was not the expected JSON shape")
            raise LLMUnavailable() from None

    async def draft(self, question: str, evidence: list[Evidence]) -> DraftAnswer:
        raw = await self._generate_json(DRAFT_SYSTEM, draft_user_message(question, evidence))
        try:
            return DraftAnswer.model_validate(coerce_draft(raw))
        except ValidationError as exc:
            # Log field locations only; values may contain evidence or question text.
            locations = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
            logger.warning("Gemini draft failed schema validation at {}", locations)
            raise LLMUnavailable() from None

    async def rewrite_query(self, question: str, feedback: str) -> str:
        raw = await self._generate_json(REWRITE_SYSTEM, rewrite_user_message(question, feedback))
        query = raw.get("query") if isinstance(raw, dict) else None
        return query if isinstance(query, str) else question

    async def review(self, claims: list[DraftClaim], evidence: list[Evidence]) -> ClaimReview:
        raw = await self._generate_json(REVIEW_SYSTEM, review_user_message(claims, evidence))
        try:
            return ClaimReview.model_validate(raw)
        except ValidationError:
            logger.warning("Gemini review failed schema validation")
            raise LLMUnavailable() from None
