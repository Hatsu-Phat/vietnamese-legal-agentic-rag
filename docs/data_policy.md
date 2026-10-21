# Chính sách Dữ liệu và Kiểm chứng Pháp lý (Data Policy)

Tài liệu này quy định cách thức thu thập, lưu trữ, kiểm chứng và bảo vệ dữ liệu pháp luật trong dự án `vietnamese-legal-agentic-rag`.

---

## 1. Nguồn Dữ liệu Chính thức (Authorized Legal Sources)

Toàn bộ văn bản quy phạm pháp luật đưa vào hệ thống phải xuất phát từ các nguồn chính thống có khả năng kiểm chứng:
1. **Cơ sở dữ liệu quốc gia về văn bản quy phạm pháp luật:** [vbpl.vn](https://vbpl.vn/) / [vbpl.moj.gov.vn](https://vbpl.moj.gov.vn/).
2. **Cổng thông tin điện tử Chính phủ:** [vanban.chinhphu.vn](https://vanban.chinhphu.vn/).
3. **Công báo nước Cộng hòa xã hội chủ nghĩa Việt Nam**.

> **Lưu ý:** Không sử dụng các nguồn blog cá nhân, diễn đàn hỏi đáp không chính thống làm căn cứ pháp lý để nạp vào cơ sở dữ liệu tri thức của hệ thống.

---

## 2. Nguyên tắc Quản lý Dữ liệu và Truy vết (Provenance & Auditing)

Mỗi văn bản nạp vào hệ thống (`data/raw/` hoặc `data/processed/`) bắt buộc phải có thông tin kiểm soát nguồn:
- **`source_url`**: Đường dẫn trực tiếp đến văn bản gốc trên cổng thông tin chính thức.
- **`content_hash`**: Mã băm SHA-256 của tệp văn bản raw để chống sửa đổi tùy tiện.
- **`ingestion_timestamp`**: Thời điểm thu thập văn bản.
- **`status`**: Trạng thái hiệu lực được xác minh (`in_force`, `expired`, `partially_expired`, `amended`).
- **`effective_from` / `effective_to`**: Khoảng thời gian có hiệu lực pháp luật.

---

## 3. Quản lý Hiệu lực Thời gian (Temporal Validity)

- Mọi câu truy vấn pháp lý phải gắn liền với một thời điểm xem xét (`as_of_date`), mặc định là ngày hiện tại nếu người dùng không chỉ định.
- Khi một văn bản bị sửa đổi, bổ sung bởi văn bản mới hơn, hệ thống phải lưu trữ quan hệ (`amends`, `replaces`, `repeals`) và áp dụng bộ lọc xác định để không trả lời bằng điều khoản đã hết hiệu lực.
- Khi thông tin hiệu lực chưa được xác minh (`unknown`), hệ thống phải cảnh báo người dùng trong trường `limitations` thay vì khẳng định tuyệt đối.

---

## 4. Bảo mật và Tuân thủ (Security & Compliance)

- **Không commit file dữ liệu lớn vào Git:** Thư mục `data/raw/` và `data/processed/` chứa các tệp PDF/HTML thực tế được loại trừ trong `.gitignore`. Chỉ commit các tệp manifest mô tả URL và metadata, hoặc mẫu nhỏ phục vụ kiểm thử (`data/sample_laws/`).
- **Không chứa dữ liệu cá nhân (PII):** Nếu có các tình huống án lệ hoặc câu hỏi chứa thông tin cá nhân (tên, CCCD, địa chỉ), phải thực hiện ẩn danh hóa trước khi đưa vào tập benchmark hoặc log hệ thống.
