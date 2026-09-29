"""PubMed parsing tests use recorded XML, never the live NCBI service."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from v5.pubmed import _article_to_document


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
