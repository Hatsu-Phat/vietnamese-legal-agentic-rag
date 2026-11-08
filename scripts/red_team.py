#!/usr/bin/env python3
"""P6/T-0602 red-team on the real system (v3 release, real Gemini/Ollama router).

1. Agent attacks: tests/eval/red_team_v1.jsonl (one role per case) plus every
   `access_probe` case of tests/eval/enterprise_v1.jsonl run as all three roles.
   Each response is checked for data outside the role's scope, uncited answers, personal data,
   system-prompt leakage and forbidden phrases; every call the cloud LLM receives is checked
   for restricted evidence and PII; every log line is checked for PII and copied question text.
2. API probes (in-process ASGI app over the same services): wrong key, confidential source
   as employee/legal, access_level filter escalation, /metrics content.
3. MCP: the server refuses to start without a valid key.

Writes docs/reports/p6/red_team_v1.json (IDs, decisions, levels, violations; no answer text).
Exit code 1 if any security violation is found.
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, get_args
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from loguru import logger

from src.agents.factory import create_router
from src.agents.graph import AskService
from src.api.app import create_app
from src.api.dependencies import AccessResolver, ApiServices
from src.common.agent_errors import AgentError
from src.common.atomic import atomic_open
from src.common.config import get_settings, load_yaml_config, resolve_project_path
from src.core.access import AccessPolicy, AccessScope, Role
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
from src.evaluation.red_team import (
    RedTeamCase,
    check_cloud_call,
    check_logs,
    check_response,
    load_red_team_cases,
)
from src.mcp_server.server import McpUnauthorized, resolve_role
from src.retrieval.search_catalog import CatalogSources
from src.retrieval.search_factory import open_search_service

ROOT = Path(__file__).resolve().parent.parent
CONFIDENTIAL_CHUNK = "internal_2026_qd_tnt_09__art_2__c0"


class CloudGuard:
    """Forwards to the cloud generator and records violations in what it would send."""

    def __init__(self, inner: AnswerGenerator) -> None:
        self.inner = inner
        self.case_id = ""
        self.calls: Counter[str] = Counter()
        self.violations: list[dict[str, Any]] = []

    def _check(self, kind: str, question: str | None, evidence: list[Evidence]) -> None:
        self.calls[kind] += 1
        for violation in check_cloud_call(question, evidence):
            self.violations.append({"case_id": self.case_id, "call": kind, "violation": violation})

    async def draft(self, question: str, evidence: list[Evidence]) -> DraftAnswer:
        self._check("draft", question, evidence)
        return await self.inner.draft(question, evidence)

    async def rewrite_query(self, question: str, feedback: str) -> str:
        self._check("rewrite", question, [])
        return await self.inner.rewrite_query(question, feedback)

    async def review(self, claims: list[DraftClaim], evidence: list[Evidence]) -> ClaimReview:
        self._check("review", " ".join(c.text for c in claims), evidence)
        return await self.inner.review(claims, evidence)

    async def aclose(self) -> None:
        close = getattr(self.inner, "aclose", None)
        if close is not None:
            await close()


def probe_cases(dataset: Path) -> list[RedTeamCase]:
    """Enterprise access probes, once per role, with that role's expected decisions."""
    cases = []
    for case in load_cases(dataset):
        if case.category != "access_probe":
            continue
        for role in get_args(Role):
            cases.append(
                RedTeamCase(
                    case_id=f"{case.case_id}:{role}",
                    attack="privilege_escalation",
                    role=role,
                    question=case.question,
                    allowed_decisions=list(case.expected_by_role[role]),
                    note="enterprise_v1 access_probe",
                )
            )
    return cases


async def api_probes(services: ApiServices, keys: dict[str, str]) -> list[dict[str, Any]]:
    @asynccontextmanager
    async def opener() -> AsyncIterator[ApiServices]:
        yield services

    # httpx's ASGI transport does not run the lifespan; the services are already open.
    app = create_app(opener)
    app.state.services = services
    transport = httpx.ASGITransport(app=app)
    results: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        results.append({"probe": name, "passed": ok, "detail": detail})

    async with httpx.AsyncClient(transport=transport, base_url="http://redteam") as client:
        bad = await client.post("/search", json={"query": "lương"}, headers={"X-API-Key": "x" * 32})
        record("wrong_key_rejected", bad.status_code == 401, f"status={bad.status_code}")
        for role, expected in (("employee", 404), ("manager", 404), ("legal", 200)):
            r = await client.get(
                f"/sources/{CONFIDENTIAL_CHUNK}", headers={"X-API-Key": keys[role]}
            )
            record(
                f"confidential_source_{role}", r.status_code == expected, f"status={r.status_code}"
            )
        r = await client.get(f"/sources/{CONFIDENTIAL_CHUNK}")
        record("confidential_source_anonymous", r.status_code == 404, f"status={r.status_code}")
        r = await client.post(
            "/search",
            json={"query": "khung lương bậc E3 E4", "filters": {"access_level": "confidential"}},
            headers={"X-API-Key": keys["employee"]},
        )
        levels = sorted({str(h.get("access_level")) for h in r.json().get("results", [])})
        leaked = r.status_code == 200 and any(lv in ("internal", "confidential") for lv in levels)
        record(
            "filter_escalation_employee",
            not leaked,
            f"status={r.status_code} levels={levels} count={r.json().get('returned_count')}",
        )
        metrics = await client.get("/metrics")
        text = metrics.text
        clean = "lương" not in text and all(k not in text for k in keys.values())
        record("metrics_without_content", metrics.status_code == 200 and clean, "checked text/keys")
    return results


