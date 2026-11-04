# Vietnamese Legal Agentic RAG

Dự án tra cứu và hỏi đáp pháp luật tiếng Việt. Hiện đã có **SearchService Hybrid BGE-M3 + BM25 + RRF**, schema dùng chung, CLI và kiểm tra trên Qdrant Cloud. FastAPI, frontend và luồng hỏi đáp LangGraph là các bước tiếp theo.

## Bắt đầu theo vai trò

| Công việc | Đọc trước | Phạm vi chính |
|---|---|---|
| Search / retrieval | [Hợp đồng Search v1](docs/design/search-v1.md) | `src/retrieval/`, tests search |
| FastAPI | [Hướng dẫn tích hợp](docs/design/search-integration.md) | `src/api/`, HTTP integration tests |
| Frontend | Hợp đồng Search v1 và [phối hợp nhóm](docs/team-workflow.md) | `frontend/` khi bắt đầu triển khai |
| Dữ liệu / crawler | [Hướng dẫn dữ liệu](data/README.md), [data policy](docs/data_policy.md) | `src/ingestion/`, `src/chunking/` |

Trước khi tách nhánh: đọc [CONTRIBUTING.md](CONTRIBUTING.md) và thống nhất base commit đã được tích hợp. Thay đổi chỉ ở workspace local chưa tự xuất hiện trên máy teammate.

## Môi trường chung

Python **3.12.15**, uv **0.13.5**. Cài đúng uv, sau đó chạy từ repo root:

```bash
python -m pip install --user "uv==0.13.5"
uv python install
uv sync --locked --extra dev
uv run --locked --extra dev python --version
```

Chọn interpreter `.venv/bin/python` (macOS/Linux) hoặc `.venv\Scripts\python.exe` (Windows) trong IDE. Python/uv pins lấy từ `.python-version` và `pyproject.toml`; dependencies lấy từ `uv.lock`. Không tự nâng thư viện hoặc chỉnh `requirements.txt` bằng tay. Quy trình đổi dependencies nằm trong CONTRIBUTING.

Đã kiểm tra runtime trên macOS ARM64 và Windows 11 x64 (`uv sync --locked`, toàn bộ `tests/unit`; torch bản CPU theo `uv.lock`). Linux chưa kiểm thử. Line endings được chuẩn hóa LF qua `.gitattributes`; mọi thao tác đọc/ghi text phải chỉ định `encoding="utf-8"` vì Windows mặc định không dùng UTF-8. Nhóm phụ thuộc `eval` chưa được xác nhận runtime đầy đủ.

## Chạy search

1. Nhận artifacts chuẩn theo danh sách trong [hướng dẫn tích hợp](docs/design/search-integration.md#4-artifacts-tối-thiểu-và-kiểm-tra-release). Các file data/model lớn không nằm trong Git.
2. Nếu chưa có `.env`, sao chép từ `.env.example`; giữ nguyên file riêng đã có. Điền `QDRANT_MODE=server`, `QDRANT_URL` và `QDRANT_API_KEY`. Mỗi thành viên dùng key riêng, chỉ cần quyền đọc khi phục vụ search.
3. Chạy CLI:

```bash
uv run --locked --extra dev python scripts/search.py --query "Thời hiệu khởi kiện tranh chấp hợp đồng" --top-k 5
```

Cấu hình model/revision và Qdrant ở `configs/dev.yaml` → `indexing.qdrant`; BM25/tokenizer ở `indexing.bm25`; ngân sách search/RRF ở `retrieval`. Credentials nằm trong `.env`, không đưa xuống frontend. Model tải một lần vào `models/cache/`; service nạp model/index một lần khi startup.

CLI không tự build index hoặc sửa Cloud khi thiếu/sai release. Hướng dẫn ingestion, BM25 và migration nằm trong [data/README.md](data/README.md). FastAPI teammate gọi `SearchPort` qua `open_search_service`; hiện chưa có HTTP server để chạy chỉ bằng README này.

## Kiểm tra và đánh giá

```bash
uv run --locked --extra dev ruff check .
uv run --locked --extra dev ruff format . --check
uv run --locked --extra dev mypy src
uv run --locked --extra dev pytest tests/unit
```

Chạy benchmark khi thay đổi retrieval hoặc cần đối chiếu, dùng dev; giữ test cho đánh giá cuối:

```bash
uv run --locked --extra dev python scripts/evaluate_search.py --device cpu
```

Số liệu và phạm vi bằng chứng nằm trong [báo cáo Search v1](docs/reports/search_v1.md); kiểm tra sau tái cấu trúc nằm trong [báo cáo refactor](docs/reports/search_refactor.md). Không chép số lượng tests hoặc benchmark vào nhiều README rồi sửa riêng từng nơi.

## Cấu trúc và ranh giới

```text
src/
  core/         # Schemas, enums, ports và cấu hình có kiểu
  common/       # Settings, hash, log và domain errors
  retrieval/    # Search runtime; một số imports/indexers cũ giữ để tương thích
  ingestion/    # Đọc corpus; indexing/ chứa migration/audit offline
  chunking/     # Chia văn bản
  evaluation/   # Đánh giá retrieval
  api/          # Khung cho teammate triển khai FastAPI
  agents/       # Khung LangGraph tương lai
  citation/     # Khung kiểm chứng tương lai
configs/        # Cấu hình không chứa secret
scripts/        # CLI, ingestion, migration, evaluation
frontend/       # Dự kiến tạo khi bắt đầu làm UI; chưa có trong checkout hiện tại
data/          # Raw, samples, benchmarks, artifacts và imports
models/         # Cache local, ngoài Git
tests/          # Backend tests và fixtures
docs/           # Thiết kế, phối hợp nhóm, quyết định và báo cáo
```

Giữ Python ở cấu trúc hiện tại; frontend có dependencies/lockfile riêng khi được triển khai. Không di chuyển toàn bộ backend hoặc đổi imports trong một PR làm endpoint/UI.

## Tài liệu chuẩn và giới hạn

[Danh mục tài liệu](docs/README.md) quy định nơi chứa từng thông tin để tránh hướng dẫn mâu thuẫn. [Kiến trúc](docs/architecture.md) phân biệt runtime hiện tại với luồng mục tiêu. [Roadmap](PROJECT_ROADMAP.md) là kế hoạch, không phải bằng chứng đã hoàn thành. [AGENTS.md](AGENTS.md) là quy tắc phát triển bắt buộc.

Search hiện trả bằng chứng, không sinh câu trả lời pháp lý. Metadata hiệu lực chưa xác minh và URL nguồn thiếu được trả qua warnings; score RRF không phải xác suất đúng. Corpus chưa có crawler cập nhật tự động. Local encoder vẫn gửi vector truy vấn tới Qdrant Cloud; hồ sơ nhạy cảm cần chính sách riêng trước khi sử dụng. Chi tiết giới hạn dữ liệu/token nằm trong các báo cáo và data policy.
