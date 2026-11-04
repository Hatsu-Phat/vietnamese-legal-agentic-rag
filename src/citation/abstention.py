"""Deterministic policy deciding whether to answer, retry, clarify or abstain."""

from typing import Literal

from src.core.agent import AbstainReason, CitationAudit, DraftAnswer

Route = Literal["finalize", "retry", "abstain"]

ABSTAIN_MESSAGES: dict[AbstainReason, str] = {
    "NO_EVIDENCE": "Không tìm thấy căn cứ trong kho văn bản đã nạp để trả lời câu hỏi này.",
    "ONLY_INVALID_EVIDENCE": (
        "Các căn cứ tìm được đều thuộc văn bản hết hiệu lực hoặc bị thay thế; "
        "hệ thống không trả lời để tránh dẫn sai pháp luật hiện hành."
    ),
    "CITATION_INVALID": (
        "Không tạo được câu trả lời có trích dẫn hợp lệ cho mọi nhận định; hệ thống từ chối trả lời."
    ),
    "INSUFFICIENT_EVIDENCE": "Căn cứ tìm được chưa đủ để trả lời chắc chắn câu hỏi này.",
    "UNSUPPORTED_CLAIMS": (
        "Các nhận định soạn được không được đoạn trích dẫn hỗ trợ trực tiếp; hệ thống từ chối trả lời."
    ),
    "NEEDS_CLARIFICATION": "Câu hỏi cần thêm thông tin để tra cứu chính xác.",
}


def route_after_audit(
    draft: DraftAnswer, audit: CitationAudit, rounds: int, max_rounds: int
) -> tuple[Route, AbstainReason | None]:
    """Retry only while the retrieval budget allows it; never loop unbounded."""
    can_retry = rounds < max_rounds
    if draft.decision == "clarify":
        return "finalize", None
    if draft.decision == "abstain":
        return ("retry", None) if can_retry else ("abstain", "INSUFFICIENT_EVIDENCE")
    if audit.passed:
        return "finalize", None
    return ("retry", None) if can_retry else ("abstain", "CITATION_INVALID")
