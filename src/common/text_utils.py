"""Text processing, normalization, and hashing utilities for Vietnamese Legal RAG.

Ensures deterministic Unicode NFC normalization, invisible character removal,
line-break preservation for legal structure parsing, and metadata parsing from document numbers.
"""

import hashlib
import re
import unicodedata
from typing import Any

# Known ministerial and governmental authorities in Vietnamese legal identifiers
AUTHORITY_MAPPING: dict[str, str] = {
    "bnn": "Bộ Nông nghiệp và Phát triển nông thôn",
    "bng": "Bộ Ngoại giao",
    "byt": "Bộ Y tế",
    "bca": "Bộ Công an",
    "bnv": "Bộ Nội vụ",
    "bct": "Bộ Công Thương",
    "btc": "Bộ Tài chính",
    "bgdđt": "Bộ Giáo dục và Đào tạo",
    "bgddt": "Bộ Giáo dục và Đào tạo",
    "bxd": "Bộ Xây dựng",
    "nhnn": "Ngân hàng Nhà nước Việt Nam",
    "ttcp": "Thanh tra Chính phủ",
    "bvhttdl": "Bộ Văn hóa, Thể thao và Du lịch",
    "blđtbxh": "Bộ Lao động - Thương binh và Xã hội",
    "bldtbxh": "Bộ Lao động - Thương binh và Xã hội",
    "bqp": "Bộ Quốc phòng",
    "btp": "Bộ Tư pháp",
    "btttt": "Bộ Thông tin và Truyền thông",
    "bkhcn": "Bộ Khoa học và Công nghệ",
    "btnmt": "Bộ Tài nguyên và Môi trường",
    "bkhđt": "Bộ Kế hoạch và Đầu tư",
    "bkhdt": "Bộ Kế hoạch và Đầu tư",
    "bgtvt": "Bộ Giao thông vận tải",
    "ubdt": "Ủy ban Dân tộc",
    "vksndtc": "Viện kiểm sát nhân dân tối cao",
    "tandtc": "Tòa án nhân dân tối cao",
    "ktnn": "Kiểm toán nhà nước",
    "vpcp": "Văn phòng Chính phủ",
}


