"""Knowledge graph, four-state conditions and Graph-RAG tests."""
from __future__ import annotations

import unittest

from rag.core.his import snapshot_from_dict
from rag.knowledge import (
    FactStore,
    KnowledgeError,
    evaluate,
    evaluate_condition_ids,
    get_default_service,
    load_knowledge_graph,
)


def snapshot(*, ga_days=238, spo2=88, gbs="negative", pending_culture=False, active_cpap=False, future=False):
    facts = [
        {"collection": "patient", "event_type": "PATIENT", "record_path": "r:patient",
         "field_path": "r:patient", "time": "2026-04-10",
         "fields": {"gender": "男", "pna_days": 2, "data_status": "observed_positive"}},
        {"collection": "derived_birth", "event_type": "BIRTH", "record_path": "r:birth",
         "field_path": "r:birth", "time": "2026-04-10",
         "fields": {"ga_days": ga_days, "ga_text": f"{ga_days // 7}周", "birth_weight_g": 2100,
                    "data_status": "observed_positive"}},
        {"collection": "diagnoses", "event_type": "DIAG", "record_path": "r:diag",
         "field_path": "r:diag", "time": "2026-04-10",
         "fields": {"name": "新生儿呼吸窘迫综合征", "data_status": "observed_positive"}},
        {"collection": "labs", "event_type": "LAB", "record_path": "r:labs",
         "field_path": "r:labs", "time": "2026-04-10",
         "fields": {"spo2_percent": spo2, "ph": 7.25, "data_status": "observed_positive"}},
        {"collection": "diagnoses", "event_type": "SYMPTOM", "record_path": "r:no-apnea",
         "field_path": "r:no-apnea", "time": "2026-04-10",
         "fields": {"symptom": ["呼吸暂停"], "data_status": "observed_negative"}},
        {"collection": "maternal", "event_type": "PREGNANCY", "record_path": "r:maternal",
         "field_path": "r:maternal", "time": "2026-04-10",
         "fields": {"subject_role": "mother", "gbs_status": gbs, "prom_hours": 2,
                    "data_status": "observed_positive"}},
        {"collection": "labs", "event_type": "CULTURE", "record_path": "r:culture",
         "field_path": "r:culture", "time": "2026-04-10",
         "fields": {"culture_status": "pending" if pending_culture else "not_recorded",
                    "data_status": "pending" if pending_culture else "not_recorded"}},
    ]
    if active_cpap:
        facts.append({"collection": "respiratory_support", "event_type": "SUPPORT",
                      "record_path": "r:cpap", "field_path": "r:cpap", "start": "2026-04-10",
                      "end": None, "fields": {"mode": "CPAP", "data_status": "observed_positive"}})
    if future:
        facts.append({"collection": "diagnoses", "event_type": "DIAG", "record_path": "r:future",
                      "field_path": "r:future", "time": "2026-05-01",
                      "fields": {"name": "未来诊断", "data_status": "observed_positive"}})
    return snapshot_from_dict({"patient_ref": "know-demo", "as_of": "2026-04-15", "records": facts})


class KnowledgeGraphTests(unittest.TestCase):
    def test_release_hash_is_stable_and_graph_has_typed_nodes(self):
        first = load_knowledge_graph()
        second = load_knowledge_graph()
        self.assertEqual(first.release.content_sha256, second.release.content_sha256)
        self.assertFalse(first.release.publishable)
        self.assertEqual(first.release.review_status, "synthetic_fixture")
        self.assertIn("recommendation", first.stats()["node_types"])
        self.assertGreater(first.stats()["recommendations"], 0)
        self.assertGreater(first.stats()["evidence_spans"], 0)

    def test_term_resolution_is_explicit(self):
        service = get_default_service()
        resolved = service.resolve_terms(["CPAP"])["terms"][0]
        ambiguous = service.resolve_terms(["PS"])["terms"][0]
        unresolved = service.resolve_terms(["完全未知术语"])["terms"][0]
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(ambiguous["status"], "ambiguous")
        self.assertEqual(len(ambiguous["candidates"]), 2)
        self.assertEqual(unresolved["status"], "unresolved")
        self.assertEqual(unresolved["candidates"], [])

    def test_evidence_span_points_to_source_revision(self):
        service = get_default_service()
        evidence = service.get_evidence("span:rds-cpap")
        self.assertEqual(evidence["span"]["span_id"], "span:rds-cpap")
        self.assertEqual(evidence["source"]["source_id"], "source:demo-rds")
        self.assertTrue(evidence["span"]["locator"])

    def test_unknown_release_is_rejected(self):
        with self.assertRaises(KnowledgeError):
            get_default_service().get_release("not-a-release")


