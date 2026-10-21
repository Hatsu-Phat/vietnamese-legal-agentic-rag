# Coding Standards & Engineering Invariants

Dự án: **Vietnamese Legal Agentic RAG**

## 1. Môi trường và Phiên bản
- Python: `3.11+`
- Trình quản lý gói: `pip` / `hatchling` / `uv`
- Cấu hình & Secret: Đọc qua `pydantic-settings` từ `.env`. Không bao giờ ghi cứng secret.

## 2. Tiêu chuẩn viết Code
- **Formatting:** `ruff format .` (Line-length 100)
- **Linting:** `ruff check .`
- **Type Safety:** Type annotations bắt buộc cho tất cả hàm và method (`mypy src --strict`).
- **Docstrings:** Chuẩn Google Style cho mọi module, class, và public function.
- **Exceptions:** Tạo các custom exception trong `src/common/exceptions.py`. Không dùng bare `except:`.

## 3. Quy tắc RAG & Agentic Workflow
- **Legal Chunking:** Giữ nguyên vẹn đơn vị Điều/Khoản/Điểm cùng breadcrumb (Chương > Mục > Điều > Khoản).
- **Retrieval Baseline:** Luôn so sánh Hybrid (BGE-M3 Dense + BM25 Sparse qua RRF) với baseline từng nhánh độc lập.
- **Agent Loops:** Giới hạn vòng lặp retry truy xuất tối đa là 2.
- **Citation Audit:** Câu trả lời bắt buộc phải có `citation_ids` đã được đối chiếu tồn tại trong ngữ cảnh. Bắt buộc kích hoạt `abstain` khi thiếu bằng chứng.
