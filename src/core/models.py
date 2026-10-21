"""Core Pydantic v2 domain models for Vietnamese Legal Agentic RAG.

Enforces schema-first architecture, strict typing, and deterministic validity bounds.
Invariants:
- Status defaults to LegalValidityStatus.UNKNOWN when validity dates are not officially verified.
- Content hashes are computed deterministically on normalized Unicode NFC text.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums import (
    AnswerableStatus,
    DocumentRelationType,
    LegalValidityStatus,
    QuestionType,
)


class LegalDocument(BaseModel):
    """Đại diện cho một văn bản quy phạm pháp luật (Luật, Nghị định, Thông tư, Quyết định...).

    Metadata được trích xuất từ số hiệu văn bản và thông tin nguồn gốc.
    Nếu ngày hiệu lực chưa được xác minh từ nguồn chính thức, status bắt buộc là UNKNOWN.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    document_id: str = Field(
        ...,
        description="Định danh duy nhất chuẩn hóa từ law_id (ví dụ: law_2015_qh13_100)",
    )
    document_number: str = Field(
        ...,
        description="Số hiệu văn bản gốc (ví dụ: 100/2015/QH13 hoặc 28/2020/NĐ-CP)",
    )
    title: str | None = Field(
        default=None,
        description="Tiêu đề đầy đủ của văn bản nếu có",
    )
    document_type: str | None = Field(
        default=None,
        description="Loại văn bản: Luật, Nghị định, Thông tư, Quyết định...",
    )
    issuing_authority: str | None = Field(
        default=None,
        description="Cơ quan ban hành: Quốc hội, Chính phủ, Bộ...",
    )
    issued_date: date | None = Field(
        default=None,
        description="Ngày ban hành chính thức",
    )
    issued_year: int | None = Field(
        default=None,
        description="Năm ban hành trích xuất từ số hiệu",
    )
    effective_from: date | None = Field(
        default=None,
        description="Ngày bắt đầu có hiệu lực",
    )
    effective_to: date | None = Field(
        default=None,
        description="Ngày hết hiệu lực nếu có",
    )
    status: LegalValidityStatus = Field(
        default=LegalValidityStatus.UNKNOWN,
        description="Trạng thái hiệu lực (mặc định UNKNOWN nếu chưa xác minh)",
    )
    source_url: str | None = Field(
        default=None,
        description="URL nguồn gốc văn bản trên cổng chính thức (VBPL, Chinhphu)",
    )
    source_dataset: str = Field(
        default="thiemcun169_data_for_gui",
        description="Tên tập dữ liệu nguồn",
    )
    source_last_verified_at: datetime | None = Field(
        default=None,
        description="Thời điểm kiểm tra/xác minh metadata lần cuối",
    )
    content_hash: str = Field(
        ...,
        description="Mã băm SHA-256 toàn bộ nội dung văn bản chuẩn hóa NFC",
    )
    ingestion_version: str = Field(
        default="1.0.0",
        description="Phiên bản pipeline nạp dữ liệu",
    )


class LegalProvision(BaseModel):
    """Đại diện cho một đơn vị quy phạm pháp luật (Điều luật) trong văn bản.

    Bảo toàn toàn văn Điều (bao gồm các Khoản và Điểm) và breadcrumb đường dẫn pháp lý.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    provision_id: str = Field(
        ...,
        description="Định danh duy nhất kết hợp giữa document_id và article_id",
    )
    document_id: str = Field(
        ...,
        description="ID văn bản cha tham chiếu đến LegalDocument",
    )
    document_number: str = Field(
        ...,
        description="Số hiệu văn bản cha",
    )
    article_id: str = Field(
        ...,
        description="ID Điều gốc từ bộ dữ liệu thô để đối chiếu benchmark",
    )
    article_number: str | None = Field(
        default=None,
        description="Số thứ tự Điều đã chuẩn hóa (ví dụ: 'Điều 1')",
    )
    title: str = Field(
        ...,
        description="Tiêu đề của Điều luật",
    )
    text: str = Field(
        ...,
        description="Toàn văn nội dung Điều luật (Unicode NFC, giữ nguyên cấu trúc dòng)",
    )
    provision_path: str = Field(
        ...,
        description="Breadcrumb đường dẫn pháp lý (ví dụ: '100/2015/QH13 > Điều 1')",
    )
    char_length: int = Field(
        ...,
        description="Độ dài ký tự của nội dung điều luật",
    )
    word_count: int = Field(
        ...,
        description="Số lượng từ tiếng Việt trong nội dung",
    )
    content_hash: str = Field(
        ...,
        description="Mã băm SHA-256 của nội dung điều luật chuẩn hóa NFC",
    )
    status: LegalValidityStatus = Field(
        default=LegalValidityStatus.UNKNOWN,
        description="Trạng thái hiệu lực của điều khoản (mặc định UNKNOWN)",
    )


class DocumentRelation(BaseModel):
    """Quan hệ pháp lý giữa hai văn bản quy phạm pháp luật đã được xác minh."""

    model_config = ConfigDict(validate_assignment=True)

    source_document_id: str = Field(
        ...,
        description="ID văn bản tác động/sửa đổi/thay thế",
    )
    target_document_id: str = Field(
        ...,
        description="ID văn bản bị tác động/bị sửa đổi/bị thay thế",
    )
    relation_type: DocumentRelationType = Field(
        ...,
        description="Loại quan hệ pháp lý",
    )
    verified_by: str | None = Field(
        default=None,
        description="Chuyên gia hoặc cơ chế xác minh quan hệ",
    )
    verification_source: str | None = Field(
        default=None,
        description="Nguồn tài liệu hoặc URL chứng minh quan hệ",
    )


class QAEvaluationItem(BaseModel):
    """Mẫu câu hỏi kiểm thử / đánh giá chất lượng truy xuất và sinh câu trả lời RAG."""

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    question_id: str = Field(
        ...,
        description="Mã định danh duy nhất của câu hỏi",
    )
    question_text: str = Field(
        ...,
        description="Nội dung câu hỏi pháp luật bằng tiếng Việt",
    )
    question_type: QuestionType = Field(
        default=QuestionType.DIRECT,
        description="Phân loại dạng câu hỏi",
    )
    as_of_date: date | None = Field(
        default=None,
        description="Mốc thời điểm áp dụng quy định được hỏi",
    )
    expected_answer: str | None = Field(
        default=None,
        description="Câu trả lời chuẩn (gold answer) nếu có",
    )
    gold_document_ids: list[str] = Field(
        default_factory=list,
        description="Danh sách document_id chứa căn cứ pháp lý chính xác",
    )
    gold_provision_ids: list[str] = Field(
        default_factory=list,
        description="Danh sách provision_id chứa điều luật chính xác",
    )
    gold_evidence_spans: list[str] = Field(
        default_factory=list,
        description="Đoạn văn bản trích dẫn bằng chứng cụ thể",
    )
    answerable_status: AnswerableStatus = Field(
        default=AnswerableStatus.ANSWERABLE,
        description="Khả năng trả lời dựa trên ngữ liệu",
    )
    annotation_source: str = Field(
        default="thiemcun169_benchmark",
        description="Nguồn gốc nhãn đánh giá",
    )
    review_status: str = Field(
        default="unverified_imported",
        description="Trạng thái kiểm duyệt nhãn",
    )
