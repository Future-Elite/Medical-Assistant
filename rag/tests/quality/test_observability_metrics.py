"""v6.7.1 observability: counters, percentiles, Prometheus text."""

from __future__ import annotations

import unittest

from rag.quality.metrics import Histogram, MetricsRegistry


class MetricsTests(unittest.TestCase):
    def test_counters_and_histogram_summary(self) -> None:
        registry = MetricsRegistry()
        registry.increment("retrieval_total")
        registry.increment("retrieval_total", 2)
        for value in (1, 2, 3, 4, 100):
            registry.observe("retrieval_duration_ms", value)
        snapshot = registry.snapshot()
        self.assertEqual(snapshot["counters"]["retrieval_total"], 3.0)
        summary = snapshot["histograms"]["retrieval_duration_ms"]
        self.assertEqual(summary["count"], 5)
        self.assertEqual(summary["min_ms"], 1)
        self.assertEqual(summary["max_ms"], 100)
        self.assertIsNotNone(summary["p50_ms"])
        self.assertLessEqual(summary["p50_ms"], summary["p95_ms"])
        self.assertLessEqual(summary["p95_ms"], summary["p99_ms"])

    def test_labeled_counter_and_prometheus_text(self) -> None:
        registry = MetricsRegistry()
        registry.labeled_increment("source_search_total", source_id="pubmed-live", status="ok")
        registry.observe("retrieval_duration_ms", 12.5)
        text = registry.prometheus()
        self.assertIn("rag_source_search_total{source_id=\"pubmed-live\",status=\"ok\"} 1", text)
        self.assertIn("rag_retrieval_duration_ms_count 1", text)
        self.assertIn('rag_retrieval_duration_ms{quantile="0.5"}', text)
        self.assertIn("+Inf", text)

    def test_reset_clears_state(self) -> None:
        registry = MetricsRegistry()
        registry.increment("x")
        registry.observe("y", 1)
        registry.reset()
        self.assertEqual(registry.snapshot()["counters"], {})
        self.assertEqual(registry.snapshot()["histograms"], {})

    def test_histogram_reservoir_is_bounded(self) -> None:
        histogram = Histogram(name="bounded", reservoir_size=10)
        for value in range(100):
            histogram.observe(value)
        self.assertEqual(histogram.count, 100)
        self.assertEqual(len(histogram._samples), 10)


if __name__ == "__main__":
    unittest.main()
