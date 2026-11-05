# T-0101 — Văn bản luật bổ sung cho công ty giải pháp công nghệ

> Lập: 2026-11-05 · **Danh sách đã được duyệt (2026-11-05)** · Toàn văn đã tải và xác minh từ nguồn chính thống (2026-11-05).
> Manifest chi tiết (URL, SHA-256, kích thước): `data/external/t0101/sources_manifest.json` (local, không commit vì `/data/` bị ignore).
> File gốc: `data/external/t0101/raw/<thư mục>/`.

## 1. Hiện trạng corpus (đếm từ `data/processed/documents.jsonl`)
- 3.271 văn bản, trong đó 99 Luật; năm ban hành tối đa 2021; `title` trống ở mọi văn bản, `status` hầu hết `unknown`.
- Có sẵn, liên quan doanh nghiệp công nghệ: BLDS 91/2015/QH13, Luật Doanh nghiệp 59/2020/QH14 (bản gốc), Luật An ninh mạng 24/2018/QH14,
  Luật Cạnh tranh 23/2018/QH14, Luật Chuyển giao công nghệ 07/2017/QH14, Luật Kế toán 88/2015/QH13, Luật Quản lý thuế 38/2019/QH14,
  Luật ATVSLĐ 84/2015/QH13, Luật Việc làm 38/2013/QH13, Luật sửa đổi SHTT 36/2009/QH12 (chỉ bản sửa đổi).
- Không có: Bộ luật Lao động, Luật Giao dịch điện tử, Luật Thương mại, Luật SHTT gốc, mọi văn bản 2022 trở đi.
- Định dạng đầu vào ingestion (`src/ingestion/thiemcun_adapter.py`): `legal_corpus.json` = danh sách `{law_id, articles: [{article_id, title, text}]}`.

## 2. Ảnh hưởng tới corpus hiện tại (xử lý khi build release P1)
| Văn bản trong corpus | Tình trạng (đã đối chiếu toàn văn) | Việc cần làm |
|---|---|---|
| Luật An ninh mạng 24/2018/QH14 | Hết hiệu lực từ 01/07/2026 theo Luật An ninh mạng 116/2025/QH15 | Gắn `SUPERSEDED` + `effective_to=2026-06-30` (hoặc tương đương) |
| Luật Doanh nghiệp 59/2020/QH14 | Còn hiệu lực, đã sửa đổi bởi 03/2022/QH15 và 76/2025/QH15 | Thay bằng VBHN 67/VBHN-VPQH hoặc giữ bản gốc kèm cờ "đã sửa đổi" `[CHƯA CHỐT]` |
| Luật sửa đổi SHTT 36/2009/QH12 | Đã được hợp nhất trong VBHN SHTT 160/2026 | Giữ; agent ưu tiên VBHN |

## 3. Danh sách đã duyệt và kết quả xác minh
Cột "Kiểm toàn văn" = số điều đếm được trong PDF và câu hiệu lực trích từ điều khoản thi hành / phần đầu VBHN.

