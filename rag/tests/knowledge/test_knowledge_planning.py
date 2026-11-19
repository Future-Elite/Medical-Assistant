"""Plan guards and bounded Agent evidence loop tests."""
from __future__ import annotations

import copy
import unittest

from rag.core.his import snapshot_from_dict
from rag.knowledge import (
    FactStore,
    KnowledgeBudgetError,
    KnowledgeGraph,
    KnowledgeReleaseRegistry,
    PlanningError,
    ReleaseLifecycleError,
    build_maintenance_comparator,
    build_review_plan,
    episode_end_gate,
    get_default_service,
    load_knowledge_graph,
    select_candidate,
)


def _base_facts():
    return [
        {"collection": "patient", "event_type": "PATIENT", "record_path": "r:p",
         "field_path": "r:p", "time": "2026-04-10",
         "fields": {"pna_days": 2, "clinically_well": True, "data_status": "observed_positive"}},
        {"collection": "derived_birth", "event_type": "BIRTH", "record_path": "r:b",
         "field_path": "r:b", "time": "2026-04-10",
         "fields": {"ga_days": 280, "birth_weight_g": 3300, "data_status": "observed_positive"}},
        {"collection": "maternal", "event_type": "PREGNANCY", "record_path": "r:m",
         "field_path": "r:m", "time": "2026-04-10",
         "fields": {"subject_role": "mother", "gbs_status": "negative", "prom_hours": 2,
                    "maternal_fever_c": 36.8, "data_status": "observed_positive"}},
        {"collection": "diagnoses", "event_type": "SYMPTOM", "record_path": "r:no-apnea",
         "field_path": "r:no-apnea", "time": "2026-04-10",
         "fields": {"symptom": ["呼吸暂停"], "data_status": "observed_negative"}},
        {"collection": "diagnoses", "event_type": "DIAG", "record_path": "r:no-apnea-dx",
         "field_path": "r:no-apnea-dx", "time": "2026-04-10",
         "fields": {"name": "呼吸暂停", "data_status": "observed_negative"}},
    ]


def _snapshot(records=None):
    return snapshot_from_dict({
        "patient_ref": "plan-demo", "as_of": "2026-04-15",
        "records": _base_facts() if records is None else records,
    })


def _rds_snapshot():
    return snapshot_from_dict({
        "patient_ref": "rds-demo", "as_of": "2026-04-15",
        "records": [
            {"collection": "derived_birth", "event_type": "BIRTH", "record_path": "r:b",
             "field_path": "r:b", "time": "2026-04-10",
             "fields": {"ga_days": 238, "data_status": "observed_positive"}},
            {"collection": "diagnoses", "event_type": "DIAG", "record_path": "r:d",
             "field_path": "r:d", "time": "2026-04-10",
             "fields": {"name": "新生儿呼吸窘迫综合征", "data_status": "observed_positive"}},
            {"collection": "labs", "event_type": "LAB", "record_path": "r:l",
             "field_path": "r:l", "time": "2026-04-10",
             "fields": {"spo2_percent": 88, "data_status": "observed_positive"}},
            {"collection": "diagnoses", "event_type": "SYMPTOM", "record_path": "r:no-apnea",
             "field_path": "r:no-apnea", "time": "2026-04-10",
             "fields": {"symptom": ["呼吸暂停"], "data_status": "observed_negative"}},
        ],
    })


class ObservationValidationTests(unittest.TestCase):
    def _context(self, records=None):
        service = get_default_service()
        patient = _snapshot(records)
        return service, patient, service.search(["观察"], patient)

    def test_wait_requires_explicit_stable_assessment(self):
        service, patient, context = self._context()
        result = service.validate_actions({"actions": [{
            "operation": "wait", "offset_hours": 0,
            "guideline_rule_ids": ["recommendation:DEMO-OBSERVE-LOW-RISK"],
        }]}, patient, context["knowledge_context_id"])
        self.assertTrue(result["eligible_for_simulation"], result)

    def test_missing_clinically_well_is_not_observation(self):
        facts = [item for item in _base_facts() if item["collection"] != "patient"]
        service, patient, context = self._context(facts)
        result = service.validate_actions({"actions": [{
            "operation": "wait", "offset_hours": 0,
            "guideline_rule_ids": ["recommendation:DEMO-OBSERVE-LOW-RISK"],
        }]}, patient, context["knowledge_context_id"])
        self.assertFalse(result["eligible_for_simulation"])
        self.assertIn("CLINICAL_REVIEW_REQUIRED", [row["code"] for row in result["errors"]])

    def test_red_flag_blocks_observation(self):
        facts = [dict(item) for item in _base_facts()]
        for item in facts:
            if item["record_path"] == "r:no-apnea":
                item["fields"] = {"symptom": ["呼吸暂停"], "data_status": "observed_positive"}
            if item["record_path"] == "r:no-apnea-dx":
                item["fields"] = {"name": "呼吸暂停", "data_status": "observed_positive"}
        service, patient, context = self._context(facts)
        result = service.validate_actions({"actions": [{
            "operation": "wait", "offset_hours": 0,
            "guideline_rule_ids": ["recommendation:DEMO-OBSERVE-LOW-RISK"],
        }]}, patient, context["knowledge_context_id"])
        self.assertIn("RED_FLAG_PRESENT", [row["code"] for row in result["errors"]])


