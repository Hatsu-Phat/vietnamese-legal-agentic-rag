"""Read-only canonical chunk lookup and shared exact-filter semantics."""

import hashlib
from pathlib import Path
from uuid import UUID

from src.common.fingerprints import file_sha256, payload_fingerprint
from src.core.api import SourceResponse
from src.core.models import LegalChunk
from src.core.search import SearchFilters


def matches(chunk: LegalChunk, filters: SearchFilters) -> bool:
    """All active conditions are exact; absent metadata does not match a value."""
    return (
        (filters.document_id is None or chunk.document_id == filters.document_id)
        and (filters.document_type is None or chunk.document_type == filters.document_type)
        and (filters.issued_year is None or chunk.issued_year == filters.issued_year)
    )


class ChunkCatalog:
    """Hydrate all branch results from one verified canonical release."""

    def __init__(self, path: Path) -> None:
        self.chunks_sha256 = file_sha256(path)
        self.chunks: dict[str, LegalChunk] = {}
        self.payload_hashes: dict[str, str] = {}
        digest = hashlib.sha256()
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                chunk = LegalChunk.model_validate_json(line)
                uid = str(UUID(chunk.point_uuid))
                if uid != chunk.point_uuid or uid != LegalChunk.generate_point_uuid(chunk.chunk_id):
                    raise ValueError("Canonical chunk has an invalid deterministic UUID")
                if uid in self.chunks:
                    raise ValueError("Canonical release has duplicate UUIDs")
                if hashlib.sha256(chunk.text.encode()).hexdigest() != chunk.content_hash:
                    raise ValueError("Canonical text hash mismatch")
                self.chunks[uid] = chunk
                fingerprint = payload_fingerprint(chunk.model_dump(mode="json"))
                self.payload_hashes[uid] = fingerprint
                digest.update(fingerprint.encode())
                digest.update(b"\n")
        if not self.chunks:
            raise ValueError("Canonical release is empty")
        self.corpus_fingerprint = digest.hexdigest()

    def get(self, chunk_id: str) -> LegalChunk | None:
        """Look up by public chunk ID; the UUID is derived, so no second index is needed."""
        return self.chunks.get(LegalChunk.generate_point_uuid(chunk_id))


class CatalogSources:
    """SourcePort over the release loaded by SearchService; no extra memory or I/O."""

    def __init__(self, catalog: ChunkCatalog) -> None:
        self.catalog = catalog

    def get_source(self, chunk_id: str, request_id: UUID) -> SourceResponse | None:
        """Verified source URLs do not exist in this release yet, so source_url stays null."""
        chunk = self.catalog.get(chunk_id)
        if chunk is None:
            return None
        return SourceResponse(
            request_id=request_id,
            source_url=None,
            **chunk.model_dump(
                mode="json",
                include={
                    "chunk_id",
                    "document_id",
                    "provision_id",
                    "article_id",
                    "document_number",
                    "title",
                    "breadcrumb",
                    "text",
                    "document_type",
                    "issued_year",
                    "status",
                },
            ),
        )
