#!/usr/bin/env python3
"""Phase 5 dev ablation: Hybrid RRF vs cross-encoder reranking (never run on qa_test)."""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.atomic import atomic_open
from src.common.config import DEFAULT_CONFIG_PATH, load_data_paths, load_yaml_config
from src.common.search_errors import SearchError
from src.evaluation.rerank_ablation import evaluate_rerank
from src.retrieval.reranker import CrossEncoderReranker
from src.retrieval.search_factory import open_search_service

RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"


async def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    parser.add_argument("--pool", type=int, default=20)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--revision", default=RERANKER_REVISION)
    args = parser.parse_args()
    config = load_yaml_config(args.config_path)
    paths = load_data_paths(config)
    model_id = config["retrieval"]["reranking"]["model_name"]
    reranker = CrossEncoderReranker(model_id, args.device, revision=args.revision)
    reranker.score("khởi động", ["khởi động"])  # load once so latency excludes model loading
    try:
        async with open_search_service(args.config_path, args.device) as service:
            report = await evaluate_rerank(
                service,
                reranker.score,
                paths.benchmarks_dir / "qa_dev.jsonl",
                args.pool,
                args.limit,
            )
    except SearchError as exc:
        print(f"Ablation stopped: {exc.code}", file=sys.stderr)
        return 2
    report.parameters.update(
        {"reranker_model": model_id, "reranker_revision": reranker.revision, "max_length": 512}
    )
    name = "rerank_ablation_dev_smoke.json" if args.limit else "rerank_ablation_dev.json"
    with atomic_open(paths.reports_dir / name) as stream:
        stream.write(report.model_dump_json(indent=2) + "\n")
    print(report.model_dump_json(indent=2, exclude={"results"}))
    return 0 if report.complete else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
