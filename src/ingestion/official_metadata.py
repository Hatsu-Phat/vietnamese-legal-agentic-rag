"""Apply verified metadata from an official-sources manifest and merge new laws into the corpus.

The manifest (written by hand during T-0101 verification) is the only source of dates, status
and URLs here: nothing is inferred. Fields missing from the manifest stay as the adapter left them
(``status`` stays ``UNKNOWN``). Documents from the official corpus replace base documents with the
same ``document_id`` (e.g. a consolidated text replacing an outdated original).
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from src.core.enums import LegalValidityStatus
from src.core.models import LegalDocument, LegalProvision

OFFICIAL_SOURCE_DATASET = "official_vn_gazette"


@dataclass(frozen=True)
class MetadataUpdate:
    """Verified fields for one document; ``None`` means "leave unchanged"."""

    document_id: str
    title: str | None = None
    issued_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    status: LegalValidityStatus | None = None
    source_url: str | None = None
    verified_at: datetime | None = None


@dataclass
class MergeReport:
    """Counts and ids for manual review of a merge."""

    base_documents: int = 0
    added_documents: list[str] = field(default_factory=list)
    replaced_documents: list[str] = field(default_factory=list)
    updated_documents: list[str] = field(default_factory=list)
    missing_documents: list[str] = field(default_factory=list)
    total_documents: int = 0
    total_provisions: int = 0


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _verified_at(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def load_metadata_updates(manifest: dict[str, Any]) -> list[MetadataUpdate]:
    """Read ``documents[]`` and ``status_overrides[]`` from a sources manifest."""
    updates = [
        MetadataUpdate(
            document_id=doc["document_id"],
            title=doc.get("name"),
            issued_date=_date(doc.get("issued_date")),
            effective_from=_date(doc.get("effective_from")),
            effective_to=_date(doc.get("effective_to")),
            status=LegalValidityStatus(doc["status"]) if doc.get("status") else None,
            source_url=doc.get("official_page") or next(iter(doc.get("download_urls", [])), None),
            verified_at=_verified_at(doc.get("verified_at")),
        )
        for doc in manifest["documents"]
    ]
    updates += [
        MetadataUpdate(
            document_id=item["document_id"],
            effective_to=_date(item.get("effective_to")),
            status=LegalValidityStatus(item["status"]),
            verified_at=_verified_at(item.get("verified_at")),
        )
        for item in manifest.get("status_overrides", [])
    ]
    ids = [u.document_id for u in updates]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"Manifest lists a document more than once: {duplicates}")
    return updates


def apply_metadata(
    documents: list[LegalDocument],
    provisions: list[LegalProvision],
    updates: list[MetadataUpdate],
) -> tuple[list[LegalDocument], list[LegalProvision], list[str]]:
    """Return copies with verified fields set; provisions inherit the document status.

    Also returns the ids of updates whose document is not in ``documents``.
    """
    by_id = {u.document_id: u for u in updates}
    new_docs: list[LegalDocument] = []
    for doc in documents:
        update = by_id.get(doc.document_id)
        if update is None:
            new_docs.append(doc)
            continue
        changes: dict[str, Any] = {
            "title": update.title,
            "issued_date": update.issued_date,
            "effective_from": update.effective_from,
            "effective_to": update.effective_to,
            "status": update.status,
            "source_url": update.source_url,
            "source_last_verified_at": update.verified_at,
        }
        new_docs.append(doc.model_copy(update={k: v for k, v in changes.items() if v is not None}))
    status_by_doc = {d.document_id: d.status for d in new_docs if d.document_id in by_id}
    new_provs = [
        p.model_copy(update={"status": status_by_doc[p.document_id]})
        if p.document_id in status_by_doc
        else p
        for p in provisions
    ]
    known = {d.document_id for d in documents}
    missing = sorted(u.document_id for u in updates if u.document_id not in known)
    return new_docs, new_provs, missing


def merge_corpora(
    base_documents: list[LegalDocument],
    base_provisions: list[LegalProvision],
    new_documents: list[LegalDocument],
    new_provisions: list[LegalProvision],
) -> tuple[list[LegalDocument], list[LegalProvision], MergeReport]:
    """Base corpus plus new documents; a new document replaces the base one with the same id."""
    new_ids = {d.document_id for d in new_documents}
    base_ids = {d.document_id for d in base_documents}
    report = MergeReport(
        base_documents=len(base_documents),
        added_documents=sorted(new_ids - base_ids),
        replaced_documents=sorted(new_ids & base_ids),
    )
    documents = [d for d in base_documents if d.document_id not in new_ids] + new_documents
    provisions = [p for p in base_provisions if p.document_id not in new_ids] + new_provisions
    # The base corpus has pre-existing duplicates; only reject ones the new documents introduce.
    new_provision_ids = [p.provision_id for p in new_provisions]
    kept_base_ids = {p.provision_id for p in base_provisions if p.document_id not in new_ids}
    if len(new_provision_ids) != len(set(new_provision_ids)) or kept_base_ids & set(
        new_provision_ids
    ):
        raise ValueError("New documents introduce a duplicate provision_id")
    report.total_documents = len(documents)
    report.total_provisions = len(provisions)
    return documents, provisions, report
