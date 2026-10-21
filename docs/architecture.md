# Kiến trúc Hệ thống Vietnamese Legal Agentic RAG

Tài liệu này mô tả chi tiết kiến trúc kỹ thuật của hệ sinh thái `vietnamese-legal-agentic-rag`.

---

## 1. Sơ đồ Kiến trúc Tổng thể

Quy trình xử lý được chia làm hai pha chính:
1. **Offline Ingestion & Indexing Pipeline:** Thu thập, chuẩn hóa, phân tách cấu trúc pháp lý (Điều/Khoản/Điểm) và lập chỉ mục kết hợp (BGE-M3 Dense + BM25 Sparse).
2. **Online Agentic Retrieval & Grounding Loop:** Xử lý câu hỏi, truy xuất lai, kiểm tra hiệu lực văn bản, sinh câu trả lời có trích dẫn, thẩm định độc lập và đưa ra phản hồi hoặc từ chối (abstention).

```mermaid
flowchart TD
    subgraph Offline["Offline Ingestion & Indexing"]
        RawDocs["Raw Legal Docs (PDF/HTML)"] --> Parser["Document Parser & Metadata Extractor"]
        Parser --> ProvisionDB["Legal Provisions & Temporal DB"]
        Parser --> LegalChunker["Legal-Aware Chunker (Điều/Khoản/Điểm + Breadcrumb)"]
        LegalChunker --> VectorIndex["ChromaDB (BGE-M3 Dense Index)"]
        LegalChunker --> SparseIndex["BM25 Keyword Index"]
    end

    subgraph Online["Online Agentic RAG Loop (LangGraph)"]
        UserQuery["Câu hỏi của Người dùng"] --> PlannerNode["1. Classify & Plan Node"]
        PlannerNode --> ParallelSearch["2. Hybrid Retrieval (BGE-M3 + BM25)"]
        VectorIndex -.-> ParallelSearch
        SparseIndex -.-> ParallelSearch
        ParallelSearch --> RRF["3. Reciprocal Rank Fusion (RRF)"]
        RRF --> TemporalCheck["4. Deterministic Temporal Check (Hiệu lực thời gian)"]
        ProvisionDB -.-> TemporalCheck
        TemporalCheck --> DraftNode["5. Draft Answer & Claims Generator"]
        DraftNode --> AuditNode["6. Citation & Evidence Audit Node"]
        AuditNode --> DecisionNode{"7. Quyết định (Route)"}
        DecisionNode -->|Hợp lệ & Đủ căn cứ| Output["Final Answer + Verified Citations"]
        DecisionNode -->|Thiếu căn cứ (Retry < 2)| ParallelSearch
        DecisionNode -->|Không đủ căn cứ / Mâu thuẫn| Abstain["Abstention / Clarification Response"]
    end
```

---

## 2. Trách nhiệm của các Module cốt lõi

| Tên Module (`src/`) | Trách nhiệm chính | Đầu vào | Đầu ra |
|---|---|---|---|
| `core` | Định nghĩa schema Pydantic cho entities (`Document`, `Chunk`, `Citation`, `AgentState`) | N/A | Type-safe data models |
| `common` | Logger, cấu hình hệ thống, exception classes, xử lý chuỗi/bảo mật | Configuration files, env | Cấu hình & tiện ích dùng chung |
| `ingestion` | Parse PDF/HTML văn bản quy phạm pháp luật, trích xuất metadata và quan hệ | File PDF/HTML | `LegalDocument` có cấu trúc |
| `chunking` | Cắt nhỏ theo Điều/Khoản/Điểm, bổ sung breadcrumb pháp lý (Chương > Mục > Điều) | `LegalDocument` | Danh sách `LegalChunk` có ID |
| `retrieval` | Dense encoder (BGE-M3), Sparse search (BM25), Fusion (RRF), Reranker | Query, filters, top-k | Danh sách ranked candidates |
| `agents` | LangGraph State Machine, State graph, Nodes điều phối, Bounded retry | User query, metadata filters | `AgentState` hoàn chỉnh |
| `citation` | Validator kiểm tra sự tồn tại của citation ID, URL, và chính sách Abstention | Draft answer, citations, evidence | Pass / Fail report, Abstain code |
| `evaluation` | Đo lường retrieval (Recall, MRR, nDCG) và generation (RAGAS) | Test dataset, system answers | Bảng báo cáo chỉ số |
| `api` | Cung cấp REST API qua FastAPI phục vụ Web UI hoặc tích hợp ngoài | HTTP Request | HTTP Response (JSON) |

---

## 3. Cơ chế Agentic RAG có kiểm soát (Controlled State Machine)

Hệ thống sử dụng **LangGraph** để tránh các hạn chế của linear RAG chain:
- **State tối thiểu:** `question`, `as_of_date`, `query_plan`, `candidate_chunks`, `validity_conflicts`, `draft_claims`, `citation_audit`, `retry_count`, `decision`, `final_answer`.
- **Bounded Retry:** Giới hạn lặp truy xuất tối đa `MAX_RETRIEVAL_ROUNDS = 2` để ngăn ngừa tình trạng agent lặp vô tận (infinite tool loop) và bùng nổ chi phí token.
- **Deterministic Enforcement:** Các quy tắc về ngày hiệu lực văn bản, trạng thái hiệu lực (`status`), và sự tồn tại của `citation_id` **bắt buộc phải được kiểm tra bằng code Python xác định**, không để LLM tự quyết định.
