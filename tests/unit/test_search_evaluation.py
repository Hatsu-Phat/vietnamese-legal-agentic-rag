"""Search contracts, ranking, real adapters and bounded asynchronous execution."""

import json
from pathlib import Path

import pytest

from src.evaluation.retrieval import evaluate_retrieval, recall, reciprocal_rank
from src.retrieval.search_catalog import ChunkCatalog
from tests.search_support import (
    Sparse,
    service_for,
)
from tests.search_support import (
    catalog as catalog,
)


def test_recall_and_mrr_grouped_gold() -> None:
    assert recall(["a", "b"], {"a", "c"}, 1) == 0.5
    assert reciprocal_rank(["x", "b", "a"], {"a", "c"}) == pytest.approx(1 / 3)
    assert recall([], {"a"}, 10) == 0


@pytest.mark.asyncio
async def test_evaluation_counts_errors_and_does_not_touch_test(
    tmp_path: Path, catalog: ChunkCatalog
) -> None:
    path = tmp_path / "dev.jsonl"
    path.write_text(
        json.dumps(
            {"question_id": "q1", "question_text": "x", "gold_provision_ids": ["law_demo__art_1"]}
        )
        + "\n",
        encoding="utf-8",
    )
    service = service_for(catalog)
    try:
        report = await evaluate_retrieval(service, path)
        assert report.complete and report.questions_evaluated == 1
        assert report.metrics["hybrid"].recall_at_k["10"] == 1
        with pytest.raises(ValueError, match="dev only"):
            await evaluate_retrieval(service, path, split="test")
        sparse = service.sparse
        assert isinstance(sparse, Sparse)
        sparse.failure = RuntimeError("failure")
        report = await evaluate_retrieval(service, path)
        assert not report.complete and report.errors == 1
    finally:
        await service.aclose()
