"""P1 enterprise release: canonical law chunks + new laws + internal documents in one collection.

Chunks of documents that did not change keep their Cloud vectors (copied from the source
collection; their ``embedding_text`` is unchanged), so only new chunks are encoded. Every
chunk in the release carries ``source_kind`` and ``access_level``. Switching the query alias
is a separate, explicit step.
"""

import hashlib
import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    CreateAlias,
    CreateAliasOperation,
    DeleteAlias,
    DeleteAliasOperation,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
)

from src.chunking.legal_chunker import LegalChunker
from src.common.fingerprints import payload_fingerprint
from src.common.logger import logger
from src.core.enums import AccessLevel, LegalValidityStatus, SourceKind
from src.core.models import LegalChunk, LegalDocument, LegalProvision
from src.retrieval.qdrant_store import QdrantStore

Encoder = Callable[[list[str]], list[list[float]]]
RETRIEVE_BATCH = 256
VECTOR_SAMPLE = 32
MIN_COSINE = 0.999


@dataclass
class ReleaseReport:
    """What went into the release, for review before upload."""

    canonical_chunks: int = 0
    kept_chunks: int = 0
    dropped_documents: list[str] = field(default_factory=list)
    new_documents: int = 0
    new_chunks: int = 0
    total_chunks: int = 0
    by_access: dict[str, int] = field(default_factory=dict)
    status_updates: int = 0


def check_release_chunks(chunks: Sequence[LegalChunk]) -> None:
    """Every chunk is tagged; laws are public; internal chunks carry a level; UUIDs unique."""
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.source_kind is None or chunk.access_level is None:
            raise ValueError(f"Chunk without source_kind/access_level: {chunk.chunk_id}")
        if chunk.source_kind == SourceKind.LAW and chunk.access_level != AccessLevel.PUBLIC:
            raise ValueError(f"Law chunk is not public: {chunk.chunk_id}")
        if chunk.point_uuid in seen:
            raise ValueError(f"Duplicate point UUID: {chunk.chunk_id}")
        seen.add(chunk.point_uuid)


def assemble_release(
    canonical: Sequence[LegalChunk],
    status_by_document: dict[str, LegalValidityStatus],
    new_documents: Sequence[LegalDocument],
    new_provisions: Sequence[LegalProvision],
    chunker: LegalChunker,
) -> tuple[list[LegalChunk], set[str], ReleaseReport]:
    """Return release chunks sorted by UUID, the UUIDs whose vectors can be reused, a report.

    Canonical chunks of documents in ``new_documents`` are dropped (they are re-chunked from
    the new text); the rest are tagged law/public and take the verified document status.
    """
    new_ids = {d.document_id for d in new_documents}
    if any(d.source_kind is None for d in new_documents):
        raise ValueError("New documents must carry source_kind/access_level")
    report = ReleaseReport(canonical_chunks=len(canonical), new_documents=len(new_documents))
    report.dropped_documents = sorted({c.document_id for c in canonical} & new_ids)
    kept: list[LegalChunk] = []
    for chunk in canonical:
        if chunk.document_id in new_ids:
            continue
        status = status_by_document.get(chunk.document_id, chunk.status)
        report.status_updates += status != chunk.status
        kept.append(
            chunk.model_copy(
                update={
                    "source_kind": SourceKind.LAW,
                    "access_level": AccessLevel.PUBLIC,
                    "status": status,
                }
            )
        )
    documents = {d.document_id: d for d in new_documents}
    missing = {p.document_id for p in new_provisions} - set(documents)
    if missing:
        raise ValueError(f"Provisions without a new document: {sorted(missing)}")
    fresh = [
        chunk
        for provision in new_provisions
        for chunk in chunker.chunk_provision(provision, documents[provision.document_id])
    ]
    chunks = sorted(kept + fresh, key=lambda c: c.point_uuid)
    check_release_chunks(chunks)
    report.kept_chunks = len(kept)
    report.new_chunks = len(fresh)
    report.total_chunks = len(chunks)
    report.by_access = dict(Counter(f"{c.source_kind}/{c.access_level}" for c in chunks))
    return chunks, {c.point_uuid for c in kept}, report


