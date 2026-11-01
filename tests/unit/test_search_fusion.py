"""Search contracts, ranking, real adapters and bounded asynchronous execution."""

from uuid import UUID

import pytest

from src.core.search import (
    RetrievalCandidate,
)
from src.retrieval.fusion import fuse_rrf, rank_candidates
from tests.search_support import (
    catalog as catalog,
)


def test_rrf_deduplicates_before_ranks_and_tie_breaks() -> None:
    a, b = UUID(int=1), UUID(int=2)
    dense = [
        RetrievalCandidate(point_uuid=b, score=0.8),
        RetrievalCandidate(point_uuid=a, score=0.8),
        RetrievalCandidate(point_uuid=a, score=0.5),
    ]
    assert [r.point_uuid for r in rank_candidates(dense)] == [a, b]
    fused = fuse_rrf(dense, [RetrievalCandidate(point_uuid=b, score=100)])
    assert [f.point_uuid for f in fused] == [b, a]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[0].matched_by == ["dense", "bm25"]
    assert fused[1].matched_by == ["dense"]
    assert fuse_rrf([], []) == []
    with pytest.raises(ValueError):
        fuse_rrf([], [], 0)
