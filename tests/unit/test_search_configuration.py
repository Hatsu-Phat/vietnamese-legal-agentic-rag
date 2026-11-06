"""Configuration errors fail before allocating model/database resources."""

from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from src.common.config import AppSettings, load_yaml_config
from src.common.search_errors import SearchTimeout, SearchUnavailable
from src.core.search import SearchFilters
from src.core.search_config import SearchConfiguration
from src.retrieval.search_adapters import QdrantDenseSearch


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("qdrant", "embedding_revision", ""),
        ("qdrant", "max_seq_length", 1024),
        ("qdrant", "distance", "Dot"),
        ("bm25", "b", 2),
        ("dense", "top_k", 0),
        ("dense", "model_name", "another-model"),
    ],
)
def test_invalid_configuration(section: str, key: str, value: Any) -> None:
    config = load_yaml_config()
    node = (
        config["indexing"][section]
        if section in ("qdrant", "bm25")
        else config["retrieval"][section]
    )
    node[key] = value
    with pytest.raises(ValidationError):
        SearchConfiguration.model_validate(config)


def test_config_and_legacy_imports() -> None:
    assert SearchConfiguration.model_validate(load_yaml_config()).retrieval.fusion.rrf_k == 60
    from src.ingestion.indexing.index_migration import TeamIndexMigration as new
    from src.retrieval.index_migration import TeamIndexMigration as old

    assert old is new
    settings = AppSettings(
        _env_file=None, GEMINI_API_KEY="example-hidden-secret", LLM_API_KEY="example-hidden-secret"
    )
    assert "example-hidden-secret" not in repr(settings)
    assert "example-hidden-secret" not in settings.model_dump_json()


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            UnexpectedResponse(
                status_code=503, reason_phrase="test", content=b"private", headers={}
            ),
            SearchUnavailable,
        ),
        (
            UnexpectedResponse(
                status_code=504, reason_phrase="test", content=b"private", headers={}
            ),
            SearchTimeout,
        ),
        (ResponseHandlingException(TimeoutError("private")), SearchTimeout),
    ],
)
def test_sdk_errors_are_translated_by_adapter(
    failure: Exception, expected: type[Exception]
) -> None:
    import time

    client = Mock()
    client.query_points.side_effect = failure
    adapter = QdrantDenseSearch(client, "test", Mock(chunks={}))
    with pytest.raises(expected) as error:
        adapter.retrieve([1.0], SearchFilters(), 10, time.monotonic() + 10)
    assert "private" not in str(error.value)
