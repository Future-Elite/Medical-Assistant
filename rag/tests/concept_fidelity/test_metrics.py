"""Hand-computed concept-bound metrics."""
from __future__ import annotations

import unittest

from rag.concept_fidelity.metrics import (
    aggregate_metrics,
    concept_identity_summary,
    ranking_metrics,
)
from rag.concept_fidelity.schema import ConceptIdentity


class RankingMetricsTests(unittest.TestCase):
    def test_hand_computed_metrics(self):
        result = ranking_metrics(
            ["a", "b", "c"],
            {
                "a": frozenset({"T"}),
                "b": frozenset({"S"}),
                "c": frozenset({"T"}),
            },
            target_ids=["T"],
            confusable_ids=["S"],
            total_target_docs=3,
            k=3,
        )
        self.assertAlmostEqual(result["entity_correct_recall"], 2 / 3)
        self.assertAlmostEqual(result["wrong_entity_contamination"], 1 / 3)
        self.assertEqual(result["sibling_discrimination"], 1.0)
        self.assertEqual(result["mrr"], 1.0)
        self.assertAlmostEqual(result["ndcg"], 1.5 / (1.0 + 1.0 / 1.584962500721156 + 0.5))

    def test_empty_denominator_is_none(self):
        result = ranking_metrics(
            [], {}, target_ids=["T"], confusable_ids=[], total_target_docs=0, k=10,
        )
        self.assertIsNone(result["entity_correct_recall"])
        self.assertIsNone(result["ndcg"])
        self.assertIsNone(result["sibling_discrimination"])

    def test_confusable_before_target_fails_discrimination(self):
        result = ranking_metrics(
            ["s", "t"],
            {"s": frozenset({"S"}), "t": frozenset({"T"})},
            target_ids=["T"], confusable_ids=["S"], total_target_docs=1, k=2,
        )
        self.assertEqual(result["sibling_discrimination"], 0.0)

    def test_aggregate_skips_unmeasured(self):
        summary = aggregate_metrics([
            {"entity_correct_recall": 1.0, "wrong_entity_contamination": 0.0,
             "sibling_discrimination": 1.0, "ndcg": 1.0, "mrr": 1.0},
            {"entity_correct_recall": None, "wrong_entity_contamination": 0.0,
             "sibling_discrimination": None, "ndcg": None, "mrr": 0.0},
        ])
        self.assertEqual(summary["entity_correct_recall"], 1.0)
        self.assertEqual(summary["entity_correct_recall_measured"], 1)
        self.assertEqual(summary["wrong_entity_contamination"], 0.0)


class IdentitySummaryTests(unittest.TestCase):
    def test_summary_counts_positive_drift_and_not_checked(self):
        summary = concept_identity_summary([
            ConceptIdentity(slot="disease", source_surface="a", target_concept_id="D1",
                            converted_concept_id="D1", label="same_concept"),
            ConceptIdentity(slot="disease", source_surface="b", target_concept_id="D2",
                            converted_concept_id="D3", label="hyponym_drift"),
            ConceptIdentity(slot="disease", source_surface="c", target_concept_id="D4",
                            label="not_checked"),
        ])
        self.assertAlmostEqual(summary["concept_identity_accuracy"], 1 / 3)
        self.assertAlmostEqual(summary["drift_rate"], 1 / 3)
        self.assertAlmostEqual(summary["not_checked_rate"], 1 / 3)


if __name__ == "__main__":
    unittest.main()
