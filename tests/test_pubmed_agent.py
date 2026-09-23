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
        self.assertEqual(result.artifact["records"][0]["doi"], "10.1000/example")
        self.assertEqual(get.call_count, 2)

    def test_rejects_reversed_year_range(self):
        with self.assertRaises(ValueError):
            PubMedSearchInput(query="test query", year_from=2025, year_to=2020)


if __name__ == "__main__":
    unittest.main()
