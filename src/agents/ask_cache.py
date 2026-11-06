"""In-process answer cache for /ask (P5/T-0502), isolated by access scope and release.

The key covers everything the answer depends on: the normalized question and request
options, the caller's access scope (results are a function of the scope, so two roles never
share an entry unless they see exactly the same documents), the search release fingerprint
and the LLM pipeline signature (models, prompt variants, routing mode). Questions with
personal identifiers are never cached, and errors are never cached (AgentError propagates).
"""

import hashlib
import json
import re
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from loguru import logger

from src.core.access import PUBLIC_ONLY, AccessScope
from src.core.agent import AskRequest, AskResponse
from src.core.api import AskPort
from src.guardrails.pii import detect_pii
from src.observability.metrics import METRICS

CACHE_HIT_TRACE = "cache_hit"
_SPACES = re.compile(r"\s+")


def normalize_question(question: str) -> str:
    """NFC, case-folded, single spaces: trivially different spellings share one entry."""
    return _SPACES.sub(" ", unicodedata.normalize("NFC", question)).strip().casefold()


def pipeline_signature(parts: Mapping[str, object]) -> str:
    """Stable digest of the settings that change answers (models, prompts, routing)."""
    blob = json.dumps(dict(parts), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def ask_cache_key(
    request: AskRequest, access: AccessScope, release_fingerprint: str, pipeline: str
) -> str:
    payload = {
        "question": normalize_question(request.question),
        "as_of_date": request.as_of_date.isoformat() if request.as_of_date else None,
        "top_k": request.top_k,
        "filters": request.filters.model_dump(mode="json"),
        "access": sorted(str(level) for level in access),
        "release": release_fingerprint,
        "pipeline": pipeline,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    bypassed: int = 0
    evictions: int = 0


@dataclass(frozen=True)
class _Entry:
    response: AskResponse
    expires_at: float


class CachedAskService:
    """AskPort decorator; `ttl_seconds == 0` turns caching off but keeps the counters."""

    def __init__(
        self,
        inner: AskPort,
        release_fingerprint: str,
        pipeline: str,
        ttl_seconds: float = 3600.0,
        max_entries: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds < 0 or max_entries < 1:
            raise ValueError("ttl_seconds must be >= 0 and max_entries >= 1")
        self._inner = inner
        self._release = release_fingerprint
        self._pipeline = pipeline
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self.stats = CacheStats()

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    def __len__(self) -> int:
        return len(self._entries)

    async def ask(
        self, request: AskRequest, request_id: UUID, access: AccessScope = PUBLIC_ONLY
    ) -> AskResponse:
        started = time.perf_counter()
        # PII is checked on the raw question: masked text must not become a shared key.
        if not self.enabled or detect_pii(request.question):
            self.stats.bypassed += 1
            return await self._inner.ask(request, request_id, access)
        key = ask_cache_key(request, access, self._release, self._pipeline)
        cached = self._lookup(key)
        if cached is not None:
            self.stats.hits += 1
            elapsed = (time.perf_counter() - started) * 1000
            METRICS.ask_decisions.inc(cached.decision, "true")
            logger.info("Ask cache hit id={} elapsed_ms={:.1f}", request_id, elapsed)
            return cached.model_copy(
                update={
                    "request_id": request_id,
                    "elapsed_ms": elapsed,
                    "trace": [*cached.trace, CACHE_HIT_TRACE],
                }
            )
        self.stats.misses += 1
        response = await self._inner.ask(request, request_id, access)
        self._store(key, response)
        return response

    def publish_metrics(self) -> None:
        """Expose this cache's counters on /metrics (read at scrape time)."""
        METRICS.cache_hits.read = lambda: self.stats.hits
        METRICS.cache_misses.read = lambda: self.stats.misses
        METRICS.cache_bypassed.read = lambda: self.stats.bypassed
        METRICS.cache_entries.read = lambda: len(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def _lookup(self, key: str) -> AskResponse | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return entry.response

    def _store(self, key: str, response: AskResponse) -> None:
        self._entries[key] = _Entry(response, self._clock() + self._ttl)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)
            self.stats.evictions += 1
