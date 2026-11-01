# Hướng dẫn đóng góp

Đọc [AGENTS.md](AGENTS.md) trước. Quy tắc pháp lý, Pydantic v2, typing, bounded retries và secrets trong đó áp dụng toàn repo. [Quy trình phối hợp](docs/team-workflow.md) quy định phạm vi sửa file và cách thay hợp đồng giữa các thành viên.

## Nhánh và base chung

- `main` là base tích hợp mặc định sau review. Hiện chưa có nhánh `develop` trong checkout; không giả định nó tồn tại. Chỉ đổi base chung khi cả nhóm thống nhất và cập nhật tài liệu này.
- Người tích hợp phải đưa thay đổi nền tảng đã review lên nhánh chung trước khi teammate tách nhánh. Không tạo nhánh mới từ workspace đang chứa thay đổi không liên quan hoặc chưa được chia sẻ.
- Mỗi đầu việc dùng một nhánh, ví dụ `feat/api-search`, `feat/frontend-search`, `fix/search-timeout`, `docs/search-contract`. Nhánh do Codex tạo mặc định dùng `codex/`.
- Trước khi cập nhật base, kiểm tra working tree và bảo toàn thay đổi của mình. Không reset/clean/force-push để bỏ xung đột. Dùng merge hoặc rebase theo chính sách nhóm; không rewrite nhánh người khác đang dùng.
- PR nhỏ theo một đầu việc. Tách PR refactor/đổi tên file khỏi PR triển khai FastAPI hoặc frontend.

## Cài môi trường và đổi dependencies

Môi trường và lệnh setup duy nhất nằm trong [README](README.md#môi-trường-chung). Backend dùng `pyproject.toml` + `uv.lock`; requirements là bản export, không phải nơi chỉnh dependencies.

Khi cần đổi thư viện, thống nhất một người thực hiện trong một PR riêng:

```bash
uv lock
uv export --locked --extra dev --no-emit-project --format requirements.txt --output-file requirements.txt
uv sync --locked --extra dev
```

Review/commit cả khai báo, lockfile và bản export. Người khác sync bằng `--locked`, không tự upgrade để chữa lỗi môi trường. Frontend chỉ có một lockfile cho package manager nhóm chọn; không trộn nhiều lockfile.

## Code và ranh giới

- Models và ports dùng chung ở `src/core/`; core không import FastAPI, Qdrant, Torch hay các module chức năng.
- API gọi SearchPort/service điều phối; không chứa embedding, RRF hoặc logic pháp lý.
- Search runtime không tự tạo collection, encode corpus hay chạy migration trong request.
- Migration/audit offline nằm trong `src/ingestion/indexing/`; giữ compatibility imports cho đến khi các caller đã chuyển.
- Code Python có type hints; dùng docstrings cho public interface và giải thích quyết định khó trong comments.
- Mỗi thay đổi hợp đồng phải cập nhật schema, fixtures, tài liệu và nơi gọi trong cùng chuỗi PR đã thống nhất.

## Trước khi gửi PR

Chạy các quality gates ở README. Với thay đổi hành vi, bổ sung kiểm thử phù hợp; sửa tài liệu không cần chạy model hoặc benchmark. Dùng dev để đo retrieval, không tuning trên test. Báo rõ phần kiểm tra local, mock, Cloud và phần chưa chạy.

Commit theo Conventional Commits: `feat(api): ...`, `fix(retrieval): ...`, `refactor(config): ...`, `docs(team): ...`.

PR cần nêu vấn đề/hành vi sau thay đổi, kết quả kiểm tra và những hợp đồng/file dùng chung bị tác động. Có ít nhất một thành viên khác review trước khi tích hợp. Sử dụng [PR template](.github/pull_request_template.md).

Không stage tất cả một cách mặc định: xem từng đường dẫn, loại `.env`, keys, raw data, model weights, cache và local indexes. Không gửi key qua mô tả PR, log, ảnh chụp hoặc frontend. Không sửa báo cáo lịch sử thành số liệu mới; ghi kết quả chạy mới trong báo cáo riêng và liên kết từ tài liệu liên quan.
