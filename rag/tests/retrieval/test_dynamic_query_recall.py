"""Dynamic English query must reach recall before ranking; no network/models."""

import json
import unittest

from rag.connectors import ConnectorRegistry
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline
from rag.pubmed import PubMedConnector


class Transport:
    def __init__(self):
        self.calls = []

    def __call__(self, url, params, timeout):
        self.calls.append(dict(params))
        if url.endswith("esearch.fcgi"):
            return json.dumps({"esearchresult": {"idlist": ["30012345"]}}).encode()
        return b'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>30012345</PMID><Article><ArticleTitle>Surfactant therapy in neonatal respiratory distress syndrome</ArticleTitle><Abstract><AbstractText>Surfactant therapy in preterm infants with respiratory distress syndrome.</AbstractText></Abstract><Journal><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'


class Translator:
    def __init__(self, query):
        self.query = query
        self.calls = 0

    def expand(self, plan):
        self.calls += 1
        return (self.query,)


class DynamicQueryRecallTest(unittest.TestCase):
    def pipeline(self, query):
        transport = Transport()
        registry = ConnectorRegistry()
        registry.register(PubMedConnector(transport=transport))
        translator = Translator(query)
        return EvidenceRetrievalPipeline(registry, neural_retrievers={"translate": translator}), transport, translator

    def test_uncovered_chinese_concept_reaches_live_pubmed_query(self):
        pipeline, transport, translator = self.pipeline("surfactant therapy for neonatal respiratory distress syndrome")
        result = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": "新生儿呼吸窘迫综合征的肺表面活性物质治疗有哪些研究证据？",
            "retrieval_method": "bm25,translate", "fidelity_gate": "warn",
        }))
        self.assertEqual(translator.calls, 1)
        self.assertIn("surfactant therapy", transport.calls[0]["term"])
        self.assertEqual(result.query_plan.original_question, "新生儿呼吸窘迫综合征的肺表面活性物质治疗有哪些研究证据？")
        self.assertFalse(result.query_plan.english_terms)
        self.assertEqual(result.query_plan.machine_translations, ("surfactant therapy for neonatal respiratory distress syndrome",))
        self.assertEqual(result.trace["connector_trace"][0]["query"], transport.calls[0]["term"])
        self.assertTrue(result.ranked_documents)
        self.assertFalse(any("没有任何英文词" in warning for warning in result.warnings))

    def test_rejected_machine_query_is_never_sent_to_pubmed(self):
        pipeline, transport, _ = self.pipeline("adult asthma treatment")
        result = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": "2月龄婴儿未记录过敏史", "retrieval_method": "translate", "fidelity_gate": "reject",
        }))
        self.assertFalse(any("adult asthma" in str(call.get("term", "")) for call in transport.calls))
        self.assertEqual(transport.calls, [])  # no usable English query -> don't call PubMed at all
        self.assertFalse(result.query_plan.machine_translations)
        self.assertTrue(any(item["action"] == "reject" for item in result.trace["recall_query_fidelity"]["decisions"]))


if __name__ == "__main__":
    unittest.main()
