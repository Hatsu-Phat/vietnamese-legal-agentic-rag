"""Qdrant Dense Vector Indexer for Vietnamese Legal Documents using BGE-M3.

Supports Local On-Disk mode (without Docker) and Server mode.
Implements batch encoding, checkpointing/resume mechanism, and metadata payload indexing.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import torch
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Range,
    VectorParams,
)
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

from src.common.logger import logger
from src.core.models import LegalChunk


def resolve_device(device: str = "auto") -> str:
    """Tự động phát hiện phần cứng tăng tốc (CUDA / MPS / CPU)."""
    if device == "auto":
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    return device


class QdrantIndexer:
    """Chỉ mục vector ngữ nghĩa sử dụng Qdrant và mô hình nhúng BGE-M3."""

    def __init__(
        self,
        mode: str = "local",
        local_path: str = "data/indexes/qdrant",
        url: str = "http://localhost:6333",
        collection_name: str = "vn_legal_bge_m3",
        embedding_model_id: str = "BAAI/bge-m3",
        vector_size: int = 1024,
        distance: str = "Cosine",
        normalize_embeddings: bool = True,
        batch_size: int = 32,
        max_seq_length: int = 512,
        on_disk_payload: bool = True,
        manifest_path: str = "data/manifests/index_manifest.json",
        device: str = "auto",
        client: QdrantClient | None = None,
        embedding_model: Any | None = None,
    ) -> None:
        self.mode = mode
        self.local_path = local_path
        self.url = url
        self.collection_name = collection_name
        self.embedding_model_id = embedding_model_id
        self.vector_size = vector_size
        self.distance = Distance.COSINE if distance.lower() == "cosine" else Distance.DOT
        self.normalize_embeddings = normalize_embeddings
        self.batch_size = batch_size
        self.max_seq_length = max_seq_length
        self.on_disk_payload = on_disk_payload
        self.manifest_path = Path(manifest_path)
        self.device = resolve_device(device)

        # Quản lý QdrantClient
        if client is not None:
            self.client = client
        elif self.mode == "memory" or self.local_path == ":memory:":
            self.client = QdrantClient(":memory:")
        elif self.mode == "server":
            logger.info(f"Kết nối tới Qdrant Server tại {self.url}")
            self.client = QdrantClient(url=self.url)
        else:
            # Chế độ local lưu trữ trực tiếp trên đĩa
            Path(self.local_path).mkdir(parents=True, exist_ok=True)
            logger.info(f"Khởi tạo Qdrant Local On-Disk tại '{self.local_path}'")
            self.client = QdrantClient(path=self.local_path)

        # Quản lý embedding model (lazy load nếu chưa truyền vào)
        self._model = embedding_model

    @property
    def model(self) -> Any:
        """Lazy load SentenceTransformer để tối ưu thời gian khởi động khi chưa cần encode."""
        if self._model is None:
            logger.info(
                f"Đang tải Embedding Model '{self.embedding_model_id}' trên thiết bị '{self.device}'..."
            )
            model = SentenceTransformer(self.embedding_model_id, device=self.device)
            model.max_seq_length = self.max_seq_length
            self._model = model
            logger.info(
                f"Tải thành công model '{self.embedding_model_id}' (max_seq_len={self.max_seq_length})"
            )
        return self._model

    def init_collection(self, recreate: bool = False) -> None:
        """Khởi tạo Collection và thiết lập các trường Payload Index."""
        exists = self.client.collection_exists(self.collection_name)
        if exists:
            if recreate:
                logger.warning(f"Xóa và tạo lại Collection '{self.collection_name}'...")
                self.client.delete_collection(self.collection_name)
            else:
                logger.info(f"Collection '{self.collection_name}' đã tồn tại, bỏ qua tạo mới.")
                return

        logger.info(
            f"Tạo mới Collection '{self.collection_name}' (size={self.vector_size}, distance={self.distance})..."
        )
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(
                size=self.vector_size,
                distance=self.distance,
                on_disk=self.on_disk_payload,
            ),
        )

        # Tạo Payload Index phục vụ lọc tốc độ cao
        indexed_fields = [
            ("document_id", PayloadSchemaType.KEYWORD),
            ("provision_id", PayloadSchemaType.KEYWORD),
            ("article_id", PayloadSchemaType.KEYWORD),
            ("document_type", PayloadSchemaType.KEYWORD),
            ("issuing_authority", PayloadSchemaType.KEYWORD),
            ("issued_year", PayloadSchemaType.INTEGER),
            ("status", PayloadSchemaType.KEYWORD),
        ]

        for field_name, field_type in indexed_fields:
            try:
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field_name,
                    field_schema=field_type,
                )
            except (UnexpectedResponse, Exception) as exc:
                logger.debug(f"Bỏ qua tạo payload index '{field_name}' ({exc})")

        logger.info(
            f"Đã khởi tạo thành công Collection '{self.collection_name}' và Payload Indexes."
        )

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
        self.init_collection(recreate=False)

        existing_ids: set[str] = set()
        if resume:
            existing_ids = self.get_existing_point_uuids()
            logger.info(
                f"Chế độ --resume: phát hiện {len(existing_ids):,} chunks đã tồn tại trong Qdrant."
            )

        pending_chunks = (
            [c for c in chunks if c.point_uuid not in existing_ids] if resume else list(chunks)
        )
        total_pending = len(pending_chunks)

        if total_pending == 0:
            logger.info("Không có chunk mới nào cần nạp vào Qdrant.")
            self.write_manifest(total_vectors=len(existing_ids))
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

            self.client.upsert(
                collection_name=self.collection_name,
                points=points,
                wait=True,
            )
            total_uploaded += len(points)
            pbar.update(len(points))

        pbar.close()
        logger.info(f"Nạp thành công {total_uploaded:,} chunks mới vào Qdrant!")

        # Cập nhật manifest
        total_vectors = len(existing_ids) + total_uploaded
        self.write_manifest(total_vectors=total_vectors)

        return total_uploaded

    def write_manifest(self, total_vectors: int) -> None:
        """Ghi thông tin phiên bản model, số lượng vector và cấu hình vào index_manifest.json."""
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_data: dict[str, Any] = {
            "created_at": datetime.now(UTC).isoformat(),
            "collection_name": self.collection_name,
            "embedding_model_id": self.embedding_model_id,
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
        with open(self.manifest_path, "w", encoding="utf-8") as f:
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
