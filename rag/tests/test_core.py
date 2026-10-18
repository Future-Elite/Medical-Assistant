import unittest

from rag import __version__
from rag.bm25 import score_documents
from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED
from rag.models import RetrievalRequest
from rag.query import build_query_plan


class ContractTests(unittest.TestCase):
    def test_request_and_unknown_source_fields(self):
        request = RetrievalRequest.from_dict({
            "question": "新生儿呼吸窘迫综合征的指南推荐吗？", "request_id": "c1",
            "english_terms": ["respiratory distress syndrome"],
            "mesh_terms": ["Respiratory Distress Syndrome, Newborn"],
            "facets": {"population": ["preterm infants"]},
        })
        self.assertEqual(request.request_id, "c1")
        self.assertEqual(request.english_terms, ("respiratory distress syndrome",))
        doc = SYNTHETIC_GUIDELINES[0]
        restored = type(doc).from_dict({"document_id": "x", "kind": "guideline", "title": "x", "text": "x", "custom": 1})
        self.assertEqual(restored.metadata["custom"], 1)
        self.assertEqual(doc.version_id, "2025")

    def test_guideline_connector_rejects_non_guideline(self):
        with self.assertRaises(ValueError):
            VersionedGuidelineConnector("bad", (SYNTHETIC_PUBMED[0],))


class AgentInterfaceContractTests(unittest.TestCase):
    """对接契约：契约 JSON、运行工具描述、示例请求必须一致可读。"""

    def test_agent_contract_json_is_valid_and_documents_language_rules(self):
        import json, pathlib

        path = pathlib.Path(__file__).resolve().parents[1] / "contracts" / "agent_contract.json"
        contract = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(contract["contract_metadata"]["contract_version"], __version__)
        rules = contract["language_rules"]
        for key in ("original_question", "question_english_terms", "reviewed_english_terms", "reviewed_mapping", "cjk_matching", "live_fallback"):
            self.assertIn(key, rules)
        request_props = contract["schemas"]["RetrievalRequest"]["properties"]
        for field in ("question", "user_terms", "english_terms", "mesh_terms", "as_of", "patient_snapshot", "facets", "filters",
                      "retrieval_method", "rerank"):
            self.assertIn(field, request_props)
        plan_props = contract["schemas"]["QueryPlan"]["properties"]
        self.assertIn("question_english_terms", plan_props)
        self.assertIn("expansion_trace", plan_props)
        #  研究层字段：契约 JSON 与枚举
        self.assertIn("fidelity_gate", request_props)
        self.assertEqual(
            request_props["fidelity_gate"]["enum"],
            ["off", "audit", "warn", "downweight", "reject"],
        )

    def test_tool_specifications_explain_bilingual_fields(self):
        from rag.agent import EvidenceRAG

        specs = {item["name"]: item for item in EvidenceRAG.tool_specifications()}
        retrieve = specs["retrieve_evidence"]
        props = retrieve["parameters"]["properties"]
        self.assertIn("中文临床原始问题", props["question"]["description"])
        self.assertIn("不翻译", props["question"]["description"])
        self.assertIn("不自动翻译", props["user_terms"]["description"])
        self.assertIn("人工审核", props["english_terms"]["description"])
        self.assertIn("人工审核", props["mesh_terms"]["description"])
        self.assertIn("question_english_terms", retrieve["description"])
        self.assertIn("not_configured", props["retrieval_method"]["description"])
        self.assertIn("cross-encoder", props["rerank"]["description"])
        self.assertIn("查询保真门控", props["fidelity_gate"]["description"])
        self.assertIn("trace.query_fidelity", props["fidelity_gate"]["description"])
        self.assertIn("新生儿", retrieve["description"])

    def test_example_requests_are_runnable_and_match_documented_behavior(self):
        import json, pathlib

        from rag.tests.helpers import fixture_tool

        examples = pathlib.Path(__file__).resolve().parents[1] / "contracts" / "examples"
        # 示例经 custom 来源路径执行：测试 fixtures 按调用方库的方式注册（产品无内置语料）。
        tool = fixture_tool()
        pneumonia = json.loads((examples / "agent_joint_retrieval_request_pneumonia.json").read_text(encoding="utf-8"))
        result = tool.retrieve_evidence(pneumonia)
        self.assertIn("新生儿感染性肺炎", pneumonia["user_terms"])
        self.assertEqual(
            set(result["trace"]["query_plan"]["english_terms"]),
            {"neonatal pneumonia", "chest radiograph", "blood culture"},
        )
        question_english = json.loads((examples / "agent_joint_retrieval_request_question_english.json").read_text(encoding="utf-8"))
        result2 = tool.retrieve_evidence(question_english)
        self.assertEqual(result2["trace"]["query_plan"]["question_english_terms"], ["surfactant"])
        self.assertIn("neonatal respiratory distress syndrome", result2["trace"]["external_terms"])

    def test_fidelity_example_is_runnable(self):
        import json, pathlib

        from rag.tests.helpers import fixture_tool

        examples = pathlib.Path(__file__).resolve().parents[1] / "contracts" / "examples"
        payload = json.loads((examples / "agent_fidelity_gate_request.json").read_text(encoding="utf-8"))
        tool = fixture_tool()  # custom-source path: no built-in corpus
        result = tool.retrieve_evidence(payload)
        fidelity = result["trace"]["query_fidelity"]
        self.assertTrue(fidelity["applied"])
        self.assertEqual(fidelity["policy"], "downweight")
        self.assertIsNotNone(fidelity["report"])
        # 审计覆盖三条路线（即使有的路线未启用）
        routes = {route["route"] for route in fidelity["report"]["routes"]}
        self.assertEqual(routes, {"reviewed_mapping", "translate", "multilingual_encoding"})


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.sources = [{"source_id": "guideline-fixture", "kinds": ["guideline"]}, {"source_id": "pubmed-fixture", "kinds": ["pubmed"]}]

    def test_bilingual_terms_and_guideline_routing(self):
        request = RetrievalRequest.from_dict({
            "question": "新生儿呼吸窘迫综合征的指南推荐是什么？", "patient_terms": ["新生儿呼吸窘迫综合征"],
            "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]},
            "mesh_terms": ["Respiratory Distress Syndrome, Newborn"],
        })
        plan = build_query_plan(request, self.sources)
        self.assertIn("respiratory distress syndrome", plan.english_terms)
        self.assertEqual(plan.source_order[0], "guideline-fixture")
        self.assertEqual(plan.clinical_intent, "guideline_recommendation")

    def test_update_intent_preserves_facets(self):
        request = RetrievalRequest.from_dict({"question": "最新证据更新", "facets": {"outcome": ["oxygenation"]}})
        plan = build_query_plan(request, self.sources)
        self.assertEqual(plan.clinical_intent, "evidence_update")
        self.assertEqual(plan.facets["outcome"], ("oxygenation",))


