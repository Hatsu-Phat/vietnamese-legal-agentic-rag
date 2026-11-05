# Refactor bảo trì Search v1

Đã thêm cấu hình Pydantic, bỏ khai báo model/tokenizer trùng trong YAML, tách kiểm tra release khỏi factory và chuyển xử lý lỗi SDK sang adapter. Các helper migration/model audit chuyển sang ingestion/indexing; public imports cũ được giữ. Các indexer build/query cũ vẫn là facade tương thích cho CLI.

SearchPort, schema, UUID và RRF không đổi. Tests Search được chia theo chức năng và dùng helpers chung.

Kiểm tra: 87 unit tests, Ruff/format, mypy strict và whitespace đạt. Có warning Qdrant Local đã biết. Regression Cloud: truy vấn công khai có filters document_id=law_2015_qh13_91, issued_year=2015 trả đủ 5 hit; tất cả khớp filters và top breadcrumb giống baseline smoke. Chỉ đọc Cloud, không thay đổi index hoặc encode corpus.

Không chạy lại 256 câu dev vì không đổi thuật toán. Kết quả 93,16% Recall@10 trong báo cáo search_v1.md là baseline đã đo trước đợt refactor này, không phải lần đo mới.

Startup ghi stage và error_type để vận hành chẩn đoán; lỗi client không lộ SDK details. LLM keys dùng SecretStr. get_settings được cache, biến settings/ENV cũ còn giữ để tương thích; chưa loại bỏ toàn bộ import-time settings.

Hướng dẫn tích hợp: [Search integration](../design/search-integration.md). FastAPI vẫn do teammate ghép qua SearchPort.