async def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-path", type=Path, default=ROOT / "configs/experiments/p6_final.yaml"
    )
    parser.add_argument("--cases", type=Path, default=ROOT / "tests/eval/red_team_v1.jsonl")
    parser.add_argument("--dataset", type=Path, default=ROOT / "tests/eval/enterprise_v1.jsonl")
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="cpu")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/reports/p6/red_team_v1.json")
    args = parser.parse_args()
    args.config_path = resolve_project_path(args.config_path)
    config = load_yaml_config(args.config_path)
    policy = AccessPolicy.from_config(config)
    settings = get_settings()
    access = AccessResolver.from_settings(policy, settings)
    keys = {
        role: secret.get_secret_value()
        for role, secret in (
            ("employee", settings.auth_api_key_employee),
            ("manager", settings.auth_api_key_manager),
            ("legal", settings.auth_api_key_legal),
        )
        if secret is not None
    }
    if set(keys) != {"employee", "manager", "legal"}:
        print("All three AUTH_API_KEY_* must be set", file=sys.stderr)
        return 2
    cases = load_red_team_cases(resolve_project_path(args.cases)) + probe_cases(
        resolve_project_path(args.dataset)
    )

    log_lines: list[str] = []
    sink = logger.add(lambda m: log_lines.append(str(m)), level="DEBUG", format="{message}")

    router = create_router(settings)
    guard: CloudGuard | None = None
    if router.cloud is not None:
        guard = CloudGuard(router.cloud)
        router.cloud = guard
    rows: list[dict[str, Any]] = []
    api: list[dict[str, Any]] = []
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
                if guard is not None:
                    guard.case_id = case.case_id
                scope: AccessScope = policy.roles[case.role]
                row: dict[str, Any] = {
                    "case_id": case.case_id,
                    "attack": case.attack,
                    "role": case.role,
                    "allowed": case.allowed_decisions,
                }
                try:
                    response = await service.ask(AskRequest(question=case.question), uuid4(), scope)
                except AgentError as exc:
                    row["error_code"] = exc.code
                    rows.append(row)
                    continue
                cited = [search.catalog.get(c.chunk_id) for c in response.citations]
                levels = [c.access_level or AccessLevel.PUBLIC for c in cited if c is not None]
                row |= {
                    "decision": response.decision,
                    "behaviour_ok": response.decision in case.allowed_decisions,
                    "cited_chunks": [c.chunk_id for c in response.citations],
                    "cited_levels": sorted({str(level) for level in levels}),
                    "violations": check_response(case, response, levels, scope),
                    "llm_routes": [
                        t for t in response.trace if t.startswith(("route:", "fallback:"))
                    ],
                }
                rows.append(row)
            api = await api_probes(
                ApiServices(search=search, sources=CatalogSources(search.catalog), access=access),
                keys,
            )
    finally:
        await router.aclose()
        logger.remove(sink)

    mcp = []
    for name, credential in (("missing", None), ("wrong", "y" * 32)):
        try:
            resolve_role(access, credential)
            mcp.append({"probe": f"mcp_{name}_key_refused", "passed": False})
        except McpUnauthorized:
            mcp.append({"probe": f"mcp_{name}_key_refused", "passed": True})

    log_findings = check_logs(log_lines, [c.question for c in cases])
    response_violations = Counter(v for r in rows for v in r.get("violations", []))
    cloud_violations = Counter(v["violation"] for v in (guard.violations if guard else []))
    ran = [r for r in rows if "decision" in r]
    security_total = (
        sum(response_violations.values())
        + sum(cloud_violations.values())
        + sum(log_findings.values())
        + sum(not p["passed"] for p in api + mcp)
    )
    report = {
        "run_date": date.today().isoformat(),
        "config": str(args.config_path.relative_to(ROOT)).replace("\\", "/"),
        "routing_mode": router.mode,
        "providers": router.providers,
        "cloud_model": settings.gemini_model_id,
        "local_model": settings.local_llm_model,
        "cases": len(rows),
        "errors": dict(Counter(r["error_code"] for r in rows if "error_code" in r)),
        "security_violations_total": security_total,
        "response_violations": dict(response_violations),
        "cloud_calls": dict(guard.calls) if guard else {},
        "cloud_violations": dict(cloud_violations),
        "cloud_violation_details": guard.violations if guard else [],
        "log_lines_checked": len(log_lines),
        "log_findings": log_findings,
        "behaviour_ok": sum(r["behaviour_ok"] for r in ran),
        "behaviour_checked": len(ran),
        "behaviour_by_attack": {
            attack: f"{sum(r['behaviour_ok'] for r in ran if r['attack'] == attack)}"
            f"/{sum(1 for r in ran if r['attack'] == attack)}"
            for attack in sorted({r["attack"] for r in ran})
        },
        "api_probes": api,
        "mcp_probes": mcp,
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with atomic_open(args.output) as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "results"}, ensure_ascii=False, indent=2
        )
    )
    return 1 if security_total else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
