"""Production entrypoint: `uvicorn src.api.main:app` (single worker; models load once)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger

from src.agents.factory import create_router
from src.agents.graph import AskService
from src.agents.router import ModelRouter
from src.api.app import create_app
from src.api.dependencies import AccessResolver, ApiServices
from src.common.agent_errors import LLMUnavailable
from src.common.config import DEFAULT_CONFIG_PATH, get_settings, load_yaml_config
from src.core.access import AccessPolicy
from src.retrieval.search_catalog import CatalogSources
from src.retrieval.search_factory import open_search_service


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
            ask = (
                AskService(
                    search,
                    generator,
                    settings.max_retrieval_rounds,
                    verify_claims=settings.strict_citation_verification,
                )
                if generator is not None
                else None
            )
            yield ApiServices(
                search=search, sources=CatalogSources(search.catalog), ask=ask, access=access
            )
    finally:
        if generator is not None:
            await generator.aclose()


app = create_app(open_services)
