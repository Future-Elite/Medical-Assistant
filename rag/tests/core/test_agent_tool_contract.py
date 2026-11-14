"""Agent JSON boundary and citation ownership tests."""

from __future__ import annotations

import unittest

from rag.agent import EvidenceRAG
from rag.eval.qrels.decision_triage import apply_value_to_snapshot
from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED
from rag.pipeline import EvidenceRetrievalPipeline


def _pipeline() -> EvidenceRetrievalPipeline:
    registry = ConnectorRegistry()
    registry.register(VersionedGuidelineConnector("guideline-fixture", SYNTHETIC_GUIDELINES))
    registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
    return EvidenceRetrievalPipeline(registry)


class TestAgentTools(unittest.TestCase):
    def test_retrieve_then_open_its_own_citation(self) -> None:
        tool = EvidenceRAG(_pipeline())
        result = tool.retrieve_evidence({
            "question": "新生儿呼吸窘迫综合征的治疗指南推荐什么？", "as_of": "2026-04-15",
            "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]},
        })
        opened = tool.open_citation({"retrieval_id": result["retrieval_id"], "citation_id": result["citations"][0]["citation_id"]})
        self.assertIn("查看原文证据", opened["citation"]["actions"][0]["label"])
        self.assertTrue(opened["citation"]["key_evidence_sentences"])
        self.assertIn("start", opened["citation"]["key_evidence_sentences"][0])

    def test_citation_cannot_cross_retrieval_boundaries(self) -> None:
        tool = EvidenceRAG(_pipeline())
        first = tool.retrieve_evidence({
            "question": "新生儿呼吸窘迫综合征指南", "as_of": "2026-04-15",
            "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]},
        })
        second = tool.retrieve_evidence({
            "question": "新生儿呼吸窘迫综合征指南推荐", "as_of": "2026-04-15",
            "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]},
        })
        with self.assertRaises(ValueError):
            tool.open_citation({"retrieval_id": second["retrieval_id"], "citation_id": first["citations"][0]["citation_id"]})

    def test_resolve_guideline_applicability_returns_card(self) -> None:
        snapshot = {
            "patient_ref": "synthetic-1",
            "as_of": "2026-01-08",
            "facts": [
                {"collection": "derived_birth", "fields": {
                    "ga_days": 224, "ga_days_range": [224, 230], "ga_precision": "week",
                    "birth_weight_g": 1400, "pna_days": 3, "data_status": "observed_positive",
                }},
                {"collection": "derived_respiratory_support", "fields": {
                    "mode": "CPAP", "data_status": "observed_positive", "mapping_status": "mapped",
                }},
            ],
        }
        card = EvidenceRAG.resolve_guideline_applicability({
            "patient_snapshot": snapshot,
            "target_guideline_id": "pdf-neo-feeding-vlbw-2015",
        })
        self.assertEqual(card["target"]["guideline_id"], "pdf-neo-feeding-vlbw-2015")
        self.assertIn("target_stop", card)
        self.assertIn("next_step", card)

    def test_resolve_rejects_empty_snapshot(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceRAG.resolve_guideline_applicability({"patient_snapshot": {}})

    def test_agent_tool_closed_loop_after_supplying_fact(self) -> None:
        snapshot = {
            "patient_ref": "synthetic-2",
            "as_of": "2026-01-08",
            "facts": [
                {"collection": "derived_birth", "fields": {
                    "ga_days": None, "ga_days_range": None, "ga_precision": None,
                    "birth_weight_g": 1400, "pna_days": 3, "data_status": "observed_positive",
                }},
                {"collection": "derived_respiratory_support", "fields": {
                    "mode": "CPAP", "data_status": "observed_positive", "mapping_status": "mapped",
                }},
            ],
        }
        arguments = {
            "patient_snapshot": snapshot,
            "target_guideline_id": "pdf-neo-human-milk-fortification-2019",
        }
        card = EvidenceRAG.resolve_guideline_applicability(arguments)
        self.assertTrue(card["target_priorities"])
        top = card["target_priorities"][0]
        modified = apply_value_to_snapshot(snapshot, top["field"], 224)
        after = EvidenceRAG.resolve_guideline_applicability({
            "patient_snapshot": modified,
            "target_guideline_id": "pdf-neo-human-milk-fortification-2019",
        })
        self.assertNotEqual(after["target"]["status"], "unknown")
        self.assertTrue(after["target_stop"])

    def test_executable_resolver_requests_and_resumes_same_resolution(self) -> None:
        snapshot = {
            "patient_ref": "synthetic-3",
            "as_of": "2026-01-08",
            "facts": [
                {"collection": "derived_birth", "fields": {
                    "birth_weight_g": 1400, "pna_days": 3, "data_status": "observed_positive",
                }},
            ],
        }
        tool = EvidenceRAG(_pipeline(), applicability_search_fn=lambda query: [])
        first = tool.resolve_applicability_workflow({
            "patient_snapshot": snapshot,
            "question": "人乳强化指南是否适用？",
            "target_guideline_id": "pdf-neo-human-milk-fortification-2019",
        })
        self.assertEqual(first["outcome"], "needs_information")
        self.assertTrue(first["information_requests"])
        field = first["information_requests"][0]["field"]
        second = tool.resolve_applicability_workflow({
            "resolution_id": first["resolution_id"],
            "supplied_facts": [{"field": field, "value": 224}],
        })
        self.assertEqual(second["outcome"], "resolved")
        self.assertEqual(second["target"]["status"], "possible_match")
        self.assertTrue(second["resumed"])

    def test_executable_resolver_executes_external_search(self) -> None:
        snapshot = {
            "patient_ref": "synthetic-4",
            "as_of": "2026-01-08",
            "facts": [
                {"collection": "derived_birth", "fields": {
                    "ga_days": 266, "birth_weight_g": 3200, "pna_days": 5,
                    "data_status": "observed_positive",
                }},
            ],
        }
        queries = []

        def fake_search(query):
            queries.append(query)
            return [{
                "title": "Preterm parenteral nutrition guideline",
                "abstract": "preterm infants parenteral nutrition",
                "pmid": "12345",
            }]

        tool = EvidenceRAG(_pipeline(), applicability_search_fn=fake_search)
        result = tool.resolve_applicability_workflow({
            "patient_snapshot": snapshot,
            "question": "肠外营养指南是否适用？",
            "target_guideline_id": "pdf-neo-parenteral-nutrition-2012",
        })
        self.assertEqual(result["outcome"], "scope_mismatch")
        self.assertEqual(len(queries), 1)
        self.assertEqual(result["evidence"][0]["pmid"], "12345")
        self.assertTrue(result["external_search"]["executed"])
        self.assertEqual(result["next_action"]["type"], "scope_mismatch")

    def test_executable_resolver_without_backend_does_not_fake_evidence(self) -> None:
        snapshot = {
            "patient_ref": "synthetic-5",
            "as_of": "2026-01-08",
            "facts": [
                {"collection": "derived_birth", "fields": {
                    "ga_days": 266, "birth_weight_g": 3200, "pna_days": 5,
                    "data_status": "observed_positive",
                }},
            ],
        }
        tool = EvidenceRAG(_pipeline())
        result = tool.resolve_applicability_workflow({
            "patient_snapshot": snapshot,
            "question": "肠外营养指南是否适用？",
            "target_guideline_id": "pdf-neo-parenteral-nutrition-2012",
            "execute_external": False,
        })
        self.assertEqual(result["outcome"], "scope_mismatch")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["rounds"][-1]["reason"], "search_backend_not_provided")

    def test_tool_schema_has_all_tools(self) -> None:
        self.assertEqual(
            {item["name"] for item in EvidenceRAG.tool_specifications()},
            {"retrieve_evidence", "open_citation", "audit_grounded_answer", "resolve_guideline_applicability", "resolve_applicability_workflow"},
        )


if __name__ == "__main__":
    unittest.main()
