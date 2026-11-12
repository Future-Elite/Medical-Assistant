"""v6.7.0 重排升级：重排候选池（rerank_pool）+ Qwen3Reranker 适配器。

离线测试约束：不加载神经模型、不联网。Qwen3Reranker 只测注入打分路径与
纯打分函数（真实模型 smoke 不在本测试中执行）；重排池默认 0（legacy 语义），
仅当请求 rerank=true 且池 > 0 时改变候选流。
"""
import math
import unittest

from rag.config import RAGConfig
from rag.models import RankedDocument, RetrievalRequest, SourceDocument
from rag.neural_adapters import Qwen3Reranker, yes_no_probability
from rag.pipeline import EvidenceRetrievalPipeline
from rag.query import build_query_plan
from rag.retrieval_adapters import DeterministicSemanticStub
from rag.tests.helpers import fixture_pipeline

RDS = {
    "question": "该患儿新生儿呼吸窘迫综合征的指南推荐和最新证据是什么？",
    "as_of": "2026-04-15",
    "top_k": 2,
    "english_terms": ["respiratory distress syndrome"],
    "mesh_terms": ["Respiratory Distress Syndrome"],
}


class RecordingReranker:
    """Deterministic stub: records how many candidates it was given, reverses order."""

    reranker_id = "recording-stub"

    def __init__(self, *, error=False):
        self.seen = []
        self.error = error

    def rank(self, plan, candidates):
        from dataclasses import replace

        self.seen.append(len(candidates))
        if self.error:
            raise RuntimeError("stub failure")
        return [
            replace(item, reranker_id=self.reranker_id, reranker_score=1.0 / (index + 1))
            for index, item in enumerate(reversed(list(candidates)))
        ]


class YesNoProbabilityTests(unittest.TestCase):
    def test_equal_logits_give_half(self):
        self.assertAlmostEqual(yes_no_probability(0.0, 0.0), 0.5)

    def test_monotonic_and_bounded(self):
        low = yes_no_probability(-2.0, 2.0)
        mid = yes_no_probability(0.0, 0.0)
        high = yes_no_probability(2.0, -2.0)
        self.assertLess(low, mid)
        self.assertLess(mid, high)
        for value in (low, mid, high):
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_matches_restricted_softmax(self):
        expected = math.exp(1.5) / (math.exp(1.5) + math.exp(-0.5))
        self.assertAlmostEqual(yes_no_probability(1.5, -0.5), expected)


class Qwen3RerankerInjectedTests(unittest.TestCase):
    def setUp(self):
        self.candidates = tuple(
            RankedDocument(
                document=SourceDocument.from_dict({
                    "document_id": f"d-{index}", "kind": "pubmed",
                    "title": f"doc {index}", "text": f"evidence text {index}",
                }, source_id="pubmed-fixture"),
                score=1.0 - index * 0.1, lexical_score=1.0, fusion_score=0.0,
                authority_score=0.7, freshness_score=0.5, evidence_status="current",
                matched_queries=(), rank_explanation=(),
            )
            for index in range(3)
        )
        self.plan = build_query_plan(
            RetrievalRequest.from_dict(RDS), [{"source_id": "pubmed-fixture", "kinds": ["pubmed"]}],
        )

    def test_injected_scores_reorder_and_record_provenance(self):
        seen_pairs = []

        def score_pairs(pairs):
            seen_pairs.extend(pairs)
            return [0.1, 0.9, 0.5]

        reranker = Qwen3Reranker(score_pairs=score_pairs)
        self.assertTrue(reranker.available)
        ordered = reranker.rank(self.plan, self.candidates)
        self.assertEqual([item.document.document_id for item in ordered], ["d-1", "d-2", "d-0"])
        self.assertEqual([item.reranker_id for item in ordered], ["qwen3-reranker"] * 3)
        self.assertAlmostEqual(ordered[0].reranker_score, 0.9)
        # 只改顺序与 reranker 字段，不动来源与证据状态。
        for item in ordered:
            self.assertEqual(item.evidence_status, "current")
            self.assertEqual(item.document.source_id, "pubmed-fixture")
        # 查询文本必须保留中文原问题（相对 MedCPT 的关键差异）。
        self.assertTrue(seen_pairs)
        self.assertIn("新生儿呼吸窘迫综合征", seen_pairs[0][0])
        self.assertIn("respiratory distress syndrome", seen_pairs[0][0])

    def test_score_pairs_length_mismatch_rejected(self):
        reranker = Qwen3Reranker(score_pairs=lambda pairs: [0.5])
        with self.assertRaises(ValueError):
            reranker.rank(self.plan, self.candidates)

    def test_empty_candidates_pass_through(self):
        reranker = Qwen3Reranker(score_pairs=lambda pairs: [])
        self.assertEqual(reranker.rank(self.plan, ()), ())

    def test_chinese_only_question_kept(self):
        seen_pairs = []
        plan = build_query_plan(
            RetrievalRequest.from_dict({"question": "该患儿湿肺与肺炎如何鉴别？"}),
            [{"source_id": "pubmed-fixture", "kinds": ["pubmed"]}],
        )
        Qwen3Reranker(score_pairs=lambda pairs: seen_pairs.extend(pairs) or [0.5] * len(pairs)).rank(plan, self.candidates[:1])
        self.assertIn("湿肺", seen_pairs[0][0])


