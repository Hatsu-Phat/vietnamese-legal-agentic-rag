"""Convert official Vietnamese legal PDFs (Công báo, datafiles.chinhphu.vn) to corpus records.

Output matches the ``legal_corpus.json`` shape read by ``ThiemCunCorpusAdapter``:
``{"law_id": "45/2019/qh14", "articles": [{"article_id", "title", "text"}]}``.

Extraction keeps only spans in the body font size, which drops gazette page headers'
signature stamps, consolidated-text (VBHN) footnotes and footnote reference numbers.
Parsing is a pure function over the extracted lines so it can be tested without PDFs.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

# Superscript flag in PyMuPDF span flags; bold is bit 4.
_SUPERSCRIPT = 1
_BOLD = 16
BODY_SIZE_TOLERANCE = 0.6
# Lone page numbers only count as header when they sit in the top band of the page.
HEADER_MAX_Y = 80.0

ARTICLE_RE = re.compile(r"^Điều\s+(\d+[a-zđ]?)\s*\.\s*(.*)$")
HEADING_RE = re.compile(
    r"^(Phần|Chương|Mục|Tiểu mục)\s+(thứ\s+\S+|[IVXLCDM]+|\d+)\b", re.IGNORECASE
)
# Gazette running header, e.g. "CÔNG BÁO/Số 971 + 972/Ngày 24-7-2025 13" (page number either side).
GAZETTE_HEADER_RE = re.compile(
    r"^(\d{1,4}\s+)?CÔNG BÁO/Số [^/]{1,40}/Ngày \d{1,2}-\d{1,2}-\d{4}(\s+\d{1,4})?$"
)
CLAUSE_START_RE = re.compile(
    r"^(\d+[a-zđ]?\.|[a-zđ]\d*\)|[-\u2013+\u2022])\s"
)  # -, en dash, bullet
# Text after the last article: signing formula, signature block, consolidation stamp, annexes.
END_MARKERS = (
    re.compile(r"^(Bộ luật|Luật|Nghị quyết) này (đã )?được Quốc hội\b"),
    re.compile(r"^(CHỦ TỊCH QUỐC HỘI|CHỦ NHIỆM|XÁC THỰC VĂN BẢN HỢP NHẤT|TM\. CHÍNH PHỦ)\b"),
    re.compile(r"^(VĂN PHÒNG QUỐC HỘI|Hà Nội, ngày)\b"),
    re.compile(r"^Nơi nhận\s*:"),
    re.compile(r"^(PHỤ LỤC|Phụ lục)\b"),
)
# Scanned pages (e.g. the signed last page of a consolidated text) are one large image,
# sometimes with an OCR text layer whose diacritics are garbled; their text is never used.
SCAN_IMAGE_MIN_AREA_RATIO = 0.5
REPEALED_RE = re.compile(r"\(?\s*(được|bị|đã được)\s+bãi bỏ\s*\)?\.?$", re.IGNORECASE)


@dataclass(frozen=True)
class PdfLine:
    """One visual line of body text."""

    text: str
    bold: bool
    page: int


@dataclass
class ConversionReport:
    """What the parser kept or dropped, for manual review."""

    law_id: str
    article_count: int = 0
    repealed_article_ids: list[str] = field(default_factory=list)
    duplicate_article_ids: list[str] = field(default_factory=list)
    empty_article_ids: list[str] = field(default_factory=list)
    ended_by: str | None = None
    image_only_pages: list[int] = field(default_factory=list)
    trimmed_tail_article_id: str | None = None


def _open_pdf(pdf_path: Path) -> pymupdf.Document:
    """Typed wrapper: PyMuPDF ships without type hints."""
    return pymupdf.open(pdf_path)  # type: ignore[no-untyped-call]


def _blocks(page: pymupdf.Page, sort: bool = False) -> list[dict[str, Any]]:
    """Text blocks with spans (size, flags, font, bbox) from PyMuPDF's dict output."""
    blocks: list[dict[str, Any]] = page.get_text("dict", sort=sort)["blocks"]  # type: ignore[no-untyped-call]
    return blocks


def _pages(doc: pymupdf.Document) -> list[pymupdf.Page]:
    return [doc[i] for i in range(doc.page_count)]


def _body_size(doc: pymupdf.Document) -> float:
    sizes: Counter[float] = Counter()
    for page in _pages(doc):
        for block in _blocks(page):
            for line in block.get("lines", []):
                for span in line["spans"]:
                    sizes[round(span["size"], 1)] += len(span["text"].strip())
    if not sizes:
        raise ValueError("PDF has no text layer")
    return sizes.most_common(1)[0][0]


