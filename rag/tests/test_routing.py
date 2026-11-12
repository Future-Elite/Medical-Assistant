"""规则路由 v0：决策函数 + shadow/active 模式（默认 off 不动 legacy）。"""
import unittest

from rag.config import RAGConfig
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline
from rag.routing import decide_route
from rag.tests.helpers import fixture_pipeline

KINDS = ["guideline", "pubmed"]


class DecideRouteTests(unittest.TestCase):
    def test_recommendation_intent_puts_guideline_first(self):
        decision = decide_route(clinical_intent="guideline_recommendation", english_coverage=True, known_kinds=KINDS)
        self.assertEqual(decision.selected_sources, ("guideline", "pubmed"))
        self.assertEqual(decision.rule, "guideline_recommendation_first")
        self.assertFalse(decision.fallback_all)

    def test_update_and_safety_put_pubmed_first(self):
        for intent, rule in (("evidence_update", "evidence_update_pubmed_first"),
                             ("drug_safety", "drug_safety_pubmed_first")):
            decision = decide_route(clinical_intent=intent, english_coverage=True, known_kinds=KINDS)
            self.assertEqual(decision.selected_sources, ("pubmed", "guideline"))
            self.assertEqual(decision.rule, rule)

    def test_no_english_drops_pubmed_but_keeps_guideline(self):
        decision = decide_route(clinical_intent="clinical_question", english_coverage=False, known_kinds=KINDS)
        self.assertEqual(decision.selected_sources, ("guideline",))
        self.assertIn("no_english_drop_pubmed", decision.rule)
        self.assertFalse(decision.fallback_all)
        self.assertTrue(any("零命中" in note for note in decision.notes))

    def test_pubmed_only_falls_back_to_all_when_english_missing(self):
        decision = decide_route(clinical_intent="clinical_question", english_coverage=False, known_kinds=["pubmed"])
        self.assertEqual(decision.selected_sources, ("pubmed",))   # 回退，避免空检索
        self.assertTrue(decision.fallback_all)
        self.assertTrue(any("回退全来源" in note for note in decision.notes))

    def test_requested_kinds_are_respected(self):
        decision = decide_route(clinical_intent="guideline_recommendation", english_coverage=True,
                                known_kinds=KINDS, requested_kinds=["pubmed"])
        self.assertEqual(decision.selected_sources, ("pubmed",))

    def test_default_intent_keeps_legacy_order(self):
        decision = decide_route(clinical_intent="clinical_question", english_coverage=True, known_kinds=KINDS)
        self.assertEqual(decision.selected_sources, ("guideline", "pubmed"))
        self.assertEqual(decision.rule, "default_all_sources")
        self.assertTrue(decision.fallback_all)


class RoutingModeTests(unittest.TestCase):
    REQUEST = {
        "question": "该患儿新生儿呼吸窘迫综合征的指南推荐是什么？",
        "as_of": "2026-04-15", "english_terms": ["respiratory distress syndrome"], "top_k": 3,
    }

    def test_off_adds_no_route_trace(self):
        package = fixture_pipeline().retrieve(RetrievalRequest.from_dict(self.REQUEST))
        self.assertNotIn("route_decision", package.trace)

    def test_shadow_records_decision_without_changing_results(self):
        off = fixture_pipeline().retrieve(RetrievalRequest.from_dict(self.REQUEST))
        shadow = fixture_pipeline(routing_mode="shadow").retrieve(RetrievalRequest.from_dict(self.REQUEST))
        self.assertEqual(
            [item.document.document_id for item in shadow.ranked_documents],
            [item.document.document_id for item in off.ranked_documents],
        )
        decision = shadow.trace["route_decision"]
        self.assertEqual(decision["mode"], "shadow")
        self.assertFalse(decision["applied"])
        self.assertEqual(decision["selected_sources"], ["guideline", "pubmed"])

    def test_active_applies_routing_order(self):
        package = fixture_pipeline(routing_mode="active").retrieve(RetrievalRequest.from_dict(self.REQUEST))
        decision = package.trace["route_decision"]
        self.assertTrue(decision["applied"])
        self.assertTrue(decision["source_order_after"])
        self.assertTrue(decision["source_order_after"][0].startswith("guideline"))

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            EvidenceRetrievalPipeline(fixture_pipeline().registry, routing_mode="sometimes")

    def test_config_default_off(self):
        self.assertEqual(RAGConfig().routing_mode, "off")


if __name__ == "__main__":
    unittest.main()
