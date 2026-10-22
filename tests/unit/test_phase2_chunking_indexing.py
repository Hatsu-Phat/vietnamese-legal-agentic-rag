"""Unit tests for Phase 2: Legal-Aware Hierarchical Chunking, BM25 & Qdrant Indexing.

Verifies:
- Short provisions (<= 400 words) remain single chunks with is_full_article=True.
- Long provisions (> 400 words with clauses) split correctly by Clause with legal breadcrumbs.
- Fallback sliding window for unstructured text.
- Deterministic UUIDv5 point IDs.
- BM25 tokenization with pyvi and keyword matching.
- Qdrant in-memory vector storage, payload indexing, metadata filtering and top-k query.
- Chunk auditor coverage calculation and empty chunk detection.
"""

import hashlib
import uuid
from pathlib import Path

import numpy as np
import pytest
from qdrant_client import QdrantClient

from src.chunking.chunk_audit import ChunkAuditor
from src.chunking.fallback_chunker import split_text_sliding_window
from src.chunking.legal_chunker import LegalChunker
from src.core.enums import LegalValidityStatus
from src.core.models import LegalChunk, LegalDocument, LegalProvision
from src.retrieval.bm25_indexer import BM25Indexer
from src.retrieval.qdrant_indexer import QdrantIndexer


class DummyEncoder:
    """Mock embedding encoder for lightning fast and isolated unit tests."""

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def encode(
        self,
        texts: str | list[str],
        batch_size: int = 32,
        show_progress_bar: bool = False,
        normalize_embeddings: bool = True,
        convert_to_numpy: bool = True,
    ) -> np.ndarray:
        input_list = [texts] if isinstance(texts, str) else list(texts)

        vecs = []
        for t in input_list:
            v = np.zeros(self.dim, dtype=np.float32)
            v[0] = 1.0
            seed = sum(ord(c) for c in t) % 100
            v[1] = float(seed) / 100.0
            if normalize_embeddings:
                norm = np.linalg.norm(v)
                if norm > 0:
                    v = v / norm
            vecs.append(v)
        return np.array(vecs)


@pytest.fixture
def sample_document() -> LegalDocument:
    """Fixture tạo một LegalDocument mẫu."""
    return LegalDocument(
        document_id="law_2015_qh13_100",
        document_number="100/2015/QH13",
        title="Bộ luật Hình sự năm 2015",
        document_type="Luật",
        issuing_authority="Quốc hội",
        issued_year=2015,
        status=LegalValidityStatus.UNKNOWN,
        content_hash="abc123hash",
    )


@pytest.fixture
def short_provision() -> LegalProvision:
    """Fixture tạo điều khoản ngắn (< 400 từ)."""
    text = (
        "Bộ luật Hình sự quy định về tội phạm và hình phạt, "
        "nhằm bảo vệ độc lập, chủ quyền, thống nhất và toàn vẹn lãnh thổ của Tổ quốc."
    )
    return LegalProvision(
        provision_id="law_2015_qh13_100__art_1",
        document_id="law_2015_qh13_100",
        document_number="100/2015/QH13",
        article_id="1",
        article_number="Điều 1",
        title="Điều 1. Nhiệm vụ của Bộ luật Hình sự",
        text=text,
        provision_path="100/2015/QH13 > Điều 1",
        char_length=len(text),
        word_count=len(text.split()),
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        status=LegalValidityStatus.UNKNOWN,
    )


@pytest.fixture
def long_provision_with_clauses() -> LegalProvision:
    """Fixture tạo điều khoản dài (> 400 từ) có 4 khoản rõ ràng."""
    c1 = (
        "1. Người nào vô ý làm chết người thì bị phạt cải tạo không giam giữ đến 03 năm hoặc phạt tù từ 01 năm đến 05 năm. "
        * 25
    )
    c2 = "2. Phạm tội làm chết 02 người trở lên thì bị phạt tù từ 03 năm đến 10 năm. " * 25
    c3 = (
        "3. Người chuẩn bị phạm tội này thì bị phạt phạt tiền từ mười triệu đồng đến năm mươi triệu đồng. "
        * 25
    )
    c4 = (
        "4. Người phạm tội còn có thể bị cấm đảm nhiệm chức vụ, cấm hành nghề hoặc làm công việc nhất định. "
        * 25
    )

    full_text = f"Tội vô ý làm chết người quy định như sau:\n\n{c1}\n\n{c2}\n\n{c3}\n\n{c4}"
    return LegalProvision(
        provision_id="law_2015_qh13_100__art_128",
        document_id="law_2015_qh13_100",
        document_number="100/2015/QH13",
        article_id="128",
        article_number="Điều 128",
        title="Điều 128. Tội vô ý làm chết người",
        text=full_text,
        provision_path="100/2015/QH13 > Điều 128",
        char_length=len(full_text),
        word_count=len(full_text.split()),
        content_hash=hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
        status=LegalValidityStatus.UNKNOWN,
    )


