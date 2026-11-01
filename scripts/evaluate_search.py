#!/usr/bin/env python3
"""Evaluate Search v1 on dev without changing the active index or tuning on test."""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.atomic import atomic_open
from src.common.config import DEFAULT_CONFIG_PATH, load_data_paths, load_yaml_config
from src.common.search_errors import SearchError
from src.evaluation.retrieval import evaluate_retrieval
from src.retrieval.search_factory import open_search_service


async def run() -> int:
    """Save typed report including all failures; never present a partial run as complete."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    paths = load_data_paths(load_yaml_config(args.config_path))
    try:
        async with open_search_service(args.config_path, args.device) as service:
            report = await evaluate_retrieval(
                service, paths.benchmarks_dir / "qa_dev.jsonl", top_k=args.top_k, limit=args.limit
            )
            output = paths.reports_dir / (
                "search_dev_smoke.json" if args.limit else "search_dev.json"
            )
            with atomic_open(output) as stream:
                stream.write(report.model_dump_json(indent=2) + "\n")
            print(report.model_dump_json(indent=2, exclude={"results"}))
            return 0 if report.complete else 2
    except SearchError as exc:
        print(f"Evaluation stopped: {exc.code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
