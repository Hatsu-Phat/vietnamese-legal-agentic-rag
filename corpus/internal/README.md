# Kho tài liệu nội bộ (hư cấu) — T-0102

Toàn bộ tài liệu trong thư mục này là **hư cấu**, soạn để thử nghiệm hệ thống RAG cho một doanh nghiệp giả định:
**Công ty Cổ phần Giải pháp Công nghệ Thiên Ngân** (ký hiệu văn bản `TNT`). Không có tên người, số điện thoại,
email, số tài khoản hay dữ liệu cá nhân thật; người ký chỉ ghi chức danh. Mọi con số (lương, ngân sách, giá, chỉ tiêu)
là giả định. Các dẫn chiếu pháp luật chỉ dùng những điều đã đối chiếu toàn văn trong corpus T-0101.

## Định dạng
Mỗi văn bản bắt đầu bằng khối front matter (`document_number`, `title`, `document_type`, `issuing_authority`,
`issued_date`, `effective_from`, `access_level`), sau đó là các dòng `Chương ...` (bỏ qua khi nạp) và
`Điều N. Tiêu đề` cùng nội dung khoản/điểm. Bộ nạp: `src/ingestion/internal_docs.py`.

`access_level`: `public` (công bố ra ngoài), `internal` (mọi nhân viên), `confidential` (ban lãnh đạo và bộ phận được giao).
