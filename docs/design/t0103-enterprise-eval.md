# T-0103 — Bộ đánh giá doanh nghiệp (luật + tài liệu nội bộ)

> Lập: 2026-11-06. Chạy trên release P1 `vn_legal_bge_m3_v3` (fingerprint `e09d3b50…`) mà **không đổi alias**.

## 1. Bộ câu hỏi `tests/eval/enterprise_v1.jsonl`
- 78 câu, tác giả soạn, **chưa có người review** (`annotation_source=authored_unreviewed`, `review_status=unreviewed`).
- Mỗi câu có `gold_provision_ids` (76 id, đều có trong release v3) và `expected_by_role` cho 3 vai trò.

| Nhóm (`category`) | Số câu | Nội dung |
|---|---|---|
| `internal_policy` | 34 | 1 điều nội bộ: public 9, internal 15, confidential 10 |
| `policy_and_law` | 12 | Điều nội bộ + điều luật mà nó dẫn chiếu (BLLĐ, BHXH, BVDLCN, TM, DN, SHTT, GDĐT) |
| `access_probe` | 5 | Cố lấy nội dung mật bằng prompt injection / mạo danh quyền |
| `out_of_scope` | 5 | Không có trong corpus (kể cả hỏi lương một người cụ thể) → mọi vai trò abstain |
| `new_law` | 22 | 13 văn bản T-0101 (AI, CNCNS, CĐS, Dữ liệu, ANM 2025, BVDLCN, GDĐT, BHXH, BLLĐ, SHTT, TM) |

- Phần nội bộ: 56 câu (yêu cầu ~50).
- Vai trò → mức truy cập (theo quyết định đã chốt trong `PROJECT_PLAN.md`): nhân viên = public; quản lý = public + internal; pháp chế = tất cả.
- `expected_by_role`:
  - **answer** khi vai trò thấy mọi điều gold;
  - **abstain** khi không thấy điều nào;
  - **answer hoặc abstain** khi chỉ thấy một phần (ví dụ nhân viên hỏi câu `policy_and_law` có điều nội bộ).

  Quy tắc nằm ở `src/core/enterprise_evaluation.py::expected_decisions`. Dữ liệu được kiểm lại bằng `gold_access` và trong unit test.
- Chỉ số rò rỉ (không câu trả lời nào của vai trò thấp trích chunk vượt quyền) được đo ở P2, khi đã có lọc quyền.

## 2. Cách chạy
- `configs/experiments/p1_release_v3.yaml`:
  - giống `configs/dev.yaml`;
  - `collection_name` ghim vào `vn_legal_bge_m3_v3`;
  - artifacts local ở `data/artifacts/releases/p1/local/`, tạo bằng `pull_cloud_release.py --step all --config-path …`;
  - báo cáo ghi vào `docs/reports/p1/`.
- `scripts/evaluate_enterprise.py --config-path configs/experiments/p1_release_v3.yaml --device cpu` ghi `docs/reports/p1/enterprise_retrieval_v1.json`.
- `scripts/evaluate_search.py --config-path configs/experiments/p1_release_v3.yaml --device cpu` ghi `docs/reports/p1/search_dev.json`.

## 3. Kết quả retrieval (hybrid, top-10, CPU, 2026-11-06)
- Đo theo vai trò bất kỳ: hiện retrieval chưa lọc quyền.
- 73 câu có gold, 0 lỗi.
- Recall là trung bình phần gold tìm thấy, nên câu nhiều gold có R@1 tối đa 0,5.

| Nhóm | Số câu | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|---|
| Tổng | 73 | 0,566 | 0,868 | 0,909 | 0,767 |
| internal_policy | 34 | 0,676 | 0,912 | 0,941 | 0,780 |
| policy_and_law | 12 | 0,361 | 0,694 | 0,778 | 0,875 |
| access_probe | 5 | 0,400 | 1,000 | 1,000 | 0,600 |
| new_law | 22 | 0,545 | 0,864 | 0,909 | 0,724 |

Theo mức truy cập của gold, R@10 / MRR:
- public: 0,938 / 0,702
- internal: 0,896 / 0,875
- confidential: 0,873 / 0,735

Latency hybrid: mean 754 ms, p95 969 ms.

**Hồi quy `qa_dev` (256 câu):**

| Release | R@10 hybrid | MRR | R@1 |
|---|---|---|---|
| v2 (`docs/reports/search_dev_windows.json`) | 0,9316 | 0,7263 | 0,6133 |
| v3 | 0,9316 | 0,7270 | 0,6172 |

Thêm 1.993 chunk mới không làm giảm retrieval trên tập cũ.

**Không vào top-10** (hybrid):
- `int_int_13`: dán dữ liệu khách hàng vào ChatGPT.
- `int_conf_08`: thẩm quyền duyệt khoản chi 300 triệu.
- `law_03`, `law_06`: hỏi ngày hiệu lực của Luật AI và Luật CNCNS. Điều "Hiệu lực thi hành" của rất nhiều luật giống nhau về câu chữ.

Hướng cải thiện: reranker (đã có ablation), hoặc ưu tiên văn bản được nêu tên trong câu hỏi. Ngưỡng nghiệm thu P1 cho bộ này vẫn `[CHƯA CHỐT]`.

## 4. Chưa làm
- Review người cho 78 câu (đổi `review_status`).
- Đo `/ask` theo vai trò (decision, rò rỉ) sau P2.