class CombinationAndTimelineTests(unittest.TestCase):
    def test_future_exclusive_support_conflicts_with_current_bundle(self):
        service = get_default_service()
        patient = _rds_snapshot()
        context = service.search(["CPAP", "呼吸窘迫"], patient)
        result = service.validate_actions({"actions": [
            {"operation": "start", "target": "action:cpap", "offset_hours": 0,
             "guideline_rule_ids": ["recommendation:DEMO-RDS-CPAP"]},
            {"operation": "start", "target": "action:nippv", "offset_hours": 6,
             "guideline_rule_ids": ["recommendation:DEMO-RDS-NIPPV"]},
        ]}, patient, context["knowledge_context_id"])
        self.assertFalse(result["eligible_for_simulation"])
        self.assertTrue(result["combination_errors"])
        self.assertEqual([row["offset_hours"] for row in result["timeline"]], [0.0, 6.0])
        self.assertEqual(result["timeline"][1]["status"], "supported")

    def test_plan_must_start_at_zero_and_be_sorted(self):
        service = get_default_service()
        patient = _rds_snapshot()
        context = service.search(["CPAP"], patient)
        result = service.validate_actions({"actions": [
            {"operation": "start", "target": "action:cpap", "offset_hours": 6,
             "guideline_rule_ids": ["recommendation:DEMO-RDS-CPAP"]},
        ]}, patient, context["knowledge_context_id"])
        self.assertFalse(result["eligible_for_simulation"])
        self.assertIn("PLAN_NOT_TIME_ORDERED", [row["code"] for row in result["errors"]])


class ReviewAndMaintenanceTests(unittest.TestCase):
    def _context(self):
        service = get_default_service()
        patient = snapshot_from_dict({
            "patient_ref": "review-demo", "as_of": "2026-04-15",
            "records": [
                {"collection": "derived_birth", "event_type": "BIRTH", "record_path": "r:b",
                 "field_path": "r:b", "time": "2026-04-10",
                 "fields": {"ga_days": 238, "data_status": "observed_positive"}},
                {"collection": "labs", "event_type": "LAB", "record_path": "r:l",
                 "field_path": "r:l", "time": "2026-04-10",
                 "fields": {"wbc": 18, "platelet": 220, "hemoglobin": 170,
                            "crp": 5, "sodium": 138, "data_status": "observed_positive"}},
                {"collection": "diagnoses", "event_type": "DIAG", "record_path": "r:d",
                 "field_path": "r:d", "time": "2026-04-10",
                 "fields": {"name": "新生儿肺炎", "data_status": "observed_positive"}},
            ],
        })
        return service, patient, service.search(["抗生素"], patient)

    def test_review_panels_expand_only_initialized_metrics(self):
        service, _, context = self._context()
        plan = service.review_plan(
            context["knowledge_context_id"],
            panel_ids=["panel:cbc", "panel:electrolytes"],
        )
        self.assertEqual(plan["review_metrics"], ["wbc", "platelet", "hemoglobin", "sodium"])
        with self.assertRaises(PlanningError):
            service.review_plan(context["knowledge_context_id"], panel_ids=["panel:blood_gas"])

    def test_review_metric_without_baseline_is_rejected(self):
        service, _, context = self._context()
        with self.assertRaises(PlanningError):
            build_review_plan(context, metric_keys=["paco2"])

    def test_maintenance_comparator_inherits_monitoring_and_loses_ties(self):
        primary = {
            "candidate_id": "proposed",
            "actions": [{"operation": "start", "target": "action:cpap", "offset_hours": 0}],
            "review_metrics": ["crp"],
            "review_panels": [],
        }
        comparator = build_maintenance_comparator(primary)
        self.assertEqual(comparator["review_metrics"], ["crp"])
        self.assertGreater(comparator["proposal_order"], 0)
        chosen = select_candidate([
            {"candidate_id": "proposed", "expected_abnormal_count": 1, "proposal_order": 0},
            {**comparator, "expected_abnormal_count": 1},
        ])
        self.assertEqual(chosen["candidate_id"], "proposed")

    def test_episode_end_is_not_inferred_from_scores(self):
        facts = FactStore()
        facts.add("crp", 5, path="r:l")
        self.assertFalse(episode_end_gate(facts)["allowed"])
        facts.add("active_treatment", "CPAP", path="r:s")
        self.assertFalse(episode_end_gate(facts, explicit_clinical_end=True)["allowed"])
        clean = FactStore()
        self.assertTrue(episode_end_gate(clean, explicit_clinical_end=True)["allowed"])


