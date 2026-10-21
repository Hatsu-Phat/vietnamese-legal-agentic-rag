# Hướng dẫn Đóng góp (Contributing Guidelines)

Chào mừng bạn đến với dự án **Vietnamese Legal Agentic RAG System**!  
Để đảm bảo chất lượng kỹ thuật, tính tái lập khoa học và sự phối hợp nhịp nhàng giữa các thành viên trong nhóm AI Engineer, toàn bộ đóng góp vào kho lưu trữ phải tuân thủ nghiêm ngặt các quy tắc dưới đây.

---

## 1. Triết lý Phát triển (Core Philosophy)

1. **Tuân thủ Lộ trình theo Phase (Phase Discipline):**  
   Mọi công việc phải bám sát theo [PROJECT_ROADMAP.md](PROJECT_ROADMAP.md). Không nhảy cóc sang code pipeline phức tạp hay dựng agent khi chưa hoàn thành hợp đồng dữ liệu (schemas), baseline retrieval và bộ tiêu chuẩn kiểm thử của phase tương ứng.
2. **Tách biệt Logic Xác định (Deterministic) khỏi Mô hình Sinh (LLM):**  
   Các quy tắc về ngày hiệu lực văn bản, trạng thái hiệu lực (`status`), kiểm tra sự tồn tại của `citation_id`, và giới hạn số vòng lặp phải được viết bằng Python xác định. Tuyệt đối không phó mặc việc kiểm chứng luật cho phán đoán tự do của LLM.
3. **Bằng chứng là Tối thượng (Citation Grounding & Abstention):**  
   Hệ thống không được phép trả lời phỏng đoán. Mọi phát biểu pháp lý phải có trích dẫn `citation_id` hợp lệ. Nếu thiếu dữ liệu hoặc văn bản mâu thuẫn, hệ thống phải kích hoạt chính sách từ chối trả lời (Abstention).
4. **Tính Tái lập Khoa học (Reproducibility):**  
   Mọi thí nghiệm benchmark retrieval hoặc generation phải có version dữ liệu snapshot, cấu hình rõ ràng trong `configs/` và có thể chạy lại để đối chiếu kết quả.

---

## 2. Quy trình Quản lý Mã nguồn với Git (Git Workflow)

### 2.1 Chiến lược phân nhánh (Branching Strategy)

- **`main`**: Nhánh ổn định, chứa các phiên bản phát hành (release) đã qua nghiệm thu Milestone (M0, M1, ..., M6). Không commit trực tiếp lên `main`.
- **`develop`**: Nhánh tích hợp chính. Tất cả tính năng sau khi review xong sẽ merge vào đây.
- **Nhánh làm việc cá nhân / nhóm:**
  - `feat/<module>-<tên-ngắn>`: Phát triển tính năng mới (ví dụ: `feat/chunker-provision-split`, `feat/retrieval-bgem3`).
  - `fix/<vấn-đề>`: Sửa lỗi (ví dụ: `fix/citation-validator-id-missing`).
  - `experiment/<mã-thí-nghiệm>`: Nhánh chạy thực nghiệm NLP/RAG (ví dụ: `experiment/e01-bm25-vs-dense`).
  - `docs/<chủ-đề>`: Bổ sung tài liệu, ADR, hoặc literature review (ví dụ: `docs/adr-002-reranker`).

### 2.2 Quy ước đặt tên Commit (Conventional Commits)

Định dạng bắt buộc:
```text
<loại>(<phạm vi>): <mô tả ngắn gọn bằng thể mệnh lệnh>

[Nội dung chi tiết nếu có, giải thích lý do thay đổi và ngữ cảnh]

[Tham chiếu Issue / Roadmap task nếu có]
```

- **Các loại commit (`type`):**
  - `feat`: Thêm tính năng mới hoặc module mới.
  - `fix`: Sửa lỗi phát sinh.
  - `docs`: Cập nhật tài liệu, README, ADR.
  - `refactor`: Tái cấu trúc mã nguồn mà không thay đổi logic bên ngoài.
  - `test`: Thêm hoặc cập nhật unit tests, integration tests.
  - `perf`: Tối ưu hiệu năng hoặc độ trễ.
  - `chore`: Cập nhật cấu hình build, dependencies, tooling.
- **Phạm vi (`scope`):**
  - `core`, `common`, `ingestion`, `chunking`, `retrieval`, `agents`, `citation`, `eval`, `api`, `config`.

*Ví dụ commit hợp lệ:*
- `feat(chunking): implement legal provision splitter preserving Điều and Khoản`
- `fix(citation): reject responses containing non-existent chunk IDs`
- `docs(adr): record architectural decision for BGE-M3 and BM25 hybrid search`

---

## 3. Quy chuẩn Viết Code (Coding Standards)

