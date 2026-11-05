"""Tests for internal-document loading and the P1 enterprise release (laws + internal)."""

import hashlib
from pathlib import Path

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from src.chunking.legal_chunker import LegalChunker
from src.core.enums import AccessLevel, LegalValidityStatus, SourceKind
from src.core.models import LegalChunk
from src.ingestion.indexing.enterprise_release import (
    activate_alias,
    assemble_release,
    check_release_chunks,
    upload_release,
    verify_release,
)
from src.ingestion.internal_docs import (
    internal_document_id,
    load_internal_dir,
    parse_internal_markdown,
)
from src.ingestion.thiemcun_adapter import ThiemCunCorpusAdapter
from src.retrieval.qdrant_store import QdrantStore

DOCS = """---
document_number: 03/2026/QĐ-TNT
title: Quy chế lương
document_type: Quy chế
issuing_authority: Tổng Giám đốc
issued_date: 2026-02-01
effective_from: 2026-02-15
access_level: confidential
---
Chương I. QUY ĐỊNH CHUNG
Điều 1. Phạm vi điều chỉnh
Quy chế này quy định thang lương của Công ty.
Điều 2. Kỳ trả lương
1. Lương được trả vào ngày 05 hằng tháng.
2. Lương thử việc bằng 100% mức lương chính thức.
---
document_number: 01/2026/QĐ-TNT
title: Bộ quy tắc ứng xử
document_type: Quy tắc
issuing_authority: Hội đồng quản trị
issued_date: 2026-01-05
effective_from: 2026-01-05
access_level: public
---
Điều 1. Nguyên tắc
Trung thực, tôn trọng, bảo mật.
"""


