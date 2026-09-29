import unittest
from unittest.mock import Mock, patch

from medrax.tools.pubmed import PubMedEvidenceTool, PubMedSearchInput


ARTICLE_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345678</PMID>
      <Article>
        <ArticleTitle>Example clinical study</ArticleTitle>
        <Abstract><AbstractText Label="RESULTS">Evidence result.</AbstractText></Abstract>
        <AuthorList><Author><ForeName>Jane</ForeName><LastName>Doe</LastName></Author></AuthorList>
        <Journal><Title>Example Journal</Title><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>
      </Article>
    </MedlineCitation>
    <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/example</ArticleId></ArticleIdList></PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""


class PubMedEvidenceToolTest(unittest.TestCase):
    @patch("medrax.tools.pubmed.requests.get")
    def test_empty_search_requests_one_relaxed_retry_without_pmids(self, get):
        response = Mock()
        response.json.return_value = {"esearchresult": {"idlist": []}}
        response.raise_for_status.return_value = None
        get.return_value = response

        content, artifact = PubMedEvidenceTool()._run(
            "osimertinib interstitial lung disease",
            year_from=2019,
            year_to=2024,
        )

        self.assertEqual(artifact["search_status"], "empty")
        self.assertTrue(artifact["retry_recommended"])
        self.assertEqual(artifact["evidence"], [])
        self.assertIn("retry exactly once", content)
        self.assertIn("do not output any PMID", content)

    @patch("medrax.tools.pubmed.requests.get")
    def test_search_returns_citable_records(self, get):
        search = Mock()
        search.json.return_value = {"esearchresult": {"idlist": ["12345678"]}}
        search.raise_for_status.return_value = None
        fetch = Mock(text=ARTICLE_XML)
        fetch.raise_for_status.return_value = None
        get.side_effect = [search, fetch]

        result = PubMedEvidenceTool().invoke(
            {
                "name": "search_pubmed_evidence",
                "args": {"query": "hypertension treatment"},
                "id": "test-call",
                "type": "tool_call",
            }
        )

        self.assertIn("PMID:12345678", result.content)
        evidence = result.artifact["evidence"][0]
        self.assertEqual(evidence["source_type"], "pubmed")
        self.assertEqual(evidence["source_id"], "PMID:12345678")
        self.assertEqual(evidence["metadata"]["doi"], "10.1000/example")
        self.assertEqual(result.artifact["search_status"], "success")
        self.assertFalse(result.artifact["retry_recommended"])
        self.assertEqual(get.call_count, 2)

    @patch("medrax.tools.pubmed.requests.get")
    def test_unreadable_fetch_is_treated_as_empty_evidence(self, get):
        search = Mock()
        search.json.return_value = {"esearchresult": {"idlist": ["12345678"]}}
        search.raise_for_status.return_value = None
        fetch = Mock()
        fetch.text = "<PubmedArticleSet />"
        fetch.raise_for_status.return_value = None
        get.side_effect = [search, fetch]

        content, artifact = PubMedEvidenceTool()._run("rare disease treatment")

        self.assertEqual(artifact["search_status"], "empty")
        self.assertTrue(artifact["retry_recommended"])
        self.assertEqual(artifact["evidence"], [])
        self.assertIn("do not output any PMID", content)

    def test_rejects_reversed_year_range(self):
        with self.assertRaises(ValueError):
            PubMedSearchInput(query="test query", year_from=2025, year_to=2020)


if __name__ == "__main__":
    unittest.main()
