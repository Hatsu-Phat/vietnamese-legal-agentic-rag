# Báo cáo Kiểm toán Dữ liệu Pháp luật (Phase 1 — Data Audit Report)

> **Dự án:** Vietnamese Legal Agentic RAG System
> **Thời điểm kiểm toán:** `2026-10-21T13:48:09.157130+00:00`
> **Phiên bản Ingestion:** `1.0.0`


---

## 1. Tổng quan Quy mô Kho Ngữ liệu

| Chỉ số | Giá trị | Ghi chú kỹ thuật |
| :--- | :--- | :--- |
| **Tổng số Văn bản (`LegalDocument`)** | **3,271** | Nạp từ `legal_corpus.json` |
| **Tổng số Điều khoản (`LegalProvision`)** | **61,068** | Chuẩn hóa toàn văn Điều (giữ Khoản/Điểm) |
| **Tổng số từ (Word Count)** | **17,682,365** | Tách theo khoảng trắng Unicode NFC |
| **Tổng số ký tự (Character Count)** | **79,752,497** | Không tính ký tự điều khiển vô hình |
| **Số từ trung bình / Điều khoản** | **289.55** | Độ dài trung bình |
| **Khoảng số từ (Min - Max)** | **1 - 55,368** | Cần chia nhỏ Khoản cho Điều > 512 từ |
| **Điều khoản rỗng (`empty_text`)** | **0** | Đã loại khỏi danh sách indexing |
| **Điều khoản trùng mã băm (`duplicate_hash`)** | **919** | Điều khoản nội dung hoàn toàn trùng lặp |

---

## 2. Phân phối Độ dài Điều khoản (Đối chiếu Bảng I — arXiv:2409.13699v1)

Bảng phân phối độ dài các điều khoản pháp lý theo số từ để chuẩn bị cho chiến lược chunking hierarchical provision và context window của mô hình `BAAI/bge-m3`:

| Khoảng độ dài (Số từ) | Số lượng Điều khoản | Tỷ lệ (%) | Nhận xét kiến trúc RAG |
| :--- | :--- | :--- | :--- |
| **< 100 từ** | 15,763 | 25.81% | Điều ngắn, vừa vặn 1 single embedding chunk |
| **101 - 256 từ** | 23,126 | 37.87% | Kích thước lý tưởng cho BGE-M3 Dense retrieval |
| **257 - 512 từ** | 14,141 | 23.16% | Giữ nguyên vẹn Điều kèm breadcrumb |
| **≥ 513 từ** | 8,038 | 13.16% | **Cần chia theo Khoản** ở Phase 2 để tránh mất thông tin |
| **Tổng cộng** | **61,068** | **100.0%** | Toàn bộ điều khoản hợp lệ |

---

## 3. Độ bao phủ Metadata & Tuân thủ Nguyên tắc Pháp lý

Hệ thống tuân thủ nghiêm ngặt nguyên tắc **Deterministic Logic**: tuyệt đối không suy diễn văn bản còn hiệu lực khi chưa có căn cứ chính thức từ cơ sở dữ liệu quốc gia.

| Thuộc tính Metadata | Tỷ lệ bao phủ (%) | Quy tắc xử lý |
| :--- | :--- | :--- |
| **Năm ban hành (`issued_year`)** | **100.0%** | Trích xuất xác định từ chuỗi số hiệu |
| **Loại văn bản (`document_type`)** | **99.8%** | Phân loại chuẩn: Luật, Nghị định, Thông tư... |
| **Cơ quan ban hành (`issuing_authority`)** | **99.8%** | Ánh xạ từ hậu tố cơ quan ban hành |
| **Tỷ lệ trạng thái `UNKNOWN` (`status`)** | **100.0%** | **Bắt buộc = 100%** (vì nguồn raw không có ngày hiệu lực) |

### Phân bố Loại văn bản:
- **Thông tư**: 1,900 văn bản
- **Nghị định**: 848 văn bản
- **Thông tư liên tịch**: 218 văn bản
- **Quyết định**: 199 văn bản
- **Luật**: 99 văn bản
- **Không xác định**: 7 văn bản

---

## 4. Kiểm toán Tập Câu hỏi Đánh giá (`qa_dev` & `qa_test`)

### Tập Phát triển (Dev Split — `qa_dev.jsonl`)
- **Tổng số câu hỏi:** 256
- **Tổng số Điều luật chuẩn (Gold Provisions):** 258
- **Số Điều luật tìm thấy trong Corpus:** 258 / 258 (100.00%)
- **Số câu hỏi phủ đủ 100% căn cứ:** 256 / 256 (100.00%)

### Tập Kiểm thử (Test Split — `qa_test.jsonl`)
- **Tổng số câu hỏi:** 599
- **Tổng số Điều luật chuẩn (Gold Provisions):** 610
- **Số Điều luật tìm thấy trong Corpus:** 610 / 610 (100.00%)
- **Số câu hỏi phủ đủ 100% căn cứ:** 599 / 599 (100.00%)

---

## 5. Kết luận Nghiệm thu Phase 1

1. **Chuẩn hóa dữ liệu:** Đã hoàn tất xuất `documents.jsonl`, `provisions.jsonl`, `source_manifest.csv`, `sample_corpus_subset.json` và `gold_eval`.
2. **Tính toàn vẹn:** 100% văn bản và điều khoản có mã băm SHA-256 tất định.
3. **Cấu trúc điều khoản:** Giữ nguyên vẹn toàn văn Điều kèm ký tự xuống dòng (`\n`) để phục vụ trích xuất Khoản/Điểm ở Phase 2.
4. **Sẵn sàng cho Phase 2:** Kho dữ liệu sẵn sàng cho việc xây dựng chỉ mục BGE-M3 Dense và BM25 Sparse.
