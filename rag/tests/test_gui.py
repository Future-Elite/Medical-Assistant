from __future__ import annotations

import json
import os
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from rag import __version__
from rag.server import RAGRequestHandler, ThreadingHTTPServer


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
    """GUI 契约：固定分区工作台，输入字段与 RetrievalRequest 一一对应。"""

    REQUIRED = (
        # 页面骨架与固定分区
        "新生儿证据检索工作流", "id=\"workspace\"", "workgrid", "workflow-strip",
        "intro-banner", "固定工作台", "所有分区固定排列", "id=\"workflowStrip\"",
        "data-node=\"case\"", "data-node=\"question\"", "data-node=\"terms\"",
        "data-node=\"scope\"", "data-node=\"method\"", "data-node=\"request\"",
        "data-node=\"run\"", "data-node=\"results\"", "data-node=\"audit\"",
        "检索配置", "▶ 执行检索", "id=\"jumpResults\"",
        # 01 快照
        "/api/his/snapshot", "id=\"demoCase\"", "id=\"patientRef\"", "id=\"asOf\"",
        "id=\"loadSnapshot\"", "patient_snapshot", "synthetic",
        # 02 问题
        "id=\"question\"", "id=\"task\"", "id=\"queryLanguage\"", "id=\"clinicalIntent\"",
        # 03 术语
        "id=\"patientTerms\"", "id=\"termMapping\"", "id=\"termMappingPreview\"",
        "id=\"facets\"", "question_english_terms", "不自动翻译",
        "term_mapping", "reviewed_term_mapping",
        # 04 范围
        "id=\"sourceMode\"", "id=\"topK\"", "id=\"filters\"", "source_kinds",
        "value=\"custom\"", "value=\"live\"", "服务不内置任何本地语料",
        # 05 方法
        "id=\"retrievalMethod\"", "id=\"rerankToggle\"", "id=\"fidelityGate\"",
        "value=\"downweight\"", "value=\"reject\"", "BGE-M3",
        # 06-07 请求与执行
        "id=\"requestPreview\"", "RetrievalRequest", "id=\"runButton\"", "/api/retrieve",
        # 08-09 结果与审计
        "id=\"resultList\"", "id=\"detailBody\"", "id=\"sourceGroups\"", "evidence_groups",
        "key_evidence_sentences", "id=\"auditAnswer\"",
        "/api/citation", "/api/audit", "id=\"auditButton\"", "copyCitationId",
        "renderPatientPanel", "his-card", "joint_selection", "query_review",
        "query_fidelity", "id=\"fidelityBox\"", "id=\"jointBox\"", "id=\"queryReviewBox\"",
        "id=\"applicabilityBox\"", "renderApplicability", "applicability", "criterion_id",
        # 演示与来源
        "/api/demo/cases", "/api/demo/guidelines", "/api/demo/guidelines/load",
        "/api/sources", "id=\"loadDemoGuidelines\"", "id=\"clearSources\"",
        "检索失败", "API 调用日志", "error_id", "exception_type", "backendWarning",
        # 新生儿内容
        "新生儿", "呼吸窘迫综合征", "感染性肺炎", "病例选择", "合成数据",
        # 固定布局：不再暴露画布缩放/平移控件
        "id=\"workspace\"", "workgrid", "workflowStrip",
        # 真实后端：可编辑快照、原始响应、API 调用日志、空白请求
        "id=\"snapshotJson\"", "id=\"manualSnapshot\"", "id=\"rawResponse\"",
        "id=\"apiLog\"", "id=\"blankRequest\"", "id=\"backendCheck\"",
        "requestJson", "performance.now",
        # 配色主题：四套明暗主题 + 跟随系统 + 持久化
        "id=\"themeSelect\"", "data-theme", "themeSelect", "localStorage",
        "value=\"dark\"", "value=\"warm\"", "value=\"indigo\"", "value=\"auto\"",
        "--fs-input", "--fs-label",
    )

    def test_formal_workbench_page_and_contract_fields(self):
        page_path = Path(__file__).resolve().parents[1] / "evidence_retrieval_workbench.html"
        self.assertTrue(page_path.is_file())
        page = page_path.read_text(encoding="utf-8")
        for text in self.REQUIRED:
            self.assertIn(text, page)
        # 画布式交互已移除：不得再出现缩放/平移/连线控件
        for removed in ("id=\"zoomIn\"", "id=\"zoomOut\"", "id=\"zoomFit\"",
                        "id=\"zoomReset\"", "id=\"zoomLabel\"", "id=\"wires\"",
                        "id=\"resetLayout\"", "节点画布", "回到画布"):
            self.assertNotIn(removed, page)
        self.assertNotIn("generateSummary", page)
        self.assertNotIn("llm_summary", page)
        # 不得残留成人演示与旧来源模式
        for legacy in (
            "value=\"simulated\"", "value=\"live_pubmed\"", "模拟来源",
            "syn-patient-00", "高血压", "糖尿病", "氨氯地平", "哮喘",
            "id=\"guidelineVersion\"", "id=\"population\"",
            # 不得内置演示快照：所有病例都必须来自 /api/demo/cases
            "DEMO_SNAPSHOTS", "syn-neo-001", "neo-rds-001",
        ):
            self.assertNotIn(legacy, page)

    def test_every_request_field_is_bound_in_the_page(self):
        """RetrievalRequest 的可输入字段必须在 GUI 有对应控件或映射说明。

        english_terms/mesh_terms/user_terms 是兼容字段，工作台统一用
        term_mapping 表达，不再单独提供三个旧输入。
        """
        from rag.demo import PRESET_FIELDS
        page = (Path(__file__).resolve().parents[1] / "evidence_retrieval_workbench.html").read_text(encoding="utf-8")
        legacy_inputs = {"english_terms", "mesh_terms", "user_terms"}
        # request_id 由执行时生成，不在 GUI 手工输入。
        for field in sorted(PRESET_FIELDS - {"request_id"} - legacy_inputs):
            self.assertIn(field, page, f"GUI 缺少字段绑定：{field}")
        self.assertIn("term_mapping", page)

    def test_page_has_no_client_side_fake_pipeline(self):
        """工作台不得用前端假数据代替后端：无内置快照/模拟检索结果。"""
        page = (Path(__file__).resolve().parents[1] / "evidence_retrieval_workbench.html").read_text(encoding="utf-8")
        for fake in ("mockRetrieve", "fakePackage", "simulateRetrieval", "const DEMO_CASES", "const DEMO_RESULTS"):
            self.assertNotIn(fake, page)
        # 每个业务动作都必须落到真实 HTTP 路径
        for endpoint in ("/api/his/snapshot", "/api/retrieve", "/api/citation", "/api/audit",
                         "/api/demo/cases", "/api/demo/guidelines", "/api/health", "/api/sources"):
            self.assertIn(endpoint, page)

    def test_node_order_matches_real_call_order(self):
        page = (Path(__file__).resolve().parents[1] / "evidence_retrieval_workbench.html").read_text(encoding="utf-8")
        positions = [page.index(f'data-node="{node}"') for node in
                     ("case", "question", "terms", "scope", "method", "request", "run", "results", "audit")]
        self.assertEqual(positions, sorted(positions))

    def test_handler_exposes_expected_routes(self):
        self.assertTrue(hasattr(RAGRequestHandler, "do_GET"))
        self.assertTrue(hasattr(RAGRequestHandler, "do_POST"))
        # 服务不内置语料：默认 live；custom 需先注册来源库。
        self.assertIn(RAGRequestHandler.source_mode, {"live", "custom"})
        from rag.server import CUSTOM_LIBRARY
        self.assertFalse(CUSTOM_LIBRARY.describe())


