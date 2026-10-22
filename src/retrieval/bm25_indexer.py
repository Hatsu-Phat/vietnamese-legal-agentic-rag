"""BM25 Lexical Indexer for Vietnamese Legal Documents.

Uses pyvi for Vietnamese word segmentation and rank-bm25 for inverted index scoring.
Supports custom stopwords filtering and serialization to disk.
"""

import json
import pickle
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pyvi import ViTokenizer
from rank_bm25 import BM25Okapi

from src.common.logger import logger
from src.core.models import LegalChunk


class BM25Indexer:
    """Chỉ mục từ khóa BM25 cho các văn bản quy phạm pháp luật tiếng Việt."""

    def __init__(
        self,
        index_path: str = "data/indexes/bm25/bm25_index.pkl",
        corpus_meta_path: str = "data/indexes/bm25/bm25_chunk_ids.json",
        stopwords_path: str = "data/raw/stopwords.txt",
        k1: float = 1.2,
        b: float = 0.65,
        segment_vietnamese: bool = True,
    ) -> None:
        self.index_path = Path(index_path)
        self.corpus_meta_path = Path(corpus_meta_path)
        self.stopwords_path = Path(stopwords_path)
        self.k1 = k1
        self.b = b
        self.segment_vietnamese = segment_vietnamese
        self.stopwords = self._load_stopwords(self.stopwords_path)
        self.bm25: BM25Okapi | None = None
        self.corpus_meta: list[dict[str, Any]] = []

    def _load_stopwords(self, path: Path) -> set[str]:
        """Tải danh sách từ dừng tiếng Việt từ file txt."""
        if not path.is_file():
            logger.warning(f"Không tìm thấy file stopwords tại {path}, tiếp tục với tập rỗng.")
            return set()
        stopwords: set[str] = set()
        with open(path, encoding="utf-8") as f:
            for line in f:
                word = line.strip().lower()
                if word:
                    stopwords.add(word)
                    # Thêm cả biến thể nối dấu gạch dưới của từ ghép
                    stopwords.add(word.replace(" ", "_"))
        return stopwords

    def tokenize(self, text: str) -> list[str]:
        """Tách từ tiếng Việt và loại bỏ từ dừng, ký tự đặc biệt."""
        cleaned = text.lower().strip()
        tokenized_text = ViTokenizer.tokenize(cleaned) if self.segment_vietnamese else cleaned

        # Tách các từ/cụm từ (giữ lại ký tự chữ cái, số và dấu gạch dưới kết nối từ ghép)
        raw_tokens = re.findall(r"[\w]+", tokenized_text)
        filtered_tokens = [
            t for t in raw_tokens if t not in self.stopwords and (len(t) > 1 or t.isalnum())
        ]
        return filtered_tokens

    def build_index(self, chunks: Iterable[LegalChunk]) -> None:
        """Xây dựng chỉ mục BM25 từ danh sách LegalChunk."""
        tokenized_corpus: list[list[str]] = []
        self.corpus_meta = []

        logger.info("Bắt đầu tách từ và chuẩn bị corpus cho BM25 Index...")
        for chunk in chunks:
            tokens = self.tokenize(chunk.embedding_text)
            tokenized_corpus.append(tokens)
            self.corpus_meta.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "point_uuid": chunk.point_uuid,
                    "provision_id": chunk.provision_id,
                    "article_id": chunk.article_id,
                    "document_id": chunk.document_id,
                    "document_number": chunk.document_number,
                    "breadcrumb": chunk.breadcrumb,
                    "title": chunk.title,
                }
            )

        logger.info(
            f"Khởi tạo BM25Okapi trên {len(tokenized_corpus):,} chunks (k1={self.k1}, b={self.b})..."
        )
        self.bm25 = BM25Okapi(tokenized_corpus, k1=self.k1, b=self.b)

        # Tránh lỗi điểm âm (negative IDF) kinh điển của BM25Okapi đối với từ xuất hiện phổ biến
        for word, val in self.bm25.idf.items():
            if val <= 0.0:
                self.bm25.idf[word] = 0.01

        logger.info("Hoàn tất xây dựng chỉ mục BM25!")

    def save(self) -> None:
        """Lưu chỉ mục BM25 và metadata tra cứu xuống đĩa."""
        if self.bm25 is None:
            raise ValueError("Chưa khởi tạo BM25 index, không thể lưu.")

        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.corpus_meta_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.index_path, "wb") as f:
            pickle.dump(self.bm25, f, protocol=pickle.HIGHEST_PROTOCOL)

        with open(self.corpus_meta_path, "w", encoding="utf-8") as f:
            json.dump(self.corpus_meta, f, ensure_ascii=False, indent=2)

        logger.info(f"Đã lưu BM25 index tại: {self.index_path}")
        logger.info(f"Đã lưu BM25 chunk metadata tại: {self.corpus_meta_path}")

    def load(self) -> None:
        """Tải chỉ mục BM25 và metadata tra cứu từ đĩa."""
        if not self.index_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy index tại {self.index_path}")
        if not self.corpus_meta_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy metadata tại {self.corpus_meta_path}")

        with open(self.index_path, "rb") as f:
            self.bm25 = pickle.load(f)

        with open(self.corpus_meta_path, encoding="utf-8") as f:
            self.corpus_meta = json.load(f)

        logger.info(f"Đã nạp BM25 index ({len(self.corpus_meta):,} chunks) từ {self.index_path}")

    def query(self, query_text: str, top_k: int = 10) -> list[dict[str, Any]]:
        """Truy vấn tìm kiếm từ khóa với BM25.

        Args:
            query_text: Nội dung câu hỏi/truy vấn.
            top_k: Số lượng kết quả cần trả về.

        Returns:
            Danh sách các chunk khớp nhất kèm điểm số BM25.
        """
        if self.bm25 is None or not self.corpus_meta:
            raise ValueError("BM25 index chưa được nạp hoặc chưa được khởi tạo.")

        tokenized_query = self.tokenize(query_text)
        if not tokenized_query:
            return []

        doc_scores = self.bm25.get_scores(tokenized_query)
        # Sắp xếp chỉ số theo điểm số giảm dần
        top_indices = sorted(range(len(doc_scores)), key=lambda i: doc_scores[i], reverse=True)[
            :top_k
        ]

        results: list[dict[str, Any]] = []
        for idx in top_indices:
            score = float(doc_scores[idx])
            if score <= 0.0:
                continue
            item = dict(self.corpus_meta[idx])
            item["score"] = round(score, 4)
            results.append(item)

        return results
