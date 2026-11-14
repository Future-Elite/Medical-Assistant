"""Dependency-free metrics for the retrieval service.

Industrial RAG fails quietly in two places: a source gets slower without
erroring, and a verifier stops being called.  This module records real
observed durations and counters in-process and exposes them as JSON or
Prometheus text.  It is intentionally small: no background thread, no external
collector, and no synthetic values.

Usage::

    from rag.quality.metrics import METRICS
    METRICS.observe("retrieval_duration_ms", elapsed_ms)
    METRICS.increment("retrieval_total")

``latency_summary`` computes p50/p95/p99 from a bounded reservoir of observed
samples; the bound is explicit, so a long-running process does not grow without
limit.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

_DEFAULT_BUCKETS_MS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000, 60000)


@dataclass
class Histogram:
    name: str
    buckets_ms: tuple[float, ...] = _DEFAULT_BUCKETS_MS
    reservoir_size: int = 2048
    count: int = 0
    total_ms: float = 0.0
    minimum_ms: float | None = None
    maximum_ms: float | None = None
    bucket_counts: dict[float, int] = field(default_factory=dict)
    _samples: deque = field(default_factory=lambda: deque(maxlen=2048), repr=False)

    def __post_init__(self) -> None:
        self._samples = deque(self._samples, maxlen=self.reservoir_size)
        self.bucket_counts = {float(bucket): 0 for bucket in self.buckets_ms}

    def observe(self, value_ms: float) -> None:
        value = max(0.0, float(value_ms))
        self.count += 1
        self.total_ms += value
        self.minimum_ms = value if self.minimum_ms is None else min(self.minimum_ms, value)
        self.maximum_ms = value if self.maximum_ms is None else max(self.maximum_ms, value)
        for bucket in self.buckets_ms:
            if value <= bucket:
                self.bucket_counts[float(bucket)] += 1
        self._samples.append(value)

    def summary(self) -> dict[str, Any]:
        samples = sorted(self._samples)
        return {
            "count": self.count,
            "sum_ms": round(self.total_ms, 3),
            "min_ms": round(self.minimum_ms, 3) if self.minimum_ms is not None else None,
            "max_ms": round(self.maximum_ms, 3) if self.maximum_ms is not None else None,
            "p50_ms": _percentile(samples, 0.50),
            "p95_ms": _percentile(samples, 0.95),
            "p99_ms": _percentile(samples, 0.99),
            "reservoir": len(samples),
        }


def _percentile(sorted_values: list[float], quantile: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return round(sorted_values[0], 3)
    position = (len(sorted_values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(sorted_values[lower], 3)
    value = sorted_values[lower] * (upper - position) + sorted_values[upper] * (position - lower)
    return round(value, 3)


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: dict[str, float] = {}
        self._histograms: dict[str, Histogram] = {}
        self._labeled: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def increment(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + float(value)

    def observe(self, name: str, value_ms: float) -> None:
        with self._lock:
            histogram = self._histograms.get(name)
            if histogram is None:
                histogram = Histogram(name=name)
                self._histograms[name] = histogram
            histogram.observe(value_ms)

    def labeled_increment(self, name: str, **labels: str) -> None:
        key = (name, tuple(sorted((str(k), str(v)) for k, v in labels.items())))
        with self._lock:
            self._labeled[key] = self._labeled.get(key, 0.0) + 1.0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": {name: value for name, value in sorted(self._counters.items())},
                "histograms": {name: histogram.summary() for name, histogram in sorted(self._histograms.items())},
                "labeled_counters": [
                    {"name": name, "labels": dict(labels), "value": value}
                    for (name, labels), value in sorted(self._labeled.items())
                ],
            }

    def prometheus(self) -> str:
        lines: list[str] = []
        snapshot = self.snapshot()
        for name, value in snapshot["counters"].items():
            metric = _metric_name(name)
            lines.extend([f"# TYPE {metric} counter", f"{metric} {_number(value)}"])
        for name, summary in snapshot["histograms"].items():
            metric = _metric_name(name)
            lines.append(f"# TYPE {metric} histogram")
            histogram = self._histograms[name]
            for bucket, count in histogram.bucket_counts.items():
                lines.append(f'{metric}_bucket{{le="{_number(bucket)}"}} {count}')
            lines.append(f'{metric}_bucket{{le="+Inf"}} {summary["count"]}')
            lines.append(f"{metric}_count {summary['count']}")
            lines.append(f"{metric}_sum {_number(summary['sum_ms'])}")
            for quantile, key in ((0.5, "p50_ms"), (0.95, "p95_ms"), (0.99, "p99_ms")):
                value = summary[key]
                if value is not None:
                    lines.append(f'{metric}{{quantile="{quantile}"}} {_number(value)}')
        for row in snapshot["labeled_counters"]:
            metric = _metric_name(row["name"])
            labels = ",".join(f'{key}="{value}"' for key, value in row["labels"].items())
            lines.append(f"{metric}{{{labels}}} {_number(row['value'])}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histograms.clear()
            self._labeled.clear()


def _metric_name(name: str) -> str:
    safe = "".join(char if char.isalnum() or char == "_" else "_" for char in str(name))
    return safe if safe.startswith(("rag_", "http_")) else f"rag_{safe}"


def _number(value: float) -> str:
    value = float(value)
    return str(int(value)) if value.is_integer() else f"{value:.6f}".rstrip("0").rstrip(".")


METRICS = MetricsRegistry()