| # | Văn bản | Số hiệu | Ban hành | Hiệu lực | Bản dùng để nạp | Kiểm toàn văn |
|---|---|---|---|---|---|---|
| 1 | Bộ luật Lao động | 45/2019/QH14 | 20/11/2019 | 01/01/2021 | **VBHN 18/VBHN-VPQH** (12/02/2026), gồm sửa đổi 71/2025, 113/2025, 124/2025/QH15 | 220 điều ✓ |
| 2 | Luật Bảo hiểm xã hội | 41/2024/QH15 | 29/06/2024 | 01/07/2025 | Bản gốc (datafiles.chinhphu.vn) | 141 điều ✓ |
| 3 | Luật Giao dịch điện tử | 20/2023/QH15 | 22/06/2023 | 01/07/2024 | **VBHN 36/VBHN-VPQH** (2026), gồm sửa đổi 60/2024, 116/2025, 148/2025/QH15 | 53 điều ✓ |
| 4 | Luật Bảo vệ dữ liệu cá nhân | 91/2025/QH15 | 26/06/2025 | 01/01/2026 | Bản gốc Công báo 971+972/2025 | 39 điều ✓ |
| 5 | Nghị định hướng dẫn Luật BVDLCN | 356/2025/NĐ-CP | 31/12/2025 | 01/01/2026 | Bản gốc Công báo số 18/2026 | hiệu lực 01/01/2026 ✓ |
| 6 | Luật An ninh mạng | 116/2025/QH15 | 10/12/2025 | 01/07/2026 | Bản gốc Công báo số 35/2026 | 45 điều ✓ |
| 7 | Luật Dữ liệu | 60/2024/QH15 | 30/11/2024 | 01/07/2025 | Bản gốc Công báo 1523+1524/2024 | 46 điều ✓ |
| 8 | Luật Sở hữu trí tuệ | 50/2005/QH11 | 29/11/2005 | 01/07/2006 | **VBHN 160/2026/VBHN-LQ-VPQH**, gồm sửa đổi 131/2025/QH15, 11/2026/QH16 | ✓ (ngày ký VBHN để trống trong bản PDF) |
| 9 | Luật Thương mại | 36/2005/QH11 | 14/06/2005 | 01/01/2006 | **VBHN 113/VBHN-VPQH** (27/08/2025) | ✓ |
| 10 | Luật Công nghiệp công nghệ số | 71/2025/QH15 | 14/06/2025 | 01/01/2026 (trừ một số điều) | Bản gốc Công báo 965+966/2025 | 51 điều ✓ |
| 11 | Luật Chuyển đổi số | 148/2025/QH15 | 11/12/2025 | 01/07/2026 | Bản gốc Công báo số 43/2026 | 48 điều ✓ |
| 12 | Luật Trí tuệ nhân tạo | 134/2025/QH15 | 10/12/2025 | 01/03/2026 (trừ một số nội dung) | Bản gốc Công báo số 40/2026 | 35 điều ✓ |
| 13 | Luật Doanh nghiệp | 59/2020/QH14 | 17/06/2020 | 01/01/2021 | **VBHN 67/VBHN-VPQH** (15/08/2025), Công báo 1105+1106 và 1107+1108 (2 phần) | 218 điều ✓ |

Đã giải quyết các điểm `[CHƯA XÁC MINH]` của bản trước:
- VBHN Bộ luật Lao động: cả 125/VBHN-VPQH (2025) và 18/VBHN-VPQH (12/02/2026) đều có trên Công báo; dùng bản **18** mới hơn.
- Các luật sửa đổi BLLĐ (71/2025, 113/2025, 124/2025/QH15) có trong chính VBHN 18 → xác nhận.

Còn mở:
- Luật BHXH 41/2024: nguồn thứ cấp nêu đã sửa đổi bởi 73/2025, 113/2025, 142/2025/QH15 — chưa tìm VBHN `[CHƯA XÁC MINH]`.
- Luật Dữ liệu: có VBHN 42/VBHN-VPQH 2026 (theo luatvietnam) — chưa tải bản chính thống; đang dùng bản gốc.
- Nghị định 145/2020/NĐ-CP (tuỳ chọn) chưa tải; Luật Đất đai không nạp (ngoài phạm vi).

## 4. Chuyển PDF → corpus (đã làm 2026-11-05)
- Code: `src/ingestion/official_pdf.py`, CLI `scripts/convert_official_pdfs.py`, test `tests/unit/test_official_pdf.py`.
- Cách làm: giữ span đúng cỡ chữ thân bài (bỏ chú thích VBHN, số mũ, dấu ký số); bỏ header "CÔNG BÁO/Số …/Ngày …" và số trang;
  bỏ trang scan (ảnh phủ ≥ 50% trang, kể cả lớp OCR lỗi dấu); tách điều theo dòng in đậm "Điều N."; bỏ "Chương/Mục" và tên in hoa;
  dừng ở công thức thông qua / khối chữ ký / "XÁC THỰC VĂN BẢN HỢP NHẤT" / "Nơi nhận" / Phụ lục; điều "(được bãi bỏ)" bị loại và ghi báo cáo.
