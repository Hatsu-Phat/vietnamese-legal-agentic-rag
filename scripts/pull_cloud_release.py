#!/usr/bin/env python3
"""Rebuild local search artifacts (chunks, BM25, dense manifest) from Qdrant Cloud.

Read-only on Cloud. Steps:
  pull         export canonical chunks, rebuild BM25, write index manifest (no query revision)
  model-check  download the pinned BGE-M3 revision, compare with Cloud vectors, record revision
  all          pull then model-check
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdrant_client import QdrantClient

from src.common.config import DEFAULT_CONFIG_PATH, AppSettings, load_data_paths, load_yaml_config
from src.ingestion.indexing.cloud_release import CloudReleasePull


def main() -> int:
    """Execute the requested steps; never writes to Cloud."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--step", choices=["pull", "model-check", "all"], required=True)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    args = parser.parse_args()
    config = load_yaml_config(args.config_path)
    paths = load_data_paths(config)
    settings = AppSettings()
    if not settings.qdrant_url or not settings.qdrant_api_key:
        raise ValueError("QDRANT_URL and QDRANT_API_KEY are required in .env")
    client = QdrantClient(
        url=settings.qdrant_url, api_key=settings.qdrant_api_key.get_secret_value(), timeout=60
    )
    try:
        release = CloudReleasePull(client, paths, config["indexing"]["qdrant"])
        total = client.count(release.collection, exact=True).count
        if args.step in {"pull", "all"}:
            from scripts.run_phase2_indexing import run_bm25_step

            chunks = release.pull_chunks()
            run_bm25_step(config, chunks)
            release.write_manifest(len(chunks))
        if args.step in {"model-check", "all"}:
            if not paths.chunks.is_file():
                raise FileNotFoundError("Run --step pull first")
            release.write_manifest(total, release.model_check(args.device))
        print(f"Release {release.collection} ready at {paths.index_manifest}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError) as exc:
        print(f"Pull stopped: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except Exception as exc:
        # SDK exception text may contain request details; do not echo it with credentials loaded.
        print(f"Pull stopped ({type(exc).__name__})", file=sys.stderr)
        raise SystemExit(2) from None
