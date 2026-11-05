"""Pulling a verified Cloud release into local search artifacts."""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from src.common.config import load_data_paths, load_yaml_config
from src.core.search_release import SearchRuntime
from src.ingestion.indexing import cloud_release
from src.ingestion.indexing.cloud_release import CloudReleasePull
from src.retrieval.bm25_indexer import BM25Indexer
from src.retrieval.search_release_check import verify_local_release
from tests.search_support import write_catalog

FINGERPRINT = "b" * 64
VECTOR = [1.0, 0.0, 0.0, 0.0]


def cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata: dict[str, Any] | None = None
) -> tuple[QdrantClient, dict[str, Any]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    catalog = write_catalog(tmp_path / "source.jsonl")
    config = load_yaml_config()
    for key in ("processed_dir", "manifests_dir", "raw_dir", "indexes_dir"):
        config["paths"][key] = str(tmp_path / "out")
    config["indexing"]["bm25"]["segment_vietnamese"] = False
    config["indexing"]["qdrant"].update({"vector_size": 4, "collection_name": "demo"})
    client = QdrantClient(":memory:")
    client.create_collection(
        "demo_v2", vectors_config=VectorParams(size=4, distance=Distance.COSINE)
    )
    client.upsert(
        "demo_v2",
        [
            PointStruct(id=uid, vector=VECTOR, payload=c.model_dump(mode="json"))
            for uid, c in catalog.chunks.items()
        ],
    )
    monkeypatch.setattr(
        client,
        "get_aliases",
        lambda: type(
            "A",
            (),
            {"aliases": [type("X", (), {"alias_name": "demo", "collection_name": "demo_v2"})]},
        ),
    )
    meta = metadata or {
        "source_fingerprint": FINGERPRINT,
        "embedding": {
            "model_id": "BAAI/bge-m3",
            "vector_size": 4,
            "normalize": True,
            "max_seq_length": 512,
            "distance": "Cosine",
        },
    }
    original = client.get_collection

    def get_collection(collection: str, **kwargs: Any) -> Any:
        info = original(collection, **kwargs)
        cfg = info.config.model_copy(update={"metadata": meta})
        return info.model_copy(update={"config": cfg})

    monkeypatch.setattr(client, "get_collection", get_collection)
    return client, config


class FakeEncoder:
    def __init__(self, model_id: str, device: str, max_seq_length: int, revision: str) -> None:
        self.revision = revision
        self.model = self

    def encode(self, texts: list[str], **kwargs: Any) -> Any:
        return np.array([VECTOR for _ in texts])


def test_pull_writes_verifiable_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, config = cloud(tmp_path, monkeypatch)
    paths = load_data_paths(config)
    qcfg = config["indexing"]["qdrant"]
    release = CloudReleasePull(client, paths, qcfg)
    assert release.collection == "demo_v2"
    chunks = release.pull_chunks()
    lines = paths.chunks.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(chunks) == 24
    assert [json.loads(line)["point_uuid"] for line in lines] == sorted(
        c.point_uuid for c in chunks
    )
    release.write_manifest(len(chunks))
    manifest = json.loads(paths.index_manifest.read_text(encoding="utf-8"))
    assert manifest["source_fingerprint"] == FINGERPRINT
    assert manifest["collection_name"] == "demo_v2" and manifest["query_alias"] == "demo"
    assert "query_embedding_revision" not in manifest

    import src.retrieval.embedding as embedding

    monkeypatch.setattr(embedding, "BGEEncoder", FakeEncoder)
    release.write_manifest(len(chunks), release.model_check("cpu"))
    paths.stopwords.parent.mkdir(parents=True, exist_ok=True)
    paths.stopwords.write_text("và\n", encoding="utf-8")
    indexer = BM25Indexer(
        index_path=paths.bm25_index,
        corpus_meta_path=paths.bm25_metadata,
        stopwords_path=paths.stopwords,
        segment_vietnamese=False,
        chunks_path=paths.chunks,
    )
    indexer.build_index(chunks)
    indexer.save()
    catalog, _, _, dense = verify_local_release(
        paths, qcfg, config["indexing"]["bm25"], SearchRuntime(vector_size=4, timeout_seconds=5)
    )
    assert dense["query_embedding_revision"] == qcfg["embedding_revision"]
    assert len(catalog.chunks) == 24


def test_unverified_or_incompatible_cloud_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, config = cloud(tmp_path, monkeypatch, {"embedding": {}})
    with pytest.raises(ValueError, match="source_fingerprint"):
        CloudReleasePull(client, load_data_paths(config), config["indexing"]["qdrant"])
    client, config = cloud(
        tmp_path / "b", monkeypatch, {"source_fingerprint": FINGERPRINT, "embedding": {}}
    )
    with pytest.raises(ValueError, match="embedding contract"):
        CloudReleasePull(client, load_data_paths(config), config["indexing"]["qdrant"])


def test_model_check_rejects_divergent_vectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, config = cloud(tmp_path, monkeypatch)
    release = CloudReleasePull(client, load_data_paths(config), config["indexing"]["qdrant"])

    class Divergent(FakeEncoder):
        def encode(self, texts: list[str], **kwargs: Any) -> Any:
            return np.array([[0.0, 1.0, 0.0, 0.0] for _ in texts])

    import src.retrieval.embedding as embedding

    monkeypatch.setattr(embedding, "BGEEncoder", Divergent)
    with pytest.raises(ValueError, match="incompatible"):
        release.model_check("cpu")
    assert cloud_release.MIN_COMPATIBILITY_COSINE == 0.999
