# ADR-001: Lựa chọn Phạm vi MVP và Stack Công nghệ

- **Trạng thái:** Được chấp thuận (Accepted)
- **Ngày:** 2026-10-21
- **Người đề xuất:** AI Engineer Team
- **Tài liệu căn cứ:** [PROJECT_ROADMAP.md](../../PROJECT_ROADMAP.md), Đề cương DSIT201

---

## 1. Ngữ cảnh (Context)

Bài toán xây dựng hệ thống hỏi đáp văn bản quy phạm pháp luật Việt Nam (Vietnamese Legal QA) đòi hỏi độ chính xác cao, tính cập nhật về hiệu lực thời gian và bằng chứng kiểm chứng được (grounded generation). Các hệ thống RAG thông thường dạng pipeline tuyến tính gặp các vấn đề lớn:
1. **Ảo giác số điều luật và nội dung:** LLM tự phán đoán hoặc gán sai số Điều/Khoản.
2. **Sai lệch hiệu lực thời gian:** Dẫn các văn bản đã bị sửa đổi, bổ sung hoặc bãi bỏ.
3. **Mất ngữ cảnh phân cấp:** Cắt đoạn cố định (fixed-size chunking) làm đứt gãy quy phạm pháp luật (Điều, Khoản, Điểm).
4. **Không có khả năng tự kiểm tra:** Trả lời một chiều mà không có cơ chế rà soát lại (self-reflection / audit) hay từ chối trả lời (abstention) khi thiếu dữ liệu.

---

## 2. Quyết định Kiến trúc (Decisions)

### 2.1 Phạm vi MVP (Scope)
- **Corpus ban đầu:** Tập trung vào một nhóm lĩnh vực pháp luật hẹp nhưng có nguồn chính thức, cấu trúc rõ ràng và dễ kiểm tra hiệu lực (ví dụ: Luật Doanh nghiệp 2020 và các nghị định hướng dẫn liên quan).
- **Mục tiêu chính:** Chứng minh được tính hiệu quả của cơ chế **Agentic RAG có kiểm soát** (controlled agentic RAG) kết hợp **Hybrid Retrieval** và **Citation Verification**.

### 2.2 Stack Công nghệ cốt lõi
1. **Ngôn ngữ & Runtime:** Python 3.11+
   - *Lý do:* Hệ sinh thái NLP/AI hiện đại, hỗ trợ Pydantic v2, LangGraph, typing mạnh.
2. **Schema & API:** FastAPI + Pydantic v2
   - *Lý do:* Ràng buộc dữ liệu nghiêm ngặt ở tầng boundary (request/response), sinh OpenAPI docs tự động.
3. **Retrieval (Hybrid Search):**
   - **Dense:** `BAAI/bge-m3` (Hỗ trợ đa ngôn ngữ, ngữ cảnh dài đến 8192 tokens, biểu diễn ngữ nghĩa tốt).
   - **Sparse:** `rank_bm25` (Bảo toàn khả năng tìm kiếm từ khóa chính xác, số hiệu văn bản, thuật ngữ pháp lý).
   - **Rank Fusion:** Reciprocal Rank Fusion (RRF) làm baseline hợp nhất danh sách.
   - **Reranker (Phase nâng cao):** `BAAI/bge-reranker-v2-m3` (Chỉ kích hoạt khi thực nghiệm chứng minh cải thiện top-k rõ rệt bù đắp được latency).
4. **Agent Orchestration:** **LangGraph**
   - *Lý do:* Mô hình State Graph linh hoạt, hỗ trợ conditional routing, quản lý trạng thái có cấu trúc, giới hạn số vòng lặp (`bounded retry`) và tích hợp các deterministic audit nodes.
5. **Lưu trữ (Storage):**
   - Vector Store: **ChromaDB** cho local development và MVP.
   - Metadata Store: **SQLite** lưu trữ quan hệ hiệu lực văn bản, audit trail và snapshot ingestion.

---

## 3. Hệ quả (Consequences)

### Tích cực
- Tách bạch rõ ràng giữa logic xác định (deterministic checks) và mô hình sinh (LLM).
- Giảm thiểu tối đa ảo giác nhờ quy tắc bắt buộc citation ID và cơ chế abstention.
- Đảm bảo tính tái lập (reproducibility) cho các thí nghiệm khoa học và báo cáo học thuật.

### Thách thức cần xử lý
- Cần xây dựng parser riêng cho cấu trúc Điều/Khoản tiếng Việt.
- BGE-M3 kích thước khá lớn (~2.2GB), cần tính toán tài nguyên phần cứng (VRAM/RAM) trong quá trình benchmark.
