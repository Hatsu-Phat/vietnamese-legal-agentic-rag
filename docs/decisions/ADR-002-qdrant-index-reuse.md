# ADR-002: Qdrant và tái sử dụng index Phase 2

Trạng thái: accepted. ADR-001 và các báo cáo lịch sử được giữ nguyên.

Dense storage thực tế dùng Qdrant Local/Cloud, thay cho Chroma trong kiến trúc ban đầu. Encoder BGE-M3 được tách khỏi storage; chuyển index có sẵn không encode lại corpus.

Bộ team có các bản trùng nội dung do alias số hiệu. Migration xác minh nhóm trùng, giữ payload và embedding input đang có trong database, chuẩn hóa thành 85.115 điểm và lưu bảng ánh xạ về từng dòng nguồn. Nội dung xung đột phải dừng, không chọn theo thứ tự ghi hay phỏng đoán thời gian hiệu lực.

Cloud dùng collection versioned, xác nhận nguồn bằng metadata fingerprint, upload có checkpoint, đối soát trước khi tạo alias dùng chung. BM25 được xây lại local; migration không triển khai toàn bộ RAG hoặc native BM25 trên Cloud.
