# Ablation reranker v1 (Phase 5, dev)

- Ngày chạy: 2026-11-05, Windows 11, **CPU** (torch CPU theo `uv.lock`). Dữ liệu thô: [`rerank_ablation_dev.json`](rerank_ablation_dev.json).
- Lệnh: `uv run --locked --extra dev --extra eval python scripts/run_ablation.py --device cpu` (code: `src/evaluation/rerank_ablation.py`, `src/retrieval/reranker.py`).
- Reranker: `BAAI/bge-reranker-v2-m3`, revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`, `max_length=512`, đầu vào `LegalChunk.embedding_text`.
- Tập: `qa_dev` 256 câu (không dùng `qa_test`). Release `59b8a7d0…0610`.
- Thiết kế paired: mỗi câu một lượt Hybrid với pool 20 ứng viên; ba cấu hình dùng chung danh sách đó.
  - `hybrid@10`: 10 chunk đầu sau RRF (không rerank).
  - `rerank_top10`: rerank đúng 10 chunk đó (tách hiệu ứng reranker khỏi kích thước pool).
  - `rerank_pool20`: rerank 20 chunk, giữ 10.
  - Metric ở mức provision (gộp chunk cùng điều), cắt 10.

## Kết quả

Run **chưa hoàn tất**: 9/256 câu lỗi `SEARCH_TIMEOUT` (deadline 30 s) vì reranker và search dùng chung CPU; số liệu tính trên 247 câu còn lại.

| Cấu hình | R@1 | R@5 | R@10 | MRR@10 | Latency mean | p95 |
|---|---:|---:|---:|---:|---:|---:|
| hybrid@10 | 0,603 | 0,870 | 0,929 | 0,719 | 2,6 s | 8,1 s |
| rerank_top10 | 0,721 | 0,917 | 0,929 | 0,809 | 19,1 s | 29,0 s |
| rerank_pool20 | 0,725 | 0,937 | 0,949 | 0,819 | 35,7 s | 48,5 s |

So sánh từng câu theo reciprocal rank (247 câu): `rerank_top10` tốt hơn 59, kém hơn 20, bằng 168; `rerank_pool20` tốt hơn 64, kém hơn 23, bằng 160. Sign test hai phía (bỏ câu bằng nhau): p ≈ 1,3e-5 cho cả hai.

Latency ở bảng này cao hơn lần chạy search riêng (`search_dev_windows.json`: Hybrid mean 0,70 s, p95 0,95 s) vì CPU bị reranker chiếm đồng thời; latency reranked = thời gian search + thời gian rerank.

## Kết luận

- Reranker cải thiện thứ hạng có ý nghĩa trên dev: MRR +0,09–0,10, R@1 +12 điểm. Phần lớn lợi ích đến từ việc sắp lại top-10 (R@10 không đổi); pool 20 thêm +2 điểm R@10.
- Trên CPU, reranker thêm 17–33 s mỗi câu và gây timeout cho search. **Giữ `retrieval.reranking.enabled: false`** trong cấu hình production cho tới khi có GPU, hoặc thử model/độ dài nhỏ hơn và đo lại.
- Lỗi xếp hạng ghi trong [đánh giá `/ask`](ask_eval_v1.md) (gold ở hạng 3–5, LLM trích hạng 1–2) là đúng loại lỗi reranker sửa được; chưa đo hiệu ứng đầu-cuối trên `/ask`.

## Giới hạn

- Một lần chạy, một seed, chỉ tập dev; chưa xác nhận trên `qa_test` (giữ cho báo cáo cuối).
- `hybrid@10` dùng pool 20 mỗi nhánh nên khác nhẹ production (pool 10): R@10 0,929 so với 0,932 ở `search_dev.json`.
- Nhãn gold nhập từ benchmark, chưa được người review (`unverified_imported`).
