"""Dynamic English query must reach recall before ranking; no network/models."""

import json
import unittest

from rag.connectors import ConnectorRegistry
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline
from rag.pubmed import PubMedConnector, PUBMED_SEARCH_CACHE


class Transport:
    def __init__(self):
        self.calls = []

    def __call__(self, url, params, timeout):
        self.calls.append(dict(params))
        if url.endswith("esearch.fcgi"):
            return json.dumps({"esearchresult": {"idlist": ["12345678"]}}).encode()
        return b'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345678</PMID><Article><ArticleTitle>Asthma treatment evidence</ArticleTitle><Abstract><AbstractText>Asthma treatment in adults.</AbstractText></Abstract><Journal><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'


class Translator:
    def __init__(self, query):
        self.query = query
        self.calls = 0

    def expand(self, plan):
        self.calls += 1
        return (self.query,)


class DynamicQueryRecallTest(unittest.TestCase):
    def test_targeted_query_and_candidate_cache_then_explicit_broadening(self):
        pipeline, transport, translator = self.pipeline('("asthma") AND ("treatment")')
        request = {"question": "哮喘治疗", "user_terms": {"哮喘": ["asthma"]},
                   "retrieval_method": "bm25,translate", "top_k": 5}
        token = PUBMED_SEARCH_CACHE.set({})
        try:
            first = pipeline.retrieve(RetrievalRequest.from_dict(request))
            self.assertEqual(transport.calls[0]["term"], '("asthma") AND ("treatment")')
            calls = len(transport.calls)
            second = pipeline.retrieve(RetrievalRequest.from_dict({**request, "clinical_intent": "evidence_update"}))
            self.assertEqual(len(transport.calls), calls)
            self.assertTrue(second.trace["connector_trace"][0]["cache_hit"])
            self.assertNotEqual(first.retrieval_id, second.retrieval_id)
            translator.query = '("asthma")'
            third = pipeline.retrieve(RetrievalRequest.from_dict({**request, "question": "哮喘"}))
            self.assertGreater(len(transport.calls), calls)
            self.assertEqual(third.trace["connector_trace"][0]["query"], '("asthma")')
            self.assertFalse(third.trace["connector_trace"][0]["cache_hit"])
        finally:
            PUBMED_SEARCH_CACHE.reset(token)

    def pipeline(self, query):
        transport = Transport()
        registry = ConnectorRegistry()
        registry.register(PubMedConnector(transport=transport))
        translator = Translator(query)
        return EvidenceRetrievalPipeline(registry, neural_retrievers={"translate": translator}), transport, translator

    def test_uncovered_chinese_concept_reaches_live_pubmed_query(self):
        pipeline, transport, translator = self.pipeline("asthma treatment in adults")
        result = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": "哮喘治疗有哪些研究证据？", "retrieval_method": "bm25,translate", "fidelity_gate": "warn",
        }))
        self.assertEqual(translator.calls, 1)
        self.assertIn("asthma treatment", transport.calls[0]["term"])
        self.assertEqual(result.query_plan.original_question, "哮喘治疗有哪些研究证据？")
        self.assertFalse(result.query_plan.english_terms)
        self.assertEqual(result.query_plan.machine_translations, ("asthma treatment in adults",))
        self.assertEqual(result.trace["connector_trace"][0]["query"], transport.calls[0]["term"])
        self.assertTrue(result.ranked_documents)
        self.assertFalse(any("没有任何英文词" in warning for warning in result.warnings))

    def test_rejected_machine_query_is_never_sent_to_pubmed(self):
        pipeline, transport, _ = self.pipeline("adult asthma treatment")
        result = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": "2月龄婴儿未记录过敏史", "retrieval_method": "translate", "fidelity_gate": "reject",
        }))
        self.assertNotIn("adult asthma", transport.calls[0]["term"])
        self.assertFalse(result.query_plan.machine_translations)
        self.assertTrue(any(item["action"] == "reject" for item in result.trace["recall_query_fidelity"]["decisions"]))


if __name__ == "__main__":
    unittest.main()
