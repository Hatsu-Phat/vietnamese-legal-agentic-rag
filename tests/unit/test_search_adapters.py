"""Search contracts, ranking, real adapters and bounded asynchronous execution."""

import time
from pathlib import Path

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from src.common.search_errors import SearchUnavailable
from src.core.search import (
    SearchFilters,
)
from src.retrieval.search_adapters import BM25SparseSearch, QdrantDenseSearch
from src.retrieval.search_catalog import ChunkCatalog
from tests.search_support import (
    catalog as catalog,
)
from tests.search_support import (
    real_bm25,
)


def test_sparse_filters_before_topk_and_manifest_tamper(
    tmp_path: Path, catalog: ChunkCatalog
) -> None:
    indexer = real_bm25(tmp_path, catalog)
    sparse = BM25SparseSearch(indexer, catalog)
    rows = sparse.retrieve("môi trường", SearchFilters(issued_year=2021), 1)
    assert len(rows) == 1 and str(rows[0].point_uuid) == list(catalog.chunks)[-1]
    assert sparse.retrieve("", SearchFilters(), 10) == []
    indexer.index_path.write_bytes(indexer.index_path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed"):
        indexer.load()


def test_real_dense_filters_and_payload_validation(catalog: ChunkCatalog) -> None:
    client = QdrantClient(":memory:")
    client.create_collection("demo", vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    client.upsert(
        "demo",
        [
            PointStruct(id=uid, vector=[1.0, 0.0, 0.0, 0.0], payload=c.model_dump(mode="json"))
            for uid, c in catalog.chunks.items()
        ],
    )
    dense = QdrantDenseSearch(client, "demo", catalog)
    rows = dense.retrieve(
        [1.0, 0.0, 0.0, 0.0], SearchFilters(issued_year=2021), 5, time.monotonic() + 10
    )
    assert len(rows) == 1
    client.set_payload("demo", {"title": "changed"}, points=[str(rows[0].point_uuid)])
    with pytest.raises(SearchUnavailable):
        dense.retrieve(
            [1.0, 0.0, 0.0, 0.0], SearchFilters(issued_year=2021), 5, time.monotonic() + 10
        )
    dense.close()
