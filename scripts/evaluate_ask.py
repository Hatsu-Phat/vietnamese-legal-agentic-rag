#!/usr/bin/env python3
"""Evaluate /ask end to end on dev: seeded benchmark sample + authored edge cases."""

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.factory import create_router
from src.agents.graph import AskService
from src.agents.router import ModelRouter
from src.common.agent_errors import AgentError
from src.common.atomic import atomic_open
from src.common.config import (
    DEFAULT_CONFIG_PATH,
    AppSettings,
    get_settings,
    load_data_paths,
    load_yaml_config,
    resolve_project_path,
)
from src.common.search_errors import SearchError
from src.core.answer_evaluation import AskCaseResult, AskEvaluationReport
from src.evaluation.answer import (
    load_cases,
    run_cases,
    sample_answerable,
    summarize,
    summarize_by_type,
)
from src.retrieval.search_factory import open_search_service

DEFAULT_CASES = "tests/eval/ask_dev_authored.jsonl"


def llm_label(settings: AppSettings, router: ModelRouter) -> str:
    """Models that may answer in this run, e.g. `ollama:qwen2.5:3b+gemini:gemini-2.5-flash`."""
    names = []
    if router.local is not None:
        names.append(f"ollama:{settings.local_llm_model}")
    if router.cloud is not None:
        names.append(f"gemini:{settings.gemini_model_id}")
    return "+".join(names)


async def run() -> int:
    """Never tune on qa_test; a run with any error is saved but marked incomplete."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    parser.add_argument("--answerable", type=int, default=30, help="qa_dev questions to sample")
    parser.add_argument("--cases", type=Path, default=Path(DEFAULT_CASES))
    parser.add_argument("--delay", type=float, default=4.0, help="seconds between questions")
    parser.add_argument("--seed", type=int, default=42, help="fixes the qa_dev sample")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--routing",
        choices=["auto", "local", "cloud"],
        help="override LLM_ROUTING_MODE; sensitive cases stay local in every mode",
    )
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="re-run only the cases that errored in the existing report and replace them",
    )
    args = parser.parse_args()
    paths = load_data_paths(load_yaml_config(args.config_path))
    seed = args.seed
    settings = get_settings()
    cases = sample_answerable(paths.benchmarks_dir / "qa_dev.jsonl", args.answerable, seed)
    cases += load_cases(resolve_project_path(args.cases))
    output = args.output or paths.reports_dir / "ask_dev.json"
    previous: list[AskCaseResult] = []
    retried: list[str] = []
    if args.retry_errors:
        previous = AskEvaluationReport.model_validate_json(
            output.read_text(encoding="utf-8")
        ).results
        retried = [r.case_id for r in previous if r.error_code is not None]
        cases = [c for c in cases if c.case_id in set(retried)]
    try:
        generator = create_router(settings, args.routing)
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

            try:
                results = await run_cases(service, cases, provision_of, args.delay)
            finally:
                await generator.aclose()
            if args.retry_errors:
                fresh = {r.case_id: r for r in results}
                results = [fresh.get(r.case_id, r) for r in previous]
            report = AskEvaluationReport(
                split="dev",
                llm_model=llm_label(settings, generator),
                release_fingerprint=search.release_fingerprint,
                run_date=date.today(),
                seed=seed,
                parameters={
                    "answerable_sample": args.answerable,
                    "authored_cases": str(args.cases).replace("\\", "/"),
                    "max_retrieval_rounds": settings.max_retrieval_rounds,
                    "strict_citation_verification": settings.strict_citation_verification,
                    "llm_temperature": settings.llm_temperature,
                    "top_k": 8,
                    "delay_seconds": args.delay,
                    "llm_routing_mode": generator.mode,
                    "llm_providers": generator.providers,
                    "local_llm_num_ctx": settings.local_llm_num_ctx,
                    "retried_error_cases": retried,
                },
                complete=all(r.error_code is None for r in results),
                overall=summarize(results),
                by_type=summarize_by_type(results),
                results=results,
            )
    except (AgentError, SearchError) as exc:
        print(f"Evaluation stopped: {exc.code}", file=sys.stderr)
        return 2
    with atomic_open(output) as stream:
        stream.write(report.model_dump_json(indent=2) + "\n")
    print(report.model_dump_json(indent=2, exclude={"results"}))
    return 0 if report.complete else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
