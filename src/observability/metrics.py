"""Minimal Prometheus-text metrics (P5/T-0503); no extra dependency.

Labels carry only bounded values (route templates, node names, providers, decisions), never
questions, evidence, credentials or request IDs.
"""

import bisect
import functools
import inspect
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

LabelValues = tuple[str, ...]
DEFAULT_BUCKETS: tuple[float, ...] = (0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(names: tuple[str, ...], values: LabelValues, extra: str = "") -> str:
    parts = [f'{n}="{_escape(v)}"' for n, v in zip(names, values, strict=True)]
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}" if parts else ""


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


class Counter:
    def __init__(self, name: str, doc: str, labels: tuple[str, ...] = ()) -> None:
        self.name, self.doc, self.label_names = name, doc, labels
        self._values: dict[LabelValues, float] = {}
        self._lock = threading.Lock()

    def inc(self, *labels: str, amount: float = 1.0) -> None:
        if len(labels) != len(self.label_names):
            raise ValueError(f"{self.name} expects labels {self.label_names}")
        with self._lock:
            self._values[labels] = self._values.get(labels, 0.0) + amount

    def value(self, *labels: str) -> float:
        return self._values.get(labels, 0.0)

    def reset(self) -> None:
        with self._lock:
            self._values.clear()

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.doc}"
        yield f"# TYPE {self.name} counter"
        for labels, value in sorted(self._values.items()):
            yield f"{self.name}{_labels(self.label_names, labels)} {_number(value)}"


class Histogram:
    def __init__(
        self,
        name: str,
        doc: str,
        labels: tuple[str, ...] = (),
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> None:
        self.name, self.doc, self.label_names = name, doc, labels
        self.buckets = tuple(sorted(buckets))
        self._series: dict[LabelValues, tuple[list[int], list[float]]] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, *labels: str) -> None:
        if len(labels) != len(self.label_names):
            raise ValueError(f"{self.name} expects labels {self.label_names}")
        with self._lock:
            counts, totals = self._series.setdefault(labels, ([0] * len(self.buckets), [0.0, 0.0]))
            index = bisect.bisect_left(self.buckets, value)
            if index < len(counts):
                counts[index] += 1
            totals[0] += 1
            totals[1] += value

    def count(self, *labels: str) -> int:
        series = self._series.get(labels)
        return int(series[1][0]) if series else 0

    def reset(self) -> None:
        with self._lock:
            self._series.clear()

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.doc}"
        yield f"# TYPE {self.name} histogram"
        for labels, (counts, (count, total)) in sorted(self._series.items()):
            cumulative = 0
            for bound, n in zip(self.buckets, counts, strict=True):
                cumulative += n
                le = f'le="{_number(bound)}"'
                yield f"{self.name}_bucket{_labels(self.label_names, labels, le)} {cumulative}"
            inf = _labels(self.label_names, labels, 'le="+Inf"')
            yield f"{self.name}_bucket{inf} {int(count)}"
            yield f"{self.name}_sum{_labels(self.label_names, labels)} {_number(total)}"
            yield f"{self.name}_count{_labels(self.label_names, labels)} {int(count)}"


class Gauge:
    """Read at scrape time from a callback (e.g. cache counters owned by a service)."""

    def __init__(self, name: str, doc: str, read: Callable[[], float] | None = None) -> None:
        self.name, self.doc, self.read = name, doc, read

    def reset(self) -> None:
        self.read = None

    def render(self) -> Iterable[str]:
        if self.read is None:
            return
        yield f"# HELP {self.name} {self.doc}"
        yield f"# TYPE {self.name} gauge"
        yield f"{self.name} {_number(self.read())}"


class Metrics:
    """Process-wide registry; one API/MCP process exposes one set of series."""

    def __init__(self) -> None:
        self.requests = Counter(
            "legal_rag_http_requests_total",
            "HTTP requests by route and status",
            ("route", "status"),
        )
        self.request_latency = Histogram(
            "legal_rag_http_request_seconds", "HTTP request latency by route", ("route",)
        )
        self.node_latency = Histogram(
            "legal_rag_ask_node_seconds", "LangGraph node latency", ("node",)
        )
        self.llm_calls = Counter(
            "legal_rag_llm_calls_total",
            "LLM calls by route (local/cloud), provider class and outcome",
            ("route", "provider", "outcome"),
        )
        self.ask_decisions = Counter(
            "legal_rag_ask_decisions_total",
            "/ask responses by decision and whether they came from the cache",
            ("decision", "cached"),
        )
        self.mcp_calls = Counter(
            "legal_rag_mcp_tool_calls_total",
            "MCP tool calls by tool and outcome",
            ("tool", "outcome"),
        )
        self.cache_hits = Gauge("legal_rag_ask_cache_hits_total", "/ask cache hits")
        self.cache_misses = Gauge("legal_rag_ask_cache_misses_total", "/ask cache misses")
        self.cache_bypassed = Gauge(
            "legal_rag_ask_cache_bypassed_total", "/ask requests not cached (PII or disabled)"
        )
        self.cache_entries = Gauge("legal_rag_ask_cache_entries", "/ask cache entries")

    @property
    def _all(self) -> tuple[Counter | Histogram | Gauge, ...]:
        return (
            self.requests,
            self.request_latency,
            self.node_latency,
            self.llm_calls,
            self.ask_decisions,
            self.mcp_calls,
            self.cache_hits,
            self.cache_misses,
            self.cache_bypassed,
            self.cache_entries,
        )

    def reset(self) -> None:
        for metric in self._all:
            metric.reset()

    def render(self) -> str:
        return "\n".join(line for metric in self._all for line in metric.render()) + "\n"


METRICS = Metrics()


def timed_node[F: Callable[..., Any]](name: str, fn: F) -> F:
    """Wrap a graph node; keeps its signature so LangGraph still injects `config`."""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return await fn(*args, **kwargs)
            finally:
                METRICS.node_latency.observe(time.perf_counter() - started, name)

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            METRICS.node_latency.observe(time.perf_counter() - started, name)

    return sync_wrapper  # type: ignore[return-value]
