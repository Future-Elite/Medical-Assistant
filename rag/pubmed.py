"""Read-only PubMed E-utilities connector for RAG.

No patient chart is accepted here.  The connector receives only the controlled
``QueryPlan`` created by the retrieval pipeline and retrieves live PubMed metadata/abstracts from
NCBI E-utilities.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .config import get_config
from .models import QueryPlan, SearchResult, SourceDocument


EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class PubMedError(RuntimeError):
    """A live PubMed request failed; callers must show the failure explicitly."""

    def __init__(self, message: str, *, stage: str = "request", status_code: int | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.status_code = status_code


@dataclass
class PubMedConnector:
    """Live connector backed by NCBI's public E-utilities API."""

    source_id: str = "pubmed-live"
    kinds: tuple[str, ...] = ("pubmed",)
    email: str | None = None
    tool_name: str = "chinallm_rag"
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        config = get_config()
        if self.email is None:
            self.email = config.ncbi_email or None
        if self.timeout_seconds == 20.0:
            self.timeout_seconds = config.request_timeout_seconds
    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        query = _pubmed_query(plan)
        identifiers = self._search_ids(query, limit)
        warnings: list[str] = []
        if not self.email:
            warnings.append("PubMed 请求未在 config.py 设置 ncbi_email；建议配置有效邮箱以符合 NCBI 使用规范。")
        if not identifiers:
            warnings.append("实时 PubMed 未返回匹配记录。可补充英文受控术语后重试。")
            return SearchResult(source_id=self.source_id, documents=(), warnings=tuple(warnings))
        documents = self._fetch_articles(identifiers, query)
        if len(documents) < len(identifiers):
            warnings.append(f"PubMed 返回 {len(identifiers)} 个 PMID，其中 {len(documents)} 条可解析为含摘要的证据记录。")
        return SearchResult(source_id=self.source_id, documents=tuple(documents), warnings=tuple(warnings))

    def _search_ids(self, query: str, limit: int) -> list[str]:
        payload = self._get("esearch.fcgi", {
            "db": "pubmed", "retmode": "json", "retmax": str(limit), "sort": "relevance", "term": query,
        })
        import json

        try:
            data = json.loads(payload.decode("utf-8"))
            return [str(value) for value in data["esearchresult"].get("idlist", [])]
        except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
            raise PubMedError("PubMed 搜索响应无法解析") from exc

    def _fetch_articles(self, identifiers: list[str], query: str) -> list[SourceDocument]:
        payload = self._get("efetch.fcgi", {"db": "pubmed", "retmode": "xml", "id": ",".join(identifiers)})
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise PubMedError("PubMed 文献响应无法解析") from exc
        documents: list[SourceDocument] = []
        for article in root.findall(".//PubmedArticle"):
            document = _article_to_document(article, self.source_id, query)
            if document is not None:
                documents.append(document)
        return documents

    def _get(self, endpoint: str, params: dict[str, str]) -> bytes:
        params["tool"] = self.tool_name
        if self.email:
            params["email"] = self.email
        api_key = get_config().ncbi_api_key
        if api_key:
            params["api_key"] = api_key
        url = f"{EUTILS}/{endpoint}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"User-Agent": f"{self.tool_name}/1.0"})
        try:
            # The execution environment may export a dead HTTP(S)_PROXY. NCBI
            # E-utilities is public HTTPS and is intentionally contacted directly.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=self.timeout_seconds) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise PubMedError(
                f"PubMed 实时请求失败：HTTP {exc.code}", stage=endpoint, status_code=exc.code,
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            detail = str(getattr(exc, "reason", exc)).strip()
            raise PubMedError(
                f"PubMed 实时请求失败：{type(exc).__name__}（{detail[:240]}）", stage=endpoint,
            ) from exc


def _pubmed_query(plan: QueryPlan) -> str:
    variants = [value.replace('"', " ").strip() for value in plan.variants if value.strip()]
    if not variants:
        raise PubMedError("查询计划没有可用于 PubMed 的关键词")
    return " OR ".join(f"({value})" for value in variants[:3])


def _article_to_document(article: ET.Element, source_id: str, query: str) -> SourceDocument | None:
    pmid = _text(article.find("./MedlineCitation/PMID"))
    title = _joined(article.find("./MedlineCitation/Article/ArticleTitle"))
    abstract_parts = []
    for node in article.findall("./MedlineCitation/Article/Abstract/AbstractText"):
        text = _joined(node)
        if text:
            label = node.attrib.get("Label")
            abstract_parts.append(f"{label}: {text}" if label else text)
    if not pmid or not title or not abstract_parts:
        return None
    journal = _joined(article.find("./MedlineCitation/Article/Journal/Title"))
    doi = None
    for identifier in article.findall("./PubmedData/ArticleIdList/ArticleId"):
        if identifier.attrib.get("IdType") == "doi":
            doi = _text(identifier)
            break
    published_at = _publication_date(article)
    return SourceDocument(
        source_id=source_id,
        document_id=f"pmid-{pmid}",
        kind="pubmed",
        title=title,
        text=" ".join(abstract_parts),
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        published_at=published_at,
        locator="Abstract",
        pmid=pmid,
        doi=doi,
        metadata={"journal": journal, "live_source": True, "retrieved_query": query},
    )


def _publication_date(article: ET.Element) -> str | None:
    date_node = article.find("./MedlineCitation/Article/Journal/JournalIssue/PubDate")
    if date_node is None:
        return None
    year = _text(date_node.find("Year"))
    if year and year.isdigit() and len(year) == 4:
        return f"{year}-01-01"
    medline_date = _text(date_node.find("MedlineDate"))
    if medline_date and medline_date[:4].isdigit():
        return f"{medline_date[:4]}-01-01"
    return None


def _text(node: ET.Element | None) -> str | None:
    return _joined(node) or None


def _joined(node: ET.Element | None) -> str:
    return "".join(node.itertext()).strip() if node is not None else ""





