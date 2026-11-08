# Báo cáo đánh giá tổng hợp P6 (bản 1, 2026-11-07; cập nhật 2026-11-08)

Mọi số dưới đây lấy từ file JSON trong repo, sinh bởi script đã chạy (ghi nguồn từng dòng). Chỗ chưa chạy ghi `CHƯA CHẠY`; số đo trên cấu hình khác cấu hình cuối thì ghi rõ cấu hình.

**Cấu hình cuối:**
- Release Qdrant Cloud v3: `vn_legal_bge_m3_v3`, 86.820 chunk, fingerprint `e09d3b5044bf…`; config `configs/experiments/p6_final.yaml` (trùng `p1_release_v3.yaml`, chỉ khác thư mục báo cáo).
- Hybrid BGE-M3 + BM25 + RRF, reranker tắt.
- `.env` lúc chạy: `LLM_ROUTING_MODE=router` (auto), cloud `gemini-3.5-flash-lite`, local `qwen2.5:3b`, `PROMPT_VARIANT=zero_shot`, `LOCAL_PROMPT_VARIANT=cot`.
- Tất cả bộ câu hỏi đều thuộc **dev** hoặc tự soạn chưa qua review người. `qa_test` **chưa chạy** (chờ chốt).

## 1. Retrieval (không gọi LLM)

| Bộ | Chỉ số | Kết quả | Nguồn |
|---|---|---|---|
| qa_dev (256 câu) | Hybrid R@1 / R@5 / R@10 / MRR@10 | 0,617 / 0,873 / 0,932 / 0,727 | `docs/reports/p6/search_dev.json` |
| qa_dev | Dense R@10 / BM25 R@10 | 0,920 / 0,846 | như trên |
| enterprise_v1 (73 câu có gold) | R@1 / R@5 / R@10 / MRR | 0,566 / 0,868 / 0,909 / 0,767 | `docs/reports/p6/enterprise_retrieval_v1.json` |
| enterprise_v1 theo mức | R@10 public / internal / confidential | 0,938 / 0,896 / 0,873 | như trên |
| Rò rỉ quyền ở retrieval | Kết quả vượt quyền / tổng (78 câu × 3 vai trò × top-10) | **0 / 2.340** | `docs/reports/p6/access_leakage_v1.json` |

Chỉ số qa_dev và enterprise trùng khít lần đo P1 trên cùng release (`docs/reports/p1/`), nghĩa là P2–P5 không làm thay đổi chất lượng retrieval. Độ trễ lần này cao hơn (enterprise mean 1.013 ms so với 743 ms) vì máy chạy song song các tác vụ khác.

Reranker (đo ở P0, `docs/reports/rerank_ablation_v1.md`): MRR 0,719 → 0,809 nhưng thêm 17–33 s/câu trên CPU, nên vẫn tắt.

## 2. `/ask` đầu-cuối

| Bộ | Cấu hình | Decision accuracy | Ghi chú | Nguồn |
|---|---|---|---|---|
| dev 52 câu (30 qa_dev + 22 tự soạn) | cloud, zero_shot | **0,981** | false answer 0 | `docs/reports/p4/ask_dev_cloud_zero_shot.json` (2026-11-06) |
| dev 52 câu | local qwen2.5:3b, cot | 0,654 | | `docs/reports/p4/ask_dev_local_cot.json` |
| dev 52 câu | **auto (cấu hình cuối)** | 0,923 | Lượt sạch 2026-11-08 (3 lỗi 429 được retry, không fallback do quota). So với cloud: kém 3 câu (2 câu bị route local vì "đơn giản"), hơn 0 câu; sign test p≈0,25. Lời gọi cloud 102 so với 110 | `docs/reports/p6/ask_dev_auto.json` |
| enterprise 78 câu, vai trò legal | cloud (evidence mật → local) | 0,416 | 0/29 lời gọi cloud mang evidence mật | `docs/reports/p3/routing_check_legal.json` |
| enterprise 78 câu, vai trò employee | auto | 0,795 | 2026-11-08; 1 fallback. internal_policy 24/34, policy_and_law 12/12, access_probe 3/5, out_of_scope 5/5, new_law 18/22. 0 lời gọi cloud mang evidence mật; 0 câu trả lời trích tài liệu ngoài quyền | `docs/reports/p6/ask_enterprise_employee.json` (lượt 07/11 bị 429: `…_quota429.json`, không tính) |
| enterprise 78 câu, vai trò manager | auto | 0,571 | 2026-11-08; 1 lỗi `LLM_TIMEOUT`; 46 câu chạy local vì có evidence internal. internal_policy 17/34, policy_and_law 5/12, access_probe 3/4, out_of_scope 4/5, new_law 15/22. 0 lời gọi cloud mang evidence mật; 10 câu trích tài liệu internal (trong quyền), 0 trích confidential | `docs/reports/p6/ask_enterprise_manager.json` |

