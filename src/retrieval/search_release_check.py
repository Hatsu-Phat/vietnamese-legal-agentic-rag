"""Read-only release verification, independent of model/client construction."""

import json
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from src.core.paths import DataPaths
from src.core.search_release import BM25Release, SearchRuntime
from src.retrieval.bm25_indexer import BM25Indexer
from src.retrieval.search_catalog import ChunkCatalog


def verify_local_release(
    paths: DataPaths, qcfg: dict[str, Any], bcfg: dict[str, Any], runtime: SearchRuntime
) -> tuple[ChunkCatalog, BM25Indexer, BM25Release, dict[str, Any]]:
    """Validate local bytes/contracts before deserializing trusted artifacts."""
    catalog = ChunkCatalog(paths.chunks)
    indexer = BM25Indexer(
        index_path=paths.bm25_index,
        corpus_meta_path=paths.bm25_metadata,
        stopwords_path=paths.stopwords,
        k1=bcfg["k1"],
        b=bcfg["b"],
        segment_vietnamese=bcfg["segment_vietnamese"],
        chunks_path=paths.chunks,
    )
    release = indexer.validate_release()
    if (
        release.canonical_chunks_sha256 != catalog.chunks_sha256
        or release.corpus_fingerprint != catalog.corpus_fingerprint
    ):
        raise ValueError("BM25 refers to another canonical corpus")
    dense_manifest = json.loads(paths.index_manifest.read_text())
    if dense_manifest.get("query_embedding_revision") != qcfg["embedding_revision"]:
        raise ValueError("Query encoder revision differs from verified dense release")
    for name, expected in {
        "embedding_model_id": qcfg["embedding_model_id"],
        "vector_size": runtime.vector_size,
        "normalize_embeddings": True,
        "max_seq_length": 512,
        "total_vectors": len(catalog.chunks),
    }.items():
        if dense_manifest.get(name) != expected:
            raise ValueError("Dense release is incompatible")
    return catalog, indexer, release, dense_manifest


def verify_cloud_release(
    active_client: QdrantClient,
    qcfg: dict[str, Any],
    dense_manifest: dict[str, Any],
    catalog: ChunkCatalog,
    runtime: SearchRuntime,
) -> str:
    """Resolve and pin a read-only collection of the expected release."""
    collection = str(qcfg["collection_name"])
    if collection not in {
        dense_manifest.get("collection_name"),
        dense_manifest.get("query_alias"),
    }:
        raise ValueError("Collection is not the released collection or alias")
    aliases = {a.alias_name: a.collection_name for a in active_client.get_aliases().aliases}
    collection = aliases.get(collection, collection)
    if collection != dense_manifest.get("collection_name"):
        raise ValueError("Alias no longer points to the local release")
    info = active_client.get_collection(collection)
    metadata = info.config.metadata or {}
    if metadata.get("source_fingerprint") != dense_manifest.get(
        "source_fingerprint"
    ) or not metadata.get("source_fingerprint"):
        raise ValueError("Cloud and local release fingerprints differ")
    embedding = metadata.get("embedding", {})
    if not isinstance(embedding, dict) or any(
        embedding.get(k) != v
        for k, v in {
            "model_id": qcfg["embedding_model_id"],
            "vector_size": runtime.vector_size,
            "normalize": True,
            "max_seq_length": 512,
            "distance": "Cosine",
        }.items()
    ):
        raise ValueError("Cloud embedding contract differs")
    params = info.config.params.vectors
    if (
        not isinstance(params, VectorParams)
        or params.size != runtime.vector_size
        or params.distance != Distance.COSINE
    ):
        raise ValueError("Cloud vector shape differs")
    if active_client.count(collection, exact=True).count != len(catalog.chunks):
        raise ValueError("Cloud point count differs from canonical release")
    return collection
