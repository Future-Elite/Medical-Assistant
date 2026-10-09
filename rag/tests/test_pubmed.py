"""PubMed parsing tests use recorded XML, never the live NCBI service."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from rag.models import RetrievalRequest
from rag.pubmed import PubMedConnector, _article_to_document, _pubmed_queries
from rag.query import build_query_plan


class TestPubMedParsing(unittest.TestCase):
    def test_article_preserves_real_source_identifiers_and_locator(self) -> None:
        article = ET.fromstring("""
        <PubmedArticle><MedlineCitation><PMID>12345678</PMID><Article>
        <ArticleTitle>Recorded title</ArticleTitle><Abstract><AbstractText Label="BACKGROUND">Recorded abstract.</AbstractText></Abstract>
        <Journal><Title>Recorded Journal</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
        </Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/recorded</ArticleId></ArticleIdList></PubmedData></PubmedArticle>
        """)
        document = _article_to_document(article, "pubmed-live", "recorded query")
        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.pmid, "12345678")
        self.assertEqual(document.locator, "Abstract")
        self.assertEqual(document.url, "https://pubmed.ncbi.nlm.nih.gov/12345678/")

    def test_query_planner_cleans_and_combines_medical_terms(self) -> None:
        request = RetrievalRequest.from_dict({
            "question": "Find recent studies on osimertinib-associated interstitial lung disease risk factors"
        })
        plan = build_query_plan(request, [{"source_id": "pubmed-live", "kinds": ["pubmed"]}])
        self.assertEqual(
            plan.filters["pubmed_terms"],
            ["osimertinib", "interstitial lung disease", "risk factors"],
        )
        self.assertEqual(
            _pubmed_queries(plan),
            (
                'osimertinib AND "interstitial lung disease" AND "risk factors"',
                'osimertinib AND "interstitial lung disease"',
            ),
        )

    def test_reviewed_english_expansion_is_kept_as_a_phrase(self) -> None:
        request = RetrievalRequest.from_dict({
            "question": "间质性肺病的治疗证据",
            "user_terms": {"间质性肺病": ["interstitial-lung disease"]},
        })
        plan = build_query_plan(request, [{"source_id": "pubmed-live", "kinds": ["pubmed"]}])
        self.assertEqual(plan.filters["pubmed_terms"], ["interstitial lung disease"])

    def test_empty_precise_query_retries_with_relaxed_query(self) -> None:
        class StubConnector(PubMedConnector):
            def __post_init__(self) -> None:
                self.calls = []

            def _search_ids(self, query: str, limit: int) -> list[str]:
                self.calls.append(query)
                return [] if len(self.calls) == 1 else ["123"]

            def _fetch_articles(self, identifiers, query):
                return []

        request = RetrievalRequest.from_dict({
            "question": "recent studies on osimertinib-associated interstitial lung disease risk factors"
        })
        plan = build_query_plan(request, [{"source_id": "pubmed-live", "kinds": ["pubmed"]}])
        connector = StubConnector(email="test@example.com")
        result = connector.search(plan, limit=5)
        self.assertEqual(connector.calls, list(_pubmed_queries(plan)))
        self.assertTrue(any("第 2 轮放宽查询" in warning for warning in result.warnings))