def release_fingerprint(chunks: Sequence[LegalChunk]) -> str:
    """Same construction as ``ChunkCatalog.corpus_fingerprint`` over UUID-sorted chunks."""
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(payload_fingerprint(chunk.model_dump(mode="json")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _point(chunk: LegalChunk, vector: Sequence[float]) -> PointStruct:
    return PointStruct(
        id=chunk.point_uuid,
        vector=[float(x) for x in vector],
        payload=chunk.model_dump(mode="json"),
    )


def upload_release(
    client: QdrantClient,
    source_collection: str,
    store: QdrantStore,
    metadata: dict[str, Any],
    chunks: Sequence[LegalChunk],
    reused: set[str],
    encode: Encoder,
    batch_size: int = 64,
) -> dict[str, int]:
    """Upsert missing or changed points; reused vectors come from ``source_collection``."""
    store.ensure_collection(metadata)
    existing = store.existing_fingerprints()
    expected = {c.point_uuid for c in chunks}
    unexpected = set(existing) - expected
    if unexpected:
        raise ValueError(f"Target holds {len(unexpected)} points outside this release")
    pending = [
        c
        for c in chunks
        if existing.get(c.point_uuid) != payload_fingerprint(c.model_dump(mode="json"))
    ]
    copy = [c for c in pending if c.point_uuid in reused]
    fresh = [c for c in pending if c.point_uuid not in reused]
    logger.info(f"Pending: {len(copy):,} reused vectors, {len(fresh):,} to encode")
    for start in range(0, len(copy), RETRIEVE_BATCH):
        batch = copy[start : start + RETRIEVE_BATCH]
        rows = client.retrieve(
            source_collection, ids=[c.point_uuid for c in batch], with_vectors=True
        )
        vectors = {str(r.id): r.vector for r in rows}
        if set(vectors) != {c.point_uuid for c in batch}:
            raise ValueError("Source collection is missing vectors for reused chunks")
        points = []
        for chunk in batch:
            vector = vectors[chunk.point_uuid]
            if not isinstance(vector, list):
                raise ValueError("Unexpected vector representation in source collection")
            points.append(_point(chunk, vector))  # type: ignore[arg-type]
        store.upload(points)
        logger.info(f"Copied {min(start + RETRIEVE_BATCH, len(copy)):,}/{len(copy):,}")
    for start in range(0, len(fresh), batch_size):
        batch = fresh[start : start + batch_size]
        vectors_new = encode([c.embedding_text for c in batch])
        store.upload([_point(c, v) for c, v in zip(batch, vectors_new, strict=True)])
        logger.info(f"Encoded {min(start + batch_size, len(fresh)):,}/{len(fresh):,}")
    return {"copied": len(copy), "encoded": len(fresh), "unchanged": len(chunks) - len(pending)}


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(float(x) * float(y) for x, y in zip(a, b, strict=True))
    return dot / math.sqrt(sum(float(x) ** 2 for x in a) * sum(float(y) ** 2 for y in b))


def verify_release(
    client: QdrantClient,
    source_collection: str,
    store: QdrantStore,
    chunks: Sequence[LegalChunk],
    reused: set[str],
    encode: Encoder,
) -> dict[str, Any]:
    """Count, every payload hash, sampled vectors (copied == source, encoded == re-encoded)."""
    expected = {c.point_uuid: payload_fingerprint(c.model_dump(mode="json")) for c in chunks}
    if store.count() != len(chunks) or store.existing_fingerprints() != expected:
        raise ValueError("Target IDs/payloads/count differ from the release chunks")
    ordered = sorted(chunks, key=lambda c: hashlib.sha256(c.point_uuid.encode()).digest())
    copied = [c for c in ordered if c.point_uuid in reused][:VECTOR_SAMPLE]
    fresh = [c for c in ordered if c.point_uuid not in reused][:VECTOR_SAMPLE]
    ids = [c.point_uuid for c in copied + fresh]
    target = {
        str(r.id): r.vector for r in client.retrieve(store.collection, ids, with_vectors=True)
    }
    source = {
        str(r.id): r.vector
        for r in client.retrieve(
            source_collection, [c.point_uuid for c in copied], with_vectors=True
        )
    }
    for chunk in copied:
        if _cosine(target[chunk.point_uuid], source[chunk.point_uuid]) < MIN_COSINE:  # type: ignore[arg-type]
            raise ValueError(f"Copied vector differs from source: {chunk.chunk_id}")
    for chunk, vector in zip(fresh, encode([c.embedding_text for c in fresh]), strict=True):
        if _cosine(target[chunk.point_uuid], vector) < MIN_COSINE:  # type: ignore[arg-type]
            raise ValueError(f"Encoded vector differs on re-encode: {chunk.chunk_id}")
    access_counts = Counter(str(c.access_level) for c in chunks)
    for level, count in access_counts.items():
        flt = Filter(must=[FieldCondition(key="access_level", match=MatchValue(value=level))])
        if client.count(store.collection, count_filter=flt, exact=True).count != count:
            raise ValueError(f"Filtered count for access_level={level} differs")
    return {
        "points": len(chunks),
        "payloads_compared": len(expected),
        "copied_vectors_checked": len(copied),
        "encoded_vectors_checked": len(fresh),
        "access_counts": dict(access_counts),
    }


def activate_alias(client: QdrantClient, alias: str, target: str, expected_current: str) -> None:
    """Atomically repoint ``alias`` from ``expected_current`` to ``target``."""
    current = {a.alias_name: a.collection_name for a in client.get_aliases().aliases}.get(alias)
    if current != expected_current:
        raise ValueError(f"Alias {alias} points to {current!r}, expected {expected_current!r}")
    client.update_collection_aliases(
        [
            DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=alias)),
            CreateAliasOperation(
                create_alias=CreateAlias(collection_name=target, alias_name=alias)
            ),
        ]
    )
