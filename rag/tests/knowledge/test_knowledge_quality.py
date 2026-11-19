"""Knowledge-graph validation contract tests.

These tests exercise the structural/provenance/CQ validator and the release
gate.  They use the shipped synthetic fixture; passing them is contract
evidence, not clinical validation.
"""
from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path

from rag.core.his import snapshot_from_dict
from rag.knowledge import (
    KnowledgeGraph,
    KnowledgeReleaseRegistry,
    ReleaseLifecycleError,
    get_default_service,
    validate_knowledge_graph,
)

FIXTURE = Path(__file__).resolve().parents[2] / "knowledge" / "fixtures" / "neonatal_graph_rag_demo.json"
CQ_FIXTURE = Path(__file__).resolve().parents[2] / "knowledge" / "fixtures" / "competency_questions.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _reviewed_payload() -> dict:
    payload = copy.deepcopy(_payload())
    payload["release"]["publishable"] = True
    payload["release"]["review_status"] = "reviewed"
    for source in payload["sources"]:
        source["review_status"] = "reviewed"
    for span in payload["spans"]:
        span["review_status"] = "reviewed"
    return payload


class KnowledgeQualityValidationTests(unittest.TestCase):
    def test_validation_report_meets_goal(self):
        report = validate_knowledge_graph()
        self.assertTrue(report["goal"]["met"], report["goal"])
        self.assertEqual(report["sections"]["structure"]["errors"], 0)
        self.assertEqual(report["sections"]["provenance"]["errors"], 0)
        self.assertEqual(report["sections"]["patient_graph"]["errors"], 0)
        self.assertEqual(report["sections"]["competency_questions"]["pass_rate"], 1.0)
        self.assertGreaterEqual(report["sections"]["quality_dimensions"]["score"], 0.9)
        self.assertGreater(report["sections"]["patient_graph"]["cases"], 0)
        self.assertGreater(report["sections"]["patient_graph"]["records"], 0)

    def test_all_cq_ids_are_unique_and_present(self):
        payload = json.loads(CQ_FIXTURE.read_text(encoding="utf-8"))
        ids = [row["id"] for row in payload["questions"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 30)

    def test_evidence_paths_use_real_edge_directions(self):
        graph = KnowledgeGraph(_payload())
        self.assertGreater(len(graph.edges), 0)
        edges = {(edge["source"], edge["relation"], edge["target"]) for edge in graph.edges}
        cqs = json.loads(CQ_FIXTURE.read_text(encoding="utf-8"))
        snapshot = snapshot_from_dict({"patient_snapshot": cqs["snapshots"]["s_rds_preterm_hypox"]})
        context = get_default_service().search(["CPAP", "呼吸窘迫"], snapshot)
        self.assertTrue(context["recommendations"])
        for recommendation in context["recommendations"]:
            for path in recommendation.get("evidence_paths") or ():
                self.assertTrue(path)
                for step in path:
                    match = re.fullmatch(r"(.+?) -\[(.+?)\]-> (.+)", step)
                    self.assertIsNotNone(match, step)
                    self.assertIn((match.group(1), match.group(2), match.group(3)), edges, step)

    def test_unknown_condition_fact_is_a_structure_error(self):
        payload = _payload()
        payload["populations"][0]["expr"] = {"lt": [{"fact": "unknown_fact"}, 37]}
        report = validate_knowledge_graph(KnowledgeGraph(payload))
        codes = {issue["code"] for issue in report["summary"]["issues"]}
        self.assertIn("CONDITION_UNKNOWN_FACT", codes)
        self.assertFalse(report["goal"]["met"])

    def test_conflict_and_unknown_exclusion_are_surfaced(self):
        from rag.knowledge import get_default_service

        service = get_default_service()
        cqs = json.loads(CQ_FIXTURE.read_text(encoding="utf-8"))
        conflict = cqs["snapshots"]["s_rds_conflict"]
        conflict_context = service.search(
            ["CPAP"], snapshot_from_dict({"patient_snapshot": conflict})
        )
        self.assertIn("临床指征存在冲突值", conflict_context["missing_information"])
        missing_apnea = cqs["snapshots"]["s_rds_missing_apnea"]
        missing_context = service.search(
            ["CPAP"], snapshot_from_dict({"patient_snapshot": missing_apnea})
        )
        self.assertIn("排除条件未知", missing_context["missing_information"])


class ReleaseGateTests(unittest.TestCase):
    def test_reviewed_release_publishes(self):
        graph = KnowledgeGraph(_reviewed_payload())
        registry = KnowledgeReleaseRegistry([graph])
        result = registry.publish(graph.release.release_id)
        self.assertEqual(result["status"], "published")
        self.assertIs(registry.active(), graph)

    def test_unreviewed_span_blocks_publish(self):
        payload = _reviewed_payload()
        payload["spans"][0]["review_status"] = "draft"
        graph = KnowledgeGraph(payload)
        with self.assertRaises(ReleaseLifecycleError):
            KnowledgeReleaseRegistry([graph]).publish(graph.release.release_id)

    def test_unreviewed_source_blocks_publish(self):
        payload = _reviewed_payload()
        payload["sources"][0]["review_status"] = "synthetic_fixture"
        graph = KnowledgeGraph(payload)
        with self.assertRaises(ReleaseLifecycleError):
            KnowledgeReleaseRegistry([graph]).publish(graph.release.release_id)

    def test_missing_locator_blocks_publish(self):
        payload = _reviewed_payload()
        payload["spans"][0]["locator"] = ""
        graph = KnowledgeGraph(payload)
        with self.assertRaises(ReleaseLifecycleError):
            KnowledgeReleaseRegistry([graph]).publish(graph.release.release_id)

    def test_retracted_source_blocks_publish(self):
        payload = _reviewed_payload()
        payload["sources"][0]["document_status"] = "retracted"
        graph = KnowledgeGraph(payload)
        with self.assertRaises(ReleaseLifecycleError):
            KnowledgeReleaseRegistry([graph]).publish(graph.release.release_id)

    def test_missing_license_blocks_publish(self):
        payload = _reviewed_payload()
        payload["sources"][0]["license_note"] = ""
        payload["sources"][0]["storage_policy"] = ""
        graph = KnowledgeGraph(payload)
        with self.assertRaises(ReleaseLifecycleError):
            KnowledgeReleaseRegistry([graph]).publish(graph.release.release_id)


if __name__ == "__main__":
    unittest.main()
