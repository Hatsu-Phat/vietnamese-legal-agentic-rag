# P2 / T-0201 — Phân quyền theo vai trò

> Lập: 2026-11-06. Phạm vi: xác thực API key → vai trò → phạm vi truy cập; lọc deterministic ở tầng retrieval.
> PII guardrail (T-0202) chưa làm.

## 1. Vai trò và ma trận quyền
| Vai trò | Header `X-API-Key` | Được thấy |
|---|---|---|
| (không gửi key) | — | `public` |
| `employee` | `AUTH_API_KEY_EMPLOYEE` | `public` |
| `manager` | `AUTH_API_KEY_MANAGER` | `public`, `internal` |
| `legal` | `AUTH_API_KEY_LEGAL` | `public`, `internal`, `confidential` |

- **Nguồn cấu hình:** ma trận nằm trong `access_control` của `configs/dev.yaml`. Startup kiểm tra:
  - mọi vai trò đều có mặt;
  - mọi phạm vi đều chứa `public`;
  - quyền ẩn danh không vượt quá `employee`.

  Unit test khẳng định YAML trùng `DEFAULT_ROLE_ACCESS` trong `src/core/access.py`; bộ eval T-0103 dùng chính hằng này.
- **Key:** chỉ đặt trong `.env`, không bao giờ trong YAML.
  - Mỗi key dài tối thiểu 16 ký tự, các vai trò không được trùng key; key rỗng coi như tắt.
  - So khớp bằng `hmac.compare_digest` trên mọi key.
  - Không ghi key vào log; log chỉ ghi danh sách vai trò đã cấu hình key.
- **Key sai → 401 `UNAUTHORIZED`**, không hạ xuống quyền ẩn danh, để client biết key hỏng. Không gửi key → quyền ẩn danh (`public`), nên `/search` cho luật vẫn dùng được như trước.

## 2. Cách lọc
- **Phạm vi do server đặt.** Phạm vi (`AccessScope`) là tham số riêng do server truyền vào: `search`, `diagnose`, `retrieve`, `matches`, `get_source`, `AskService.ask`, `AgentState.access`.
  - Không nằm trong `SearchFilters` hay `AskRequest`.
  - Body gửi `filters.access_level` bị 422, do `extra="forbid"`.
- **Mặc định an toàn.** Ở mọi tầng, giá trị mặc định là `PUBLIC_ONLY`; quên truyền phạm vi thì chỉ mất quyền đọc, không lộ dữ liệu.
  - Script đo chất lượng retrieval (`evaluate_enterprise.py`) truyền rõ `FULL_ACCESS`.
- **Dense (Qdrant):** điều kiện được thêm vào `query_filter` trước top-k, gồm `access_level ∈ phạm vi` hoặc `access_level` rỗng.
  - Điểm thiếu `access_level` là luật của release trước P1 (v2), luôn là public.
  - Kết quả trả về được kiểm lại bằng `matches(...)`; nếu lọt điểm ngoài phạm vi thì trả `SEARCH_UNAVAILABLE` thay vì lộ.
- **BM25:** lọc theo metadata trước khi chọn top-k, nên không bị hụt kết quả.
- **Sau RRF:** service kiểm lại mọi ứng viên bằng `matches(chunk, filters, access)`.
- **`/sources/{chunk_id}`:** chunk ngoài phạm vi trả 404 `SOURCE_NOT_FOUND`, giống hệt chunk không tồn tại, nên người hỏi không biết tài liệu mật có tồn tại hay không.
- **`/ask`:** agent chỉ nhận evidence đã lọc. Giới hạn: chưa có router; evidence mật vẫn có thể đi tới Gemini khi vai trò `legal` hỏi. Đây là việc của P3.

## 3. Kiểm chứng
- Unit test `tests/unit/test_authz.py` (9 test):
  - Qdrant in-memory thật và BM25 thật, catalog trộn đủ các mức truy cập, gồm cả chunk luật kiểu cũ không có trường quyền.
  - Mỗi vai trò, cả hai nhánh chỉ thấy đúng phạm vi; top-k nhỏ vẫn đủ kết quả.
  - Lọc theo `document_id` của tài liệu mật không mở rộng được quyền.
  - API: chuyển đúng phạm vi; key sai → 401 (thân lỗi không chứa key); 404 giống nhau.
  - Agent truyền đúng phạm vi.
- Kiểm tra rò rỉ trên release v3 thật: xem mục 4.

## 4. Kết quả rò rỉ trên v3
Lệnh `scripts/evaluate_enterprise.py --config-path configs/experiments/p1_release_v3.yaml --device cpu --leakage` (2026-11-06) chạy cả 78 câu T-0103 dưới cả 3 vai trò, top-10, qua `SearchService` thật (Qdrant Cloud v3 + BM25). Kết quả lưu ở `docs/reports/p1/access_leakage_v1.json`.

| Vai trò | Truy vấn | Lỗi | Kết quả trả về | Ngoài phạm vi | Câu có gold được thấy | Tìm được gold đó trong top-10 |
|---|---|---|---|---|---|---|
| employee | 78 | 0 | 780 | **0** | 43 | 0,884 |
| manager | 78 | 0 | 780 | **0** | 58 | 0,931 |
| legal | 78 | 0 | 780 | **0** | 73 | 0,945 |

- 0 kết quả vượt quyền trên tổng 2.340 kết quả, kể cả 5 câu `access_probe` (prompt injection, mạo danh quyền). Lọc nằm ở tầng retrieval nên câu chữ trong prompt không ảnh hưởng.
- Đo lại retrieval với `FULL_ACCESS` cho số liệu trùng T-0103 (R@10 0,909, MRR 0,767), nghĩa là thêm tham số phạm vi không đổi kết quả.
- Chưa làm:
  - smoke qua HTTP trên v3: API mặc định đọc alias, hiện vẫn là v2 và chưa có tài liệu nội bộ;
  - đo `/ask` theo vai trò (decision/abstain), vì cần gọi LLM.

## 5. Điều kiện đổi alias sang v3
Lọc quyền ở retrieval đã có. Tuy vậy theo quyết định đã chốt, **evidence mật không được gửi Gemini**, mà router local/cloud là việc của P3. Nếu đổi alias ngay, vai trò `legal` hỏi về tài liệu mật sẽ gửi đoạn mật lên Gemini. Đề xuất: đổi alias sau P3, hoặc chặn tạm `confidential` khỏi `/ask` cho tới khi có model local.
