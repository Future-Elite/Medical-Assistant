import unittest

from rag import __version__
from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline


class PipelineTests(unittest.TestCase):
    def setUp(self):
        registry = ConnectorRegistry()
        registry.register(VersionedGuidelineConnector("guideline-fixture", SYNTHETIC_GUIDELINES))
        registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
        self.tool = EvidenceRetrievalPipeline(registry)

    def test_joint_retrieval_has_source_trace_and_citations(self):
        package = self.tool.retrieve(RetrievalRequest.from_dict({
            "request_id": "p1", "question": "高血压指南推荐和最新证据？", "as_of": "2025-12-31",
            "user_terms": {"高血压": ["hypertension"]}, "mesh_terms": ["Hypertension"], "top_k": 4,
        }))
        self.assertEqual(package.trace["rag_version"], __version__)
        self.assertEqual(package.trace["candidate_count"], len(SYNTHETIC_PUBMED) + len(SYNTHETIC_GUIDELINES))
        self.assertTrue(package.citations)
        self.assertTrue(any(row["source_id"] == "guideline-fixture" for row in package.trace["connector_trace"]))
        self.assertTrue(all(c.retrieval_id if hasattr(c, "retrieval_id") else True for c in package.citations))
        self.assertTrue(any(c.version_id for c in package.citations if c.kind == "guideline"))

    def test_superseded_and_future_are_excluded(self):
        package = self.tool.retrieve(RetrievalRequest.from_dict({
            "question": "高血压指南推荐", "as_of": "2023-06-01", "top_k": 8,
        }))
        self.assertFalse(any(c.document_id == "guide-2023-old" for c in package.citations))
        self.assertFalse(any(c.document_id == "guide-2024-a" for c in package.citations))
        self.assertTrue(any("排除" in warning for warning in package.warnings))

    def test_version_filter_warning_and_citation_fields(self):
        package = self.tool.retrieve(RetrievalRequest.from_dict({
            "question": "高血压指南推荐", "filters": {"guideline_version": "2024"},
            "user_terms": {"高血压": ["hypertension"]}, "top_k": 3,
        }))
        guide = next(c for c in package.citations if c.kind == "guideline")
        self.assertEqual(guide.version_id, "2024")
        self.assertEqual(guide.recommendation_id, "2.1")
        self.assertTrue(guide.key_evidence_sentences)


class AgentTests(unittest.TestCase):
    def test_citation_is_scoped_to_retrieval(self):
        from rag.agent import EvidenceRAG
        registry = ConnectorRegistry()
        registry.register(InMemoryConnector("pubmed", ("pubmed",), SYNTHETIC_PUBMED))
        rag = EvidenceRAG(EvidenceRetrievalPipeline(registry))
        first = rag.retrieve_evidence({"question": "hypertension"})
        citation_id = first["citations"][0]["citation_id"]
        opened = rag.open_citation({"retrieval_id": first["retrieval_id"], "citation_id": citation_id})
        self.assertEqual(opened["citation"]["citation_id"], citation_id)
        with self.assertRaises(ValueError):
            rag.open_citation({"retrieval_id": "retrieval-unknown", "citation_id": citation_id})
