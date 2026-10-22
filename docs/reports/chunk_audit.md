# Báo cáo Kiểm toán Phân mảnh Văn bản Pháp lý (Phase 2 Chunking Audit)

> **Thời điểm kiểm toán:** 2026-10-21T17:15:49.573668+00:00
> **Trạng thái:** ✅ ĐẠT CHUẨN

---

## 1. Tổng quan Phân mảnh (Chunking Overview)

| Chỉ số | Giá trị | Ghi chú |
| :--- | :--- | :--- |
| **Tổng số Điều luật gốc** | **60,679** | Dữ liệu từ `provisions.jsonl` |
| **Tổng số Chunks sinh ra** | **85,745** | Sau khi phân cấp Điều/Khoản |
| **Số Điều phủ được** | **60,679** | Phải đạt 100% |
| **Tỷ lệ phủ Điều luật (Coverage)** | **100.00%** | Yêu cầu nghiêm ngặt 100% |
| **Số Chunk rỗng (word_count = 0)** | **0** | Bắt buộc bằng 0 |
| **Full Article Chunks** | **48,766** | Giữ nguyên toàn bộ Điều |
| **Sub-clause Chunks** | **36,979** | Tách theo Khoản / Fallback |
| **Nội dung trùng băm (Duplicate)** | **1,278** | |

---

## 2. Phân bố Độ dài Từ (Word Count Distribution)

- **Số từ nhỏ nhất:** 1 từ
- **Số từ lớn nhất:** 417 từ
- **Số từ trung bình:** 207.22 từ
- **Trung vị (Median):** 196 từ

### Biểu đồ phân bổ khoảng từ

| Khoảng số từ | Số lượng Chunks | Tỷ lệ (%) |
| :--- | :--- | :--- |
| **<= 50 từ** | 6,548 | 7.64% |
| **51 - 100 từ** | 13,667 | 15.94% |
| **101 - 200 từ** | 23,479 | 27.38% |
| **201 - 300 từ** | 18,120 | 21.13% |
| **301 - 400 từ** | 23,927 | 27.90% |
| **> 400 từ** | 4 | 0.00% |

---

## 3. Tiêu chí Nghiệm thu & Cam kết Chất lượng

1. **Bảo toàn Gold QA Mapping:** Mọi `article_id` và `provision_id` đều được bảo toàn 100%.
2. **Tách biệt Citation & Embedding:** Trường `text` nguyên văn được bảo toàn thuần khiết; trường `embedding_text` mang đầy đủ breadcrumb phân cấp pháp lý.
3. **Sẵn sàng Lập chỉ mục:** Chunks đã sẵn sàng nạp vào BM25 Lexical Index và Qdrant Dense Vector Store.
