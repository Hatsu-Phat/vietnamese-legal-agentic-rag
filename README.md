# Vietnamese Legal Agentic RAG

Nhánh refactor chuẩn hóa data layout, môi trường và công cụ indexing/Qdrant Cloud. Hybrid SearchService, contracts và FastAPI integration được phát triển trong nhánh phụ thuộc `codex/search-v1`.

## Môi trường

Python 3.12.15, uv 0.13.5; dùng đúng pins trong pyproject.toml và .python-version.

```bash
python -m pip install --user "uv==0.13.5"
uv python install
uv sync --locked --extra dev
```

## Dữ liệu và indexing

[Hướng dẫn dữ liệu](data/README.md) mô tả artifacts, snapshot của team, BM25 và migration. Data/model lớn bị bỏ qua bởi Git. Không ghi đè .env đã có; nếu chưa có thì sao chép .env.example và điền credentials riêng.

Model/revision/Qdrant ở configs/dev.yaml → indexing.qdrant; BM25 ở indexing.bm25. Không tự đổi model hoặc rebuild corpus để ghép code. Các tác vụ migration/audit offline nằm ở src/ingestion/indexing; imports cũ ở src/retrieval còn giữ tương thích.

## Kiểm tra

```bash
uv run --locked --extra dev ruff check .
uv run --locked --extra dev ruff format . --check
uv run --locked --extra dev mypy src
uv run --locked --extra dev pytest tests/unit
```

Runtime trước đây đã kiểm tra trên macOS ARM64; dry-run cài đặt Windows/Linux không thay thế kiểm thử thực tế.

## Phối hợp

Đọc [CONTRIBUTING](CONTRIBUTING.md), [team workflow](docs/team-workflow.md), [danh mục tài liệu](docs/README.md) và [AGENTS.md](AGENTS.md). [Roadmap](PROJECT_ROADMAP.md) là mục tiêu phát triển, không phải bằng chứng mọi tính năng đã có. Các báo cáo là snapshot của từng lần chạy.

Frontend, HTTP API, Hybrid SearchService và luồng LangGraph/citation là các phần phát triển sau nền tảng của nhánh này. Metadata chưa xác minh giữ unknown; không dùng điểm tìm kiếm để khẳng định hiệu lực pháp lý. Credentials chỉ ở backend.
