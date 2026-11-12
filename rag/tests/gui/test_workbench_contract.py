from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from rag import __version__
from rag.quality.guideline_lifecycle_index import DEFAULT_CACHE_PATH
from rag.server import RAGRequestHandler, ThreadingHTTPServer

# 仓库级 data/ 不随发布分支分发；缺生命周期缓存时跳过依赖真实缓存的用例。
_LIFECYCLE_CACHE = DEFAULT_CACHE_PATH.is_file()


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
    """GUI 契约：React/Vite 前端源码（web/src）与构建产物（web/dist）。

    重构后工作台不再是单文件内联 HTML；契约检查针对前端源码与构建产物，
    保留既有输入字段、可选值、真实端点与“无前端假数据”约束。
    """

    WEB = Path(__file__).resolve().parents[2] / "web"
    SRC = WEB / "src"
    DIST = WEB / "dist"

    @classmethod
    def frontend_source(cls) -> str:
        parts = []
        for path in sorted(cls.SRC.rglob("*")):
            if path.suffix in {".ts", ".tsx", ".css", ".html"}:
                parts.append(path.read_text(encoding="utf-8"))
        entry = cls.WEB / "index.html"
        if entry.is_file():
            parts.append(entry.read_text(encoding="utf-8"))
        return "\n".join(parts)

    def test_react_build_is_present_and_served(self):
        index = self.DIST / "index.html"
        self.assertTrue(index.is_file(), "缺少 web/dist/index.html；请运行 npm run build")
        page = index.read_text(encoding="utf-8")
        self.assertIn('id="root"', page)
        self.assertRegex(page, r"assets/index-[\w-]+\.js")
        self.assertRegex(page, r"assets/index-[\w-]+\.css")
        # 构建产物必须自包含：不依赖 CDN / 外部脚本。
        self.assertNotIn("http://", page.replace("http://www.w3.org", ""))
        self.assertNotIn("https://", page)

    def test_contract_fields_are_present(self):
        source = self.frontend_source()
        required = (
            # 页面骨架与 01–10 流程
            "新生儿证据检索", "病例与快照", "问题与意图", "术语与映射",
            "范围与过滤", "检索方法与门控", "请求组装", "执行检索",
            "证据结果", "指南解析与 HIS 核对", "引用与审计", "STEP_ORDER",
            # 01 快照
            "/api/his/snapshot", "patient_snapshot", "synthetic", "manualSnapshot",
            # 02 问题
            "question", "clinical_intent", "query_language",
            # 03 术语
            "patient_terms", "term_mapping", "mapping_trace", "needs_review",
            "facets",
            # 04 范围
            "source_mode", "source_kinds", "top_k", "filters",
            # 05 方法
            "retrieval_method", "fidelity_gate", "rerank", "rerank_pool",
            "MedCPT", "Qwen3-Reranker", "BGE-M3",
            # 06-07 请求与执行
            "RetrievalRequest", "/api/retrieve", "/api/controller/retrieve",
            "controller_policy", "controller-v1-trial",
            # 08 结果
            "evidence_groups", "key_evidence_sentences", "rank_explanation",
            "joint_selection", "query_review", "query_fidelity",
            "entity_linking", "guideline_lifecycle", "rerank_trace",
            # 09 指南解析
            "applicability", "criterion_id", "试算预览", "病历实际",
            "对得上", "chunk_id", "section_path",
            # 09 适用性解析完整流程（/api/applicability + 续跑 + 并列检索）
            "指南适用性链路", "ApplicabilityChain", "目标指南", "目标主题", "查表", "停在这里", "停止",
            "/api/applicability", "execute_external",
            "补事实并续跑", "information_requests", "parallel_retrieval",
            "coverage_guidelines", "population_qualifiers", "status_families",
            "lifecycle_status", "stop_reason",
            # 工业级图试点 + 生命周期并列
            "工业级图试点", "include_industrial_graph", "industrial_graph",
            "evidence_level", "lifecycle_checked_at",
            "主张级裁定", "claim_adjudication", "counterfactual_certificate",
            "minimal_changes",
            # 10 审计
            "/api/citation", "/api/audit", "citation_completeness",
            "citation_precision", "not_checked",
            # 演示与来源、日志
            "/api/demo/cases", "/api/demo/guidelines", "/api/demo/guidelines/load",
            "/api/sources", "API 调用日志", "error_id", "exception_type",
            # 安全与口径
            "合成数据", "非诊疗结论", "只读",
        )
        for text in required:
            self.assertIn(text, source, f"GUI 缺少契约字段：{text}")

    def test_all_original_options_are_preserved(self):
        source = self.frontend_source()
        options = (
            "clinical_question", "evidence_update", "zh", "en",
            "guideline_recommendation", "drug_safety",
            "off", "audit", "warn", "downweight", "reject",
            "custom", "live", "guideline", "pubmed",
            "bm25", "ql", "rm3", "dense", "xlingual", "m3", "translate",
            "splade", "colbert", "query2doc",
            "bm25,ql", "bm25,xlingual", "bm25,m3", "bm25,dense", "bm25,translate",
            "light", "dark", "warm", "indigo", "auto",
            "medcpt", "qwen3",
            "recursive", "structure", "fixed",
            "markdown", "row", "row_context",
            "reference", "caption_context",
            "advanced", "standard",
        )
        for option in options:
            self.assertIn(option, source, f"GUI 丢失原有选项：{option}")

    def test_page_has_no_client_side_fake_pipeline(self):
        source = self.frontend_source()
        for fake in ("mockRetrieve", "fakePackage", "simulateRetrieval",
                     "const DEMO_CASES", "const DEMO_RESULTS", "const DEMO_SNAPSHOTS"):
            self.assertNotIn(fake, source)
        for endpoint in (
            "/api/his/snapshot", "/api/retrieve", "/api/citation", "/api/audit",
            "/api/demo/cases", "/api/demo/guidelines", "/api/health", "/api/sources",
            "/api/guidelines", "/api/guidelines/chunks", "/api/guidelines/parse",
            "/api/guidelines/pdf/meta", "/api/guidelines/pdf/page",
            "/api/guidelines/applicability", "/api/guidelines/save-live",
            "/api/settings/reranker", "/api/settings/industrial",
            "/api/controller/retrieve",
        ):
            self.assertIn(endpoint, source, f"GUI 未接真实端点：{endpoint}")

    def test_guideline_parsing_stage_is_wired(self):
        source = self.frontend_source()
        for function in (
            "loadGuidelines", "loadChunks", "parseGuidelines", "showPdfPage",
            "selectChunk", "openGuidelineChunk", "checkApplicability",
            "saveLiveGuideline",
        ):
            self.assertIn(function, source, f"缺少指南解析动作：{function}")
        for text in ("chunk-section", "chunk-text", "guidelineChunks",
                     "selectedChunkId", "pdfPages", "parseOptions"):
            self.assertIn(text, source)

    def test_request_fields_are_bound(self):
        from rag.demo import PRESET_FIELDS
        source = self.frontend_source()
        legacy_inputs = {"english_terms", "mesh_terms", "user_terms"}
        for field in sorted(PRESET_FIELDS - {"request_id"} - legacy_inputs):
            self.assertIn(field, source, f"GUI 缺少字段绑定：{field}")

    def test_step_order_matches_call_order(self):
        source = (self.SRC / "state" / "workbench.tsx").read_text(encoding="utf-8")
        match = re.search(r"const STEP_ORDER: StepKey\[\] = \[(.*?)\];", source, re.S)
        self.assertIsNotNone(match)
        order = re.findall(r'"([a-z]+)"', match.group(1))
        self.assertEqual(order, [
            "case", "question", "terms", "scope", "method",
            "request", "run", "results", "guidelines", "audit",
        ])

    def test_handler_exposes_expected_routes(self):
        self.assertTrue(hasattr(RAGRequestHandler, "do_GET"))
        self.assertTrue(hasattr(RAGRequestHandler, "do_POST"))
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

    def tearDown(self):
        # 运行时重排覆盖是进程级状态；测试间清空，避免影响按 config 选择的单测。
        from rag import server
        server._RERANKER_RUNTIME["model"] = None
        server._RERANKER_RUNTIME["rerank_pool"] = None
        RAGRequestHandler.tools.clear()

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

    def _get_raw(self, path):
        conn = HTTPConnection(self.host, self.port, timeout=60)
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
        conn.close()
        return response.status, headers, body

    def test_workbench_serves_react_build(self):
        status, headers, body = self._get_raw("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        page = body.decode("utf-8")
        self.assertIn('id="root"', page)
        self.assertRegex(page, r"assets/index-[\w-]+\.js")

    def test_asset_route_and_path_traversal(self):
        _, _, body = self._get_raw("/")
        match = re.search(r"assets/index-[\w-]+\.js", body.decode("utf-8"))
        self.assertIsNotNone(match)
        status, headers, asset = self._get_raw("/" + match.group(0))
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn("immutable", headers["Cache-Control"])
        status, _, _ = self._get_raw("/assets/../server.py")
        self.assertEqual(status, 404)

    def test_health_and_tools(self):
        status, health = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["rag_version"], __version__)
        self.assertEqual(health["retrieval_mode"], "custom")
        self.assertIn("custom", health["available_modes"])
        self.assertEqual(health["demo_profile"], "neonatal_demo_v1")
        self.assertTrue(health["demo_cases_available"])
        # v6.7.0：自动映射库与重排器状态须在 health 暴露（重排器默认 MedCPT / 池 0）
        self.assertIn("term_mapping_library", health)
        self.assertEqual(health["term_mapping_library"]["configured"], False)
        self.assertEqual(health["reranker"]["model"], "medcpt")
        self.assertEqual(health["reranker"]["rerank_pool"], 0)
        self.assertEqual(health["reranker"]["available_models"], ["medcpt", "qwen3"])
        self.assertEqual(health["routing"], {"mode": "off"})
        self.assertEqual(health["controller"]["version"], "controller-v1-trial")
        # v6.7.1：升级后的行为机制默认开启；legacy 通过 off 备选
        self.assertEqual(health["entity_linking"]["mode"], "active")
        self.assertGreaterEqual(health["entity_linking"]["lexicon"]["descriptors"], 700)
        self.assertTrue(health["entity_linking"]["semantic_requested"])
        self.assertEqual(health["guideline_lifecycle"]["mode"], "active")
        self.assertEqual(health["claim_verifier"]["mode"], "claim_verification")
        self.assertTrue(health["claim_verifier"]["configured"])
        self.assertEqual(health["pubmed"]["max_results"], 200)
        # v6.7.1 GUI：React 构建产物由服务直接托管，旧单文件保留在 /legacy
        self.assertEqual(health["gui"]["framework"], "react")
        self.assertTrue(health["gui"]["built"])
        self.assertEqual(health["gui"]["entry"], "/")
        status, tools = self.request("GET", "/api/tools")
        self.assertEqual(status, 200)
        self.assertEqual({item["name"] for item in tools["tools"]}, {"retrieve_evidence", "open_citation", "audit_grounded_answer", "resolve_guideline_applicability", "resolve_applicability_workflow"})

    def test_applicability_endpoint_executes_local_resolution(self):
        status, payload = self.request("POST", "/api/applicability", {
            "question": "肠外营养指南是否适用？",
            "patient_snapshot": {
                "patient_ref": "synthetic-http",
                "as_of": "2026-01-08",
                "facts": [{
                    "collection": "derived_birth",
                    "fields": {"ga_days": 266, "birth_weight_g": 3200, "pna_days": 5, "data_status": "observed_positive"},
                }],
            },
            "target_guideline_id": "pdf-neo-parenteral-nutrition-2012",
            "execute_external": False,
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["outcome"], "scope_mismatch")
        self.assertEqual(payload["evidence"], [])
        self.assertEqual(payload["rounds"][-1]["reason"], "search_backend_not_provided")
        self.assertEqual(payload["next_action"]["type"], "scope_mismatch")
        self.assertIn("target_detail", payload)

    @unittest.skipUnless(_LIFECYCLE_CACHE, "guideline lifecycle cache is not shipped")
    def test_applicability_endpoint_returns_lifecycle_and_industrial_graph(self):
        status, payload = self.request("POST", "/api/applicability", {
            "question": "极低出生体重儿喂养指南是否适用？",
            "patient_snapshot": {
                "patient_ref": "synthetic-http-graph",
                "as_of": "2024-06-10",
                "facts": [{
                    "collection": "derived_birth",
                    "fields": {"birth_weight_g": 1010, "ga_days": 210, "data_status": "observed_positive"},
                }],
            },
            "target_guideline_id": "pdf-neo-feeding-vlbw-2015",
            "execute_external": False,
            "include_industrial_graph": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["parallel_retrieval"]["lifecycle_status"], "current")
        self.assertEqual(
            payload["parallel_retrieval"]["lifecycle"]["pdf-neo-feeding-vlbw-2015"]["pmid"],
            "25580815",
        )
        self.assertEqual(payload["industrial_graph"]["guideline_id"], "pdf-neo-feeding-vlbw-2015")
        self.assertEqual(payload["industrial_graph"]["scope"]["status"], "match")
        self.assertTrue(payload["industrial_graph"]["recommendations"])
        self.assertTrue(payload["industrial_graph"]["claim_adjudication"]["claims"])

    def test_industrial_settings_endpoint(self):
        status, payload = self.request("POST", "/api/settings/industrial", {
            "entity_linking_mode": "shadow",
            "guideline_lifecycle_mode": "off",
            "claim_verifier": "off",
            "entity_linking_semantic": False,
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["industrial"]["entity_linking"]["mode"], "shadow")
        self.assertEqual(payload["industrial"]["guideline_lifecycle"]["mode"], "off")
        self.assertFalse(payload["industrial"]["claim_verifier"]["configured"])
        status, health = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["entity_linking"]["mode"], "shadow")
        self.assertEqual(health["guideline_lifecycle"]["mode"], "off")
        # restore defaults for the rest of the suite
        status, _ = self.request("POST", "/api/settings/industrial", {
            "entity_linking_mode": "active",
            "guideline_lifecycle_mode": "active",
            "claim_verifier": "flan-t5-large",
            "entity_linking_semantic": True,
        })
        self.assertEqual(status, 200)

    def test_reranker_settings_endpoint(self):
        status, payload = self.request("POST", "/api/settings/reranker", {"model": "qwen3", "rerank_pool": 20})
        self.assertEqual(status, 200)
        self.assertEqual(payload["reranker"]["model"], "qwen3")
        self.assertEqual(payload["reranker"]["rerank_pool"], 20)
        self.assertTrue(payload["reranker"]["runtime_override"])
        status, health = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["reranker"]["model"], "qwen3")
        self.assertEqual(health["reranker"]["rerank_pool"], 20)
        status, invalid = self.request("POST", "/api/settings/reranker", {"model": "bogus"})
        self.assertEqual(status, 400)
        status, invalid_pool = self.request("POST", "/api/settings/reranker", {"rerank_pool": -1})
        self.assertEqual(status, 400)
        status, reset = self.request("POST", "/api/settings/reranker", {"model": "medcpt", "rerank_pool": 0})
        self.assertEqual(status, 200)
        self.assertEqual(reset["reranker"]["model"], "medcpt")
        self.assertEqual(reset["reranker"]["rerank_pool"], 0)

    def test_controller_endpoint_returns_rounds_and_replay(self):
        status, result = self.request("POST", "/api/controller/retrieve", {
            "question": "该患儿新生儿呼吸窘迫综合征的指南推荐是什么？",
            "top_k": 3,
            "source_kinds": ["guideline", "pubmed"],
            "english_terms": ["respiratory distress syndrome"],
            "source_mode": "custom",
        })
        self.assertEqual(status, 200)
        self.assertEqual(result["controller_version"], "controller-v1-trial")
        self.assertTrue(result["rounds"])
        self.assertIn("reason", result["stop"])
        self.assertIn("decision_sequence", result["replay"])
        self.assertEqual(result["replay"]["stop_reason"], result["stop"]["reason"])
        self.assertGreaterEqual(len(result["replay"]["decision_sequence"]), 1)

    def test_demo_cases_and_guidelines_endpoints(self):
        status, cases = self.request("GET", "/api/demo/cases")
        self.assertEqual(status, 200)
        self.assertEqual(cases["profile"], "neonatal_demo_v1")
        self.assertTrue(cases["synthetic"])
        case_ids = {case["case_id"] for case in cases["cases"]}
        self.assertEqual(len(case_ids), 50)
        self.assertIn("neo-ehr-001-pneumonia", case_ids)
        self.assertIn("neo-ehr-002-rds", case_ids)
        self.assertIn("neo-ehr-010-rds", case_ids)
        status, chain = self.request("GET", "/api/demo/applicability-cases")
        self.assertEqual(status, 200)
        self.assertEqual(chain["profile"], "neonatal_applicability_demo_v1")
        self.assertTrue(chain["synthetic"])
        self.assertEqual(len(chain["cases"]), 5)
        self.assertIn("neo-chain-01-vlbw-match", {case["case_id"] for case in chain["cases"]})
        self.assertIn("neo-chain-04-pocus-no-coverage", {case["case_id"] for case in chain["cases"]})
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

        src_root = Path(__file__).resolve().parents[3]
        server_script = Path(__file__).resolve().parents[2] / "server.py"
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

            # 首次检索会加载本地神经模型；冷启动在本机约 70 秒，不能套用普通请求超时。
            conn = HTTPConnection("127.0.0.1", port, timeout=180)
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
        src_root = str(Path(__file__).resolve().parents[3])
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
