"""Fetch and normalize PubMed records with NCBI E-utilities.

Example:
    python pubmed_fetcher.py --query "(lung cancer) AND (immunotherapy)" \
        --min-year 2019 --max-year 2025 --max-results 100 \
        --out data/pubmed_lung_cancer
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


@dataclass
class PubMedRecord:
    pmid: str
    title: str
    abstract: str
    authors: list[str]
    journal: str
    journal_abbreviation: str
    publication_year: str
    publication_date: str
    doi: str
    pubmed_url: str
    article_type: list[str]
    keywords: list[str]
    mesh_terms: list[str]


def _text(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def _first_text(parent: ET.Element, path: str) -> str:
    return _text(parent.find(path))


def _request(endpoint: str, params: dict[str, str], email: str | None, api_key: str | None) -> bytes:
    query = dict(params)
    if email:
        query["email"] = email
    if api_key:
        query["api_key"] = api_key
    request = Request(f"{EUTILS}/{endpoint}?{urlencode(query)}", headers={"User-Agent": "MedLLM-PubMedFetcher/1.0"})
    with urlopen(request, timeout=60) as response:
        return response.read()


def search(query: str, retmax: int, min_year: int | None, max_year: int | None,
           email: str | None, api_key: str | None) -> list[str]:
    if min_year or max_year:
        start = f"{min_year or 1800}/01/01"
        end = f"{max_year or 2100}/12/31"
        query = f"({query}) AND ({start}[PDAT] : {end}[PDAT])"
    data = _request("esearch.fcgi", {"db": "pubmed", "term": query, "retmode": "json", "retmax": str(retmax)}, email, api_key)
    payload = json.loads(data)
    return payload.get("esearchresult", {}).get("idlist", [])


def _parse_article(article: ET.Element) -> PubMedRecord:
    pmid = _first_text(article, "MedlineCitation/PMID")
    citation = article.find("MedlineCitation")
    article_node = citation.find("Article") if citation is not None else None
    article_node = article_node or ET.Element("Article")
    title = _first_text(article_node, "ArticleTitle")
    abstract = " ".join(_text(x) for x in article_node.findall("Abstract/AbstractText"))
    authors = []
    for author in article_node.findall("AuthorList/Author"):
        name = " ".join(filter(None, [_first_text(author, "ForeName"), _first_text(author, "LastName")]))
        authors.append(name or _first_text(author, "CollectiveName"))
    journal = _first_text(article_node, "Journal/Title")
    journal_abbreviation = _first_text(article_node, "Journal/ISOAbbreviation")
    date = article_node.find("Journal/JournalIssue/PubDate")
    publication_date = _text(date)
    publication_year = next((part for part in publication_date.split() if len(part) == 4 and part.isdigit()), "")
    doi = ""
    for aid in article.findall("PubmedData/ArticleIdList/ArticleId"):
        if aid.attrib.get("IdType") == "doi":
            doi = _text(aid)
            break
    types = [_text(x) for x in article_node.findall("PublicationTypeList/PublicationType")]
    keywords = [_text(x) for x in article.findall("MedlineCitation/KeywordList/Keyword")]
    mesh = [_text(x) for x in article.findall("MedlineCitation/MeshHeadingList/MeshHeading/DescriptorName")]
    return PubMedRecord(pmid, title, abstract, authors, journal, journal_abbreviation, publication_year,
                        publication_date, doi, f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", types, keywords, mesh)


def fetch(ids: Iterable[str], email: str | None, api_key: str | None, batch_size: int = 200) -> list[PubMedRecord]:
    ids = list(ids)
    records: list[PubMedRecord] = []
    for offset in range(0, len(ids), batch_size):
        batch = ids[offset:offset + batch_size]
        xml = _request("efetch.fcgi", {"db": "pubmed", "id": ",".join(batch), "retmode": "xml"}, email, api_key)
        root = ET.fromstring(xml)
        records.extend(_parse_article(article) for article in root.findall("PubmedArticle"))
        if offset + batch_size < len(ids):
            time.sleep(0.11 if api_key else 0.34)
    return records


def save(records: list[PubMedRecord], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(record) for record in records]
    output.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = list(payload[0]) if payload else list(PubMedRecord.__dataclass_fields__)
    with output.with_suffix(".csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in payload:
            writer.writerow({key: "; ".join(value) if isinstance(value, list) else value for key, value in row.items()})


def main() -> None:
    parser = argparse.ArgumentParser(description="Download structured PubMed metadata and abstracts.")
    parser.add_argument("--query", required=True, help="PubMed query syntax, e.g. 'lung cancer AND immunotherapy'")
    parser.add_argument("--min-year", type=int)
    parser.add_argument("--max-year", type=int)
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--out", type=Path, default=Path("data/pubmed_records"))
    parser.add_argument("--email", default=os.getenv("NCBI_EMAIL"))
    parser.add_argument("--api-key", default=os.getenv("NCBI_API_KEY"))
    args = parser.parse_args()
    ids = search(args.query, args.max_results, args.min_year, args.max_year, args.email, args.api_key)
    records = fetch(ids, args.email, args.api_key)
    save(records, args.out)
    print(f"Fetched {len(records)} records. Wrote {args.out.with_suffix('.json')} and {args.out.with_suffix('.csv')}")


if __name__ == "__main__":
    main()
