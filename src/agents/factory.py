"""Startup wiring for AskService; use inside FastAPI lifespan or the CLI."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from src.agents.gemini import GeminiAnswerGenerator
from src.agents.graph import AskService
from src.agents.ollama import OllamaAnswerGenerator
from src.agents.router import ModelRouter, parse_mode
from src.common.agent_errors import LLMUnavailable
from src.common.config import DEFAULT_CONFIG_PATH, AppSettings, get_settings
from src.common.logger import logger
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


def create_local_generator(settings: AppSettings) -> OllamaAnswerGenerator | None:
    """None when LOCAL_LLM_PROVIDER is `none`; reachability is checked per call, not here."""
    provider = settings.local_llm_provider.strip().lower()
    if provider in ("none", "off", "disabled"):
        return None
    if provider != "ollama":
        raise ValueError(f"Unsupported LOCAL_LLM_PROVIDER: {settings.local_llm_provider!r}")
    return OllamaAnswerGenerator(
        model=settings.local_llm_model,
        base_url=settings.ollama_base_url,
        temperature=settings.llm_temperature,
        timeout_seconds=settings.local_llm_timeout_seconds,
        num_ctx=settings.local_llm_num_ctx,
    )


def create_router(settings: AppSettings, mode: str | None = None) -> ModelRouter:
    """Local (Ollama) and cloud (Gemini) behind the P3 routing rules.

    Every production path must use this, never a bare cloud generator: AskService treats a
    bare generator as a trusted local provider.
    """
    try:
        cloud: GeminiAnswerGenerator | None = create_generator(settings)
    except LLMUnavailable:
        cloud = None
    local = create_local_generator(settings)
    if local is None and cloud is None:
        raise LLMUnavailable()
    router = ModelRouter(local, cloud, parse_mode(mode or settings.llm_routing_mode))
    logger.info("LLM router mode={} providers={}", router.mode, router.providers)
    return router


@asynccontextmanager
async def open_ask_service(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    device: str = "auto",
    settings: AppSettings | None = None,
) -> AsyncIterator[AskService]:
    """Fail fast when no LLM is configured, before the slow search startup."""
    active = settings or get_settings()
    generator = create_router(active)
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