class BM25Tests(unittest.TestCase):
    def test_title_and_mesh_fields_rank_expected_document(self):
        request = RetrievalRequest.from_dict({"question": "respiratory distress syndrome"})
        scores = score_documents(request_plan(request), SYNTHETIC_PUBMED)
        ordered = sorted(scores.items(), key=lambda item: (-item[1].score, item[0]))
        self.assertEqual(ordered[0][0], SYNTHETIC_PUBMED[0].identity_key())
        self.assertIn("title", ordered[0][1].matched_fields)

    def test_empty_query_is_deterministic(self):
        request = RetrievalRequest.from_dict({"question": "x"})
        scores = score_documents(request_plan(request), SYNTHETIC_PUBMED)
        self.assertTrue(all(item.score >= 0 for item in scores.values()))

    def test_cjk_bigram_matching(self):
        from rag.tests.fixtures import SYNTHETIC_GUIDELINES

        cn_doc = next(doc for doc in SYNTHETIC_GUIDELINES if doc.document_id == "guide-jnd-2025")
        hit = score_documents(request_plan(RetrievalRequest.from_dict({"question": "光疗阈值与胆红素监测"})), (cn_doc,))
        self.assertGreater(hit[cn_doc.identity_key()].score, 0.0)
        # 无关病种不产生匹配（肺炎与黄疸指南无共同二元组）
        miss = score_documents(request_plan(RetrievalRequest.from_dict({"question": "肺炎"})), (cn_doc,))
        self.assertEqual(miss[cn_doc.identity_key()].score, 0.0)

    def test_question_english_terms_extraction(self):
        from rag.query import _question_english_terms

        terms = _question_english_terms("What is the effect of surfactant on FiO2 levels? 0.30")
        self.assertEqual(terms, ("effect", "surfactant", "fio2", "levels"))
        request = RetrievalRequest.from_dict({"question": "该患儿 surfactant 治疗新生儿呼吸窘迫综合征的证据？"})
        plan = build_query_plan(request, [{"source_id": "pubmed-fixture", "kinds": ["pubmed"]}])
        self.assertIn("surfactant", plan.question_english_terms)
        # 中文概念不因存在英文词而被翻译
        self.assertNotIn("呼吸窘迫", plan.question_english_terms)


def request_plan(request):
    return build_query_plan(request, [{"source_id": "pubmed-fixture", "kinds": ["pubmed"]}])
