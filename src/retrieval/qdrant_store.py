"""Storage operations without imports of Torch, tokenizers or embedding models."""

import math
from collections.abc import Sequence
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.local.qdrant_local import QdrantLocal
from qdrant_client.models import (
    Distance,
    HnswConfigDiff,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from src.common.fingerprints import payload_fingerprint as payload_fingerprint


class QdrantStore:
    """Explicit collection validation and point upload, separate from encoding."""

    def __init__(
        self,
        client: QdrantClient,
        collection: str,
        vector_size: int = 1024,
        distance: Distance = Distance.COSINE,
        on_disk: bool = True,
    ) -> None:
        self.client = client
        self.collection = collection
        self.vector_size = vector_size
        self.distance = distance
        self.on_disk = on_disk

    def ensure_collection(self, metadata: dict[str, Any] | None = None) -> None:
        """Create or validate; existing collections are never silently replaced."""
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                self.collection,
                vectors_config=VectorParams(
                    size=self.vector_size,
                    distance=self.distance,
                    on_disk=self.on_disk,
                ),
                on_disk_payload=self.on_disk,
                hnsw_config=HnswConfigDiff(on_disk=self.on_disk),
                metadata=metadata,
            )
        config = self.client.get_collection(self.collection).config
        if (
            metadata is not None
            and not isinstance(self.client._client, QdrantLocal)
            and config.metadata != metadata
        ):
            raise ValueError(
                "Collection metadata belongs to another source or embedding configuration"
            )
        params = config.params.vectors
        if not isinstance(params, VectorParams):
            raise ValueError("Expected one unnamed dense vector")
        if params.size != self.vector_size or params.distance != self.distance:
            raise ValueError("Existing collection has incompatible vector parameters")
        fields = {
            "document_id": PayloadSchemaType.KEYWORD,
            "provision_id": PayloadSchemaType.KEYWORD,
            "article_id": PayloadSchemaType.KEYWORD,
            "document_type": PayloadSchemaType.KEYWORD,
            "issuing_authority": PayloadSchemaType.KEYWORD,
            "issued_year": PayloadSchemaType.INTEGER,
            "status": PayloadSchemaType.KEYWORD,
        }
        indexed = self.client.get_collection(self.collection).payload_schema
        for name, schema in fields.items():
            if name not in indexed:
                self.client.create_payload_index(self.collection, name, schema, wait=True)

    def upload(self, points: Sequence[PointStruct]) -> None:
        """Validate before sending; wait for the database to confirm the write."""
        for point in points:
            vector = point.vector
            if not isinstance(vector, list) or len(vector) != self.vector_size:
                raise ValueError("Vector dimension mismatch")
            if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vector):
                raise ValueError("Non-finite or invalid vector value")
        self.client.upsert(self.collection, list(points), wait=True)

    def existing_fingerprints(self) -> dict[str, str]:
        """Retrieve payload hashes for content-aware resume."""
        if not self.client.collection_exists(self.collection):
            return {}
        result: dict[str, str] = {}
        offset = None
        while True:
            rows, offset = self.client.scroll(
                self.collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for row in rows:
                result[str(row.id)] = payload_fingerprint(row.payload or {})
            if offset is None:
                break
        return result

    def count(self) -> int:
        """Use the database's exact count, not the number of attempted writes."""
        return self.client.count(self.collection, exact=True).count