class HttpContractTests(unittest.TestCase):
    """HTTP 契约：服务本身不内置语料；测试通过 /api/sources/custom 注册 fixtures。"""

    @classmethod
    def setUpClass(cls):
        from rag.server import CUSTOM_LIBRARY
        from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), RAGRequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host, cls.port = cls.server.server_address
        RAGRequestHandler.source_mode = "custom"
        CUSTOM_LIBRARY.clear()
        register = lambda payload: cls._post_json(cls, "/api/sources/custom", payload)  # noqa: E731
        register({"source_id": "test-guideline-library", "kinds": ["guideline"],
                  "version": "test-2026-10-17", "documents": [_document_dict(doc) for doc in SYNTHETIC_GUIDELINES]})
        register({"source_id": "test-pubmed-library", "kinds": ["pubmed"],
                  "version": "test-2026-10-17", "documents": [_document_dict(doc) for doc in SYNTHETIC_PUBMED]})

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
        # 120s: in a venv with torch/transformers the first default request also
        # loads the dense retriever lazily; 5s only covers the stdlib-only path.
        conn = HTTPConnection(self.host, self.port, timeout=120)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        response = conn.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        conn.close()
        return response.status, data

    def request(self, method, path, payload=None):
        conn = HTTPConnection(self.host, self.port, timeout=120)
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
        self.assertEqual(health["demo_profile"], "neonatal_demo_v1")
        self.assertTrue(health["demo_cases_available"])
        status, tools = self.request("GET", "/api/tools")
        self.assertEqual(status, 200)
        self.assertEqual({item["name"] for item in tools["tools"]}, {"retrieve_evidence", "open_citation", "audit_grounded_answer"})

    def test_demo_cases_and_guidelines_endpoints(self):
        status, cases = self.request("GET", "/api/demo/cases")
        self.assertEqual(status, 200)
        self.assertEqual(cases["profile"], "neonatal_demo_v1")
        self.assertTrue(cases["synthetic"])
        case_ids = {case["case_id"] for case in cases["cases"]}
        self.assertIn("neo-rds", case_ids)
        self.assertIn("neo-pneumonia", case_ids)
        self.assertIn("neo-rds-retro", case_ids)
        status, guidelines = self.request("GET", "/api/demo/guidelines")
        self.assertEqual(status, 200)
        source_ids = {item["source_id"] for item in guidelines["guidelines"]}
        self.assertEqual(source_ids, {"neonatal-guideline-metadata", "neonatal-guideline-synthetic", "neonatal-literature-synthetic"})

    def test_demo_guideline_load_is_explicit_and_idempotent(self):
        from rag.server import CUSTOM_LIBRARY
        try:
            status, payload = self.request("POST", "/api/demo/guidelines/load", {})
            self.assertEqual(status, 200)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual({event["action"] for event in payload["events"]}, {"register"})
            registered = {item["source_id"] for item in CUSTOM_LIBRARY.describe()}
            self.assertIn("neonatal-guideline-metadata", registered)
            self.assertIn("neonatal-guideline-synthetic", registered)
            # 第二次加载是显式 replace，不是静默合并
            status, payload = self.request("POST", "/api/demo/guidelines/load", {})
            self.assertEqual(status, 200)
            self.assertEqual({event["action"] for event in payload["events"]}, {"replace"})
        finally:
            CUSTOM_LIBRARY.clear()
            RAGRequestHandler.tools.pop("custom", None)
            # 恢复类级 fixtures：本测试清空了共享库，不能让后续用例依赖执行顺序。
            from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED
            self._post_json("/api/sources/custom", {"source_id": "test-guideline-library", "kinds": ["guideline"],
                                                    "version": "test-2026-10-17", "documents": [_document_dict(doc) for doc in SYNTHETIC_GUIDELINES]})
            self._post_json("/api/sources/custom", {"source_id": "test-pubmed-library", "kinds": ["pubmed"],
                                                    "version": "test-2026-10-17", "documents": [_document_dict(doc) for doc in SYNTHETIC_PUBMED]})

    def test_his_snapshot_endpoint(self):
        # HIS 快照由调用方随请求传入（服务不持有内置患者数据）：过去事实保留、未来事实被抑制。
        from rag.tests.fixtures import SYNTHETIC_HIS_SNAPSHOTS
        payload = {"patient_snapshot": _snapshot_dict(SYNTHETIC_HIS_SNAPSHOTS["syn-neo-001"])}
        status, response = self.request("POST", "/api/his/snapshot", payload)
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["patient_ref"], "syn-neo-001")
        self.assertGreaterEqual(response["data"]["suppressed_future"], 1)
        # 缺少 patient_ref/as_of 的载荷必须显式报错，而不是静默返回空快照。
        status_bad, bad = self.request("POST", "/api/his/snapshot", {"payload": "missing fields"})
        self.assertEqual(status_bad, 400)

        status, package = self.request("POST", "/api/retrieve", {
            "request_id": "http-test", "question": "新生儿呼吸窘迫综合征指南推荐和最新证据？", "as_of": "2026-04-15",
            "source_kinds": ["pubmed", "guideline"], "patient_terms": ["新生儿呼吸窘迫综合征"],
            "english_terms": ["respiratory distress syndrome"], "mesh_terms": ["Respiratory Distress Syndrome, Newborn"], "top_k": 4,
            "source_mode": "custom",
            "patient_snapshot": _snapshot_dict(SYNTHETIC_HIS_SNAPSHOTS["syn-neo-001"]),
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
        conn = HTTPConnection(self.host, self.port, timeout=120)
        conn.request("POST", "/api/retrieve", "not-json", {"Content-Type": "application/json"})
        response = conn.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        conn.close()
        status, body = self.request("GET", "/missing")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_cross_retrieval_citation_is_rejected(self):
        _, first = self.request("POST", "/api/retrieve", {"question": "surfactant", "top_k": 1, "source_mode": "custom"})
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
            from rag.tests.fixtures import SYNTHETIC_PUBMED
            conn = HTTPConnection("127.0.0.1", port, timeout=10)
            body = json.dumps({"source_id": "test-pubmed-library", "kinds": ["pubmed"],
                               "version": "test-2026-10-17",
                               "documents": [_document_dict(doc) for doc in SYNTHETIC_PUBMED]},
                              ensure_ascii=False).encode("utf-8")
            conn.request("POST", "/api/sources/custom", body=body,
                         headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            conn.close()

            conn = HTTPConnection("127.0.0.1", port, timeout=10)
            body = json.dumps({"question": "respiratory distress syndrome", "top_k": 2, "source_mode": "custom"},
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
