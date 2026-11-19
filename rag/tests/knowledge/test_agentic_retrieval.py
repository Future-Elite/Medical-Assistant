"""Agentic iterative retrieval contract tests."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from rag.core.his import snapshot_from_dict
from rag.retrieval.agentic import AgenticBudget, AgenticIterativeRetriever

FIXTURE = Path(__file__).resolve().parents[2] / "knowledge" / "fixtures" / "fusion_evaluation_set.json"


def _scenario(scenario_id: str) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return next(row for row in payload["scenarios"] if row["id"] == scenario_id)


def _snapshot(scenario_id: str):
    return snapshot_from_dict({"patient_snapshot": _scenario(scenario_id)["snapshot"]})


class AgenticIterativeTests(unittest.TestCase):
    def test_sufficient_stops_in_one_round(self):
        scenario = _scenario("FUS-001")
        result = AgenticIterativeRetriever().run(
            scenario["queries"], _snapshot("FUS-001"), intent=scenario["intent"])
        self.assertEqual(result["status"], "sufficient")
        self.assertEqual(result["stop_reason"], "sufficient")
        self.assertEqual(len(result["rounds"]), 1)
        self.assertTrue(result["sufficient"])
        cpap = next(row for row in result["recommendations"]
                    if row["recommendation_id"] == "recommendation:DEMO-RDS-CPAP")
        self.assertTrue(cpap["patient_graph_evidence"])

    def test_excluded_target_is_not_applicable(self):
        scenario = _scenario("FUS-003")
        result = AgenticIterativeRetriever().run(
            scenario["queries"], _snapshot("FUS-003"), intent=scenario["intent"])
        self.assertEqual(result["status"], "not_applicable")
        self.assertIn("recommendation:DEMO-RDS-CPAP",
                      {row["id"] for row in result["excluded_recommendations"]})

    def test_missing_fact_triggers_gap_and_needs_information(self):
        scenario = _scenario("FUS-002")
        result = AgenticIterativeRetriever().run(
            scenario["queries"], _snapshot("FUS-002"), intent=scenario["intent"])
        self.assertEqual(result["status"], "needs_information")
        self.assertGreaterEqual(len(result["rounds"]), 2)
        self.assertIn("临床指征仍有未知项", " ".join(result["missing_information"]))
        self.assertTrue(result["gap_analysis"]["next_queries"] or result["rounds"][-1]["queries"])

    def test_direction_conflict_requires_review(self):
        scenario = _scenario("FUS-007")
        result = AgenticIterativeRetriever().run(
            scenario["queries"], _snapshot("FUS-007"), intent=scenario["intent"])
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(result["gap_analysis"]["critical_conflicts"])

    def test_initial_ambiguity_is_not_silently_resolved(self):
        scenario = _scenario("FUS-017")
        result = AgenticIterativeRetriever().run(
            scenario["queries"], _snapshot("FUS-017"), intent=scenario["intent"])
        self.assertEqual(result["status"], "needs_disambiguation")
        self.assertIn("PS", result["clinical_schema"]["initial_ambiguous"])

    def test_external_sources_are_absorbed_per_round(self):
        scenario = _scenario("FUS-001")

        def external(queries, source_kinds):
            return {
                "documents": [
                    {"source_id": "pubmed-live", "document_id": "pmid:1", "kind": "pubmed", "title": "PubMed item", "text": "abstract"},
                    {"source_id": "drug-openfda", "document_id": "openfda:1", "kind": "drug", "title": "Drug label", "text": "label"},
                ],
                "source_counts": {"pubmed": 1, "drug": 1},
                "warnings": [],
            }

        result = AgenticIterativeRetriever().run(
            scenario["queries"], _snapshot("FUS-001"), intent=scenario["intent"],
            source_kinds=["pubmed", "drug"], external_search=external,
        )
        self.assertEqual(result["external_source_counts"], {"pubmed": 1, "drug": 1})
        self.assertEqual(len(result["external_documents"]), 2)
        self.assertEqual(result["rounds"][0]["external"]["documents"], 2)
        self.assertEqual(result["rounds"][0]["external"]["source_counts"], {"pubmed": 1, "drug": 1})
        self.assertEqual(result["gap_analysis"]["missing_sources"], [])

    def test_missing_external_source_is_reported(self):
        scenario = _scenario("FUS-001")

        def external(queries, source_kinds):
            return {
                "documents": [{"source_id": "pubmed-live", "document_id": "pmid:1", "kind": "pubmed", "title": "PubMed item", "text": "abstract"}],
                "source_counts": {"pubmed": 1},
                "warnings": [],
            }

        result = AgenticIterativeRetriever().run(
            scenario["queries"], _snapshot("FUS-001"), intent=scenario["intent"],
            source_kinds=["pubmed", "drug"], external_search=external,
        )
        self.assertEqual(result["gap_analysis"]["missing_sources"], ["drug"])

    def test_budget_and_single_shot_are_reproducible(self):
        scenario = _scenario("FUS-002")
        single = AgenticIterativeRetriever(budget=AgenticBudget(max_rounds=1)).run(
            scenario["queries"], _snapshot("FUS-002"), intent=scenario["intent"])
        self.assertEqual(len(single["rounds"]), 1)
        self.assertNotEqual(single["stop_reason"], "needs_information")


if __name__ == "__main__":
    unittest.main()
