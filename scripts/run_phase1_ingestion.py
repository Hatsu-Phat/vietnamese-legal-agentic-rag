#!/usr/bin/env python3
"""CLI script executing Phase 1: Corpus Ingestion, Legal Metadata Schema & Data Audit.

Usage:
    python scripts/run_phase1_ingestion.py --raw-dir data/raw
    python scripts/run_phase1_ingestion.py --sample-only --sample-size 50
"""

import argparse
import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.logger import logger
from src.ingestion.audit import CorpusAuditor
from src.ingestion.qa_importer import QAImporter
from src.ingestion.source_registry import SourceRegistry
from src.ingestion.thiemcun_adapter import ThiemCunCorpusAdapter


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for Phase 1 ingestion CLI."""
    parser = argparse.ArgumentParser(
        description="Thực thi Phase 1 — Corpus Ingestion, Legal Metadata Schema & Data Audit"
    )
    parser.add_argument(
        "--raw-dir",
        type=str,
        default="data/raw/thiemcun169_gui",
        help="Đường dẫn đến thư mục chứa dữ liệu thô (mặc định: data/raw/thiemcun169_gui)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/processed",
        help="Đường dẫn thư mục xuất documents.jsonl và provisions.jsonl",
    )
    parser.add_argument(
        "--manifest-dir",
        type=str,
        default="data/manifests",
        help="Đường dẫn thư mục lưu source_manifest.csv và corpus_stats.json",
    )
    parser.add_argument(
        "--gold-dir",
        type=str,
        default="data/gold_eval",
        help="Đường dẫn thư mục lưu qa_dev.jsonl và qa_test.jsonl",
    )
    parser.add_argument(
        "--sample-dir",
        type=str,
        default="data/sample_laws",
        help="Đường dẫn thư mục lưu sample_corpus_subset.json",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=50,
        help="Số lượng văn bản cho tập mẫu (mặc định: 50)",
    )
    parser.add_argument(
        "--sample-only",
        action="store_true",
        help="Chỉ nạp và xử lý tập mẫu (dùng cho CI hoặc kiểm thử nhanh)",
    )
    return parser.parse_args()


def resolve_raw_directory(requested_dir: str) -> Path:
    """Tự động phát hiện thư mục dữ liệu thô nếu đường dẫn mặc định chưa tồn tại."""
    req_path = Path(requested_dir)
    if req_path.is_dir() and (
        (req_path / "legal_corpus.json").is_file()
        or (req_path / "corpus" / "legal_corpus.json").is_file()
    ):
        return req_path

    # Fallback to standard data/raw if available
    alt_path = Path("data/raw")
    if alt_path.is_dir() and (
        (alt_path / "legal_corpus.json").is_file()
        or (alt_path / "corpus" / "legal_corpus.json").is_file()
    ):
        logger.info(
            f"Thư mục '{requested_dir}' chưa chứa dữ liệu, tự động chuyển sang '{alt_path}'"
        )
        return alt_path

    return req_path


def main() -> int:
    """Điểm nhập chính cho CLI thực thi Phase 1."""
    args = parse_args()
    logger.info("=" * 70)
    logger.info("BẮT ĐẦU THỰC THI PHASE 1: CORPUS INGESTION & DATA AUDIT")
    logger.info("=" * 70)

    raw_dir = resolve_raw_directory(args.raw_dir)
    output_dir = Path(args.output_dir)
    manifest_dir = Path(args.manifest_dir)
    gold_dir = Path(args.gold_dir)
    sample_dir = Path(args.sample_dir)

    # 1. Khởi tạo Adapter và tìm tệp corpus
    adapter = ThiemCunCorpusAdapter()
    corpus_file = adapter.locate_corpus_file(raw_dir)
    logger.info(f"Đọc dữ liệu thô từ: {corpus_file}")
    raw_corpus = adapter.load_raw_corpus(corpus_file)
    logger.info(f"Tổng số văn bản trong tệp thô: {len(raw_corpus):,}")

    # 2. Xuất tập mẫu phục vụ smoke test & CI
    sample_corpus_file = sample_dir / "sample_corpus_subset.json"
    adapter.export_sample_corpus_subset(
        raw_corpus,
        sample_path=sample_corpus_file,
        sample_size=args.sample_size,
    )

    # 3. Chuyển đổi dữ liệu sang LegalDocument và LegalProvision
    limit = args.sample_size if args.sample_only else None
    if args.sample_only:
        logger.info(f"Chế độ --sample-only: giới hạn xử lý {limit} văn bản đầu tiên")

    ingestion_result = adapter.process_corpus(raw_corpus, limit=limit)

    # 4. Xuất documents.jsonl và provisions.jsonl
    docs_path, provs_path = adapter.export_processed_jsonl(
        ingestion_result.documents,
        ingestion_result.provisions,
        output_dir=output_dir,
    )

    # 5. Xuất source_manifest.csv
    registry = SourceRegistry(manifest_path=manifest_dir / "source_manifest.csv")
    registry.save_manifest(ingestion_result.manifest_entries)

    # 6. Nhập và chuẩn hóa bộ Q&A
    qa_importer = QAImporter(raw_dir=raw_dir)
    qa_dev_items, qa_test_items = qa_importer.import_datasets()

    # Kiểm tra coverage của Q&A với corpus đã nạp
    known_provision_ids = {p.provision_id for p in ingestion_result.provisions}
    qa_dev_report = qa_importer.audit_coverage(qa_dev_items, known_provision_ids)
    qa_test_report = qa_importer.audit_coverage(qa_test_items, known_provision_ids)

    # Xuất qa_dev.jsonl và qa_test.jsonl
    qa_importer.export_gold_eval(qa_dev_items, qa_test_items, output_dir=gold_dir)

    # 7. Kiểm toán dữ liệu và sinh báo cáo
    auditor = CorpusAuditor(
        processed_dir=output_dir,
        manifest_dir=manifest_dir,
        gold_eval_dir=gold_dir,
        report_dir="docs/reports",
    )
    stats = auditor.compute_audit_stats(
        docs=ingestion_result.documents,
        provs=ingestion_result.provisions,
        qa_dev_report=qa_dev_report,
        qa_test_report=qa_test_report,
    )
    auditor.export_audit_json(stats)
    report_md = auditor.generate_markdown_report(stats)

    # 8. Tóm tắt kết quả
    logger.info("=" * 70)
    logger.info("HOÀN THÀNH PHASE 1 XUẤT SẮC:")
    logger.info(f"- Legal Documents:   {len(ingestion_result.documents):,} tệp -> {docs_path}")
    logger.info(f"- Legal Provisions:  {len(ingestion_result.provisions):,} điều -> {provs_path}")
    logger.info(f"- Source Manifest:   {manifest_dir / 'source_manifest.csv'}")
    logger.info(f"- Sample Corpus:     {sample_corpus_file}")
    logger.info(
        f"- QA Dev:            {len(qa_dev_items)} câu hỏi ({qa_dev_report.coverage_rate_questions * 100:.1f}% phủ đủ)"
    )
    logger.info(
        f"- QA Test:           {len(qa_test_items)} câu hỏi ({qa_test_report.coverage_rate_questions * 100:.1f}% phủ đủ)"
    )
    logger.info(f"- Báo cáo kiểm toán: {report_md}")
    logger.info("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