def normalize_legal_text(text: str) -> str:
    """Chuẩn hóa bảng mã và khoảng trắng văn bản pháp luật.

    Quy tắc:
    1. Chuẩn hóa bảng mã Unicode NFC.
    2. Loại bỏ các ký tự vô hình/điều khiển không mong muốn (zero-width spaces, null bytes).
    3. Chuẩn hóa ký tự xuống dòng về LF ('\\n').
    4. Chuẩn hóa khoảng trắng ngang thừa trên cùng một dòng, NHƯNG BẮT BUỘC giữ nguyên '\\n'
       để bảo tồn ranh giới Khoản (1., 2.) và Điểm (a), b)) cho Phase 2.
    5. Cắt bỏ khoảng trắng thừa ở đầu và cuối dòng, thu gọn nhiều dòng trống liên tiếp.

    Args:
        text: Chuỗi văn bản thô.

    Returns:
        Chuỗi văn bản đã chuẩn hóa NFC và cấu trúc dòng vẹn toàn.
    """
    if not text:
        return ""

    # 1. Unicode NFC
    normalized = unicodedata.normalize("NFC", text)

    # 2. Convert line endings to LF
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")

    # 3. Filter invisible characters / control chars except newline and tab
    filtered_chars: list[str] = []
    for char in normalized:
        if char in ("\n", "\t"):
            filtered_chars.append(char)
        elif char in ("\u200b", "\u200c", "\u200d", "\ufeff", "\x00") or unicodedata.category(
            char
        ) in ("Cc", "Cf"):
            continue
        else:
            filtered_chars.append(char)
    cleaned = "".join(filtered_chars)

    # 4. Clean line by line: normalize horizontal spaces, trim line edges
    lines = cleaned.split("\n")
    processed_lines: list[str] = []
    for line in lines:
        # replace multiple tabs/spaces with single space
        line_clean = re.sub(r"[ \t]+", " ", line).strip()
        processed_lines.append(line_clean)

    # 5. Join lines, collapse 3+ consecutive newlines to 2 newlines (preserve paragraphs)
    result = "\n".join(processed_lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def compute_sha256(text: str) -> str:
    """Tính mã băm SHA-256 tất định trên chuỗi UTF-8.

    Args:
        text: Chuỗi văn bản đầu vào.

    Returns:
        Chuỗi hex 64 ký tự của SHA-256.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def remove_vietnamese_diacritics(text: str) -> str:
    """Chuyển đổi ký tự tiếng Việt có dấu sang ký tự không dấu (dùng cho slug ID)."""
    text_nfkd = unicodedata.normalize("NFKD", text)
    no_diacritics = "".join(c for c in text_nfkd if not unicodedata.combining(c))
    # Replace special Vietnamese chars like đ, Đ
    no_diacritics = no_diacritics.replace("đ", "d").replace("Đ", "D")
    return no_diacritics


def normalize_document_id(law_id: str) -> str:
    """Chuẩn hóa số hiệu/law_id thành định danh tất định, hợp lệ cho ID và URL.

    Ví dụ:
    - '100/2015/QH13' -> 'law_2015_qh13_100'
    - '28/2020/NĐ-CP' -> 'law_2020_nd_cp_28'
    - '01/2009/TT-BNN' -> 'law_2009_tt_bnn_01'
    - '21-LCT/HĐNN8'  -> 'law_21_lct_hdnn8'

    Args:
        law_id: Số hiệu gốc từ dataset.

    Returns:
        Chuỗi document_id chuẩn hóa.
    """
    raw = law_id.strip().lower()
    raw_ascii = remove_vietnamese_diacritics(raw)

    # Pattern standard: {number}/{year}/{symbol}
    match = re.match(r"^(\d+)/(\d{4})/([^/]+)$", raw_ascii)
    if match:
        number, year, symbol = match.groups()
        symbol_slug = re.sub(r"[^a-z0-9]+", "_", symbol).strip("_")
        return f"law_{year}_{symbol_slug}_{number}"

    # General fallback: sanitize all separators
    clean_slug = re.sub(r"[^a-z0-9]+", "_", raw_ascii).strip("_")
    return f"law_{clean_slug}"


def build_provision_id(document_id: str, article_id: str) -> str:
    """Tạo provision_id duy nhất và tất định từ document_id và article_id.

    Args:
        document_id: Định danh văn bản cha (ví dụ: 'law_2015_qh13_100').
        article_id: Mã điều gốc (ví dụ: '1' hoặc '21a').

    Returns:
        Chuỗi provision_id (ví dụ: 'law_2015_qh13_100__art_1').
    """
    clean_art = str(article_id).strip()
    return f"{document_id}__art_{clean_art}"


def extract_article_number(title: str) -> str | None:
    """Trích xuất số thứ tự Điều từ tiêu đề điều khoản nếu có.

    Ví dụ:
    - 'Điều 1. Phạm vi áp dụng' -> 'Điều 1'
    - 'Điều 21a. Các trường hợp miễn trừ' -> 'Điều 21a'

    Args:
        title: Tiêu đề điều khoản.

    Returns:
        Chuỗi 'Điều X' hoặc None nếu không khớp.
    """
    if not title:
        return None
    match = re.match(r"^(Điều\s+\d+[a-zA-Z]?)", title.strip(), re.IGNORECASE)
    if match:
        val = match.group(1).strip()
        # Standardize capitalization: "Điều X"
        parts = val.split()
        if len(parts) == 2:
            return f"Điều {parts[1]}"
        return val
    return None


def parse_document_number_metadata(law_id: str) -> dict[str, Any]:
    """Trích xuất metadata có thể xác minh từ số hiệu văn bản (law_id).

    Tuyệt đối không suy đoán nếu không có quy tắc rõ ràng (trả về None).

    Args:
        law_id: Số hiệu văn bản quy phạm pháp luật.

    Returns:
        dict gồm:
        - 'issued_year': int hoặc None
        - 'document_type': str hoặc None
        - 'issuing_authority': str hoặc None
    """
    cleaned = law_id.strip().lower()
    cleaned_ascii = remove_vietnamese_diacritics(cleaned)

    # 1. Trích xuất năm ban hành (19xx hoặc 20xx)
    issued_year: int | None = None
    year_match = re.search(r"/(19\d{2}|20\d{2})(/|$)", cleaned_ascii)
    if year_match:
        issued_year = int(year_match.group(1))
    else:
        # Fallback search for 4 digits
        general_year = re.search(r"(19\d{2}|20\d{2})", cleaned_ascii)
        if general_year:
            issued_year = int(general_year.group(1))

    # 2. Xác định loại văn bản và cơ quan ban hành
    document_type: str | None = None
    issuing_authority: str | None = None

    if (
        "/qh" in cleaned_ascii
        or cleaned_ascii.endswith("qh")
        or re.search(r"/qh\d+", cleaned_ascii)
    ):
        document_type = "Luật"
        issuing_authority = "Quốc hội"
    elif "nd-cp" in cleaned_ascii or "nđ-cp" in cleaned:
        document_type = "Nghị định"
        issuing_authority = "Chính phủ"
    elif "qd-ttg" in cleaned_ascii or "qđ-ttg" in cleaned:
        document_type = "Quyết định"
        issuing_authority = "Thủ tướng Chính phủ"
    elif "ttlt-" in cleaned_ascii or "/ttlt" in cleaned_ascii:
        document_type = "Thông tư liên tịch"
        issuing_authority = "Liên tịch"
    elif "tt-" in cleaned_ascii or "/tt" in cleaned_ascii:
        document_type = "Thông tư"
        # Match specific ministry suffix
        suffix_match = re.search(r"tt-([a-z0-9đ]+)", cleaned)
        if suffix_match:
            code = suffix_match.group(1)
            issuing_authority = AUTHORITY_MAPPING.get(code, "Bộ/Cơ quan ngang bộ")
        else:
            issuing_authority = "Bộ/Cơ quan ngang bộ"
    elif "qd-" in cleaned_ascii or "/qd" in cleaned_ascii or "qđ-" in cleaned:
        document_type = "Quyết định"
        issuing_authority = "Cơ quan có thẩm quyền"
    elif "nq-" in cleaned_ascii or "/nq" in cleaned_ascii:
        document_type = "Nghị quyết"
        issuing_authority = "Cơ quan có thẩm quyền"
    elif "lct" in cleaned_ascii:
        document_type = "Luật"
        issuing_authority = "Chủ tịch nước / Hội đồng Nhà nước"

    return {
        "issued_year": issued_year,
        "document_type": document_type,
        "issuing_authority": issuing_authority,
    }
