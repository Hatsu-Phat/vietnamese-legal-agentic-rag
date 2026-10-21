# Tổng quan Nghiên cứu và Literature Matrix (Literature Review)

Tài liệu này tổng hợp các công trình nghiên cứu nền tảng và phương pháp luận kỹ thuật được tham chiếu trong dự án `vietnamese-legal-agentic-rag`.

---

## 1. Ma trận Nghiên cứu Khoa học (Paper Matrix)

| Mã | Bài báo / Tài liệu | Tác giả & Năm | Ý tưởng chính | Áp dụng vào Project |
|---|---|---|---|---|
| **P1** | [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401) | Lewis et al., 2020 | Kết hợp bộ nhớ tham số (LLM) và bộ nhớ phi tham số (retriever) | Nền tảng luồng truy xuất và sinh câu trả lời có bằng chứng |
| **P2** | [Sentence-BERT](https://aclanthology.org/D19-1410/) | Reimers & Gurevych, 2019 | Siamese/triplet network tạo sentence embedding cho so sánh ngữ nghĩa nhanh | Cơ sở lý thuyết cho dense vector search |
| **P3** | [Dense Passage Retrieval (DPR)](https://aclanthology.org/2020.emnlp-main.550/) | Karpukhin et al., 2020 | Dual-encoder cho open-domain QA; đánh chỉ mục độc lập trước truy vấn | Baseline dense retrieval so sánh với BM25 |
| **P4** | [BGE-M3-Embedding](https://arxiv.org/abs/2402.03216) | Chen et al., 2024 | Embedding đa ngôn ngữ, đa chức năng (dense, sparse, multi-vector), ngữ cảnh 8192 tokens | Mô hình dense embedding mặc định của hệ thống |
| **P5** | [Reciprocal Rank Fusion (RRF)](https://doi.org/10.1145/1571941.1572114) | Cormack et al., 2009 | Hợp nhất danh sách xếp hạng không cần chuẩn hóa điểm số khác thang đo | Thuật toán hợp nhất kết quả tìm kiếm BM25 và Dense vector |
| **P6** | [Passage Re-ranking with BERT](https://arxiv.org/abs/1901.04085) | Nogueira & Cho, 2019 | Cross-encoder chấm điểm cặp query–passage | Đánh giá xếp hạng lại (reranker) ở Phase 5 |
| **P7** | [Query2doc](https://www.microsoft.com/en-us/research/publication/query2doc-query-expansion-with-large-language-models/) | Wang et al., 2023 | Mở rộng truy vấn bằng pseudo-document do LLM sinh ra | Áp dụng khi câu hỏi ngắn hoặc thiếu ngữ cảnh |
| **P8** | [Vietnamese Legal IR in QA System](https://arxiv.org/abs/2409.13699) | Nguyen Ba Thiem et al., 2024 | Xử lý dữ liệu luật tiếng Việt, BM25 + dense retrieval, Active Retrieval | Tài liệu tham khảo trực tiếp cho bài toán pháp luật tiếng Việt |
| **P9** | [Enabling LLMs to Generate Text with Citations (ALCE)](https://aclanthology.org/2023.emnlp-main.398/) | Gao et al., 2023 | Benchmark đánh giá khả năng sinh văn bản kèm trích dẫn chính xác | Chuẩn hóa schema trích dẫn và tiêu chí đánh giá citation |
| **P11** | [Self-RAG](https://proceedings.iclr.cc/paper_files/paper/2024/hash/25f7be9694d7b32d5cc670927b8091e1-Abstract-Conference.html) | Asai et al., 2024 | Cơ chế tự phản ánh: đánh giá sự cần thiết của retrieval và tính trung thực của câu trả lời | Thiết kế các node tự kiểm định (audit node) trong LangGraph |
| **P13** | [RAGAS: Automated Evaluation of RAG](https://arxiv.org/abs/2309.15217) | Es et al., 2023 | Framework đánh giá tự động: Faithfulness, Answer Relevance, Context Precision, Context Recall | Đánh giá chất lượng pipeline sinh câu trả lời |
| **P15** | [Lost in the Middle](https://doi.org/10.1162/tacl_a_00638) | Liu et al., 2024 | LLM suy giảm độ chính xác khi bằng chứng nằm ở giữa prompt dài | Giới hạn số lượng chunks nạp vào prompt và sắp xếp vị trí tối ưu |
| **P16** | [LegalBench-RAG](https://arxiv.org/abs/2408.10343) | Pipitone & Alami, 2024 | Benchmark đánh giá RAG trong lĩnh vực pháp lý | Tham khảo cấu trúc thiết kế bài test và rubric đánh giá |

---

## 2. Câu hỏi Nghiên cứu (Research Questions)

Hệ thống được thiết kế để trả lời 5 câu hỏi nghiên cứu cốt lõi:
1. **RQ1:** Với tài liệu pháp luật tiếng Việt, mô hình Hybrid Search (BGE-M3 Dense + BM25 Sparse qua RRF) có cải thiện đáng kể Recall@k và MRR so với BM25 đơn lẻ và Dense đơn lẻ không?
2. **RQ2:** Chiến lược phân đoạn theo cấu trúc phân cấp pháp lý (Điều/Khoản/Điểm) có vượt trội hơn so với Fixed-size token chunking về độ chính xác trích dẫn (Citation Precision) và khả năng truy xuất không?
3. **RQ3:** Việc áp dụng Cross-Encoder Reranker (`bge-reranker-v2-m3`) có cải thiện chất lượng bằng chứng ở top-3 đủ để bù đắp cho chi phí độ trễ (latency overhead) không?
4. **RQ4:** Cơ chế lọc hiệu lực thời gian và quan hệ văn bản xác định (Deterministic Temporal Validity Filtering) có triệt tiêu được các câu trả lời dẫn chứng văn bản hết hiệu lực so với RAG thông thường không?
5. **RQ5:** Quy trình thẩm định trích dẫn độc lập (Citation Audit) và cơ chế từ chối trả lời (Abstention Policy) trong LangGraph có làm giảm tỷ lệ phát biểu không có căn cứ (Unfaithful Claims) về mức chấp nhận được không?
