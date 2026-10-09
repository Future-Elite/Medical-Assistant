import unittest

from rag import __version__
from rag.bm25 import score_documents
from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED
from rag.models import RetrievalRequest
from rag.query import build_query_plan


class ContractTests(unittest.TestCase):
    def test_request_and_unknown_source_fields(self):
        request = RetrievalRequest.from_dict({
            "question": "高血压指南推荐吗？", "request_id": "c1", "english_terms": ["hypertension"],
            "mesh_terms": ["Hypertension"], "facets": {"population": ["adults"]},
        })
        self.assertEqual(request.request_id, "c1")
        self.assertEqual(request.english_terms, ("hypertension",))
        doc = SYNTHETIC_GUIDELINES[0]
        restored = type(doc).from_dict({"document_id": "x", "kind": "guideline", "title": "x", "text": "x", "custom": 1})
        self.assertEqual(restored.metadata["custom"], 1)
        self.assertEqual(doc.version_id, "2024")

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

    def test_example_requests_are_runnable_and_match_documented_behavior(self):
        import json, pathlib

        from rag.server import create_tool

        examples = pathlib.Path(__file__).resolve().parents[1] / "contracts" / "examples"
        tool = create_tool("simulated")
        diabetes = json.loads((examples / "agent_joint_retrieval_request_diabetes.json").read_text(encoding="utf-8"))
        result = tool.retrieve_evidence(diabetes)
        self.assertIn("2型糖尿病", diabetes["user_terms"])
        self.assertEqual(set(result["trace"]["query_plan"]["english_terms"]), {"type 2 diabetes", "metformin"})
        question_english = json.loads((examples / "agent_joint_retrieval_request_question_english.json").read_text(encoding="utf-8"))
        result2 = tool.retrieve_evidence(question_english)
        self.assertEqual(result2["trace"]["query_plan"]["question_english_terms"], ["metformin"])
        self.assertEqual(set(result2["trace"]["external_terms"]), {"type 2 diabetes", "metformin"})


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.sources = [{"source_id": "guideline-fixture", "kinds": ["guideline"]}, {"source_id": "pubmed-fixture", "kinds": ["pubmed"]}]

    def test_bilingual_terms_and_guideline_routing(self):
        request = RetrievalRequest.from_dict({
            "question": "高血压的指南推荐是什么？", "patient_terms": ["高血压"],
            "user_terms": {"高血压": ["hypertension"]}, "mesh_terms": ["Hypertension"],
        })
        plan = build_query_plan(request, self.sources)
        self.assertIn("hypertension", plan.english_terms)
        self.assertEqual(plan.source_order[0], "guideline-fixture")
        self.assertEqual(plan.clinical_intent, "guideline_recommendation")

    def test_update_intent_preserves_facets(self):
        request = RetrievalRequest.from_dict({"question": "最新证据更新", "facets": {"outcome": ["blood pressure"]}})
        plan = build_query_plan(request, self.sources)
        self.assertEqual(plan.clinical_intent, "evidence_update")
        self.assertEqual(plan.facets["outcome"], ("blood pressure",))


class BM25Tests(unittest.TestCase):
    def test_title_and_mesh_fields_rank_expected_document(self):
        request = RetrievalRequest.from_dict({"question": "hypertension"})
        scores = score_documents(request_plan(request), SYNTHETIC_PUBMED)
        ordered = sorted(scores.items(), key=lambda item: (-item[1].score, item[0]))
        self.assertEqual(ordered[0][0], SYNTHETIC_PUBMED[0].identity_key())
        self.assertIn("title", ordered[0][1].matched_fields)

    def test_empty_query_is_deterministic(self):
        request = RetrievalRequest.from_dict({"question": "x"})
        scores = score_documents(request_plan(request), SYNTHETIC_PUBMED)
        self.assertTrue(all(item.score >= 0 for item in scores.values()))

    def test_cjk_bigram_matching(self):
        from rag.fixtures import SYNTHETIC_GUIDELINES

        cn_doc = next(doc for doc in SYNTHETIC_GUIDELINES if doc.document_id == "guide-cn-asthma-2025")
        hit = score_documents(request_plan(RetrievalRequest.from_dict({"question": "哮喘长期管理"})), (cn_doc,))
        self.assertGreater(hit[cn_doc.identity_key()].score, 0.0)
        # 单字/无关词不产生匹配（"黄疸" 与儿童哮喘指南无共同二元组）
        miss = score_documents(request_plan(RetrievalRequest.from_dict({"question": "黄疸"})), (cn_doc,))
        self.assertEqual(miss[cn_doc.identity_key()].score, 0.0)

    def test_question_english_terms_extraction(self):
        from rag.query import _question_english_terms

        terms = _question_english_terms("What is the effect of metformin on HbA1c levels? 38.9")
        self.assertEqual(terms, ("effect", "metformin", "hba1c", "levels"))
        request = RetrievalRequest.from_dict({"question": "该患者 metformin 治疗2型糖尿病的证据？"})
        plan = build_query_plan(request, [{"source_id": "pubmed-fixture", "kinds": ["pubmed"]}])
        self.assertIn("metformin", plan.question_english_terms)
        # 中文概念不因存在英文词而被翻译
        self.assertNotIn("糖尿病", plan.question_english_terms)


def request_plan(request):
    return build_query_plan(request, [{"source_id": "pubmed-fixture", "kinds": ["pubmed"]}])
