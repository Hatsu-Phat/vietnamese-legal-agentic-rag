# P3 — LLM local, router local/cloud, fallback

> Lập: 2026-11-06. Phạm vi: T-0301 provider Ollama, T-0302 router deterministic + fallback.
> Số liệu đo ở mục 5. Mục 6: sửa lỗi hồi quy của P2 phát hiện khi đo.

## 1. Thành phần
- **`OllamaAnswerGenerator`** (`src/agents/ollama.py`):
  - Cùng Protocol `AnswerGenerator` với Gemini (`draft`, `rewrite_query`, `review`) và dùng chung prompt (`src/agents/prompts.py`).
  - Gọi `POST /api/chat` của Ollama với `format` là JSON schema (structured outputs), `stream=false`, `temperature` lấy từ `LLM_TEMPERATURE`.
  - Kết quả vẫn qua `DraftAnswer` / `ClaimReview` kiểm tra chặt như Gemini.
  - Mọi lỗi provider đổi thành `LLM_UNAVAILABLE` / `LLM_TIMEOUT`; log không chứa câu hỏi hay evidence.
- **`ModelRouter`** (`src/agents/router.py`): giữ hai provider (`local`, `cloud`), chọn đường đi cho từng lời gọi LLM và xử lý fallback.
- **Graph** (`src/agents/graph.py`):
  - Node mới `select_llm` nằm giữa `temporal_check` và `draft`.
  - `draft`, `verify`, `rewrite` đều gọi LLM qua router.
  - `verify` dùng lại provider đã soạn bản nháp.
- **Wiring** (`src/agents/factory.py`): `create_router(settings, mode)` dựng Ollama + Gemini. API (`src/api/main.py`), `scripts/ask.py`, `scripts/evaluate_ask.py`, `scripts/evaluate_faithfulness.py` đều đi qua router.
  - `AskService` nhận một generator trần thì coi đó là provider local tin cậy (dùng cho test). Vì vậy **mọi đường production phải dùng `create_router`**, không truyền Gemini trần.

## 2. Biến môi trường
| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `LOCAL_LLM_PROVIDER` | `ollama` | `none` để tắt LLM local |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | |
| `LOCAL_LLM_MODEL` | `qwen2.5:3b` | Đã pull bằng Ollama 0.40.14 |
| `LOCAL_LLM_TIMEOUT_SECONDS` | `120` | Timeout mỗi lời gọi local |
| `LOCAL_LLM_NUM_CTX` | `8192` | Context Ollama; mặc định 4096 của Ollama cắt mất system prompt (mục 5) |
| `LLM_ROUTING_MODE` | `cloud` | `auto` (cũ: `router`) / `local` / `cloud`; mặc định `cloud` đến khi P4 cải thiện local |

- Dòng trống (ví dụ `LOCAL_LLM_TIMEOUT_SECONDS=`) giờ được bỏ qua và lấy giá trị mặc định (`env_ignore_empty=True`).
- `.env.example` chưa được cập nhật các biến này (Claude bị chặn đọc file đó); cần bổ sung tay.

## 3. Luật định tuyến (theo thứ tự)
| # | Điều kiện | Đường đi | Lý do ghi trong trace |
|---|---|---|---|
| 1 | Câu hỏi có PII (đã che ở P2) | local, **cấm cloud** | `pii` |
| 1 | Có evidence `internal`/`confidential` trong **toàn bộ** evidence đã lấy của request | local, **cấm cloud** | `restricted_evidence` |
| 2 | Chỉ cấu hình một provider | provider đó | `only_local` / `only_cloud` |
| 3 | `LLM_ROUTING_MODE=local` / `cloud` | provider đó | `mode_local` / `mode_cloud` |
| 4 | `auto` và câu "đơn giản" | local | `simple` |
| 4 | `auto`, còn lại | cloud | `complex` |

- **Luật 1 thắng mọi chế độ**, kể cả `cloud`.
  - Không có LLM local → request lỗi `LLM_UNAVAILABLE` thay vì gửi cloud.
  - Evidence tích lũy qua các vòng, nên khi đã lấy phải chunk mật thì mọi lời gọi sau của request (kể cả `rewrite`) vẫn ở local.
- **Câu "đơn giản"** (`SimpleRule`, ngưỡng mặc định, `[CHƯA CHỐT]`, cần hiệu chỉnh bằng số liệu):
  - câu hỏi ≤ 30 từ;
  - ≤ 1 dấu `?`;
  - 3 evidence đầu thuộc ≤ 1 văn bản.
- **Fallback:**
  - Cloud lỗi (sau khi Gemini đã tự retry 503/429) → local, trace `fallback:local:cloud_failed`.
  - Local lỗi → cloud **chỉ khi luật 1 không áp dụng** (`fallback:cloud:local_failed`).
  - Request nhạy cảm mà local lỗi thì trả lỗi.
