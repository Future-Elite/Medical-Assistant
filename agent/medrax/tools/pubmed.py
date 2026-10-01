"""PubMed evidence retrieval tool backed by NCBI E-utilities."""

import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Literal, Optional, Tuple, Type

import requests
from langchain_core.callbacks import CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, model_validator

from .evidence import EvidenceAggregator


class PubMedSearchInput(BaseModel):
    """Input schema for PubMed evidence retrieval."""

    query: str = Field(..., min_length=2, description="PubMed search query")
    max_results: int = Field(default=8, ge=1, le=20, description="Maximum articles")
    year_from: Optional[int] = Field(default=None, ge=1800, le=3000)
    year_to: Optional[int] = Field(default=None, ge=1800, le=3000)

    @model_validator(mode="after")
    def validate_years(self):
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("year_from must not be later than year_to")
        return self


class PubMedEvidenceTool(BaseTool):
    """Search PubMed and return readable evidence plus structured records."""

    name: str = "search_pubmed_evidence"
    description: str = (
        "Search peer-reviewed biomedical literature in PubMed. Use for published "
        "studies, research evidence, treatment efficacy, disease mechanisms, reviews, "
        "and guidelines. Returns titles, abstracts, PMID, DOI, and PubMed URLs. Never "
        "include patient identifiers in the query."
    )
    args_schema: Type[BaseModel] = PubMedSearchInput
    response_format: Literal["content_and_artifact"] = "content_and_artifact"
    base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    timeout: float = 20.0

    def _common_params(self) -> Dict[str, str]:
        params = {"tool": os.getenv("NCBI_TOOL", "medicalagent")}
        if email := os.getenv("NCBI_EMAIL"):
            params["email"] = email
        if api_key := os.getenv("NCBI_API_KEY"):
            params["api_key"] = api_key
        return params

    @staticmethod
    def _text(element: Optional[ET.Element]) -> str:
        return "" if element is None else "".join(element.itertext()).strip()

    def _parse_articles(self, xml_text: str) -> List[Dict[str, Any]]:
        records = []
        for item in ET.fromstring(xml_text).findall(".//PubmedArticle"):
            citation = item.find("./MedlineCitation")
            article = citation.find("./Article") if citation is not None else None
            if citation is None or article is None:
                continue

            pmid = self._text(citation.find("./PMID"))
            title = self._text(article.find("./ArticleTitle"))
            abstract_parts = []
            for part in article.findall("./Abstract/AbstractText"):
                text = self._text(part)
                label = part.attrib.get("Label")
                if text:
                    abstract_parts.append(f"{label}: {text}" if label else text)

            journal = self._text(article.find("./Journal/Title"))
            date_text = self._text(article.find("./Journal/JournalIssue/PubDate"))
            year_match = re.search(r"\b(18|19|20|21)\d{2}\b", date_text)
            year = int(year_match.group()) if year_match else None

            doi = None
            for article_id in item.findall("./PubmedData/ArticleIdList/ArticleId"):
                if article_id.attrib.get("IdType") == "doi":
                    doi = self._text(article_id)
                    break

            authors = []
            for author in article.findall("./AuthorList/Author"):
                collective = self._text(author.find("./CollectiveName"))
                personal = " ".join(
                    value
                    for value in (
                        self._text(author.find("./ForeName")),
                        self._text(author.find("./LastName")),
                    )
                    if value
                )
                if collective or personal:
                    authors.append(collective or personal)

            records.append(
                {
                    "pmid": pmid,
                    "title": title,
                    "abstract": "\n".join(abstract_parts),
                    "journal": journal,
                    "year": year,
                    "doi": doi,
                    "authors": authors,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
                }
            )
        return records

    def _run(
        self,
        query: str,
        max_results: int = 8,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        search_query = query.strip()
        if year_from or year_to:
            search_query = f"({search_query}) AND ({year_from or 1800}:{year_to or 3000}[pdat])"

        search_response = requests.get(
            f"{self.base_url}/esearch.fcgi",
            params={**self._common_params(), "db": "pubmed", "term": search_query,
                    "retmode": "json", "retmax": max_results, "sort": "relevance"},
            timeout=self.timeout,
        )
        search_response.raise_for_status()
        pmids = search_response.json().get("esearchresult", {}).get("idlist", [])
        if not pmids:
            artifact = {
                "query": search_query,
                "count": 0,
                "evidence": [],
                "search_status": "empty",
                "retry_recommended": True,
            }
            return (
                "No PubMed articles were found for this query. If this was the first "
                "PubMed call in the current turn, retry exactly once with a shorter "
                "query containing only the core disease, intervention, and outcome, "
                "and omit year_from/year_to. If this was already the retry, do not call "
                "PubMed again and do not output any PMID.",
                artifact,
            )

        fetch_response = requests.get(
            f"{self.base_url}/efetch.fcgi",
            params={**self._common_params(), "db": "pubmed", "id": ",".join(pmids),
                    "retmode": "xml"},
            timeout=self.timeout,
        )
        fetch_response.raise_for_status()
        articles = self._parse_articles(fetch_response.text)
        if not articles:
            artifact = {
                "query": search_query,
                "count": 0,
                "evidence": [],
                "search_status": "empty",
                "retry_recommended": True,
            }
            return (
                "PubMed returned identifiers but no readable article records. If this "
                "was the first PubMed call in the current turn, retry exactly once "
                "with a shorter query containing only the core disease, intervention, "
                "and outcome, and omit year_from/year_to. If this was already the "
                "retry, do not call PubMed again and do not output any PMID.",
                artifact,
            )

        evidence = EvidenceAggregator.aggregate({
            "source_type": "pubmed",
            "source_id": f"PMID:{article['pmid']}",
            "title": article["title"],
            "content": (article["abstract"] or "Abstract unavailable.")[:4000],
            "date": str(article["year"] or ""),
            "url": article["url"],
            "metadata": {
                "journal": article["journal"],
                "doi": article["doi"],
                "authors": article["authors"],
            },
        } for article in articles)
        artifact = {
            "query": search_query,
            "count": len(evidence),
            "evidence": evidence,
            "search_status": "success",
            "retry_recommended": False,
        }
        return EvidenceAggregator.format_for_model(evidence), artifact
