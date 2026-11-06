#!/usr/bin/env python3
"""P3 acceptance on real data: no internal/confidential evidence ever reaches the cloud LLM.

Runs /ask (AskService, real retrieval on the pinned v3 release, real Ollama + Gemini) for every
case in tests/eval/enterprise_v1.jsonl as one role. The cloud provider is wrapped by a spy that
records the access levels of the evidence in every call it receives; the run fails if any call
carried restricted evidence.
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, get_args
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.factory import create_router
from src.agents.graph import AskService
from src.agents.router import RESTRICTED
from src.common.agent_errors import AgentError
from src.common.atomic import atomic_open
from src.common.config import get_settings, load_yaml_config, resolve_project_path
from src.core.access import AccessPolicy, Role
from src.core.agent import (
    AnswerGenerator,
    AskRequest,
    ClaimReview,
    DraftAnswer,
    DraftClaim,
    Evidence,
)
from src.core.enums import AccessLevel
from src.evaluation.enterprise import load_cases
from src.retrieval.search_factory import open_search_service

ROOT = Path(__file__).resolve().parent.parent


class CloudSpy:
    """Forwards to the real cloud generator and records what each call would send."""

    def __init__(self, inner: AnswerGenerator) -> None:
        self.inner = inner
        self.calls: Counter[str] = Counter()
        self.restricted_calls: list[dict[str, Any]] = []
        self.case_id = ""

    def _record(self, kind: str, evidence: list[Evidence]) -> None:
        self.calls[kind] += 1
        levels = {str(e.access_level or AccessLevel.PUBLIC) for e in evidence}
        if any((e.access_level or AccessLevel.PUBLIC) in RESTRICTED for e in evidence):
            self.restricted_calls.append(
                {"case_id": self.case_id, "call": kind, "levels": sorted(levels)}
            )

    async def draft(self, question: str, evidence: list[Evidence]) -> DraftAnswer:
        self._record("draft", evidence)
        return await self.inner.draft(question, evidence)

    async def rewrite_query(self, question: str, feedback: str) -> str:
        self._record("rewrite", [])
        return await self.inner.rewrite_query(question, feedback)

    async def review(self, claims: list[DraftClaim], evidence: list[Evidence]) -> ClaimReview:
        self._record("review", evidence)
        return await self.inner.review(claims, evidence)

    async def aclose(self) -> None:
        close = getattr(self.inner, "aclose", None)
        if close is not None:
            await close()


async def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-path", type=Path, default=ROOT / "configs/experiments/p1_release_v3.yaml"
    )
    parser.add_argument("--dataset", type=Path, default=ROOT / "tests/eval/enterprise_v1.jsonl")
    parser.add_argument("--role", choices=get_args(Role), default="legal")
    parser.add_argument("--routing", choices=["auto", "local", "cloud"])
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="cpu")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    role: Role = args.role
    config = load_yaml_config(args.config_path)
    scope = AccessPolicy.from_config(config).roles[role]
    settings = get_settings()
    cases = load_cases(resolve_project_path(args.dataset))
    output = args.output or ROOT / f"docs/reports/p3/routing_check_{role}.json"

    router = create_router(settings, args.routing)
    if router.cloud is None:
        print("No cloud LLM configured: nothing to check", file=sys.stderr)
        return 2
    spy = CloudSpy(router.cloud)
    router.cloud = spy
    rows: list[dict[str, Any]] = []
    try:
        async with open_search_service(args.config_path, args.device) as search:
            service = AskService(
                search,
                router,
                settings.max_retrieval_rounds,
                verify_claims=settings.strict_citation_verification,
            )
            for index, case in enumerate(cases):
                if index and args.delay:
                    await asyncio.sleep(args.delay)
                spy.case_id = case.case_id
                row: dict[str, Any] = {"case_id": case.case_id, "category": case.category}
                try:
                    response = await service.ask(AskRequest(question=case.question), uuid4(), scope)
                except AgentError as exc:
                    row["error_code"] = exc.code
                    rows.append(row)
                    continue
                cited = [search.catalog.get(c.chunk_id) for c in response.citations]
                row |= {
                    "decision": response.decision,
                    "expected": case.expected_by_role[role],
                    "llm_routes": [
                        t for t in response.trace if t.startswith(("route:", "fallback:"))
                    ],
                    "cited_levels": sorted(
                        {str(c.access_level or AccessLevel.PUBLIC) for c in cited if c}
                    ),
                }
                rows.append(row)
    finally:
        await router.aclose()

    routes = Counter(t for r in rows for t in r.get("llm_routes", []))
    restricted_cases = [
        r["case_id"] for r in rows if "route:local:restricted_evidence" in r.get("llm_routes", [])
    ]
    answered = [r for r in rows if "decision" in r]
    report = {
        "run_date": date.today().isoformat(),
        "role": role,
        "access_scope": sorted(level.value for level in scope),
        "config": str(args.config_path.relative_to(ROOT)).replace("\\", "/"),
        "dataset": args.dataset.name,
        "routing_mode": router.mode,
        "providers": router.providers,
        "local_model": settings.local_llm_model,
        "cloud_model": settings.gemini_model_id,
        "cases": len(rows),
        "errors": dict(Counter(r["error_code"] for r in rows if "error_code" in r)),
        "cloud_calls": dict(spy.calls),
        "cloud_calls_with_restricted_evidence": len(spy.restricted_calls),
        "restricted_call_details": spy.restricted_calls,
        "cases_routed_local_for_restricted_evidence": len(restricted_cases),
        "answers_citing_restricted": sum(
            1
            for r in answered
            if r["decision"] == "answer" and set(r["cited_levels"]) & {"internal", "confidential"}
        ),
        "decision_accuracy": (
            sum(r["decision"] in r["expected"] for r in answered) / len(answered)
            if answered
            else None
        ),
        "route_counts": dict(routes),
        "results": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with atomic_open(output) as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "results"}, ensure_ascii=False, indent=2
        )
    )
    return 1 if spy.restricted_calls else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
