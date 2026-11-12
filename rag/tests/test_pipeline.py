import unittest

from rag import __version__
from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED
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
            "request_id": "p1", "question": "新生儿呼吸窘迫综合征的指南推荐和最新证据？", "as_of": "2026-04-15",
            "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]},
            "english_terms": ["respiratory distress syndrome"],
            "mesh_terms": ["Respiratory Distress Syndrome, Newborn"], "top_k": 4,
        }))
        self.assertEqual(package.trace["rag_version"], __version__)
        self.assertEqual(package.trace["candidate_count"], len(SYNTHETIC_PUBMED) + len(SYNTHETIC_GUIDELINES))
        self.assertTrue(package.citations)
        self.assertTrue(any(row["source_id"] == "guideline-fixture" for row in package.trace["connector_trace"]))
        self.assertTrue(any(c.version_id for c in package.citations if c.kind == "guideline"))

    def test_superseded_and_future_are_excluded(self):
        package = self.tool.retrieve(RetrievalRequest.from_dict({
            "question": "新生儿呼吸窘迫综合征指南推荐", "as_of": "2023-06-01", "top_k": 8,
        }))
        self.assertFalse(any(c.document_id == "guide-rds-2023-old" for c in package.citations))
        self.assertFalse(any(c.document_id == "guide-rds-2025" for c in package.citations))
        self.assertTrue(any("排除" in warning for warning in package.warnings))

    def test_version_filter_warning_and_citation_fields(self):
        package = self.tool.retrieve(RetrievalRequest.from_dict({
            "question": "新生儿呼吸窘迫综合征指南推荐", "filters": {"guideline_version": "2025"},
            "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]}, "top_k": 3,
        }))
        guide = next(c for c in package.citations if c.kind == "guideline")
        self.assertEqual(guide.version_id, "2025")
        self.assertEqual(guide.recommendation_id, "3.1")
        self.assertTrue(guide.key_evidence_sentences)

    def test_future_dated_guideline_is_excluded(self):
        """未来发表的 BPD 指南在 as_of 前不得进入结果（版本号不等于发表时间）。"""
        package = self.tool.retrieve(RetrievalRequest.from_dict({
            "question": "bronchopulmonary dysplasia definition", "as_of": "2026-05-20", "top_k": 8,
        }))
        self.assertNotIn("guide-bpd-2027", {item.document.document_id for item in package.ranked_documents})


class AgentTests(unittest.TestCase):
    def test_citation_is_scoped_to_retrieval(self):
        from rag.agent import EvidenceRAG
        registry = ConnectorRegistry()
        registry.register(InMemoryConnector("pubmed", ("pubmed",), SYNTHETIC_PUBMED))
        rag = EvidenceRAG(EvidenceRetrievalPipeline(registry))
        first = rag.retrieve_evidence({"question": "surfactant"})
        citation_id = first["citations"][0]["citation_id"]
        opened = rag.open_citation({"retrieval_id": first["retrieval_id"], "citation_id": citation_id})
        self.assertEqual(opened["citation"]["citation_id"], citation_id)
        with self.assertRaises(ValueError):
            rag.open_citation({"retrieval_id": "retrieval-unknown", "citation_id": citation_id})
