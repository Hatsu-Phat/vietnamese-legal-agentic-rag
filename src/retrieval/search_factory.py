"""Verified startup and lifespan helper for a teammate's API or a CLI."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient

from src.common.config import (
    DEFAULT_CONFIG_PATH,
    AppSettings,
    get_settings,
    load_data_paths,
    load_yaml_config,
)
from src.common.logger import logger
from src.common.search_errors import SearchUnavailable
from src.core.search_config import SearchConfiguration
from src.core.search_release import SearchRuntime
from src.retrieval.embedding import BGEEncoder
from src.retrieval.search_adapters import BM25SparseSearch, PinnedQueryEncoder, QdrantDenseSearch
from src.retrieval.search_release_check import verify_cloud_release, verify_local_release
from src.retrieval.search_service import SearchService


def create_search_service(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    device: str = "auto",
    *,
    settings: AppSettings | None = None,
    client: QdrantClient | None = None,
    model: Any = None,
) -> SearchService:
    """Blocking startup: verify releases before loading pickle/model; never create/write Cloud data."""
    active_client = client
    stage = "configuration"
    try:
        config = load_yaml_config(config_path)
        validated = SearchConfiguration.model_validate(config)
        paths = load_data_paths(config)
        qcfg = validated.indexing.qdrant.model_dump()
        bcfg = validated.indexing.bm25.model_dump()
        rcfg = validated.retrieval.model_dump()
        runtime = SearchRuntime(
            timeout_seconds=rcfg.get("timeout_seconds", 30),
            dense_candidates=rcfg["dense"]["top_k"],
            sparse_candidates=rcfg["sparse"]["top_k"],
            fusion_candidates=rcfg["fusion"]["top_candidates"],
            rrf_k=rcfg["fusion"]["rrf_k"],
            max_query_tokens=qcfg["max_seq_length"],
            vector_size=qcfg["vector_size"],
        )
        stage = "local_release"
        catalog, indexer, release, dense_manifest = verify_local_release(paths, qcfg, bcfg, runtime)
        stage = "cloud_release"
        settings = settings or get_settings()
        if active_client is None:
            if (
                (settings.qdrant_mode or qcfg["mode"]) != "server"
                or not settings.qdrant_url
                or not settings.qdrant_api_key
            ):
                raise ValueError("Search v1 factory requires configured Qdrant server credentials")
            active_client = QdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key.get_secret_value(),
                timeout=min(15, settings.tool_timeout_seconds),
            )
        collection = verify_cloud_release(active_client, qcfg, dense_manifest, catalog, runtime)
        stage = "lexical_resources"
        indexer.load()
        for row in indexer.corpus_meta:
            chunk = catalog.chunks.get(str(row.get("point_uuid")))
            if chunk is None or any(chunk.model_dump().get(k) != v for k, v in row.items()):
                raise ValueError("BM25 metadata differs from canonical corpus")
        sparse = BM25SparseSearch(indexer, catalog)
        stage = "encoder"
        if model is None:
            encoder = BGEEncoder(
                qcfg["embedding_model_id"], device, 512, qcfg["embedding_revision"]
            )
            warmed_model = encoder.model
            # Weights are memory-mapped lazily; the first forward pass can take tens of
            # seconds on CPU/HDD, so pay it at startup instead of inside a request budget.
            warmed_model.encode("khởi động", normalize_embeddings=True, show_progress_bar=False)
        else:
            warmed_model = model
        dense = QdrantDenseSearch(
            active_client, collection, catalog, min(15, settings.tool_timeout_seconds)
        )
        return SearchService(
            catalog,
            PinnedQueryEncoder(warmed_model),
            dense,
            sparse,
            runtime,
            str(dense_manifest["source_fingerprint"]),
            provenance={
                "collection": collection,
                "embedding_model_id": qcfg["embedding_model_id"],
                "embedding_revision": qcfg["embedding_revision"],
                "device": str(getattr(warmed_model, "device", device)),
                "canonical_chunks_sha256": catalog.chunks_sha256,
                "bm25_index_sha256": release.index_sha256,
                "bm25_metadata_sha256": release.metadata_sha256,
                "stopwords_sha256": release.stopwords_sha256,
                "versions": release.versions,
            },
        )
    except Exception as exc:
        logger.error(
            "Search startup failed stage={} error_type={} code=SEARCH_UNAVAILABLE",
            stage,
            type(exc).__name__,
        )
        if active_client is not None:
            with suppress(Exception):
                active_client.close()
        raise SearchUnavailable() from None


@asynccontextmanager
async def open_search_service(
    config_path: str | Path = DEFAULT_CONFIG_PATH, device: str = "auto"
) -> AsyncIterator[SearchService]:
    """Use inside FastAPI lifespan; construction/inference never block its event loop."""
    service = await asyncio.to_thread(create_search_service, config_path, device)
    try:
        yield service
    finally:
        await service.aclose()
