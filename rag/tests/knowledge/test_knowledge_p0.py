"""P0 contract tests for the extended knowledge graph."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from rag.core.his import snapshot_from_dict
from rag.knowledge import (
    KnowledgeGraph,
    KnowledgeService,
    ReviewStateError,
    can_transition,
    get_default_service,
    publication_ready,
    validate_review_history,
)

FIXTURE = Path(__file__).resolve().parents[2] / "knowledge" / "fixtures" / "neonatal_graph_rag_demo.json"


def _snapshot():
    return snapshot_from_dict({"patient_ref": "p0", "as_of": "2026-04-15", "records": [
        {"collection": "derived_birth", "event_type": "BIRTH", "record_path": "r:b",
         "field_path": "r:b", "time": "2026-04-10",
         "fields": {"ga_days": 238, "birth_weight_g": 2100, "data_status": "observed_positive"}},
        {"collection": "diagnoses", "event_type": "DIAG", "record_path": "r:d",
         "field_path": "r:d", "time": "2026-04-10",
         "fields": {"name": "新生儿呼吸窘迫综合征", "data_status": "observed_positive"}},
        {"collection": "diagnoses", "event_type": "SYMPTOM", "record_path": "r:no-apnea",
         "field_path": "r:no-apnea", "time": "2026-04-10",
         "fields": {"symptom": ["呼吸暂停"], "data_status": "observed_negative"}},
        {"collection": "labs", "event_type": "LAB", "record_path": "r:l",
         "field_path": "r:l", "time": "2026-04-10",
         "fields": {"spo2_percent": 88, "data_status": "observed_positive"}},
    ]})


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class P0SchemaTests(unittest.TestCase):
    def test_source_supersession_edge_is_built(self):
        payload = _payload()
        old = copy.deepcopy(payload["sources"][0])
        old.update({"source_id": "source:old", "revision_id": "old-r0",
                    "title": "old source", "document_status": "superseded",
                    "superseded_by": "source:demo-rds", "supersedes": []})
        payload["sources"].append(old)
        for source in payload["sources"]:
            if source["source_id"] == "source:demo-rds":
                source["supersedes"] = ["source:old"]
        graph = KnowledgeGraph(payload)
        self.assertIn({"source": "source:demo-rds", "relation": "SUPERSEDES", "target": "source:old"}, graph.edges)

    def test_unformalized_condition_blocks_eligibility(self):
        payload = _payload()
        for rec in payload["recommendations"]:
            if rec["id"] == "recommendation:DEMO-RDS-CPAP":
                rec["unformalized_conditions"] = ["需人工复核的雾化条件"]
        graph = KnowledgeGraph(payload)
        service = KnowledgeService(graph)
        context = service.search(["CPAP"], _snapshot())
        row = next(item for item in context["recommendations"] if item["recommendation_id"] == "recommendation:DEMO-RDS-CPAP")
        self.assertFalse(row["eligible_for_simulation"])
        self.assertIn("不可形式化", "；".join(row["missing_information"]))

    def test_setting_dimension_is_evaluable(self):
        payload = _payload()
        payload["settings"].append({
            "id": "setting:test_false", "label": "测试场景",
            "expr": {"eq": [{"fact": "care_setting"}, "other"]},
        })
        for rec in payload["recommendations"]:
            if rec["id"] == "recommendation:DEMO-RDS-CPAP":
                rec["setting_id"] = "setting:test_false"
        graph = KnowledgeGraph(payload)
        context = KnowledgeService(graph).search(["CPAP"], _snapshot())
        row = next(item for item in context["recommendations"] if item["recommendation_id"] == "recommendation:DEMO-RDS-CPAP")
        self.assertEqual(row["applicability"]["setting"], "unknown")
        self.assertFalse(row["eligible_for_simulation"])

    def test_detection_limit_and_unit_are_conservative(self):
        from rag.knowledge.conditions import FactStore, evaluate

        facts = FactStore()
        facts.add("crp", 0.5, qualifier="<", time="2026-04-10")
        self.assertEqual(evaluate({"lt": [{"fact": "crp"}, 1]}, facts).status, "met")
        self.assertEqual(evaluate({"gte": [{"fact": "crp"}, 0.4]}, facts).status, "unknown")
        facts.add("bilirubin", 10, unit="mg/dL")
        self.assertEqual(evaluate({"lt": [{"fact": "bilirubin", "unit": "umol/L"}, 200]}, facts).status, "met")

    def test_ga_weeks_only_is_interval(self):
        from rag.knowledge.conditions import FactStore, evaluate

        facts = FactStore()
        facts.add("ga_weeks", 35)
        facts.add("ga_precision", "weeks_only")
        self.assertEqual(evaluate({"lt": [{"fact": "ga_weeks"}, 36]}, facts).status, "met")
        self.assertEqual(evaluate({"gte": [{"fact": "ga_weeks"}, 36]}, facts).status, "not_met")
        self.assertEqual(evaluate({"lt": [{"fact": "ga_weeks"}, 35.5]}, facts).status, "unknown")


class P0ServiceTests(unittest.TestCase):
    def test_context_replay_and_token_budget(self):
        service = get_default_service()
        context = service.search(["CPAP"], _snapshot())
        replay = service.replay_context(context["knowledge_context_id"])
        self.assertEqual(replay["knowledge_context_id"], context["knowledge_context_id"])
        self.assertEqual(replay["release_sha256"], context["release_sha256"])
        projection = service.model_projection(context, max_tokens=10)
        self.assertTrue(projection["truncated"])
        self.assertGreaterEqual(projection["omitted_counts"]["recommendations"], 0)
        self.assertEqual(projection["tokenizer"], "char_div_4_estimate")

    def test_repeated_query_returns_no_new_evidence(self):
        service = get_default_service()
        session = service.session(_snapshot(), ["CPAP"], max_evidence_rounds=2)
        context = session.request_more(["CPAP"])
        self.assertEqual(context["status"], "no_new_evidence")
        self.assertTrue(context["diagnostics"]["no_new_evidence"])

    def test_intent_classification_and_monitoring_split(self):
        service = get_default_service()
        context = service.search(["开始 CPAP"], _snapshot())
        self.assertEqual(context["diagnostics"]["intent"], "start")
        self.assertTrue(context["recommended_monitoring_panels"])
        self.assertIn("unavailable_monitoring", context)


class ReviewStateTests(unittest.TestCase):
    def test_legal_history_and_transitions(self):
        history = [
            {"state": "discovered", "at": "2026-11-01"},
            {"state": "screened", "at": "2026-11-01"},
            {"state": "extracted", "at": "2026-11-02"},
            {"state": "terminology_reviewed", "at": "2026-11-03"},
            {"state": "clinically_reviewed", "at": "2026-11-04"},
            {"state": "approved", "at": "2026-11-05"},
        ]
        records = validate_review_history(history)
        self.assertEqual(records[-1].state, "approved")
        self.assertTrue(publication_ready(history))
        self.assertTrue(can_transition("approved", "published"))
        self.assertFalse(can_transition("extracted", "approved"))

    def test_skipping_clinical_review_is_rejected(self):
        with self.assertRaises(ReviewStateError):
            validate_review_history([
                {"state": "discovered", "at": "2026-11-01"},
                {"state": "approved", "at": "2026-11-02"},
            ])


if __name__ == "__main__":
    unittest.main()
