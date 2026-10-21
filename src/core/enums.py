"""Core domain enums for Vietnamese Legal Agentic RAG.

Defines standardized taxonomies for legal validity, inter-document relations,
question classification, and answerability status.
"""

from enum import StrEnum


class LegalValidityStatus(StrEnum):
    """Trạng thái hiệu lực pháp lý của văn bản hoặc điều khoản.


    Invariants:
    - Nếu nguồn dữ liệu chưa có ngày hiệu lực hoặc trạng thái chính thức,
      bắt buộc dùng UNKNOWN. Tuyệt đối không tự suy đoán VALID.
    """

    VALID = "valid"
    EXPIRED = "expired"
    PARTIALLY_AMENDED = "partially_amended"
    SUPERSEDED = "superseded"
    UNKNOWN = "unknown"


class DocumentRelationType(StrEnum):
    """Quan hệ pháp lý có cấu trúc giữa các văn bản quy phạm pháp luật."""

    AMENDS = "amends"
    AMENDED_BY = "amended_by"
    REPLACES = "replaces"
    REPLACED_BY = "replaced_by"
    REPEALS = "repeals"
    REPEALED_BY = "repealed_by"
    CONSOLIDATES = "consolidates"
    IMPLEMENTS = "implements"


class QuestionType(StrEnum):
    """Phân loại dạng câu hỏi tra cứu pháp luật theo độ phức tạp ngữ nghĩa và thời gian."""

    DIRECT = "direct"
    PARAPHRASE = "paraphrase"
    EXACT_REFERENCE = "exact_reference"
    MULTI_DOCUMENT = "multi_document"
    TEMPORAL = "temporal"
    AMBIGUOUS = "ambiguous"
    UNANSWERABLE = "unanswerable"
    ADVERSARIAL = "adversarial"


class AnswerableStatus(StrEnum):
    """Khả năng trả lời của hệ thống đối với câu hỏi dựa trên ngữ liệu hiện có."""

    ANSWERABLE = "answerable"
    UNANSWERABLE = "unanswerable"
    NEEDS_CLARIFICATION = "needs_clarification"
