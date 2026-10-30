import unittest

from rag import DeterministicSemanticStub
from rag.connectors import ConnectorRegistry, InMemoryConnector
from rag.tests.fixtures import SYNTHETIC_PUBMED
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline


class AdapterTests(unittest.TestCase):
    def test_semantic_adapter_is_traceable_and_not_a_medical_claim(self):
        registry = ConnectorRegistry()
        registry.register(InMemoryConnector("pubmed", ("pubmed",), SYNTHETIC_PUBMED))
        scores = {SYNTHETIC_PUBMED[0].identity_key(): 0.9, SYNTHETIC_PUBMED[1].identity_key(): 0.2}
        pipeline = EvidenceRetrievalPipeline(registry, semantic_retriever=DeterministicSemanticStub(scores))
        package = pipeline.retrieve(RetrievalRequest.from_dict({"question": "respiratory distress syndrome", "top_k": 2}))
        self.assertEqual(package.trace["semantic_trace"]["status"], "ok")
        self.assertEqual(package.trace["semantic_trace"]["retriever_id"], "deterministic-semantic-stub")
        self.assertTrue(any("RRF" in explanation for explanation in package.ranked_documents[0].rank_explanation))
        self.assertEqual(package.ranked_documents[0].semantic_score, 0.9)
