"""Tests for claim-level adjudication and the minimal counterfactual certificate."""
from __future__ import annotations

import unittest

from rag.eval.qrels.claim_adjudication import (
    adjudicate_claims,
    validate_certificates_on_pairs,
)
from rag.his import snapshot_from_dict
from rag.quality.guideline_graph import load_guideline_graph


def _snapshot(*, weight=1010, diagnosis="", condition="", extra=None):
    birth = {"data_status": "observed_positive", "ga_days": 210}
    if weight is not None:
        birth["birth_weight_g"] = weight
    facts = [
        {
            "collection": "derived_birth", "event_type": "DERIVED",
            "record_path": "synthetic:birth", "time": "2024-05-01",
            "fields": birth,
        },
    ]
    if diagnosis or condition:
        facts.append({
            "collection": "discharge", "event_type": "DISCHARGE",
            "record_path": "synthetic:discharge", "time": "2024-06-10",
            "fields": {"dischargeDiagnosis": diagnosis, "dischargeCondition": condition},
        })
    return snapshot_from_dict({
        "patient_ref": "synthetic-claim-1", "as_of": "2024-06-10",
        "source": "synthetic_chart", "facts": facts, "extra": extra or {},
    })


def _claims(result):
    return {item["recommendation_id"]: item for item in result["claims"]}


class ClaimAdjudicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = load_guideline_graph()

    def test_concordant_claims(self):
        result = adjudicate_claims(
            _snapshot(condition="母乳+强化喂养，腹软不胀，喂养好"), self.graph,
        )
        claims = _claims(result)
        self.assertEqual(claims["vlbw-milk"]["verdict"], "concordant")
        self.assertEqual(claims["vlbw-tolerance"]["verdict"], "concordant")
        self.assertEqual(
            claims["vlbw-milk"]["counterfactual_certificate"]["status"], "not_needed"
        )

    def test_hard_exception_dominates(self):
        result = adjudicate_claims(_snapshot(diagnosis="肠穿孔"), self.graph)
        claim = _claims(result)["vlbw-trophic"]
        self.assertEqual(claim["verdict"], "guideline_exception")
        self.assertEqual(
            claim["counterfactual_certificate"]["status"], "exception_dominates"
        )
        self.assertEqual(claim["next_action"]["type"], "review_exception")

    def test_related_condition_requires_review(self):
        result = adjudicate_claims(_snapshot(diagnosis="坏死性小肠结肠炎"), self.graph)
        claim = _claims(result)["vlbw-trophic"]
        self.assertEqual(claim["verdict"], "guideline_review")
        self.assertEqual(
            claim["counterfactual_certificate"]["status"], "review_dominates"
        )

    def test_time_gap_is_unstructured(self):
        result = adjudicate_claims(_snapshot(condition="母乳喂养，腹软"), self.graph)
        claim = _claims(result)["vlbw-advance"]
        self.assertEqual(claim["verdict"], "insufficient_evidence")
        self.assertEqual(
            claim["counterfactual_certificate"]["status"], "requires_unstructured_data"
        )
        self.assertIn("birth_time", claim["counterfactual_certificate"]["missing_facts"])

    def test_missing_weight_has_minimal_certificate(self):
        result = adjudicate_claims(_snapshot(weight=None), self.graph)
        claim = _claims(result)["vlbw-milk"]
        certificate = claim["counterfactual_certificate"]
        self.assertEqual(certificate["status"], "flippable")
        self.assertTrue(certificate["minimal_changes"])
        change = certificate["minimal_changes"][0]
        self.assertEqual(change["field"], "derived_birth.birth_weight_g")
        self.assertEqual(change["predicted_scope_status"], "match")
        self.assertEqual(change["criterion_id"], "vlbw-birth-weight")
        self.assertIn("locator", change)
        self.assertEqual(certificate["search_space"]["algorithm"], "uniform_cost_search")
        self.assertGreater(certificate["search_space"]["nodes_expanded"], 0)
        self.assertTrue(certificate["search_space"]["optimal_within_search_space"])
        self.assertEqual(certificate["total_cost"], 1.0)

    def test_patient_outside_scope_is_not_applicable(self):
        result = adjudicate_claims(_snapshot(weight=2000), self.graph)
        claim = _claims(result)["vlbw-milk"]
        self.assertEqual(claim["verdict"], "not_applicable")
        self.assertEqual(
            claim["counterfactual_certificate"]["status"], "not_applicable"
        )

    def test_pair_certificate_validation(self):
        pair = {
            "pair_id": "cf-test",
            "guideline_id": "pdf-neo-feeding-vlbw-2015",
            "changed_field": "birth_weight_g",
            "before": {"birth_weight_g": 1790},
            "after": {"birth_weight_g": 1400},
            "label_before": "mismatch",
            "label_after": "possible_match",
        }
        result = validate_certificates_on_pairs([pair], self.graph)
        self.assertEqual(result["pairs_checked"], 1)
        self.assertEqual(result["field_hit"], 1)
        self.assertEqual(result["direction_match"], 1)
        self.assertEqual(result["minimality_1field"], 1)
        self.assertEqual(result["direction_ci95"], [1.0, 1.0])

    def test_audit_boundaries(self):
        result = adjudicate_claims(_snapshot(condition="母乳喂养，腹软"), self.graph)
        self.assertTrue(result["not_clinical_advice"])
        self.assertTrue(result["boundaries"])
        for claim in result["claims"]:
            self.assertIn("audit", claim)
            self.assertIn("patient_side", claim)
            self.assertIn("guideline_side", claim)


if __name__ == "__main__":
    unittest.main()
