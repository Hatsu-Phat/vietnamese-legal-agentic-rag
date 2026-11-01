"""Search contracts, ranking, real adapters and bounded asynchronous execution."""

import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
import yaml
from qdrant_client.models import Distance, VectorParams

from src.common.config import load_yaml_config
from src.common.search_errors import SearchUnavailable
from src.core.search import (
    SearchFilters,
    SearchRequest,
)
from src.retrieval.bm25_indexer import BM25Indexer
from src.retrieval.search_catalog import ChunkCatalog
from src.retrieval.search_factory import create_search_service
from tests.search_support import (
    MockModel,
    factory_fixture,
)
from tests.search_support import (
    catalog as catalog,
)


@pytest.mark.asyncio
async def test_factory_verified_startup_does_not_write_database(
    tmp_path: Path, catalog: ChunkCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.retrieval import search_factory

    config, client = factory_fixture(tmp_path, catalog, monkeypatch)
    upsert = Mock(side_effect=AssertionError("Search must not write"))
    monkeypatch.setattr(client, "upsert", upsert)
    monkeypatch.setattr(client, "create_collection", upsert)
    monkeypatch.setattr(
        search_factory,
        "BGEEncoder",
        Mock(side_effect=AssertionError("Injected model must not be loaded")),
    )
    service = create_search_service(config, client=client, model=MockModel())
    try:
        response = await service.search(
            SearchRequest(query="môi trường", filters=SearchFilters(issued_year=2021)), uuid4()
        )
        assert response.returned_count == 1 and response.results[0].issued_year == 2021
        upsert.assert_not_called()
    finally:
        await service.aclose()


def test_factory_rejects_release_changes_before_pickle(
    tmp_path: Path, catalog: ChunkCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.common.config import load_data_paths

    config, client = factory_fixture(tmp_path, catalog, monkeypatch)
    paths = load_data_paths(load_yaml_config(config))
    manifest = paths.bm25_index.with_suffix(".manifest.json")
    data = json.loads(manifest.read_text())
    data["corpus_fingerprint"] = "b" * 64
    manifest.write_text(json.dumps(data))
    load = Mock(side_effect=AssertionError("Must not unpickle an invalid corpus"))
    monkeypatch.setattr(BM25Indexer, "load", load)
    with pytest.raises(SearchUnavailable) as error:
        create_search_service(config, client=client, model=MockModel())
    load.assert_not_called()
    assert str(error.value) == "Dịch vụ tìm kiếm chưa sẵn sàng. Vui lòng thử lại sau."


def test_factory_rejects_cloud_fingerprint(
    tmp_path: Path, catalog: ChunkCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.common.config import load_data_paths

    config, client = factory_fixture(tmp_path, catalog, monkeypatch)
    paths = load_data_paths(load_yaml_config(config))
    data = json.loads(paths.index_manifest.read_text())
    data["source_fingerprint"] = "c" * 64
    paths.index_manifest.write_text(json.dumps(data))
    with pytest.raises(SearchUnavailable):
        create_search_service(config, client=client, model=MockModel())


@pytest.mark.asyncio
async def test_alias_is_pinned_to_verified_release(
    tmp_path: Path, catalog: ChunkCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, client = factory_fixture(tmp_path, catalog, monkeypatch)
    from qdrant_client.models import (
        CreateAlias,
        CreateAliasOperation,
        DeleteAlias,
        DeleteAliasOperation,
    )

    client.update_collection_aliases(
        [
            CreateAliasOperation(
                create_alias=CreateAlias(collection_name="demo", alias_name="public-alias")
            )
        ]
    )
    cfg = load_yaml_config(config)
    cfg["indexing"]["qdrant"]["collection_name"] = "public-alias"
    config.write_text(yaml.safe_dump(cfg))
    manifest = tmp_path / "index_manifest.json"
    data = json.loads(manifest.read_text())
    data["query_alias"] = "public-alias"
    manifest.write_text(json.dumps(data))
    service = create_search_service(config, client=client, model=MockModel())
    try:
        client.create_collection(
            "other", vectors_config=VectorParams(size=4, distance=Distance.COSINE)
        )
        client.update_collection_aliases(
            [
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name="public-alias")),
                CreateAliasOperation(
                    create_alias=CreateAlias(collection_name="other", alias_name="public-alias")
                ),
            ]
        )
        response = await service.search(SearchRequest(query="môi trường"), uuid4())
        assert response.results
        assert service.provenance["collection"] == "demo"
    finally:
        await service.aclose()


def test_startup_logs_stage_without_sdk_details(
    tmp_path: Path, catalog: ChunkCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.common.logger import logger

    config, client = factory_fixture(tmp_path, catalog, monkeypatch)
    monkeypatch.setattr(
        client, "get_collection", Mock(side_effect=RuntimeError("PRIVATE_SDK_SECRET"))
    )
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)))
    try:
        with pytest.raises(SearchUnavailable):
            create_search_service(config, client=client, model=MockModel())
    finally:
        logger.remove(sink)
    assert "stage=cloud_release" in "".join(messages)
    assert "PRIVATE_SDK_SECRET" not in "".join(messages)