class ConditionTests(unittest.TestCase):
    def test_four_state_and_unknown_do_not_become_met(self):
        facts = FactStore()
        self.assertEqual(evaluate({"lt": [{"fact": "ga_weeks"}, 37]}, facts).status, "unknown")
        facts.add("ga_weeks", 34)
        self.assertEqual(evaluate({"lt": [{"fact": "ga_weeks"}, 37]}, facts).status, "met")
        self.assertEqual(evaluate({"gte": [{"fact": "ga_weeks"}, 37]}, facts).status, "not_met")
        self.assertEqual(evaluate({"not": {"lt": [{"fact": "ga_weeks"}, 37]}}, facts).status, "not_met")

    def test_unknown_propagates_through_all_and_any(self):
        facts = FactStore()
        facts.add("ga_weeks", 34)
        all_unknown = evaluate({"all": [
            {"lt": [{"fact": "ga_weeks"}, 37]},
            {"exists": {"fact": "missing"}},
        ]}, facts)
        any_met = evaluate({"any": [
            {"lt": [{"fact": "ga_weeks"}, 37]},
            {"exists": {"fact": "missing"}},
        ]}, facts)
        any_unknown = evaluate({"any": [
            {"gte": [{"fact": "ga_weeks"}, 37]},
            {"exists": {"fact": "missing"}},
        ]}, facts)
        self.assertEqual(all_unknown.status, "unknown")
        self.assertEqual(any_met.status, "met")
        self.assertEqual(any_unknown.status, "unknown")

    def test_conflict_is_preserved(self):
        facts = FactStore()
        facts.add("ga_weeks", 34, time="2026-04-10", path="a")
        facts.add("ga_weeks", 36, time="2026-04-10", path="b")
        result = evaluate({"lt": [{"fact": "ga_weeks"}, 37]}, facts)
        self.assertEqual(result.status, "conflict")
        self.assertEqual(evaluate({"not": {"lt": [{"fact": "ga_weeks"}, 37]}}, facts).status, "conflict")

    def test_not_recorded_is_unknown_not_false(self):
        facts = FactStore()
        facts.add("gbs_status", None, status="not_recorded", path="m")
        self.assertEqual(evaluate({"eq": [{"fact": "gbs_status"}, "positive"]}, facts).status, "unknown")

    def test_latest_value_wins_and_contains_uses_multi_values(self):
        facts = FactStore()
        facts.add("crp", 40, time="2026-04-10")
        facts.add("crp", 10, time="2026-04-12")
        self.assertEqual(evaluate({"lt": [{"fact": "crp"}, 20]}, facts).status, "met")
        facts.add("diagnosis", "新生儿肺炎", status="observed_positive", multi=True)
        self.assertEqual(evaluate({"contains": [{"fact": "diagnosis"}, "肺炎"]}, facts).status, "met")

    def test_condition_ids_combine_all(self):
        conditions = {
            "a": {"expr": {"lt": [{"fact": "ga_weeks"}, 37]}},
            "b": {"expr": {"gte": [{"fact": "ga_weeks"}, 32]}},
        }
        facts = FactStore(); facts.add("ga_weeks", 34)
        self.assertEqual(evaluate_condition_ids(["a", "b"], conditions, facts).status, "met")
        facts2 = FactStore(); facts2.add("ga_weeks", 30)
        self.assertEqual(evaluate_condition_ids(["a", "b"], conditions, facts2).status, "not_met")


