"""Validated, streaming migration from Qdrant Local SQLite to a cloud collection.

The source bundle is immutable. Pickles are decoded with an inert allowlisted
PointStruct stand-in, never with unrestricted pickle.load or a writable local client.
"""

import hashlib
import io
import json
import math
import pickle
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from qdrant_client.local.qdrant_local import QdrantLocal
from qdrant_client.models import (
    CreateAlias,
    CreateAliasOperation,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
)

from src.common.atomic import atomic_open
from src.common.config import load_data_paths
from src.common.logger import logger
from src.core.migration import EmbeddingSpec, ImportReport, MigrationCheckpoint, VerificationReport
from src.core.models import LegalChunk
from src.core.paths import DataPaths
from src.retrieval.qdrant_store import QdrantStore, payload_fingerprint


class _InertPoint:
    """A data container with no deserialization hooks from the source package."""

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.state = state


class _DataOnlyUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        if (module, name) == ("qdrant_client.http.models.models", "PointStruct"):
            return _InertPoint
        raise ValueError(f"Unsupported serialized global: {module}.{name}")


def sha256_file(path: Path) -> str:
    """Hash large files without loading their bytes into RAM."""
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def write_model(path: Path, value: BaseModel) -> None:
    """Commit a typed report or checkpoint only after serialization succeeds."""
    with atomic_open(path) as stream:
        stream.write(value.model_dump_json(indent=2) + "\n")


def decode_point(blob: bytes, vector_size: int) -> PointStruct:
    """Decode data only; reject unknown globals, malformed payloads and vectors."""
    value = _DataOnlyUnpickler(io.BytesIO(blob)).load()
    if not isinstance(value, _InertPoint):
        raise ValueError("Expected serialized PointStruct")
    data = value.state["__dict__"]
    payload = LegalChunk.model_validate(data["payload"])
    if str(data["id"]) != payload.point_uuid:
        raise ValueError("Point ID differs from payload UUID")
    if payload.point_uuid != LegalChunk.generate_point_uuid(payload.chunk_id):
        raise ValueError("Invalid deterministic chunk UUID")
    if hashlib.sha256(payload.text.encode()).hexdigest() != payload.content_hash:
        raise ValueError("Invalid content hash")
    vector = data["vector"]
    if not isinstance(vector, list) or len(vector) != vector_size:
        raise ValueError("Invalid stored vector dimension")
    if not all(isinstance(x, (float, int)) and math.isfinite(x) for x in vector):
        raise ValueError("Invalid stored vector value")
    if not math.isclose(sum(x * x for x in vector), 1.0, abs_tol=1e-4):
        raise ValueError("Stored vector is not normalized")
    return PointStruct(
        id=payload.point_uuid, vector=vector, payload=payload.model_dump(mode="json")
    )


