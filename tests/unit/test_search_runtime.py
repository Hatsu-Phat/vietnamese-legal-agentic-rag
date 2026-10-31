"""Search contracts, ranking, real adapters and bounded asynchronous execution."""

import asyncio
from uuid import UUID, uuid4

import pytest

from src.common.logger import logger
from src.common.search_errors import InvalidSearchRequest, SearchTimeout, SearchUnavailable
from src.core.search import (
    RetrievalCandidate,
    SearchFilters,
    SearchRequest,
)
from src.retrieval.search_catalog import ChunkCatalog
from tests.search_support import (
    Dense,
    Encoder,
    Sparse,
    service_for,
)
from tests.search_support import (
    catalog as catalog,
)


@pytest.mark.asyncio
async def test_top20_hydration_warnings_and_empty_branches(catalog: ChunkCatalog) -> None:
    service = service_for(catalog)
    try:
        identifier = uuid4()
        response = await service.search(SearchRequest(query="môi trường", top_k=20), identifier)
        assert response.request_id == identifier and response.returned_count == 20
        assert [h.rank for h in response.results] == list(range(1, 21))
        assert all(
            h.text and h.breadcrumb and h.matched_by == ["dense", "bm25"] for h in response.results
        )
        assert {w.code for w in response.warnings} == {
            "VALIDITY_UNVERIFIED",
            "SOURCE_URL_UNAVAILABLE",
        }
        dense = service.dense
        assert isinstance(dense, Dense) and dense.limit >= 20
        sparse = service.sparse
        assert isinstance(sparse, Sparse)
        sparse.empty = True
        response = await service.search(SearchRequest(query="môi trường", top_k=5), uuid4())
        assert response.returned_count == 5 and all(
            h.matched_by == ["dense"] for h in response.results
        )
        response = await service.search(
            SearchRequest(query="x", filters=SearchFilters(issued_year=1900)), uuid4()
        )
        assert response.results == [] and response.warnings == []
        dense.fixed = []
        sparse.empty = False
        response = await service.search(SearchRequest(query="x"), uuid4())
        assert response.returned_count == 0
        # BM25-only candidates still hydrate the complete canonical text.
        sparse.dense = Dense(catalog)
        response = await service.search(SearchRequest(query="x"), uuid4())
        assert response.results and all(
            h.text and h.matched_by == ["bm25"] for h in response.results
        )
    finally:
        await service.aclose()
    assert not service.ready
    with pytest.raises(SearchUnavailable):
        await service.search(SearchRequest(query="x"), uuid4())


@pytest.mark.asyncio
async def test_invalid_tokens_failure_and_safe_logging(catalog: ChunkCatalog) -> None:
    service = service_for(catalog)
    logs: list[str] = []
    sink = logger.add(lambda message: logs.append(str(message)))
    try:
        encoder = service.encoder
        assert isinstance(encoder, Encoder)
        encoder.tokens = 513
        with pytest.raises(InvalidSearchRequest):
            await service.search(SearchRequest(query="PRIVATE_SENTINEL"), uuid4())
        assert encoder.calls == 0
        encoder.tokens = 5
        sparse = service.sparse
        assert isinstance(sparse, Sparse)
        sparse.failure = RuntimeError("LEAK_SECRET")
        with pytest.raises(SearchUnavailable) as error:
            await service.search(SearchRequest(query="PRIVATE_SENTINEL"), uuid4())
        assert error.value.status_code == 503
        assert "LEAK_SECRET" not in str(error.value)
        sparse.failure = TimeoutError("LEAK_SECRET")
        with pytest.raises(SearchTimeout) as timeout:
            await service.search(SearchRequest(query="PRIVATE_SENTINEL"), uuid4())
        assert timeout.value.status_code == 504
        bad = SearchRequest.model_construct(query=" ", top_k=10, filters=SearchFilters())
        with pytest.raises(InvalidSearchRequest):
            await service.search(bad, uuid4())
    finally:
        logger.remove(sink)
        await service.aclose()
    assert "PRIVATE_SENTINEL" not in "".join(logs) and "LEAK_SECRET" not in "".join(logs)


@pytest.mark.asyncio
async def test_unknown_id_and_filter_leak_fail_closed(catalog: ChunkCatalog) -> None:
    service = service_for(catalog)
    dense = service.dense
    assert isinstance(dense, Dense)
    try:
        dense.fixed = [RetrievalCandidate(point_uuid=uuid4(), score=1)]
        with pytest.raises(SearchUnavailable):
            await service.search(SearchRequest(query="x"), uuid4())
        dense.fixed = [RetrievalCandidate(point_uuid=UUID(next(iter(catalog.chunks))), score=1)]
        with pytest.raises(SearchUnavailable):
            await service.search(
                SearchRequest(query="x", filters=SearchFilters(issued_year=2021)), uuid4()
            )
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_timeouts_keep_worker_bounded_and_event_loop_free(catalog: ChunkCatalog) -> None:
    service = service_for(catalog, 0.02)
    dense = service.dense
    assert isinstance(dense, Dense)
    dense.delay = 0.10
    try:
        job = asyncio.create_task(service.search(SearchRequest(query="x"), uuid4()))
        await asyncio.sleep(0.005)
        assert not job.done()  # event loop runs while native work sleeps
        with pytest.raises(SearchTimeout):
            await job
        with pytest.raises(SearchTimeout):
            await service.search(SearchRequest(query="x"), uuid4())
        assert dense.calls == 1 and dense.maximum_active == 1
    finally:
        await service.aclose()
    assert dense.closed
