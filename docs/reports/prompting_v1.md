# P4 — Thí nghiệm prompting (bước soạn câu trả lời)

> Chạy: 2026-11-06.
> - **Bộ đo:** 52 câu, gồm 30 câu `qa_dev` (seed 42) và 22 câu `tests/eval/ask_dev_authored.jsonl`. Không dùng `qa_test`.
> - **Release:** v3 (fingerprint `e09d3b5044bf`), quyền ẩn danh (`public`).
> - **Model:** local `qwen2.5:3b` (Ollama 0.40.14, `num_ctx` 8192); cloud `gemini-3.5-flash-lite`. Temperature 0.
> - **Script:** `scripts/evaluate_ask.py --routing local|cloud --prompt <variant>`. Bảng sinh bởi `scripts/compare_prompting.py`.
> - **Báo cáo:** `docs/reports/p4/ask_dev_*.json`.

## 1. Biến thể
- Chỉ prompt `DRAFT_SYSTEM` thay đổi; prompt rewrite và review giữ nguyên để so sánh công bằng (`src/agents/prompts.py`).
- Chọn biến thể bằng `PROMPT_VARIANT` hoặc `--prompt`. Mặc định `zero_shot`.

| Biến thể | Nội dung |
|---|---|
| `zero_shot` | Prompt hiện tại (baseline) |
| `few_shot` | Thêm 3 ví dụ answer / abstain / clarify, evidence là nguyên văn Điều 19 và 20 Bộ luật Dân sự 91/2015/QH13 |
| `cot` | Yêu cầu trường `reasoning` (≤ 5 câu) trước khi quyết định |

- **Few-shot:** hai điều luật dùng làm ví dụ không là gold của bộ eval nào; `test_few_shot_examples_are_not_eval_gold` kiểm điều này.
- **CoT:** `reasoning` nằm trong JSON nhưng bị `coerce_draft` bỏ trước khi validate. Vì vậy nó không vào audit, reviewer hay response (có test). Với Ollama, `reasoning` được đặt đầu schema để model viết trước khi quyết định.

## 2. Kết quả: local (`qwen2.5:3b`)
- Hai biến thể được chạy 2 lần để đo dao động. "Wins/losses" tính theo từng câu so với lượt `zero_shot` đầu tiên.
- p là sign test hai phía trên các câu khác nhau, chỉ để tham khảo.

| Report | Prompt | Decision acc. | Answer rate | False answer | Clarify (ambig.) | Gold hit | Citation valid. | Mean / p95 ms | Calls | Prompt tok/case | Output tok/case | Schema errors | Wins/losses vs base | Sign test p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `ask_dev_local_zero_shot.json` | zero_shot | 0.615 | 0.667 | 0.133 | 0.667 | 0.792 | 1.000 | 11593 / 27998 | 131 | 5974 | 226 | 0 | 0/0 | — |
| `ask_dev_local_zero_shot_rerun.json` | zero_shot | 0.558 | 0.611 | 0.200 | 0.500 | 0.773 | 1.000 | 11468 / 23154 | 144 | 6317 | 249 | 0 | 1/4 | 0.375 |
| `ask_dev_local_few_shot.json` | few_shot | 0.577 | 0.528 | 0.000 | 0.833 | 0.684 | 1.000 | 12556 / 25725 | 147 | 7598 | 230 | 0 | 4/6 | 0.754 |
| `ask_dev_local_cot.json` | cot | 0.654 | 0.639 | 0.067 | 0.667 | 0.913 | 1.000 | 14695 / 26916 | 151 | 6894 | 375 | 0 | 8/6 | 0.791 |
| `ask_dev_local_cot_rerun.json` | cot | 0.654 | 0.639 | 0.067 | 0.667 | 0.913 | 1.000 | 16143 / 35889 | 151 | 6894 | 375 | 0 | 8/6 | 0.791 |

