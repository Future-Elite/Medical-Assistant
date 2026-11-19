"""Pipeline/controller integration for the graph-only patient-fact path."""
from __future__ import annotations

from dataclasses import asdict
import unittest

from rag.models import RetrievalRequest
from rag.retrieval.controller import RetrievalController
from rag.tests.fixtures import SYNTHETIC_HIS_SNAPSHOTS
from rag.tests.helpers import fixture_pipeline


def _request():
    return RetrievalRequest.from_dict({
        "question": "该患儿新生儿呼吸窘迫综合征的指南推荐是什么？",
        "as_of": "2026-04-15",
        "top_k": 3,
        "english_terms": ["respiratory distress syndrome"],
        "patient_snapshot": asdict(SYNTHETIC_HIS_SNAPSHOTS["syn-neo-001"]),
    })


class PipelinePatientGraphTests(unittest.TestCase):
    def test_graph_path_adds_fields_to_hits_and_trace(self):
        package = fixture_pipeline().retrieve(_request())
        self.assertTrue(package.patient_hits)
        hit = package.patient_hits[0]
        self.assertTrue(hit.status)
        self.assertTrue(hit.concept_ids)
        self.assertTrue(hit.graph_path)
        self.assertIn(hit.match_method, {"intent", "concept", "lexical", "expansion"})
        self.assertEqual(package.trace["patient_graph"]["engine"], "patient_graph")
        self.assertTrue(package.patient_context.get("graph_snapshot_sha256"))
        payload = package.to_dict()
        self.assertIn("patient_graph", payload["trace"])
        self.assertIn("graph_path", payload["patient_hits"][0])

    def test_controller_round_zero_records_graph_engine(self):
        result = RetrievalController(fixture_pipeline()).run(_request())
        round_zero = result["rounds"][0]
        self.assertEqual(round_zero["role"], "patient_facts")
        self.assertEqual(round_zero["result"]["patient_graph_engine"], "patient_graph")
        self.assertIn(round_zero["result"]["patient_graph_status"], {"ok", "partial", "no_results"})
        self.assertGreaterEqual(round_zero["result"]["patient_graph_nodes"], 1)


if __name__ == "__main__":
    unittest.main()
