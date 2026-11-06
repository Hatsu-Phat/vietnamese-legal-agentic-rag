# P5 — MCP server, cache `/ask`, observability

Trạng thái: code + unit test xong trên nhánh `feat/mcp-cache-observability` (từ `develop` `f3ccd14`). Số liệu smoke ở mục 5 lấy từ lệnh đã chạy; mục nào chưa chạy ghi `CHƯA CHẠY`.

## 1. Cache `/ask` (T-0502) — `src/agents/ask_cache.py`

- `CachedAskService` bọc `AskService` (cùng `AskPort`), dùng cho cả API và MCP. Cache nằm trong tiến trình (LRU + TTL), không thêm dependency.
- Khoá = sha256 của: câu hỏi chuẩn hoá (NFC, casefold, gộp khoảng trắng), `as_of_date`, `top_k`, `filters`, **phạm vi quyền** (tập `access_level`), **release fingerprint** của search, **chữ ký pipeline** (`answer_pipeline` trong `src/api/main.py`: routing mode, model cloud/local, `PROMPT_VARIANT`, `LOCAL_PROMPT_VARIANT`, `LOCAL_LLM_MAX_EVIDENCE`, `STRICT_CITATION_VERIFICATION`, `MAX_RETRIEVAL_ROUNDS`).
- Khoá theo phạm vi quyền chứ không theo tên vai trò: kết quả chỉ phụ thuộc phạm vi, nên hai vai trò khác phạm vi không bao giờ dùng chung mục cache (nhân viên và ẩn danh cùng `public` thì dùng chung, và vốn nhận cùng kết quả).
- Không cache: câu có PII (kiểm trên câu gốc, trước khi che), lỗi (`AgentError` được ném ra, không lưu). Kết quả `answer`/`clarify`/`abstain` đều được cache.
- Lần trúng cache: `request_id` mới, `elapsed_ms` là thời gian tra cache, `trace` thêm `cache_hit`; không gọi search hay LLM.
- Cấu hình: `ASK_CACHE_TTL_SECONDS` (mặc định 3600; `0` = tắt), `ASK_CACHE_MAX_ENTRIES` (512).
- Giới hạn: hai request giống nhau đến **đồng thời** đều có thể trượt cache và cùng gọi LLM (chưa gộp request). Cache mất khi khởi động lại; mỗi worker có cache riêng (API chạy 1 worker). Câu trả lời sinh ra lúc cloud lỗi và đã fallback sang local cũng được cache đến hết TTL.

## 2. Observability (T-0503) — `src/observability/metrics.py`

`GET /metrics` (định dạng Prometheus text, tự sinh; tắt bằng `METRICS_ENABLED=false`; không nằm trong OpenAPI):

| Series | Ý nghĩa |
|---|---|
| `legal_rag_http_requests_total{route,status}` | Request theo route template; đường dẫn lạ gộp thành `unmatched`; `/metrics` không tự đếm chính nó |
| `legal_rag_http_request_seconds{route}` | Histogram độ trễ theo route |
| `legal_rag_ask_node_seconds{node}` | Histogram độ trễ theo node LangGraph (`timed_node` bọc từng node, giữ chữ ký để LangGraph vẫn truyền `config`) |
| `legal_rag_llm_calls_total{route,provider,outcome}` | Lời gọi LLM qua `ModelRouter` (local/cloud, lớp provider, ok/error; fallback được đếm thành 2 lời gọi) |
| `legal_rag_ask_decisions_total{decision,cached}` | Kết quả `/ask` và MCP `ask` |
| `legal_rag_ask_cache_{hits,misses,bypassed}_total`, `legal_rag_ask_cache_entries` | Bộ đếm của cache |
| `legal_rag_mcp_tool_calls_total{tool,outcome}` | Lời gọi tool MCP |

Nhãn chỉ chứa giá trị hữu hạn; không có câu hỏi, evidence, credential hay request ID.

Log: `LOG_FORMAT=json` → mỗi dòng một JSON (loguru `serialize`) trên stderr; middleware gắn `request_id` vào `extra` của mọi log trong request. Log không chứa câu hỏi/evidence/câu trả lời (giữ quy tắc từ P0).

## 3. MCP server (T-0501) — `src/mcp_server/server.py`, `scripts/mcp_server.py`