- Lệnh: `python scripts/convert_official_pdfs.py --manifest data/external/t0101/sources_manifest.json --output data/external/t0101/legal_corpus_t0101.json`
- Kết quả: 13 văn bản, **1.461 điều**; `ThiemCunCorpusAdapter` đọc được, 0 bất thường; quét đầu ra: 0 header Công báo, 0 dòng chữ ký, 0 chú thích.

| Văn bản | Số điều nạp | Ghi chú |
|---|---|---|
| BLLĐ (VBHN 18) | 220 | |
| BHXH | 141 | |
| GDĐT (VBHN 36) | 52 | 1 điều bãi bỏ; trang cuối là scan → **Điều 53 bị bỏ đoạn cuối (khoản 5 cụt)** |
| BVDLCN | 39 | |
| NĐ 356/2025 | 42 | |
| An ninh mạng 2025 | 45 | |
| Dữ liệu | 46 | |
| SHTT (VBHN 160) | 225 | 15 điều bãi bỏ |
| Thương mại (VBHN 113) | 299 | 8 điều bãi bỏ + cả Mục 2 (Điều 102–116) bãi bỏ; trang cuối là scan → **thiếu thân Điều 323 và cả Điều 324** |
| CN công nghệ số | 51 | |
| Chuyển đổi số | 48 | |
| Trí tuệ nhân tạo | 35 | |
| Doanh nghiệp (VBHN 67, 2 phần) | 218 | |

Phần thiếu do trang scan (GDĐT Đ53 k5; TM Đ323–324) là điều khoản thi hành/chuyển tiếp; có thể bổ sung từ bản gốc Công báo nếu cần `[CHƯA LÀM]`.

## 5. Gộp corpus và gắn metadata (đã làm 2026-11-05)
- Code: `src/ingestion/official_metadata.py`, CLI `scripts/build_enterprise_corpus.py`, test `tests/unit/test_official_metadata.py`.
- Lệnh: `python scripts/build_enterprise_corpus.py --official-corpus data/external/t0101/legal_corpus_t0101.json --manifest data/external/t0101/sources_manifest.json --output-dir data/processed_p1`
  (đọc `data/processed`, không ghi đè; không ghi Qdrant).
- Metadata chỉ lấy từ manifest, không suy đoán: `title`, `issued_date`, `effective_from`, `source_url` (trang Công báo, thiếu thì link PDF chính thức), `source_last_verified_at`; `source_dataset=official_vn_gazette`.
- Quyết định đã áp dụng (có thể đổi trong manifest rồi build lại):
  - **Luật Doanh nghiệp 59/2020: dùng VBHN 67** thay bản gốc (cùng `document_id`); mọi `provision_id` cũ vẫn còn (0 mất) nên gold eval không đổi.
  - `status`: VBHN chính thức → `valid` (5 văn bản); bản gốc chưa kiểm tra sửa đổi sau đó → giữ `unknown` (8 văn bản).
  - Luật An ninh mạng 24/2018 → `superseded`, `effective_to=2026-06-30` (`status_overrides` trong manifest); điều khoản kế thừa status của văn bản.
- Kết quả: 3.283 văn bản (3.271 + 12 mới, 1 thay thế), 62.312 điều; status: 5 valid, 1 superseded, 3.277 unknown.
  Corpus gốc vốn có 389 `provision_id` trùng (có từ trước); bước gộp chỉ chặn trùng do văn bản mới gây ra.

## 6. Việc tiếp theo
1. `source_kind=law`, `access_level=public` thêm cùng schema của T-0102.
2. Chunk + embed BGE-M3 (revision đã khoá) `data/processed_p1` → collection Qdrant Cloud mới; cần API key có quyền ghi và thống nhất người phụ trách đổi alias.
3. T-0102 (kho quy chế nội bộ) trước khi build release chung.
