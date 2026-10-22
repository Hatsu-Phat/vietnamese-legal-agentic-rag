"""Legal-Aware Hierarchical Chunker for Vietnamese legal provisions.

Splits provisions according to the legal hierarchy: Điều > Khoản > Điểm.
Guarantees 100% gold benchmark mapping preservation (provision_id & article_id).
Separates citation text from embedding text with breadcrumb context.
"""

import hashlib
import re
from collections.abc import Iterable
from typing import Any

from src.chunking.fallback_chunker import split_text_sliding_window
from src.core.enums import LegalValidityStatus
from src.core.models import LegalChunk, LegalDocument, LegalProvision

# Regex nhận diện ranh giới đầu dòng của các Khoản: "1. ", "2. ", "1) ", "2) "
CLAUSE_REGEX = re.compile(r"^\s*(\d+)[\.\)]\s+", re.MULTILINE)


class LegalChunker:
    """Bộ phân mảnh phân cấp pháp lý cho Điều khoản luật Việt Nam."""

    def __init__(
        self,
        max_words_per_chunk: int = 400,
        overlap_words: int = 50,
        min_words_per_chunk: int = 15,
        include_breadcrumb_in_embedding_text: bool = True,
    ) -> None:
        self.max_words_per_chunk = max_words_per_chunk
        self.overlap_words = overlap_words
        self.min_words_per_chunk = min_words_per_chunk
        self.include_breadcrumb_in_embedding_text = include_breadcrumb_in_embedding_text

    def chunk_provision(
        self,
        provision: LegalProvision,
        doc: LegalDocument | None = None,
    ) -> list[LegalChunk]:
        """Phân mảnh một LegalProvision thành một hoặc nhiều LegalChunk.

        Args:
            provision: Điều luật cần phân mảnh.
            doc: Văn bản cha tương ứng để bổ sung metadata phân loại.

        Returns:
            Danh sách các LegalChunk được đánh chỉ mục đầy đủ.
        """
        doc_type = doc.document_type if doc else None
        issuing_auth = doc.issuing_authority if doc else None
        issued_yr = doc.issued_year if doc else None
        status = doc.status if doc else (provision.status or LegalValidityStatus.UNKNOWN)

        # Trường hợp 1: Điều khoản ngắn (<= max_words_per_chunk) -> Giữ nguyên 1 chunk duy nhất
        if provision.word_count <= self.max_words_per_chunk:
            return [
                self._build_single_article_chunk(
                    provision, doc_type, issuing_auth, issued_yr, status
                )
            ]

        # Trường hợp 2: Điều khoản dài (> max_words_per_chunk) -> Tách phân cấp theo Khoản
        raw_chunks_data = self._split_hierarchical(provision)

        total_chunks = len(raw_chunks_data)
        legal_chunks: list[LegalChunk] = []

        for idx, item in enumerate(raw_chunks_data):
            chunk_id = f"{provision.provision_id}__c{idx}"
            point_uuid = LegalChunk.generate_point_uuid(chunk_id)
            chunk_text = item["text"]
            breadcrumb = item["breadcrumb"]
            clause_number = item.get("clause_number")
            is_full_article = total_chunks == 1

            if self.include_breadcrumb_in_embedding_text:
                embedding_text = f"[{breadcrumb}] {provision.title}\n{chunk_text}"
            else:
                embedding_text = f"{provision.title}\n{chunk_text}"

            chunk = LegalChunk(
                chunk_id=chunk_id,
                point_uuid=point_uuid,
                document_id=provision.document_id,
                document_number=provision.document_number,
                provision_id=provision.provision_id,
                article_id=provision.article_id,
                chunk_index=idx,
                total_chunks_in_provision=total_chunks,
                is_full_article=is_full_article,
                clause_number=clause_number,
                breadcrumb=breadcrumb,
                title=provision.title,
                text=chunk_text,
                embedding_text=embedding_text,
                word_count=len(chunk_text.split()),
                char_length=len(chunk_text),
                content_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                is_duplicate_content=False,
                document_type=doc_type,
                issuing_authority=issuing_auth,
                issued_year=issued_yr,
                status=status,
            )
            legal_chunks.append(chunk)

        return legal_chunks

    def _build_single_article_chunk(
        self,
        provision: LegalProvision,
        doc_type: str | None,
        issuing_auth: str | None,
        issued_yr: int | None,
        status: LegalValidityStatus,
    ) -> LegalChunk:
        """Tạo chunk trọn vẹn cho điều khoản ngắn."""
        chunk_id = f"{provision.provision_id}__c0"
        point_uuid = LegalChunk.generate_point_uuid(chunk_id)
        breadcrumb = provision.provision_path
        chunk_text = provision.text

        if self.include_breadcrumb_in_embedding_text:
            embedding_text = f"[{breadcrumb}] {provision.title}\n{chunk_text}"
        else:
            embedding_text = f"{provision.title}\n{chunk_text}"

        return LegalChunk(
            chunk_id=chunk_id,
            point_uuid=point_uuid,
            document_id=provision.document_id,
            document_number=provision.document_number,
            provision_id=provision.provision_id,
            article_id=provision.article_id,
            chunk_index=0,
            total_chunks_in_provision=1,
            is_full_article=True,
            clause_number=None,
            breadcrumb=breadcrumb,
            title=provision.title,
            text=chunk_text,
            embedding_text=embedding_text,
            word_count=provision.word_count,
            char_length=provision.char_length,
            content_hash=provision.content_hash,
            is_duplicate_content=False,
            document_type=doc_type,
            issuing_authority=issuing_auth,
            issued_year=issued_yr,
            status=status,
        )

    def _split_hierarchical(self, provision: LegalProvision) -> list[dict[str, Any]]:
        """Phân tích và tách điều luật dài theo cấu trúc Khoản hoặc fallback sliding window."""
        text = provision.text
        matches = list(CLAUSE_REGEX.finditer(text))

        # Nếu không tìm thấy cấu trúc Khoản (1., 2...), dùng fallback_chunker
        if not matches:
            slices = split_text_sliding_window(
                text,
                max_words=self.max_words_per_chunk,
                overlap_words=self.overlap_words,
                min_words=self.min_words_per_chunk,
            )
            results: list[dict[str, Any]] = []
            for s_idx, s_text in enumerate(slices):
                part_breadcrumb = (
                    f"{provision.provision_path} (phần {s_idx + 1})"
                    if len(slices) > 1
                    else provision.provision_path
                )
                results.append(
                    {
                        "text": s_text,
                        "breadcrumb": part_breadcrumb,
                        "clause_number": None,
                    }
                )
            return results

        # Tách intro_header nếu có trước Khoản 1
        intro_header = text[: matches[0].start()].strip()

        # Thu thập các Khoản
        clauses: list[dict[str, str]] = []
        for i, match in enumerate(matches):
            c_num = match.group(1)
            c_start = match.start()
            c_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            c_text = text[c_start:c_end].strip()
            clauses.append({"num": c_num, "text": c_text})

        # Nếu có intro_header, gắn vào Khoản đầu tiên để giữ ngữ cảnh dẫn nhập
        if intro_header and clauses:
            clauses[0]["text"] = f"{intro_header}\n\n{clauses[0]['text']}"

        # Gom nhóm các Khoản liên tiếp theo word_count
        grouped_results: list[dict[str, Any]] = []
        current_group: list[dict[str, str]] = []
        current_words = 0

        for clause in clauses:
            c_words = len(clause["text"].split())

            # Trường hợp 1 bản thân Khoản đó dài hơn max_words_per_chunk
            if c_words > self.max_words_per_chunk:
                # Flush nhóm trước đó nếu có
                if current_group:
                    grouped_results.append(
                        self._flush_group(current_group, provision.provision_path)
                    )
                    current_group = []
                    current_words = 0

                # Tách riêng Khoản này bằng fallback sliding window
                sub_slices = split_text_sliding_window(
                    clause["text"],
                    max_words=self.max_words_per_chunk,
                    overlap_words=self.overlap_words,
                    min_words=self.min_words_per_chunk,
                )
                for sub_idx, sub_text in enumerate(sub_slices):
                    sub_breadcrumb = (
                        f"{provision.provision_path} > Khoản {clause['num']} (phần {sub_idx + 1})"
                        if len(sub_slices) > 1
                        else f"{provision.provision_path} > Khoản {clause['num']}"
                    )
                    grouped_results.append(
                        {
                            "text": sub_text,
                            "breadcrumb": sub_breadcrumb,
                            "clause_number": f"Khoản {clause['num']}",
                        }
                    )
                continue

            # Khoản vừa vặn, kiểm tra gom nhóm
            if current_words + c_words <= self.max_words_per_chunk:
                current_group.append(clause)
                current_words += c_words
            else:
                if current_group:
                    grouped_results.append(
                        self._flush_group(current_group, provision.provision_path)
                    )
                current_group = [clause]
                current_words = c_words

        if current_group:
            grouped_results.append(self._flush_group(current_group, provision.provision_path))

        return grouped_results

    def _flush_group(
        self,
        group: list[dict[str, str]],
        provision_path: str,
    ) -> dict[str, Any]:
        """Tạo bản ghi dữ liệu chunk từ một nhóm Khoản liên tiếp."""
        if len(group) == 1:
            c_num = group[0]["num"]
            clause_number = f"Khoản {c_num}"
            breadcrumb = f"{provision_path} > Khoản {c_num}"
            text = group[0]["text"]
        else:
            first_num = group[0]["num"]
            last_num = group[-1]["num"]
            clause_number = f"Khoản {first_num}-{last_num}"
            breadcrumb = f"{provision_path} > Khoản {first_num}-{last_num}"
            text = "\n\n".join(c["text"] for c in group)

        return {
            "text": text,
            "breadcrumb": breadcrumb,
            "clause_number": clause_number,
        }

    def chunk_corpus(
        self,
        provisions: Iterable[LegalProvision],
        doc_map: dict[str, LegalDocument] | None = None,
    ) -> list[LegalChunk]:
        """Xử lý toàn bộ danh sách provisions và trả về danh sách LegalChunk."""
        all_chunks: list[LegalChunk] = []
        doc_lookup = doc_map or {}
        for prov in provisions:
            doc = doc_lookup.get(prov.document_id)
            chunks = self.chunk_provision(prov, doc=doc)
            all_chunks.extend(chunks)
        return all_chunks
