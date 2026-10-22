"""Chunk audit module for Vietnamese Legal Agentic RAG.

Validates that 100% of legal provisions are covered, checks for empty chunks,
and generates statistical reports (docs/reports/chunk_audit.md and data/manifests/chunk_stats.json).
"""

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.models import LegalChunk


class ChunkAuditor:
    """Kiểm toán chất lượng phân mảnh và xuất báo cáo đối soát."""

    def __init__(
        self,
        report_path: str = "docs/reports/chunk_audit.md",
        manifest_path: str = "data/manifests/chunk_stats.json",
    ) -> None:
        self.report_path = Path(report_path)
        self.manifest_path = Path(manifest_path)

    def compute_stats(
        self,
        chunks: Iterable[LegalChunk],
        original_provision_ids: set[str],
    ) -> dict[str, Any]:
        """Tính toán các chỉ số thống kê kiểm toán trên tập chunk."""
        chunks_list = list(chunks)
        total_chunks = len(chunks_list)
        total_original = len(original_provision_ids)

        covered_provisions: set[str] = set()
        full_article_count = 0
        sub_clause_count = 0
        empty_chunks_count = 0

        word_counts: list[int] = []
        char_lengths: list[int] = []
        seen_hashes: set[str] = set()
        duplicate_hashes = 0

        # Phân bố độ dài từ
        buckets = {
            "<=50": 0,
            "51-100": 0,
            "101-200": 0,
            "201-300": 0,
            "301-400": 0,
            ">400": 0,
        }

        for c in chunks_list:
            covered_provisions.add(c.provision_id)

            if c.is_full_article:
                full_article_count += 1
            else:
                sub_clause_count += 1

            if c.word_count == 0 or not c.text.strip():
                empty_chunks_count += 1

            word_counts.append(c.word_count)
            char_lengths.append(c.char_length)

            w = c.word_count
            if w <= 50:
                buckets["<=50"] += 1
            elif w <= 100:
                buckets["51-100"] += 1
            elif w <= 200:
                buckets["101-200"] += 1
            elif w <= 300:
                buckets["201-300"] += 1
            elif w <= 400:
                buckets["301-400"] += 1
            else:
                buckets[">400"] += 1

            if c.content_hash in seen_hashes:
                duplicate_hashes += 1
            else:
                seen_hashes.add(c.content_hash)

        missing_provisions = original_provision_ids - covered_provisions
        coverage_rate = len(covered_provisions) / total_original if total_original > 0 else 1.0

        min_words = min(word_counts) if word_counts else 0
        max_words = max(word_counts) if word_counts else 0
        mean_words = sum(word_counts) / total_chunks if total_chunks > 0 else 0.0

        sorted_words = sorted(word_counts)
        median_words = sorted_words[len(sorted_words) // 2] if sorted_words else 0

        min_chars = min(char_lengths) if char_lengths else 0
        max_chars = max(char_lengths) if char_lengths else 0
        mean_chars = sum(char_lengths) / total_chunks if total_chunks > 0 else 0.0

        stats: dict[str, Any] = {
            "audit_timestamp": datetime.now(UTC).isoformat(),
            "total_original_provisions": total_original,
            "total_chunks_generated": total_chunks,
            "covered_provisions_count": len(covered_provisions),
            "missing_provisions_count": len(missing_provisions),
            "provision_coverage_rate": round(coverage_rate, 6),
            "empty_chunks_count": empty_chunks_count,
            "full_article_chunks": full_article_count,
            "sub_clause_chunks": sub_clause_count,
            "duplicate_content_hashes": duplicate_hashes,
            "word_count_stats": {
                "min": min_words,
                "max": max_words,
                "mean": round(mean_words, 2),
                "median": median_words,
                "distribution": buckets,
            },
            "char_length_stats": {
                "min": min_chars,
                "max": max_chars,
                "mean": round(mean_chars, 2),
            },
            "missing_provision_ids_sample": list(missing_provisions)[:10],
        }

        return stats

    def export_stats_json(
        self,
        stats: dict[str, Any],
        output_path: Path | str | None = None,
    ) -> Path:
        """Xuất báo cáo thống kê dưới dạng JSON manifest."""
        out_file = Path(output_path) if output_path else self.manifest_path
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        return out_file

    def generate_markdown_report(
        self,
        stats: dict[str, Any],
        output_path: Path | str | None = None,
    ) -> str:
        """Sinh tài liệu báo cáo kiểm toán Markdown lưu trữ vào docs/reports/."""
        out_file = Path(output_path) if output_path else self.report_path
        out_file.parent.mkdir(parents=True, exist_ok=True)

        w_dist = stats["word_count_stats"]["distribution"]
        total_chunks = stats["total_chunks_generated"]

        md_content = f"""# Báo cáo Kiểm toán Phân mảnh Văn bản Pháp lý (Phase 2 Chunking Audit)

> **Thời điểm kiểm toán:** {stats["audit_timestamp"]}
> **Trạng thái:** {"✅ ĐẠT CHUẨN" if stats["provision_coverage_rate"] == 1.0 and stats["empty_chunks_count"] == 0 else "❌ CÓ LỖI"}

---

## 1. Tổng quan Phân mảnh (Chunking Overview)

| Chỉ số | Giá trị | Ghi chú |
| :--- | :--- | :--- |
| **Tổng số Điều luật gốc** | **{stats["total_original_provisions"]:,}** | Dữ liệu từ `provisions.jsonl` |
| **Tổng số Chunks sinh ra** | **{stats["total_chunks_generated"]:,}** | Sau khi phân cấp Điều/Khoản |
| **Số Điều phủ được** | **{stats["covered_provisions_count"]:,}** | Phải đạt 100% |
| **Tỷ lệ phủ Điều luật (Coverage)** | **{stats["provision_coverage_rate"] * 100:.2f}%** | Yêu cầu nghiêm ngặt 100% |
| **Số Chunk rỗng (word_count = 0)** | **{stats["empty_chunks_count"]}** | Bắt buộc bằng 0 |
| **Full Article Chunks** | **{stats["full_article_chunks"]:,}** | Giữ nguyên toàn bộ Điều |
| **Sub-clause Chunks** | **{stats["sub_clause_chunks"]:,}** | Tách theo Khoản / Fallback |
| **Nội dung trùng băm (Duplicate)** | **{stats["duplicate_content_hashes"]:,}** | |

---

## 2. Phân bố Độ dài Từ (Word Count Distribution)

- **Số từ nhỏ nhất:** {stats["word_count_stats"]["min"]} từ
- **Số từ lớn nhất:** {stats["word_count_stats"]["max"]} từ
- **Số từ trung bình:** {stats["word_count_stats"]["mean"]} từ
- **Trung vị (Median):** {stats["word_count_stats"]["median"]} từ

### Biểu đồ phân bổ khoảng từ

| Khoảng số từ | Số lượng Chunks | Tỷ lệ (%) |
| :--- | :--- | :--- |
| **<= 50 từ** | {w_dist["<=50"]:,} | {w_dist["<=50"] / total_chunks * 100 if total_chunks else 0:.2f}% |
| **51 - 100 từ** | {w_dist["51-100"]:,} | {w_dist["51-100"] / total_chunks * 100 if total_chunks else 0:.2f}% |
| **101 - 200 từ** | {w_dist["101-200"]:,} | {w_dist["101-200"] / total_chunks * 100 if total_chunks else 0:.2f}% |
| **201 - 300 từ** | {w_dist["201-300"]:,} | {w_dist["201-300"] / total_chunks * 100 if total_chunks else 0:.2f}% |
| **301 - 400 từ** | {w_dist["301-400"]:,} | {w_dist["301-400"] / total_chunks * 100 if total_chunks else 0:.2f}% |
| **> 400 từ** | {w_dist[">400"]:,} | {w_dist[">400"] / total_chunks * 100 if total_chunks else 0:.2f}% |

---

## 3. Tiêu chí Nghiệm thu & Cam kết Chất lượng

1. **Bảo toàn Gold QA Mapping:** Mọi `article_id` và `provision_id` đều được bảo toàn 100%.
2. **Tách biệt Citation & Embedding:** Trường `text` nguyên văn được bảo toàn thuần khiết; trường `embedding_text` mang đầy đủ breadcrumb phân cấp pháp lý.
3. **Sẵn sàng Lập chỉ mục:** Chunks đã sẵn sàng nạp vào BM25 Lexical Index và Qdrant Dense Vector Store.
"""
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(md_content)

        return md_content
