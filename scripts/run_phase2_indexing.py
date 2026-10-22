#!/usr/bin/env python3
"""CLI script executing Phase 2: Legal-Aware Hierarchical Chunking, Qdrant Vector Index & BM25 Index.

Usage:
    python scripts/run_phase2_indexing.py --step all
    python scripts/run_phase2_indexing.py --step chunk
    python scripts/run_phase2_indexing.py --sample-only --step all
    python scripts/run_phase2_indexing.py --step qdrant --resume --batch-size 64
"""

import argparse
import sys
from pathlib import Path

# Thêm thư mục gốc dự án vào sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.chunking.chunk_audit import ChunkAuditor
from src.chunking.legal_chunker import LegalChunker
from src.common.config import load_yaml_config
from src.common.logger import logger
from src.core.models import LegalChunk, LegalDocument, LegalProvision
from src.retrieval.bm25_indexer import BM25Indexer
from src.retrieval.qdrant_indexer import QdrantIndexer


def parse_args() -> argparse.Namespace:
    """Định nghĩa và xử lý đối số dòng lệnh cho Phase 2 Indexing CLI."""
    parser = argparse.ArgumentParser(
        description="Thực thi Phase 2 — Legal-Aware Hierarchical Chunking, Qdrant Vector Index & BM25 Index"
    )
    parser.add_argument(
        "--config-path",
        type=str,
        default="configs/dev.yaml",
        help="Đường dẫn file cấu hình YAML (mặc định: configs/dev.yaml)",
    )
    parser.add_argument(
        "--step",
        type=str,
        choices=["all", "chunk", "bm25", "qdrant"],
        default="all",
        help="Bước cần thực hiện: chunk, bm25, qdrant, hoặc all (mặc định: all)",
    )
    parser.add_argument(
        "--sample-only",
        action="store_true",
        help="Chỉ chạy trên tập 50 văn bản mẫu để kiểm thử toàn luồng nhanh",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=50,
        help="Số lượng văn bản mẫu khi bật --sample-only (mặc định: 50)",
    )
    parser.add_argument(
        "--device",
        type=str,
        choices=["auto", "cuda", "mps", "cpu"],
        default="auto",
        help="Thiết bị tính toán cho BGE-M3 (mặc định: auto)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Kích thước batch khi encode và upload vector vào Qdrant (mặc định: 32)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Bỏ qua các chunk_id đã tồn tại trong Qdrant khi nạp lại",
    )
    parser.add_argument(
        "--qdrant-mode",
        type=str,
        choices=["local", "server", "memory"],
        default=None,
        help="Chế độ chạy Qdrant: local (lưu trên đĩa), server (Docker), hoặc memory",
    )
    return parser.parse_args()


