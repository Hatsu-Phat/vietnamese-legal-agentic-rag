"""Read-only Dense/BM25 adapters; no model loading or indexing inside requests."""

import heapq
import math
import time
from typing import Any
from uuid import UUID

import httpx
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from qdrant_client.models import Condition, FieldCondition, Filter, MatchValue

from src.common.fingerprints import payload_fingerprint
from src.common.search_errors import SearchTimeout, SearchUnavailable
from src.core.search import RetrievalCandidate, SearchFilters
from src.retrieval.bm25_indexer import BM25Indexer
from src.retrieval.fusion import rank_candidates
from src.retrieval.search_catalog import ChunkCatalog, matches


class PinnedQueryEncoder:
    """Reuse a warmed model and its tokenizer; never silently truncate a query."""

    def __init__(self, model: Any) -> None:
        self.model = model

    def token_count(self, query: str) -> int:
        return len(
            self.model.tokenizer(
                query,
                add_special_tokens=True,
                truncation=False,
                return_attention_mask=False,
                verbose=False,
            )["input_ids"]
        )

    def encode_query(self, query: str) -> list[float]:
        vector = self.model.encode(
            query, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
        )
        return [float(x) for x in vector.tolist()]


class QdrantDenseSearch:
    """Query one already-verified collection and compare payloads with the catalog."""

    def __init__(
        self,
        client: QdrantClient,
        collection: str,
        catalog: ChunkCatalog,
        timeout_seconds: int = 15,
    ) -> None:
        self.client = client
        self.collection = collection
        self.catalog = catalog
        self.timeout_seconds = timeout_seconds

    def retrieve(
        self, vector: list[float], filters: SearchFilters, limit: int, deadline: float
    ) -> list[RetrievalCandidate]:
        remaining = int(deadline - time.monotonic())
        if remaining < 1:
            raise SearchTimeout()
        conditions: list[Condition] = [
            FieldCondition(key=key, match=MatchValue(value=value))
            for key, value in filters.model_dump(exclude_none=True).items()
        ]
        try:
            response = self.client.query_points(
                self.collection,
                query=vector,
                query_filter=Filter(must=conditions) if conditions else None,
                limit=limit,
                with_payload=True,
                with_vectors=False,
                timeout=min(self.timeout_seconds, remaining),
            )
        except (TimeoutError, httpx.TimeoutException):
            raise SearchTimeout() from None
        except ResponseHandlingException as exc:
            if isinstance(exc.source, (TimeoutError, httpx.TimeoutException)):
                raise SearchTimeout() from None
            raise SearchUnavailable() from None
        except UnexpectedResponse as exc:
            if exc.status_code in {408, 504}:
                raise SearchTimeout() from None
            raise SearchUnavailable() from None
        result: list[RetrievalCandidate] = []
        for row in response.points:
            uid = str(row.id)
            if self.catalog.payload_hashes.get(uid) != payload_fingerprint(row.payload or {}):
                raise SearchUnavailable()
            if not matches(self.catalog.chunks[uid], filters):
                raise SearchUnavailable()
            result.append(RetrievalCandidate(point_uuid=UUID(uid), score=row.score))
        return rank_candidates(result)[:limit]

    def close(self) -> None:
        self.client.close()


class BM25SparseSearch:
    """Filter on canonical metadata before taking the highest-scoring eligible IDs."""

    def __init__(self, indexer: BM25Indexer, catalog: ChunkCatalog) -> None:
        self.indexer = indexer
        self.catalog = catalog
        self.ids = [str(row["point_uuid"]) for row in indexer.corpus_meta]
        if len(set(self.ids)) != len(self.ids) or set(self.ids) != set(catalog.chunks):
            raise ValueError("BM25 and canonical UUID sets differ")
        if indexer.bm25 is None or indexer.bm25.corpus_size != len(self.ids):
            raise ValueError("BM25 corpus size differs from metadata")

    def retrieve(self, query: str, filters: SearchFilters, limit: int) -> list[RetrievalCandidate]:
        tokens = self.indexer.tokenize(query)
        if not tokens:
            return []
        if self.indexer.bm25 is None:
            raise SearchUnavailable()
        scores = self.indexer.bm25.get_scores(tokens)
        eligible: list[tuple[float, str]] = []
        for uid, raw in zip(self.ids, scores, strict=True):
            score = float(raw)
            if not math.isfinite(score):
                raise SearchUnavailable()
            if score > 0 and matches(self.catalog.chunks[uid], filters):
                eligible.append((-score, uid))
        return [
            RetrievalCandidate(point_uuid=UUID(uid), score=-negative)
            for negative, uid in heapq.nsmallest(limit, eligible)
        ]
