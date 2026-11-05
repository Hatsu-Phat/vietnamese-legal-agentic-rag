"""Load internal company documents (Markdown with front matter) as documents and provisions.

A file holds one or more documents. Each document starts with a front-matter block::

    ---
    document_number: 01/2026/QĐ-TNT
    title: Nội quy lao động
    document_type: Nội quy
    issuing_authority: Tổng Giám đốc
    issued_date: 2026-01-05
    effective_from: 2026-01-15
    access_level: internal
    ---
    Chương I. QUY ĐỊNH CHUNG
    Điều 1. Phạm vi điều chỉnh
    1. ...

"Điều N." lines start an article; "Phần/Chương/Mục" lines are structure and are skipped.
Documents get ``source_kind=internal`` and ids prefixed ``internal_`` so they never collide
with law ids.
"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.common.text_utils import build_provision_id, compute_sha256, normalize_legal_text
from src.core.enums import AccessLevel, LegalValidityStatus, SourceKind
from src.core.models import LegalDocument, LegalProvision

INTERNAL_SOURCE_DATASET = "internal_fictional_company"
REQUIRED_FIELDS = (
    "document_number",
    "title",
    "document_type",
    "issuing_authority",
    "issued_date",
    "effective_from",
    "access_level",
)
ARTICLE_RE = re.compile(r"^Điều\s+(\d+[a-zđ]?)\.\s*(.+)$")
STRUCTURE_RE = re.compile(r"^(Phần|Chương|Mục)\s+\S+", re.IGNORECASE)
NUMBER_RE = re.compile(r"^(\d+)/(\d{4})/([A-ZĐ]+)-([A-Z]+)$")


@dataclass(frozen=True)
class InternalArticle:
    """One article as written in the source file."""

    article_id: str
    title: str
    text: str


@dataclass(frozen=True)
class InternalSource:
    """One document: front matter plus its articles."""

    meta: dict[str, str]
    articles: list[InternalArticle]


def internal_document_id(document_number: str) -> str:
    """``12/2026/QĐ-TNT`` → ``internal_2026_qd_tnt_12``."""
    match = NUMBER_RE.match(document_number.strip().upper())
    if not match:
        raise ValueError(f"Unsupported internal document number: {document_number!r}")
    number, year, kind, issuer = match.groups()
    kind = kind.replace("Đ", "D").lower()
    return f"internal_{year}_{kind}_{issuer.lower()}_{int(number):02d}"


def _split_blocks(text: str) -> list[tuple[str, str]]:
    """Pairs of (front matter, body) in file order."""
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[tuple[list[str], list[str]]] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "---":
            end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() == "---"), None)
            if end is None:
                raise ValueError("Front matter is not closed with ---")
            blocks.append((lines[i + 1 : end], []))
            i = end + 1
            continue
        if blocks:
            blocks[-1][1].append(lines[i])
        elif lines[i].strip():
            raise ValueError("Text before the first front matter block")
        i += 1
    return [("\n".join(meta), "\n".join(body)) for meta, body in blocks]


def parse_internal_markdown(text: str) -> list[InternalSource]:
    """Parse every document in one file; raises on missing fields or malformed articles."""
    sources: list[InternalSource] = []
    for meta_text, body in _split_blocks(text):
        meta: dict[str, str] = {}
        for line in meta_text.splitlines():
            if line.strip():
                key, sep, value = line.partition(":")
                if not sep:
                    raise ValueError(f"Bad front matter line: {line!r}")
                meta[key.strip()] = value.strip()
        missing = [f for f in REQUIRED_FIELDS if not meta.get(f)]
        if missing:
            raise ValueError(f"{meta.get('document_number', '?')}: missing {missing}")
        articles: list[tuple[str, str, list[str]]] = []
        for raw in body.splitlines():
            line = raw.strip()
            if not line or STRUCTURE_RE.match(line):
                continue
            match = ARTICLE_RE.match(line)
            if match:
                articles.append((match.group(1), f"Điều {match.group(1)}. {match.group(2)}", []))
            elif articles:
                articles[-1][2].append(line)
            else:
                raise ValueError(f"{meta['document_number']}: text before the first article")
        if not articles:
            raise ValueError(f"{meta['document_number']}: no articles")
        ids = [a[0] for a in articles]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{meta['document_number']}: duplicate article numbers")
        empty = [a[0] for a in articles if not a[2]]
        if empty:
            raise ValueError(f"{meta['document_number']}: empty articles {empty}")
        sources.append(
            InternalSource(
                meta=meta,
                articles=[InternalArticle(a, t, "\n".join(body)) for a, t, body in articles],
            )
        )
    return sources


def build_internal_corpus(
    sources: list[InternalSource],
) -> tuple[list[LegalDocument], list[LegalProvision]]:
    """Documents (status valid from their own effective date) and one provision per article."""
    documents: list[LegalDocument] = []
    provisions: list[LegalProvision] = []
    seen: set[str] = set()
    for source in sources:
        meta = source.meta
        number = meta["document_number"]
        doc_id = internal_document_id(number)
        if doc_id in seen:
            raise ValueError(f"Duplicate internal document number: {number}")
        seen.add(doc_id)
        access = AccessLevel(meta["access_level"])
        issued = date.fromisoformat(meta["issued_date"])
        effective = date.fromisoformat(meta["effective_from"])
        if effective < issued:
            raise ValueError(f"{number}: effective_from before issued_date")
        doc_provisions = []
        for article in source.articles:
            text = normalize_legal_text(article.text)
            doc_provisions.append(
                LegalProvision(
                    provision_id=build_provision_id(doc_id, article.article_id),
                    document_id=doc_id,
                    document_number=number,
                    article_id=article.article_id,
                    article_number=f"Điều {article.article_id}",
                    title=article.title,
                    text=text,
                    provision_path=f"{number} > Điều {article.article_id}",
                    char_length=len(text),
                    word_count=len(text.split()),
                    content_hash=compute_sha256(text),
                    status=LegalValidityStatus.VALID,
                )
            )
        documents.append(
            LegalDocument(
                document_id=doc_id,
                document_number=number,
                title=meta["title"],
                document_type=meta["document_type"],
                issuing_authority=meta["issuing_authority"],
                issued_date=issued,
                issued_year=issued.year,
                effective_from=effective,
                status=LegalValidityStatus.VALID,
                source_dataset=INTERNAL_SOURCE_DATASET,
                content_hash=compute_sha256("\n\n".join(p.text for p in doc_provisions)),
                source_kind=SourceKind.INTERNAL,
                access_level=access,
            )
        )
        provisions.extend(doc_provisions)
    return documents, provisions


def load_internal_dir(directory: Path) -> tuple[list[LegalDocument], list[LegalProvision]]:
    """Every ``*.md`` file in name order (README.md is documentation and is skipped)."""
    sources: list[InternalSource] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.lower() != "readme.md":
            sources.extend(parse_internal_markdown(path.read_text(encoding="utf-8")))
    return build_internal_corpus(sources)