**Dao động giữa các lần chạy:**
- Hai lượt `zero_shot` cùng cấu hình lệch nhau ở 5/52 câu: 0,615 và 0,558.
- Hai lượt `cot` cho kết quả trùng khớp từng câu.
- Trung bình hai lượt: `zero_shot` 0,587, `cot` 0,654.
- Tính trên câu ổn định ở cả hai lượt của mỗi biến thể: `cot` đúng mà `zero_shot` sai ở 8 câu; ngược lại 4 câu. Sign test p ≈ 0,39, **không có ý nghĩa thống kê**.

## 3. Kết quả: cloud (Gemini)
- Chạy với `LOCAL_LLM_PROVIDER=none`, nên lỗi cloud không được chuyển sang local.
- 3 câu lỗi tạm thời (2 timeout, 1 HTTP 429) được chạy bù bằng `--retry-errors`. Số lời gọi và token của lần chạy bù đã được cộng vào.

| Report | Prompt | Decision acc. | Answer rate | False answer | Clarify (ambig.) | Gold hit | Citation valid. | Mean / p95 ms | Calls | Prompt tok/case | Output tok/case | Schema errors | Wins/losses vs base | Sign test p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `ask_dev_cloud_zero_shot.json` | zero_shot | 0.981 | 0.972 | 0.000 | 1.000 | 0.972 | 1.000 | 6358 / 8983 | 110 | 5504 | 235 | 0 | 0/0 | — |
| `ask_dev_cloud_few_shot.json` | few_shot | 0.942 | 0.917 | 0.000 | 1.000 | 0.882 | 1.000 | 4148 / 5571 | 109 | 6196 | 218 | 0 | 0/2 | 0.500 |
| `ask_dev_cloud_cot.json` | cot | 0.962 | 0.944 | 0.000 | 1.000 | 0.943 | 1.000 | 5904 / 12299 | 110 | 5748 | 355 | 0 | 0/1 | 1.000 |

## 4. Lỗi theo loại

| Lượt | Từ chối câu trả lời được | Hỏi lại câu trả lời được | Hỏi lại câu nên từ chối | Trả lời câu mơ hồ / khác |
|---|---|---|---|---|
| local zero_shot (lượt 1 / 2) | 9 / 13 | 3 / 1 | 6 / 6 | 2 / 3 |
| local few_shot | 14 | 3 | 4 | 1 |
| local cot | 11 | 2 | 3 | 2 |
| cloud zero_shot | 1 | 0 | 0 | 0 |
| cloud few_shot | 2 | 1 | 0 | 0 |
| cloud cot | 2 | 0 | 0 | 0 |

- Lỗi chính của model 3B vẫn là **từ chối câu trả lời được**: 9–14/36 câu trả lời được.
- Few-shot giảm lỗi trả lời sai câu mơ hồ, nhưng làm model từ chối nhiều hơn.
- CoT giảm lỗi "hỏi lại câu nên từ chối" (6 → 3) và có gold hit cao nhất (0,913).

## 5. Chi phí
- **Prompt:**
  - few-shot thêm khoảng 1.600 token/câu trên local và 700 token/câu trên Gemini;
  - CoT thêm khoảng 900 token prompt/câu trên local và 250 trên Gemini;
  - CoT tăng output khoảng 60%: 226 → 375 token/câu (local), 235 → 355 (Gemini).
- **Cắt prompt:** few-shot và CoT mỗi biến thể có 1 lần Ollama cắt prompt vì vượt 8192 token (theo `server.log`). Zero-shot có 0 lần.
- **Lỗi schema:** 0 ở mọi lượt. Structured outputs của Ollama và JSON mode của Gemini đều giữ được JSON hợp lệ với cả CoT.
- **Faithfulness (RAGAS):** CHƯA CHẠY, để tiết kiệm quota Gemini.

