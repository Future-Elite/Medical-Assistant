"""Contract tests for the guideline-graph / patient-evidence pilot (no real data)."""
from __future__ import annotations

import unittest

from rag.his import snapshot_from_dict
from rag.quality.guideline_graph import (
    assess_guideline_graph,
    load_guideline_graph,
)


def _snapshot(
    *,
    weight: float | None = 1010,
    discharge_diagnosis: str = "",
    discharge_condition: str = "",
    treatment: str = "",
    presenting: str = "",
    extra: dict | None = None,
):
    birth_fields = {"data_status": "observed_positive", "ga_days": 210}
    if weight is not None:
        birth_fields["birth_weight_g"] = weight
    facts = [
        {
            "collection": "derived_birth", "event_type": "DERIVED",
            "record_path": "synthetic:birth", "time": "2024-05-01",
            "fields": birth_fields,
        },
    ]
    if discharge_diagnosis or discharge_condition:
        facts.append({
            "collection": "discharge", "event_type": "DISCHARGE",
            "record_path": "synthetic:discharge", "time": "2024-06-10",
            "fields": {
                "dischargeDiagnosis": discharge_diagnosis,
                "dischargeCondition": discharge_condition,
            },
        })
    if treatment:
        facts.append({
            "collection": "treatment_course", "event_type": "TREATMENT",
            "record_path": "synthetic:treatment", "time": "2024-06-09",
            "fields": {"treatmentCourse": treatment},
        })
    if presenting:
        facts.append({
            "collection": "neonatal_summary", "event_type": "SUMMARY",
            "record_path": "synthetic:presenting", "time": "2024-05-01",
            "fields": {"presentingSymptoms": presenting},
        })
    return snapshot_from_dict({
        "patient_ref": "synthetic-graph-1", "as_of": "2024-06-10",
        "source": "synthetic_chart", "facts": facts, "extra": extra or {},
    })


def _by_id(result):
    return {item["recommendation_id"]: item for item in result["recommendations"]}


class GuidelineGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = load_guideline_graph()

    def test_graph_loads_scope_and_evidence_levels(self):
        self.assertEqual(self.graph.guideline_id, "pdf-neo-feeding-vlbw-2015")
        self.assertEqual(len(self.graph.recommendations), 4)
        self.assertEqual(len(self.graph.scope), 1)
        self.assertTrue(all(item.evidence_level for item in self.graph.recommendations))

    def test_scope_match_milk_and_tolerance(self):
        result = assess_guideline_graph(
            _snapshot(discharge_condition="母乳+强化喂养，腹软不胀，喂养好"),
            self.graph,
        )
        self.assertEqual(result["scope"]["status"], "match")
        items = _by_id(result)
        self.assertEqual(items["vlbw-milk"]["status"], "applicable")
        self.assertEqual(items["vlbw-tolerance"]["status"], "applicable")
        self.assertEqual(result["patient_evidence"]["milk"]["value"], "母乳+强化")
        self.assertEqual(result["patient_evidence"]["tolerance"]["value"], "tolerant")

    def test_time_to_full_feed_missing_is_insufficient(self):
        result = assess_guideline_graph(
            _snapshot(discharge_condition="母乳喂养，腹软"), self.graph,
        )
        advance = _by_id(result)["vlbw-advance"]
        self.assertEqual(advance["status"], "insufficient_data")
        self.assertIn("birth_time", advance["missing_facts"])
        self.assertEqual(advance["audit"]["fusion_rule"], "time_unknown")

    def test_time_window_can_match_when_timestamps_exist(self):
        snapshot = _snapshot(
            extra={
                "birth_time": "2024-06-01T00:00:00",
                "feeding_start_time": "2024-06-01T10:00:00",
                "full_feed_time": "2024-06-05T00:00:00",
            },
        )
        result = assess_guideline_graph(snapshot, self.graph)
        items = _by_id(result)
        self.assertEqual(items["vlbw-trophic"]["time"]["status"], "match")
        self.assertEqual(items["vlbw-advance"]["time"]["status"], "match")
        self.assertEqual(items["vlbw-advance"]["status"], "applicable")

    def test_hard_exception_triggers_withhold(self):
        result = assess_guideline_graph(
            _snapshot(discharge_diagnosis="肠穿孔"), self.graph,
        )
        trophic = _by_id(result)["vlbw-trophic"]
        self.assertEqual(trophic["status"], "exception_triggered")
        self.assertEqual(trophic["audit"]["fusion_rule"], "hard_exception")
        exception = next(item for item in trophic["exceptions"] if item["exception_id"] == "intestinal-obstruction")
        self.assertEqual(exception["status"], "triggered")
        self.assertEqual(exception["action"], "withhold_trophic_feeds")

    def test_uncertain_hard_exception_needs_review(self):
        result = assess_guideline_graph(
            _snapshot(treatment="目前考虑有肠穿孔风险，需外科评估"), self.graph,
        )
        trophic = _by_id(result)["vlbw-trophic"]
        self.assertEqual(trophic["status"], "needs_review")
        self.assertEqual(trophic["audit"]["fusion_rule"], "hard_exception_suspected")

    def test_maternal_history_is_not_attributed_to_baby(self):
        result = assess_guideline_graph(
            _snapshot(
                presenting="母亲腹部手术史（肠梗阻），本次剖宫产娩出",
                discharge_condition="母乳喂养，腹软不胀，喂养好",
            ),
            self.graph,
        )
        trophic = _by_id(result)["vlbw-trophic"]
        self.assertNotEqual(trophic["status"], "exception_triggered")
        exception = next(item for item in trophic["exceptions"] if item["exception_id"] == "intestinal-obstruction")
        self.assertEqual(exception["status"], "not_found")

    def test_negated_exception_is_not_triggered(self):
        result = assess_guideline_graph(
            _snapshot(discharge_diagnosis="无肠梗阻"), self.graph,
        )
        exception = next(
            item for item in _by_id(result)["vlbw-trophic"]["exceptions"]
            if item["exception_id"] == "intestinal-obstruction"
        )
        self.assertEqual(exception["status"], "not_found")

    def test_nec_related_requires_review(self):
        result = assess_guideline_graph(
            _snapshot(discharge_diagnosis="坏死性小肠结肠炎"), self.graph,
        )
        trophic = _by_id(result)["vlbw-trophic"]
        self.assertEqual(trophic["status"], "needs_review")
        self.assertEqual(trophic["audit"]["fusion_rule"], "related_condition")

    def test_missing_weight_makes_scope_unknown(self):
        result = assess_guideline_graph(_snapshot(weight=None), self.graph)
        self.assertEqual(result["scope"]["status"], "unknown")
        self.assertTrue(all(item["status"] == "insufficient_data" for item in result["recommendations"]))

    def test_model_proposal_cannot_override_and_must_have_span(self):
        def judge(_recommendation, _snapshot, _evidence):
            return [
                {"recommendation_id": "vlbw-milk", "status": "needs_review",
                 "evidence_span": "腹软", "reason": "model hint"},
                {"recommendation_id": "vlbw-milk", "status": "needs_review",
                 "evidence_span": "not in the record", "reason": "bad span"},
                {"recommendation_id": "vlbw-trophic", "status": "needs_review",
                 "evidence_span": "母乳", "reason": "should not override"},
            ]

        result = assess_guideline_graph(
            _snapshot(discharge_diagnosis="肠穿孔", discharge_condition="母乳喂养，腹软"),
            self.graph, judge_fn=judge,
        )
        items = _by_id(result)
        self.assertEqual(items["vlbw-milk"]["status"], "needs_review")
        self.assertEqual(len(items["vlbw-milk"]["model_proposals"]), 1)
        self.assertEqual(items["vlbw-trophic"]["status"], "exception_triggered")
        # The proposal is kept for audit but cannot change the deterministic status.
        self.assertEqual(len(items["vlbw-trophic"]["model_proposals"]), 1)

    def test_retrieval_and_audit_trace_are_present(self):
        result = assess_guideline_graph(
            _snapshot(discharge_condition="母乳喂养，腹软"), self.graph,
            question="极低出生体重儿开奶和喂养耐受",
        )
        self.assertEqual(result["retrieval"]["ranker"], "bm25_lexical")
        self.assertEqual(len(result["retrieval"]["candidates"]), 4)
        self.assertTrue(result["not_clinical_advice"])
        self.assertTrue(result["boundaries"])
        for item in result["recommendations"]:
            self.assertTrue(item["audit"]["source_locators"])
            self.assertIn("fusion_rule", item["audit"])

    def test_injected_semantic_ranker_slot(self):
        def ranker(_query, recommendations):
            return {item.recommendation_id: 1.0 for item in recommendations}

        result = assess_guideline_graph(
            _snapshot(discharge_condition="母乳喂养，腹软"), self.graph,
            semantic_ranker=ranker,
        )
        self.assertEqual(result["retrieval"]["ranker"], "injected_semantic_ranker")


if __name__ == "__main__":
    unittest.main()
