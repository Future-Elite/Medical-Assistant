"""Backward-compatible import paths for external Agent code.

The implementation moved into subpackages; ``rag/__init__.py`` registers the
historical module paths as aliases so callers do not need to change imports.
"""
import importlib
import unittest


class LegacyImportAliasTests(unittest.TestCase):
    def test_historical_modules_resolve_to_the_same_objects(self):
        import rag

        cases = {
            "rag.models": "rag.core.models",
            "rag.config": "rag.core.config",
            "rag.his": "rag.core.his",
            "rag.pipeline": "rag.retrieval.pipeline",
            "rag.connectors": "rag.sources.connectors",
            "rag.custom_sources": "rag.sources.custom_sources",
            "rag.pubmed": "rag.sources.pubmed",
            "rag.guidelines": "rag.sources.guidelines",
            "rag.demo": "rag.sources.demo",
            "rag.neural_adapters": "rag.adapters.neural",
            "rag.audit": "rag.agent.audit",
        }
        for legacy, canonical in cases.items():
            module = importlib.import_module(legacy)
            self.assertEqual(module.__name__, canonical, legacy)
            self.assertIs(module, importlib.import_module(canonical), legacy)
        self.assertTrue(hasattr(rag, "models"))
        self.assertTrue(hasattr(rag, "pubmed"))

    def test_external_agent_imports_remain_available(self):
        from rag.agent import EvidenceRAG
        from rag.audit import GroundedAnswerAuditor
        from rag.pubmed import PUBMED_SEARCH_CACHE
        from rag.guidelines import who_guideline_metadata_registry
        from rag.models import RetrievalRequest
        from rag.pipeline import EvidenceRetrievalPipeline
        from rag.his import PatientSnapshot
        from rag.connectors import ConnectorRegistry
        from rag.custom_sources import CustomSourceLibrary
        from rag.demo import load_demo_cases
        from rag.neural_adapters import build_neural_registry
        from rag.config import RAGConfig

        self.assertTrue(EvidenceRAG and GroundedAnswerAuditor and PUBMED_SEARCH_CACHE)
        self.assertTrue(who_guideline_metadata_registry and RetrievalRequest)
        self.assertTrue(EvidenceRetrievalPipeline and PatientSnapshot and ConnectorRegistry)
        self.assertTrue(CustomSourceLibrary and load_demo_cases and build_neural_registry and RAGConfig)


if __name__ == "__main__":
    unittest.main()
