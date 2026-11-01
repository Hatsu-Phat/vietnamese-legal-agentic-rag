"""Search contracts, ranking, real adapters and bounded asynchronous execution."""

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from src.common.config import load_yaml_config
from src.core.models import LegalChunk
from src.core.search import (
    RetrievalCandidate,
    SearchFilters,
)
from src.core.search_release import SearchRuntime
from src.retrieval.bm25_indexer import BM25Indexer
from src.retrieval.search_catalog import ChunkCatalog, matches
from src.retrieval.search_service import SearchService


def write_catalog(path: Path, count: int = 24) -> ChunkCatalog:
    chunks = []
    for i in range(count):
        cid = f"law_demo__art_{i + 1}__c0"
        text = f"bảo vệ môi trường trách nhiệm pháp luật điều {i + 1}"
        chunks.append(
            LegalChunk(
                chunk_id=cid,
                point_uuid=LegalChunk.generate_point_uuid(cid),
                document_id="law_demo",
                document_number="DEMO",
                provision_id=f"law_demo__art_{i + 1}",
                article_id=str(i + 1),
                chunk_index=0,
                total_chunks_in_provision=1,
                is_full_article=True,
                breadcrumb=f"DEMO > Điều {i + 1}",
                title=f"Điều {i + 1}",
                text=text,
                embedding_text=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                char_length=len(text),
                word_count=len(text.split()),
                document_type="Luật",
                issued_year=2021 if i == count - 1 else 2020,
            )
        )
    path.write_text("\n".join(c.model_dump_json() for c in chunks) + "\n")
    return ChunkCatalog(path)


class Encoder:
    tokens = 5
    calls = 0

    def token_count(self, query: str) -> int:
        return self.tokens

    def encode_query(self, query: str) -> list[float]:
        self.calls += 1
        return [1.0, 0.0, 0.0, 0.0]


class Dense:
    def __init__(self, catalog: ChunkCatalog) -> None:
        self.catalog = catalog
        self.limit = 0
        self.delay = 0.0
        self.failure: Exception | None = None
        self.closed = False
        self.calls = 0
        self.maximum_active = 0
        self.active = 0
        self.fixed: list[RetrievalCandidate] | None = None

    def retrieve(
        self, vector: list[float], filters: SearchFilters, limit: int, deadline: float
    ) -> list[RetrievalCandidate]:
        self.limit = limit
        self.calls += 1
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            time.sleep(self.delay)
            if self.failure:
                raise self.failure
            if self.fixed is not None:
                return self.fixed
            return [
                RetrievalCandidate(point_uuid=UUID(uid), score=1 - i * 0.001)
                for i, (uid, c) in enumerate(self.catalog.chunks.items())
                if matches(c, filters)
            ][:limit]
        finally:
            self.active -= 1

    def close(self) -> None:
        assert self.active == 0
        self.closed = True


class Sparse:
    def __init__(self, dense: Dense) -> None:
        self.dense = dense
        self.empty = False
        self.failure: Exception | None = None

    def retrieve(self, query: str, filters: SearchFilters, limit: int) -> list[RetrievalCandidate]:
        if self.failure:
            raise self.failure
        if self.empty:
            return []
        if self.dense.fixed is not None:
            return self.dense.fixed
        return [
            RetrievalCandidate(point_uuid=UUID(uid), score=10 - i * 0.01)
            for i, (uid, c) in enumerate(self.dense.catalog.chunks.items())
            if matches(c, filters)
        ][:limit]


@pytest.fixture
def catalog(tmp_path: Path) -> ChunkCatalog:
    return write_catalog(tmp_path / "chunks.jsonl")


def service_for(catalog: ChunkCatalog, timeout: float = 30) -> SearchService:
    dense = Dense(catalog)
    return SearchService(
        catalog,
        Encoder(),
        dense,
        Sparse(dense),
        SearchRuntime(vector_size=4, timeout_seconds=timeout),
    )


def real_bm25(tmp_path: Path, catalog: ChunkCatalog) -> BM25Indexer:
    stop = tmp_path / "stopwords.txt"
    stop.write_text("và\n")
    indexer = BM25Indexer(
        index_path=tmp_path / "bm25.pkl",
        corpus_meta_path=tmp_path / "bm25.json",
        stopwords_path=stop,
        segment_vietnamese=False,
        chunks_path=tmp_path / "chunks.jsonl",
    )
    indexer.build_index(catalog.chunks.values())
    indexer.save()
    return indexer


def factory_fixture(
    tmp_path: Path, catalog: ChunkCatalog, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, QdrantClient]:
    from src.common.config import load_data_paths

    indexer = real_bm25(tmp_path, catalog)
    config = load_yaml_config()
    for key in ("processed_dir", "manifests_dir", "raw_dir", "indexes_dir"):
        config["paths"][key] = str(tmp_path)
    config["indexing"]["bm25"]["segment_vietnamese"] = False
    config["indexing"]["qdrant"].update({"vector_size": 4, "collection_name": "demo"})
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    paths = load_data_paths(config)
    # The factory uses standard artifact names under configured directories.
    paths.bm25_index.parent.mkdir(parents=True, exist_ok=True)
    indexer.index_path.rename(paths.bm25_index)
    indexer.corpus_meta_path.rename(paths.bm25_metadata)
    indexer.manifest_path.rename(paths.bm25_index.with_suffix(".manifest.json"))
    fingerprint = "a" * 64
    paths.index_manifest.write_text(
        json.dumps(
            {
                "query_embedding_revision": config["indexing"]["qdrant"]["embedding_revision"],
                "embedding_model_id": "BAAI/bge-m3",
                "vector_size": 4,
                "normalize_embeddings": True,
                "max_seq_length": 512,
                "total_vectors": len(catalog.chunks),
                "collection_name": "demo",
                "query_alias": "demo",
                "source_fingerprint": fingerprint,
            }
        )
    )
    client = QdrantClient(":memory:")
    client.create_collection("demo", vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    client.upsert(
        "demo",
        [
            PointStruct(id=uid, vector=[1.0, 0.0, 0.0, 0.0], payload=c.model_dump(mode="json"))
            for uid, c in catalog.chunks.items()
        ],
    )
    original = client.get_collection

    def get_collection(collection: str, **kwargs: Any) -> Any:
        info = original(collection, **kwargs)
        cfg = info.config.model_copy(
            update={
                "metadata": {
                    "source_fingerprint": fingerprint,
                    "embedding": {
                        "model_id": "BAAI/bge-m3",
                        "vector_size": 4,
                        "normalize": True,
                        "max_seq_length": 512,
                        "distance": "Cosine",
                    },
                }
            }
        )
        return info.model_copy(update={"config": cfg})

    monkeypatch.setattr(client, "get_collection", get_collection)
    return path, client


class MockModel:
    def tokenizer(self, query: str, **kwargs: Any) -> dict[str, list[int]]:
        return {"input_ids": [0, 1, 2]}

    def encode(self, query: str, **kwargs: Any) -> Any:
        import numpy as np

        return np.array([1.0, 0.0, 0.0, 0.0])