Nhận xét:
- Với câu hỏi luật công khai, cloud đạt chất lượng cao (0,981 trên dev). Chế độ auto (0,923) chỉ bớt 8 lời gọi cloud nhưng kém hơn trên 3 câu, không câu nào tốt hơn → **khuyến nghị cấu hình cuối `LLM_ROUTING_MODE=cloud`** (quy tắc evidence mật/PII → local vẫn áp dụng ở mọi chế độ). Khác biệt chưa có ý nghĩa thống kê (p≈0,25, n=52).
- Chất lượng giảm theo lượng evidence mật mà vai trò được thấy: employee 0,795 → manager 0,571 → legal 0,416 (legal đo ở chế độ cloud, P3). Nguyên nhân chung: câu có evidence internal/confidential buộc chạy model local 3B.
- Điểm yếu lớn nhất là **câu hỏi có evidence mật**. Theo quy tắc bảo mật, những câu này chỉ được chạy trên model local 3B, và model này trả lời kém (legal 0,416). Đây là cái giá có chủ đích của quy tắc "dữ liệu mật không lên cloud". Hướng cải thiện: model local lớn hơn (cần GPU/VRAM, phải hỏi trước khi tải), hoặc dùng một LLM được phép xử lý dữ liệu mật.

## 3. Faithfulness (RAGAS)

Chạy 2026-11-08 (`docs/reports/p6/faithfulness_dev.json`; judge `gemini-3.5-flash-lite`, context = text các chunk được trích): 37 câu, 33 answer, 4 abstain, 0 lỗi `/ask`. **Chỉ chấm được 10/33 câu** vì judge RAGAS gặp Gemini 429 (44 lỗi trong log). Trên 10 câu này: mean faithfulness 0,95, min 0,5, 1 câu < 1.

Mẫu nhỏ và không ngẫu nhiên (câu được chấm là câu chạy trước khi hết quota), nên **chưa đủ để kết luận**; chỉ là tín hiệu tích cực. Lần P0 (`docs/reports/faithfulness_dev.json`) cũng chỉ chấm 7/17.

## 4. Bảo mật (red-team)

Chi tiết ở `docs/security_checklist.md` §5 và `docs/reports/p6/red_team_v1.json` (chạy 2026-11-06, Gemini còn quota; 31 câu, 1 lỗi `LLM_TIMEOUT`):
- **0 vi phạm bảo mật**: không trích nguồn ngoài quyền, không lộ PII hay system prompt, không có lời gọi Gemini mang evidence mật hoặc PII (56 lời gọi), log sạch. Probe API/MCP đạt 9/9.
- Hành vi đúng kỳ vọng 23/30: injection 3/3, lộ prompt 2/2, PII 3/3, ngoài phạm vi 3/3, leo quyền và access probe 12/19. 4/7 câu lệch là vai trò legal không trả lời được vì model local 3B.
- PII (P2): 34/34, 0 dương tính giả trên tập phát triển (`docs/reports/p2/pii_v1.json`).

## 5. Prompting (P4)

`docs/reports/prompting_v1.md`: không có khác biệt có ý nghĩa thống kê giữa zero-shot, few-shot và CoT. Chọn zero-shot cho cloud (0,981) và CoT cho local (0,654).

## 6. Hạn chế của đánh giá

- Bộ tự soạn (`ask_dev_authored` 22 câu, `enterprise_v1` 78 câu, `red_team_v1` 16 câu) **chưa qua review người**.
- Mọi số đều trên dev. `qa_test` chưa chạy.
- Chất lượng cloud phụ thuộc quota Gemini miễn phí: các lượt bị 429/503 bị loại, không tính. Faithfulness bị cắt còn 10/33 câu vì lý do này.
- Mỗi lần khởi động script trên máy Windows/CPU/HDD mất khoảng 40 phút (nạp model + catalog), nên một lượt eval đầy đủ tốn nhiều giờ.

## 7. Còn phải chạy

1. Faithfulness đủ 33 câu: `evaluate_faithfulness.py --config-path <abs>/configs/experiments/p6_final.yaml --device cpu` vào ngày Gemini còn quota, không chạy chung với eval khác (judge cần nhiều request).
2. `qa_test` một lần, nếu được chốt.
3. Review người cho các bộ tự soạn.
