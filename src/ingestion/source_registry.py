"""Source registry and manifest management for legal documents.

Manages data/manifests/source_manifest.csv to track provenance, document hashes,
article counts, and ingestion metadata for complete auditability.
"""

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.common.exceptions import ManifestError
from src.common.logger import logger
from src.core.models import LegalDocument

MANIFEST_COLUMNS: list[str] = [
    "document_id",
    "document_number",
    "document_type",
    "issuing_authority",
    "issued_year",
    "status",
    "article_count",
    "total_chars",
    "source_dataset",
    "source_url",
    "content_hash",
    "ingested_at",
    "ingestion_version",
]


class SourceRegistry:
    """Manages reading and writing the legal document source manifest CSV."""

    def __init__(self, manifest_path: str | Path = "data/manifests/source_manifest.csv") -> None:
        self.manifest_path = Path(manifest_path)

    def create_entry(
        self,
        doc: LegalDocument,
        article_count: int,
        total_chars: int,
        ingested_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Tạo một bản ghi manifest từ LegalDocument và các số liệu thống kê.

        Args:
            doc: Đối tượng LegalDocument.
            article_count: Tổng số điều khoản thuộc văn bản.
            total_chars: Tổng số ký tự của tất cả các điều khoản.
            ingested_at: Thời điểm nạp (mặc định là hiện tại UTC).

        Returns:
            dict tương ứng với các cột trong MANIFEST_COLUMNS.
        """
        now = (ingested_at or datetime.now(UTC)).isoformat()
        return {
            "document_id": doc.document_id,
            "document_number": doc.document_number,
            "document_type": doc.document_type or "",
            "issuing_authority": doc.issuing_authority or "",
            "issued_year": doc.issued_year if doc.issued_year is not None else "",
            "status": doc.status.value,
            "article_count": article_count,
            "total_chars": total_chars,
            "source_dataset": doc.source_dataset,
            "source_url": doc.source_url or "",
            "content_hash": doc.content_hash,
            "ingested_at": now,
            "ingestion_version": doc.ingestion_version,
        }

    def save_manifest(self, entries: list[dict[str, Any]]) -> Path:
        """Lưu danh sách bản ghi manifest ra tệp CSV.

        Args:
            entries: Danh sách các bản ghi dictionary.

        Returns:
            Đường dẫn Path đến tệp CSV đã ghi.

        Raises:
            ManifestError: Nếu có lỗi khi ghi tệp CSV.
        """
        try:
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.manifest_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
                writer.writeheader()
                for entry in entries:
                    filtered = {col: entry.get(col, "") for col in MANIFEST_COLUMNS}
                    writer.writerow(filtered)
            logger.info(
                f"Đã lưu source manifest thành công tại {self.manifest_path} ({len(entries)} văn bản)"
            )
            return self.manifest_path
        except Exception as e:
            raise ManifestError(
                f"Không thể ghi source manifest tại {self.manifest_path}",
                details={"error": str(e)},
            ) from e

    def load_manifest(self) -> list[dict[str, Any]]:
        """Đọc toàn bộ bản ghi manifest từ tệp CSV.

        Returns:
            Danh sách các bản ghi manifest.

        Raises:
            ManifestError: Nếu tệp không tồn tại hoặc lỗi đọc.
        """
        if not self.manifest_path.is_file():
            raise ManifestError(f"Tệp source manifest không tồn tại: {self.manifest_path}")

        try:
            entries: list[dict[str, Any]] = []
            with open(self.manifest_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Cast numeric fields appropriately
                    row_dict = dict(row)
                    if row_dict.get("article_count"):
                        row_dict["article_count"] = int(row_dict["article_count"])
                    if row_dict.get("total_chars"):
                        row_dict["total_chars"] = int(row_dict["total_chars"])
                    if row_dict.get("issued_year"):
                        row_dict["issued_year"] = int(row_dict["issued_year"])
                    entries.append(row_dict)
            return entries
        except Exception as e:
            raise ManifestError(
                f"Lỗi khi đọc source manifest từ {self.manifest_path}",
                details={"error": str(e)},
            ) from e
