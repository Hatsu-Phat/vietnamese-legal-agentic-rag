#!/usr/bin/env python3
"""Measure the PII guardrail (P2/T-0202).

Labelled set: tests/eval/pii_v1.jsonl (authored, unreviewed).
Extra negatives: real questions from data/benchmarks/qa_dev.jsonl (if present) and
tests/eval/enterprise_v1.jsonl, which contain no personal identifiers.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.atomic import atomic_open
from src.evaluation.pii import load_labelled, score

ROOT = Path(__file__).resolve().parent.parent


def questions(path: Path, key: str) -> list[str]:
    if not path.is_file():
        return []
    rows = path.read_text(encoding="utf-8").splitlines()
    return [str(json.loads(line)[key]) for line in rows if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "tests/eval/pii_v1.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/reports/p2/pii_v1.json")
    args = parser.parse_args()
    negatives = questions(ROOT / "data/benchmarks/qa_dev.jsonl", "question_text") + questions(
        ROOT / "tests/eval/enterprise_v1.jsonl", "question"
    )
    report = {"dataset": args.dataset.name, **score(load_labelled(args.dataset), negatives)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with atomic_open(args.output) as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps({k: v for k, v in report.items() if k != "errors"}, ensure_ascii=False, indent=2)
    )
    print("errors:", report["errors"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