- SDK `mcp` **2.3.0** (theo `uv.lock`; 2.x đổi `FastMCP` → `MCPServer`), transport **stdio**. Gói mới trong `dependencies` của `pyproject.toml`; lock chỉ thêm gói (`mcp`, `mcp-types`, `cryptography`, `cffi`, `pycparser`, `pyjwt`, `python-multipart`, `pywin32`, `sse-starlette`), không đổi phiên bản gói cũ.
- Tools: `search_legal(query, top_k=5)`, `get_source(chunk_id)`, `ask(question, as_of_date?)`. Đầu ra có cấu trúc theo đúng hợp đồng Pydantic của API (`SearchResponse`, `SourceResponse`, `AskResponse`).
- Phân quyền: `MCP_API_KEY` (một trong các `AUTH_API_KEY_*`) được kiểm **trước** khi nạp model bằng cùng `AccessResolver` của P2; thiếu/sai key → không khởi động (exit 2), không có chế độ ẩn danh. Mỗi tiến trình phục vụ đúng một vai trò.
- `get_source` ngoài quyền trả cùng lỗi `SOURCE_NOT_FOUND` như chunk không tồn tại. `search_legal` che PII như route `/search`; `ask` che PII trong `AskService`.
- Lỗi trả về là tool error với mã + thông báo cố định (`SEARCH_TIMEOUT: ...`), không có stack trace.
- stdout dành cho giao thức MCP; mọi log ra stderr.
- Demo: `scripts/mcp_demo_client.py --roles legal,employee` khởi động server cho từng vai trò, gọi đủ 3 tool, ghi `docs/reports/p5/mcp_demo.json` (chỉ id, access level, decision, cờ, thời gian).

Cấu hình máy khách MCP khác (ví dụ Claude Desktop): lệnh `python scripts/mcp_server.py` trong thư mục repo, biến môi trường `MCP_API_KEY`.

## 4. Kiểm thử (239/239 unit pass, ruff check + format --check + mypy sạch)

- `tests/unit/test_ask_cache.py` (7): khoá đổi theo từng thành phần; chuẩn hoá câu hỏi; trúng cache với request ID mới; vai trò không dùng chung kết quả; PII không vào cache; TTL/LRU/tắt cache; graph thật với provider giả: lần hai **0** lời gọi LLM/search.
- `tests/unit/test_metrics.py` (4): sau 3 `/health`, 2 `/ask` (1 trúng cache), 1 `/sources` 404, 1 đường dẫn lạ → các bộ đếm khớp đúng; tắt `/metrics`; định dạng histogram/counter; log JSON có `request_id`, không chứa câu hỏi.
- `tests/unit/test_mcp_server.py` (5, client MCP chạy trong tiến trình): bắt buộc credential; 3 tool có schema vào/ra; search/ask chạy với phạm vi của vai trò; nhân viên/quản lý không mở được chunk mật (lỗi giống chunk không tồn tại), pháp chế mở được; input sai, che PII, đếm metrics.

## 5. Smoke thật

Chạy 2026-11-06 trên máy Windows, CPU, Qdrant Cloud alias → `vn_legal_bge_m3_v3`, `LLM_ROUTING_MODE=cloud` (`.env`).

**MCP** (`scripts/mcp_demo_client.py --roles legal,employee`, kết quả `docs/reports/p5/mcp_demo.json`):

| Vai trò | Khởi động | 3 tool | `search_legal` "khung lương bậc E3 và E4" (top 10) | `get_source` `internal_2026_qd_tnt_09__art_2__c0` (mật) | `ask` lần 1 → lần 2 |
|---|---|---|---|---|---|
| legal | 489,9 s (lần đầu, đọc đĩa lạnh) | có | 3 chunk `confidential` | mở được | answer, BLDS Đ429, 4,36 s → trúng cache 0,0 s |
| employee | 101,2 s | có | 0 chunk mật (10 public) | `SOURCE_NOT_FOUND` | answer, BLDS Đ429, 7,97 s → trúng cache 0,0 s |

**API** (uvicorn qua preview `legal-rag-api`; client httpx trong scratchpad, không thuộc repo). Các dòng `/metrics` sau smoke lưu ở `docs/reports/p5/api_metrics_smoke.txt`:
- Câu BLDS hỏi 2 lần (khác hoa/thường, khoảng trắng): lần 1 4,62 s, lần 2 trúng cache 0,00 s, cùng trích dẫn `law_2015_qh13_91__art_429__c0`.
- Câu có CCCD: không vào cache (`bypassed` = 1) và được định tuyến sang model local (`llm_calls_total{route="local"}` = 2: draft + review), đúng luật P3.
- Bộ đếm khớp với số request đã gửi: `/ask` 200 = 5, cache hits 2 / misses 2 / bypassed 1, entries 2; Gemini 3 lời gọi, Ollama 2 lời gọi.
- 3 request `/ask` trả 400 trong bộ đếm là lần thử đầu bằng `curl` trên Git Bash: tiếng Việt bị gửi sai bảng mã, không phải lỗi server; lần chạy lại bằng httpx (UTF-8) trả 200.
