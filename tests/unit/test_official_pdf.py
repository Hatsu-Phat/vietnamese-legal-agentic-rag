"""Tests for converting official legal PDFs into legal_corpus.json records."""

from pathlib import Path

import pymupdf
import pytest

from src.ingestion.official_pdf import (
    GAZETTE_HEADER_RE,
    PdfLine,
    convert_pdfs,
    extract_lines,
    image_only_pages,
    parse_articles,
)
from src.ingestion.thiemcun_adapter import ThiemCunCorpusAdapter


def bold(text: str) -> PdfLine:
    return PdfLine(text=text, bold=True, page=0)


def plain(text: str) -> PdfLine:
    return PdfLine(text=text, bold=False, page=0)


LAW = [
    bold("LUẬT"),
    bold("BẢO VỆ DỮ LIỆU CÁ NHÂN"),
    plain("Căn cứ Hiến pháp nước Cộng hòa xã hội chủ nghĩa Việt Nam;"),
    bold("Chương I"),
    bold("NHỮNG QUY ĐỊNH CHUNG"),
    bold("Điều 1. Phạm vi điều chỉnh và đối tượng"),
    bold("áp dụng"),
    plain("1. Luật này quy định về dữ liệu cá nhân, bảo vệ dữ liệu cá nhân và quyền,"),
    plain("nghĩa vụ của cơ quan, tổ chức, cá nhân có liên quan."),
    plain("2. Luật này áp dụng đối với:"),
    plain("a) Cơ quan, tổ chức Việt Nam;"),
    plain("b) Cá nhân nước ngoài theo Điều 5. Thông tin dẫn chiếu không in đậm."),
    bold("Mục 2"),
    bold("XỬ LÝ DỮ LIỆU"),
    bold("Điều 2. Giải thích từ ngữ"),
    plain("Trong Luật này, các từ ngữ dưới đây được hiểu như sau:"),
    plain("1. Dữ liệu cá nhân là dữ liệu số."),
    bold("Điều 3. (được bãi bỏ)"),
    bold("Điều 4. Hiệu lực thi hành"),
    plain("Luật này có hiệu lực thi hành từ ngày 01 tháng 01 năm 2026."),
    plain("Luật này được Quốc hội nước Cộng hòa xã hội chủ nghĩa Việt Nam khóa XV thông qua."),
    bold("CHỦ TỊCH QUỐC HỘI"),
    plain("Trần Thanh Mẫn"),
]


def test_parse_articles_splits_titles_bodies_and_skips_structure() -> None:
    record, report = parse_articles("91/2025/qh15", LAW)

    assert record["law_id"] == "91/2025/qh15"
    assert [a["article_id"] for a in record["articles"]] == ["1", "2", "4"]
    first = record["articles"][0]
    assert first["title"] == "Điều 1. Phạm vi điều chỉnh và đối tượng áp dụng"
    assert first["text"].split("\n") == [
        "1. Luật này quy định về dữ liệu cá nhân, bảo vệ dữ liệu cá nhân và quyền, "
        "nghĩa vụ của cơ quan, tổ chức, cá nhân có liên quan.",
        "2. Luật này áp dụng đối với:",
        "a) Cơ quan, tổ chức Việt Nam;",
        "b) Cá nhân nước ngoài theo Điều 5. Thông tin dẫn chiếu không in đậm.",
    ]
    assert "XỬ LÝ DỮ LIỆU" not in record["articles"][1]["text"]
    assert (
        record["articles"][2]["text"]
        == "Luật này có hiệu lực thi hành từ ngày 01 tháng 01 năm 2026."
    )
    assert report.repealed_article_ids == ["3"]
    assert report.ended_by is not None and report.ended_by.startswith("Luật này được Quốc hội")
    assert report.article_count == 3


