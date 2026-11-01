"""Asynchronous Search v1 service with a bounded worker and safe domain errors."""

import asyncio
import math
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import httpx
from pydantic import UUID4, TypeAdapter, ValidationError

from src.common.logger import logger
from src.common.search_errors import (
    InvalidSearchRequest,
    SearchError,
    SearchTimeout,
    SearchUnavailable,
)
from src.core.enums import LegalValidityStatus
from src.core.search import SearchHit, SearchRequest, SearchResponse, SearchRun, SearchWarning
from src.core.search_ports import DenseSearch, QueryEncoder, SparseSearch
from src.core.search_release import SearchRuntime
from src.retrieval.fusion import fuse_rrf, rank_candidates
from src.retrieval.search_catalog import ChunkCatalog, matches


class SearchService:
    """Initialize once, inject as SearchPort, close in the application lifespan."""

    def __init__(
        self,
        catalog: ChunkCatalog,
        encoder: QueryEncoder,
        dense: DenseSearch,
        sparse: SparseSearch,
        runtime: SearchRuntime | None = None,
        release_fingerprint: str = "injected-test-release",
        provenance: dict[str, object] | None = None,
    ) -> None:
        self.catalog = catalog
        self.encoder = encoder
        self.dense = dense
        self.sparse = sparse
        self.runtime = runtime or SearchRuntime()
        self.release_fingerprint = release_fingerprint
        self.provenance = provenance or {}
        self._ready = True
        self._slot = asyncio.Semaphore(1)
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="legal-search")

    @property
    def ready(self) -> bool:
        """Resources are available and not shutting down."""
        return self._ready

    async def search(self, request: SearchRequest, request_id: UUID) -> SearchResponse:
        """The only interface needed by the FastAPI route."""
        return (await self.diagnose(request, request_id)).response

    async def diagnose(self, request: SearchRequest, request_id: UUID) -> SearchRun:
        """Same pipeline with branch rankings for offline evaluation; no alternate API mode."""
        started = time.monotonic()
        try:
            validated = SearchRequest.model_validate(request.model_dump())
            identifier = TypeAdapter(UUID4).validate_python(request_id)
        except (ValidationError, AttributeError):
            raise InvalidSearchRequest() from None
        if not self.ready:
            raise SearchUnavailable()
        acquired = False
        submitted = False
        try:
            async with asyncio.timeout(self.runtime.timeout_seconds):
                await self._slot.acquire()
                acquired = True
                if not self.ready:
                    raise SearchUnavailable()
                future = asyncio.get_running_loop().run_in_executor(
                    self._worker,
                    self._run,
                    validated,
                    identifier,
                    started,
                    started + self.runtime.timeout_seconds,
                )
                submitted = True

                # Keep the slot until the real thread ends, even if its HTTP caller times out.
                # Cancelling to_thread/a Future does not stop native inference or a socket call.
                def finished(job: asyncio.Future[SearchRun]) -> None:
                    self._slot.release()
                    if not job.cancelled():
                        job.exception()

                future.add_done_callback(finished)
                result = await asyncio.shield(future)
                logger.info(
                    "Search completed id={} release={} hits={} elapsed_ms={:.1f}",
                    identifier,
                    self.release_fingerprint,
                    result.response.returned_count,
                    result.response.elapsed_ms,
                )
                return result
        except TimeoutError:
            logger.warning("Search failed id={} code=SEARCH_TIMEOUT", identifier)
            raise SearchTimeout() from None
        except asyncio.CancelledError:
            raise
        except SearchError as exc:
            logger.warning("Search failed id={} code={}", identifier, exc.code)
            raise
        except Exception:
            logger.error("Search failed id={} code=INTERNAL_ERROR", identifier)
            raise SearchError("INTERNAL_ERROR", "Không thể hoàn tất tìm kiếm.") from None
        finally:
            if acquired and not submitted:
                self._slot.release()

    def _run(
        self, request: SearchRequest, request_id: UUID, started: float, deadline: float
    ) -> SearchRun:
        """Run blocking model/database work outside the event loop."""
        try:
            if time.monotonic() >= deadline:
                raise SearchTimeout()
            if self.encoder.token_count(request.query) > self.runtime.max_query_tokens:
                raise InvalidSearchRequest()
            candidate_k = max(
                request.top_k,
                self.runtime.dense_candidates,
                self.runtime.sparse_candidates,
                self.runtime.fusion_candidates,
            )
            dense_started = time.monotonic()
            vector = self.encoder.encode_query(request.query)
            if len(vector) != self.runtime.vector_size or not all(math.isfinite(x) for x in vector):
                raise SearchUnavailable()
            if not math.isclose(sum(x * x for x in vector), 1, abs_tol=1e-3):
                raise SearchUnavailable()
            if time.monotonic() >= deadline:
                raise SearchTimeout()
            dense = rank_candidates(
                self.dense.retrieve(vector, request.filters, candidate_k, deadline)
            )[:candidate_k]
            dense_ms = (time.monotonic() - dense_started) * 1000
            if time.monotonic() >= deadline:
                raise SearchTimeout()
            sparse_started = time.monotonic()
            sparse = rank_candidates(
                self.sparse.retrieve(request.query, request.filters, candidate_k)
            )[:candidate_k]
            sparse_ms = (time.monotonic() - sparse_started) * 1000
            for candidate in [*dense, *sparse]:
                chunk = self.catalog.chunks.get(str(candidate.point_uuid))
                if chunk is None or not matches(chunk, request.filters):
                    raise SearchUnavailable()
            hits: list[SearchHit] = []
            for rank, fused in enumerate(
                fuse_rrf(dense, sparse, self.runtime.rrf_k)[: request.top_k], 1
            ):
                chunk = self.catalog.chunks[str(fused.point_uuid)]
                data = chunk.model_dump(
                    mode="json",
                    include={
                        "chunk_id",
                        "document_id",
                        "provision_id",
                        "article_id",
                        "document_number",
                        "title",
                        "text",
                        "breadcrumb",
                        "document_type",
                        "issued_year",
                        "status",
                    },
                )
                hits.append(
                    SearchHit(
                        **data,
                        point_uuid=fused.point_uuid,
                        source_url=None,
                        rank=rank,
                        score=fused.score,
                        matched_by=fused.matched_by,
                    )
                )
            warnings: list[SearchWarning] = []
            if any(h.status == LegalValidityStatus.UNKNOWN for h in hits):
                warnings.append(
                    SearchWarning(
                        code="VALIDITY_UNVERIFIED",
                        message="Có kết quả chưa xác minh hiệu lực pháp lý.",
                    )
                )
            if any(h.source_url is None for h in hits):
                warnings.append(
                    SearchWarning(
                        code="SOURCE_URL_UNAVAILABLE",
                        message="Có kết quả chưa có URL nguồn được kiểm chứng.",
                    )
                )
            if time.monotonic() >= deadline:
                raise SearchTimeout()
            response = SearchResponse(
                request_id=request_id,
                results=hits,
                returned_count=len(hits),
                elapsed_ms=(time.monotonic() - started) * 1000,
                warnings=warnings,
            )
            return SearchRun(
                response=response, dense=dense, bm25=sparse, dense_ms=dense_ms, bm25_ms=sparse_ms
            )
        except SearchError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise SearchTimeout() from None
        except Exception:
            raise SearchUnavailable() from None

    async def aclose(self) -> None:
        """Wait for native workers before closing the shared client."""
        if not self._ready:
            return
        self._ready = False
        await asyncio.to_thread(self._worker.shutdown, wait=True)
        await asyncio.to_thread(self.dense.close)
