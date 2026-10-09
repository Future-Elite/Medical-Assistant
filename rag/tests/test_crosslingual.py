"""跨语言检索方法：xlingual / translate 的管线契约（离线、无模型）。

离线约束：这些测试不加载任何神经模型、不联网。它们只验证：

- 方法名解析与管线调度（xlingual 走 neural adapter 槽，translate 走查询变体扩展）；
- 未配置时报告 not_configured，不静默降级、不伪造译文；
- translate 的译文只作为额外变体，原问题保留；数字不一致给出警告；
- 零信号路线不参与 RRF（防止中文问题对英文语料 BM25 全零时注入噪声）。

真实模型行为由单独的离线评测脚本执行，不在单元测试里跑。
"""
import unittest

from rag.connectors import ConnectorRegistry, InMemoryConnector
from rag.fixtures import SYNTHETIC_PUBMED
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline
from rag.retrieval_adapters import DeterministicSemanticStub


def build_pipeline(**kwargs) -> EvidenceRetrievalPipeline:
    registry = ConnectorRegistry()
    registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
    return EvidenceRetrievalPipeline(registry, **kwargs)


class TranslateStub:
    """测试用翻译器：固定译文，不加载模型。"""

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.calls = 0

    def expand(self, plan):
        self.calls += 1
        return (self.mapping.get(plan.original_question, ""),)


class MethodTokenTests(unittest.TestCase):
    def test_new_tokens_are_accepted(self):
        self.assertEqual(RetrievalRequest.parse_methods("xlingual"), ("xlingual",))
        self.assertEqual(RetrievalRequest.parse_methods("translate"), ("translate",))
        self.assertEqual(RetrievalRequest.parse_methods("bm25,xlingual,translate"), ("bm25", "xlingual", "translate"))

    def test_unknown_token_still_rejected(self):
        with self.assertRaises(ValueError):
            RetrievalRequest.parse_methods("google-translate")


class TranslateMethodTests(unittest.TestCase):
    def test_translate_adds_variant_and_keeps_original_question(self):
        question = "该患者高血压的初始降压药物应该如何选择？"
        stub = TranslateStub({question: "How should initial antihypertensive therapy be chosen for this patient with hypertension?"})
        package = build_pipeline(neural_retrievers={"translate": stub}).retrieve(
            RetrievalRequest.from_dict({"question": question, "retrieval_method": "translate", "top_k": 4}))
        self.assertEqual(stub.calls, 1)
        self.assertEqual(package.query_plan.original_question, question)
        variants = package.query_plan.variants
        self.assertIn(question, variants)
        self.assertTrue(any("antihypertensive" in variant for variant in variants))
        sources = {entry["source"] for entry in package.query_plan.expansion_trace}
        self.assertIn("machine_translation_zh_en", sources)
        states = {item["method"]: item["status"] for item in package.trace["method_trace"]}
        self.assertEqual(states.get("translate"), "ok")

    def test_translate_unconfigured_reports_not_configured(self):
        package = build_pipeline().retrieve(
            RetrievalRequest.from_dict({"question": "高血压", "retrieval_method": "translate"}))
        states = {item["method"]: item["status"] for item in package.trace["method_trace"]}
        self.assertEqual(states.get("translate"), "not_configured")
        self.assertTrue(any("translate" in warning and "not_configured" in warning for warning in package.warnings))

    def test_translate_numeric_mismatch_is_warned(self):
        question = "2月龄婴儿发热的评估推荐是什么？"
        stub = TranslateStub({question: "What are the recommendations for fever assessment in infants?"})
        package = build_pipeline(neural_retrievers={"translate": stub}).retrieve(
            RetrievalRequest.from_dict({"question": question, "retrieval_method": "translate", "top_k": 4}))
        self.assertTrue(any("数字/单位" in warning for warning in package.warnings))
        entry = next(item for item in package.trace["method_trace"] if item.get("method") == "translate")
        self.assertTrue(entry.get("numeric_mismatches"))


class XlingualMethodTests(unittest.TestCase):
    def test_xlingual_uses_neural_slot_and_fuses_via_rrf(self):
        stub = DeterministicSemanticStub({doc.identity_key(): 0.9 if index == 0 else 0.2 for index, doc in enumerate(SYNTHETIC_PUBMED)})
        package = build_pipeline(neural_retrievers={"xlingual": stub}).retrieve(
            RetrievalRequest.from_dict({"question": "hypertension", "retrieval_method": "bm25,xlingual", "top_k": 4}))
        states = {item["method"]: item["status"] for item in package.trace["method_trace"]}
        self.assertEqual(states.get("xlingual"), "ok")
        first = package.ranked_documents[0]
        self.assertEqual(first.fusion_method, "bm25+xlingual_rrf")
        self.assertEqual(first.semantic_score, 0.9)

    def test_xlingual_unconfigured_reports_not_configured(self):
        package = build_pipeline().retrieve(
            RetrievalRequest.from_dict({"question": "hypertension", "retrieval_method": "xlingual"}))
        states = {item["method"]: item["status"] for item in package.trace["method_trace"]}
        self.assertEqual(states.get("xlingual"), "not_configured")


class ZeroSignalRrfTests(unittest.TestCase):
    """零信号路线不得参与 RRF：中文问题对英文 fixtures 的 BM25 常为全零。"""

    def test_zero_signal_lexical_route_does_not_perturb_fusion(self):
        # 构造一个中文问题，词面对英文 fixture 全零，同时 neural stub 给出明确排序。
        stub = DeterministicSemanticStub({doc.identity_key(): 0.9 - 0.1 * index for index, doc in enumerate(SYNTHETIC_PUBMED)})
        package = build_pipeline(neural_retrievers={"xlingual": stub}).retrieve(
            RetrievalRequest.from_dict({"question": "完全中文的问题没有英文词", "retrieval_method": "bm25,xlingual", "top_k": 3}))
        # BM25 全零 -> 只有 xlingual 提供排序；RRF 不应包含 bm25 的文档 ID 名次噪声。
        first = package.ranked_documents[0]
        self.assertEqual(first.document.identity_key(), SYNTHETIC_PUBMED[0].identity_key())
        self.assertIn("xlingual", first.fusion_method)


if __name__ == "__main__":
    unittest.main()
