"""Scoring contract for offline resolver evaluation; no real patient data."""
from __future__ import annotations

import unittest

from rag.eval.qrels.resolver_evaluation import evaluate_resolver
from rag.tests.quality.test_applicability_resolver import _snapshot


def _case(case_type, guideline_id=None, topic=None, **gold):
    return {
        "case_type": case_type,
        "question": "synthetic evaluation question",
        "snapshot": _snapshot(ga=266, birth_weight=3200, respiratory="无"),
        "gold": {
            "guideline_id": guideline_id,
            "topic": topic,
            "guideline_status": "mismatch" if guideline_id else None,
            "coverage": [],
            "missing_fields": [],
            **gold,
        },
    }


class ResolverEvaluationTests(unittest.TestCase):
    def test_b_and_d_score_without_external_search(self):
        cases = [
            _case("B", "pdf-neo-parenteral-nutrition-2012"),
            _case("D", topic="hyperbilirubinemia"),
        ]
        result = evaluate_resolver(cases)
        self.assertEqual(result["local_abcd"], {"n": 2, "correct": 2, "rate": 1.0})
        self.assertEqual(result["per_case_type"]["B"]["outcomes"], {"scope_mismatch": 1})
        self.assertEqual(result["per_case_type"]["D"]["outcomes"], {"evidence_gap": 1})
        self.assertEqual(result["target_status"]["accuracy"], 1.0)

    def test_cross_family_only_a_is_an_intentional_stop(self):
        case = _case(
            "A", "pdf-neo-pocus-espnic-2020",
            coverage=["pdf-neo-human-milk-fortification-2019"],
        )
        result = evaluate_resolver([case])
        self.assertEqual(result["local_abcd"]["correct"], 0)
        self.assertEqual(result["per_case_type"]["A"]["outcomes"], {"scope_mismatch": 1})

    def test_ga_is_unhandled_not_fake_extraction_accuracy(self):
        result = evaluate_resolver([_case("GA")])
        self.assertEqual(result["ga"]["n"], 1)
        self.assertEqual(result["ga"]["handled"], 0)
        self.assertNotIn("accuracy", result["ga"])
        self.assertEqual(result["local_abcd"]["n"], 0)
        self.assertIsNone(result["local_abcd"]["rate"])


if __name__ == "__main__":
    unittest.main()
