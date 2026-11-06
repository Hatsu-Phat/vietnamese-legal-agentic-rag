#!/usr/bin/env python3
"""RAGAS faithfulness on dev: re-run /ask for cases that expect an answer, then judge them.

Needs the eval extra: uv run --locked --extra dev --extra eval python scripts/evaluate_faithfulness.py
"""

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.factory import create_router
from src.agents.graph import AskService
from src.common.agent_errors import AgentError, LLMUnavailable
from src.common.atomic import atomic_open
from src.common.config import (
    DEFAULT_CONFIG_PATH,
    get_settings,
    load_data_paths,
    load_yaml_config,
    resolve_project_path,
)
from src.common.search_errors import SearchError
from src.core.answer_evaluation import FaithfulnessReport
from src.evaluation.answer import load_cases, run_cases, sample_answerable
from src.evaluation.ragas_runner import gemini_faithfulness, score_answers, summarize_faithfulness
from src.retrieval.search_factory import open_search_service

os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")


async def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    parser.add_argument("--answerable", type=int, default=30)
    parser.add_argument("--cases", type=Path, default=Path("tests/eval/ask_dev_authored.jsonl"))
    parser.add_argument("--delay", type=float, default=4.0)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    paths = load_data_paths(load_yaml_config(args.config_path))
    settings = get_settings()
    cases = sample_answerable(paths.benchmarks_dir / "qa_dev.jsonl", args.answerable, args.seed)
    cases += [
        c for c in load_cases(resolve_project_path(args.cases)) if "answer" in c.expected_decisions
    ]
    questions = {c.case_id: c.question for c in cases}
    key = settings.gemini_api_key or settings.llm_api_key
    try:
        if key is None:
            raise LLMUnavailable()
        judge = gemini_faithfulness(key, settings.gemini_model_id)
        generator = create_router(settings)
        async with open_search_service(args.config_path, args.device) as search:
            service = AskService(
                search,
                generator,
                settings.max_retrieval_rounds,
                verify_claims=settings.strict_citation_verification,
            )

            def provision_of(chunk_id: str) -> str | None:
                chunk = search.catalog.get(chunk_id)
                return chunk.provision_id if chunk else None

            def text_of(chunk_id: str) -> str | None:
                chunk = search.catalog.get(chunk_id)
                return chunk.text if chunk else None

            try:
                results = await run_cases(service, cases, provision_of, args.delay)
            finally:
                await generator.aclose()
            scored = await score_answers(results, questions, text_of, judge)
    except (AgentError, SearchError) as exc:
        print(f"Evaluation stopped: {exc.code}", file=sys.stderr)
        return 2
    report = FaithfulnessReport(
        split="dev",
        llm_model=settings.gemini_model_id,
        judge_model=settings.gemini_model_id,
        run_date=date.today(),
        parameters={
            "answerable_sample": args.answerable,
            "authored_cases": str(args.cases).replace("\\", "/"),
            "seed": args.seed,
            "contexts": "text of cited chunks only",
            "ask_errors": sum(r.error_code is not None for r in results),
            "decisions": {
                d: sum(r.decision == d for r in results) for d in ("answer", "clarify", "abstain")
            },
        },
        cases=scored,
        **summarize_faithfulness(scored),  # type: ignore[arg-type]
    )
    with atomic_open(paths.reports_dir / "faithfulness_dev.json") as stream:
        stream.write(report.model_dump_json(indent=2) + "\n")
    print(report.model_dump_json(indent=2, exclude={"cases"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
