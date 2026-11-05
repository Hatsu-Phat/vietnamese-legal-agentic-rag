#!/usr/bin/env python3
"""Audit, prepare, upload and verify an existing team index without full re-encoding."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdrant_client import QdrantClient

from src.common.config import (
    DEFAULT_CONFIG_PATH,
    PROJECT_ROOT,
    AppSettings,
    load_data_paths,
    load_yaml_config,
)
from src.ingestion.indexing.index_migration import TeamIndexMigration


def main() -> int:
    """Execute one explicit stage; model downloads happen only with --model-check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", choices=["audit", "prepare", "upload", "verify"], required=True)
    parser.add_argument("--source", type=Path, default=PROJECT_ROOT / "data/imports/team-phase2")
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--collection", default="vn_legal_bge_m3_v2")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--model-check", action="store_true")
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    args = parser.parse_args()
    config = load_yaml_config(args.config_path)
    migration = TeamIndexMigration(args.source, load_data_paths(config))
    if args.step in {"audit", "prepare"}:
        report = migration.prepare(audit_only=args.step == "audit")
        print(report.model_dump_json(indent=2, exclude={"checksums"}))
        if args.step == "prepare":
            from scripts.run_phase2_indexing import load_chunks_from_file, run_bm25_step
            from src.chunking.chunk_audit import ChunkAuditor

            chunks = load_chunks_from_file(migration.paths.chunks)
            run_bm25_step(config, chunks)
            auditor = ChunkAuditor()
            stats = auditor.compute_stats(chunks, {c.provision_id for c in chunks})
            stats["coverage_basis"] = (
                "Team chunks only; not evidence of complete raw-corpus coverage"
            )
            auditor.export_stats_json(stats, migration.paths.chunk_stats)
            auditor.generate_markdown_report(stats, migration.paths.reports_dir / "chunk_audit.md")
        return 0
    settings = AppSettings()
    if not settings.qdrant_url or not settings.qdrant_api_key:
        raise ValueError("QDRANT_URL and QDRANT_API_KEY are required")
    client = QdrantClient(
        url=settings.qdrant_url, api_key=settings.qdrant_api_key.get_secret_value(), timeout=60
    )
    try:
        if args.step == "upload":
            migration.upload(client, args.collection, args.resume, args.batch_size)
        else:
            report = migration.verify(client, args.collection)
            if args.model_check:
                from src.ingestion.indexing.model_audit import audit_model

                report = audit_model(migration, client, report, args.device)
            print(report.model_dump_json(indent=2))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError) as exc:
        print(f"Migration stopped: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except Exception as exc:
        # SDK exception text may contain request details; do not echo it with credentials loaded.
        print(
            f"Migration stopped ({type(exc).__name__}); progress is checkpointed", file=sys.stderr
        )
        raise SystemExit(2) from None
