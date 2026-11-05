# HTTP API v1 (FastAPI)

Hợp đồng dữ liệu nằm trong `src/core/search.py`, `src/core/agent.py`, `src/core/api.py`; HTTP nằm trong `src/api/`. Route chỉ kiểm tra input và gọi service, không chứa logic retrieval hay suy luận pháp lý. Quy tắc search: [Search v1](search-v1.md); quy tắc agent: [Agent `/ask` v1](agent-ask-v1.md).

## 1. Chạy

```bash
uv run --locked --extra dev uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

- Chạy **một worker**: model BGE-M3, BM25 và catalog nạp một lần trong lifespan (CPU mất khoảng 2–3 phút). Không dùng `--reload`/nhiều worker vì mỗi tiến trình nạp lại toàn bộ.
- Thiết bị encoder lấy từ `EMBEDDING_DEVICE` (mặc định `cpu`).
- Thiếu `GEMINI_API_KEY`: app vẫn khởi động, `/search` và `/sources` hoạt động, `/ask` trả `503 LLM_UNAVAILABLE`.
- Tài liệu OpenAPI tự sinh tại `/docs`.

## 2. Endpoint

| Method | Path | Body / tham số | Thành công |
|---|---|---|---|
| GET | `/health` | — | `200 {"status":"ok"}` (liveness, không chạm model) |
| GET | `/ready` | — | `200` khi search sẵn sàng, ngược lại `503`; body `{search, ask, sources}` |
| POST | `/search` | `SearchRequest` | `200 SearchResponse` |
| POST | `/ask` | `AskRequest` | `200 AskResponse` (`decision` = `answer`/`clarify`/`abstain`) |
| GET | `/sources/{chunk_id}` | `chunk_id` khớp `^[A-Za-z0-9_.\-]{1,200}$` | `200 SourceResponse` (toàn văn đoạn + metadata) |

Mỗi `Citation` trong `AskResponse` có `chunk_id`; frontend mở nguồn bằng `GET /sources/{chunk_id}`. Mọi trường của nguồn lấy từ catalog của release đang chạy, không lấy từ LLM. `source_url` hiện luôn `null` vì corpus chưa có URL đã kiểm chứng.

## 3. Request ID và lỗi

- Server sinh UUIDv4 cho mỗi request, trả trong header `X-Request-ID` và trường `request_id` của body (cả khi lỗi). Header `X-Request-ID` do client gửi bị bỏ qua.
- Mọi lỗi dùng một envelope: `{request_id, code, message}`. Thông báo cố định, không lặp lại câu hỏi, không chứa stack trace hay lỗi gốc của provider.

| HTTP | `code` | Khi nào |
|---|---|---|
| 422 | `INVALID_REQUEST` | Sai kiểu (strict: `"5"` không phải số), trường lạ, query rỗng/quá dài, `top_k` ngoài 1–20, `chunk_id` sai mẫu |
| 404 | `SOURCE_NOT_FOUND` | `chunk_id` không thuộc release đang chạy |
| 404 | `NOT_FOUND` | Đường dẫn không tồn tại |
| 503 | `SEARCH_UNAVAILABLE` | Search chưa sẵn sàng hoặc một nhánh retrieval lỗi |
| 503 | `LLM_UNAVAILABLE` | Chưa cấu hình LLM, provider lỗi, hết quota, JSON sai schema |
| 504 | `SEARCH_TIMEOUT` / `LLM_TIMEOUT` | Vượt deadline |
| 500 | `INTERNAL_ERROR` | Lỗi ngoài dự kiến (log chỉ ghi request ID và tên lớp lỗi) |

## 4. Kiểm thử

- `tests/unit/test_api.py`: service giả qua `create_app(opener)`; kiểm tra request ID body = header, bỏ qua ID của client, ánh xạ 422/404/503/504/500, `/ask` khi thiếu LLM, `/sources`.
- Smoke với service thật (Qdrant Cloud + Gemini) ngày 2026-11-05: `/ready` đủ ba khả năng; `/search` trả BLDS 2015 Điều 429 trong top 3; `/ask` trả `answer` trích `law_2015_qh13_91__art_429__c0` và `/sources` mở được đoạn đó; `chunk_id` không tồn tại trả 404. Đây là một lần chạy, không phải số liệu đánh giá.