## 6. Kết luận và đề xuất (ngưỡng nghiệm thu vẫn `[CHƯA CHỐT]`)
- **Cloud:** giữ `zero_shot`, cao nhất 0,981. Hai biến thể kia chỉ thua, không thắng câu nào.
- **Local:**
  - `cot` tốt nhất về trung bình (0,654 so với 0,587) và gold hit (0,913), nhưng khác biệt chưa có ý nghĩa thống kê trên 52 câu.
  - Khoảng cách với cloud vẫn lớn: 0,65 so với 0,98.
  - Prompting đơn thuần **chưa đủ** để chuyển lại `LLM_ROUTING_MODE=router`; giữ `cloud`.
- **Đề xuất bước sau:**
  - Cho phép đặt prompt riêng cho local (`cot`) và cloud (`zero_shot`).
  - Thử giảm số evidence gửi cho local (8 → 4–5) để giảm prompt và giảm từ chối.
  - Hoặc thử model local lớn hơn nếu VRAM cho phép (6 GB; hiện dùng 3,8 GB).

## 7. Cấu hình chọn theo số liệu và thí nghiệm giảm evidence cho local
- **Prompt theo provider** (đã cài):
  - `LOCAL_PROMPT_VARIANT`, mặc định `cot`, cho model local;
  - `PROMPT_VARIANT`, mặc định `zero_shot`, cho Gemini;
  - `--prompt` của `evaluate_ask.py` ghi đè cả hai (dùng cho thí nghiệm).
- **`LOCAL_LLM_MAX_EVIDENCE`** (mặc định trống = gửi đủ evidence như cloud): chỉ đưa N đoạn đầu cho model local.
  - Audit trích dẫn, review và response chỉ dùng đúng N đoạn đó. Model local trích một đoạn nó chưa thấy sẽ bị audit loại (có test).

**Thí nghiệm** (local, `cot`, cùng 52 câu, v3):
- Lượt 5 evidence có 9 câu lỗi `SEARCH_UNAVAILABLE` (Qdrant Cloud gián đoạn tạm thời, không phải LLM); đã chạy bù bằng `--retry-errors`, 0 lỗi còn lại.
- Cột Prompt ghi `local=cot,cloud=zero_shot` là cấu hình mặc định mới; với `--routing local` chỉ model local được dùng.

| Report | Prompt | Decision acc. | Answer rate | False answer | Clarify (ambig.) | Gold hit | Citation valid. | Mean / p95 ms | Calls | Prompt tok/case | Output tok/case | Schema errors | Wins/losses vs base | Sign test p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `ask_dev_local_cot.json` | cot | 0.654 | 0.639 | 0.067 | 0.667 | 0.913 | 1.000 | 14695 / 26916 | 151 | 6894 | 375 | 0 | 0/0 | — |
| `ask_dev_local_cot_ev5.json` | local=cot,cloud=zero_shot | 0.615 | 0.611 | 0.000 | 0.667 | 0.864 | 1.000 | 11028 / 20730 | 129 | 4195 | 313 | 0 | 7/9 | 0.804 |
| `ask_dev_local_cot_ev4.json` | local=cot,cloud=zero_shot | 0.596 | 0.639 | 0.067 | 0.333 | 0.870 | 1.000 | 13349 / 32483 | 137 | 3750 | 313 | 0 | 5/8 | 0.581 |

- **Kết luận:** giảm evidence giảm khoảng 40–45% token prompt (6.894 → 4.195 / 3.750 mỗi câu), nhưng **không cải thiện** quyết định (0,654 → 0,615 / 0,596) và gold hit (0,913 → 0,864 / 0,870).
- Vì vậy giữ mặc định gửi đủ evidence. Biến `LOCAL_LLM_MAX_EVIDENCE` vẫn để lại để dùng khi cần giảm VRAM hoặc độ trễ.
- **Còn lại để thu hẹp khoảng cách với cloud** (0,65 so với 0,98): thử model local lớn hơn trong giới hạn 6 GB VRAM. Đây là việc riêng, cần tải model mới, nên phải hỏi trước.
