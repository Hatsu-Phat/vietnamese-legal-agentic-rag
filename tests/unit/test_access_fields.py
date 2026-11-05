"""Tests for source_kind/access_level on documents and chunks (P1, T-0102 schema)."""

import pytest
from pydantic import ValidationError

from src.chunking.legal_chunker import LegalChunker
from src.common.fingerprints import payload_fingerprint
from src.core.enums import AccessLevel, SourceKind
from src.core.models import LegalChunk, LegalDocument
from src.ingestion.thiemcun_adapter import ThiemCunCorpusAdapter

LAW = {
    "law_id": "91/2025/qh15",
    "articles": [{"article_id": "1", "title": "Điều 1. Phạm vi", "text": "Nội dung ngắn."}],
}


def law_corpus() -> tuple[LegalDocument, list[LegalChunk]]:
    result = ThiemCunCorpusAdapter().process_corpus([LAW])
    doc = result.documents[0]
    return doc, LegalChunker().chunk_provision(result.provisions[0], doc)


def test_chunk_without_access_fields_serializes_as_before() -> None:
    _, chunks = law_corpus()
    dumped = chunks[0].model_dump(mode="json")

    assert "source_kind" not in dumped and "access_level" not in dumped
    assert "access_level" not in chunks[0].model_dump_json()
    # Old payloads (no access keys) still validate and keep their fingerprint.
    restored = LegalChunk.model_validate(dumped)
    assert payload_fingerprint(restored.model_dump(mode="json")) == payload_fingerprint(dumped)


def test_chunker_copies_access_fields_from_document() -> None:
    doc, _ = law_corpus()
    tagged = doc.model_copy(
        update={"source_kind": SourceKind.LAW, "access_level": AccessLevel.PUBLIC}
    )
    result = ThiemCunCorpusAdapter().process_corpus([LAW])

    chunk = LegalChunker().chunk_provision(result.provisions[0], tagged)[0]

    dumped = chunk.model_dump(mode="json")
    assert (dumped["source_kind"], dumped["access_level"]) == ("law", "public")
    assert LegalChunk.model_validate_json(chunk.model_dump_json()) == chunk


@pytest.mark.parametrize(
    ("source_kind", "access_level", "message"),
    [
        ("internal", None, "set together"),
        (None, "public", "set together"),
        ("law", "confidential", "must have access_level=public"),
        ("law", "internal", "must have access_level=public"),
    ],
)
def test_invalid_access_combinations_are_rejected(
    source_kind: str | None, access_level: str | None, message: str
) -> None:
    doc, chunks = law_corpus()

    with pytest.raises(ValidationError, match=message):
        LegalDocument.model_validate(
            doc.model_dump() | {"source_kind": source_kind, "access_level": access_level}
        )
    with pytest.raises(ValidationError, match=message):
        LegalChunk.model_validate(
            chunks[0].model_dump() | {"source_kind": source_kind, "access_level": access_level}
        )


@pytest.mark.parametrize("level", list(AccessLevel))
def test_internal_documents_accept_every_access_level(level: AccessLevel) -> None:
    doc, _ = law_corpus()

    internal = LegalDocument.model_validate(
        doc.model_dump() | {"source_kind": "internal", "access_level": level.value}
    )

    assert internal.access_level == level
