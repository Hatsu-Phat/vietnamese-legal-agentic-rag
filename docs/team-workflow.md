# Phối hợp nhóm và giảm xung đột

Tài liệu này quy định cách chia việc; không bảo đảm Git sẽ không bao giờ có conflict. Không tự thay đổi branch protection hoặc CODEOWNERS vì chưa có danh sách tài khoản/reviewer chính thức.

## 1. Một nơi cho mỗi loại thông tin

| Nội dung | Nơi cập nhật chính |
|---|---|
| Bắt đầu, setup và lệnh kiểm tra | README root |
| Quy trình Git/PR/dependencies | CONTRIBUTING |
| Schema/interface và hành vi search | docs/design/search-v1.md + src/core/search.py + search_ports.py |
| Cách nối service vào API | docs/design/search-integration.md |
| Dữ liệu và artifacts cần chia sẻ | data/README.md |
| Cấu hình thực tế | configs/dev.yaml + model cấu hình; không ghi bản sao giá trị vào nhiều tài liệu |
| Kết quả đã chạy | Báo cáo có phạm vi/thời điểm trong docs/reports/ |
| Kế hoạch dài hạn | PROJECT_ROADMAP.md |

Các tài liệu khác đặt liên kết tới nơi chính. Không ghi lại JSON response ở README/frontend README; frontend dùng fixtures đã chốt. Không thay số tests/metric trong báo cáo cũ khi chỉ sửa code mới.

## 2. Phạm vi sửa file

| Vai trò | Khu vực làm việc chính | Phải trao đổi khi cần sửa |
|---|---|---|
| Search | src/retrieval/, src/evaluation/, tests search | core contracts, config, API callers |
| FastAPI | src/api/, HTTP integration tests | SearchPort/schemas, startup factory, dependency pins |
| Frontend | frontend/ và tests của UI khi triển khai | HTTP contract, field names, warnings/error semantics |
| Ingestion/crawler | src/ingestion/, src/chunking/, CLI offline | UUID/versioning, BM25/dense release, data policy |
| Người tích hợp | File dùng chung và docs navigation | Xác nhận từ các vai trò bị ảnh hưởng |

Đây là trách nhiệm review, không cấm sửa lỗi chéo module. Với file dùng chung (core, common config, YAML, pyproject/lockfiles, README, roadmap), ghi rõ dự định trên issue/PR trước khi sửa để tránh hai người tự đổi cùng hợp đồng.

## 3. Trình tự làm song song

1. Đưa baseline search/refactor đã review lên base chung; mọi người xác nhận cùng commit. Không coi thay đổi local là đã có trên remote.
2. Teammate FastAPI dùng SearchPort và fixtures để làm route; frontend dùng cùng request/response hoặc API mock.
3. Search giữ interface ổn định trong lúc các nhánh tích hợp. Không kèm đổi schema/đổi tên module vào PR tối ưu thuật toán.
4. Nếu cần đổi hợp đồng, chốt field/type/default/error trước; cập nhật schema, fixtures và thiết kế. Merge PR hợp đồng trước rồi các nhánh cập nhật theo.
5. Tích hợp các PR nhỏ, chạy checks và smoke tương ứng. Khi có conflict, người phụ trách vùng code xác nhận ý nghĩa từng thay đổi; không chọn toàn bộ ours/theirs để bỏ qua một bên.

## 4. Phát hành dữ liệu và cấu hình

- Một người phụ trách build/publish mỗi release; không để các nhánh API/frontend tự regenerate manifests hoặc đổi alias Cloud.
- Giữ corpus, BM25, tokenizer/stopwords, encoder và Cloud fingerprint đồng bộ. Alias thay đổi cần cập nhật local artifacts rồi restart service để pin release mới.
- Không đổi UUID, chunking hoặc model trong PR chỉ thêm endpoint/UI. Không encode/migrate lại để giải quyết Git conflict.
- Chỉ một PR thay dependencies ở một thời điểm. Backend và frontend giữ dependency files riêng.

## 5. Những phần đang triển khai tiếp

SearchService được triển khai trong nhánh codex/search-v1. FastAPI, frontend, LangGraph/local–API routing, PrivacyGate, citation audit và crawler là các phần tiếp theo. Không tự tạo module giả hoặc endpoint placeholder trong nhánh search để giữ chỗ cho teammate; thống nhất qua contracts và task description.
