"""Check cloud credentials and config precedence without connecting to a server."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import run_phase2_indexing
from src.common.config import AppSettings
from src.retrieval import qdrant_indexer


def test_qdrant_settings_read_and_redact_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cloud credentials come from env and stay masked when serialized."""
    secret = "fake-key-for-unit-tests"
    monkeypatch.setenv("QDRANT_MODE", "server")
    monkeypatch.setenv("QDRANT_URL", "https://cluster.example.invalid:6333")
    monkeypatch.setenv("QDRANT_API_KEY", secret)
    config = AppSettings(_env_file=None)
    assert config.qdrant_mode == "server"
    assert config.qdrant_url == "https://cluster.example.invalid:6333"
    assert config.qdrant_api_key is not None
    assert config.qdrant_api_key.get_secret_value() == secret
    assert secret not in config.model_dump_json(include={"qdrant_api_key"})
    assert secret not in repr(config.qdrant_api_key)


def test_server_client_receives_auth_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass the key to the SDK without storing it on the indexer or in a manifest."""
    factory = Mock()
    monkeypatch.setattr(qdrant_indexer, "QdrantClient", factory)
    manifest = tmp_path / "index_manifest.json"
    indexer = qdrant_indexer.QdrantIndexer(
        mode="server",
        url="https://cluster.example.invalid:6333",
        api_key="fake-key-for-unit-tests",
        timeout=19,
        manifest_path=manifest,
        device="cpu",
    )
    factory.assert_called_once_with(
        url="https://cluster.example.invalid:6333",
        api_key="fake-key-for-unit-tests",
        timeout=19,
    )
    assert indexer.client is factory.return_value
    indexer.write_manifest(total_vectors=0)
    assert "fake-key-for-unit-tests" not in manifest.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("cli_mode", "env_mode", "env_url", "expected_mode"),
    [
        (None, None, None, "local"),
        (None, "server", "https://cluster.example.invalid:6333", "server"),
        ("memory", "server", "https://cluster.example.invalid:6333", "memory"),
        ("server", "local", None, "server"),
    ],
)
def test_phase2_cloud_config_precedence(
    cli_mode: str | None,
    env_mode: str | None,
    env_url: str | None,
    expected_mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI overrides env, env overrides YAML, and auth reaches the indexing workflow."""
    config = AppSettings(
        _env_file=None,
        QDRANT_MODE=env_mode,
        QDRANT_URL=env_url,
        QDRANT_API_KEY="fake-key-for-unit-tests",
        TOOL_TIMEOUT_SECONDS=19,
    )
    monkeypatch.setattr(run_phase2_indexing, "settings", config)
    factory = Mock()
    factory.return_value.index_chunks.return_value = 0
    factory.return_value.collection_name = "test_collection"
    monkeypatch.setattr(run_phase2_indexing, "QdrantIndexer", factory)
    run_phase2_indexing.run_qdrant_step(
        {"indexing": {"qdrant": {"mode": "local", "url": "http://localhost:6333"}}},
        chunks=[],
        qdrant_mode=cli_mode,
        device="cpu",
    )
    kwargs = factory.call_args.kwargs
    assert kwargs["mode"] == expected_mode
    assert kwargs["url"] == (env_url or "http://localhost:6333")
    assert kwargs["api_key"] == "fake-key-for-unit-tests"
    assert kwargs["timeout"] == 19
