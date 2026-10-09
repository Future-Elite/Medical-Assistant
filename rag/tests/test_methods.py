"""检索方法选择：bm25 / ql / rm3 / dense / splade / colbert / query2doc 与 RRF 融合。

离线测试约束：不加载神经模型、不联网。神经方法在这里只验证
"选择解析 + not_configured 显式状态 + 融合结构"，模型本身在 venv 里单独验收。
"""
import unittest

from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED
from rag.lexical import score_documents_ql, score_documents_rm3
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline
from rag.query import build_query_plan
from rag.retrieval_adapters import DeterministicSemanticStub


def build_pipeline(**kwargs) -> EvidenceRetrievalPipeline:
    registry = ConnectorRegistry()
    registry.register(VersionedGuidelineConnector("guideline-fixture", SYNTHETIC_GUIDELINES))
    registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
    return EvidenceRetrievalPipeline(registry, **kwargs)


HYPERTENSION = {
    "question": "该患者高血压的指南推荐和最新证据是什么？", "as_of": "2025-12-31", "top_k": 4,
    "english_terms": ["hypertension"], "mesh_terms": ["Hypertension"],
}


class MethodParsingTests(unittest.TestCase):
    def test_method_aliases(self):
        self.assertEqual(RetrievalRequest.parse_methods(None), ("bm25",))
        self.assertEqual(RetrievalRequest.parse_methods(""), ("bm25",))
        self.assertEqual(RetrievalRequest.parse_methods("bm25"), ("bm25",))
        self.assertEqual(RetrievalRequest.parse_methods("bm25+dense"), ("bm25", "dense"))
        self.assertEqual(RetrievalRequest.parse_methods(["bm25", "rm3", "dense"]), ("bm25", "rm3", "dense"))
        self.assertEqual(RetrievalRequest.parse_methods("bm25,rm3,dense"), ("bm25", "rm3", "dense"))
        # 去重且保序
        self.assertEqual(RetrievalRequest.parse_methods("dense,dense,bm25"), ("dense", "bm25"))
        with self.assertRaises(ValueError):
            RetrievalRequest.parse_methods("unknown-method")
        with self.assertRaises(ValueError):
            RetrievalRequest.from_dict({"question": "x", "retrieval_method": "bogus"})

    def test_default_method_is_none_and_resolves_to_bm25(self):
        request = RetrievalRequest.from_dict({"question": "x"})
        self.assertIsNone(request.retrieval_method)
        package = build_pipeline().retrieve(request)
        self.assertEqual(package.trace["retrieval_method"], "bm25")
        self.assertEqual([item["method"] for item in package.trace["method_trace"]], ["bm25"])


class LexicalMethodTests(unittest.TestCase):
    def setUp(self):
        self.plan = build_query_plan(RetrievalRequest.from_dict(HYPERTENSION), [{"source_id": "pubmed-fixture", "kinds": ["pubmed"]}])
        self.documents = tuple(SYNTHETIC_PUBMED)

    def test_ql_prefers_title_and_mesh_matches(self):
        scores = score_documents_ql(self.plan, self.documents)
        best = max(scores.items(), key=lambda item: (item[1].score, item[0]))[0]
        self.assertEqual(best, SYNTHETIC_PUBMED[0].identity_key())
        self.assertGreater(scores[best].score, 0.0)

    def test_rm3_expands_but_keeps_topical_ranking(self):
        rm3 = score_documents_rm3(self.plan, self.documents)
        matched = rm3[SYNTHETIC_PUBMED[0].identity_key()].matched_terms
        self.assertIn("hypertension", matched)
        # RM3 反馈项应加入 matched_terms（不止原查询词）
        self.assertGreater(len(matched), 1)
        ordered = sorted(rm3.items(), key=lambda item: (-item[1].score, item[0]))[:2]
        self.assertEqual({key for key, _ in ordered}, {SYNTHETIC_PUBMED[0].identity_key(), SYNTHETIC_PUBMED[1].identity_key()})

    def test_ql_and_bm25_produce_same_selected_docs_but_different_scores(self):
        from rag.bm25 import score_documents

        bm25 = score_documents(self.plan, self.documents)
        ql = score_documents_ql(self.plan, self.documents)
        for key in bm25:
            if bm25[key].score > 0:
                self.assertGreater(ql[key].score, 0.0)
            if bm25[key].score == 0:
                self.assertEqual(ql[key].score, 0.0)
        # 分数尺度不同（QL 是对数概率，BM25 是加权词频），选择结果一致即可
        self.assertNotEqual(round(bm25[SYNTHETIC_PUBMED[0].identity_key()].score, 6), round(ql[SYNTHETIC_PUBMED[0].identity_key()].score, 6))


class MethodDispatchTests(unittest.TestCase):
    def test_ql_and_rm3_run_end_to_end(self):
        for method in ("ql", "rm3"):
            package = build_pipeline().retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "retrieval_method": method}))
            self.assertEqual(package.trace["retrieval_method"], method)
            self.assertEqual(package.trace["method_trace"][0]["status"], "ok")
            self.assertTrue(package.citations)
            self.assertTrue(all(item.fusion_method == method for item in package.ranked_documents))

    def test_neural_methods_report_not_configured_without_adapter(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "retrieval_method": "dense"}))
        states = {item["method"]: item["status"] for item in package.trace["method_trace"]}
        self.assertEqual(states.get("dense"), "not_configured")
        self.assertEqual(states.get("bm25"), "fallback")
        self.assertTrue(any("not_configured" in warning for warning in package.warnings))

    def test_dense_with_stub_fuses_via_rrf(self):
        stub = DeterministicSemanticStub({doc.identity_key(): 0.9 if index == 0 else 0.1 for index, doc in enumerate(SYNTHETIC_PUBMED)})
        package = build_pipeline(neural_retrievers={"dense": stub}).retrieve(
            RetrievalRequest.from_dict({**HYPERTENSION, "retrieval_method": "bm25,dense"}))
        self.assertEqual(package.trace["retrieval_method"], "bm25+dense")
        states = {item["method"]: item["status"] for item in package.trace["method_trace"]}
        self.assertEqual(states, {"bm25": "ok", "dense": "ok"})
        first = package.ranked_documents[0]
        self.assertEqual(first.fusion_method, "bm25+dense_rrf")
        self.assertTrue(any("RRF" in line for line in first.rank_explanation))

    def test_unknown_method_rejected_before_pipeline(self):
        with self.assertRaises(ValueError):
            build_pipeline().retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "retrieval_method": "tfidf"}))

    def test_rerank_flag_trace_without_reranker(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "rerank": True}))
        self.assertEqual(package.trace["rerank_trace"]["requested"], True)
        self.assertEqual(package.trace["rerank_trace"]["status"], "not_configured")
        self.assertTrue(any("重排" in warning for warning in package.warnings))

    def test_sample_queries_all_methods_do_not_crash(self):
        """示例问题 × 所有方法：默认环境不装模型时也必须给出可审计结果而不是异常。"""
        for method in ("bm25", "ql", "rm3", "dense", "splade", "colbert", "query2doc", "bm25,dense", "bm25,rm3,dense"):
            package = build_pipeline().retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "retrieval_method": method}))
            self.assertTrue(package.citations or package.warnings)


if __name__ == "__main__":
    unittest.main()
