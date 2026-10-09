from __future__ import annotations

import json
import os
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from rag import __version__
from rag.server import RAGRequestHandler, ThreadingHTTPServer


class GuiContractTests(unittest.TestCase):
    def test_formal_workbench_page_and_contract_fields(self):
        page_path = Path(__file__).resolve().parents[1] / "evidence_retrieval_workbench.html"
        self.assertTrue(page_path.is_file())
        page = page_path.read_text(encoding="utf-8")
        for text in (
            "Evidence Retrieval Workbench", "id=\"searchForm\"", "id=\"englishTerms\"",
            "id=\"meshTerms\"", "id=\"guidelineVersion\"", "version_id", "recommendation_id",
            "key_evidence_sentences", "evidence_status", "patient_snapshot", "patient_hits", "evidence_groups", "/api/his/snapshot", "/api/health", "/api/retrieve", "/api/citation", "/api/audit",
            "合成", "不等于主张支持验证", "state.selectedIndex",
            "id=\"demoPreset\"", "demoPresets", "syn-patient-002", "Diabetes Mellitus, Type 2",
            "syn-patient-003", "syn-patient-004", "syn-patient-005", "syn-patient-006",
            "syn-patient-007", "新生儿黄疸", "婴儿发热", "婴儿疫苗", "Jaundice, Neonatal", "氨氯地平",
            "id=\"planQEnglish\"", "question_english_terms", "支气管哮喘", "cnquestion",
            "id=\"retrievalMethod\"", "id=\"rerankToggle\"", "methodHint", "retrieval_methods", "fusion_method",
            "id=\"auditPanel\"", "id=\"auditAnswer\"", "真实错误详情", "error_id", "exception_type",
        ):
            self.assertIn(text, page)
        self.assertNotIn("generateSummary", page)
        self.assertNotIn("llm_summary", page)

    def test_handler_exposes_expected_routes(self):
        self.assertTrue(hasattr(RAGRequestHandler, "do_GET"))
        self.assertTrue(hasattr(RAGRequestHandler, "do_POST"))
        self.assertEqual(RAGRequestHandler.tool.pipeline.registry.describe()[0]["source_id"], "guideline-fixture")


class HttpContractTests(unittest.TestCase):
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
        cls.thread.join(timeout=2)

    def request(self, method, path, payload=None):
        conn = HTTPConnection(self.host, self.port, timeout=5)
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        conn.close()
        return response.status, data

    def test_health_and_tools(self):
        status, health = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["rag_version"], __version__)
        self.assertEqual(health["retrieval_mode"], "offline_synthetic_fixture")
        status, tools = self.request("GET", "/api/tools")
        self.assertEqual(status, 200)
        self.assertEqual({item["name"] for item in tools["tools"]}, {"retrieve_evidence", "open_citation", "audit_grounded_answer"})

    def test_his_snapshot_endpoint(self):
        status, response = self.request("POST", "/api/his/snapshot", {"patient_ref": "syn-patient-001", "as_of": "2025-12-31"})
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["patient_ref"], "syn-patient-001")
        self.assertGreaterEqual(response["data"]["suppressed_future"], 1)

        status, package = self.request("POST", "/api/retrieve", {
            "request_id": "http-test", "question": "高血压指南推荐和最新证据？", "as_of": "2025-12-31",
            "source_kinds": ["pubmed", "guideline"], "patient_terms": ["高血压"],
            "english_terms": ["hypertension"], "mesh_terms": ["Hypertension"], "top_k": 4,
        })
        self.assertEqual(status, 200)
        self.assertEqual(package["trace"]["rag_version"], __version__)
        self.assertTrue(package["citations"])
        citation_id = package["citations"][0]["citation_id"]
        status, opened = self.request("POST", "/api/citation", {"retrieval_id": package["retrieval_id"], "citation_id": citation_id})
        self.assertEqual(status, 200)
        self.assertEqual(opened["citation"]["citation_id"], citation_id)
        status, audited = self.request("POST", "/api/audit", {"retrieval_id": package["retrieval_id"], "answer": f"依据当前证据[{citation_id}]。"})
        self.assertEqual(status, 200)
        self.assertIn("claims", audited)

    def test_bad_json_and_unknown_route(self):
        conn = HTTPConnection(self.host, self.port, timeout=5)
        conn.request("POST", "/api/retrieve", "not-json", {"Content-Type": "application/json"})
        response = conn.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        conn.close()
        status, body = self.request("GET", "/missing")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_cross_retrieval_citation_is_rejected(self):
        _, first = self.request("POST", "/api/retrieve", {"question": "hypertension", "top_k": 1})
        _, second = self.request("POST", "/api/retrieve", {"question": "guideline", "top_k": 1})
        status, body = self.request("POST", "/api/citation", {"retrieval_id": second["retrieval_id"], "citation_id": first["citations"][0]["citation_id"]})
        self.assertEqual(status, 400)
        self.assertIn("does not belong", body["error"])


class StaleServerCleanupTests(unittest.TestCase):
    """重启时端口被旧 RAG 实例占用的自动清退逻辑。"""

    def test_port_pids_and_identity_checks(self):
        import socket

        from rag.server import _find_port_pids, _is_rag_server_process, _process_alive

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            pids = _find_port_pids(port)
            self.assertIn(os.getpid(), pids)
        # 当前测试进程不是 RAG 服务进程：不得被识别为可清退对象（负向保护）
        self.assertFalse(_is_rag_server_process(os.getpid()))
        self.assertTrue(_process_alive(os.getpid()))
        self.assertFalse(_process_alive(99999999))

    def test_release_port_ignores_unrelated_holders(self):
        import socket

        from rag.server import _release_port

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            released = _release_port(port)
        # 无关进程（本测试进程）占用端口时：绝不终止、返回空
        self.assertEqual(released, [])

    def test_release_port_clears_real_rag_instance(self):
        import socket
        import subprocess
        import sys
        import time

        from rag.server import _find_port_pids, _release_port

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        src_root = str(Path(__file__).resolve().parents[2])
        env = dict(os.environ)
        env["PYTHONPATH"] = src_root
        env["RAG_SERVER_PORT"] = str(port)
        # 用与真实用户一致的启动方式：python -m rag.server（端口经 RAG_SERVER_PORT 指定）
        proc = subprocess.Popen(
            [sys.executable, "-m", "rag.server"],
            cwd=src_root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 8
            while time.time() < deadline and not _find_port_pids(port):
                time.sleep(0.1)
            self.assertTrue(_find_port_pids(port), "RAG 实例未在预期时间内监听端口")
            released = _release_port(port)
            self.assertIn(proc.pid, released)
            deadline = time.time() + 5
            while time.time() < deadline and _find_port_pids(port):
                time.sleep(0.1)
            self.assertEqual(_find_port_pids(port), [], "清退后端口应被释放")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
