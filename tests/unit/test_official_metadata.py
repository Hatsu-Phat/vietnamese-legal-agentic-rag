"""Tests for applying verified official metadata and merging official laws into the corpus."""

from datetime import date
from typing import Any

import pytest

from src.core.enums import LegalValidityStatus
from src.ingestion.official_metadata import (
    OFFICIAL_SOURCE_DATASET,
    apply_metadata,
    load_metadata_updates,
    merge_corpora,
)
from src.ingestion.thiemcun_adapter import ThiemCunCorpusAdapter


def corpus(items: list[dict[str, Any]], dataset: str = "base"):  # type: ignore[no-untyped-def]
    result = ThiemCunCorpusAdapter(source_dataset_name=dataset).process_corpus(items)
    return result.documents, result.provisions


def law(law_id: str, *article_ids: str) -> dict[str, Any]:
    return {
        "law_id": law_id,
        "articles": [
            {"article_id": a, "title": f"Điều {a}. T", "text": f"Nội dung {law_id} {a}."}
            for a in article_ids
        ],
    }


MANIFEST: dict[str, Any] = {
    "documents": [
        {
            "document_id": "law_2020_qh14_59",
            "name": "Luật Doanh nghiệp",
            "issued_date": "2020-06-17",
            "effective_from": "2021-01-01",
            "status": "valid",
            "official_page": "https://congbao.chinhphu.vn/van-ban/x.htm",
            "download_urls": ["https://example.invalid/a.pdf"],
            "verified_at": "2026-11-05",
        },
        {
            "document_id": "law_2025_qh15_116",
            "name": "Luật An ninh mạng",
            "official_page": None,
            "download_urls": ["https://congbaocdn.chinhphu.vn/b.pdf"],
        },
    ],
    "status_overrides": [
        {
            "document_id": "law_2018_qh14_24",
            "status": "superseded",
            "effective_to": "2026-06-30",
            "verified_at": "2026-11-05",
        }
    ],
}


def test_load_metadata_updates_reads_documents_and_overrides() -> None:
    updates = {u.document_id: u for u in load_metadata_updates(MANIFEST)}

    dn = updates["law_2020_qh14_59"]
    assert dn.issued_date == date(2020, 6, 17)
    assert dn.status == LegalValidityStatus.VALID
    assert dn.source_url == "https://congbao.chinhphu.vn/van-ban/x.htm"
    assert dn.verified_at is not None and dn.verified_at.tzinfo is not None
    anm_new = updates["law_2025_qh15_116"]
    assert anm_new.status is None  # no status in manifest: never inferred
    assert anm_new.source_url == "https://congbaocdn.chinhphu.vn/b.pdf"
    assert updates["law_2018_qh14_24"].status == LegalValidityStatus.SUPERSEDED


def test_load_metadata_updates_rejects_duplicate_ids() -> None:
    manifest = {"documents": MANIFEST["documents"][:1] * 2}

    with pytest.raises(ValueError, match="more than once"):
        load_metadata_updates(manifest)


def test_merge_replaces_same_id_and_apply_sets_status_on_provisions() -> None:
    base_docs, base_provs = corpus(
        [law("59/2020/qh14", "1", "2"), law("24/2018/qh14", "1"), law("91/2015/qh13", "1")]
    )
    new_docs, new_provs = corpus(
        [law("59/2020/qh14", "1", "2", "3"), law("116/2025/qh15", "1")],
        dataset=OFFICIAL_SOURCE_DATASET,
    )

    docs, provs, report = merge_corpora(base_docs, base_provs, new_docs, new_provs)
    docs, provs, missing = apply_metadata(docs, provs, load_metadata_updates(MANIFEST))

    assert report.replaced_documents == ["law_2020_qh14_59"]
    assert report.added_documents == ["law_2025_qh15_116"]
    assert (report.total_documents, report.total_provisions) == (4, 6)
    assert missing == []
    by_id = {d.document_id: d for d in docs}
    dn = by_id["law_2020_qh14_59"]
    assert dn.source_dataset == OFFICIAL_SOURCE_DATASET
    assert (dn.title, dn.effective_from, dn.status) == (
        "Luật Doanh nghiệp",
        date(2021, 1, 1),
        LegalValidityStatus.VALID,
    )
    old_anm = by_id["law_2018_qh14_24"]
    assert old_anm.status == LegalValidityStatus.SUPERSEDED
    assert old_anm.effective_to == date(2026, 6, 30)
    assert by_id["law_2025_qh15_116"].status == LegalValidityStatus.UNKNOWN
    assert by_id["law_2015_qh13_91"].status == LegalValidityStatus.UNKNOWN
    status = {p.provision_id: p.status for p in provs}
    assert status["law_2018_qh14_24__art_1"] == LegalValidityStatus.SUPERSEDED
    assert status["law_2020_qh14_59__art_3"] == LegalValidityStatus.VALID
    assert status["law_2015_qh13_91__art_1"] == LegalValidityStatus.UNKNOWN


def test_apply_metadata_reports_documents_absent_from_corpus() -> None:
    docs, provs = corpus([law("91/2015/qh13", "1")])

    _, _, missing = apply_metadata(docs, provs, load_metadata_updates(MANIFEST))

    assert missing == ["law_2018_qh14_24", "law_2020_qh14_59", "law_2025_qh15_116"]


def test_merge_rejects_new_duplicate_provision_ids() -> None:
    base_docs, base_provs = corpus([law("91/2015/qh13", "1")])
    new_docs, new_provs = corpus([law("116/2025/qh15", "1")])

    with pytest.raises(ValueError, match="duplicate provision_id"):
        merge_corpora(base_docs, base_provs, new_docs, new_provs + new_provs[:1])