def extract_lines(pdf_path: Path, page_offset: int = 0) -> list[PdfLine]:
    """Read body-size lines in reading order.

    Dropped: gazette running header, footnotes, superscript references, scanned pages.
    """
    lines: list[PdfLine] = []
    with _open_pdf(pdf_path) as doc:
        body = _body_size(doc)
        for page_no, page in enumerate(_pages(doc)):
            if _is_scan(page):
                continue
            for block in _blocks(page, sort=True):
                for line in block.get("lines", []):
                    spans = [
                        s
                        for s in line["spans"]
                        if s["text"].strip()
                        and abs(s["size"] - body) <= BODY_SIZE_TOLERANCE
                        and not s["flags"] & _SUPERSCRIPT
                    ]
                    if not spans:
                        continue
                    text = " ".join("".join(s["text"] for s in spans).split())
                    if GAZETTE_HEADER_RE.match(text) or (
                        text.isdigit() and line["bbox"][1] < HEADER_MAX_Y
                    ):
                        continue
                    bold_chars = sum(len(s["text"].strip()) for s in spans if s["flags"] & _BOLD)
                    total_chars = sum(len(s["text"].strip()) for s in spans)
                    lines.append(
                        PdfLine(
                            text=text, bold=bold_chars * 2 > total_chars, page=page_no + page_offset
                        )
                    )
    return lines


def _is_scan(page: pymupdf.Page) -> bool:
    px0, py0, px1, py1 = page.rect
    page_area = (px1 - px0) * (py1 - py0)
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        visible = max(0.0, min(x1, px1) - max(x0, px0)) * max(0.0, min(y1, py1) - max(y0, py0))
        if page_area > 0 and visible >= SCAN_IMAGE_MIN_AREA_RATIO * page_area:
            return True
    return False


def image_only_pages(pdf_path: Path) -> list[int]:
    """Pages that are scans: a single image covers most of the page."""
    with _open_pdf(pdf_path) as doc:
        return [page_no for page_no, page in enumerate(_pages(doc)) if _is_scan(page)]


def _join_paragraphs(lines: list[str]) -> str:
    """Re-flow wrapped lines; keep a line break before clauses, points and after sentence ends."""
    paragraphs: list[str] = []
    for line in lines:
        if (
            paragraphs
            and not CLAUSE_START_RE.match(line)
            and not paragraphs[-1].endswith((".", ";", ":"))
        ):
            paragraphs[-1] = f"{paragraphs[-1]} {line}"
        else:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def parse_articles(
    law_id: str, lines: list[PdfLine], trim_incomplete_tail: bool = False
) -> tuple[dict[str, Any], ConversionReport]:
    """Split body lines into articles; text before the first article (preamble) is ignored.

    ``trim_incomplete_tail`` is for sources whose last page is a scan: the final paragraph
    is dropped when it stops mid-sentence, so no truncated provision reaches the index.
    """
    report = ConversionReport(law_id=law_id)
    articles: list[dict[str, str]] = []
    seen: set[str] = set()
    current: dict[str, Any] | None = None
    in_title = False
    in_heading = False

    def close() -> None:
        if current is None:
            return
        article_id = current["article_id"]
        title = " ".join(current["title_parts"])
        text = _join_paragraphs(current["body"])
        if not text and REPEALED_RE.search(title):
            report.repealed_article_ids.append(article_id)
            return
        if not text:
            # The adapter would skip it anyway; e.g. the body sits on a scanned page.
            report.empty_article_ids.append(article_id)
            return
        if article_id in seen:
            report.duplicate_article_ids.append(article_id)
            return
        seen.add(article_id)
        articles.append({"article_id": article_id, "title": title, "text": text})

    for line in lines:
        text = line.text
        end = next((m for m in END_MARKERS if m.match(text)), None)
        if end is not None and current is not None:
            report.ended_by = text[:80]
            break
        match = ARTICLE_RE.match(text)
        if match and line.bold:
            close()
            current = {
                "article_id": match.group(1),
                "title_parts": [f"Điều {match.group(1)}. {match.group(2)}".strip()],
                "body": [],
            }
            in_title, in_heading = True, False
            continue
        if line.bold and HEADING_RE.match(text):
            in_heading, in_title = True, False
            continue
        if current is None:
            continue
        if line.bold and (in_title or in_heading):
            # Wrapped article title, or the all-caps name under a chapter/section heading.
            if in_title:
                current["title_parts"].append(text)
            continue
        in_title = in_heading = False
        current["body"].append(text)
    close()
    if trim_incomplete_tail and articles:
        last = articles[-1]
        paragraphs = last["text"].split("\n")
        if not paragraphs[-1].rstrip("”\"' ").endswith((".", ";", ":")):
            report.trimmed_tail_article_id = last["article_id"]
            last["text"] = "\n".join(paragraphs[:-1])
            if not last["text"]:
                report.empty_article_ids.append(last["article_id"])
                articles.pop()
    report.article_count = len(articles)
    return {"law_id": law_id, "articles": articles}, report


def convert_pdfs(law_id: str, pdf_paths: list[Path]) -> tuple[dict[str, Any], ConversionReport]:
    """Convert one legal document, possibly split over several gazette issues, in the given order."""
    lines: list[PdfLine] = []
    scans: list[int] = []
    offset = 0
    last_page_is_scan = False
    for path in pdf_paths:
        with _open_pdf(path) as doc:
            page_count = doc.page_count
        part_scans = image_only_pages(path)
        scans.extend(offset + p for p in part_scans)
        last_page_is_scan = bool(part_scans) and part_scans[-1] == page_count - 1
        lines.extend(extract_lines(path, page_offset=offset))
        offset += page_count
    record, report = parse_articles(
        law_id.strip().lower(), lines, trim_incomplete_tail=last_page_is_scan
    )
    report.image_only_pages = scans
    return record, report
