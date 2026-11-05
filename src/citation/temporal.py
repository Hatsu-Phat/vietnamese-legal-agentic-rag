"""Deterministic validity screening of retrieved evidence before generation."""

from datetime import date

from src.core.agent import Evidence, Limitation
from src.core.enums import LegalValidityStatus

# Evidence in these states must not ground an answer about the law currently in force.
EXCLUDED_STATUSES = frozenset({LegalValidityStatus.EXPIRED, LegalValidityStatus.SUPERSEDED})


def screen_evidence(
    evidence: list[Evidence], as_of_date: date | None
) -> tuple[list[Evidence], list[Limitation]]:
    """Drop expired/superseded chunks and report what could not be verified."""
    kept = [e for e in evidence if e.status not in EXCLUDED_STATUSES]
    limitations: list[Limitation] = []
    if len(kept) < len(evidence):
        limitations.append(
            Limitation(
                code="EXCLUDED_INVALID_EVIDENCE",
                message="Đã loại các đoạn thuộc văn bản hết hiệu lực hoặc bị thay thế.",
            )
        )
    if any(e.status != LegalValidityStatus.VALID for e in kept):
        limitations.append(
            Limitation(
                code="VALIDITY_UNVERIFIED",
                message="Hiệu lực của một số văn bản trích dẫn chưa được xác minh từ nguồn chính thức.",
            )
        )
    if as_of_date is not None and kept:
        # Chunk metadata has no verified effective dates yet, so no as-of filtering is possible.
        limitations.append(
            Limitation(
                code="AS_OF_DATE_UNVERIFIED",
                message=f"Chưa xác minh được hiệu lực tại thời điểm {as_of_date.isoformat()}.",
            )
        )
    if any(e.source_url is None for e in kept):
        limitations.append(
            Limitation(
                code="SOURCE_URL_UNAVAILABLE",
                message="Một số nguồn chưa có đường dẫn gốc.",
            )
        )
    return kept, limitations
