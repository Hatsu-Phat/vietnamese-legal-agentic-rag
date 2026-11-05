#!/usr/bin/env python3
"""Build the P1 Cloud release (laws + internal documents in one collection), one step at a time.

Steps:
  prepare  canonical chunks + new official laws + internal documents -> <out>/chunks.jsonl
  upload   create/resume --collection; copy unchanged vectors from the alias' collection,
           encode only new chunks (BGE-M3, pinned revision)
  verify   count, every payload hash, sampled vectors, access_level filter counts
  activate repoint the query alias to --collection (needs --expected-current)

Nothing changes the alias unless ``activate`` is run explicitly.

Example:
  python scripts/build_enterprise_release.py --step prepare
  python scripts/build_enterprise_release.py --step upload --collection vn_legal_bge_m3_v3
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.chunking.legal_chunker import LegalChunker
from src.common.atomic import atomic_open
from src.common.config import DEFAULT_CONFIG_PATH, AppSettings, load_yaml_config
from src.core.models import LegalChunk, LegalDocument, LegalProvision
from src.ingestion.indexing.enterprise_release import (
    activate_alias,
    assemble_release,
    release_fingerprint,
    upload_release,
    verify_release,
)
from src.ingestion.internal_docs import load_internal_dir
from src.ingestion.official_metadata import OFFICIAL_SOURCE_DATASET


def _jsonl[T: (LegalChunk, LegalDocument, LegalProvision)](path: Path, model: type[T]) -> list[T]:
    with path.open(encoding="utf-8") as f:
        return [model.model_validate_json(line) for line in f if line.strip()]


def prepare(args: argparse.Namespace, config: dict) -> None:  # type: ignore[type-arg]
    chunk_cfg = config["chunking"]
    chunker = LegalChunker(
        max_words_per_chunk=chunk_cfg["max_words_per_chunk"],
        overlap_words=chunk_cfg["overlap_words"],
        min_words_per_chunk=chunk_cfg["min_words_per_chunk"],
        include_breadcrumb_in_embedding_text=chunk_cfg["include_breadcrumb_in_embedding_text"],
    )
    canonical = _jsonl(args.canonical, LegalChunk)
    p1_docs = _jsonl(args.p1_dir / "documents.jsonl", LegalDocument)
    official = [d for d in p1_docs if d.source_dataset == OFFICIAL_SOURCE_DATASET]
    official_ids = {d.document_id for d in official}
    official_provs = [
        p
        for p in _jsonl(args.p1_dir / "provisions.jsonl", LegalProvision)
        if p.document_id in official_ids
    ]
    internal_docs, internal_provs = load_internal_dir(args.internal_dir)
    chunks, reused, report = assemble_release(
        canonical,
        {d.document_id: d.status for d in p1_docs},
        official + internal_docs,
        official_provs + internal_provs,
        chunker,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with atomic_open(args.out_dir / "chunks.jsonl") as f:
        for chunk in chunks:
            f.write(chunk.model_dump_json() + "\n")
    with atomic_open(args.out_dir / "reused_uuids.txt") as f:
        f.write("\n".join(sorted(reused)) + "\n")
    summary = asdict(report) | {
        "source_fingerprint": release_fingerprint(chunks),
        "internal_documents": len(internal_docs),
        "official_documents": len(official),
    }
    (args.out_dir / "release_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def main() -> int:
    """Run one step; exit 2 on a validation error."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--step", choices=["prepare", "upload", "verify", "activate"], required=True
    )
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--canonical", type=Path, default=Path("data/artifacts/processed/chunks.jsonl")
    )
    parser.add_argument("--p1-dir", type=Path, default=Path("data/processed_p1"))
    parser.add_argument("--internal-dir", type=Path, default=Path("corpus/internal"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/artifacts/releases/p1"))
    parser.add_argument("--collection", default="vn_legal_bge_m3_v3")
    parser.add_argument("--expected-current", help="collection the alias points to now")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    config = load_yaml_config(args.config_path)
    if args.step == "prepare":
        prepare(args, config)
        return 0

    from qdrant_client import QdrantClient

    from src.retrieval.qdrant_store import QdrantStore

    qcfg = config["indexing"]["qdrant"]
    settings = AppSettings()
    if not settings.qdrant_url or not settings.qdrant_api_key:
        raise ValueError("QDRANT_URL and QDRANT_API_KEY are required")
    client = QdrantClient(
        url=settings.qdrant_url, api_key=settings.qdrant_api_key.get_secret_value(), timeout=120
    )
    try:
        alias = str(qcfg["collection_name"])
        if args.step == "activate":
            if not args.expected_current:
                parser.error("--expected-current is required for activate")
            activate_alias(client, alias, args.collection, args.expected_current)
            print(f"Alias {alias} -> {args.collection}")
            return 0
        aliases = {a.alias_name: a.collection_name for a in client.get_aliases().aliases}
        source = aliases.get(alias, alias)
        if source == args.collection:
            raise ValueError("Target collection is the one currently served by the alias")
        chunks = _jsonl(args.out_dir / "chunks.jsonl", LegalChunk)
        reused = set((args.out_dir / "reused_uuids.txt").read_text(encoding="utf-8").split())
        report = json.loads((args.out_dir / "release_report.json").read_text(encoding="utf-8"))
        if release_fingerprint(chunks) != report["source_fingerprint"]:
            raise ValueError("chunks.jsonl changed since prepare")

        from src.retrieval.embedding import BGEEncoder

        encoder = BGEEncoder(
            qcfg["embedding_model_id"],
            args.device,
            qcfg["max_seq_length"],
            revision=qcfg["embedding_revision"],
        )

        def encode(texts: list[str]) -> list[list[float]]:
            vectors = encoder.model.encode(
                texts,
                batch_size=len(texts),
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            return [list(map(float, v)) for v in vectors]

        store = QdrantStore(client, args.collection, int(qcfg["vector_size"]))
        metadata = {
            "source_fingerprint": report["source_fingerprint"],
            "embedding": {
                "model_id": qcfg["embedding_model_id"],
                "vector_size": qcfg["vector_size"],
                "distance": qcfg["distance"],
                "normalize": True,
                "max_seq_length": qcfg["max_seq_length"],
                "revision": qcfg["embedding_revision"],
            },
        }
        if args.step == "upload":
            result = upload_release(
                client, source, store, metadata, chunks, reused, encode, args.batch_size
            )
        else:
            store.ensure_collection(metadata)
            result = verify_release(client, source, store, chunks, reused, encode)
            (args.out_dir / "verification.json").write_text(
                json.dumps(result | {"collection": args.collection, "source": source}, indent=1),
                encoding="utf-8",
            )
        print(json.dumps(result, ensure_ascii=False, indent=1))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError) as exc:
        print(f"Release stopped: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
