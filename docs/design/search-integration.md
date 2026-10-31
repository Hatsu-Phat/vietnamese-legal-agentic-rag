# Tích hợp SearchService vào FastAPI

Trạng thái: schema và service Search v1 đã triển khai, có CLI và kiểm tra Cloud thực tế. HTTP routes, middleware và authentication do teammate phụ trách. [Hợp đồng đầy đủ](search-v1.md).

## 1. Những thành phần dùng chung

| Thành phần | Import / vị trí | Vai trò |
|---|---|---|
| Schemas | `src.core.search` | SearchRequest, SearchFilters, SearchHit, SearchResponse, SearchErrorResponse |
| Interface | `src.core.search_ports.SearchPort` | Dependency cho route; có `ready` và `async search(request, request_id)` |
| Khởi tạo / đóng | `src.retrieval.search_factory.open_search_service` | Async context manager, nạp tài nguyên một lần ở lifespan |
| Domain errors | `src.common.search_errors.SearchError` | `code`, `message`, `status_code`; không chứa SDK exception hoặc input |
| Fixtures | `tests/fixtures/search/` | request, success, empty và unavailable; JSON đã parse bằng schemas thật |

Luồng service dùng BGE-M3 + Qdrant và BM25, áp dụng cùng bộ lọc, gộp RRF và hydrate nội dung từ canonical chunks. Search không gọi LLM sinh văn bản; không cần LangChain/LangGraph để gọi service này.

## 2. Gọi service

Ví dụ phía service, có thể chạy trong một chương trình async:

```python
from uuid import uuid4

from src.core.search import SearchRequest
from src.retrieval.search_factory import open_search_service


async def example() -> None:
    async with open_search_service(device="cpu") as service:
        result = await service.search(
            SearchRequest(query="Thời hiệu khởi kiện tranh chấp hợp đồng", top_k=5),
            uuid4(),
        )
        print(result.model_dump_json(indent=2))
```

Trong FastAPI, mở context manager một lần ở lifespan và lưu service vào app state/dependency; đóng khi shutdown. **Không mở lại context trong mỗi route call.** `create_search_service` là bản blocking dành cho các runner riêng; `open_search_service` đã chuyển startup sang worker để không chặn event loop.

Một worker thực hiện inference/retrieval tại một thời điểm. Request chờ tài nguyên vẫn tính trong deadline 30 giây. Nếu request timeout hoặc bị hủy, worker đang chạy được giữ đến khi native work kết thúc rồi mới nhận việc tiếp; không giả định hủy asyncio sẽ dừng Torch hay socket.

## 3. Việc teammate làm trong FastAPI

1. Tạo UUIDv4 cho từng HTTP request, trước validation; dùng cùng ID trong response body và `X-Request-ID`.
2. `POST /search` nhận SearchRequest, lấy SearchPort từ dependency và gọi `await service.search(request, request_id)`.
3. Chuyển SearchError thành SearchErrorResponse bằng `code`, `message`, `status_code`.
4. Handler RequestValidationError trả 422/INVALID_REQUEST theo cùng envelope; không trả raw validation input. Handler lỗi ngoài dự kiến trả 500/INTERNAL_ERROR với thông báo cố định.
5. Liveness xác nhận process; readiness dựa trên startup/service.ready. Dịch vụ chưa sẵn sàng trả 503.
6. Bổ sung HTTP integration tests cho request ID, ba fixtures, lỗi validation/dependency/timeout và kiểm tra log không chứa query/key.

Route không chứa encode, BM25, RRF hoặc logic kiểm chứng hiệu lực. Mock chỉ cần triển khai SearchPort và trả SearchResponse từ fixtures để frontend có thể phát triển trước.

## 4. Artifacts tối thiểu và kiểm tra release

Máy chạy backend cần:

- `data/artifacts/processed/chunks.jsonl` chuẩn.
- `data/artifacts/indexes/bm25/bm25_index.pkl`, `bm25_chunk_ids.json`, `bm25_index.manifest.json` cùng release.
- `data/raw/stopwords.txt`.
- `data/artifacts/manifests/index_manifest.json`, `configs/dev.yaml` và `.env` riêng.
- BGE-M3 revision đã khóa; tải một lần vào `models/cache/` hoặc dùng cache đã chuẩn bị.