- **Phòng thủ nhiều lớp:** `ModelRouter._provider` từ chối đường cloud nếu quyết định có cờ `sensitive`.
- **Trace:** `route:<local|cloud>:<lý do>` ở mỗi lần chọn, `fallback:...` khi chuyển. `AskCaseResult.llm_routes` lưu các mục này cho báo cáo.

## 4. Bằng chứng (unit test, `tests/unit/test_llm_router.py`, 25 test)
- **Bảng quyết định:** 11 trường hợp, gồm PII ở chế độ `cloud`, evidence `internal`, chunk thiếu `access_level`.
- **0 lời gọi cloud** (provider giả đếm lời gọi) với:
  - evidence mật ở cả 3 chế độ;
  - PII khi chưa có evidence (vòng `rewrite`);
  - vòng 2 sau khi vòng 1 đã lấy chunk mật;
  - local lỗi trên request nhạy cảm (trả lỗi, không chuyển cloud).
- **`/ask` qua HTTP với Gemini thật trả 503** (httpx mock): Gemini được gọi 3 lần (1 lần + 2 retry), sau đó local trả lời, HTTP 200, trace có `fallback:local:cloud_failed`. Đây là tiêu chí nghiệm thu 3.
- **Ollama provider:** gửi đúng schema, parse 3 loại đầu ra; lỗi kết nối, timeout, 404 model, JSON hỏng đều đổi thành lỗi agent.

## 5. Đo thực tế local vs cloud (2026-11-06)
- **Bộ đo:** `scripts/evaluate_ask.py`, cùng 52 câu cho mọi lượt: 30 câu `qa_dev` (seed 42) và 22 câu `tests/eval/ask_dev_authored.jsonl`.
- **Release:** alias `vn_legal_bge_m3` → v2, toàn luật công khai, nên luật 1 không kích hoạt.
- **Môi trường:** BGE-M3 chạy CPU; Ollama 0.40.14, `qwen2.5:3b`; Gemini `gemini-3.5-flash-lite`.
- **Báo cáo:** `docs/reports/p3/ask_dev_{local,cloud,auto}.json`, `ask_dev_local_ctx4096.json`.

| Lượt | Decision acc. | Answer rate (answerable) | False answer (unanswerable) | Gold hit | Citation validity | Mean / p95 (ms) | Fallback |
|---|---|---|---|---|---|---|---|
| local, ctx 4096 (lỗi, xem dưới) | 0,500 | 0,472 | 0,067 | 0,824 | 1,0 | 19.647 / 72.512 | 7 lần sang cloud (local timeout) |
| **local**, ctx 8192 | **0,538** | 0,583 | 0,200 | 0,762 | 1,0 | 14.226 / 30.650 | 0 |
| **cloud** | **0,962** | 0,944 | 0,000 | 0,912 | 1,0 | 15.165 / 30.018 | 1 lần sang local |
| auto (**không hợp lệ**) | 0,673 | 0,778 | 0,200 | 0,893 | 1,0 | 8.204 / 15.347 | 37 lần sang local (Gemini hết quota, HTTP 429) |

- **Context của Ollama:**
  - Lượt đầu dùng context mặc định 4096; Ollama log `truncating input prompt` 25 lần, prompt dài nhất 6.667 token, giữ lại 4 token đầu nên mất system prompt.
  - Đã thêm `LOCAL_LLM_NUM_CTX` (mặc định 8192). Lượt sau không còn lần cắt nào.
  - VRAM khi chạy (`nvidia-smi`, `ollama ps`, cả máy): ctx 4096 → model 2,2 GB, GPU dùng 3.539/6.144 MiB; ctx 8192 → model 2,4 GB, GPU dùng 3.795/6.144 MiB. Model nằm 100% trên GPU.
- **Theo từng câu (local ctx 8192 vs cloud):**
  - cả hai đúng 27;
  - chỉ cloud đúng 23;
  - chỉ local đúng 1;
  - cả hai sai 1.
- **Lỗi của local:**
  - từ chối câu trả lời được: 13;
  - hỏi lại câu trả lời được: 2;
  - hỏi lại câu nên từ chối: 6;
  - trả lời câu mơ hồ: 3.
- **Theo loại câu (đúng local / cloud):**
  - direct: 18/28 trên 30;
  - unanswerable: 2/8 trên 8;
  - ambiguous: 3/6 trên 6;
  - adversarial: 4/5 trên 5.