### 3.1 Môi trường và Ngôn ngữ
- Ngôn ngữ: **Python 3.11+**
- Tất cả cấu trúc dữ liệu truyền giữa các module phải là **Pydantic v2 BaseModel** (nằm trong `src/core/`).
- Bắt buộc khai báo Type Annotations (`typing`) cho toàn bộ hàm và phương thức.

### 3.2 Bộ công cụ Kiểm tra Tự động (Quality Gates)
Trước khi tạo Pull Request, lập trình viên bắt buộc phải chạy và vượt qua các lệnh sau:

```bash
# 1. Định dạng mã nguồn theo chuẩn PEP 8 (Line length = 100)
ruff format .

# 2. Kiểm tra lỗi linter và import
ruff check .

# 3. Kiểm tra tính nghiêm ngặt của kiểu dữ liệu (Strict Type Checking)
mypy src

# 4. Chạy toàn bộ Unit Tests
pytest tests/unit
```

### 3.3 Quy tắc Docstrings và Chú thích
- Áp dụng định dạng **Google Style Docstrings** cho mọi class và public function.
- Chú thích mã nguồn cần tập trung giải thích **TẠI SAO** (ngữ cảnh pháp lý hoặc lý do thuật toán), thay vì chỉ nhắc lại code làm gì.
- Giữ nguyên các chú thích tham chiếu điều luật hoặc nguồn bài báo khoa học.

---

## 4. Quy tắc Kiến trúc và Phân định Ranh giới Module

```
src/
├── core/         # Chỉ chứa Pydantic schemas, enums, data contracts (Tuyệt đối không import các module khác)
├── common/       # Tiện ích logging, settings loader, exceptions
├── ingestion/    # Đọc PDF/HTML, hash SHA-256, trích xuất văn bản thô
├── chunking/     # Cắt nhỏ theo Điều/Khoản/Điểm, gắn breadcrumb pháp lý
├── retrieval/    # Dense (BGE-M3), Sparse (BM25), Fusion (RRF), Reranker
├── agents/       # LangGraph state machine, query planner, router
├── citation/     # Citation validator, claim audit, chính sách abstention
├── evaluation/   # Chỉ số đánh giá tự động (Recall@k, MRR, nDCG, RAGAS)
└── api/          # FastAPI web routes & middleware
```

**Nguyên tắc cấm kỵ:**
1. Không import vòng tròn (circular dependency).
2. Module `core` không được phụ thuộc vào các thư viện nặng (như `langgraph`, `torch`, `chromadb`).
3. Không thực hiện gọi LLM tự do bên ngoài các node đã được kiểm soát trong `src/agents/`.
4. Không bao giờ hardcode chuỗi prompt trực tiếp trong hàm nghiệp vụ; đưa prompt vào file template hoặc module quản lý riêng.

---

## 5. Quy tắc An toàn Dữ liệu và Bí mật (Security & Data Hygiene)

- **Tuyệt đối không commit tệp `.env` hoặc chứa API Key vào Git.**  
  Mọi cấu hình nhạy cảm được cấu hình qua `.env.example` và nạp vào mã nguồn bằng `pydantic-settings`.
- **Không đưa dữ liệu thô kích thước lớn vào Git.**  
  Văn bản PDF/HTML thực tế lưu tại `data/raw/` (đã có trong `.gitignore`). Nhóm chỉ commit manifest URL và file mẫu kích thước nhỏ tại `data/sample_laws/`.
- **Phòng chống Prompt Injection:**  
  Coi mọi truy vấn của người dùng và nội dung văn bản bóc tách từ tài liệu là dữ liệu chưa tin cậy (untrusted data). Không truyền trực tiếp kết quả vào system instruction mà không có phân tách ranh giới rõ ràng.

---

## 6. Quy trình Gửi và Thẩm định Pull Request (PR Review)

Mỗi Pull Request đưa vào nhánh `develop` phải thỏa mãn:
1. **Tiêu đề PR:** Theo chuẩn Conventional Commits.
2. **Mô tả PR:**
   - Tóm tắt các thay đổi chính.
   - Liên kết đến Issue hoặc Milestone trong `PROJECT_ROADMAP.md`.
   - Kết quả kiểm thử cục bộ (`ruff`, `mypy`, `pytest`).
3. **Tiêu chuẩn Hoàn thành (Definition of Done - DoD):**
   - [ ] Mã nguồn tuân thủ coding standards và có type hints đầy đủ.
   - [ ] Có bổ sung Unit Test tương ứng trong `tests/unit/`.
   - [ ] Toàn bộ test hiện tại và test mới đều `PASS`.
   - [ ] Đã cập nhật tài liệu liên quan (`README.md`, `docs/`, docstrings).
   - [ ] Được ít nhất một thành viên khác trong nhóm review và Approve.