Không cần bản Qdrant SQLite 1,1 GB để truy vấn Cloud. Mỗi thành viên dùng key riêng có quyền đọc. Không đưa file `.env`, key hoặc data/model lớn lên Git.

Startup kiểm tra checksum artifacts BM25 trước unpickle, checksum/fingerprint corpus, phiên bản Python/thư viện, cấu hình tokenizer/BM25 và revision model. Sau đó đối chiếu fingerprint, embedding contract, kích thước vector và số điểm Cloud. Thiếu manifest hoặc sai release → SEARCH_UNAVAILABLE, không tự build lại index hay tự upload.

Alias trong cấu hình được resolve một lần khi startup. Service giữ collection vật lý đã xác minh đến khi shutdown, tránh trộn BM25 cũ với alias vừa chuyển sang release mới. Khi phát hành release mới, cập nhật artifacts local và restart service sau khi alias/manifest đã sẵn sàng.

BM25 manifest mới được tạo cùng lúc build/save. Nếu dùng artifacts cũ chưa có manifest, người phụ trách dữ liệu chạy lại bước BM25, không tự tạo manifest cho pickle chưa xác minh:

```bash
uv run --locked --extra dev python scripts/run_phase2_indexing.py --step bm25
```

## 5. Chạy trực tiếp và đánh giá

```bash
uv run --locked --extra dev python scripts/search.py --query "Thời hiệu khởi kiện tranh chấp hợp đồng" --top-k 5
uv run --locked --extra dev python scripts/search.py --query "hợp đồng" --document-id law_2015_qh13_91 --issued-year 2015
uv run --locked --extra dev python scripts/evaluate_search.py --device cpu
```

CLI search in SearchResponse ra stdout; logs ở stderr. CLI đánh giá chỉ đọc dev, không chỉnh tham số hoặc sử dụng test. Báo cáo đầy đủ ở `docs/reports/search_dev.json`; dùng `--limit` cho smoke run sẽ ghi `search_dev_smoke.json` riêng. Chạy lại full dev chỉ khi thay đổi retrieval, phát hiện lỗi hoặc cần đối chiếu, không dùng để kiểm tra mỗi lần sửa HTTP route.

Kết quả và giới hạn đo thực tế: [báo cáo Search v1](../reports/search_v1.md). Không có mặc định nào trong service cho phép xử lý hồ sơ nhạy cảm hoàn toàn local: Dense đang gửi vector truy vấn tới Qdrant Cloud. Các cảnh báo hiệu lực/URL chưa xác minh phải được client giữ và hiển thị.

## 6. Ranh giới sau refactor bảo trì

- SearchConfiguration (src/core/search_config.py) kiểm tra các phần cấu hình search trước khi nạp tài nguyên. Model/revision chỉ cấu hình ở indexing.qdrant; tokenizer/k1/b ở indexing.bm25. Các trường model_name cũ nếu còn xuất hiện phải khớp cấu hình chính.
- search_factory quản lý việc khởi tạo/đóng; search_release_check kiểm tra local và Cloud release. Khi startup lỗi, server log chỉ ghi stage và error_type; client vẫn nhận SEARCH_UNAVAILABLE, không nhận raw exception.
- SearchService chỉ biết ports và domain errors. Adapter chịu trách nhiệm ánh xạ lỗi SDK/timeout của Qdrant.
- Các tác vụ migration và audit model ở src/ingestion/indexing/. Import cũ từ src/retrieval/index_migration.py và model_audit.py vẫn được giữ để tương thích. Các lớp indexer cũ còn là facade dùng cho CLI/build cũ; chưa loại bỏ public API của chúng.
- Tests chia theo contracts, fusion, runtime, adapters, startup, configuration và evaluation; fixtures/helpers dùng chung trong tests/search_support.py.
- get_settings() được cache và có thể inject settings khi startup. Biến settings và các biến môi trường legacy vẫn được giữ để không làm hỏng CLI/.env cũ; chưa loại bỏ hoàn toàn việc nạp settings lúc import. Key LLM được che bằng SecretStr giống key Qdrant.

Schema /search, SearchPort, UUID, artifacts và thuật toán RRF không đổi. Báo cáo dev 256 câu là kết quả baseline trước đợt refactor; kiểm tra hồi quy sau refactor được ghi riêng, không coi đó là lần chạy lại full dev.
