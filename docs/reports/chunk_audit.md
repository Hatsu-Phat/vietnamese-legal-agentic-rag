# Báo cáo Kiểm toán Phân mảnh Văn bản Pháp lý (Phase 2 Chunking Audit)

> Báo cáo lịch sử của lần chạy ghi trong tài liệu. Số tests, trạng thái Cloud và tính năng ở thời điểm đó không thay thế hiện trạng trong [README](../../README.md).


> **Thời điểm kiểm toán:** 2026-10-31T13:35:12.778637+00:00
> **Trạng thái:** ✅ ĐẠT CHUẨN
> **Phạm vi đối chiếu:** Team chunks only; not evidence of complete raw-corpus coverage

---

## 1. Tổng quan Phân mảnh (Chunking Overview)

| Chỉ số | Giá trị | Ghi chú |
| :--- | :--- | :--- |
| **Số provision trong tập đối chiếu** | **60,679** | Theo phạm vi đối chiếu ở trên |
| **Tổng số Chunks sinh ra** | **85,115** | Sau khi phân cấp Điều/Khoản |
| **Số Điều phủ được** | **60,679** | Phải đạt 100% |
| **Tỷ lệ phủ Điều luật (Coverage)** | **100.00%** | Yêu cầu nghiêm ngặt 100% |
| **Số Chunk rỗng (word_count = 0)** | **0** | Bắt buộc bằng 0 |
| **Full Article Chunks** | **48,512** | Giữ nguyên toàn bộ Điều |
| **Sub-clause Chunks** | **36,603** | Tách theo Khoản / Fallback |
| **Nội dung trùng băm (Duplicate)** | **648** | |

---

## 2. Phân bố Độ dài Từ (Word Count Distribution)

- **Số từ nhỏ nhất:** 1 từ
- **Số từ lớn nhất:** 417 từ
- **Số từ trung bình:** 206.98 từ
- **Trung vị (Median):** 196 từ

### Biểu đồ phân bổ khoảng từ

| Khoảng số từ | Số lượng Chunks | Tỷ lệ (%) |
| :--- | :--- | :--- |
| **<= 50 từ** | 6,530 | 7.67% |
| **51 - 100 từ** | 13,591 | 15.97% |
| **101 - 200 từ** | 23,331 | 27.41% |
| **201 - 300 từ** | 17,977 | 21.12% |
| **301 - 400 từ** | 23,682 | 27.82% |
| **> 400 từ** | 4 | 0.00% |

---

## 3. Tiêu chí Nghiệm thu & Cam kết Chất lượng

1. **Gold QA Mapping:** Cần đối chiếu riêng với benchmark; coverage provision không chứng minh mọi gold reference tồn tại.
2. **Tách biệt Citation & Embedding:** Trường `text` nguyên văn được bảo toàn thuần khiết; trường `embedding_text` mang đầy đủ breadcrumb phân cấp pháp lý.
3. **Sẵn sàng Lập chỉ mục:** Chunks đã sẵn sàng nạp vào BM25 Lexical Index và Qdrant Dense Vector Store.
