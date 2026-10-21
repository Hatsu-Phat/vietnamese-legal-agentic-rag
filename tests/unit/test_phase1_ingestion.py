"""Unit tests for Phase 1: Corpus Ingestion, Legal Metadata Schema & Data Audit.

Validates text normalization, Unicode NFC compliance, legal validity invariants,
fixture-based ingestion adaptation, manifest generation, and data audit calculations.
"""

import unicodedata
from pathlib import Path
from typing import Any

import pytest

from src.common.text_utils import (
    build_provision_id,
    compute_sha256,
    extract_article_number,
    normalize_document_id,
    normalize_legal_text,
    parse_document_number_metadata,
)
from src.core.enums import (
    AnswerableStatus,
    LegalValidityStatus,
    QuestionType,
)
from src.core.models import (
    LegalDocument,
    LegalProvision,
    QAEvaluationItem,
)
from src.ingestion.audit import CorpusAuditor
from src.ingestion.source_registry import MANIFEST_COLUMNS, SourceRegistry
from src.ingestion.thiemcun_adapter import ThiemCunCorpusAdapter


@pytest.fixture
def sample_raw_corpus() -> list[dict[str, Any]]:
    """Fixture providing a mock raw legal corpus structure with typical edge cases."""
    return [
        {
            "law_id": "100/2015/QH13",
            "articles": [
                {
                    "article_id": "1",
                    "title": "Điều 1. Phạm vi áp dụng",
                    "text": (
                        "Thông tư này quy định về bảo vệ đê điều.\n"
                        "1. Áp dụng cho tổ chức và cá nhân.\n"
                        "2. Các trường hợp đặc biệt:\na) Cứu hộ khẩn cấp;\nb) Phòng chống lụt bão."
                    ),
                },
                {
                    "article_id": "2",
                    "title": "Điều 2. Giải thích từ ngữ",
                    "text": "Trong văn bản này, các từ ngữ được hiểu như sau:\n1. 'Đê điều' là...",
                },
                {
                    "article_id": "3",
                    "title": "Điều 3. Điều khoản rỗng",
                    "text": "   \n\n\t  ",  # Empty / whitespace-only text
                },
            ],
        },
        {
            "law_id": "28/2020/NĐ-CP",
            "articles": [
                {
                    "article_id": "21",
                    "title": "Điều 21. Khám sức khỏe định kỳ",
                    "text": "Người sử dụng lao động phải tổ chức khám sức khỏe định kỳ cho người lao động.",
                }
            ],
        },
    ]


