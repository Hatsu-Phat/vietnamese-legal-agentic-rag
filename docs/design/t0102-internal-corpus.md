# T-0102 — Kho tài liệu nội bộ và release P1 (luật + nội bộ chung một collection)

> Lập: 2026-11-06. Yêu cầu của chủ repo: soạn khoảng 50 tài liệu nội bộ, lưu **chung collection** với luật trên Qdrant Cloud.

## 1. Bộ tài liệu
- Công ty hư cấu: **Công ty Cổ phần Giải pháp Công nghệ Thiên Ngân** (ký hiệu `TNT`), sản phẩm giả định TN-HRM (SaaS nhân sự) và TN-Desk (trợ lý AI hỗ trợ khách hàng).
- Nguồn: `corpus/internal/*.md` (commit vào repo; không có dữ liệu cá nhân thật, người ký chỉ ghi chức danh, mọi con số là giả định).
- 53 văn bản, 249 điều: `public` 8, `internal` 36, `confidential` 9.

| Nhóm | Văn bản (số hiệu) | Mật |
|---|---|---|
| Nhân sự | 01 Nội quy lao động; 02 Tuyển dụng; 03 Thử việc; 04 Thời giờ làm việc; 05 Làm việc từ xa; 06 Làm thêm giờ/trực sự cố; 07 Nghỉ phép; 08 Phúc lợi; 11 Đánh giá hiệu suất; 12 Đào tạo; 13 Kỷ luật; 14 Chống quấy rối; 15 Nghỉ việc; 17 Công tác phí; 19 Tố giác | internal |
| Nhân sự (mật) | 09 Quy chế lương; 10 Quy chế thưởng; 16 ESOP 2026 | confidential |
| Công khai | 18 Bộ quy tắc ứng xử; 20 Chính sách tuyển dụng; 28 Chính sách bảo vệ DLCN; 34 Minh bạch AI; 40 Điều khoản dịch vụ; 41 SLA; 45 Chống tham nhũng; 46 Hỗ trợ kỹ thuật | public |
| An ninh, dữ liệu, AI | 21 Chính sách ANTT; 22 Phân loại tài liệu; 23 Quản lý truy cập; 24 Tài nguyên CNTT; 25 Ứng phó sự cố; 26 Phát triển an toàn; 27 Sao lưu/BCP; 29 DLCN người lao động; 30 Yêu cầu chủ thể dữ liệu; 31 Đánh giá tác động; 32 Dùng AI tạo sinh; 33 Quản trị AI sản phẩm; 35 Nhà cung cấp | internal |
| Pháp chế, thương mại | 37 Sở hữu trí tuệ; 38 Nguồn mở; 39 Hợp đồng; 42 Mẫu NDA; 43 Chữ ký điện tử; 48 Tài sản; 49 Quản trị nội bộ; 53 Truyền thông | internal |
| Mật khác | 36 Báo cáo rủi ro ANTT 2026; 44 Chính sách giá; 47 Chi tiêu và phân cấp phê duyệt; 50 Giao dịch người có liên quan; 51 Tái cấu trúc 2026–2027; 52 Kế hoạch kinh doanh 2027 | confidential |

- Dẫn chiếu pháp luật: 73 dẫn chiếu "Điều N <luật>" đều tồn tại trong corpus T-0101 (kiểm bằng script). Các con số pháp định được dùng
  (thử việc ≤ 60 ngày và lương thử việc ≥ 85%; làm thêm 150/200/300%, ≤ 40 giờ/tháng, ≤ 200 giờ/năm; phép năm 12 ngày; thời hiệu kỷ luật 6/12 tháng;
  báo trước 45/30/3 ngày; thanh toán khi nghỉ việc 14 ngày làm việc; BHXH 8% và 3% + 14%; thông báo vi phạm DLCN 72 giờ; phạt vi phạm ≤ 8%;
  ngưỡng 35% tài sản; cổ đông trên 10%) đã đối chiếu với toàn văn.
- Tham chiếu chéo giữa văn bản nội bộ (ví dụ Nội quy → Quy chế kỷ luật 13/2026) đều trỏ tới văn bản có thật trong bộ.

## 2. Nạp và release
- Bộ nạp: `src/ingestion/internal_docs.py`. `document_id` = `internal_<năm>_<loại>_tnt_<số>` (không trùng id luật), `source_kind=internal`,
  `status=valid`, `source_dataset=internal_fictional_company`.
- Release: `src/ingestion/indexing/enterprise_release.py`, CLI `scripts/build_enterprise_release.py --step prepare|upload|verify|activate`.
  - Giữ 84.827 chunk chuẩn hiện có (gắn `law/public`, status từ `data/processed_p1`), bỏ chunk cũ của Luật DN 59/2020 (thay bằng VBHN),
    thêm chunk của 13 luật T-0101 và 249 điều nội bộ: tổng **86.820 chunk**; `law/public` 86.571, `internal/internal` 169,
    `internal/confidential` 42, `internal/public` 38.
  - Vector của chunk giữ nguyên được **chép từ collection đang phục vụ** (`vn_legal_bge_m3_v2`); chỉ encode 1.993 chunk mới bằng BGE-M3
    revision `5617a9f6…` (CPU).
  - Collection mới `vn_legal_bge_m3_v3`, metadata `source_fingerprint` = `e09d3b50…`. Alias `vn_legal_bge_m3` **chưa đổi**; đổi bằng
    `--step activate --expected-current vn_legal_bge_m3_v2`, sau đó mọi máy chạy `scripts/pull_cloud_release.py` để lấy artifacts mới.

- Đã upload và kiểm chứng (2026-11-06, `data/artifacts/releases/p1/verification.json`): 86.820 điểm; so khớp payload 86.820/86.820;
  32 vector chép + 32 vector encode lấy mẫu khớp (cosine ≥ 0,999); đếm theo `access_level`: public 86.609, internal 169, confidential 42.

## 3. Chưa làm
- Lọc theo quyền khi truy xuất (P2): hiện API/agent chưa lọc `access_level`; **không đổi alias sang v3 trên môi trường dùng chung trước khi
  có lọc quyền**, nếu không người hỏi bất kỳ có thể nhận nội dung mật (dữ liệu hư cấu, nhưng sai yêu cầu bài toán).
- T-0103: ~50 câu eval nội bộ + câu eval luật mới.
