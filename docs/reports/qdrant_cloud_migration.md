# Bàn giao Qdrant Cloud — bộ index Phase 2

> Báo cáo lịch sử của lần chạy ghi trong tài liệu. Số tests, trạng thái Cloud và tính năng ở thời điểm đó không thay thế hiện trạng trong [README](../../README.md).


Kiểm tra thực tế lúc 2026-10-31T13:50:30.712635+00:00 (UTC).

## Kết quả

- Collection: `vn_legal_bge_m3_v2`; alias dùng chung: `vn_legal_bge_m3`.
- Cluster báo trạng thái `yellow`; số điểm thực tế **85,115**.
- 85,745 dòng nguồn → 85,115 chunk duy nhất, gộp 630 dòng alias. Giữ UUID, nội dung và embedding input của payload đang gắn với vector nguồn.
- Tái sử dụng 85,115 vector; encode mới cho corpus: 0.
- Đã đối chiếu toàn bộ 85,115 ID/payload, 256 vector mẫu và 3 truy vấn vector có bộ lọc.
- Truy vấn qua alias bằng QdrantIndexer trả 3 kết quả, top score 1.0; không cần tải encoder khi đã truyền query_vector.
- BM25 xây lại từ bộ chuẩn: PyVi, stopwords local, k1=1.2, b=0.65.

## Model và giới hạn token

Encoder câu hỏi: BAAI/bge-m3, revision `5617a9f61b028005a4858fdac845db406aefb181`, normalize=True, max_seq_length=512. Revision này đã khóa trong configs/dev.yaml.

Tám embedding nguồn được encode lại để kiểm tra tương thích: cosine thấp nhất **0.999999999980**. Manifest gốc không lưu revision; kết quả này là kiểm tra thực nghiệm trên mẫu, không chứng minh mọi vector tái tạo giống từng bit.

**1,420/85,115 chunk (1.67%) có embedding_text dài hơn 512 token**, dài nhất 943 token. Đợt này giữ nguyên baseline của team: encoder chỉ dùng phần đầu theo giới hạn 512. Muốn khắc phục cần phiên bản chunk/index mới và đánh giá lại; không thay đổi âm thầm bộ đang dùng.

## Smoke queries thực tế

Đây là kiểm tra truy xuất, chưa phải đánh giá chất lượng câu trả lời pháp lý hoặc toàn bộ RAG.

| Câu hỏi | Kết quả đầu | Score |
|---|---|---|
| Thời hiệu khởi kiện tranh chấp hợp đồng là bao lâu? | 91/2015/qh13 > Điều 429 | 0.7965 |
| Điều kiện hưởng lương hưu của người lao động là gì? | 33/2016/nđ-cp > Điều 8 > Khoản 1 | 0.7876 |
| Quy định xử phạt vi phạm giao thông đường bộ | 100/2019/nđ-cp > Điều 30 > Khoản 14 (phần 2) | 0.7578 |

## Đối chiếu benchmark

| Tập | Câu hỏi | Gold reference không có trong bộ chuẩn |
|---|---:|---:|
| qa_dev | 256 | 0 |
| qa_test | 599 | 0 |

Kết quả này xác nhận mapping tồn tại, không thay thế Recall@k/MRR hay chứng minh hiệu lực pháp lý. Coverage provision trong chunk audit chỉ tính trên bộ team, không xác nhận toàn bộ raw corpus.

## Kiểm tra mã và môi trường

Ruff, format check, mypy strict (`mypy src`), **48 unit tests** và `git diff --check` đều đạt. Có một warning đã biết: Qdrant Local không thực thi payload indexes như server. Lockfile kiểm tra đạt; môi trường đã sync lại, giữ 206 package trong lock.

## Nơi lưu và vận hành

- Gói nguồn nguyên vẹn: `data/imports/team-phase2/`; checksum và fingerprint trong `data/artifacts/manifests/team-phase2-import.json`.
- Bộ chuẩn: `data/artifacts/processed/chunks.jsonl`; BM25: `data/artifacts/indexes/bm25/`.
- Ánh xạ từng dòng/số hiệu nguồn: `data/artifacts/manifests/team-phase2-aliases.jsonl`.
- Bằng chứng Cloud/model: `data/artifacts/manifests/team-phase2-verification.json`; checkpoint và alias smoke cùng thư mục.
- Fingerprint nguồn: `59b8a7d07fc8f17f1209d0e915c748ddbe76bebaafaf123af23043637e900610`.
- Hướng dẫn lệnh và cấu hình teammate: [data/README.md](../../data/README.md).

Mỗi teammate dùng cùng cluster URL và alias, API key riêng có quyền đọc. Key và dữ liệu lớn được bỏ qua bởi Git. Cloud chứa dense index; BM25 và encoder câu hỏi vẫn chạy trong backend. Chưa có toàn bộ LangGraph/RRF/citation/API. Chưa commit hoặc push các thay đổi trong workspace.

## Trạng thái tối ưu nền lúc bàn giao

Status `yellow`, optimizer `ok`, HNSW đã lập chỉ mục 38,080 vector tại thời điểm kiểm tra. Toàn bộ 85.115 điểm đã lưu và truy vấn được; Cloud tiếp tục tối ưu nền nếu status yellow. Số vector HNSW có thể thấp hơn tổng số điểm do các segment nhỏ chưa đạt ngưỡng indexing. Vector, payload và HNSW đều xác nhận lưu trên đĩa.
