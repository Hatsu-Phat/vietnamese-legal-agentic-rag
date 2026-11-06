#!/usr/bin/env python3
"""Paired comparison of /ask reports (P4): one Markdown table per model, baseline first.

Every report must come from the same cases (same seed and authored set); per case, a variant
"wins" when it gets the decision right and the baseline does not, and "loses" in the reverse.
The two-sided exact sign test over wins vs losses is reported as a rough guide only.

Example:
  python scripts/compare_prompting.py docs/reports/p4/ask_dev_local_zero_shot.json \
      docs/reports/p4/ask_dev_local_few_shot.json docs/reports/p4/ask_dev_local_cot.json
"""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


def correct(row: dict[str, Any]) -> bool:
    return row["error_code"] is None and row["decision"] in row["expected_decisions"]


def sign_test(wins: int, losses: int) -> float | None:
    """Two-sided exact binomial test with p = 0.5 on the discordant pairs."""
    n = wins + losses
    if n == 0:
        return None
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def fmt(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path, help="baseline first")
    args = parser.parse_args()
    loaded = [json.loads(p.read_text(encoding="utf-8")) for p in args.reports]
    base_ids = [r["case_id"] for r in loaded[0]["results"]]
    for path, report in zip(args.reports, loaded, strict=True):
        if [r["case_id"] for r in report["results"]] != base_ids:
            print(f"{path} has different cases than the baseline", file=sys.stderr)
            return 2
    base = {r["case_id"]: correct(r) for r in loaded[0]["results"]}
    print(
        "| Report | Prompt | Decision acc. | Answer rate | False answer | Clarify (ambig.) "
        "| Gold hit | Citation valid. | Mean / p95 ms | Calls | Prompt tok/case "
        "| Output tok/case | Schema errors | Wins/losses vs base | Sign test p |"
    )
    print("|" + "---|" * 15)
    for path, report in zip(args.reports, loaded, strict=True):
        o = report["overall"]
        params = report["parameters"]
        usage: dict[str, int] = {}
        for counters in params.get("llm_usage", {}).values():
            for key, value in counters.items():
                usage[key] = usage.get(key, 0) + value
        cases = max(1, o["cases"])
        wins = sum(correct(r) and not base[r["case_id"]] for r in report["results"])
        losses = sum(not correct(r) and base[r["case_id"]] for r in report["results"])
        p = sign_test(wins, losses)
        print(
            f"| `{path.name}` | {params.get('prompt_variant', '?')} "
            f"| {fmt(o['decision_accuracy'])} | {fmt(o['answer_rate_on_answerable'])} "
            f"| {fmt(o['false_answer_rate_on_unanswerable'])} "
            f"| {fmt(o['clarify_rate_on_ambiguous'])} | {fmt(o['gold_hit_rate'])} "
            f"| {fmt(o['citation_validity'])} "
            f"| {fmt(o['mean_ms'], 0)} / {fmt(o['p95_ms'], 0)} | {usage.get('calls', 0)} "
            f"| {usage.get('prompt_tokens', 0) // cases} | {usage.get('output_tokens', 0) // cases} "
            f"| {usage.get('schema_errors', 0)} | {wins}/{losses} | {fmt(p)} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