# -----------------------------------------------------------------------------
# 1. Tests for Legal-Aware Hierarchical Chunking
# -----------------------------------------------------------------------------
def test_short_provision_single_chunk(
    sample_document: LegalDocument, short_provision: LegalProvision
) -> None:
    """Kiểm tra điều khoản ngắn (<= 400 từ) được giữ nguyên 1 chunk với is_full_article=True."""
    chunker = LegalChunker(max_words_per_chunk=400)
    chunks = chunker.chunk_provision(short_provision, doc=sample_document)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.is_full_article is True
    assert chunk.chunk_index == 0
    assert chunk.total_chunks_in_provision == 1
    assert chunk.clause_number is None
    assert chunk.provision_id == short_provision.provision_id
    assert chunk.article_id == short_provision.article_id
    assert chunk.document_id == sample_document.document_id
    assert chunk.document_type == sample_document.document_type
    assert chunk.issued_year == sample_document.issued_year
    assert chunk.breadcrumb == short_provision.provision_path
    # Kiểm tra tách biệt text nguyên văn và embedding_text
    assert chunk.text == short_provision.text
    assert f"[{chunk.breadcrumb}]" in chunk.embedding_text
    assert chunk.title in chunk.embedding_text


def test_long_provision_clause_splitting(
    sample_document: LegalDocument, long_provision_with_clauses: LegalProvision
) -> None:
    """Kiểm tra điều khoản dài (> 400 từ có Khoản) được tách đúng theo Khoản và giữ breadcrumb."""
    chunker = LegalChunker(max_words_per_chunk=400)
    chunks = chunker.chunk_provision(long_provision_with_clauses, doc=sample_document)

    assert len(chunks) > 1
    for idx, chunk in enumerate(chunks):
        assert chunk.chunk_index == idx
        assert chunk.total_chunks_in_provision == len(chunks)
        assert chunk.is_full_article is False
        assert chunk.provision_id == long_provision_with_clauses.provision_id
        assert chunk.article_id == long_provision_with_clauses.article_id
        assert "Khoản" in chunk.breadcrumb
        assert chunk.word_count > 0
        assert chunk.point_uuid == LegalChunk.generate_point_uuid(chunk.chunk_id)


def test_fallback_sliding_window_unstructured_text() -> None:
    """Kiểm tra fallback_chunker khi văn bản dài không có cấu trúc Khoản."""
    sentence = "Đây là một câu kiểm thử quy tắc phân mảnh văn bản pháp luật tiếng Việt dài. "
    long_text = sentence * 60  # ~840 words
    chunks = split_text_sliding_window(long_text, max_words=400, overlap_words=50)

    assert len(chunks) >= 2
    for c in chunks:
        assert len(c.split()) <= 450
        assert len(c.strip()) > 0


def test_point_uuid_deterministic() -> None:
    """Kiểm tra point_uuid là UUIDv5 tất định sinh từ chunk_id."""
    chunk_id = "law_2015_qh13_100__art_15__c0"
    expected_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id))
    generated_uuid = LegalChunk.generate_point_uuid(chunk_id)

    assert generated_uuid == expected_uuid
    # Chạy lại vẫn phải ra cùng kết quả
    assert LegalChunk.generate_point_uuid(chunk_id) == expected_uuid