def load_documents_map(documents_path: Path) -> dict[str, LegalDocument]:
    """Tải danh sách LegalDocument từ documents.jsonl vào từ điển tra cứu theo document_id."""
    doc_map: dict[str, LegalDocument] = {}
    if not documents_path.is_file():
        logger.warning(f"Không tìm thấy file documents tại {documents_path}")
        return doc_map

    with open(documents_path, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                doc_obj = LegalDocument.model_validate_json(line_str)
                doc_map[doc_obj.document_id] = doc_obj

    logger.info(f"Đã tải {len(doc_map):,} tài liệu vào document lookup map.")
    return doc_map


def load_provisions(
    provisions_path: Path,
    target_document_ids: set[str] | None = None,
) -> list[LegalProvision]:
    """Tải danh sách LegalProvision từ file jsonl, hỗ trợ lọc theo target_document_ids."""
    provisions: list[LegalProvision] = []
    if not provisions_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file provisions tại {provisions_path}")

    with open(provisions_path, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                prov_obj = LegalProvision.model_validate_json(line_str)
                if target_document_ids is None or prov_obj.document_id in target_document_ids:
                    provisions.append(prov_obj)

    logger.info(f"Đã tải {len(provisions):,} điều khoản từ {provisions_path}")
    return provisions


def load_chunks_from_file(chunks_path: Path) -> list[LegalChunk]:
    """Tải các LegalChunk đã xử lý từ file jsonl."""
    chunks: list[LegalChunk] = []
    if not chunks_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file chunks tại {chunks_path}")

    with open(chunks_path, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                chunks.append(LegalChunk.model_validate_json(line_str))

    logger.info(f"Đã tải {len(chunks):,} chunks từ {chunks_path}")
    return chunks


def run_chunking_step(
    cfg: dict,
    sample_only: bool,
    sample_size: int,
) -> list[LegalChunk]:
    """Thực thi Bước 1: Legal-Aware Hierarchical Chunking & Audit."""
    logger.info("=" * 70)
    logger.info("BẮT ĐẦU BƯỚC 1: LEGAL-AWARE HIERARCHICAL CHUNKING")
    logger.info("=" * 70)

    chunk_cfg = cfg.get("chunking", {})
    output_chunks_path = Path(chunk_cfg.get("output_chunks_path", "data/processed/chunks.jsonl"))
    chunk_stats_path = Path(chunk_cfg.get("chunk_stats_path", "data/manifests/chunk_stats.json"))
    max_words = int(chunk_cfg.get("max_words_per_chunk", 400))
    overlap_words = int(chunk_cfg.get("overlap_words", 50))
    min_words = int(chunk_cfg.get("min_words_per_chunk", 15))
    inc_breadcrumb = bool(chunk_cfg.get("include_breadcrumb_in_embedding_text", True))

    doc_map = load_documents_map(Path("data/processed/documents.jsonl"))

    target_doc_ids: set[str] | None = None
    if sample_only:
        # Lấy sample_size văn bản đầu tiên
        target_doc_ids = set(list(doc_map.keys())[:sample_size])
        logger.info(f"Chế độ --sample-only: Giới hạn xử lý {len(target_doc_ids)} văn bản mẫu.")

    provisions = load_provisions(
        Path("data/processed/provisions.jsonl"),
        target_document_ids=target_doc_ids,
    )

    chunker = LegalChunker(
        max_words_per_chunk=max_words,
        overlap_words=overlap_words,
        min_words_per_chunk=min_words,
        include_breadcrumb_in_embedding_text=inc_breadcrumb,
    )

    logger.info(f"Tiến hành phân mảnh {len(provisions):,} điều khoản...")
    chunks = chunker.chunk_corpus(provisions, doc_map=doc_map)

    # Lưu chunks.jsonl
    output_chunks_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_chunks_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(chunk.model_dump_json() + "\n")

    logger.info(f"Đã xuất {len(chunks):,} chunks ra file: {output_chunks_path}")

    # Chạy kiểm toán chunking
    auditor = ChunkAuditor(
        report_path="docs/reports/chunk_audit.md",
        manifest_path=str(chunk_stats_path),
    )
    original_prov_ids = {p.provision_id for p in provisions}
    stats = auditor.compute_stats(chunks, original_provision_ids=original_prov_ids)
    auditor.export_stats_json(stats, chunk_stats_path)
    auditor.generate_markdown_report(stats, "docs/reports/chunk_audit.md")

    logger.info(
        f"Hoàn thành kiểm toán phân mảnh! Tỷ lệ phủ điều luật: {stats['provision_coverage_rate'] * 100:.2f}%"
    )
    logger.info(f"Báo cáo Markdown: docs/reports/chunk_audit.md | Manifest: {chunk_stats_path}")

    return chunks


def run_bm25_step(
    cfg: dict,
    chunks: list[LegalChunk] | None = None,
) -> BM25Indexer:
    """Thực thi Bước 2: Xây dựng chỉ mục từ khóa BM25."""
    logger.info("=" * 70)
    logger.info("BẮT ĐẦU BƯỚC 2: BM25 LEXICAL INDEXING")
    logger.info("=" * 70)

    chunk_cfg = cfg.get("chunking", {})
    output_chunks_path = Path(chunk_cfg.get("output_chunks_path", "data/processed/chunks.jsonl"))

    if chunks is None:
        chunks = load_chunks_from_file(output_chunks_path)

    idx_cfg = cfg.get("indexing", {})
    bm25_cfg = idx_cfg.get("bm25", {})

    indexer = BM25Indexer(
        index_path=bm25_cfg.get("index_path", "data/indexes/bm25/bm25_index.pkl"),
        corpus_meta_path=bm25_cfg.get("corpus_meta_path", "data/indexes/bm25/bm25_chunk_ids.json"),
        stopwords_path=bm25_cfg.get("stopwords_path", "data/raw/stopwords.txt"),
        k1=float(bm25_cfg.get("k1", 1.2)),
        b=float(bm25_cfg.get("b", 0.65)),
        segment_vietnamese=bool(bm25_cfg.get("segment_vietnamese", True)),
    )

    indexer.build_index(chunks)
    indexer.save()

    logger.info("Hoàn tất lập chỉ mục BM25 thành công!")
    return indexer


def run_qdrant_step(
    cfg: dict,
    chunks: list[LegalChunk] | None = None,
    qdrant_mode: str | None = None,
    device: str = "auto",
    batch_size: int = 32,
    resume: bool = False,
) -> QdrantIndexer:
    """Thực thi Bước 3: Xây dựng chỉ mục Dense Vector Qdrant với BGE-M3."""
    logger.info("=" * 70)
    logger.info("BẮT ĐẦU BƯỚC 3: QDRANT DENSE VECTOR INDEXING (BGE-M3)")
    logger.info("=" * 70)

    chunk_cfg = cfg.get("chunking", {})
    output_chunks_path = Path(chunk_cfg.get("output_chunks_path", "data/processed/chunks.jsonl"))

    if chunks is None:
        chunks = load_chunks_from_file(output_chunks_path)

    idx_cfg = cfg.get("indexing", {})
    qdrant_cfg = idx_cfg.get("qdrant", {})
    manifest_path = idx_cfg.get("manifest_path", "data/manifests/index_manifest.json")

    mode = qdrant_mode or qdrant_cfg.get("mode", "local")

    indexer = QdrantIndexer(
        mode=mode,
        local_path=qdrant_cfg.get("local_path", "data/indexes/qdrant"),
        url=qdrant_cfg.get("url", "http://localhost:6333"),
        collection_name=qdrant_cfg.get("collection_name", "vn_legal_bge_m3"),
        embedding_model_id=qdrant_cfg.get("embedding_model_id", "BAAI/bge-m3"),
        vector_size=int(qdrant_cfg.get("vector_size", 1024)),
        distance=qdrant_cfg.get("distance", "Cosine"),
        normalize_embeddings=bool(qdrant_cfg.get("normalize_embeddings", True)),
        batch_size=batch_size,
        max_seq_length=int(qdrant_cfg.get("max_seq_length", 512)),
        on_disk_payload=bool(qdrant_cfg.get("on_disk_payload", True)),
        manifest_path=manifest_path,
        device=device,
    )

    uploaded_count = indexer.index_chunks(chunks, resume=resume)
    logger.info(
        f"Hoàn tất lập chỉ mục Qdrant! Đã nạp {uploaded_count:,} chunks mới vào collection '{indexer.collection_name}'."
    )
    return indexer


def main() -> int:
    """Hàm điều phối thực thi chính cho CLI Phase 2."""
    args = parse_args()
    logger.info("=" * 70)
    logger.info("KHỞI CHẠY PIPELINE PHASE 2: LEGAL CHUNKING & INDEXING")
    logger.info(f"- Cấu hình: {args.config_path}")
    logger.info(f"- Bước thực hiện: {args.step}")
    logger.info(f"- Sample Only: {args.sample_only}")
    logger.info(f"- Thiết bị: {args.device}")
    logger.info(f"- Batch Size: {args.batch_size}")
    logger.info(f"- Resume: {args.resume}")
    logger.info("=" * 70)

    cfg = load_yaml_config(args.config_path)

    active_chunks: list[LegalChunk] | None = None

    if args.step in ["all", "chunk"]:
        active_chunks = run_chunking_step(
            cfg=cfg,
            sample_only=args.sample_only,
            sample_size=args.sample_size,
        )

    if args.step in ["all", "bm25"]:
        run_bm25_step(cfg=cfg, chunks=active_chunks)

    if args.step in ["all", "qdrant"]:
        run_qdrant_step(
            cfg=cfg,
            chunks=active_chunks,
            qdrant_mode=args.qdrant_mode,
            device=args.device,
            batch_size=args.batch_size,
            resume=args.resume,
        )

    logger.info("=" * 70)
    logger.info("HOÀN TẤT THÀNH CÔNG PHASE 2!")
    logger.info("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
