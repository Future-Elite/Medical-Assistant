"""Bidirectional patient/knowledge graph retrieval fusion tests."""
from __future__ import annotations

import unittest

from rag.core.his import snapshot_from_dict
from rag.knowledge import KnowledgeService, get_default_service
from rag.knowledge.fusion import FUSION_VERSION
from tests.knowledge.test_knowledge import snapshot


class BidirectionalFusionTests(unittest.TestCase):
    def test_fusion_records_forward_and_backward_evidence(self):
        context = get_default_service().search(["CPAP", "呼吸窘迫"], snapshot())
        fusion = context["fusion"]
        self.assertEqual(fusion["schema_version"], FUSION_VERSION)
        self.assertGreaterEqual(fusion["forward"]["hits"], 1)
        self.assertTrue(fusion["backward"]["queries"])
        self.assertGreaterEqual(fusion["backward"]["hits"], 1)
        self.assertTrue(any(row.get("patient_graph_evidence") for row in context["recommendations"]))
        evidence = next(row["patient_graph_evidence"] for row in context["recommendations"]
                        if row.get("patient_graph_evidence"))
        self.assertIn(evidence[0]["origin"], {"forward", "backward"})
        self.assertTrue(evidence[0]["record_path"])
        self.assertTrue(evidence[0]["graph_path"])

    def test_fusion_does_not_change_condition_eligibility(self):
        service = get_default_service()
        fused = service.search(["CPAP", "呼吸窘迫"], snapshot(), fusion=True)
        plain = service.search(["CPAP", "呼吸窘迫"], snapshot(), fusion=False)
        self.assertNotIn("fusion", plain)
        fused_rows = {row["recommendation_id"]: row["eligible_for_simulation"]
                      for row in fused["recommendations"]}
        plain_rows = {row["recommendation_id"]: row["eligible_for_simulation"]
                      for row in plain["recommendations"]}
        self.assertEqual(fused_rows, plain_rows)
        self.assertEqual(fused["missing_information"], plain["missing_information"])

    def test_fusion_is_cached_by_mode(self):
        service = KnowledgeService()
        first = service.search(["CPAP"], snapshot(), fusion=True)
        second = service.search(["CPAP"], snapshot(), fusion=True)
        plain = service.search(["CPAP"], snapshot(), fusion=False)
        self.assertTrue(first["diagnostics"]["cache_hit"] is False)
        self.assertTrue(second["diagnostics"]["cache_hit"] is True)
        self.assertFalse(plain["diagnostics"]["cache_hit"])
        self.assertIn("fusion", second)
        self.assertNotIn("fusion", plain)

    def test_condition_references_fill_backward_topk_gaps(self):
        context = get_default_service().search(["CPAP", "呼吸窘迫"], snapshot())
        cpap = next(row for row in context["recommendations"]
                    if row["recommendation_id"] == "recommendation:DEMO-RDS-CPAP")
        refs = {hit["record_path"] for hit in cpap["patient_graph_evidence"]}
        self.assertTrue({"r:birth", "r:diag", "r:no-apnea", "r:labs"} <= refs)
        self.assertGreater(cpap["fusion"]["condition_ref_matched"], 0)
        self.assertEqual(cpap["fusion"]["coverage"], 1.0)

    def test_forward_backward_modes_keep_eligibility_and_add_evidence(self):
        service = KnowledgeService()
        contexts = {
            mode: service.search(["CPAP", "呼吸窘迫"], snapshot(), fusion=True, fusion_mode=mode)
            for mode in ("forward", "backward", "full")
        }
        off = service.search(["CPAP", "呼吸窘迫"], snapshot(), fusion=False)
        baseline = {row["recommendation_id"]: row["eligible_for_simulation"]
                    for row in off["recommendations"]}
        for mode, context in contexts.items():
            current = {row["recommendation_id"]: row["eligible_for_simulation"]
                       for row in context["recommendations"]}
            self.assertEqual(current, baseline, mode)
            self.assertTrue(any(row.get("patient_graph_evidence")
                                for row in context["recommendations"]), mode)
        self.assertGreater(contexts["forward"]["fusion"]["forward"]["hits"], 0)
        self.assertGreater(contexts["backward"]["fusion"]["backward"]["hits"], 0)

    def test_query_action_match_precedes_generic_condition_paths(self):
        service = get_default_service()
        surf = service.search(
            ["肺表面活性物质", "呼吸窘迫"],
            snapshot(ga_days=210), intent="start", fusion=True,
        )
        self.assertEqual(surf["recommendations"][0]["recommendation_id"],
                         "recommendation:DEMO-RDS-SURFACTANT")
        chest = service.search(
            ["胸片", "呼吸窘迫"], snapshot(), intent="start", fusion=True,
        )
        self.assertEqual(chest["recommendations"][0]["recommendation_id"],
                         "recommendation:DEMO-CHEST-XRAY")

    def test_stop_intent_prefers_stop_only_recommendation(self):
        service = get_default_service()
        patient = snapshot_from_dict({"patient_ref": "stop-demo", "as_of": "2026-04-15", "records": [
            {"record_id": "r:p", "record_type": "patient", "event_type": "PATIENT",
             "event_time": "2026-04-10", "fields": {"pna_days": 2, "clinically_well": True,
                                                     "data_status": "observed_positive"}},
            {"record_id": "r:b", "record_type": "derived_birth", "event_type": "BIRTH",
             "event_time": "2026-04-10", "fields": {"ga_days": 238, "birth_weight_g": 2100,
                                                     "data_status": "observed_positive"}},
            {"record_id": "r:no-resp", "record_type": "diagnoses", "event_type": "DIAG",
             "event_time": "2026-04-10", "fields": {"name": "呼吸窘迫", "data_status": "observed_negative"}},
            {"record_id": "r:no-apnea", "record_type": "diagnoses", "event_type": "SYMPTOM",
             "event_time": "2026-04-10", "fields": {"symptom": ["呼吸暂停"], "data_status": "observed_negative"}},
            {"record_id": "r:cpap", "record_type": "respiratory_support", "event_type": "SUPPORT",
             "start": "2026-04-10", "end": None, "fields": {"mode": "CPAP", "data_status": "observed_positive"}},
        ]})
        context = service.search(["停止 CPAP"], patient, intent="stop", fusion=True)
        self.assertEqual(context["recommendations"][0]["recommendation_id"],
                         "recommendation:DEMO-STOP-CPAP")


if __name__ == "__main__":
    unittest.main()