class TeamIndexMigration:
    """Prepare an immutable index release, upload it resumably and verify it."""

    def __init__(self, source: Path, paths: DataPaths | None = None) -> None:
        self.source = source.resolve()
        self.paths = paths or load_data_paths()
        manifest = json.loads(
            (self.source / "data/manifests/index_manifest.json").read_text(encoding="utf-8")
        )
        self.spec = EmbeddingSpec(
            model_id=manifest["embedding_model_id"],
            vector_size=manifest["vector_size"],
            distance=manifest["distance"],
            normalize=manifest["normalize_embeddings"],
            max_seq_length=manifest["max_seq_length"],
        )
        if self.spec.model_id != "BAAI/bge-m3" or not self.spec.normalize:
            raise ValueError("Unsupported source embedding contract")
        self.source_collection = str(manifest["collection_name"])
        if Path(self.source_collection).name != self.source_collection:
            raise ValueError("Invalid source collection name")
        metadata = json.loads(
            (self.source / "data/indexes/qdrant/meta.json").read_text(encoding="utf-8")
        )
        params = metadata["collections"][self.source_collection]["vectors"]
        if (params["size"], params["distance"]) != (self.spec.vector_size, self.spec.distance):
            raise ValueError("Source manifest and storage parameters differ")
        self.db = (
            self.source
            / "data/indexes/qdrant/collection"
            / self.source_collection
            / "storage.sqlite"
        )
        self.report_path = self.paths.manifests_dir / "team-phase2-import.json"
        self.alias_map = self.paths.manifests_dir / "team-phase2-aliases.jsonl"
        self.checkpoint_path = self.paths.manifests_dir / "team-phase2-checkpoint.json"
        self.verification_path = self.paths.manifests_dir / "team-phase2-verification.json"

    def iter_points(self) -> Iterator[PointStruct]:
        """Stream validated records without modifying or loading the source DB."""
        if Path(str(self.db) + "-wal").exists():
            raise ValueError("Source database must be a closed, checkpointed copy")
        connection = sqlite3.connect(self.db.as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed")
            for (blob,) in connection.execute("SELECT point FROM points ORDER BY id"):
                yield decode_point(blob, self.spec.vector_size)
        finally:
            connection.close()

    def checksums(self) -> dict[str, str]:
        """Inventory all source files except Finder metadata."""
        return {
            str(p.relative_to(self.source)): sha256_file(p)
            for p in sorted(self.source.rglob("*"))
            if p.is_file() and p.name != ".DS_Store"
        }

    def prepare(self, audit_only: bool = False) -> ImportReport:
        """Compare all chunk variants with stored payloads and retain exact vector inputs."""
        sums = self.checksums()
        fingerprint = payload_fingerprint({"checksums": sums, "embedding": self.spec.model_dump()})
        groups: dict[str, dict[str, Any]] = {}
        total = 0
        source_chunks = self.source / "data/processed/chunks.jsonl"
        with source_chunks.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                chunk = LegalChunk.model_validate_json(line)
                payload = chunk.model_dump(mode="json")
                if chunk.point_uuid != LegalChunk.generate_point_uuid(chunk.chunk_id):
                    raise ValueError("Invalid source chunk UUID")
                stable = {
                    k: v
                    for k, v in payload.items()
                    if k
                    not in {
                        "document_number",
                        "breadcrumb",
                        "embedding_text",
                        "is_duplicate_content",
                    }
                }
                key = payload_fingerprint(stable)
                entry = groups.setdefault(
                    chunk.point_uuid, {"stable": key, "variants": set(), "aliases": []}
                )
                if entry["stable"] != key:
                    raise ValueError(f"Conflicting chunk content/structure: {chunk.chunk_id}")
                entry["variants"].add(payload_fingerprint(payload))
                entry["aliases"].append(
                    {
                        "source_line": line_number,
                        "document_number": chunk.document_number,
                        "chunk_id": chunk.chunk_id,
                        "point_uuid": chunk.point_uuid,
                        "document_id": chunk.document_id,
                        "provision_id": chunk.provision_id,
                        "article_id": chunk.article_id,
                    }
                )
                total += 1
        seen: set[str] = set()

        # Audit uses a sink in memory per record, never persists a canonical corpus.
        def emit(canonical: Any, aliases: Any) -> None:
            for point in self.iter_points():
                uid = str(point.id)
                entry = groups.get(uid)
                if entry is None or uid in seen:
                    raise ValueError("Unknown or repeated database point ID")
                if payload_fingerprint(point.payload or {}) not in entry["variants"]:
                    raise ValueError("Stored payload does not match any source chunk variant")
                seen.add(uid)
                if canonical is not None:
                    canonical.write(json.dumps(point.payload, ensure_ascii=False) + "\n")
                    for alias in entry["aliases"]:
                        aliases.write(json.dumps(alias, ensure_ascii=False) + "\n")
            missing = set(groups) - seen
            if missing:
                raise ValueError(
                    f"Missing source vectors for {len(missing)} chunks; encode repair required"
                )

        if audit_only:
            emit(None, None)
        else:
            with (
                atomic_open(self.paths.chunks) as canonical,
                atomic_open(self.alias_map) as aliases,
            ):
                emit(canonical, aliases)
        report = ImportReport(
            source_fingerprint=fingerprint,
            checksums=sums,
            embedding=self.spec,
            input_chunks=total,
            unique_chunks=len(groups),
            merged_rows=total - len(groups),
            source_vectors=len(seen),
            reused_vectors=len(seen),
            canonical_chunks_sha256="" if audit_only else sha256_file(self.paths.chunks),
            alias_map_sha256="" if audit_only else sha256_file(self.alias_map),
        )
        write_model(
            self.report_path.with_name("team-phase2-audit.json")
            if audit_only
            else self.report_path,
            report,
        )
        logger.info(
            f"Validated {total:,} source rows -> {len(seen):,} unique chunks; reused all stored vectors"
        )
        return report

    def load_report(self) -> ImportReport:
        """Reject changes in source files or prepared outputs since preparation."""
        report = ImportReport.model_validate_json(self.report_path.read_text(encoding="utf-8"))
        sums = self.checksums()
        actual = payload_fingerprint({"checksums": sums, "embedding": self.spec.model_dump()})
        if actual != report.source_fingerprint:
            raise ValueError("Source bundle changed since preparation")
        if sha256_file(self.paths.chunks) != report.canonical_chunks_sha256:
            raise ValueError("Canonical chunks changed since preparation")
        if sha256_file(self.alias_map) != report.alias_map_sha256:
            raise ValueError("Source alias map changed since preparation")
        return report

    def checkpoint(self, report: ImportReport, collection: str) -> MigrationCheckpoint:
        """Bind resume to the exact source, collection and embedding configuration."""
        if self.checkpoint_path.exists():
            checkpoint = MigrationCheckpoint.model_validate_json(
                self.checkpoint_path.read_text(encoding="utf-8")
            )
            if (checkpoint.source_fingerprint, checkpoint.collection, checkpoint.embedding) != (
                report.source_fingerprint,
                collection,
                report.embedding,
            ):
                raise ValueError("Checkpoint belongs to another migration")
            return checkpoint
        return MigrationCheckpoint(
            source_fingerprint=report.source_fingerprint,
            collection=collection,
            embedding=report.embedding,
        )

    def upload(
        self, client: QdrantClient, collection: str, resume: bool = False, batch_size: int = 64
    ) -> None:
        """Send only validated points; retry transient failures with bounded backoff."""
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        report = self.load_report()
        checkpoint = self.checkpoint(report, collection)
        exists = client.collection_exists(collection)
        if exists and not self.checkpoint_path.exists():
            raise ValueError(
                "Refusing to adopt an existing collection without its source checkpoint"
            )
        if exists and not resume:
            raise ValueError("Use --resume to continue an existing migration collection")
        write_model(self.checkpoint_path, checkpoint)
        store = QdrantStore(client, collection, self.spec.vector_size)
        store.ensure_collection(
            {
                "source_fingerprint": report.source_fingerprint,
                "embedding": report.embedding.model_dump(),
            }
        )
        info = client.get_collection(collection).config
        if not isinstance(client._client, QdrantLocal) and (
            not info.params.on_disk_payload or not info.hnsw_config.on_disk
        ):
            raise ValueError("Target must store payload and HNSW on disk")
        hashes = store.existing_fingerprints() if resume else {}
        if len(hashes) > report.unique_chunks:
            raise ValueError("Target contains unexpected points")
        if hashes:
            with self.paths.chunks.open(encoding="utf-8") as stream:
                expected_ids = {str(json.loads(line)["point_uuid"]) for line in stream}
            if set(hashes) - expected_ids:
                raise ValueError("Unexpected IDs already exist in target")
        batch: list[PointStruct] = []
        processed = 0

        def flush() -> None:
            if not batch:
                return
            for attempt in range(4):
                try:
                    store.upload(batch)
                    break
                except (UnexpectedResponse, ResponseHandlingException, httpx.TransportError) as exc:
                    status = getattr(exc, "status_code", None)
                    if isinstance(exc, UnexpectedResponse) and status not in {
                        429,
                        500,
                        502,
                        503,
                        504,
                    }:
                        raise RuntimeError(
                            f"Cloud upload rejected (HTTP {status}); batch not confirmed"
                        ) from None
                    # Capacity errors are not made safe by retrying.
                    if isinstance(exc, UnexpectedResponse) and any(
                        term in str(exc.content).lower()
                        for term in ("disk", "out of memory", "quota", "resource exhausted")
                    ):
                        raise RuntimeError("Cloud capacity limit; upload stopped") from None
                    if attempt == 3:
                        raise RuntimeError("Cloud upload failed after three retries") from None
                    time.sleep(2**attempt)
            checkpoint.confirmed_points = processed
            checkpoint.verified = False
            checkpoint.alias_activated = False
            write_model(self.checkpoint_path, checkpoint)
            logger.info(f"Cloud confirmed progress: {processed:,}/{report.unique_chunks:,}")
            batch.clear()

        source_ids: set[str] = set()
        for point in self.iter_points():
            uid = str(point.id)
            source_ids.add(uid)
            processed += 1
            if hashes.get(uid) == payload_fingerprint(point.payload or {}):
                continue
            batch.append(point)
            if len(batch) >= batch_size:
                flush()
        flush()
        if set(hashes) - source_ids:
            raise ValueError("Unexpected IDs already exist in target")
        if store.count() != report.unique_chunks:
            raise ValueError("Cloud count differs from source")
        checkpoint.confirmed_points = store.count()
        write_model(self.checkpoint_path, checkpoint)

    def verify(
        self, client: QdrantClient, collection: str, activate_alias: bool = True
    ) -> VerificationReport:
        """Compare every payload/ID, sample 256 vectors, then activate the alias."""
        report = self.load_report()
        checkpoint = self.checkpoint(report, collection)
        store = QdrantStore(client, collection, self.spec.vector_size)
        store.ensure_collection(
            {
                "source_fingerprint": report.source_fingerprint,
                "embedding": report.embedding.model_dump(),
            }
        )
        remote_hashes = store.existing_fingerprints()
        expected: dict[str, str] = {}
        selected: dict[str, PointStruct] = {}
        # Hash-order sampling is deterministic, independent of source row ordering.
        sample_ids = set(
            sorted(remote_hashes, key=lambda x: hashlib.sha256(x.encode()).digest())[:256]
        )
        for point in self.iter_points():
            uid = str(point.id)
            expected[uid] = payload_fingerprint(point.payload or {})
            if uid in sample_ids:
                selected[uid] = point
        if expected != remote_hashes or store.count() != report.unique_chunks:
            raise ValueError("Remote IDs/payloads/count do not match prepared source")
        compared = 0
        ids = sorted(selected)
        for start in range(0, len(ids), 64):
            rows = client.retrieve(
                collection, ids=ids[start : start + 64], with_vectors=True, with_payload=False
            )
            for row in rows:
                vector = row.vector
                source_vector = selected[str(row.id)].vector
                if not isinstance(vector, list) or not isinstance(source_vector, list):
                    raise ValueError("Unexpected vector representation")
                if len(vector) != self.spec.vector_size or not all(
                    math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-6)
                    for a, b in zip(
                        cast(list[float], vector), cast(list[float], source_vector), strict=True
                    )
                ):
                    raise ValueError("Remote vector differs from source")
                compared += 1
        if compared != len(selected):
            raise ValueError("Incomplete vector retrieval")
        query_checks = 0
        for point in list(selected.values())[:3]:
            payload = point.payload or {}
            result = client.query_points(
                collection,
                query=cast(list[float], point.vector),
                limit=5,
                query_filter=Filter(
                    must=[
                        FieldCondition(
                            key="document_id", match=MatchValue(value=payload["document_id"])
                        )
                    ]
                ),
            )
            if not result.points or result.points[0].score < 0.999:
                raise ValueError("Vector/filter smoke query failed")
            if any(
                (p.payload or {}).get("document_id") != payload["document_id"]
                for p in result.points
            ):
                raise ValueError("Filter returned an unexpected document")
            query_checks += 1
        verification = VerificationReport(
            source_fingerprint=report.source_fingerprint,
            collection=collection,
            expected_points=report.unique_chunks,
            actual_points=store.count(),
            compared_payloads=len(expected),
            compared_vectors=compared,
            vector_queries_passed=query_checks,
        )
        checkpoint.verified = True
        write_model(self.verification_path, verification)
        write_model(self.checkpoint_path, checkpoint)
        if activate_alias:
            aliases = client.get_aliases().aliases
            alias = "vn_legal_bge_m3"
            existing = next((a for a in aliases if a.alias_name == alias), None)
            if existing is not None and existing.collection_name != collection:
                raise ValueError("Alias points to a different collection; refusing replacement")
            if existing is None:
                if client.collection_exists(alias):
                    raise ValueError("Alias name is occupied by a collection")
                client.update_collection_aliases(
                    [
                        CreateAliasOperation(
                            create_alias=CreateAlias(collection_name=collection, alias_name=alias)
                        ),
                    ]
                )
            verification.alias_activated = True
            checkpoint.alias_activated = True
            write_model(self.verification_path, verification)
            write_model(self.checkpoint_path, checkpoint)
            with atomic_open(self.paths.index_manifest) as stream:
                json.dump(
                    {
                        "mode": "server",
                        "collection_name": collection,
                        "query_alias": alias,
                        "total_vectors": verification.actual_points,
                        "embedding_model_id": self.spec.model_id,
                        "vector_size": self.spec.vector_size,
                        "distance": self.spec.distance,
                        "normalize_embeddings": self.spec.normalize,
                        "max_seq_length": self.spec.max_seq_length,
                        "source_fingerprint": report.source_fingerprint,
                        "reused_vectors": report.reused_vectors,
                        "encoded_vectors": report.encoded_vectors,
                        "verified_payloads": verification.compared_payloads,
                        "verified_vectors": verification.compared_vectors,
                    },
                    stream,
                    ensure_ascii=False,
                    indent=2,
                )
        return verification