class RerankPoolTests(unittest.TestCase):
    def _request(self, **overrides):
        return RetrievalRequest.from_dict({**RDS, **overrides})

    def test_legacy_pool_sees_only_final_top_k(self):
        stub = RecordingReranker()
        pipeline = fixture_pipeline(reranker=stub, rerank_pool=0)
        package = pipeline.retrieve(self._request(rerank=True))
        self.assertEqual(stub.seen, [2])
        self.assertEqual(package.trace["rerank_trace"]["status"], "ok")
        self.assertNotIn("pool_enabled", package.trace["rerank_trace"])
        self.assertEqual(len(package.ranked_documents), 2)

    def test_pool_feeds_larger_candidate_set_then_truncates(self):
        stub = RecordingReranker()
        pipeline = fixture_pipeline(reranker=stub, rerank_pool=50)
        package = pipeline.retrieve(self._request(rerank=True))
        self.assertEqual(len(stub.seen), 1)
        self.assertGreater(stub.seen[0], 2, "重排应看到多于 top_k 的候选")
        self.assertLessEqual(stub.seen[0], 50)
        self.assertEqual(len(package.ranked_documents), 2)
        trace = package.trace["rerank_trace"]
        self.assertEqual(trace["pool_enabled"], True)
        self.assertEqual(trace["pool_size"], 50)
        self.assertEqual(trace["reranked_count"], stub.seen[0])
        self.assertEqual([item.reranker_id for item in package.ranked_documents], ["recording-stub"] * 2)

    def test_pool_inert_without_rerank_request(self):
        stub = RecordingReranker()
        pipeline = fixture_pipeline(reranker=stub, rerank_pool=50)
        package = pipeline.retrieve(self._request())
        self.assertEqual(stub.seen, [])
        self.assertEqual(package.trace["rerank_trace"]["requested"], False)
        legacy = fixture_pipeline().retrieve(self._request())
        self.assertEqual(
            [item.document.document_id for item in package.ranked_documents],
            [item.document.document_id for item in legacy.ranked_documents],
        )

    def test_pool_keeps_top_k_contract_on_reranker_error(self):
        stub = RecordingReranker(error=True)
        pipeline = fixture_pipeline(reranker=stub, rerank_pool=50)
        package = pipeline.retrieve(self._request(rerank=True))
        self.assertEqual(package.trace["rerank_trace"]["status"], "error")
        self.assertLessEqual(len(package.ranked_documents), 2)

    def test_pool_without_reranker_falls_back_to_selection_top_k(self):
        pipeline = fixture_pipeline(rerank_pool=50)
        package = pipeline.retrieve(self._request(rerank=True))
        self.assertEqual(package.trace["rerank_trace"]["status"], "not_configured")
        self.assertLessEqual(len(package.ranked_documents), 2)

    def test_negative_pool_rejected(self):
        with self.assertRaises(ValueError):
            EvidenceRetrievalPipeline(fixture_pipeline().registry, rerank_pool=-1)
        with self.assertRaises(ValueError):
            EvidenceRetrievalPipeline(fixture_pipeline().registry, rerank_pool="x")


class ServerWiringTests(unittest.TestCase):
    def test_config_defaults_are_legacy(self):
        config = RAGConfig()
        self.assertEqual(config.reranker_model, "medcpt")
        self.assertEqual(config.rerank_pool, 0)

    def test_neural_parts_selects_reranker_by_config(self):
        from unittest import mock

        from rag import server

        with mock.patch.object(server, "get_config", return_value=RAGConfig(reranker_model="medcpt")):
            _neural, reranker = server._neural_parts()
            self.assertEqual(reranker.reranker_id, "medcpt-cross-encoder")
        with mock.patch.object(server, "get_config", return_value=RAGConfig(reranker_model="qwen3")):
            _neural, reranker = server._neural_parts()
            self.assertIsInstance(reranker, Qwen3Reranker)
        with mock.patch.object(server, "get_config", return_value=RAGConfig(reranker_model="bogus")):
            with self.assertRaises(ValueError):
                server._neural_parts()

    def test_method_status_reports_qwen3_reranker(self):
        from rag import server

        status = server._method_status()
        self.assertIn("qwen3_reranker", status)
        self.assertEqual(status["qwen3_reranker"]["kind"], "reranker")


class DeterministicSemanticStubReuseTests(unittest.TestCase):
    def test_stub_retriever_still_works_with_pool_enabled(self):
        stub = DeterministicSemanticStub({})
        pipeline = fixture_pipeline(neural_retrievers={"dense": stub}, rerank_pool=50)
        package = pipeline.retrieve(RetrievalRequest.from_dict({**RDS, "retrieval_method": "bm25,dense"}))
        self.assertTrue(package.citations)


if __name__ == "__main__":
    unittest.main()
