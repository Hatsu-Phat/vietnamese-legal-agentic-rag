# Search v1 — kết quả triển khai và kiểm tra

> Báo cáo lịch sử của lần chạy ghi trong tài liệu. Số tests, trạng thái Cloud và tính năng ở thời điểm đó không thay thế hiện trạng trong [README](../../README.md) hoặc kết quả [refactor mới hơn](search_refactor.md).


Báo cáo tạo lúc 2026-10-31T22:48:52.877707+07:00. Đây là kết quả chạy thực tế với BGE-M3 CPU và Qdrant Cloud; không phải số liệu suy từ unit tests hoặc báo cáo lịch sử.

## Phần đã triển khai

- Pydantic v2 contracts: SearchRequest, SearchFilters, SearchHit, SearchResponse, SearchErrorResponse và SearchPort.
- SearchService async: giữ một worker cho encode/retrieval, deadline 30 giây tính cả chờ; worker không nhân đôi khi request timeout/hủy.
- Dense + BM25 lọc AND trước top candidates, hydrate nội dung từ canonical corpus; fusion RRF k=60, loại trùng UUID, xếp hạng không làm tròn và tie-break cố định với cùng candidates.
- Startup xác minh artifacts BM25 trước unpickle, checksum canonical corpus, config/thư viện, model revision và fingerprint/count/vector contract của Cloud. Query dùng collection vật lý đã resolve từ alias lúc startup.
- CLI search và runner đánh giá dev, không sửa hoặc tạo collection Cloud, không gọi LLM sinh văn bản.

HTTP endpoint, middleware, readiness handler, auth và integration tests FastAPI là đầu việc tiếp theo của teammate. Không có thay đổi src/api trong đợt này. Hướng dẫn ghép: [search integration](../design/search-integration.md).

## Đánh giá dev đầy đủ

Đã xử lý **256/256 câu**, **0 lỗi**, **0 câu thiếu gold**. Không sử dụng tập test để chọn tham số. Mỗi câu dùng một lượt encode, một Dense query và một BM25 query; Hybrid dùng chính hai danh sách đó.

- API top_k=10; candidate limit=10 mỗi nhánh, RRF k=60; không rerank, rewrite hay tuning sau khi xem kết quả.
- Chấm gold provision IDs. Chỉ lấy các chunk trong top-k thực tế được trả rồi loại trùng theo provision, không lấy thêm ứng viên ngoài cutoff để nâng kết quả.
- Recall@k là trung bình theo câu hỏi, tính cả câu có nhiều gold references; MRR@10 giới hạn trong danh sách trả về đã nhóm provision.

| Phương pháp | Recall@1 | Recall@5 | Recall@10 | MRR@10 | Mean ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|
| Dense | 60.94% | 87.30% | 91.99% | 0.7194 | 296.8 | 348.6 |
| BM25 | 51.76% | 77.93% | 84.57% | 0.6327 | 104.0 | 152.0 |
| Hybrid RRF | 61.33% | 87.30% | 93.16% | 0.7263 | 401.3 | 468.5 |

Hybrid có Recall@10 cao hơn Dense khoảng 1.17 điểm phần trăm trong lần đo này. Đây là baseline trên dev, chưa phải kết luận về mọi loại câu hỏi, ý nghĩa thống kê hoặc khả năng trả lời pháp lý.

Latency Dense gồm encode + database; BM25 là tìm kiếm từ khóa; Hybrid gồm cả hai nhánh, queue, fusion/hydration. Không tính thời gian startup/tải model; lượt encode đầu tiên trong evaluation vẫn được tính. Kết quả phụ thuộc CPU, mạng và tải Cloud; không cam kết latency cho máy khác.

Bằng chứng từng question ID, gold và ranking: [search_dev.json](search_dev.json).

## Smoke trên Cloud với phiên bản service cuối

- top_k=20 trả đủ 20 kết quả.
- Bộ lọc AND document_id=law_2015_qh13_91, document_type=Luật, issued_year=2015 trả 5 kết quả và tất cả khớp.
- document_id không tồn tại trả danh sách rỗng và không có result warnings.
- Fingerprint release trùng lần đánh giá dev. Xem [search_smoke.json](search_smoke.json).

## Kiểm tra mã

**76 unit tests đạt**, Ruff/format check, mypy strict trên src và kiểm tra whitespace đạt. Tests bao phủ validation, RRF, BM25-only hydration, filters trước top-k, payload mismatch, release tamper, factory read-only, alias pinning, deadline/chờ worker, event loop và log không chứa nội dung query/key giả lập. Có một warning đã biết từ Qdrant Local: payload indexes không được thực thi như trên server.

Các HTTP integration tests thuộc bước FastAPI chưa được chạy. Không mô tả unit tests hoặc CLI này là kiểm tra API end-to-end.

## Dữ liệu và giới hạn

- Query alias: vn_legal_bge_m3; collection tại startup: vn_legal_bge_m3_v2.
- Model: BAAI/bge-m3; revision 5617a9f61b028005a4858fdac845db406aefb181; normalize=True, max_seq_length=512.
- Release fingerprint: `59b8a7d07fc8f17f1209d0e915c748ddbe76bebaafaf123af23043637e900610`.
- BM25: PyVi, stopwords đã khóa checksum, k1=1.2 và b=0.65; manifest cùng thư mục index.
- source_url hiện null và status unknown được trả cùng warnings; không suy đoán URL hoặc khẳng định hiệu lực.
- 1.420 chunk nguồn vượt 512 token giữ nguyên baseline của team theo quyết định migration; xem [báo cáo migration](qdrant_cloud_migration.md).
- Corpus cũ chưa được crawler cập nhật. Score tìm kiếm không phải xác suất đúng và không thay thế kiểm chứng citation/hiệu lực trong /ask.

Không commit/push, không sửa .env, không upload hoặc thay đổi dữ liệu Cloud trong đợt triển khai Search v1.