def test_parse_articles_reports_duplicates_and_empty_articles() -> None:
    lines = [
        bold("Điều 1. A"),
        plain("Nội dung một."),
        bold("Điều 1. A lặp"),
        plain("Nội dung lặp."),
        bold("Điều 2. Chỉ có tiêu đề"),
        bold("Điều 3. C"),
        plain("Nội dung ba."),
    ]
    record, report = parse_articles("x", lines)

    assert [a["article_id"] for a in record["articles"]] == ["1", "3"]
    assert record["articles"][0]["text"] == "Nội dung một."
    assert report.duplicate_article_ids == ["1"]
    assert report.empty_article_ids == ["2"]


@pytest.mark.parametrize(
    ("tail", "expected_text", "trimmed"),
    [
        ("2. Xác nhận đăng ký hoạt động cung cấp dịch vụ chứng thực hợp đồng điện", "1. Đủ.", "9"),
        ("2. Đoạn kết thúc đầy đủ.", "1. Đủ.\n2. Đoạn kết thúc đầy đủ.", None),
    ],
)
def test_trim_incomplete_tail_only_drops_unfinished_paragraph(
    tail: str, expected_text: str, trimmed: str | None
) -> None:
    lines = [bold("Điều 9. Chuyển tiếp"), plain("1. Đủ."), plain(tail)]
    record, report = parse_articles("x", lines, trim_incomplete_tail=True)

    assert record["articles"][0]["text"] == expected_text
    assert report.trimmed_tail_article_id == trimmed


def test_gazette_header_pattern() -> None:
    assert GAZETTE_HEADER_RE.match("CÔNG BÁO/Số 971 + 972/Ngày 24-7-2025")
    assert GAZETTE_HEADER_RE.match("6 CÔNG BÁO/Số 987 + 988/Ngày 25-8-2024")
    assert GAZETTE_HEADER_RE.match("CÔNG BÁO/Số 131/Ngày 28-02-2026 23")
    assert not GAZETTE_HEADER_RE.match("CÔNG BÁO/Số 131 là ấn phẩm của Văn phòng Chính phủ")


def make_pdf(path: Path, scan_last_page: bool = False) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 30), "12", fontsize=14)  # page number in the header band
    page.insert_text((72, 120), "Body line one with reference", fontsize=14)
    page.insert_text((300, 116), "7", fontsize=8)  # footnote reference
    page.insert_text((72, 140), "Bold heading line", fontsize=14, fontname="hebo")
    for i in range(20):
        page.insert_text((72, 160 + 18 * i), f"Body filler line {i}", fontsize=14)
    page.insert_text((72, 760), "7 Footnote text in a smaller font.", fontsize=10)
    if scan_last_page:
        scan = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
        pix.clear_with(200)
        scan.insert_image(scan.rect, pixmap=pix)
        scan.insert_text((72, 120), "Garbled OCR layer Difu 324", fontsize=14)
    doc.save(path)
    doc.close()
    return path


def test_extract_lines_keeps_body_size_and_drops_noise(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "law.pdf", scan_last_page=True)

    lines = extract_lines(pdf)
    texts = [line.text for line in lines]

    assert texts[0] == "Body line one with reference"
    assert "12" not in texts
    assert not any("Footnote" in t or "OCR" in t or t == "7" for t in texts)
    assert [line.bold for line in lines if line.text == "Bold heading line"] == [True]
    assert image_only_pages(pdf) == [1]


def test_convert_pdfs_requires_text_layer(tmp_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page()
    doc.save(tmp_path / "empty.pdf")
    doc.close()

    with pytest.raises(ValueError, match="no text layer"):
        convert_pdfs("1/2025/qh15", [tmp_path / "empty.pdf"])


def test_output_is_readable_by_existing_corpus_adapter() -> None:
    record, _ = parse_articles("91/2025/qh15", LAW)

    result = ThiemCunCorpusAdapter().process_corpus([record])

    assert [d.document_id for d in result.documents] == ["law_2025_qh15_91"]
    assert [p.provision_id for p in result.provisions] == [
        "law_2025_qh15_91__art_1",
        "law_2025_qh15_91__art_2",
        "law_2025_qh15_91__art_4",
    ]
    assert result.provisions[0].article_number == "Điều 1"
