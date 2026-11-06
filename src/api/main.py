"""Production entrypoint: `uvicorn src.api.main:app` (single worker; models load once)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger

from src.agents.ask_cache import CachedAskService, pipeline_signature
from src.agents.factory import create_router
from src.agents.graph import AskService
from src.agents.router import ModelRouter
from src.api.app import create_app
from src.api.dependencies import AccessResolver, ApiServices
from src.common.agent_errors import LLMUnavailable
from src.common.config import DEFAULT_CONFIG_PATH, AppSettings, get_settings, load_yaml_config
from src.common.logger import configure_logging
from src.core.access import AccessPolicy
from src.core.api import AskPort
from src.retrieval.search_catalog import CatalogSources
from src.retrieval.search_factory import open_search_service


def answer_pipeline(settings: AppSettings) -> str:
    """Settings that change /ask answers; part of every cache key."""
    return pipeline_signature(
        {
            "routing": settings.llm_routing_mode,
            "cloud_model": settings.gemini_model_id,
            "local_model": settings.local_llm_model,
            "prompt": settings.prompt_variant,
            "local_prompt": settings.local_prompt_variant,
            "local_max_evidence": settings.local_max_evidence,
            "verify": settings.strict_citation_verification,
            "rounds": settings.max_retrieval_rounds,
        }
    )


@asynccontextmanager
async def open_services() -> AsyncIterator[ApiServices]:
    """/search and /sources work without any LLM; /ask then answers 503 LLM_UNAVAILABLE."""
    settings = get_settings()
    access = AccessResolver.from_settings(
        AccessPolicy.from_config(load_yaml_config(DEFAULT_CONFIG_PATH)), settings
    )
    logger.info("Access control roles_with_keys={}", access.configured_roles)
    generator: ModelRouter | None
    try:
        generator = create_router(settings)
    except LLMUnavailable:
        generator = None
        logger.warning("No LLM configured; /ask is disabled")
    try:
        async with open_search_service(DEFAULT_CONFIG_PATH, settings.embedding_device) as search:
            ask: AskPort | None = None
            if generator is not None:
                cached = CachedAskService(
                    AskService(
                        search,
                        generator,
                        settings.max_retrieval_rounds,
                        verify_claims=settings.strict_citation_verification,
                    ),
                    release_fingerprint=search.release_fingerprint,
                    pipeline=answer_pipeline(settings),
                    ttl_seconds=settings.ask_cache_ttl_seconds,
                    max_entries=settings.ask_cache_max_entries,
                )
                cached.publish_metrics()
                ask = cached
            yield ApiServices(
                search=search, sources=CatalogSources(search.catalog), ask=ask, access=access
            )
    finally:
        if generator is not None:
            await generator.aclose()


configure_logging(get_settings().log_format, get_settings().log_level)
app = create_app(open_services, metrics_enabled=get_settings().metrics_enabled)
