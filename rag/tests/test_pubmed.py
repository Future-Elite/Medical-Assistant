import json
import unittest
import xml.etree.ElementTree as ET

from rag.models import QueryPlan
from rag.pubmed import PubMedConnector, PubMedError, _parse_efetch


ES = json.dumps({"esearchresult": {"idlist": ["123"]}}).encode()
EF = b'''<eFetchResult><PubmedArticle><MedlineCitation><PMID>123</PMID><Article><ArticleTitle>Hypertension study</ArticleTitle><Abstract><AbstractText>Abstract text.</AbstractText></Abstract></Article><Journal><Journal><Title>Test Journal</Title></Journal></Journal><PubDate><Year>2024</Year><Month>Jan</Month><Day>2</Day></PubDate></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/test</ArticleId></ArticleIdList></PubmedData></PubmedArticle></eFetchResult>'''


class FakeTransport:
    def __init__(self): self.calls=[]
    def __call__(self, url, params, timeout):
        self.calls.append((url, dict(params), timeout))
        return ES if url.endswith("esearch.fcgi") else EF


class PubMedConnectorTests(unittest.TestCase):
    def test_search_maps_fields_and_keeps_patient_data_out(self):
        transport = FakeTransport()
        connector = PubMedConnector(transport=transport)
        plan = QueryPlan("patient question", ("patient question",), ("hypertension",), ("pubmed-live",), {"source_kinds": ["pubmed"]}, (), english_terms=("hypertension",), mesh_terms=("Hypertension",))
        result = connector.search(plan, limit=3)
        self.assertEqual(result.documents[0].pmid, "123")
        self.assertEqual(result.documents[0].url, "https://pubmed.ncbi.nlm.nih.gov/123/")
        self.assertEqual(result.documents[0].locator, "Abstract")
        self.assertEqual(result.documents[0].doi, "10.1/test")
        self.assertEqual(result.documents[0].published_at, "2024-01-02")
        self.assertNotIn("patient", transport.calls[0][1]["term"].lower())
        self.assertNotIn("patient question", transport.calls[0][1]["term"].lower())
        self.assertTrue(result.documents[0].metadata["live_source"])

    def test_http_failure_is_typed(self):
        def fail(url, params, timeout):
            raise PubMedError("rate limited", stage="esearch", status_code=429, retryable=True)
        connector = PubMedConnector(transport=fail)
        plan = QueryPlan("q", ("q",), ("hypertension",), ("pubmed-live",), {}, ())
        with self.assertRaises(PubMedError) as context:
            connector.search(plan, limit=1)
        self.assertEqual(context.exception.status_code, 429)
        self.assertTrue(context.exception.retryable)

    def test_parse_missing_abstract_skips(self):
        raw = b"<eFetchResult><PubmedArticle><MedlineCitation><PMID>1</PMID><Article><ArticleTitle>x</ArticleTitle></Article></MedlineCitation></PubmedArticle></eFetchResult>"
        docs, skipped = _parse_efetch(raw, "pubmed-live")
        self.assertEqual(docs, [])
        self.assertEqual(skipped, 1)

    def test_question_english_terms_reach_live_query(self):
        transport = FakeTransport()
        connector = PubMedConnector(transport=transport)
        plan = QueryPlan(
            "该患者 metformin 的证据", ("该患者 metformin 的证据",), (), ("pubmed-live",),
            {"source_kinds": ["pubmed"]}, (),
            english_terms=("hypertension",), question_english_terms=("metformin",),
        )
        connector.search(plan, limit=1)
        term = transport.calls[0][1]["term"]
        self.assertIn("hypertension", term)
        self.assertIn("metformin", term)

    def test_fallback_prefers_ascii_concepts_over_cjk(self):
        from rag.pubmed import _safe_term
        plan = QueryPlan("问题", ("问题",), ("asthma", "哮喘管理"), ("pubmed-live",), {}, ())
        term = _safe_term(plan)
        self.assertIn("asthma", term)
        self.assertNotIn("哮喘", term)