class ReleaseLifecycleTests(unittest.TestCase):
    def _publishable_graph(self, release_id):
        data = copy.deepcopy(load_knowledge_graph().raw)
        data["release"]["release_id"] = release_id
        data["release"]["publishable"] = True
        data["release"]["review_status"] = "reviewed"
        for span in data["spans"]:
            span["review_status"] = "reviewed"
        for source in data["sources"]:
            source["review_status"] = "reviewed"
        return KnowledgeGraph(data)

    def test_demo_release_cannot_be_published(self):
        registry = KnowledgeReleaseRegistry([load_knowledge_graph()])
        with self.assertRaises(ReleaseLifecycleError):
            registry.publish("DEMO-RELEASE-NOT-FOR-PRODUCTION")

    def test_publish_withdraw_rollback(self):
        first = self._publishable_graph("TEST-RELEASE-A")
        second = self._publishable_graph("TEST-RELEASE-B")
        registry = KnowledgeReleaseRegistry([first, second])
        registry.publish("TEST-RELEASE-A")
        self.assertEqual(registry.active().release.release_id, "TEST-RELEASE-A")
        registry.publish("TEST-RELEASE-B")
        self.assertEqual(registry.active().release.release_id, "TEST-RELEASE-B")
        registry.rollback("TEST-RELEASE-B", "TEST-RELEASE-A", reason="demo rollback")
        self.assertEqual(registry.active().release.release_id, "TEST-RELEASE-A")
        registry.withdraw("TEST-RELEASE-A", reason="demo withdrawal")
        self.assertIsNone(registry.active())
        states = {row["release_id"]: row["status"] for row in registry.list_releases()["releases"]}
        self.assertEqual(states["TEST-RELEASE-A"], "withdrawn")
        self.assertEqual(states["TEST-RELEASE-B"], "superseded")


class EvidenceLoopTests(unittest.TestCase):
    def test_needs_evidence_runs_the_query_and_records_graph(self):
        service = get_default_service()
        session = service.session(_rds_snapshot(), ["CPAP"], max_evidence_rounds=2)
        context = session.request_more(["LISA"])
        self.assertEqual(context["term_resolutions"][0]["candidates"][0]["id"], "action:surfactant")
        self.assertTrue(session.trace)
        self.assertEqual(session.trace[0]["queries"], ["LISA"])
        self.assertIn("action:surfactant", session.trace[0]["normalized_terms"])
        self.assertTrue(session.trace[0]["graph"]["edges"])

    def test_budget_stops_loop_without_state_change(self):
        service = get_default_service()
        session = service.session(_rds_snapshot(), ["CPAP"], max_evidence_rounds=2)
        session.request_more(["LISA"])
        session.request_more(["CPAP"])
        before = session.snapshot.context_id
        with self.assertRaises(KnowledgeBudgetError):
            session.request_more(["CPAP"])
        self.assertEqual(session.snapshot.context_id, before)
        self.assertEqual(len(session.trace), 2)

    def test_run_loop_handles_one_needs_evidence_then_ready(self):
        service = get_default_service()
        session = service.session(_rds_snapshot(), ["CPAP"], max_evidence_rounds=2)
        calls = {"count": 0}

        def propose(_context):
            calls["count"] += 1
            if calls["count"] == 1:
                return {"status": "needs_evidence", "knowledge_queries": ["LISA"]}
            return {"status": "ready", "plans": []}

        result = session.run_loop(propose)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["evidence_rounds"], 1)
        self.assertEqual(len(result["trace"]), 1)


if __name__ == "__main__":
    unittest.main()
