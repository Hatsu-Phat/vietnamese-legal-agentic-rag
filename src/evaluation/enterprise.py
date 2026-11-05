"""Retrieval evaluation of the enterprise set (T-0103) against one pinned release."""

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import uuid4

from src.common.search_errors import SearchError
from src.core.access import FULL_ACCESS, ROLES, AccessScope
from src.core.enterprise_evaluation import (
    ROLE_ACCESS,
    EnterpriseEvalCase,
    EnterpriseRetrievalReport,
    expected_decisions,
)
from src.core.enums import AccessLevel
from src.core.retrieval_evaluation import RetrievalMetrics
from src.core.search import SearchRequest
from src.evaluation.retrieval import recall, reciprocal_rank, unique_provisions
from src.retrieval.search_service import SearchService

LEVEL_ORDER = (AccessLevel.PUBLIC, AccessLevel.INTERNAL, AccessLevel.CONFIDENTIAL)


def load_cases(path: Path) -> list[EnterpriseEvalCase]:
    """Parse the JSONL set; case ids must be unique."""
    cases = [
        EnterpriseEvalCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case_id in enterprise evaluation set")
    return cases


def gold_access(
    cases: Sequence[EnterpriseEvalCase], levels: Mapping[str, AccessLevel]
) -> dict[str, AccessLevel]:
    """Check every gold id against the release and each case's expectations; return its top level.

    `levels` maps provision_id -> access_level as stored in the release chunks.
    """
    problems: list[str] = []
    top: dict[str, AccessLevel] = {}
    for case in cases:
        missing = [pid for pid in case.gold_provision_ids if pid not in levels]
        if missing:
            problems.append(f"{case.case_id}: gold not in release {missing}")
            continue
        case_levels = [levels[pid] for pid in case.gold_provision_ids]
        if case.expected_by_role != expected_decisions(case_levels):
            problems.append(f"{case.case_id}: expected_by_role disagrees with gold access levels")
        if case_levels:
            top[case.case_id] = max(case_levels, key=LEVEL_ORDER.index)
    if problems:
        raise ValueError("; ".join(problems))
    return top


def summarize(rows: Sequence[tuple[list[str], set[str], float]], top_k: int) -> RetrievalMetrics:
    """Macro means over cases, same definitions as the qa_dev retrieval report."""
    count = len(rows)
    times = sorted(row[2] for row in rows)
    return RetrievalMetrics(
        recall_at_k={
            str(k): sum(recall(r, g, k) for r, g, _ in rows) / count if count else 0.0
            for k in (1, 5, 10)
            if k <= top_k
        },
        mrr=sum(reciprocal_rank(r, g) for r, g, _ in rows) / count if count else 0.0,
        mean_ms=sum(times) / count if count else 0.0,
        p95_ms=times[math.ceil(0.95 * count) - 1] if count else 0.0,
    )


async def evaluate_enterprise(
    service: SearchService,
    dataset: Path,
    levels: Mapping[str, AccessLevel],
    top_k: int = 10,
    access: AccessScope = FULL_ACCESS,
) -> EnterpriseRetrievalReport:
    """Hybrid metrics per category and per gold access level; Dense/BM25 kept in results.

    Retrieval quality is measured with full access by default; role leakage is a separate test.
    """
    if not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")
    cases = load_cases(dataset)
    top = gold_access(cases, levels)
    rows: list[tuple[EnterpriseEvalCase, list[str], set[str], float]] = []
    results: list[dict[str, object]] = []
    errors = 0
    for case in cases:
        if not case.gold_provision_ids:
            continue
        gold = set(case.gold_provision_ids)
        try:
            run = await service.diagnose(
                SearchRequest(query=case.question, top_k=top_k), uuid4(), access
            )
        except SearchError as exc:
            errors += 1
            results.append({"case_id": case.case_id, "error_code": exc.code})
            continue
        ranked = {
            "dense": unique_provisions([str(c.point_uuid) for c in run.dense[:top_k]], service),
            "bm25": unique_provisions([str(c.point_uuid) for c in run.bm25[:top_k]], service),
            "hybrid": unique_provisions([str(h.point_uuid) for h in run.response.results], service),
        }
        rows.append((case, ranked["hybrid"], gold, run.response.elapsed_ms))
        results.append(
            {
                "case_id": case.case_id,
                "category": case.category.value,
                "gold_access": top[case.case_id].value,
                "gold_provision_ids": sorted(gold),
                "hybrid_rank": next(
                    (i for i, pid in enumerate(ranked["hybrid"], 1) if pid in gold), None
                ),
                "ranked_provisions": ranked,
            }
        )

    by_category: dict[str, list[tuple[list[str], set[str], float]]] = {}
    by_level: dict[str, list[tuple[list[str], set[str], float]]] = {}
    for item, ranked_hybrid, gold_ids, elapsed in rows:
        row = (ranked_hybrid, gold_ids, elapsed)
        by_category.setdefault(item.category.value, []).append(row)
        by_level.setdefault(top[item.case_id].value, []).append(row)
    with_gold = sum(bool(case.gold_provision_ids) for case in cases)
    return EnterpriseRetrievalReport(
        dataset=dataset.name,
        collection=str(service.provenance.get("collection", "")),
        release_fingerprint=service.release_fingerprint,
        query_top_k=top_k,
        cases_total=len(cases),
        cases_with_gold=with_gold,
        errors=errors,
        complete=errors == 0 and len(rows) == with_gold,
        by_category={name: summarize(by_category[name], top_k) for name in sorted(by_category)},
        by_access_level={
            lvl.value: summarize(by_level[lvl.value], top_k)
            for lvl in LEVEL_ORDER
            if lvl.value in by_level
        },
        overall=summarize([(r, g, ms) for _, r, g, ms in rows], top_k),
        parameters={
            **service.provenance,
            **service.runtime.model_dump(),
            "mode": "hybrid metrics; dense/bm25 rankings kept per case",
            "access_scope": sorted(level.value for level in access),
        },
        results=results,
    )


async def evaluate_leakage(
    service: SearchService,
    dataset: Path,
    levels: Mapping[str, AccessLevel],
    top_k: int = 10,
) -> dict[str, object]:
    """Run every case as every role; any returned chunk above the role's scope is a leak."""
    cases = load_cases(dataset)
    gold_access(cases, levels)
    roles: dict[str, object] = {}
    leaks_total = 0
    for role in ROLES:
        scope = ROLE_ACCESS[role]
        hits = leaks = errors = 0
        leaked_cases: list[str] = []
        visible_gold = found_visible = 0
        for case in cases:
            try:
                response = await service.search(
                    SearchRequest(query=case.question, top_k=top_k), uuid4(), scope
                )
            except SearchError:
                errors += 1
                continue
            returned = [service.catalog.chunks[str(h.point_uuid)] for h in response.results]
            hits += len(returned)
            bad = [c for c in returned if (c.access_level or AccessLevel.PUBLIC) not in scope]
            if bad:
                leaks += len(bad)
                leaked_cases.append(case.case_id)
            allowed_gold = {p for p in case.gold_provision_ids if levels[p] in scope}
            if allowed_gold:
                visible_gold += 1
                found_visible += bool(allowed_gold & {c.provision_id for c in returned})
        leaks_total += leaks
        roles[role] = {
            "scope": sorted(level.value for level in scope),
            "queries": len(cases) - errors,
            "errors": errors,
            "hits": hits,
            "out_of_scope_hits": leaks,
            "leaked_cases": leaked_cases,
            "cases_with_visible_gold": visible_gold,
            "visible_gold_hit_at_k": found_visible / visible_gold if visible_gold else None,
        }
    return {
        "dataset": dataset.name,
        "collection": str(service.provenance.get("collection", "")),
        "release_fingerprint": service.release_fingerprint,
        "query_top_k": top_k,
        "out_of_scope_hits_total": leaks_total,
        "passed": leaks_total == 0,
        "roles": roles,
    }