# -----------------------------------------------------------------------------
# 2. Tests for BM25 Lexical Indexer
# -----------------------------------------------------------------------------
def test_bm25_tokenization_and_exact_match(tmp_path: Path, short_provision: LegalProvision) -> None:
    """Kiểm tra BM25 indexer tách từ tiếng Việt, lọc từ dừng và tìm kiếm exact match."""
    chunker = LegalChunker(max_words_per_chunk=400)
    chunks = chunker.chunk_provision(short_provision)

    stopwords_file = tmp_path / "stopwords.txt"
    stopwords_file.write_text("và\nvề\nnhằm\n", encoding="utf-8")

    index_file = tmp_path / "bm25_index.pkl"
    meta_file = tmp_path / "bm25_meta.json"

    indexer = BM25Indexer(
        index_path=str(index_file),
        corpus_meta_path=str(meta_file),
        stopwords_path=str(stopwords_file),
        k1=1.2,
        b=0.65,
        segment_vietnamese=True,
    )

    indexer.build_index(chunks)
    indexer.save()

    assert index_file.is_file()
    assert meta_file.is_file()

    # Thử nạp lại từ đĩa
    new_indexer = BM25Indexer(
        index_path=str(index_file),
        corpus_meta_path=str(meta_file),
        stopwords_path=str(stopwords_file),
    )
    new_indexer.load()

    # Truy vấn từ khóa có trong text
    results = new_indexer.query("tội phạm hình phạt", top_k=5)
    assert len(results) > 0
    assert results[0]["chunk_id"] == chunks[0].chunk_id
    assert results[0]["score"] > 0.0


# -----------------------------------------------------------------------------
# 3. Tests for Qdrant Dense Vector Indexer
# -----------------------------------------------------------------------------
def test_qdrant_in_memory_indexing_and_filtering(
    tmp_path: Path,
    sample_document: LegalDocument,
    short_provision: LegalProvision,
) -> None:
    """Kiểm tra Qdrant in-memory nạp point, lọc payload và truy vấn top-k."""
    chunker = LegalChunker(max_words_per_chunk=400)
    chunks = chunker.chunk_provision(short_provision, doc=sample_document)

    client = QdrantClient(":memory:")
    manifest_file = tmp_path / "index_manifest.json"

    dummy_encoder = DummyEncoder(dim=1024)

    indexer = QdrantIndexer(
        mode="memory",
        client=client,
        collection_name="test_legal_chunks",
        embedding_model=dummy_encoder,
        vector_size=1024,
        manifest_path=str(manifest_file),
    )

    uploaded = indexer.index_chunks(chunks)
    assert uploaded == len(chunks)
    assert manifest_file.is_file()

    # Truy vấn không có filter
    res = indexer.query(query_text="nhiệm vụ bộ luật", top_k=5)
    assert len(res) == 1
    assert res[0]["chunk_id"] == chunks[0].chunk_id

    # Truy vấn có filter khớp
    res_filtered = indexer.query(
        query_text="nhiệm vụ",
        filters={"issued_year": 2015, "document_type": "Luật"},
        top_k=5,
    )
    assert len(res_filtered) == 1

    # Truy vấn có filter không khớp
    res_mismatch = indexer.query(
        query_text="nhiệm vụ",
        filters={"issued_year": 1999},
        top_k=5,
    )
    assert len(res_mismatch) == 0


# -----------------------------------------------------------------------------
# 4. Tests for Chunk Auditor
# -----------------------------------------------------------------------------
def test_chunk_auditor_metrics(tmp_path: Path, short_provision: LegalProvision) -> None:
    """Kiểm tra ChunkAuditor tính đúng tỷ lệ phủ và phát hiện lỗi."""
    chunker = LegalChunker(max_words_per_chunk=400)
    chunks = chunker.chunk_provision(short_provision)

    report_path = tmp_path / "chunk_audit.md"
    manifest_path = tmp_path / "chunk_stats.json"

    auditor = ChunkAuditor(report_path=str(report_path), manifest_path=str(manifest_path))
    stats = auditor.compute_stats(chunks, original_provision_ids={short_provision.provision_id})

    assert stats["total_original_provisions"] == 1
    assert stats["total_chunks_generated"] == 1
    assert stats["provision_coverage_rate"] == 1.0
    assert stats["empty_chunks_count"] == 0
    assert stats["full_article_chunks"] == 1
    assert stats["sub_clause_chunks"] == 0

    auditor.export_stats_json(stats, manifest_path)
    auditor.generate_markdown_report(stats, report_path)

    assert manifest_path.is_file()
    assert report_path.is_file()
