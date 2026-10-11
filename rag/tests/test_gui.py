from __future__ import annotations

import json
import os
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from rag import __version__
from rag.server import RAGRequestHandler, ThreadingHTTPServer
from rag.tests.helpers import SYNTHETIC_HIS_SNAPSHOTS


def _document_dict(document):
    """SourceDocument -> custom-source payload record（保留全部审计字段）。"""
    payload = {
        "document_id": document.document_id, "kind": document.kind,
        "title": document.title, "text": document.text,
    }
    for field_name in ("url", "published_at", "updated_at", "locator", "pmid", "doi",
                       "authority", "version_id", "recommendation_id", "population",
                       "status", "superseded_by", "index_snapshot"):
        value = getattr(document, field_name, None)
        if value is not None:
            payload[field_name] = value
    if document.metadata:
        payload["metadata"] = dict(document.metadata)
    return payload


def _snapshot_dict(snapshot):
    from dataclasses import asdict
    return asdict(snapshot)


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
            # 联合选择轨迹、检索式自检与 HIS 患者面板
            "joint_selection", "jointBox", "selected_per_source", "guaranteed_minimums", "merit_filled_per_source",
            "query_review", "queryReviewBox", "检索式自检", "来源可见性",
            "renderPatientPanel", "his-card", "his-head", "患者快照（只读 HIS）", "已抑制未来记录",
            "来源内 #", "来源内分数", "BGE-M3", "value=\"m3\"",
            # 来源只有两种：live / custom（服务不内置语料）
            "value=\"live\"", "value=\"custom\"", "/api/sources", "customSourcePanel",
            "自定义来源库", "调用方传入数据库", "服务不内置任何本地语料",
            # 审计输入入口：citation_id 必须暴露给用户（审计回答要用它）
            "copyCitationId", "auditTemplate", "引用 ID", "插入引用模板",
            # 查询保真门控（研究特性）：选项与面板必须在页面契约中
            "id=\"fidelityGate\"", "id=\"fidelityBox\"", "查询保真门控", "query_fidelity",
            "value=\"downweight\"", "value=\"reject\"", "suspected",
        ):
            self.assertIn(text, page)
        self.assertNotIn("generateSummary", page)
        self.assertNotIn("llm_summary", page)
        # 来源模式不得再出现模拟/离线选项与旧模式值
        for legacy in ("value=\"simulated\"", "value=\"live_pubmed\"", "模拟来源"):
            self.assertNotIn(legacy, page)

    def test_handler_exposes_expected_routes(self):
        self.assertTrue(hasattr(RAGRequestHandler, "do_GET"))
        self.assertTrue(hasattr(RAGRequestHandler, "do_POST"))
        # 服务不内置语料：默认 live；custom 需先注册来源库。
        self.assertIn(RAGRequestHandler.source_mode, {"live", "custom"})
        from rag.server import CUSTOM_LIBRARY, create_tool
        self.assertFalse(CUSTOM_LIBRARY.describe())


