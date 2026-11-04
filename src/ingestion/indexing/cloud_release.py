"""Rebuild local search artifacts from the verified Qdrant Cloud release (read-only on Cloud)."""

import json
import math
from typing import Any, cast

from qdrant_client import QdrantClient

from src.common.atomic import atomic_open
from src.common.fingerprints import file_sha256, payload_fingerprint
from src.common.logger import logger
from src.core.models import LegalChunk
from src.core.paths import DataPaths

SCROLL_PAGE = 512
MODEL_SAMPLE_SIZE = 8
MIN_COMPATIBILITY_COSINE = 0.999


class CloudReleasePull:
    """Export canonical chunks and the dense manifest from one pinned Cloud collection."""

    def __init__(self, client: QdrantClient, paths: DataPaths, qdrant_cfg: dict[str, Any]) -> None:
        self.client = client
        self.paths = paths
        self.cfg = qdrant_cfg
        self.alias = str(qdrant_cfg["collection_name"])
        aliases = {a.alias_name: a.collection_name for a in client.get_aliases().aliases}
        self.collection = aliases.get(self.alias, self.alias)
        metadata = client.get_collection(self.collection).config.metadata or {}
        self.source_fingerprint = str(metadata.get("source_fingerprint") or "")
        if not self.source_fingerprint:
            raise ValueError("Cloud collection has no source_fingerprint; release is not verified")
        embedding = metadata.get("embedding", {})
        expected = {
            "model_id": qdrant_cfg["embedding_model_id"],
            "vector_size": qdrant_cfg["vector_size"],
            "normalize": True,
            "max_seq_length": qdrant_cfg["max_seq_length"],
            "distance": qdrant_cfg["distance"],
        }
        if not isinstance(embedding, dict) or any(
            embedding.get(k) != v for k, v in expected.items()
        ):
            raise ValueError("Cloud embedding contract differs from configs/dev.yaml")

    def pull_chunks(self) -> list[LegalChunk]:
        """Stream every payload, validate it and write chunks sorted by UUID (same bytes for all)."""
        expected_count = self.client.count(self.collection, exact=True).count
        payloads: dict[str, dict[str, Any]] = {}
        offset: Any = None
        while True:
            points, offset = self.client.scroll(
                self.collection,
                limit=SCROLL_PAGE,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                uid = str(point.id)
                if uid in payloads:
                    raise ValueError("Cloud returned a repeated point ID")
                payloads[uid] = point.payload or {}
            logger.info(f"Pulled {len(payloads):,}/{expected_count:,} payloads")
            if offset is None:
                break
        if len(payloads) != expected_count:
            raise ValueError("Pulled payload count differs from Cloud point count")
        chunks: list[LegalChunk] = []
        with atomic_open(self.paths.chunks) as stream:
            for uid in sorted(payloads):
                payload = payloads[uid]
                chunk = LegalChunk.model_validate(payload)
                if uid != chunk.point_uuid or uid != LegalChunk.generate_point_uuid(chunk.chunk_id):
                    raise ValueError(f"Point ID does not match chunk UUID: {chunk.chunk_id}")
                if payload_fingerprint(chunk.model_dump(mode="json")) != payload_fingerprint(
                    payload
                ):
                    raise ValueError(f"Payload is not a lossless LegalChunk: {chunk.chunk_id}")
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
                chunks.append(chunk)
        logger.info(f"Wrote {len(chunks):,} canonical chunks to {self.paths.chunks}")
        return chunks

    def model_check(self, device: str = "auto") -> str:
        """Re-encode stored inputs with the pinned revision; vectors must match Cloud."""
        from src.retrieval.embedding import BGEEncoder

        encoder = BGEEncoder(
            self.cfg["embedding_model_id"],
            device,
            self.cfg["max_seq_length"],
            revision=self.cfg["embedding_revision"],
        )
        points, _ = self.client.scroll(
            self.collection, limit=MODEL_SAMPLE_SIZE, with_payload=True, with_vectors=True
        )
        vectors = encoder.model.encode(
            [(p.payload or {})["embedding_text"] for p in points],
            batch_size=1,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        for point, vector in zip(points, vectors, strict=True):
            if not isinstance(point.vector, list):
                raise ValueError("Unexpected vector representation")
            stored = cast(list[float], point.vector)
            cosine = sum(float(a) * float(b) for a, b in zip(stored, vector, strict=True)) / (
                math.sqrt(sum(float(a) ** 2 for a in stored) * sum(float(b) ** 2 for b in vector))
            )
            if cosine < MIN_COMPATIBILITY_COSINE:
                raise ValueError(f"Encoder incompatible with Cloud vectors (cosine {cosine:.5f})")
        if encoder.revision != self.cfg["embedding_revision"]:
            raise ValueError("Resolved model revision differs from configs/dev.yaml")
        logger.info(f"Encoder revision {encoder.revision} matches {len(points)} Cloud vectors")
        return str(encoder.revision)

    def write_manifest(self, total_vectors: int, query_revision: str | None = None) -> None:
        """Record the pinned release; the query revision is set only after model_check."""
        manifest: dict[str, Any] = {}
        if self.paths.index_manifest.is_file():
            manifest = json.loads(self.paths.index_manifest.read_text(encoding="utf-8"))
            if manifest.get("source_fingerprint") != self.source_fingerprint:
                manifest = {}
        manifest.update(
            {
                "mode": "server",
                "collection_name": self.collection,
                "query_alias": self.alias,
                "total_vectors": total_vectors,
                "embedding_model_id": self.cfg["embedding_model_id"],
                "vector_size": self.cfg["vector_size"],
                "distance": self.cfg["distance"],
                "normalize_embeddings": True,
                "max_seq_length": self.cfg["max_seq_length"],
                "source_fingerprint": self.source_fingerprint,
                "canonical_chunks_sha256": file_sha256(self.paths.chunks),
                "provenance": "pulled_from_cloud",
            }
        )
        if query_revision is not None:
            manifest["query_embedding_revision"] = query_revision
        with atomic_open(self.paths.index_manifest) as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
