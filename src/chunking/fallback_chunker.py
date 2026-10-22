"""Fallback sliding window text chunker for Vietnamese legal texts.

Used when legal articles or clauses lack structural clause markers
or exceed maximum word capacity.
"""

import re


def split_sentences(text: str) -> list[str]:
    """Tách đoạn văn bản thành các câu hoặc dòng văn bản tự nhiên."""
    # Tách trước theo ngắt dòng kép hoặc đơn
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    atomic_units: list[str] = []

    for line in lines:
        # Nếu dòng dài hơn 80 từ, thử tách theo dấu chấm kết thúc câu
        words = line.split()
        if len(words) > 80:
            sentences = re.split(r"(?<=[.!?])\s+", line)
            for s in sentences:
                s_strip = s.strip()
                if s_strip:
                    atomic_units.append(s_strip)
        else:
            atomic_units.append(line)

    return atomic_units


def split_text_sliding_window(
    text: str,
    max_words: int = 400,
    overlap_words: int = 50,
    min_words: int = 15,
) -> list[str]:
    """Cắt văn bản theo cửa sổ trượt bảo toàn ngữ cảnh câu/dòng.

    Args:
        text: Văn bản thô cần cắt.
        max_words: Giới hạn số từ tối đa trong một chunk.
        overlap_words: Số từ gối đầu giữa hai chunk liên tiếp.
        min_words: Số từ tối thiểu cho chunk độc lập.

    Returns:
        Danh sách các chuỗi văn bản sau khi chia nhỏ.
    """
    clean_text = text.strip()
    if not clean_text:
        return []

    words = clean_text.split()
    if len(words) <= max_words:
        return [clean_text]

    units = split_sentences(clean_text)
    if not units:
        return [clean_text]

    chunks: list[str] = []
    current_units: list[str] = []
    current_word_count = 0

    i = 0
    while i < len(units):
        unit = units[i]
        unit_words = unit.split()
        unit_len = len(unit_words)

        # Trường hợp một unit độc lập dài hơn max_words (hiếm gặp)
        if unit_len > max_words:
            # Nếu đang có buffer, flush trước
            if current_units:
                chunk_str = "\n".join(current_units).strip()
                if chunk_str:
                    chunks.append(chunk_str)
                current_units = []
                current_word_count = 0

            # Cắt cứng theo từ cho unit ngoại cỡ này
            for start_idx in range(0, unit_len, max_words - overlap_words):
                chunk_words = unit_words[start_idx : start_idx + max_words]
                if chunk_words:
                    chunks.append(" ".join(chunk_words))
            i += 1
            continue

        if current_word_count + unit_len <= max_words:
            current_units.append(unit)
            current_word_count += unit_len
            i += 1
        else:
            # Đã đầy chunk hiện tại
            if current_units:
                chunk_str = "\n".join(current_units).strip()
                if chunk_str:
                    chunks.append(chunk_str)

            # Tạo phần gối đầu (overlap) từ cuối chunk trước
            overlap_buffer: list[str] = []
            overlap_count = 0
            for prev_unit in reversed(current_units):
                prev_len = len(prev_unit.split())
                if overlap_count + prev_len <= overlap_words:
                    overlap_buffer.insert(0, prev_unit)
                    overlap_count += prev_len
                else:
                    break

            current_units = list(overlap_buffer)
            current_word_count = overlap_count

            # Thêm unit hiện tại vào chunk mới
            current_units.append(unit)
            current_word_count += unit_len
            i += 1

    # Flush phần còn lại
    if current_units:
        final_str = "\n".join(current_units).strip()
        if final_str:
            final_word_count = len(final_str.split())
            if final_word_count < min_words and chunks:
                # Gộp vào chunk liền trước nếu quá ngắn
                chunks[-1] = f"{chunks[-1]}\n{final_str}"
            else:
                chunks.append(final_str)

    return chunks if chunks else [clean_text]