def test_parse_and_build_internal_documents(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(DOCS, encoding="utf-8")
    (tmp_path / "README.md").write_text("Not a document", encoding="utf-8")

    docs, provs = load_internal_dir(tmp_path)

    assert [d.document_id for d in docs] == ["internal_2026_qd_tnt_03", "internal_2026_qd_tnt_01"]
    salary = docs[0]
    assert (salary.source_kind, salary.access_level) == (
        SourceKind.INTERNAL,
        AccessLevel.CONFIDENTIAL,
    )
    assert salary.status == LegalValidityStatus.VALID and salary.title == "Quy chế lương"
    assert [p.provision_id for p in provs[:2]] == [
        "internal_2026_qd_tnt_03__art_1",
        "internal_2026_qd_tnt_03__art_2",
    ]
    assert provs[1].text.split("\n") == [
        "1. Lương được trả vào ngày 05 hằng tháng.",
        "2. Lương thử việc bằng 100% mức lương chính thức.",
    ]
    assert provs[1].title == "Điều 2. Kỳ trả lương"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (DOCS.replace("access_level: public\n", ""), "missing"),
        (DOCS.replace("Điều 2. Kỳ trả lương", "Điều 1. Kỳ trả lương"), "duplicate article"),
        (DOCS.replace("Trung thực, tôn trọng, bảo mật.\n", ""), "empty articles"),
        ("Lời mở đầu\n" + DOCS, "before the first front matter"),
    ],
)
def test_parse_rejects_malformed_documents(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_internal_markdown(text)


def test_internal_document_id_rejects_unknown_numbers() -> None:
    assert internal_document_id("12/2026/QĐ-TNT") == "internal_2026_qd_tnt_12"
    with pytest.raises(ValueError):
        internal_document_id("45/2019/QH14")


def canonical_chunks() -> list[LegalChunk]:
    result = ThiemCunCorpusAdapter().process_corpus(
        [
            {
                "law_id": "24/2018/qh14",
                "articles": [
                    {"article_id": "1", "title": "Điều 1. A", "text": "Nội dung an ninh mạng cũ."}
                ],
            },
            {
                "law_id": "59/2020/qh14",
                "articles": [
                    {"article_id": "1", "title": "Điều 1. B", "text": "Nội dung doanh nghiệp cũ."}
                ],
            },
        ]
    )
    docs = {d.document_id: d for d in result.documents}
    chunker = LegalChunker()
    return [c for p in result.provisions for c in chunker.chunk_provision(p, docs[p.document_id])]


def release() -> tuple[list[LegalChunk], set[str]]:
    docs, provs = load_internal_dir_from_text(DOCS)
    new_law = ThiemCunCorpusAdapter(source_dataset_name="official").process_corpus(
        [
            {
                "law_id": "59/2020/qh14",
                "articles": [
                    {
                        "article_id": "1",
                        "title": "Điều 1. B",
                        "text": "Nội dung doanh nghiệp hợp nhất.",
                    }
                ],
            }
        ]
    )
    law_doc = new_law.documents[0].model_copy(
        update={"source_kind": SourceKind.LAW, "access_level": AccessLevel.PUBLIC}
    )
    chunks, reused, report = assemble_release(
        canonical_chunks(),
        {"law_2018_qh14_24": LegalValidityStatus.SUPERSEDED},
        [law_doc, *docs],
        [*new_law.provisions, *provs],
        LegalChunker(),
    )
    assert report.dropped_documents == ["law_2020_qh14_59"]
    assert (report.kept_chunks, report.new_chunks, report.status_updates) == (1, 4, 1)
    return chunks, reused


def load_internal_dir_from_text(text: str):  # type: ignore[no-untyped-def]
    from src.ingestion.internal_docs import build_internal_corpus

    return build_internal_corpus(parse_internal_markdown(text))


def test_assemble_release_tags_reuses_and_replaces() -> None:
    chunks, reused = release()

    by_doc = {c.document_id: c for c in chunks}
    old = by_doc["law_2018_qh14_24"]
    assert (old.source_kind, old.access_level, old.status) == (
        SourceKind.LAW,
        AccessLevel.PUBLIC,
        LegalValidityStatus.SUPERSEDED,
    )
    assert reused == {old.point_uuid}
    assert "hợp nhất" in by_doc["law_2020_qh14_59"].text
    assert by_doc["internal_2026_qd_tnt_03"].access_level == AccessLevel.CONFIDENTIAL
    assert [c.point_uuid for c in chunks] == sorted(c.point_uuid for c in chunks)


def test_check_release_chunks_rejects_untagged_chunks() -> None:
    with pytest.raises(ValueError, match="without source_kind"):
        check_release_chunks(canonical_chunks())


def fake_encode(texts: list[str]) -> list[list[float]]:
    vectors = []
    for text in texts:
        raw = [b / 255 + 0.01 for b in hashlib.sha256(text.encode()).digest()[:8]]
        norm = sum(x * x for x in raw) ** 0.5
        vectors.append([x / norm for x in raw])
    return vectors


def test_upload_copies_reused_vectors_encodes_new_and_verifies() -> None:
    chunks, reused = release()
    client = QdrantClient(":memory:")
    source = QdrantStore(client, "v2", 8)
    source.ensure_collection()
    old = [c for c in chunks if c.point_uuid in reused]
    source_vector = [1.0] + [0.0] * 7
    source.upload([PointStruct(id=c.point_uuid, vector=source_vector, payload={}) for c in old])
    target = QdrantStore(client, "v3", 8)

    first = upload_release(
        client, "v2", target, {"source_fingerprint": "x"}, chunks, reused, fake_encode
    )
    again = upload_release(
        client, "v2", target, {"source_fingerprint": "x"}, chunks, reused, fake_encode
    )
    result = verify_release(client, "v2", target, chunks, reused, fake_encode)

    assert first == {"copied": 1, "encoded": 4, "unchanged": 0}
    assert again == {"copied": 0, "encoded": 0, "unchanged": 5}
    assert result["points"] == 5
    assert result["access_counts"] == {"public": 3, "confidential": 2}
    stored = client.retrieve("v3", [old[0].point_uuid], with_vectors=True)[0].vector
    assert stored == source_vector


def test_activate_alias_requires_expected_current() -> None:
    client = QdrantClient(":memory:")
    for name in ("v2", "v3"):
        QdrantStore(client, name, 8).ensure_collection()
    with pytest.raises(ValueError, match="expected"):
        activate_alias(client, "vn_legal_bge_m3", "v3", "v2")