class HttpContractTests(unittest.TestCase):
    """HTTP 契约：服务本身不内置语料；测试通过 /api/sources/custom 注册 fixtures。"""

    @classmethod
    def setUpClass(cls):
        from rag.server import CUSTOM_LIBRARY
        from rag.tests.helpers import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), RAGRequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host, cls.port = cls.server.server_address
        RAGRequestHandler.source_mode = "custom"
        CUSTOM_LIBRARY.clear()
        register = lambda payload: cls._post_json(cls, "/api/sources/custom", payload)  # noqa: E731
        register({"source_id": "test-guideline-library", "kinds": ["guideline"],
                  "version": "test-2026-10-09", "documents": [_document_dict(doc) for doc in SYNTHETIC_GUIDELINES]})
        register({"source_id": "test-pubmed-library", "kinds": ["pubmed"],
                  "version": "test-2026-10-09", "documents": [_document_dict(doc) for doc in SYNTHETIC_PUBMED]})

    @classmethod
    def tearDownClass(cls):
        from rag.server import CUSTOM_LIBRARY
        CUSTOM_LIBRARY.clear()
        RAGRequestHandler.source_mode = "live"
        RAGRequestHandler.tools.pop("custom", None)
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def _post_json(self, path, payload):
        conn = HTTPConnection(self.host, self.port, timeout=5)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        response = conn.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        conn.close()
        return response.status, data

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
        self.assertEqual(health["retrieval_mode"], "custom")
        self.assertIn("custom", health["available_modes"])
        status, tools = self.request("GET", "/api/tools")
        self.assertEqual(status, 200)
        self.assertEqual({item["name"] for item in tools["tools"]}, {"retrieve_evidence", "open_citation", "audit_grounded_answer"})

    def test_his_snapshot_endpoint(self):
        # HIS 快照由调用方随请求传入（服务不持有内置患者数据）：过去事实保留、未来事实被抑制。
        from rag.tests.helpers import SYNTHETIC_HIS_SNAPSHOTS
        payload = {"patient_snapshot": _snapshot_dict(SYNTHETIC_HIS_SNAPSHOTS["syn-patient-001"])}
        status, response = self.request("POST", "/api/his/snapshot", payload)
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["patient_ref"], "syn-patient-001")
        self.assertGreaterEqual(response["data"]["suppressed_future"], 1)
        # 缺少 patient_ref/as_of 的载荷必须显式报错，而不是静默返回空快照。
        status_bad, bad = self.request("POST", "/api/his/snapshot", {"payload": "missing fields"})
        self.assertEqual(status_bad, 400)

        status, package = self.request("POST", "/api/retrieve", {
            "request_id": "http-test", "question": "高血压指南推荐和最新证据？", "as_of": "2025-12-31",
            "source_kinds": ["pubmed", "guideline"], "patient_terms": ["高血压"],
            "english_terms": ["hypertension"], "mesh_terms": ["Hypertension"], "top_k": 4,
            "source_mode": "custom",
            "patient_snapshot": _snapshot_dict(SYNTHETIC_HIS_SNAPSHOTS["syn-patient-001"]),
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
        _, first = self.request("POST", "/api/retrieve", {"question": "hypertension", "top_k": 1, "source_mode": "custom"})
        _, second = self.request("POST", "/api/retrieve", {"question": "guideline", "top_k": 1, "source_mode": "custom"})
        status, body = self.request("POST", "/api/citation", {"retrieval_id": second["retrieval_id"], "citation_id": first["citations"][0]["citation_id"]})
        self.assertEqual(status, 400)
        self.assertIn("does not belong", body["error"])


class ScriptLaunchTests(unittest.TestCase):
    """README §7 方式二：直接跑脚本（python3 rag/server.py）也必须可用。

    脚本方式下 ``__package__`` 为空，包内相对导入会 ImportError；该错误在
    首次检索构建工具（create_tool -> _fidelity_parts）时才会触发，因此必须
    用真实 HTTP 检索请求端到端验证，而不是只检查服务能启动。
    """

    def test_script_launch_serves_retrieval(self):
        import socket
        import subprocess
        import sys
        import time

        src_root = Path(__file__).resolve().parents[2]
        server_script = Path(__file__).resolve().parents[1] / "server.py"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)  # 脚本方式不依赖 PYTHONPATH 条目
        env["RAG_SERVER_PORT"] = str(port)
        proc = subprocess.Popen(
            [sys.executable, str(server_script)],
            cwd=src_root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 8
            while time.time() < deadline:
                try:
                    conn = HTTPConnection("127.0.0.1", port, timeout=2)
                    conn.request("GET", "/api/health")
                    response = conn.getresponse()
                    response.read()
                    conn.close()
                    if response.status == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                self.fail("脚本方式启动的 RAG 实例未在预期时间内就绪")

            # custom 模式：注册调用方来源后真实检索（触发 create_tool -> _fidelity_parts）
            from rag.tests.helpers import SYNTHETIC_PUBMED
            conn = HTTPConnection("127.0.0.1", port, timeout=10)
            body = json.dumps({"source_id": "test-pubmed-library", "kinds": ["pubmed"],
                               "version": "test-2026-10-09",
                               "documents": [_document_dict(doc) for doc in SYNTHETIC_PUBMED]},
                              ensure_ascii=False).encode("utf-8")
            conn.request("POST", "/api/sources/custom", body=body,
                         headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            conn.close()

            conn = HTTPConnection("127.0.0.1", port, timeout=10)
            body = json.dumps({"question": "hypertension therapy", "top_k": 2, "source_mode": "custom"},
                              ensure_ascii=False).encode("utf-8")
            conn.request("POST", "/api/retrieve", body=body,
                         headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
            response = conn.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            conn.close()
            self.assertEqual(
                response.status, 200,
                f"脚本方式下检索失败（相对导入回归）：{payload.get('error')} {payload.get('exception_type')}",
            )
            self.assertIn("trace", payload)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)


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
