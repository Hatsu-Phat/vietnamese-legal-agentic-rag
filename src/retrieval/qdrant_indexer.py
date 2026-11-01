"""Qdrant Dense Vector Indexer for Vietnamese Legal Documents using BGE-M3.

Supports Local On-Disk mode (without Docker) and Server mode.
Implements batch encoding, checkpointing/resume mechanism, and metadata payload indexing.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    Range,
)
from tqdm import tqdm

from src.common.atomic import atomic_open
from src.common.config import load_data_paths, resolve_project_path
from src.common.logger import logger
from src.core.models import LegalChunk
from src.retrieval.embedding import BGEEncoder
from src.retrieval.embedding import resolve_device as resolve_device
from src.retrieval.qdrant_store import QdrantStore, payload_fingerprint


class QdrantIndexer:
    """Chỉ mục vector ngữ nghĩa sử dụng Qdrant và mô hình nhúng BGE-M3."""

    def __init__(
        self,
        mode: str = "local",
        local_path: str | Path | None = None,
        url: str = "http://localhost:6333",
        collection_name: str = "vn_legal_bge_m3",
        embedding_model_id: str = "BAAI/bge-m3",
        vector_size: int = 1024,
        distance: str = "Cosine",
        normalize_embeddings: bool = True,
        batch_size: int = 32,
        max_seq_length: int = 512,
        on_disk_payload: bool = True,
        manifest_path: str | Path | None = None,
        device: str = "auto",
        client: QdrantClient | None = None,
        embedding_model: Any | None = None,
        api_key: str | None = None,
        timeout: int = 15,
        embedding_revision: str | None = None,
    ) -> None:
        self.mode = mode
        paths = load_data_paths()
        self.local_path = (
            ":memory:"
            if str(local_path) == ":memory:"
            else str(
                resolve_project_path(local_path if local_path is not None else paths.qdrant_dir)
            )
        )
        self.url = url
        self.collection_name = collection_name
        self.embedding_model_id = embedding_model_id
        self.vector_size = vector_size
        if distance.lower() not in {"cosine", "dot"}:
            raise ValueError("Distance must be Cosine or Dot")
        if batch_size <= 0 or vector_size <= 0:
            raise ValueError("batch_size and vector_size must be positive")
        self.distance = Distance.COSINE if distance.lower() == "cosine" else Distance.DOT
        self.normalize_embeddings = normalize_embeddings
        self.batch_size = batch_size
        self.max_seq_length = max_seq_length
        self.on_disk_payload = on_disk_payload
        self.manifest_path = resolve_project_path(
            manifest_path if manifest_path is not None else paths.index_manifest
        )
        self.device = device
        self.encoder = BGEEncoder(embedding_model_id, device, max_seq_length, embedding_revision)

        # Quản lý QdrantClient
        if client is not None:
            self.client = client
        elif self.mode == "memory" or self.local_path == ":memory:":
            self.client = QdrantClient(":memory:")
        elif self.mode == "server":
            logger.info(f"Kết nối tới Qdrant Server tại {self.url}")
            self.client = QdrantClient(url=self.url, api_key=api_key, timeout=timeout)
        else:
            # Chế độ local lưu trữ trực tiếp trên đĩa
            Path(self.local_path).mkdir(parents=True, exist_ok=True)
            logger.info(f"Khởi tạo Qdrant Local On-Disk tại '{self.local_path}'")
            self.client = QdrantClient(path=self.local_path)

        # Quản lý embedding model (lazy load nếu chưa truyền vào)
        self._model = embedding_model
        self.store = QdrantStore(
            self.client, collection_name, vector_size, self.distance, on_disk_payload
        )

    @property
    def model(self) -> Any:
        """Lazy load SentenceTransformer để tối ưu thời gian khởi động khi chưa cần encode."""
        if self._model is None:
            logger.info(
                f"Đang tải Embedding Model '{self.embedding_model_id}' trên thiết bị '{self.device}'..."
            )
            self._model = self.encoder.model
            self.device = str(getattr(self._model, "device", self.device))
            logger.info(
                f"Tải thành công model '{self.embedding_model_id}' (max_seq_len={self.max_seq_length})"
            )
        return self._model

    def init_collection(self, recreate: bool = False) -> None:
        """Khởi tạo Collection và thiết lập các trường Payload Index."""
        if recreate and self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)
        self.store.ensure_collection()

    def get_existing_point_uuids(self) -> set[str]:
        """Thu thập danh sách point_uuid đã có trong collection để phục vụ resume."""
        if not self.client.collection_exists(self.collection_name):
            return set()

        existing_ids: set[str] = set()
        offset = None
        while True:
            records, next_offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=10000,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            for r in records:
                existing_ids.add(str(r.id))
            if next_offset is None or not records:
                break
            offset = next_offset

        return existing_ids

    def index_chunks(
        self,
        chunks: Sequence[LegalChunk],
        resume: bool = False,
    ) -> int:
        """Mã hóa và nạp danh sách LegalChunk vào Qdrant theo từng batch.

        Args:
            chunks: Danh sách các LegalChunk cần lập chỉ mục.
            resume: Nếu True, bỏ qua các chunk đã có trong Qdrant.

        Returns:
            Tổng số lượng point đã nạp mới vào Qdrant.
        """
        if len({c.point_uuid for c in chunks}) != len(chunks):
            raise ValueError("Duplicate chunk UUIDs: normalize the source before indexing")
        self.init_collection(recreate=False)

        fingerprints = self.store.existing_fingerprints() if resume else {}
        pending_chunks = [
            c
            for c in chunks
            if fingerprints.get(c.point_uuid) != payload_fingerprint(c.model_dump(mode="json"))
        ]
        total_pending = len(pending_chunks)

        if total_pending == 0:
            logger.info("Không có chunk mới nào cần nạp vào Qdrant.")
            self.write_manifest(total_vectors=self.store.count())
            return 0

        logger.info(
            f"Bắt đầu nạp {total_pending:,} chunks vào Qdrant theo batch {self.batch_size}..."
        )

        total_uploaded = 0
        pbar = tqdm(total=total_pending, desc="Qdrant Indexing", unit="chunk")

        for i in range(0, total_pending, self.batch_size):
            batch = pending_chunks[i : i + self.batch_size]
            texts = [c.embedding_text for c in batch]

            # Mã hóa vector với SentenceTransformer
            vectors = self.model.encode(
                texts,
                batch_size=len(texts),
                show_progress_bar=False,
                normalize_embeddings=self.normalize_embeddings,
                convert_to_numpy=True,
            )

            points: list[PointStruct] = []
            for chunk, vec in zip(batch, vectors, strict=True):
                payload = chunk.model_dump(mode="json")
                points.append(
                    PointStruct(
                        id=chunk.point_uuid,
                        vector=vec.tolist(),
                        payload=payload,
                    )
                )

            self.store.upload(points)
            total_uploaded += len(points)
            pbar.update(len(points))

        pbar.close()
        logger.info(f"Nạp thành công {total_uploaded:,} chunks mới vào Qdrant!")

        # Cập nhật manifest
        total_vectors = self.store.count()
        self.write_manifest(total_vectors=total_vectors)

        return total_uploaded

    def write_manifest(self, total_vectors: int) -> None:
        """Ghi thông tin phiên bản model, số lượng vector và cấu hình vào index_manifest.json."""
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_data: dict[str, Any] = {
            "created_at": datetime.now(UTC).isoformat(),
            "collection_name": self.collection_name,
            "embedding_model_id": self.embedding_model_id,
            "embedding_revision": self.encoder.revision,
            "vector_size": self.vector_size,
            "distance": "Cosine" if self.distance == Distance.COSINE else "Dot",
            "total_vectors": total_vectors,
            "mode": self.mode,
            "local_path": self.local_path,
            "device": self.device,
            "normalize_embeddings": self.normalize_embeddings,
            "max_seq_length": self.max_seq_length,
            "on_disk_payload": self.on_disk_payload,
        }
        with atomic_open(self.manifest_path) as f:
            json.dump(manifest_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Đã lưu index manifest tại: {self.manifest_path}")

    def query(
        self,
        query_text: str | None = None,
        query_vector: list[float] | None = None,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Truy vấn tìm kiếm vector trong Qdrant kèm lọc metadata.

        Args:
            query_text: Nội dung câu hỏi (sẽ được tự động encode nếu query_vector=None).
            query_vector: Vector nhúng của truy vấn nếu đã có sẵn.
            top_k: Số lượng kết quả cần lấy.
            filters: Dict chứa điều kiện lọc (ví dụ: {'issued_year': 2020, 'document_type': 'Luật'}).

        Returns:
            Danh sách các chunk tương đồng nhất kèm score và payload.
        """
        if query_vector is None:
            if query_text is None:
                raise ValueError("Cần cung cấp query_text hoặc query_vector để tìm kiếm.")
            vec = self.model.encode(
                query_text,
                normalize_embeddings=self.normalize_embeddings,
                convert_to_numpy=True,
            )
            if hasattr(vec, "squeeze"):
                vec = vec.squeeze()
            if hasattr(vec, "tolist"):
                query_vector = vec.tolist()
            elif isinstance(vec, list) and len(vec) > 0 and isinstance(vec[0], list):
                query_vector = vec[0]
            else:
                query_vector = list(vec)
        elif hasattr(query_vector, "squeeze"):
            query_vector = query_vector.squeeze().tolist()

        qdrant_filter: Filter | None = None
        if filters:
            must_conditions: list[Any] = []
            for key, val in filters.items():
                if isinstance(val, (int, str, bool)):
                    must_conditions.append(FieldCondition(key=key, match=MatchValue(value=val)))
                elif isinstance(val, dict) and ("gte" in val or "lte" in val):
                    must_conditions.append(
                        FieldCondition(
                            key=key,
                            range=Range(gte=val.get("gte"), lte=val.get("lte")),
                        )
                    )
            if must_conditions:
                qdrant_filter = Filter(must=must_conditions)

        search_result = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            query_filter=qdrant_filter,
            limit=top_k,
        )

        results: list[dict[str, Any]] = []
        for scored_point in search_result.points:
            item = dict(scored_point.payload or {})
            item["score"] = round(float(scored_point.score), 4)
            item["point_uuid"] = str(scored_point.id)
            results.append(item)

        return results
