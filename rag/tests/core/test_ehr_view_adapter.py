"""EHR view -> PatientSnapshot adapter contract."""
from __future__ import annotations

from datetime import datetime
import unittest

from rag.adapters import snapshot_from_ehr_view
from rag.knowledge import get_default_service
from rag.knowledge.patient_facts import patient_fact_store
from rag.patient_graph import build_patient_graph


def _view():
    return {
        "intake": {
            "gestational_weeks": 35, "gestational_days": 6,
            "birth_weight_g": 2328, "age_hours": 21, "symptoms": ["气促"],
        },
        "phase": "treatment", "time_hours": 23,
        "observations": [
            {"event_id": "obs1", "session_type": "admission",
             "collected_at_hours": 0, "time_hours": 0,
             "items": [
                 {"key": "pH", "value": 7.36},
                 {"key": "PaCO2(mmHg)", "value": 54},
                 {"key": "SpO2(%)", "value": 91},
                 {"key": "CRP(mg/L)", "value": 24},
             ]},
            {"event_id": "future", "session_type": "followup",
             "collected_at_hours": 30, "time_hours": 30,
             "items": [{"key": "SpO2(%)", "value": 80}]},
        ],
        "active_treatments": [{"target": "cpap"}],
        "pending_tests": [{"target": "culture"}],
        "conditions": [{"condition": "rds", "status": "active"}],
        "executed_actions": [{"action": "start_cpap", "at_hours": 0}],
        "remaining_cycles": 2,
    }


class EhrViewAdapterTests(unittest.TestCase):
    def test_view_maps_to_flat_snapshot_and_normalized_facts(self):
        snapshot = snapshot_from_ehr_view(
            _view(), patient_ref="ehr-1",
            start_time=datetime.fromisoformat("2026-01-08T08:00:00"),
        )
        self.assertEqual(snapshot.as_of, "2026-01-09T07:00:00")
        self.assertTrue(snapshot.records)
        facts = patient_fact_store(snapshot)
        compact = facts.compact()
        self.assertAlmostEqual(compact["ga_weeks"]["value"], 35 + 6 / 7, places=2)
        self.assertEqual(compact["birth_weight_g"]["value"], 2328)
        self.assertEqual(compact["spo2"]["value"], 91)
        self.assertEqual(compact["paco2"]["value"], 54)
        self.assertEqual(compact["active_treatment"]["value"], "cpap")
        self.assertEqual(compact["pending_test"]["value"], "culture")
        # Future observation is filtered by as_of (2026-01-09 08:00).
        paths = {fact.record_id for fact in snapshot.visible_records()}
        self.assertIn("ehr:observation:obs1", paths)
        self.assertNotIn("ehr:observation:future", paths)

    def test_patient_graph_and_knowledge_graph_accept_the_projection(self):
        snapshot = snapshot_from_ehr_view(
            _view(), patient_ref="ehr-1",
            start_time=datetime.fromisoformat("2026-01-08T08:00:00"),
        )
        graph = build_patient_graph(snapshot)
        self.assertGreater(len(graph.nodes), 0)
        self.assertGreater(len(graph.edges), 0)
        context = get_default_service().search(["CPAP", "呼吸窘迫"], snapshot)
        self.assertIn(context["status"], {"ok", "partial"})
        self.assertTrue(context["recommendations"])

    def test_missing_anchor_is_rejected(self):
        with self.assertRaises(ValueError):
            snapshot_from_ehr_view(_view(), patient_ref="ehr-1")

    def test_pydantic_view_is_accepted(self):
        class Model:
            def model_dump(self, mode="python"):
                return _view()

        snapshot = snapshot_from_ehr_view(
            Model(), patient_ref="ehr-pydantic", as_of="2026-01-09T07:00:00",
        )
        self.assertTrue(snapshot.records)
        self.assertEqual(snapshot.patient_ref, "ehr-pydantic")


if __name__ == "__main__":
    unittest.main()
