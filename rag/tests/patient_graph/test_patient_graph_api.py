"""HTTP contract for the local patient-graph endpoints."""
from __future__ import annotations

import json
import threading
import unittest
from http.client import HTTPConnection

from rag.patient_graph.client import PatientGraphClient
from rag.server import RAGRequestHandler, ThreadingHTTPServer


def _snapshot():
    return {
        "patient_ref": "synthetic-graph-http",
        "as_of": "2024-02-21",
        "records": [
            {"record_type": "patient", "event_type": "PATIENT", "record_id": "r1:patient",
             "event_time": "2024-02-17",
             "fields": {"gender": "女", "pna_days": 6, "data_status": "observed_positive"}},
            {"record_type": "derived_treatment", "event_type": "DERIVED_TREATMENT",
             "record_id": "r1:derived_treatment", "event_time": "2024-02-17",
             "fields": {"phototherapy": True, "phototherapy_status": "observed_positive",
                        "source_paths": ["r1:treatment_course"], "data_status": "observed_positive"}},
        ],
    }


class PatientGraphHttpTests(unittest.TestCase):
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

    def _post(self, path, payload):
        connection = HTTPConnection(self.host, self.port, timeout=10)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, data

    def test_build_graph_returns_nodes_edges_and_hash(self):
        status, payload = self._post("/api/patient-graph", {"patient_snapshot": _snapshot()})
        self.assertEqual(status, 200)
        graph = payload["data"]
        self.assertEqual(graph["patient_ref"], "synthetic-graph-http")
        self.assertGreater(graph["node_count"], 0)
        self.assertGreater(graph["edge_count"], 0)
        self.assertEqual(len(graph["snapshot_sha256"]), 64)
        self.assertNotIn("records", graph)

    def test_search_returns_hits_with_status_and_path(self):
        status, payload = self._post("/api/patient-graph/search", {
            "patient_snapshot": _snapshot(),
            "queries": ["该患儿是否接受光疗？"],
            "top_k": 5,
        })
        self.assertEqual(status, 200)
        data = payload["data"]
        self.assertTrue(data["hits"])
        self.assertEqual(data["hits"][0]["record_path"], "r1:derived_treatment")
        self.assertEqual(data["hits"][0]["status"], "observed_positive")
        self.assertTrue(data["hits"][0]["graph_path"])
        self.assertEqual(data["trace"]["engine"], "patient_graph")

    def test_empty_queries_are_rejected(self):
        status, payload = self._post("/api/patient-graph/search", {
            "patient_snapshot": _snapshot(), "queries": [],
        })
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_missing_snapshot_is_rejected(self):
        status, payload = self._post("/api/patient-graph", {})
        self.assertEqual(status, 400)

    def test_health_reports_patient_graph_engine(self):
        connection = HTTPConnection(self.host, self.port, timeout=10)
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertIn("patient_graph", payload)
        self.assertEqual(payload["patient_graph"]["engine"], "patient_graph")
        self.assertEqual(payload["patient_graph"]["contract"], "patient-snapshot")
        self.assertTrue(payload["patient_graph"]["accepts_ehr_view"])
        self.assertGreaterEqual(payload["patient_graph"]["max_nodes"], 1)


    def test_ehr_view_payload_is_accepted(self):
        status, payload = self._post("/api/patient-graph/search", {
            "patient_ref": "ehr-http",
            "as_of": "2026-01-09T07:00:00",
            "ehr_view": {
                "intake": {"gestational_weeks": 35, "gestational_days": 6,
                           "birth_weight_g": 2328, "age_hours": 21, "symptoms": ["气促"]},
                "phase": "treatment", "time_hours": 23,
                "observations": [{"event_id": "obs1", "items": [
                    {"key": "SpO2(%)", "value": 91}, {"key": "CRP(mg/L)", "value": 24}]}],
                "active_treatments": [{"target": "cpap"}],
                "pending_tests": [{"target": "culture"}],
                "conditions": [{"condition": "rds", "status": "active"}],
                "executed_actions": [{"action": "start_cpap", "at_hours": 0}],
            },
            "queries": ["CPAP", "呼吸窘迫"], "top_k": 5,
        })
        self.assertEqual(status, 200)
        data = payload["data"]
        self.assertEqual(data["graph"]["patient_ref"], "ehr-http")
        self.assertTrue(data["graph"]["snapshot_sha256"])
        self.assertTrue(data["hits"])
        self.assertEqual(data["trace"]["engine"], "patient_graph")

    def test_client_uses_ehr_view_contract(self):
        client = PatientGraphClient(f"http://{self.host}:{self.port}")
        data = client.search(
            patient_ref="ehr-client",
            as_of="2026-01-09T07:00:00",
            ehr_view={
                "intake": {"gestational_weeks": 35, "gestational_days": 6,
                           "birth_weight_g": 2328, "age_hours": 21},
                "time_hours": 23,
                "observations": [{"event_id": "obs1", "items": [{"key": "SpO2(%)", "value": 91}]}],
                "active_treatments": [{"target": "cpap"}],
            },
            queries=["CPAP"],
        )
        self.assertEqual(data["graph"]["patient_ref"], "ehr-client")
        self.assertTrue(data["hits"])
        self.assertEqual(data["trace"]["engine"], "patient_graph")


if __name__ == "__main__":
    unittest.main()
