"""Startup wiring for AskService; use inside FastAPI lifespan or the CLI."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from src.agents.gemini import GeminiAnswerGenerator
from src.agents.graph import AskService
from src.common.agent_errors import LLMUnavailable
from src.common.config import DEFAULT_CONFIG_PATH, AppSettings, get_settings
from src.retrieval.search_factory import open_search_service


def create_generator(settings: AppSettings) -> GeminiAnswerGenerator:
    """Only Gemini is wired for now; the key stays a SecretStr end to end."""
    if settings.llm_provider != "gemini":
        raise LLMUnavailable()
    key = settings.gemini_api_key or settings.llm_api_key
    if key is None or not key.get_secret_value():
        raise LLMUnavailable()
    return GeminiAnswerGenerator(
        api_key=key,
        model=settings.gemini_model_id,
        temperature=settings.llm_temperature,
        timeout_seconds=settings.llm_timeout_seconds,
    )


@asynccontextmanager
async def open_ask_service(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    device: str = "auto",
    settings: AppSettings | None = None,
) -> AsyncIterator[AskService]:
    """Fail fast on a missing LLM key before the slow search startup."""
    active = settings or get_settings()
    generator = create_generator(active)
    try:
        async with open_search_service(config_path, device) as search:
            yield AskService(
                search,
                generator,
                active.max_retrieval_rounds,
                verify_claims=active.strict_citation_verification,
            )
    finally:
        await generator.aclose()
