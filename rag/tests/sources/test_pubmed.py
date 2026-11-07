import io
import json
import unittest
import urllib.error

from rag.models import QueryPlan
from rag.pubmed import (
    PubMedConnector, PubMedError, _parse_efetch, _parse_europepmc, _upstream_error_text,
)


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

    def test_esearch_empty_falls_back_to_europepmc_pmids(self):
        connector = PubMedConnector()
        connector._esearch_ids = lambda params, query: []
        connector._europepmc_ids = lambda query, limit: (["30012345"], "")
        connector._request = lambda endpoint, params: EF
        plan = QueryPlan(
            "该新生儿感染性肺炎的评估与抗感染推荐是什么？",
            ("q",), ("neonatal pneumonia",), ("pubmed-live",),
            {"source_kinds": ["pubmed"]}, (),
            english_terms=("neonatal pneumonia",),
            question_english_terms=("neonatal", "pneumonia"),
        )
        result = connector.search(plan, limit=1)
        self.assertEqual(result.documents[0].pmid, "30012345")
        self.assertTrue(any("Europe PMC" in warning for warning in result.warnings))

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

    def test_esearch_outage_uses_europepmc_records(self):
        connector = PubMedConnector()
        connector._esearch_ids = lambda params, query: (_ for _ in ()).throw(
            PubMedError("PubMed upstream HTTP error", stage="esearch", status_code=500)
        )
        connector._europepmc_ids = lambda query, limit: ([], "")
        connector._europepmc_documents = lambda query, limit: (
            [_epmc_document()], ""
        )
        plan = QueryPlan(
            "新生儿感染性肺炎", ("q",), ("neonatal pneumonia",), ("pubmed-live",),
            {"source_kinds": ["pubmed"]}, (),
            english_terms=("neonatal pneumonia",),
        )
        result = connector.search(plan, limit=3)
        self.assertEqual(result.documents[0].pmid, "30012345")
        self.assertEqual(result.documents[0].metadata["upstream"], "europepmc_fallback")
        self.assertTrue(any("Europe PMC" in warning for warning in result.warnings))

    def test_efetch_outage_resolves_pmids_through_europepmc(self):
        connector = PubMedConnector()
        connector._esearch_ids = lambda params, query: ["30012345"]

        def fail_efetch(endpoint, params):
            raise PubMedError("PubMed upstream HTTP error", stage=endpoint, status_code=500)

        connector._request = fail_efetch
        connector._europepmc_documents = lambda query, limit: (
            [_epmc_document()], ""
        )
        plan = QueryPlan(
            "新生儿感染性肺炎", ("q",), ("neonatal pneumonia",), ("pubmed-live",),
            {"source_kinds": ["pubmed"]}, (),
            english_terms=("neonatal pneumonia",),
        )
        result = connector.search(plan, limit=3)
        self.assertEqual(result.documents[0].pmid, "30012345")
        self.assertTrue(any("EFetch" in warning for warning in result.warnings))

    def test_parse_europepmc_maps_med_records_and_skips_missing_abstract(self):
        payload = {
            "resultList": {"result": [
                {
                    "source": "MED", "pmid": "30012345", "doi": "10.1/rds",
                    "title": "Surfactant in <i>neonatal</i> RDS",
                    "abstractText": "<h4>Background</h4>Abstract text.",
                    "firstPublicationDate": "2025-01-02",
                    "journalInfo": {"journal": {"title": "Test Journal"}},
                    "pubTypeList": {"pubType": ["Journal Article", "Review"]},
                },
                {"source": "MED", "pmid": "30099999", "title": "No abstract"},
                {"source": "PMC", "pmid": "30088888", "title": "x", "abstractText": "y"},
            ]}
        }
        documents, skipped = _parse_europepmc(payload, "pubmed-live")
        self.assertEqual(len(documents), 1)
        self.assertEqual(skipped, 1)
        document = documents[0]
        self.assertEqual(document.pmid, "30012345")
        self.assertEqual(document.title, "Surfactant in neonatal RDS")
        self.assertEqual(document.text, "Background Abstract text.")
        self.assertEqual(document.published_at, "2025-01-02")
        self.assertEqual(document.metadata["journal"], "Test Journal")
        self.assertEqual(document.metadata["publication_types"], ["Journal Article", "Review"])

    def test_esearch_backend_error_is_reported_with_reason(self):
        connector = PubMedConnector()
        connector._request = lambda endpoint, params: json.dumps({
            "esearchresult": {
                "ERROR": "Search Backend failed: Search is temporarily unavailable. "
                         "Details: Cannot connect to SOLR"
            }
        }).encode()
        with self.assertRaises(PubMedError) as context:
            connector._esearch_ids({}, "neonatal pneumonia")
        self.assertEqual(context.exception.stage, "esearch_backend")
        self.assertIn("SOLR", str(context.exception))

    def test_http_error_body_reason_is_redacted(self):
        body = b'<html><ERROR>Cannot connect to SOLR (url https://eutils.ncbi.nlm.nih.gov/?api_key=SECRET&term=x)</ERROR></html>'
        exc = urllib.error.HTTPError("https://eutils.ncbi.nlm.nih.gov", 500, "err", {}, io.BytesIO(body))
        reason = _upstream_error_text(exc, "SECRET")
        self.assertIn("Cannot connect to SOLR", reason)
        self.assertNotIn("SECRET", reason)
        self.assertIn("api_key=[REDACTED]", reason)

    def test_http_error_html_diagnostic_reports_server(self):
        body = b'<html><body>Server Error\nDiagnostic Information:\nError: 500\nServer: eutils102\n</body></html>'
        exc = urllib.error.HTTPError("https://eutils.ncbi.nlm.nih.gov", 500, "err", {}, io.BytesIO(body))
        reason = _upstream_error_text(exc)
        self.assertIn("Error 500", reason)
        self.assertIn("eutils102", reason)


def _epmc_document():
    from rag.models import SourceDocument
    return SourceDocument(
        source_id="pubmed-live", document_id="pmid-30012345", kind="pubmed",
        title="Surfactant in neonatal RDS", text="Abstract text.", pmid="30012345",
        published_at="2025-01-02", locator="Abstract",
        metadata={"live_source": True, "content_mode": "abstract", "upstream": "europepmc_fallback"},
    )
