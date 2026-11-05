# Đánh giá đầu-cuối `/ask` v1 (dev, pilot)

- Ngày chạy: 2026-11-05, máy Windows 11, CPU. Dữ liệu thô: [`ask_dev.json`](ask_dev.json).
- Lệnh: `scripts/evaluate_ask.py --device cpu`, sau đó `--retry-errors --delay 10` để chạy lại đúng các case bị lỗi provider (danh sách nằm trong `parameters.retried_error_cases`).
- LLM: `gemini-3.5-flash-lite`, temperature 0, `MAX_RETRIEVAL_ROUNDS=2`, reviewer bật, `top_k=8`. Release Qdrant: `59b8a7d0…0610` (giống báo cáo search).
- Tập case (52): 30 câu lấy mẫu có seed 42 từ `qa_dev` (nhãn gold nhập từ benchmark, `unverified_imported`) + 22 case tự soạn ở `tests/eval/ask_dev_authored.jsonl` (8 ngoài phạm vi, 6 mơ hồ, 5 adversarial, 1 temporal, 2 trích dẫn đích danh). **Case tự soạn chưa được người thứ hai review.** Không dùng `qa_test`.

## Kết quả

Run **chưa hoàn tất**: 1/52 case vẫn lỗi `LLM_UNAVAILABLE` (Gemini HTTP 503, quá tải phía provider) sau khi chạy lại; các chỉ số dưới đây tính trên 51 case đã chạy.

| Chỉ số | Giá trị | Mẫu số |
|---|---:|---|
| Decision accuracy | 0,961 | 51 case |
| Answer rate trên câu phải trả lời | 0,943 | 35 case |
| False abstention | 0,057 | 35 case |
| False answer trên câu không được trả lời (ngoài phạm vi + mơ hồ + adv_04) | 0,000 | 15 case |
| Clarify rate trên câu mơ hồ | 1,000 | 6 case |
| Citation validity (chunk tồn tại trong release) | 1,000 | mọi citation |
| Gold hit (≥ 1 citation thuộc gold provision) | 0,941 | 34 câu có gold đã trả lời |
| Citation gold precision (trung bình) | 0,922 | 34 câu |
| Độ trễ mean / p95 | 12,0 s / 29,4 s | 51 case |

Theo nhóm: `direct` 29 case chạy, accuracy 0,931, gold hit 0,926; `unanswerable` 8/8 abstain; `ambiguous` 6/6 clarify; `adversarial` 5/5 đúng hành vi (4 câu chèn lệnh vẫn trích đúng BLDS 2015 Điều 429 / Luật HNGĐ 2014 Điều 8; câu đòi in system prompt bị abstain); `temporal` 1/1 trả lời kèm `AS_OF_DATE_UNVERIFIED`; `exact_reference` 2/2.

## Phân tích lỗi (4 case sai, lượt chạy 1)

| Câu (rút gọn) | Hành vi | Hạng của gold trong Hybrid top-10 (`search_dev_windows.json`) | Nhãn |
|---|---|---|---|
| Hỗ trợ đại diện UBND xã tham gia thi hành án tử hình | abstain `INSUFFICIENT_EVIDENCE` | Không có | `retrieval_miss`; abstain là hành vi an toàn |
| Định nghĩa động vật rừng thông thường | abstain `INSUFFICIENT_EVIDENCE` | Không có | `retrieval_miss` |
| Việc trung chuyển hàng hóa | answer, trích NĐ 59/2018 Đ1 | 3 (trích hạng 1) | `ranking_error`; câu hỏi chung, có thể nhiều văn bản liên quan |
| Hồ sơ điều chỉnh giấy phép cơ sở KCB thuộc BQP | answer, trích TT 18/2014/TT-BQP Đ28 | 5 (trích hạng 2) | `ranking_error` hoặc nhãn gold chưa đầy đủ — **CHƯA XÁC MINH**, cần người đọc hai điều |

## RAGAS faithfulness (thử lần 1, chưa đủ dữ liệu)

- Lệnh: `uv run --locked --extra dev --extra eval python scripts/evaluate_faithfulness.py --device cpu`; dữ liệu thô [`faithfulness_dev.json`](faithfulness_dev.json). Judge = `gemini-3.5-flash-lite` qua endpoint OpenAI-compatible, metric `ragas.metrics.collections.Faithfulness` (ragas 0.4.3), context = toàn văn các chunk được trích.
- Chạy lại `/ask` cho 37 case cần trả lời: 18 lượt lỗi provider (14 `LLM_UNAVAILABLE` do HTTP 503, 4 `LLM_TIMEOUT`), 17 lượt trả lời, 2 abstain. Trong 17 câu trả lời, bộ chấm lỗi 10 lượt (`InstructorRetryException`, cùng thời điểm provider 503).
- Chỉ **7 câu được chấm, cả 7 đạt 1,0**. Mẫu quá nhỏ và bị chọn lọc bởi lỗi provider, **không dùng làm kết luận**. Cần chạy lại khi provider ổn định hoặc với model judge khác.
- Lưu ý kỹ thuật: ragas 0.4.3 import `langchain_community.chat_models.vertexai`, module không còn trong `langchain-community 0.4.2` của `uv.lock`; `src/evaluation/ragas_runner.py` chèn placeholder rỗng (chỉ dùng cho `isinstance`). Nên sửa gốc bằng một PR dependency riêng.

## Giới hạn

- Mẫu nhỏ (pilot); không đủ để khái quát. Case tự soạn do cùng một bên viết hệ thống tạo ra, có nguy cơ thiên lệch.
- `gold hit`/`precision` chỉ đo citation có rơi vào gold provision hay không; **không đo citation có thực sự hỗ trợ claim** (cần chấm thủ công theo roadmap §6.3) và không đo độ đúng/đầy đủ của câu trả lời.
- RAGAS mới chấm được 7 câu (mục trên); reranker đã đo riêng ở [ablation](rerank_ablation_v1.md), chưa bật trong `/ask`.
- Kết quả phụ thuộc trạng thái provider: lượt 1 có 6/52 case lỗi 503.
