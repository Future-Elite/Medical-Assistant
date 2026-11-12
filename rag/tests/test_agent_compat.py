"""Agent 集成兼容面：``main`` 分支 ``agent/`` 直接导入的 RAG 接口。

agent 侧（不由本仓库维护）仍从以下位置导入符号：
``rag.audit.MiniCheckVerifier``、``rag.pubmed.PUBMED_SEARCH_CACHE``、
``rag.guidelines.who_guideline_metadata_registry``，并读取
``trace.connector_trace[].date_filter`` / ``cache_hit``。本模块把这些接口的
行为固定在发布分支上；任何移除都必须先与 agent 维护方对齐。
"""
import unittest

from rag.audit import MiniCheckVerifier
from rag.config import RAGConfig
from rag.guidelines import GuidelineMetadataConnector, who_guideline_metadata_registry
from rag.models import QueryPlan
from rag.pubmed import PUBMED_SEARCH_CACHE, PubMedConnector, _safe_term


class MiniCheckCompatibilityTests(unittest.TestCase):
    def test_disabled_by_default_without_model(self):
        verifier = MiniCheckVerifier()
        self.assertEqual(verifier.name, "minicheck")
        self.assertFalse(verifier.available)
        self.assertIn("minicheck_model", verifier.unavailable_reason)

    def test_config_keeps_legacy_fields(self):
        config = RAGConfig()
        self.assertEqual(config.minicheck_model, "")
        self.assertEqual(config.minicheck_cache_dir, "")


class PubMedCacheCompatibilityTests(unittest.TestCase):
    def test_context_cache_is_reused_within_one_turn(self):
        class Transport:
            def __init__(self) -> None:
                self.calls = []

            def __call__(self, url, params, timeout):
                self.calls.append(url)
                if "esearch" in url:
                    return b'{"esearchresult": {"idlist": ["123"]}}'
                return b"<PubmedArticleSet></PubmedArticleSet>"

        transport = Transport()
        connector = PubMedConnector(transport=transport)
        plan = QueryPlan("q", (), (), ("pubmed-live",), {}, (), machine_translations=("neonatal sepsis",))
        token = PUBMED_SEARCH_CACHE.set({})
        try:
            first = connector.search(plan, limit=3)
            self.assertFalse(connector.last_cache_hit)
            calls_after_first = len(transport.calls)
            second = connector.search(plan, limit=3)
            self.assertTrue(connector.last_cache_hit)
        finally:
            PUBMED_SEARCH_CACHE.reset(token)
        self.assertIs(first, second)
        self.assertEqual(len(transport.calls), calls_after_first)
        self.assertEqual(calls_after_first, 2)  # esearch + efetch
        self.assertEqual(connector.last_date_filter, {})

    def test_publication_window_is_validated(self):
        connector = PubMedConnector(transport=lambda url, params, timeout: b'{"esearchresult": {"idlist": []}}')
        plan = QueryPlan("neonatal sepsis", ("neonatal sepsis",), (), (), {"publication_from": "2024-02-30"}, ())
        with self.assertRaises(ValueError):
            connector.search(plan, limit=1)


class WhoRegistryCompatibilityTests(unittest.TestCase):
    def test_registry_keeps_topic_and_population_scope(self):
        connector = GuidelineMetadataConnector("who-guideline-live", who_guideline_metadata_registry())
        cases = (
            ("neonatal pneumonia treatment", 0),
            ("neonatal hypertension treatment", 0),
            ("adult hypertension treatment", 1),
        )
        for query, expected in cases:
            plan = QueryPlan(query, (query,), (), (), {}, ())
            with self.subTest(query=query):
                self.assertEqual(len(connector.search(plan, limit=8).documents), expected)


class SafeTermCompatibilityTests(unittest.TestCase):
    def test_single_dynamic_translation_is_not_rewrapped(self):
        plan = QueryPlan(
            "q", (), (), (), {}, (),
            machine_translations=('("pneumonia") AND ("neonatal" OR "newborn")',),
        )
        self.assertEqual(_safe_term(plan), '("pneumonia") AND ("neonatal" OR "newborn")')


if __name__ == "__main__":
    unittest.main()
