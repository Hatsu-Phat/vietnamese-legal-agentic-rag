"""Adapter for ingesting Vietnamese legal corpus from the thiemcun169 repository.

Reads legal_corpus.json, standardizes documents and provisions, computes deterministic
SHA-256 hashes, normalizes text while preserving structure, and enforces legal validity invariants.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.common.exceptions import CorpusNotFoundError, CorpusParseError
from src.common.logger import logger
from src.common.text_utils import (
    build_provision_id,
    compute_sha256,
    extract_article_number,
    normalize_document_id,
    normalize_legal_text,
    parse_document_number_metadata,
)
from src.core.enums import LegalValidityStatus
from src.core.models import LegalDocument, LegalProvision


@dataclass
class IngestionAnomaly:
    """Bản ghi chi tiết các bất thường phát hiện trong quá trình nạp dữ liệu."""

    law_id: str
    article_id: str | None = None
    issue_type: str = "unknown"  # empty_text | empty_title | duplicate_article_id | parse_error
    description: str = ""


@dataclass
class IngestionResult:
    """Kết quả tổng kết quá trình nạp dữ liệu pháp luật."""

    documents: list[LegalDocument] = field(default_factory=list)
    provisions: list[LegalProvision] = field(default_factory=list)
    manifest_entries: list[dict[str, Any]] = field(default_factory=list)
    anomalies: list[IngestionAnomaly] = field(default_factory=list)
    total_raw_documents: int = 0
    total_raw_articles: int = 0
    skipped_empty_articles: int = 0
    duplicate_articles_found: int = 0


class ThiemCunCorpusAdapter:
    """Adapter chuyên biệt để nạp và chuẩn hóa dữ liệu từ legal_corpus.json."""

    def __init__(
        self,
        raw_corpus_path: str | Path | None = None,
        source_dataset_name: str = "thiemcun169_data_for_gui",
    ) -> None:
        self.raw_corpus_path = Path(raw_corpus_path) if raw_corpus_path else None
        self.source_dataset_name = source_dataset_name

    def locate_corpus_file(self, raw_dir: str | Path) -> Path:
        """Tìm tệp legal_corpus.json trong thư mục chỉ định hoặc các thư mục con phổ biến.

        Hỗ trợ cả cấu trúc phẳng (raw_dir/legal_corpus.json) và cấu trúc lồng
        (raw_dir/corpus/legal_corpus.json).

        Args:
            raw_dir: Thư mục chứa dữ liệu thô.

        Returns:
            Đường dẫn Path tới legal_corpus.json.

        Raises:
            CorpusNotFoundError: Nếu không tìm thấy tệp ở bất kỳ vị trí nào.
        """
        base = Path(raw_dir)
        candidates = [
            base / "legal_corpus.json",
            base / "corpus" / "legal_corpus.json",
            base / "thiemcun169_gui" / "corpus" / "legal_corpus.json",
            base / "thiemcun169_gui" / "legal_corpus.json",
        ]
        for candidate in candidates:
            if candidate.is_file():
                logger.info(f"Đã tìm thấy legal_corpus.json tại: {candidate}")
                return candidate

        raise CorpusNotFoundError(
            f"Không tìm thấy tệp legal_corpus.json tại '{raw_dir}' hoặc các thư mục con",
            details={"searched_candidates": [str(c) for c in candidates]},
        )

    def load_raw_corpus(self, file_path: Path) -> list[dict[str, Any]]:
        """Đọc và phân tích cú pháp tệp JSON dữ liệu thô.

        Args:
            file_path: Đường dẫn tới legal_corpus.json.

        Returns:
            Danh sách các dictionary văn bản gốc.

        Raises:
            CorpusParseError: Nếu tệp JSON bị lỗi định dạng.
        """
        try:
            with open(file_path, encoding="utf-8") as f:
                data: Any = json.load(f)
                if not isinstance(data, list):
                    raise CorpusParseError(
                        f"Dữ liệu trong {file_path} không phải là JSON list",
                        details={"actual_type": type(data).__name__},
                    )
                return data
        except json.JSONDecodeError as e:
            raise CorpusParseError(
                f"Lỗi giải mã JSON từ tệp {file_path}",
                details={"line": e.lineno, "col": e.colno, "msg": e.msg},
            ) from e
        except Exception as e:
            raise CorpusParseError(
                f"Không thể đọc tệp dữ liệu thô {file_path}",
                details={"error": str(e)},
            ) from e

    def process_corpus(
        self,
        raw_items: list[dict[str, Any]],
        limit: int | None = None,
        filter_law_ids: set[str] | None = None,
    ) -> IngestionResult:
        """Chuyển đổi danh sách văn bản thô sang các domain entities LegalDocument và LegalProvision.

        Quy tắc bất biến:
        - status = LegalValidityStatus.UNKNOWN và effective_from = None nếu không có dữ liệu nguồn.
        - Điều khoản có text rỗng được ghi nhận vào anomalies và bỏ qua trong danh sách truy xuất.
        - Trùng lặp article_id trong cùng một văn bản được cảnh báo và xử lý tất định.

        Args:
            raw_items: Danh sách dict từ legal_corpus.json.
            limit: Giới hạn số lượng văn bản xử lý (phục vụ test).
            filter_law_ids: Tập các law_id cần xử lý nếu chỉ trích xuất subset.

        Returns:
            IngestionResult chứa danh sách documents, provisions, anomalies và thống kê.
        """
        result = IngestionResult()
        result.total_raw_documents = len(raw_items)

        items_to_process = raw_items
        if filter_law_ids:
            items_to_process = [
                item for item in items_to_process if item.get("law_id") in filter_law_ids
            ]
        if limit is not None and limit > 0:
            items_to_process = items_to_process[:limit]

        for doc_item in items_to_process:
            raw_law_id = str(doc_item.get("law_id", "")).strip()
            if not raw_law_id:
                result.anomalies.append(
                    IngestionAnomaly(
                        law_id="",
                        issue_type="missing_law_id",
                        description="Văn bản không có law_id, bỏ qua",
                    )
                )
                continue

            doc_id = normalize_document_id(raw_law_id)
            meta = parse_document_number_metadata(raw_law_id)

            raw_articles = doc_item.get("articles", [])
            result.total_raw_articles += len(raw_articles)

            doc_provisions: list[LegalProvision] = []
            seen_article_ids: set[str] = set()

            for art_item in raw_articles:
                raw_art_id = str(art_item.get("article_id", "")).strip()
                raw_title = str(art_item.get("title", "")).strip()
                raw_text = str(art_item.get("text", ""))

                if not raw_art_id:
                    result.anomalies.append(
                        IngestionAnomaly(
                            law_id=raw_law_id,
                            article_id="",
                            issue_type="empty_article_id",
                            description=f"Điều khoản thiếu article_id trong văn bản {raw_law_id}",
                        )
                    )
                    continue

                if raw_art_id in seen_article_ids:
                    result.duplicate_articles_found += 1
                    result.anomalies.append(
                        IngestionAnomaly(
                            law_id=raw_law_id,
                            article_id=raw_art_id,
                            issue_type="duplicate_article_id",
                            description=f"Trùng lặp article_id '{raw_art_id}' trong văn bản {raw_law_id}",
                        )
                    )
                    # Deduplicate: append suffix to ensure unique provision_id
                    raw_art_id = f"{raw_art_id}_dup"
                seen_article_ids.add(raw_art_id)

                clean_text = normalize_legal_text(raw_text)
                if not clean_text:
                    result.skipped_empty_articles += 1
                    result.anomalies.append(
                        IngestionAnomaly(
                            law_id=raw_law_id,
                            article_id=raw_art_id,
                            issue_type="empty_text",
                            description=f"Điều {raw_art_id} trong văn bản {raw_law_id} có nội dung rỗng",
                        )
                    )
                    continue

                if not raw_title:
                    result.anomalies.append(
                        IngestionAnomaly(
                            law_id=raw_law_id,
                            article_id=raw_art_id,
                            issue_type="empty_title",
                            description=f"Điều {raw_art_id} trong văn bản {raw_law_id} thiếu tiêu đề",
                        )
                    )
                    raw_title = f"Điều {raw_art_id}"

                art_number = extract_article_number(raw_title) or f"Điều {raw_art_id}"
                provision_path = f"{raw_law_id} > {art_number}"
                prov_id = build_provision_id(doc_id, raw_art_id)
                prov_hash = compute_sha256(clean_text)

                provision = LegalProvision(
                    provision_id=prov_id,
                    document_id=doc_id,
                    document_number=raw_law_id,
                    article_id=raw_art_id,
                    article_number=art_number,
                    title=raw_title,
                    text=clean_text,
                    provision_path=provision_path,
                    char_length=len(clean_text),
                    word_count=len(clean_text.split()),
                    content_hash=prov_hash,
                    status=LegalValidityStatus.UNKNOWN,
                )
                doc_provisions.append(provision)

            # Document-level content hash is SHA-256 of concatenated provision texts
            combined_content = "\n\n".join(p.text for p in doc_provisions)
            doc_hash = compute_sha256(combined_content)
            total_doc_chars = sum(p.char_length for p in doc_provisions)

            legal_doc = LegalDocument(
                document_id=doc_id,
                document_number=raw_law_id,
                title=None,
                document_type=meta.get("document_type"),
                issuing_authority=meta.get("issuing_authority"),
                issued_date=None,
                issued_year=meta.get("issued_year"),
                effective_from=None,
                effective_to=None,
                status=LegalValidityStatus.UNKNOWN,
                source_url=None,
                source_dataset=self.source_dataset_name,
                source_last_verified_at=None,
                content_hash=doc_hash,
                ingestion_version="1.0.0",
            )

            result.documents.append(legal_doc)
            result.provisions.extend(doc_provisions)

            # Create manifest entry
            result.manifest_entries.append(
                {
                    "document_id": legal_doc.document_id,
                    "document_number": legal_doc.document_number,
                    "document_type": legal_doc.document_type or "",
                    "issuing_authority": legal_doc.issuing_authority or "",
                    "issued_year": legal_doc.issued_year
                    if legal_doc.issued_year is not None
                    else "",
                    "status": legal_doc.status.value,
                    "article_count": len(doc_provisions),
                    "total_chars": total_doc_chars,
                    "source_dataset": legal_doc.source_dataset,
                    "source_url": legal_doc.source_url or "",
                    "content_hash": legal_doc.content_hash,
                    "ingestion_version": legal_doc.ingestion_version,
                }
            )

        logger.info(
            f"Hoàn thành chuyển đổi: {len(result.documents)} văn bản, "
            f"{len(result.provisions)} điều khoản (bỏ qua {result.skipped_empty_articles} điều rỗng, "
            f"phát hiện {result.duplicate_articles_found} điều trùng ID)"
        )
        return result

    def export_processed_jsonl(
        self,
        documents: list[LegalDocument],
        provisions: list[LegalProvision],
        output_dir: str | Path = "data/processed",
    ) -> tuple[Path, Path]:
        """Xuất danh sách documents và provisions ra định dạng JSON Lines (.jsonl).

        Args:
            documents: Danh sách LegalDocument đã chuẩn hóa.
            provisions: Danh sách LegalProvision đã chuẩn hóa.
            output_dir: Thư mục lưu kết quả (mặc định data/processed).

        Returns:
            Tuple chứa (đường_dẫn_documents_jsonl, đường_dẫn_provisions_jsonl).
        """
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        docs_file = out_path / "documents.jsonl"
        with open(docs_file, "w", encoding="utf-8") as f:
            for doc in documents:
                f.write(doc.model_dump_json() + "\n")

        provs_file = out_path / "provisions.jsonl"
        with open(provs_file, "w", encoding="utf-8") as f:
            for prov in provisions:
                f.write(prov.model_dump_json() + "\n")

        logger.info(
            f"Đã xuất dữ liệu chuẩn hóa: {docs_file} ({len(documents)} dòng), "
            f"{provs_file} ({len(provisions)} dòng)"
        )
        return docs_file, provs_file

    def export_sample_corpus_subset(
        self,
        raw_items: list[dict[str, Any]],
        sample_path: str | Path = "data/sample_laws/sample_corpus_subset.json",
        sample_size: int = 50,
    ) -> Path:
        """Trích xuất tập mẫu N văn bản thô đầu tiên phục vụ smoke test và CI.

        Args:
            raw_items: Danh sách văn bản thô từ legal_corpus.json.
            sample_path: Đường dẫn lưu tệp JSON mẫu.
            sample_size: Số lượng văn bản lấy mẫu (mặc định 50).

        Returns:
            Đường dẫn Path tới tệp mẫu đã lưu.
        """
        target = Path(sample_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        sample_data = raw_items[:sample_size]

        with open(target, "w", encoding="utf-8") as f:
            json.dump(sample_data, f, ensure_ascii=False, indent=2)

        logger.info(f"Đã lưu tập văn bản mẫu ({len(sample_data)} văn bản) tại {target}")
        return target