class RetrievalTests(unittest.TestCase):
    def test_cpap_retrieval_has_paths_conditions_and_evidence(self):
        service = get_default_service()
        context = service.search(["CPAP", "呼吸窘迫"], snapshot())
        self.assertEqual(context["schema_version"], "knowledge-context")
        self.assertEqual(context["status"], "ok")
        cpap = next(row for row in context["recommendations"] if row["recommendation_id"] == "recommendation:DEMO-RDS-CPAP")
        self.assertEqual(cpap["direction"], "support")
        self.assertEqual(cpap["applicability"]["population"], "met")
        self.assertEqual(cpap["applicability"]["indication"], "met")
        self.assertEqual(cpap["applicability"]["exclusion"], "not_met")
        self.assertTrue(cpap["eligible_for_simulation"])
        self.assertTrue(cpap["evidence_paths"])
        self.assertTrue(cpap["evidence_span_ids"])
        panel = next(row for row in context["available_monitoring_panels"] if row["id"] == "panel:blood_gas")
        self.assertIn("ph", panel["metric_keys"])
        self.assertIn("spo2", panel["metric_keys"])

    def test_ambiguous_query_does_not_force_a_candidate(self):
        service = get_default_service()
        context = service.search(["PS"], snapshot_from_dict({"patient_ref": "empty", "as_of": "2026-04-15", "records": []}))
        self.assertEqual(context["status"], "needs_disambiguation")
        self.assertEqual(context["recommendations"], [])
        term = context["term_resolutions"][0]
        self.assertEqual(term["status"], "ambiguous")

    def test_population_mismatch_is_excluded_not_supported(self):
        service = get_default_service()
        context = service.search(["CPAP"], snapshot(ga_days=280, spo2=96))
        self.assertTrue(any(row["id"] == "recommendation:DEMO-RDS-CPAP" for row in context["excluded_recommendations"]))
        self.assertFalse(any(row["recommendation_id"] == "recommendation:DEMO-RDS-CPAP" for row in context["recommendations"]))

    def test_future_facts_do_not_enter_patient_context(self):
        service = get_default_service()
        context = service.search(["CPAP"], snapshot(future=True))
        self.assertNotIn("future", context["patient_facts"])
        self.assertNotIn("未来诊断", str(context["patient_facts"]))

    def test_missing_condition_is_partial_and_not_eligible(self):
        service = get_default_service()
        no_spo2 = snapshot(spo2=None)
        context = service.search(["CPAP"], no_spo2)
        cpap = next(row for row in context["recommendations"] if row["recommendation_id"] == "recommendation:DEMO-RDS-CPAP")
        self.assertFalse(cpap["eligible_for_simulation"])
        self.assertEqual(cpap["applicability"]["indication"], "unknown")
        self.assertTrue(cpap["missing_information"])


