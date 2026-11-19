"""HTTP contract for the EHR-style knowledge graph endpoints."""
from __future__ import annotations

import json
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from rag.server import RAGRequestHandler, ThreadingHTTPServer

# 仓库级 data/ 不随发布分支分发；缺真实指南数据时跳过依赖它的集成用例。
_DATA = Path(__file__).resolve().parents[3] / "data"
_HAS_GUIDELINE_DATA = (_DATA / "guidelines" / "guideline_pdfs.json").is_file()
_HAS_GUIDELINE_GRAPHS = (
    _DATA / "generated" / "validation" / "guideline_graph_library.json"
).is_file()


def _snapshot():
    return {
        "patient_ref": "knowledge-http-demo",
        "as_of": "2026-04-15",
        "records": [
            {"collection": "patient", "event_type": "PATIENT", "record_path": "r:p",
             "field_path": "r:p", "time": "2026-04-10",
             "fields": {"pna_days": 2, "data_status": "observed_positive"}},
            {"collection": "derived_birth", "event_type": "BIRTH", "record_path": "r:b",
             "field_path": "r:b", "time": "2026-04-10",
             "fields": {"ga_days": 238, "birth_weight_g": 2100, "data_status": "observed_positive"}},
            {"collection": "diagnoses", "event_type": "DIAG", "record_path": "r:d",
             "field_path": "r:d", "time": "2026-04-10",
             "fields": {"name": "新生儿呼吸窘迫综合征", "data_status": "observed_positive"}},
            {"collection": "labs", "event_type": "LAB", "record_path": "r:l",
             "field_path": "r:l", "time": "2026-04-10",
             "fields": {"spo2_percent": 88, "ph": 7.25, "wbc": 18, "platelet": 220,
                        "hemoglobin": 170, "crp": 5, "sodium": 138,
                        "data_status": "observed_positive"}},
            {"collection": "diagnoses", "event_type": "SYMPTOM", "record_path": "r:no-apnea",
             "field_path": "r:no-apnea", "time": "2026-04-10",
             "fields": {"symptom": ["呼吸暂停"], "data_status": "observed_negative"}},
        ],
    }


class KnowledgeHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), RAGRequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host, cls.port = cls.server.server_address

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _request(self, method, path, payload=None):
        connection = HTTPConnection(self.host, self.port, timeout=10)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, data

    def test_stats_and_release_are_exposed(self):
        status, stats = self._request("GET", "/api/knowledge")
        self.assertEqual(status, 200)
        self.assertGreater(stats["recommendations"], 0)
        self.assertFalse(stats["publishable"])

        status, releases = self._request("GET", "/api/knowledge/releases")
        self.assertEqual(status, 200)
        release = releases["releases"][0]
        self.assertEqual(release["review_status"], "synthetic_fixture")
        self.assertEqual(len(release["content_sha256"]), 64)
        self.assertEqual(release["status"], "draft")

        status, denied = self._request("POST", "/api/knowledge/releases/publish", {
            "release_id": release["release_id"],
        })
        self.assertEqual(status, 400)
        self.assertIn("publishable", denied["error"])

    def test_graph_and_evidence_are_exposed(self):
        status, graph = self._request("GET", "/api/knowledge/graph")
        self.assertEqual(status, 200)
        self.assertIn("nodes", graph)
        self.assertTrue(graph["edges"])

        status, evidence = self._request("GET", "/api/knowledge/evidence/span:rds-cpap")
        self.assertEqual(status, 200)
        self.assertEqual(evidence["span"]["span_id"], "span:rds-cpap")
        self.assertTrue(evidence["source"]["source_id"])

        status, missing = self._request("GET", "/api/knowledge/evidence/span:missing")
        self.assertEqual(status, 404)
        self.assertIn("error", missing)

    def test_search_and_validate_round_trip(self):
        status, context = self._request("POST", "/api/knowledge/search", {
            "patient_snapshot": _snapshot(),
            "queries": ["CPAP", "呼吸窘迫"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(context["status"], "ok")
        self.assertTrue(context["recommendations"])
        self.assertTrue(context["knowledge_context_id"])

        status, result = self._request("POST", "/api/knowledge/actions/validate", {
            "patient_snapshot": _snapshot(),
            "knowledge_context_id": context["knowledge_context_id"],
            "candidate_plan": {"actions": [{
                "operation": "start", "target": "action:cpap", "offset_hours": 0,
                "guideline_rule_ids": ["recommendation:DEMO-RDS-CPAP"],
            }]},
        })
        self.assertEqual(status, 200)
        self.assertTrue(result["eligible_for_simulation"], result)
        self.assertEqual(result["status"], "supported")
        self.assertEqual(result["schema_version"], "action-validation")
        self.assertTrue(result["timeline"])

    def test_terms_search_subgraph_releases_and_audit(self):
        status, resolved = self._request("POST", "/api/knowledge/terms/resolve", {
            "terms": ["SpO₂", "nCPAP", "PS"],
        })
        self.assertEqual(status, 200)
        terms = {row["input"]: row for row in resolved["terms"]}
        self.assertEqual(terms["SpO₂"]["status"], "resolved")
        self.assertEqual(terms["nCPAP"]["status"], "resolved")
        self.assertEqual(terms["PS"]["status"], "ambiguous")
        self.assertTrue(terms["PS"]["needed_context"])

        status, context = self._request("POST", "/api/knowledge/search", {
            "patient_snapshot": _snapshot(), "queries": ["CPAP", "呼吸窘迫"], "intent": "start",
        })
        self.assertEqual(status, 200)
        self.assertEqual(context["diagnostics"]["intent"], "start")
        self.assertTrue(context["retrieval_id"])
        self.assertTrue(context["effective_queries"])
        self.assertIn("setting", context["recommendations"][0]["applicability"])
        self.assertTrue(context["recommended_monitoring_panels"])

        status, result = self._request("POST", "/api/knowledge/actions/validate", {
            "patient_snapshot": _snapshot(),
            "knowledge_context_id": context["knowledge_context_id"],
            "candidate_plan": {"actions": [{
                "operation": "start", "target": "action:cpap", "offset_hours": 0,
                "guideline_rule_ids": ["recommendation:DEMO-RDS-CPAP"],
            }]},
        })
        self.assertEqual(status, 200)
        self.assertTrue(result["eligible_for_simulation"], result)

        status, subgraph = self._request("GET", "/api/knowledge/subgraph?recommendation_id=recommendation:DEMO-RDS-CPAP&limit=40")
        self.assertEqual(status, 200)
        self.assertTrue(subgraph["nodes"])
        self.assertTrue(subgraph["edges"])
        self.assertFalse(subgraph["truncated"])

        status, releases = self._request("GET", "/api/knowledge/releases")
        self.assertEqual(status, 200)
        release = releases["releases"][0]
        self.assertTrue(release["coverage"])
        self.assertTrue(release["dependencies"])

        status, detail = self._request("GET", f"/api/knowledge/releases/{release['release_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["release_id"], release["release_id"])

        status, audit = self._request("GET", "/api/knowledge/audit?limit=10")
        self.assertEqual(status, 200)
        self.assertGreater(audit["count"], 0)
        self.assertTrue(audit["events"][-1]["retrieval_id"])

    def test_ehr_view_payload_is_accepted(self):
        status, context = self._request("POST", "/api/knowledge/search", {
            "patient_ref": "ehr-knowledge",
            "as_of": "2026-01-09T07:00:00",
            "ehr_view": {
                "intake": {"gestational_weeks": 35, "gestational_days": 6,
                           "birth_weight_g": 2328, "age_hours": 21},
                "time_hours": 23,
                "observations": [{"event_id": "obs1", "items": [
                    {"key": "SpO2(%)", "value": 91}, {"key": "pH", "value": 7.36}]}],
                "active_treatments": [{"target": "cpap"}],
                "conditions": [{"condition": "rds", "status": "active"}],
            },
            "queries": ["CPAP", "呼吸窘迫"],
        })
        self.assertEqual(status, 200)
        self.assertTrue(context["knowledge_context_id"])
        self.assertTrue(context["recommendations"])

    @unittest.skipUnless(_HAS_GUIDELINE_GRAPHS, "guideline graph library is not shipped with the release checkout")
    def test_real_guideline_graph_library_is_exposed(self):
        status, summary = self._request("GET", "/api/knowledge/guideline-graphs")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(summary["stats"]["guidelines"], 1)
        self.assertFalse(summary["publishable"])
        guideline = summary["guidelines"][0]["guideline_id"]
        status, graph = self._request(
            "GET", f"/api/knowledge/guideline-graphs/{guideline}")
        self.assertEqual(status, 200)
        self.assertEqual(graph["guideline_id"], guideline)
        self.assertTrue(graph["nodes"])
        self.assertEqual(graph["validation"]["errors"], [])

    def test_search_fusion_flag(self):
        status, fused = self._request("POST", "/api/knowledge/search", {
            "patient_snapshot": _snapshot(), "queries": ["CPAP", "呼吸窘迫"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(fused["fusion"]["schema_version"], "bidirectional-graph-fusion")
        self.assertGreaterEqual(fused["fusion"]["forward"]["hits"], 1)
        status, plain = self._request("POST", "/api/knowledge/search", {
            "patient_snapshot": _snapshot(), "queries": ["CPAP", "呼吸窘迫"], "fusion": False,
        })
        self.assertEqual(status, 200)
        self.assertNotIn("fusion", plain)

    def test_agentic_iterative_retrieval_endpoint(self):
        status, payload = self._request("POST", "/api/knowledge/iterative-retrieve", {
            "patient_snapshot": _snapshot(),
            "queries": ["CPAP", "呼吸窘迫"],
            "intent": "start",
            "max_rounds": 2,
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema_version"], "agentic-iterative-retrieval")
        self.assertTrue(payload["rounds"])
        self.assertIn(payload["status"], {"sufficient", "needs_review", "not_applicable",
                                          "needs_information", "needs_disambiguation"})
        self.assertTrue(payload["recommendations"])
        self.assertTrue(payload["clinical_schema"]["intent"])

    @unittest.skipUnless(_HAS_GUIDELINE_DATA, "guideline data/ is not shipped with the release checkout")
    def test_agentic_iterative_retrieval_uses_selected_sources(self):
        status, payload = self._request("POST", "/api/knowledge/iterative-retrieve", {
            "patient_snapshot": _snapshot(),
            "queries": ["CPAP", "呼吸窘迫"],
            "intent": "start",
            "max_rounds": 1,
            "source_kinds": ["guideline"],
        })
        self.assertEqual(status, 200)
        self.assertGreater(payload["external_source_counts"].get("guideline", 0), 0)
        self.assertTrue(payload["external_documents"])
        self.assertGreater(payload["rounds"][0]["external"]["documents"], 0)
        self.assertEqual(payload["gap_analysis"]["missing_sources"], [])

    def test_review_plan_and_episode_end(self):
        status, context = self._request("POST", "/api/knowledge/search", {
            "patient_snapshot": _snapshot(),
            "queries": ["CPAP", "呼吸窘迫"],
        })
        self.assertEqual(status, 200)
        context_id = context["knowledge_context_id"]
        status, review = self._request("POST", "/api/knowledge/review-plan", {
            "knowledge_context_id": context_id,
            "review_panels": ["panel:blood_gas", "panel:cbc"],
        })
        self.assertEqual(status, 200)
        self.assertIn("ph", review["review_metrics"])
        self.assertIn("wbc", review["review_metrics"])

        status, gate = self._request("POST", "/api/knowledge/episode-end", {
            "knowledge_context_id": context_id,
        })
        self.assertEqual(status, 200)
        self.assertFalse(gate["allowed"])
        self.assertIn("不自动", gate["reason"])

    def test_invalid_queries_are_rejected(self):
        status, payload = self._request("POST", "/api/knowledge/search", {
            "patient_snapshot": _snapshot(), "queries": [],
        })
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_unknown_context_is_rejected(self):
        status, payload = self._request("POST", "/api/knowledge/actions/validate", {
            "patient_snapshot": _snapshot(),
            "knowledge_context_id": "does-not-exist",
            "candidate_plan": {"actions": [{
                "operation": "start", "target": "action:cpap", "offset_hours": 0,
            }]},
        })
        self.assertEqual(status, 400)
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
