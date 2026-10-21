# AGENTS.md — Quy tắc Phát triển và Chuẩn mực Kỹ thuật cho Agent & Developers

> **Dự án:** Vietnamese Legal Agentic RAG System  
> **Áp dụng cho:** Toàn bộ thành viên nhóm phát triển, Pair-programming AI, và Sub-agents.

Tài liệu này định nghĩa các quy tắc cốt lõi (Core Invariants) và ranh giới kiến trúc (Architectural Boundaries) bắt buộc phải tuân thủ khi viết mã trong kho lưu trữ `vietnamese-legal-agentic-rag`.

---

## 1. Nguyên tắc cốt lõi (Core Invariants)

1. **Deterministic Logic tách biệt hoàn toàn khỏi LLM:**
   - Trạng thái pháp lý (`status`), ngày có hiệu lực (`effective_from`, `effective_to`), văn bản sửa đổi/bổ sung, kiểm tra sự tồn tại của `citation_id`, và giới hạn vòng lặp agent **phải là code Python xác định (deterministic)**.
   - Tuyệt đối không để LLM tự phán đoán hoặc bịa đặt tính hiệu lực của văn bản quy phạm pháp luật.

2. **Quy tắc trích dẫn và chống ảo giác (Citation Grounding & Abstention):**
   - Mọi câu trả lời pháp lý phải kèm `claims` và `citation_ids` tương ứng.
   - Nếu bằng chứng tìm được không đủ, mâu thuẫn, hoặc nằm ngoài kho ngữ liệu đã nạp, hệ thống phải kích hoạt chính sách **Abstention** (từ chối trả lời lịch sự hoặc yêu cầu làm rõ), không được tạo ảo giác suy đoán (hallucination).

3. **Schema-First & Strong Typing:**
   - Mọi cấu trúc dữ liệu (`Document`, `Provision`, `Chunk`, `QueryResult`, `AgentState`, `AnswerPayload`) phải được định nghĩa bằng **Pydantic v2 Models** tại `src/core/`.
   - Toàn bộ mã nguồn Python phải có type hints đầy đủ và vượt qua kiểm tra `mypy --strict`.

4. **Kiểm soát vòng lặp Agent (Bounded Retry & State Machine):**
   - Sử dụng **LangGraph** để quản lý luồng trạng thái có định hướng.
   - Số vòng lặp truy xuất lại (`retrieval_retry`) tối đa là **2 vòng** (`MAX_RETRIEVAL_ROUNDS = 2`).
   - Mọi tool call phải có schema đầu vào/đầu ra rõ ràng, timeout cố định và được trace lại.

5. **Bảo mật và Vệ sinh Dữ liệu (Security & Secrets):**
   - Tuyệt đối không hardcode API key, token, hoặc mật khẩu vào mã nguồn. Tất cả phải nạp qua biến môi trường (`.env`) sử dụng `pydantic-settings`.
   - Coi nội dung văn bản truy xuất và câu hỏi người dùng là **dữ liệu không tin cậy (untrusted data)**, áp dụng các biện pháp phòng chống prompt injection.

---

## 2. Quy ước cấu trúc mã nguồn (`src/`)

```
src/
├── core/         # Pydantic Schemas & Domain Entities (không phụ thuộc vào framework bên ngoài)
├── common/       # Config loader, logger (Loguru), exception classes, security utils
├── ingestion/    # Parser PDF/HTML và trích xuất cấu trúc văn bản
├── chunking/     # Bộ chia nhỏ văn bản theo cấu trúc pháp lý (Điều/Khoản/Điểm) kèm breadcrumb
├── retrieval/    # Hybrid search: Dense (BGE-M3), Sparse (BM25), Fusion (RRF), Reranker
├── agents/       # LangGraph state machine, query planner, router, workflow nodes
├── citation/     # Citation validator, claim audit, và abstention policy
├── evaluation/   # Đo lường định lượng (Recall@k, MRR, nDCG, RAGAS metrics)
└── api/          # FastAPI application, route handlers, error middleware
```

**Ranh giới phụ thuộc:**
- `core` và `common` là các tầng cơ sở, không import từ các module chức năng cấp cao (`retrieval`, `agents`, `api`).
- `agents` điều phối các tool trong `retrieval` và `citation` qua interface định trước.
- `api` chỉ gọi vào `agents` hoặc service orchestration, không chứa logic nghiệp vụ NLP phức tạp.

---

## 3. Quy trình làm việc và chất lượng mã (Quality Gates)

Trước khi gửi Pull Request hoặc tích hợp mã mới:
1. **Linter & Formatter:** Chạy `ruff check .` và `ruff format .` — không có cảnh báo/lỗi.
2. **Type Checking:** Chạy `mypy src` — 0 lỗi type.
3. **Testing:** Chạy `pytest tests/unit` — 100% tests vượt qua.
4. **No Secrets:** Đảm bảo không có tệp nhạy cảm hoặc key trong commit.
