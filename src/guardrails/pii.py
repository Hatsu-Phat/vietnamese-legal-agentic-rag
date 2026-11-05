"""PII guardrail (P2/T-0202): find and mask Vietnamese personal identifiers in user input.

Deterministic regexes, no model. Types:
- CCCD (12 digits, starts with 0) anywhere; CMND (9 digits) only after an ID keyword.
- Mobile/landline phone numbers (0 or +84 prefix, separators allowed).
- Email addresses.
- Bank account numbers (6-19 digits) only after an account keyword.
- Payment card numbers (13-19 digits) that pass the Luhn check.
Legal references (Điều 113, 45/2019/QH14, 01/2026/QĐ-TNT) and money amounts with
thousand separators (1.000.000) are deliberately not matched.
"""

import re
from dataclasses import dataclass
from enum import StrEnum


class PiiType(StrEnum):
    """Kinds of personal identifiers the guardrail masks."""

    NATIONAL_ID = "national_id"
    PHONE = "phone"
    EMAIL = "email"
    BANK_ACCOUNT = "bank_account"
    PAYMENT_CARD = "payment_card"


MASK = {
    PiiType.NATIONAL_ID: "[SỐ_GIẤY_TỜ]",
    PiiType.PHONE: "[SỐ_ĐIỆN_THOẠI]",
    PiiType.EMAIL: "[EMAIL]",
    PiiType.BANK_ACCOUNT: "[SỐ_TÀI_KHOẢN]",
    PiiType.PAYMENT_CARD: "[SỐ_THẺ]",
}

# A digit run must not continue into more digits or a legal/number separator pattern.
_START = r"(?<![\w./-])"
_END = r"(?![\w/-]|[.,]\d)"

_ID_KEYWORD = r"(?:cccd|căn\s*cước(?:\s*công\s*dân)?|cmnd|chứng\s*minh(?:\s*nhân\s*dân|\s*thư)?|định\s*danh(?:\s*cá\s*nhân)?|hộ\s*chiếu)"
_ACCOUNT_KEYWORD = r"(?:số\s*tài\s*khoản|stk|tài\s*khoản(?:\s*ngân\s*hàng)?|tk\s*ngân\s*hàng|account(?:\s*number)?)"
# A company tax code (10 digits starting with 0) looks like a mobile number; it is not PII.
_TAX_CONTEXT = re.compile(
    r"(?:mã\s*số\s*thuế|\bmst)(?:\s+(?:của\s+)?(?:doanh\s+nghiệp|công\s+ty|là|số))*\W*$",
    re.IGNORECASE,
)
_GAP = r"(?:\s*(?:số|là|:|#|của\s*tôi|tôi\s*là)?\s*){0,3}"

PATTERNS: list[tuple[PiiType, re.Pattern[str]]] = [
    (
        PiiType.EMAIL,
        re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
    ),
    (
        PiiType.NATIONAL_ID,
        re.compile(
            rf"{_ID_KEYWORD}{_GAP}(?P<value>[A-Z]\d{{7}}|\d{{12}}|\d{{9}}){_END}", re.IGNORECASE
        ),
    ),
    (PiiType.NATIONAL_ID, re.compile(rf"{_START}(?P<value>0\d{{11}}){_END}")),
    # Account keyword first: an account number may pass the Luhn check by chance.
    (
        PiiType.BANK_ACCOUNT,
        re.compile(rf"{_ACCOUNT_KEYWORD}{_GAP}(?P<value>\d[\d ]{{4,22}}\d){_END}", re.IGNORECASE),
    ),
    (
        PiiType.PAYMENT_CARD,
        re.compile(rf"{_START}(?P<value>\d{{4}}(?:[ -]?\d{{4}}){{2}}[ -]?\d{{1,7}}){_END}"),
    ),
    (
        PiiType.PHONE,
        re.compile(
            rf"{_START}(?P<value>(?:\(?\+84\)?|84|0)[ .-]?(?:[35789]|2\d)(?:[ .-]?\d){{8}}){_END}"
        ),
    ),
]


@dataclass(frozen=True)
class PiiMatch:
    """One identifier; offsets refer to the original text."""

    type: PiiType
    start: int
    end: int
    value: str


@dataclass(frozen=True)
class MaskResult:
    """Masked text and the types found; the raw values are not kept."""

    text: str
    types: tuple[PiiType, ...]

    @property
    def contains_pii(self) -> bool:
        return bool(self.types)


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def detect_pii(text: str) -> list[PiiMatch]:
    """Non-overlapping matches; earlier patterns in PATTERNS win an overlap."""
    found: list[PiiMatch] = []
    for kind, pattern in PATTERNS:
        for m in pattern.finditer(text):
            group = "value" if "value" in pattern.groupindex else 0
            start, end = m.span(group)
            value = m.group(group)
            digits = re.sub(r"\D", "", value)
            if kind is PiiType.PAYMENT_CARD and not (13 <= len(digits) <= 19 and _luhn(digits)):
                continue
            if kind is PiiType.BANK_ACCOUNT and not 6 <= len(digits) <= 19:
                continue
            if kind is PiiType.PHONE:
                if _TAX_CONTEXT.search(text[max(0, start - 30) : start]):
                    continue
                national = "0" + digits[2:] if digits.startswith("84") else digits
                if len(national) not in (10, 11):
                    continue
            if any(start < f.end and f.start < end for f in found):
                continue
            found.append(PiiMatch(kind, start, end, value))
    return sorted(found, key=lambda f: f.start)


def mask_pii(text: str) -> MaskResult:
    """Replace every identifier by a typed placeholder."""
    matches = detect_pii(text)
    parts: list[str] = []
    cursor = 0
    for m in matches:
        parts.append(text[cursor : m.start])
        parts.append(MASK[m.type])
        cursor = m.end
    parts.append(text[cursor:])
    types = tuple(dict.fromkeys(m.type for m in matches))
    return MaskResult("".join(parts), types)