- **Lượt auto:**
  - Luật "câu đơn giản" chỉ chọn local cho 6/52 câu (lần chọn đầu). Trên đúng 6 câu đó, local đúng 2, cloud đúng 6.
  - Ngưỡng hiện tại không chọn được câu mà model 3B làm tốt.
  - Số liệu tổng của lượt auto không dùng được do 37 fallback vì hết quota. Cần chạy lại khi có quota: CHƯA CHẠY.
- **Kết luận tạm** (ngưỡng nghiệm thu vẫn `[CHƯA CHỐT]`):
  - Với prompt hiện tại, `qwen2.5:3b` kém Gemini rõ rệt về quyết định (0,54 so với 0,96).
  - Citation validity vẫn 1,0 nhờ audit deterministic.
  - Đường local nên dùng cho dữ liệu nhạy cảm (bắt buộc) và làm phương án dự phòng; chưa nên dùng cho "câu đơn giản" công khai.
  - Đã chốt (Phat, 2026-11-06): mặc định `LLM_ROUTING_MODE=cloud` cho đến khi P4 (prompting) cải thiện local. Chế độ `cloud` vẫn giữ luật 1.
- **Fallback đã chứng minh trên dịch vụ thật:** lượt auto hoàn tất 52/52 câu, 0 lỗi, dù Gemini hết quota giữa chừng.

### 5b. Kiểm tra trên dữ liệu mật thật (tiêu chí nghiệm thu 1, 2026-11-06)
- **Cách chạy:** `scripts/check_llm_routing.py --role legal --routing cloud`.
  - Toàn bộ 78 câu `enterprise_v1`, truy xuất thật trên v3 (`configs/experiments/p1_release_v3.yaml`).
  - Vai trò `legal` thấy cả `internal` và `confidential`.
  - Provider cloud được bọc bởi `CloudSpy`, ghi `access_level` của evidence trong mọi lời gọi gửi tới nó, trước khi gửi HTTP.
- **Báo cáo:** `docs/reports/p3/routing_check_legal.json`.
- **Kết quả:**
  - **0 / 29 lời gọi cloud mang evidence `internal`/`confidential`** (24 draft, 5 rewrite).
  - 58/78 câu được giữ ở local vì evidence mật.
  - 10 câu trả lời có trích dẫn chunk mật, tất cả được soạn ở local.
- **Ghi chú:**
  - Gemini vẫn hết quota (28 lần HTTP 429 → fallback local), nên decision accuracy 0,416 của lượt này chủ yếu là của model local, không dùng để đánh giá chất lượng.
  - Có 1 câu nhạy cảm lỗi `LLM_UNAVAILABLE` vì local trả JSON sai schema. Câu này không được chuyển sang cloud, đúng như thiết kế.
  - **Hệ quả cần biết:** với vai trò `manager`/`legal`, 8/22 câu luật mới cũng bị giữ ở local, vì top-k có lẫn chunk nội bộ. Chất lượng các câu này phụ thuộc model 3B; cần xem lại ở P4.
- **`LLM_ROUTING_MODE=router`:** giá trị cũ trong `.env` được chấp nhận như `auto`, để API không lỗi khi khởi động.

## 6. Sửa lỗi hồi quy của P2 phát hiện khi đo
- **Triệu chứng:** sau P2 (PR #12), mọi `/search` và `/ask` trên cấu hình mặc định (alias → `vn_legal_bge_m3_v2`) đều trả `SEARCH_UNAVAILABLE`.
- **Nguyên nhân:**
  - Dense search luôn gửi bộ lọc `access_level`.
  - Qdrant Cloud từ chối lọc trên trường chưa có payload index: HTTP 400 `Index required but not found for "access_level"`.
  - Collection v2 không có index này; v3 có.
  - Unit test dùng Qdrant in-memory, không kiểm tra index, nên không bắt được.
- **Sửa** (`src/retrieval/search_adapters.py`):
  - `QdrantDenseSearch.has_restricted` lấy từ catalog (catalog đã được đối chiếu fingerprint với collection).
  - Release toàn chunk công khai thì không gửi bộ lọc; kết quả vẫn được `matches(..., access)` kiểm lại như trước.
  - Release có chunk mật (v3) vẫn lọc trong truy vấn.
- **Kiểm chứng:**
  - Test `test_public_only_release_sends_no_access_filter`.
  - Tìm kiếm thật trên Cloud alias v2 trả kết quả bình thường.

## 7. Còn mở
- Ngưỡng chất lượng local và ngưỡng "câu đơn giản": `[CHƯA CHỐT]`, chờ số liệu mục 5.
- Đo trên bộ doanh nghiệp theo vai trò (evidence mật thật trên v3): cần `/ask` eval theo vai trò, CHƯA CHẠY.
- Model 3B trả lời sai khi không có evidence (thử tay 1 câu), nên vẫn giữ audit trích dẫn và reviewer cho cả đường local.
