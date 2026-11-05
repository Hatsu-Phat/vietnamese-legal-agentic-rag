"""Deterministic ranking and Reciprocal Rank Fusion over canonical UUIDs."""

from collections.abc import Sequence
from uuid import UUID

from src.core.search import Branch, FusedCandidate, RetrievalCandidate


def rank_candidates(candidates: Sequence[RetrievalCandidate]) -> list[RetrievalCandidate]:
    """Deduplicate before ranking; same-score ties use UUID, without rounding."""
    best: dict[UUID, RetrievalCandidate] = {}
    for candidate in candidates:
        if candidate.point_uuid not in best or candidate.score > best[candidate.point_uuid].score:
            best[candidate.point_uuid] = candidate
    return sorted(best.values(), key=lambda c: (-c.score, str(c.point_uuid)))


def fuse_rrf(
    dense: Sequence[RetrievalCandidate], bm25: Sequence[RetrievalCandidate], rrf_k: int = 60
) -> list[FusedCandidate]:
    """Each unique branch rank contributes once; zero-result branches are valid."""
    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")
    scores: dict[UUID, float] = {}
    ranks: dict[UUID, int] = {}
    sources: dict[UUID, list[Branch]] = {}
    branches: tuple[tuple[Branch, Sequence[RetrievalCandidate]], ...] = (
        ("dense", dense),
        ("bm25", bm25),
    )
    for branch, candidates in branches:
        for rank, candidate in enumerate(rank_candidates(candidates), 1):
            uid = candidate.point_uuid
            scores[uid] = scores.get(uid, 0) + 1 / (rrf_k + rank)
            ranks[uid] = min(ranks.get(uid, rank), rank)
            sources.setdefault(uid, []).append(branch)
    ordered = sorted(scores, key=lambda uid: (-scores[uid], ranks[uid], str(uid)))
    return [
        FusedCandidate(
            point_uuid=uid, score=scores[uid], best_rank=ranks[uid], matched_by=sources[uid]
        )
        for uid in ordered
    ]
