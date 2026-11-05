"""Production entrypoint: `uvicorn src.api.main:app` (single worker; models load once)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger

from src.agents.factory import create_generator
from src.agents.gemini import GeminiAnswerGenerator
from src.agents.graph import AskService
from src.api.app import create_app
from src.api.dependencies import ApiServices
from src.common.agent_errors import LLMUnavailable
from src.common.config import DEFAULT_CONFIG_PATH, get_settings
from src.retrieval.search_catalog import CatalogSources
from src.retrieval.search_factory import open_search_service


@asynccontextmanager
async def open_services() -> AsyncIterator[ApiServices]:
    """/search and /sources work without an LLM key; /ask then answers 503 LLM_UNAVAILABLE."""
    settings = get_settings()
    generator: GeminiAnswerGenerator | None
    try:
        generator = create_generator(settings)
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
            yield ApiServices(search=search, sources=CatalogSources(search.catalog), ask=ask)
    finally:
        if generator is not None:
            await generator.aclose()


app = create_app(open_services)
