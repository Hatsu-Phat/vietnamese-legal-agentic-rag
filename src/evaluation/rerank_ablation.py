"""Paired dev ablation: Hybrid RRF vs cross-encoder reranking over the same candidates."""

import math
import time
from collections.abc import Callable, Sequence
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
from src.evaluation.retrieval import recall, reciprocal_rank
from src.retrieval.reranker import rerank
from src.retrieval.search_service import SearchService

ScoreFn = Callable[[str, Sequence[str]], list[float]]
CUTOFF = 10


def provisions(uids: Sequence[str], service: SearchService) -> list[str]:
    """Unique provisions in rank order, cut at the evaluated depth."""
    return list(dict.fromkeys(service.catalog.chunks[u].provision_id for u in uids))[:CUTOFF]


def aggregate(rows: list[tuple[list[str], set[str], float]]) -> RetrievalMetrics:
    times = sorted(r[2] for r in rows)
    n = len(rows)
    return RetrievalMetrics(
        recall_at_k={
            str(k): sum(recall(r, g, k) for r, g, _ in rows) / n if n else 0 for k in (1, 5, 10)
        },
        mrr=sum(reciprocal_rank(r, g) for r, g, _ in rows) / n if n else 0,
        mean_ms=sum(times) / n if n else 0,
        p95_ms=times[math.ceil(0.95 * n) - 1] if n else 0,
    )


async def evaluate_rerank(
    service: SearchService,
    score: ScoreFn,
    dataset: Path,
    pool: int = 20,
    limit: int | None = None,
) -> RetrievalEvaluationReport:
    """One search per question with a `pool`-sized Hybrid list; all configs share it.

    hybrid@10: first 10 fused chunks (no reranking).
    rerank_top10: rerank those same 10 (isolates the reranker from pool size).
    rerank_pool: rerank all `pool` chunks and keep 10 (adds recall from deeper candidates).
    Latency of reranked configs = Hybrid search time + reranking time.
    """
    if not CUTOFF <= pool <= 20:
        raise ValueError("pool must be between 10 and 20 (SearchRequest top_k limit)")
    items = [
        QAEvaluationItem.model_validate_json(line)
        for line in dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ][:limit]
    modes = ("hybrid@10", "rerank_top10", f"rerank_pool{pool}")
    rows: dict[str, list[tuple[list[str], set[str], float]]] = {m: [] for m in modes}
    results: list[QuestionEvaluation] = []
    errors = skipped = 0
    for item in items:
        gold = set(item.gold_provision_ids)
        if not gold:
            skipped += 1
            continue
        try:
            response = await service.search(
                SearchRequest(query=item.question_text, top_k=pool), uuid4()
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
            continue
        uids = [str(h.point_uuid) for h in response.results]
        texts = [service.catalog.chunks[u].embedding_text for u in uids]
        # Pair scores are independent, so the top-10 scores are reused inside the pool.
        started = time.perf_counter()
        top10_scores = score(item.question_text, texts[:CUTOFF])
        top10_ms = (time.perf_counter() - started) * 1000
        scores = top10_scores + score(item.question_text, texts[CUTOFF:])
        pool_ms = (time.perf_counter() - started) * 1000
        ranked = {
            modes[0]: provisions(uids[:CUTOFF], service),
            modes[1]: provisions(rerank(uids[:CUTOFF], top10_scores), service),
            modes[2]: provisions(rerank(uids, scores), service),
        }
        latency = {
            modes[0]: response.elapsed_ms,
            modes[1]: response.elapsed_ms + top10_ms,
            modes[2]: response.elapsed_ms + pool_ms,
        }
        for mode in modes:
            rows[mode].append((ranked[mode], gold, latency[mode]))
        results.append(
            QuestionEvaluation(
                question_id=item.question_id,
                gold_provision_ids=sorted(gold),
                ranked_provisions=ranked,
            )
        )
    return RetrievalEvaluationReport(
        split="dev",
        release_fingerprint=service.release_fingerprint,
        query_top_k=CUTOFF,
        questions_total=len(items),
        questions_evaluated=len(rows[modes[0]]),
        errors=errors,
        skipped_without_gold=skipped,
        metrics={m: aggregate(rows[m]) for m in modes},
        parameters={
            **service.provenance,
            "candidate_pool": pool,
            "reranker_input": "LegalChunk.embedding_text (breadcrumb + title + text)",
            "note": "hybrid@10 uses a pool-sized branch fetch, so it can differ slightly from production search_dev.json",
        },
        results=results,
        complete=errors == 0 and skipped == 0 and bool(items),
    )
