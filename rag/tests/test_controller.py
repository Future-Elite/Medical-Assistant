"""检索控制器试运行：逐轮决策、停止条件与回放。"""
from __future__ import annotations

import unittest
from dataclasses import asdict

from rag.controller import ControllerPolicy, RetrievalController, replay_controller_result, role_for_source
from rag.models import RetrievalRequest
from rag.tests.fixtures import SYNTHETIC_HIS_SNAPSHOTS
from rag.tests.helpers import fixture_pipeline


def _request(*, english_terms=("respiratory distress syndrome",), question="该患儿新生儿呼吸窘迫综合征的指南推荐是什么？"):
    snapshot = SYNTHETIC_HIS_SNAPSHOTS["syn-neo-001"]
    return RetrievalRequest.from_dict({
        "question": question,
        "as_of": "2026-04-15",
        "top_k": 3,
        "english_terms": list(english_terms),
        "patient_snapshot": asdict(snapshot),
    })


class ControllerRunTests(unittest.TestCase):
    def test_patient_facts_first_and_rounds_are_recorded(self):
        result = RetrievalController(fixture_pipeline()).run(_request())
        self.assertEqual(result["rounds"][0]["round"], 0)
        self.assertEqual(result["rounds"][0]["role"], "patient_facts")
        self.assertGreaterEqual(result["rounds"][0]["result"]["patient_hits"], 1)
        self.assertGreaterEqual(result["final"]["rounds"], 1)
        self.assertIn(result["stop"]["reason"], {
            "sufficient_evidence", "sources_exhausted", "no_eligible_source",
            "budget_exhausted", "max_rounds", "no_positive_expected_gain",
        })
        for row in result["rounds"]:
            self.assertIn("considered", row)
            self.assertIn("state_after", row)

    def test_guideline_intent_queries_guideline_role_first(self):
        result = RetrievalController(fixture_pipeline()).run(_request())
        external = [row for row in result["rounds"] if row["role"] != "patient_facts"]
        self.assertTrue(external)
        self.assertEqual(external[0]["role"], "guideline_registry")

    def test_no_english_coverage_skips_english_sources(self):
        request = _request(english_terms=())
        result = RetrievalController(fixture_pipeline()).run(request)
        roles = [row["role"] for row in result["rounds"]]
        self.assertNotIn("pubmed", roles)
        skipped = [
            item for row in result["rounds"] for item in row["considered"]
            if item["role"] == "pubmed" and item["decision"] == "skip"
        ]
        self.assertTrue(any("无英文覆盖" in item["reason"] for item in skipped))

    def test_replay_view_matches_rounds_and_stop(self):
        result = RetrievalController(fixture_pipeline()).run(_request())
        view = replay_controller_result(result)
        self.assertEqual(len(view), len(result["rounds"]) + 1)
        self.assertEqual(view[-1]["action"], "stop")
        self.assertEqual(view[-1]["stop_after"], result["stop"]["reason"])
        self.assertEqual(result["replay"]["stop_reason"], result["stop"]["reason"])
        self.assertEqual(len(result["replay"]["decision_sequence"]), len(result["rounds"]))

    def test_same_request_is_deterministic(self):
        controller = RetrievalController(fixture_pipeline())
        first = controller.run(_request())
        second = controller.run(_request())
        self.assertEqual(
            [(row["round"], row["action"], row["role"], row["source_ids"]) for row in first["rounds"]],
            [(row["round"], row["action"], row["role"], row["source_ids"]) for row in second["rounds"]],
        )
        self.assertEqual(first["stop"]["reason"], second["stop"]["reason"])
        self.assertEqual(first["final"]["evidence_count"], second["final"]["evidence_count"])

    def test_budget_stop_is_recorded(self):
        policy = ControllerPolicy(max_cost=0.1, min_evidence=99, min_coverage=0.99)
        result = RetrievalController(fixture_pipeline(), policy=policy).run(_request())
        self.assertEqual(result["stop"]["reason"], "budget_exhausted")
        self.assertTrue(any(item["reason"] == "预算不足" for row in result["rounds"] for item in row["considered"]))

    def test_max_rounds_is_respected(self):
        policy = ControllerPolicy(max_rounds=1, min_evidence=99, min_coverage=0.99)
        result = RetrievalController(fixture_pipeline(), policy=policy).run(_request())
        self.assertLessEqual(result["final"]["rounds"], 1)
        self.assertEqual(result["stop"]["reason"], "max_rounds")


class ControllerUtilityTests(unittest.TestCase):
    def test_role_mapping(self):
        self.assertEqual(role_for_source("pubmed-live", ("pubmed",)), "pubmed")
        self.assertEqual(role_for_source("guideline-fulltext-live", ("guideline",)), "europe_pmc")
        self.assertEqual(role_for_source("guideline-pdf-local", ("guideline",)), "authorized_pdf")
        self.assertEqual(role_for_source("neonatal-guideline-metadata", ("guideline",)), "guideline_registry")
        self.assertEqual(role_for_source("custom-pubmed-library", ("pubmed",)), "pubmed")

    def test_only_source_ids_restricts_connectors_without_kind_filters(self):
        pipeline = fixture_pipeline()
        package = pipeline.retrieve(_request(), only_source_ids=("guideline-fixture",))
        self.assertTrue(package.ranked_documents)
        self.assertTrue(all(item.document.source_id == "guideline-fixture" for item in package.ranked_documents))
        self.assertFalse(any(item.document.source_id == "pubmed-fixture" for item in package.ranked_documents))


if __name__ == "__main__":
    unittest.main()
