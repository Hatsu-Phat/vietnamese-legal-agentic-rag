"""Deterministic application of the reviewer's claim-support verdicts."""

from src.core.agent import ClaimReview, DraftAnswer, Limitation


def apply_review(draft: DraftAnswer, review: ClaimReview) -> tuple[DraftAnswer, list[Limitation]]:
    """Keep only supported claims; rebuild the answer text when any claim is removed."""
    supported = {v.claim_index for v in review.verdicts if v.supported}
    kept = [c for i, c in enumerate(draft.claims) if i in supported]
    if len(kept) == len(draft.claims):
        return draft, []
    # The free-text answer may restate removed claims, so only supported claims are shown.
    reviewed = draft.model_copy(update={"claims": kept, "answer": " ".join(c.text for c in kept)})
    limitation = Limitation(
        code="UNSUPPORTED_CLAIMS_REMOVED",
        message="Đã loại các nhận định không được đoạn trích dẫn hỗ trợ trực tiếp.",
    )
    return reviewed, [limitation] if kept else []
