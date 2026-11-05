#!/usr/bin/env python3
"""Evaluate retrieval on the enterprise set (T-0103) against a pinned release.

Use the P1 experiment config so the alias does not have to move:
  python scripts/evaluate_enterprise.py --config-path configs/experiments/p1_release_v3.yaml --device cpu
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.atomic import atomic_open
from src.common.config import DEFAULT_CONFIG_PATH, load_data_paths, load_yaml_config
from src.common.search_errors import SearchError
from src.core.enums import AccessLevel
from src.evaluation.enterprise import evaluate_enterprise
from src.retrieval.search_factory import open_search_service

DEFAULT_DATASET = Path("tests/eval/enterprise_v1.jsonl")


async def run() -> int:
    """Save the full report; exit 2 when any case failed or gold ids are not in the release."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    paths = load_data_paths(load_yaml_config(args.config_path))
    try:
        async with open_search_service(args.config_path, args.device) as service:
            levels = {
                chunk.provision_id: chunk.access_level or AccessLevel.PUBLIC
                for chunk in service.catalog.chunks.values()
            }
            report = await evaluate_enterprise(service, args.dataset, levels, top_k=args.top_k)
    except SearchError as exc:
        print(f"Evaluation stopped: {exc.code}", file=sys.stderr)
        return 2
    output = paths.reports_dir / f"enterprise_retrieval_{args.dataset.stem.split('_')[-1]}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with atomic_open(output) as stream:
        stream.write(report.model_dump_json(indent=2) + "\n")
    print(report.model_dump_json(indent=2, exclude={"results"}))
    print(f"Saved {output}")
    return 0 if report.complete else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