@pytest.mark.unit
class TestTextNormalization:
    """Kiểm thử tiện ích chuẩn hóa văn bản và băm dữ liệu."""

    def test_unicode_nfc_normalization(self) -> None:
        """Kiểm tra chuẩn hóa Unicode NFC và loại bỏ ký tự điều khiển ẩn."""
        # Chuỗi NFD với dấu kết hợp chuẩn
        text_nfd = unicodedata.normalize("NFD", "Điều 1")
        # Ký tự zero-width space
        text_with_zw = f"{text_nfd}\u200b. Phạm\u200c vi áp\ufeff dụng"

        normalized = normalize_legal_text(text_with_zw)
        assert "\u200b" not in normalized
        assert "\u200c" not in normalized
        assert "\ufeff" not in normalized
        assert normalized == "Điều 1. Phạm vi áp dụng"

    def test_preserve_newlines_for_legal_clauses(self) -> None:
        """BẮT BUỘC giữ nguyên ký tự xuống dòng '\\n' để bảo tồn ranh giới Khoản và Điểm."""
        raw_text = (
            "Điều 1. Phạm vi điều chỉnh\r\n"
            "1. Khoản một quy định về trách nhiệm.\r\n"
            "2. Khoản hai quy định về:\r\n"
            "a) Điểm a;\r\n"
            "b) Điểm b."
        )
        cleaned = normalize_legal_text(raw_text)

        # Kiểm tra không còn ký tự CRLF của Windows
        assert "\r" not in cleaned
        # Kiểm tra các dòng vẫn tách biệt rõ ràng bằng \n
        lines = cleaned.split("\n")
        assert len(lines) == 5
        assert lines[0] == "Điều 1. Phạm vi điều chỉnh"
        assert lines[1] == "1. Khoản một quy định về trách nhiệm."
        assert lines[2] == "2. Khoản hai quy định về:"
        assert lines[3] == "a) Điểm a;"
        assert lines[4] == "b) Điểm b."

    def test_deterministic_sha256(self) -> None:
        """Kiểm tra mã băm SHA-256 tất định trên cùng chuỗi văn bản."""
        text1 = "Quy định xử phạt vi phạm hành chính"
        text2 = "Quy định xử phạt vi phạm hành chính"
        text3 = "Quy định xử phạt vi phạm hành chính khác"

        hash1 = compute_sha256(text1)
        hash2 = compute_sha256(text2)
        hash3 = compute_sha256(text3)

        assert len(hash1) == 64
        assert hash1 == hash2
        assert hash1 != hash3

    def test_normalize_document_id(self) -> None:
        """Kiểm tra hàm chuẩn hóa document_id theo đúng quy ước slug."""
        assert normalize_document_id("100/2015/QH13") == "law_2015_qh13_100"
        assert normalize_document_id("28/2020/NĐ-CP") == "law_2020_nd_cp_28"
        assert normalize_document_id("01/2009/TT-BNN") == "law_2009_tt_bnn_01"
        assert normalize_document_id("21-LCT/HĐNN8") == "law_21_lct_hdnn8"

    def test_build_provision_id(self) -> None:
        """Kiểm tra tạo provision_id duy nhất."""
        prov_id = build_provision_id("law_2015_qh13_100", "1")
        assert prov_id == "law_2015_qh13_100__art_1"

    def test_extract_article_number(self) -> None:
        """Kiểm tra trích xuất số thứ tự Điều từ tiêu đề."""
        assert extract_article_number("Điều 1. Phạm vi áp dụng") == "Điều 1"
        assert extract_article_number("Điều 21a. Miễn trừ") == "Điều 21a"
        assert extract_article_number("Phần thứ nhất") is None

    def test_parse_metadata_from_law_id(self) -> None:
        """Kiểm tra phân tích năm, loại và cơ quan ban hành từ số hiệu văn bản."""
        qh = parse_document_number_metadata("100/2015/QH13")
        assert qh["issued_year"] == 2015
        assert qh["document_type"] == "Luật"
        assert qh["issuing_authority"] == "Quốc hội"

        nd = parse_document_number_metadata("28/2020/NĐ-CP")
        assert nd["issued_year"] == 2020
        assert nd["document_type"] == "Nghị định"
        assert nd["issuing_authority"] == "Chính phủ"

        tt = parse_document_number_metadata("01/2009/TT-BNN")
        assert tt["issued_year"] == 2009
        assert tt["document_type"] == "Thông tư"
        assert tt["issuing_authority"] == "Bộ Nông nghiệp và Phát triển nông thôn"

        unknown = parse_document_number_metadata("xyz-unknown-symbol")
        assert unknown["issued_year"] is None
        assert unknown["document_type"] is None
        assert unknown["issuing_authority"] is None


@pytest.mark.unit
class TestDomainModelsAndLegalInvariants:
    """Kiểm thử Pydantic v2 schemas và nguyên tắc unknown != valid."""

    def test_legal_document_default_unknown_status(self) -> None:
        """Văn bản không có ngày hiệu lực bắt buộc gán status == UNKNOWN."""
        doc = LegalDocument(
            document_id="law_2015_qh13_100",
            document_number="100/2015/QH13",
            content_hash=compute_sha256("sample"),
        )
        assert doc.status == LegalValidityStatus.UNKNOWN
        assert doc.effective_from is None
        assert doc.effective_to is None
        assert doc.ingestion_version == "1.0.0"

    def test_legal_provision_default_unknown_status(self) -> None:
        """Điều khoản bắt buộc mặc định gán status == UNKNOWN."""
        prov = LegalProvision(
            provision_id="law_2015_qh13_100__art_1",
            document_id="law_2015_qh13_100",
            document_number="100/2015/QH13",
            article_id="1",
            article_number="Điều 1",
            title="Điều 1. Phạm vi áp dụng",
            text="Nội dung điều 1",
            provision_path="100/2015/QH13 > Điều 1",
            char_length=15,
            word_count=4,
            content_hash=compute_sha256("Nội dung điều 1"),
        )
        assert prov.status == LegalValidityStatus.UNKNOWN
        assert prov.word_count == 4
        assert prov.char_length == 15

    def test_qa_evaluation_item_defaults(self) -> None:
        """Kiểm tra giá trị mặc định của câu hỏi đánh giá."""
        qa = QAEvaluationItem(
            question_id="q123",
            question_text="Quy định khám sức khỏe như thế nào?",
        )
        assert qa.question_type == QuestionType.DIRECT
        assert qa.answerable_status == AnswerableStatus.ANSWERABLE
        assert qa.gold_document_ids == []
        assert qa.gold_provision_ids == []
        assert qa.review_status == "unverified_imported"


