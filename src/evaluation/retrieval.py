"""Matched Dense/BM25/Hybrid evaluation using one encoding/retrieval pass per query."""

import math
from pathlib import Path
from uuid import uuid4

from src.common.search_errors import SearchError
from src.core.models import QAEvaluationItem
from src.core.retrieval_evaluation import (
    QuestionEvaluation,
    RetrievalEvaluationReport,
    RetrievalMetrics,
)
from src.core.search import SearchRequest
from src.retrieval.search_service import SearchService


def unique_provisions(chunk_ids: list[str], service: SearchService) -> list[str]:
    """Preserve the first occurrence; never count chunks as separate gold answers."""
    return list(dict.fromkeys(service.catalog.chunks[uid].provision_id for uid in chunk_ids))


def recall(ranked: list[str], gold: set[str], k: int) -> float:
    """Recall over unique gold references, including multi-provision questions."""
    return len(set(ranked[:k]) & gold) / len(gold) if gold else 0.0


def reciprocal_rank(ranked: list[str], gold: set[str]) -> float:
    """Reciprocal position of the first relevant unique provision."""
    return next((1 / rank for rank, pid in enumerate(ranked, 1) if pid in gold), 0.0)


async def evaluate_retrieval(
    service: SearchService,
    dataset: Path,
    split: str = "dev",
    top_k: int = 10,
    limit: int | None = None,
) -> RetrievalEvaluationReport:
    """Evaluate frozen queries with production candidate limits; no parameter tuning."""
    if split != "dev":
        raise ValueError("This development runner accepts dev only; keep test reserved")
    if not 1 <= top_k <= 20 or (limit is not None and limit <= 0):
        raise ValueError("Invalid evaluation limits")
    items = [
        QAEvaluationItem.model_validate_json(line)
        for line in dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if limit is not None:
        items = items[:limit]
    results: list[QuestionEvaluation] = []
    rows: dict[str, list[tuple[list[str], set[str], float]]] = {
        mode: [] for mode in ("dense", "bm25", "hybrid")
    }
    skipped = errors = 0
    for item in items:
        gold = set(item.gold_provision_ids)
        if not gold:
            skipped += 1
            continue
        try:
            run = await service.diagnose(
                SearchRequest(query=item.question_text, top_k=top_k), uuid4()
            )
            ranked = {
                "dense": unique_provisions([str(c.point_uuid) for c in run.dense[:top_k]], service),
                "bm25": unique_provisions([str(c.point_uuid) for c in run.bm25[:top_k]], service),
                "hybrid": unique_provisions(
                    [str(h.point_uuid) for h in run.response.results], service
                ),
            }
            latencies = {
                "dense": run.dense_ms,
                "bm25": run.bm25_ms,
                "hybrid": run.response.elapsed_ms,
            }
            for mode in rows:
                rows[mode].append((ranked[mode], gold, latencies[mode]))
            results.append(
                QuestionEvaluation(
                    question_id=item.question_id,
                    gold_provision_ids=sorted(gold),
                    ranked_provisions=ranked,
                )
            )
        except SearchError as exc:
            errors += 1
            results.append(
                QuestionEvaluation(
                    question_id=item.question_id,
                    gold_provision_ids=sorted(gold),
                    ranked_provisions={},
                    error_code=exc.code,
                )
            )
    metrics: dict[str, RetrievalMetrics] = {}
    for mode, values in rows.items():
        times = sorted(row[2] for row in values)
        count = len(values)
        metrics[mode] = RetrievalMetrics(
            recall_at_k={
                str(k): sum(recall(r, g, k) for r, g, _ in values) / count if count else 0
                for k in (1, 5, 10)
                if k <= top_k
            },
            mrr=sum(reciprocal_rank(r, g) for r, g, _ in values) / count if count else 0,
            mean_ms=sum(times) / count if count else 0,
            p95_ms=times[math.ceil(0.95 * count) - 1] if count else 0,
        )
    return RetrievalEvaluationReport(
        split=split,
        release_fingerprint=service.release_fingerprint,
        query_top_k=top_k,
        questions_total=len(items),
        questions_evaluated=len(rows["hybrid"]),
        errors=errors,
        skipped_without_gold=skipped,
        metrics=metrics,
        results=results,
        complete=errors == 0 and skipped == 0 and bool(items),
        parameters={
            **service.provenance,
            **service.runtime.model_dump(),
            "provision_ranking": "Deduplicate only the returned top-k chunks before scoring; no extra candidates after the API cutoff",
            "latency": "Dense includes encoding and database query; BM25 is lexical retrieval; Hybrid includes queue, both branches, hydration and fusion",
        },
    )
