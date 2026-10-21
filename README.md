# Vietnamese Legal Agentic RAG System

> **Hệ thống hỏi đáp tra cứu văn bản quy phạm pháp luật Việt Nam có kiểm chứng sử dụng Agentic RAG (LangGraph + BGE-M3 + BM25 + Citation Verification)**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Framework](https://img.shields.io/badge/Orchestration-LangGraph-purple.svg)](https://github.com/langchain-ai/langgraph)
[![Embeddings](https://img.shields.io/badge/Dense%20Model-BAAI%2Fbge--m3-orange.svg)](https://huggingface.co/BAAI/bge-m3)
[![Search](https://img.shields.io/badge/Keyword%20Search-BM25-green.svg)](https://github.com/dorianbrown/rank_bm25)
[![API](https://img.shields.io/badge/API-FastAPI-teal.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 1. Giới thiệu Dự án (Executive Summary)

Dự án **Vietnamese Legal Agentic RAG** là một hệ thống AI Engineer học thuật kết hợp triển khai thực tế (Capstone Project) nhằm giải quyết bài toán hỏi đáp, tra cứu và giải thích văn bản quy phạm pháp luật Việt Nam bằng ngôn ngữ tự nhiên.

Khác với các hệ thống RAG thông thường dạng pipeline tuyến tính (dễ gặp hiện tượng bịa đặt số điều luật, dẫn văn bản hết hiệu lực hoặc ảo giác nội dung), dự án áp dụng mô hình **Controlled Agentic RAG**:
- Quản trị luồng trạng thái qua **LangGraph** với số vòng lặp có chặn (`bounded retry <= 2`).
- Tách bạch tuyệt đối giữa **logic xác định (deterministic validation)** và **mô hình sinh ngôn ngữ (LLM)**.
- Bắt buộc kiểm định trích dẫn (`citation grounding`) và kích hoạt chính sách từ chối trả lời (`abstention policy`) khi không đủ căn cứ pháp lý tin cậy.

> [!NOTE]
> **Hiện trạng dự án:** Đang ở **Phase 0 — Thiết lập tiêu chuẩn, quy tắc dự án và khung kiến trúc (Rules & Architecture Setup)**. Toàn bộ quy chuẩn thư mục, hợp đồng dữ liệu và quy tắc phát triển được thiết lập để đội ngũ kỹ sư bám theo trước khi triển khai chi tiết mã nguồn pipeline và agent.

---

## 2. Các Trụ cột Kỹ thuật Cốt lõi (Technical Pillars)

```
                                  +------------------------------------+
                                  |    Câu hỏi Pháp luật Tiếng Việt    |
                                  +-----------------+------------------+
                                                    |
                                                    v
                                  +------------------------------------+
                                  |   LangGraph State & Query Planner  |
                                  +-----------------+------------------+
                                                    |
                         +--------------------------+--------------------------+
                         |                                                     |
                         v                                                     v
          +------------------------------+                      +------------------------------+
          | Dense Search (BAAI/bge-m3)   |                      | Sparse Search (BM25)         |
          +--------------+---------------+                      +--------------+---------------+
                         |                                                     |
                         +--------------------------+--------------------------+
                                                    |
                                                    v
                                  +------------------------------------+
                                  |   Reciprocal Rank Fusion (RRF)     |
                                  +-----------------+------------------+
                                                    |
                                                    v
                                  +------------------------------------+
                                  | Deterministic Temporal Check       |
                                  | (Hiệu lực: As-of Date & Quan hệ)   |
                                  +-----------------+------------------+
                                                    |
                                                    v
                                  +------------------------------------+
                                  | LLM Draft Generation with Claims   |
                                  +-----------------+------------------+
                                                    |
                                                    v
                                  +------------------------------------+
                                  | Citation & Evidence Audit Node     |
                                  +-----------------+------------------+
                                                    |
                         +--------------------------+--------------------------+
                         |                                                     |
             [Bằng chứng hợp lệ]                                  [Thiếu căn cứ / Mâu thuẫn]
                         v                                                     v
          +------------------------------+                      +------------------------------+
          | Final Answer + Citation IDs  |                      | Abstention / Clarification   |
          +------------------------------+                      +------------------------------+
```

1. **Phân đoạn theo Cấu trúc Pháp lý (Legal-Aware Chunking):**  
   Không sử dụng cắt đoạn kích thước cố định làm đứt gãy quy phạm. Văn bản được phân tách theo **Chương / Mục / Điều / Khoản / Điểm** và lưu giữ đầy đủ đường dẫn ngữ cảnh (legal breadcrumb).
2. **Truy xuất Lai (Hybrid Retrieval) với BGE-M3 & BM25:**  
   Kết hợp sức mạnh biểu diễn ngữ nghĩa của mô hình đa ngôn ngữ `BAAI/bge-m3` với khả năng khớp chính xác số hiệu văn bản và thuật ngữ pháp lý của `BM25`, hợp nhất qua thuật toán **Reciprocal Rank Fusion (RRF)**.
3. **Quản lý Hiệu lực Thời gian (Temporal Validity Management):**  
   Kiểm soát trạng thái văn bản (`in_force`, `expired`, `amended`) và quan hệ sửa đổi/bổ sung dựa trên thời điểm xem xét (`as_of_date`), ngăn chặn việc trả lời dựa trên văn bản đã bị bãi bỏ.
4. **Trích dẫn Nghiêm ngặt và Cơ chế Từ chối (Citation Grounding & Abstention):**  
   Mọi mệnh đề (claim) trong câu trả lời bắt buộc phải liên kết trực tiếp với một `citation_id` thực sự tồn tại trong bằng chứng. Khi thiếu dữ liệu hoặc nguồn xung đột, hệ thống sẽ lịch sự từ chối trả lời (abstain) thay vì phỏng đoán.
5. **Điều phối Agentic có kiểm soát (LangGraph Workflow):**  
   Sử dụng đồ thị trạng thái có nhánh điều kiện, hỗ trợ query rewriting, mở rộng ngữ cảnh Điều/Khoản cha khi cần thiết, và khống chế vòng lặp tối đa 2 lần.

---

## 3. Cấu trúc Thư mục Dự án (Project Layout)

Kho lưu trữ được tổ chức theo cấu trúc module phân tầng chuyên nghiệp:

```text
vietnamese-legal-agentic-rag/
├── .agents/                      # Quy tắc dành cho AI coding assistant
│   └── rules/
│       └── coding-standards.md   # Tiêu chuẩn viết mã và kiểm soát invariant
├── configs/                      # Cấu hình pipeline (không chứa secret)
│   ├── dev.yaml                  # Cấu hình mặc định môi trường phát triển
│   └── experiments/              # Cấu hình các kịch bản thực nghiệm NLP
├── data/                         # Quản lý dữ liệu (chỉ commit file mẫu & manifest)
│   ├── raw/                      # Văn bản luật gốc (PDF/HTML) - ignored by Git
│   ├── processed/                # Dữ liệu JSON đã parse cấu trúc - ignored by Git
│   ├── sample_laws/              # Dữ liệu mẫu nhỏ phục vụ unit test & smoke test
│   ├── gold_eval/                # Bộ câu hỏi kiểm thử chuẩn (Ground Truth benchmark)
│   └── manifests/                # Danh mục nguồn, URL và checksum SHA-256
├── docs/                         # Tài liệu kiến trúc và nghiên cứu
│   ├── decisions/                # Architecture Decision Records (ADRs)
│   │   └── ADR-001-scope-and-tech-stack.md
│   ├── architecture.md           # Thiết kế kỹ thuật chi tiết của hệ thống
│   ├── data_policy.md            # Quy chế quản lý nguồn dữ liệu và hiệu lực pháp lý
│   └── literature_review.md      # Tổng quan bài báo khoa học & ma trận tài liệu
├── notebooks/                    # Jupyter notebooks phục vụ nghiên cứu & EDA
├── src/                          # Mã nguồn chính của dự án (Package)
│   ├── core/                     # Pydantic v2 schemas & domain entities
│   ├── common/                   # Tiện ích logging, config loader, exceptions, bảo mật
│   ├── ingestion/                # Module trích xuất và chuẩn hóa văn bản luật
│   ├── chunking/                 # Bộ chia nhỏ theo Điều/Khoản/Điểm và gắn breadcrumb
│   ├── retrieval/                # Dense (BGE-M3), Sparse (BM25), Fusion (RRF), Reranker
│   ├── agents/                   # LangGraph state machine, query planner, router
│   ├── citation/                 # Citation validator, claim audit, chính sách abstention
│   ├── evaluation/               # Đánh giá tự động (Recall@k, MRR, nDCG, RAGAS)
│   └── api/                      # FastAPI endpoints và middleware phục vụ Web UI
├── tests/                        # Hệ thống kiểm thử tự động
│   ├── unit/                     # Unit tests kiểm tra tính đúng đắn của logic xác định
│   ├── integration/              # Integration tests kiểm tra liên kết giữa các module
│   └── eval/                     # Scripts chạy kiểm thử benchmark định lượng
├── .env.example                  # Mẫu khai báo biến môi trường và API keys
├── .gitignore                    # Bộ lọc git chặt chẽ cho Python, caches, dữ liệu lớn
├── AGENTS.md                     # Quy định cốt lõi bắt buộc tuân thủ khi viết mã
├── CONTRIBUTING.md               # Quy định làm việc nhóm, Git flow, commit convention
├── PROJECT_ROADMAP.md            # Lộ trình kỹ thuật chi tiết theo 15 tuần học thuật
├── pyproject.toml                # Cấu hình dự án, dependencies, ruff, mypy, pytest
└── README.md                     # Tài liệu tổng quan dự án (file này)
```

---

## 4. Lộ trình Triển khai theo Phase & Milestones

Lộ trình dự án được ánh xạ trực tiếp từ [PROJECT_ROADMAP.md](PROJECT_ROADMAP.md):

| Cột mốc (Milestone) | Phase | Nhiệm vụ chính | Tiêu chí hoàn thành (DoD) |
|---|---|---|---|
| **M0 — Scope & Rules Locked** *(Hiện tại)* | **Phase 0** | Khóa phạm vi corpus, lập literature matrix, quy định cấu trúc repo và rules | Repo khởi tạo chuẩn, `README`, `CONTRIBUTING`, `AGENTS.md`, `ADR-001` sẵn sàng |
| **M1 — Data-Ready** | **Phase 1** | Thu thập corpus hẹp, xác minh nguồn và metadata hiệu lực thời gian | Manifest hoàn chỉnh, checksum SHA-256, quan hệ văn bản được kiểm chứng |
| **M2 — Retrieval Baseline** | **Phase 2 & 3** | Legal chunking (Điều/Khoản), index BGE-M3 + BM25, đo đạc baseline | Báo cáo bảng so sánh Recall@k, MRR giữa Dense vs BM25 vs Hybrid (RRF) |
| **M3 — End-to-End MVP** | **Phase 4** | Xây dựng LangGraph state workflow, Citation Validator và Abstention | Smoke test API thông suốt; citation ID tồn tại 100%; tự động từ chối khi thiếu tin |
| **M4 — Evaluation-Ready** | **Phase 5 & 6** | Thử nghiệm Cross-Encoder Reranker, đánh giá chất lượng câu trả lời bằng RAGAS | Bộ test gold kín được đánh giá; phân tích lỗi và trade-off độ trễ/chi phí |
| **M5 — UX & Security-Ready** | **Phase 7** | Giao diện Web tra cứu (Streamlit/React), phòng chống Prompt Injection | Demo tương tác mượt mà, hiển thị rõ nguồn trích dẫn và giới hạn dữ liệu |
| **M6 — Release Candidate** | **Phase 8** | Đóng gói Docker Compose, tài liệu hướng dẫn tái lập kết quả, báo cáo Capstone | Đồng đội hoặc hội đồng có thể clone về, dựng môi trường và tái hiện kết quả |

---

## 5. Câu hỏi Nghiên cứu (Research Questions)

Dự án kiểm chứng 5 giả thuyết khoa học:
- **RQ1:** Mô hình Hybrid Search (BGE-M3 + BM25 qua RRF) có cải thiện Recall@k và MRR vượt trội so với tìm kiếm đơn lẻ trên tiếng Việt pháp lý không?
- **RQ2:** Phân đoạn theo cấu trúc quy phạm (Điều/Khoản/Điểm) có nâng cao độ chính xác trích dẫn (Citation Precision) so với Fixed-size chunking không?
- **RQ3:** Việc bổ sung Cross-Encoder Reranker (`bge-reranker-v2-m3`) có mang lại lợi ích đủ lớn so với chi phí độ trễ tăng thêm không?
- **RQ4:** Bộ lọc hiệu lực thời gian xác định (Temporal Validity Filter) có triệt tiêu được hiện tượng trả lời dựa trên văn bản đã hết hiệu lực không?
- **RQ5:** Cơ chế Citation Audit và Abstention Policy trong LangGraph có làm giảm tỷ lệ phát biểu sai lệch (Unfaithful Claims) về mức chấp nhận được không?

---

## 6. Hướng dẫn Bắt đầu (Quickstart & Setup)

### 6.1 Yêu cầu Tiên quyết
- **Python:** `>= 3.11, < 3.13`
- **Git**
- Khuyến nghị sử dụng môi trường ảo (`venv` hoặc `conda`)

### 6.2 Cài đặt Môi trường Phát triển

```bash
# 1. Clone kho lưu trữ
git clone https://github.com/Hatsu-Phat/vietnamese-legal-agentic-rag.git
cd vietnamese-legal-agentic-rag

# 2. Khởi tạo và kích hoạt virtual environment
python -m venv .venv

# Trên Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Trên Linux/macOS:
# source .venv/bin/activate

# 3. Cập nhật pip và cài đặt các phụ thuộc phát triển
pip install --upgrade pip
pip install -e ".[dev]"
```

### 6.3 Cấu hình Biến Môi trường

```bash
# Sao chép tệp mẫu biến môi trường
cp .env.example .env

# Mở .env và điền các khóa API cần thiết (OpenAI, Anthropic, ...)
```

### 6.4 Chạy Kiểm tra Chất lượng Mã nguồn (Quality Checks)

Trước khi cam kết mã nguồn, hãy đảm bảo toàn bộ các bước kiểm tra tự động đều vượt qua:

```bash
# Kiểm tra định dạng và linter
ruff format --check .
ruff check .

# Kiểm tra tĩnh kiểu dữ liệu (Strict Type Checking)
mypy src

# Chạy kiểm thử tự động
pytest tests/unit
```

---

## 7. Quy tắc Đóng góp (Contributing)

Mọi thành viên tham gia phát triển dự án bắt buộc phải đọc kỹ:
- [CONTRIBUTING.md](CONTRIBUTING.md): Hướng dẫn Git branching, quy ước Conventional Commits và quy trình PR Review.
- [AGENTS.md](AGENTS.md): Các nguyên tắc bất biến (Core Invariants) và ranh giới kiến trúc khi triển khai mã nguồn.
- [PROJECT_ROADMAP.md](PROJECT_ROADMAP.md): Lộ trình kỹ thuật 15 tuần và tiêu chí nghiệm thu chi tiết.

---

## 8. Tài liệu Tham khảo Nền tảng (Key References)

1. **Lewis et al. (2020)** — *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*. [arXiv:2005.11401](https://arxiv.org/abs/2005.11401).
2. **Chen et al. (2024)** — *BGE-M3-Embedding: Multi-Linguality, Multi-Functionality, Multi-Granularity Text Embeddings*. [arXiv:2402.03216](https://arxiv.org/abs/2402.03216).
3. **Nguyen Ba Thiem, Doan The Vinh, Pham Quang Tung & Tran Van Toan (2024)** — *Vietnamese Legal Information Retrieval in Question-Answering System*. [arXiv:2409.13699](https://arxiv.org/abs/2409.13699).
4. **Cormack et al. (2009)** — *Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods*. [ACM DOI: 10.1145/1571941.1572114](https://doi.org/10.1145/1571941.1572114).
5. **Gao et al. (2023)** — *Enabling Large Language Models to Generate Text with Citations (ALCE)*. [EMNLP 2023](https://aclanthology.org/2023.emnlp-main.398/).
6. **Es et al. (2023)** — *RAGAS: Automated Evaluation of Retrieval Augmented Generation*. [arXiv:2309.15217](https://arxiv.org/abs/2309.15217).

---

## 9. Tuyên bố Miễn trừ Trách nhiệm Pháp lý (Legal Disclaimer)

Hệ thống **Vietnamese Legal Agentic RAG** là sản phẩm nghiên cứu khoa học và đồ án kỹ thuật AI Engineer nhằm mục đích hỗ trợ tra cứu, đối chiếu và trích xuất thông tin văn bản quy phạm pháp luật.  
**Hệ thống KHÔNG cung cấp lời khuyên pháp lý chính thức.** Người sử dụng khi giải quyết các vấn đề pháp lý thực tế cần tham vấn ý kiến của luật sư có chứng chỉ hành nghề hoặc đối chiếu trực tiếp với các văn bản gốc ban hành trên Công báo và Cổng thông tin Chính phủ.