class ValidationTests(unittest.TestCase):
    def _context(self, **kwargs):
        service = get_default_service()
        patient = snapshot(**kwargs)
        context = service.search(["CPAP", "呼吸窘迫"], patient)
        return service, patient, context

    def _plan(self, *actions):
        return {"actions": list(actions)}

    def test_start_cpap_is_supported_when_required_conditions_met(self):
        service, patient, context = self._context()
        result = service.validate_actions(self._plan({
            "operation": "start", "target": "action:cpap", "offset_hours": 0,
            "guideline_rule_ids": ["recommendation:DEMO-RDS-CPAP"],
            "parameters": {"fio2": 0.3},
        }), patient, context["knowledge_context_id"])
        self.assertTrue(result["eligible_for_simulation"])
        self.assertEqual(result["status"], "supported")
        self.assertEqual(result["actions"][0]["status"], "supported")

    def test_continue_without_active_treatment_is_blocked(self):
        service, patient, context = self._context()
        result = service.validate_actions(self._plan({
            "operation": "continue", "target": "action:cpap", "offset_hours": 0,
            "guideline_rule_ids": ["recommendation:DEMO-RDS-CPAP"],
        }), patient, context["knowledge_context_id"])
        self.assertFalse(result["eligible_for_simulation"])
        self.assertIn("NO_ACTIVE_TREATMENT", [row["code"] for row in result["errors"]])

    def test_continue_with_active_treatment_can_pass(self):
        service, patient, context = self._context(active_cpap=True)
        result = service.validate_actions(self._plan({
            "operation": "continue", "target": "action:cpap", "offset_hours": 0,
            "guideline_rule_ids": ["recommendation:DEMO-RDS-CPAP"],
        }), patient, context["knowledge_context_id"])
        self.assertTrue(result["eligible_for_simulation"], result)

    def test_not_routine_cannot_authorize_start(self):
        service = get_default_service()
        patient = snapshot_from_dict({
            "patient_ref": "term-well", "as_of": "2026-04-15",
            "records": [
                {"collection": "patient", "event_type": "PATIENT", "record_path": "r:p",
                 "field_path": "r:p", "time": "2026-04-10",
                 "fields": {"pna_days": 2, "clinically_well": True, "data_status": "observed_positive"}},
                {"collection": "derived_birth", "event_type": "BIRTH", "record_path": "r:b",
                 "field_path": "r:b", "time": "2026-04-10",
                 "fields": {"ga_days": 280, "birth_weight_g": 3300, "data_status": "observed_positive"}},
                {"collection": "maternal", "event_type": "PREGNANCY", "record_path": "r:m",
                 "field_path": "r:m", "time": "2026-04-10",
                 "fields": {"subject_role": "mother", "gbs_status": "negative", "prom_hours": 2,
                            "data_status": "observed_positive"}},
            ],
        })
        context = service.search(["抗生素"], patient)
        self.assertIn(
            "recommendation:DEMO-EOS-NO-ROUTINE-ANTIBIOTIC",
            [row["recommendation_id"] for row in context["recommendations"]],
        )
        result = service.validate_actions(self._plan({
            "operation": "start", "target": "action:antimicrobial", "offset_hours": 0,
            "guideline_rule_ids": ["recommendation:DEMO-EOS-NO-ROUTINE-ANTIBIOTIC"],
        }), patient, context["knowledge_context_id"])
        self.assertIn("DIRECTION_MISMATCH", [row["code"] for row in result["errors"]])

    def test_unretrieved_recommendation_is_rejected(self):
        service, patient, context = self._context()
        result = service.validate_actions(self._plan({
            "operation": "start", "target": "action:cpap", "offset_hours": 0,
            "guideline_rule_ids": ["recommendation:NOT-IN-THIS-CONTEXT"],
        }), patient, context["knowledge_context_id"])
        self.assertIn("RECOMMENDATION_NOT_RETRIEVED", [row["code"] for row in result["errors"]])

    def test_parameter_outside_binding_is_rejected(self):
        service, patient, context = self._context()
        result = service.validate_actions(self._plan({
            "operation": "start", "target": "action:cpap", "offset_hours": 0,
            "guideline_rule_ids": ["recommendation:DEMO-RDS-CPAP"],
            "parameters": {"peep": 6},
        }), patient, context["knowledge_context_id"])
        self.assertIn("PARAMETER_NOT_SUPPORTED", [row["code"] for row in result["errors"]])

    def test_order_test_duplicate_is_rejected(self):
        service = get_default_service()
        patient = snapshot(gbs="positive", pending_culture=True)
        context = service.search(["血培养"], patient)
        result = service.validate_actions(self._plan({
            "operation": "order_test", "target": "action:culture", "offset_hours": 0,
            "guideline_rule_ids": ["recommendation:DEMO-BLOOD-CULTURE"],
        }), patient, context["knowledge_context_id"])
        self.assertIn("PENDING_TEST_DUPLICATE", [row["code"] for row in result["errors"]])

    def test_simultaneous_exclusive_supports_are_rejected(self):
        service, patient, context = self._context()
        result = service.validate_actions(self._plan(
            {"operation": "start", "target": "action:cpap", "offset_hours": 0,
             "guideline_rule_ids": ["recommendation:DEMO-RDS-CPAP"]},
            {"operation": "start", "target": "action:nippv", "offset_hours": 0,
             "guideline_rule_ids": ["recommendation:DEMO-RDS-NIPPV"]},
        ), patient, context["knowledge_context_id"])
        self.assertFalse(result["eligible_for_simulation"])
        self.assertTrue(result["combination_errors"])

    def test_stale_snapshot_is_rejected(self):
        service, patient, context = self._context()
        changed = snapshot(spo2=80)
        result = service.validate_actions(self._plan({
            "operation": "start", "target": "action:cpap", "offset_hours": 0,
            "guideline_rule_ids": ["recommendation:DEMO-RDS-CPAP"],
        }), changed, context["knowledge_context_id"])
        self.assertIn("SNAPSHOT_STALE", [row["code"] for row in result["errors"]])


if __name__ == "__main__":
    unittest.main()
