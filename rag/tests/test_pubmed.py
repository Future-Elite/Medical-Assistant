import json
import unittest

from rag.models import QueryPlan
from rag.pubmed import PubMedConnector, PubMedError, _parse_efetch


ES = json.dumps({"esearchresult": {"idlist": ["30012345"]}}).encode()
EF = b'''<eFetchResult><PubmedArticle><MedlineCitation><PMID>30012345</PMID><Article><ArticleTitle>Surfactant in neonatal respiratory distress syndrome</ArticleTitle><Abstract><AbstractText>Abstract text.</AbstractText></Abstract></Article><Journal><Journal><Title>Test Journal</Title></Journal></Journal><PubDate><Year>2025</Year><Month>Jan</Month><Day>2</Day></PubDate></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/rds</ArticleId></ArticleIdList></PubmedData></PubmedArticle></eFetchResult>'''


class FakeTransport:
    def __init__(self): self.calls=[]
    def __call__(self, url, params, timeout):
        self.calls.append((url, dict(params), timeout))
        return ES if url.endswith("esearch.fcgi") else EF


class PubMedConnectorTests(unittest.TestCase):
    def test_search_maps_fields_and_keeps_patient_data_out(self):
        transport = FakeTransport()
        connector = PubMedConnector(transport=transport)
        plan = QueryPlan("patient question", ("patient question",), ("respiratory distress syndrome",), ("pubmed-live",), {"source_kinds": ["pubmed"]}, (), english_terms=("respiratory distress syndrome",), mesh_terms=("Respiratory Distress Syndrome, Newborn",))
        result = connector.search(plan, limit=3)
        self.assertEqual(result.documents[0].pmid, "30012345")
        self.assertEqual(result.documents[0].url, "https://pubmed.ncbi.nlm.nih.gov/30012345/")
        self.assertEqual(result.documents[0].locator, "Abstract")
        self.assertEqual(result.documents[0].doi, "10.1/rds")
        self.assertEqual(result.documents[0].published_at, "2025-01-02")
        self.assertNotIn("patient", transport.calls[0][1]["term"].lower())
        self.assertNotIn("patient question", transport.calls[0][1]["term"].lower())
        self.assertTrue(result.documents[0].metadata["live_source"])

    def test_http_failure_is_typed(self):
        def fail(url, params, timeout):
            raise PubMedError("rate limited", stage="esearch", status_code=429, retryable=True)
        connector = PubMedConnector(transport=fail)
        plan = QueryPlan("q", ("q",), ("respiratory distress syndrome",), ("pubmed-live",), {}, ())
        with self.assertRaises(PubMedError) as context:
            connector.search(plan, limit=1)
        self.assertEqual(context.exception.status_code, 429)
        self.assertTrue(context.exception.retryable)

    def test_parse_missing_abstract_skips(self):
        raw = b"<eFetchResult><PubmedArticle><MedlineCitation><PMID>1</PMID><Article><ArticleTitle>x</ArticleTitle></Article></MedlineCitation></PubmedArticle></eFetchResult>"
        docs, skipped = _parse_efetch(raw, "pubmed-live")
        self.assertEqual(docs, [])
        self.assertEqual(skipped, 1)

    def test_parse_rejects_entity_declarations(self):
        raw = b'<!DOCTYPE eFetchResult [<!ENTITY x "y">]><eFetchResult>&x;</eFetchResult>'
        with self.assertRaises(PubMedError):
            _parse_efetch(raw, "pubmed-live")

    def test_parse_accepts_plain_external_doctype(self):
        raw = b'<?xml version="1.0"?><!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle//EN" "https://example.invalid/pubmed.dtd">' + EF
        docs, skipped = _parse_efetch(raw, "pubmed-live")
        self.assertEqual(len(docs), 1)
        self.assertEqual(skipped, 0)

    def test_question_english_terms_reach_live_query(self):
        transport = FakeTransport()
        connector = PubMedConnector(transport=transport)
        plan = QueryPlan(
            "该患儿 surfactant 的证据", ("该患儿 surfactant 的证据",), (), ("pubmed-live",),
            {"source_kinds": ["pubmed"]}, (),
            english_terms=("respiratory distress syndrome",), question_english_terms=("surfactant",),
        )
        connector.search(plan, limit=1)
        term = transport.calls[0][1]["term"]
        self.assertIn("respiratory distress syndrome", term)
        self.assertIn("surfactant", term)

    def test_fallback_prefers_ascii_concepts_over_cjk(self):
        from rag.pubmed import _safe_term
        plan = QueryPlan("问题", ("问题",), ("surfactant", "肺表面活性物质"), ("pubmed-live",), {}, ())
        term = _safe_term(plan)
        self.assertIn("surfactant", term)
        self.assertNotIn("肺表面活性物质", term)

    def test_units_do_not_become_required_pubmed_terms(self):
        from rag.pubmed import _safe_term
        plan = QueryPlan(
            "该低出生体重儿生后2小时，血糖2.1 mmol/L，新生儿低血糖的筛查和喂养推荐是什么？",
            ("q",), (), ("pubmed-live",), {"source_kinds": ["pubmed"]}, (),
            english_terms=("neonatal hypoglycemia", "blood glucose", "low birth weight infant"),
            mesh_terms=("Hypoglycemia", "Infant, Newborn"),
            question_english_terms=("mmol/l",),
        )
        term = _safe_term(plan)
        self.assertIn("neonatal hypoglycemia", term)
        self.assertIn("neonatal OR newborn OR infant OR preterm", term)
        self.assertNotIn("mmol/l", term.lower())
        # Reviewed synonyms are fallback-only; they must not dilute the anchor.
        self.assertNotIn("blood glucose", term.lower())

    def test_primary_no_hits_falls_back_to_reviewed_synonyms(self):
        class EmptyThenHit:
            def __init__(self):
                self.terms = []

            def __call__(self, url, params, timeout):
                if url.endswith("esearch.fcgi"):
                    self.terms.append(dict(params).get("term", ""))
                    if len(self.terms) == 1:
                        return json.dumps({"esearchresult": {"idlist": []}}).encode()
                    return ES
                return EF

        transport = EmptyThenHit()
        connector = PubMedConnector(transport=transport)
        plan = QueryPlan(
            "该低出生体重儿生后2小时，血糖2.1 mmol/L，新生儿低血糖的筛查和喂养推荐是什么？",
            ("q",), (), ("pubmed-live",), {"source_kinds": ["pubmed"]}, (),
            english_terms=("neonatal hypoglycemia", "blood glucose", "low birth weight infant"),
            mesh_terms=("Hypoglycemia", "Infant, Newborn"),
            question_english_terms=("mmol/l",),
        )
        connector.search(plan, limit=1)
        self.assertEqual(len(transport.terms), 2)
        self.assertIn("neonatal hypoglycemia", transport.terms[0])
        self.assertIn("blood glucose", transport.terms[1])
        self.assertNotIn("mmol/l", transport.terms[1].lower())
