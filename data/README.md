# Dữ liệu và index dùng chung

## Cấu trúc

| Thư mục | Nội dung | Git |
|---|---|---|
| `raw/` | Corpus JSON, câu hỏi CSV, gold references JSON và stopwords gốc | Bỏ qua |
| `samples/` | Corpus nhỏ để phát triển | Có |
| `benchmarks/` | `qa_dev.jsonl`, `qa_test.jsonl`; giữ nguyên ID nguồn để đánh giá | Có |
| `imports/team-phase2/` | Bản gói team nguyên vẹn: chunks, Qdrant SQLite, BM25 cũ và báo cáo lịch sử | Bỏ qua |
| `artifacts/processed/` | `chunks.jsonl` chuẩn đang dùng | Bỏ qua |
| `artifacts/indexes/bm25/` | BM25 được xây lại từ chunks chuẩn và metadata tương ứng | Bỏ qua |
| `artifacts/indexes/qdrant/` | Chỉ dùng khi tự tạo database local mới | Bỏ qua |
| `artifacts/manifests/` | Manifests nguồn; báo cáo/checkpoint `team-phase2-*` là file local | Một phần |
| `artifacts/sample/` | Đầu ra sample tách khỏi full corpus | Bỏ qua |

File trong gói import không dùng trực tiếp đường dẫn cũ trong README của team. Không giải nén đè lên `data/` hay khởi tạo Qdrant Local trên bản nguồn; SDK có thể tự chỉnh storage khi mở. Chỉ pickle của BM25 do chính lần chạy local tạo ra mới được nạp bằng `BM25Indexer.load()`.

## Bộ dữ liệu chuẩn của team

Gói có 85.745 dòng chunk nhưng chỉ 85.115 UUID duy nhất. 630 bản trùng là cách viết có/không dấu trong số hiệu của bốn văn bản; nội dung, tiêu đề và cấu trúc chunk giống nhau. Chọn đúng payload đang gắn với vector nguồn, giữ nguyên `embedding_text` và UUID; không cần encode lại chỉ để sửa ID.

- `team-phase2-import.json`: checksum toàn gói, fingerprint, hợp đồng embedding và số lượng thực tế.
- `team-phase2-aliases.jsonl`: từng dòng nguồn/số hiệu gốc → UUID, document/provision/article ID chuẩn.
- `team-phase2-checkpoint.json`: tiến độ upload, collection và fingerprint cho resume.
- `team-phase2-verification.json`: kết quả đối soát Cloud, kiểm tra vector và model/query thực tế.

Các báo cáo cũ bên trong `imports/` là bằng chứng lịch sử. Coverage của chunks team không chứng minh coverage toàn bộ raw corpus hiện có. `documents.jsonl`/`provisions.jsonl` không có sẵn trong gói team; muốn chạy chunking mới, hãy chạy ingestion trong một cấu hình/output riêng, không ghi đè index đang dùng.

## Chuẩn bị và chuyển lên Cloud

Từ repo root, dùng môi trường khóa đã cài (`uv sync --locked --extra dev`). Điền `QDRANT_MODE=server`, `QDRANT_URL`, `QDRANT_API_KEY` trong `.env`; giữ file này ngoài Git. Không truyền key qua đối số dòng lệnh.

```bash
uv run --locked --extra dev python scripts/migrate_team_index.py --step audit
uv run --locked --extra dev python scripts/migrate_team_index.py --step prepare
uv run --locked --extra dev python scripts/migrate_team_index.py --step upload
# Chỉ dùng --resume nếu upload đã bị gián đoạn:
uv run --locked --extra dev python scripts/migrate_team_index.py --step upload --resume
uv run --locked --extra dev python scripts/migrate_team_index.py --step verify --model-check
```

`prepare` kiểm tra mọi nhóm trùng, xuất chunks/bảng ánh xạ và xây lại BM25 bằng PyVi, stopwords hiện tại, k1=1.2, b=0.65. `audit` và `upload` không tải Torch hay BGE-M3. Chỉ `--model-check` tải model vào `models/cache/` (bị Git bỏ qua).

Collection đích mặc định `vn_legal_bge_m3_v2`: dense 1.024 chiều, Cosine; vector/payload/HNSW trên đĩa. Upload batch 64, timeout 60 giây, tối đa ba lần retry cho lỗi tạm thời. Checkpoint được ghi sau xác nhận; resume so sánh toàn payload và kiểm tra fingerprint local/metadata collection. Không tự xóa collection, đổi model hoặc chọn nội dung khác khi xung đột. Thiếu vector hoặc khác input sẽ dừng để kiểm tra model trước khi sửa/encode, không tự trộn vector mới.

`verify` so sánh toàn bộ UUID và payload, số điểm thực tế, 256 vector mẫu tất định và truy vấn có bộ lọc. Khi đạt sẽ tạo alias `vn_legal_bge_m3`. `--model-check` kiểm tra tám embedding nguồn (cosine ≥ 0.999), ghi revision model thực tế, thống kê input vượt 512 token và chạy ba câu hỏi tiếng Việt. Model gốc không ghi revision; kiểm tra tương thích thực nghiệm không chứng minh tái tạo bit-for-bit.

Nếu model không tương thích, dữ liệu nguồn vẫn giữ nguyên và bộ vector đã đối soát vẫn dùng được qua `query_vector`; cần encoder tương thích để dùng `query_text`. Không tự re-encode toàn bộ corpus. Có input vượt 512 token thì chỉ báo cáo: đợt migration này giữ nguyên baseline của team.

## Teammate sử dụng

Mỗi người dùng cùng cluster URL, alias `vn_legal_bge_m3` và API key riêng có quyền đọc. Người phụ trách ingestion cần key có quyền ghi/tạo collection. Không chia sẻ file `.env` hoặc commit key.

- Dense: đọc cấu hình YAML và `.env` qua `run_qdrant_step` cho indexing hoặc tạo `QdrantIndexer(mode="server", ...)` với URL/key từ `AppSettings` cho truy vấn. Constructor dùng trực tiếp không tự chuyển sang Cloud chỉ vì `.env` có mode server.
- Encoder câu hỏi: `BAAI/bge-m3`, normalize=True, max_seq_length=512; dùng revision đã kiểm tra trong báo cáo Cloud.
- BM25: dùng `data/artifacts/indexes/bm25/`, `data/raw/stopwords.txt` và môi trường đã khóa. Cloud chưa chạy BM25 pickle hoặc toàn bộ RAG thay cho backend.
- Sau lần migrate này, thay đổi nội dung/cách chunk/model phải tạo release/collection mới và đánh giá lại.

Smoke pipeline ghi vào `data/artifacts/sample/`, benchmark/report sample riêng và collection `vn_legal_bge_m3_sample`. Chạy cả hai bước để có input đúng:

```bash
uv run --locked --extra dev python scripts/run_phase1_ingestion.py --sample-only
uv run --locked --extra dev python scripts/run_phase2_indexing.py --sample-only --step chunk
```