@pytest.mark.unit
class TestIngestionAdapter:
    """Kiểm thử bộ nạp ThiemCunCorpusAdapter trên fixture."""

    def test_process_corpus_fixture(self, sample_raw_corpus: list[dict[str, Any]]) -> None:
        """Kiểm tra adapter xử lý đúng ID, loại bỏ điều rỗng và tạo metadata."""
        adapter = ThiemCunCorpusAdapter()
        res = adapter.process_corpus(sample_raw_corpus)

        # 2 văn bản được nạp
        assert len(res.documents) == 2
        assert res.documents[0].document_id == "law_2015_qh13_100"
        assert res.documents[1].document_id == "law_2020_nd_cp_28"

        # Văn bản 1 có 3 điều trong đó Điều 3 rỗng -> còn 2 điều hợp lệ
        # Văn bản 2 có 1 điều -> tổng cộng 3 điều
        assert len(res.provisions) == 3
        assert res.skipped_empty_articles == 1

        # Kiểm tra provision_id tạo chuẩn
        prov_ids = [p.provision_id for p in res.provisions]
        assert "law_2015_qh13_100__art_1" in prov_ids
        assert "law_2015_qh13_100__art_2" in prov_ids
        assert "law_2020_nd_cp_28__art_21" in prov_ids

        # Kiểm tra anomaly được ghi nhận
        assert len(res.anomalies) == 1
        assert res.anomalies[0].issue_type == "empty_text"
        assert res.anomalies[0].article_id == "3"

    def test_export_and_load_manifest(
        self, tmp_path: Path, sample_raw_corpus: list[dict[str, Any]]
    ) -> None:
        """Kiểm tra ghi và đọc lại source_manifest.csv."""
        adapter = ThiemCunCorpusAdapter()
        res = adapter.process_corpus(sample_raw_corpus)

        manifest_file = tmp_path / "manifests" / "source_manifest.csv"
        registry = SourceRegistry(manifest_path=manifest_file)
        saved_path = registry.save_manifest(res.manifest_entries)
        assert saved_path.is_file()

        loaded_entries = registry.load_manifest()
        assert len(loaded_entries) == 2
        for col in MANIFEST_COLUMNS:
            assert col in loaded_entries[0]

        assert loaded_entries[0]["document_id"] == "law_2015_qh13_100"
        assert loaded_entries[0]["article_count"] == 2
        assert loaded_entries[0]["status"] == "unknown"


@pytest.mark.unit
class TestAuditReportGeneration:
    """Kiểm thử tính toán thống kê và sinh báo cáo kiểm toán."""

    def test_audit_calculation_and_markdown(
        self, tmp_path: Path, sample_raw_corpus: list[dict[str, Any]]
    ) -> None:
        """Kiểm tra tính toán số liệu và sinh tệp data_audit.md."""
        adapter = ThiemCunCorpusAdapter()
        res = adapter.process_corpus(sample_raw_corpus)

        processed_dir = tmp_path / "processed"
        manifest_dir = tmp_path / "manifests"
        report_dir = tmp_path / "reports"

        adapter.export_processed_jsonl(res.documents, res.provisions, output_dir=processed_dir)

        auditor = CorpusAuditor(
            processed_dir=processed_dir,
            manifest_dir=manifest_dir,
            report_dir=report_dir,
        )
        stats = auditor.compute_audit_stats(res.documents, res.provisions)

        assert stats.total_documents == 2
        assert stats.total_provisions == 3
        assert stats.status_unknown_rate == 1.0
        assert stats.length_distribution.total == 3

        stats_json = auditor.export_audit_json(stats)
        assert stats_json.is_file()

        report_md = auditor.generate_markdown_report(stats)
        assert report_md.is_file()

        content = report_md.read_text(encoding="utf-8")
        assert "Báo cáo Kiểm toán Dữ liệu Pháp luật" in content
        assert "2" in content  # 2 documents
        assert "3" in content  # 3 provisions
