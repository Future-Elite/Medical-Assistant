"""Live real-guideline full-text connector (Europe PMC).

The connector is deliberately online-only: every retrieval queries Europe PMC
and fetches the JATS full text at request time.  It does not ship or persist a
guideline corpus, and it never substitutes a synthetic demo block.

Retrieval order:

1. **Clinical practice guidelines** (``PUB_TYPE:"practice guideline"``,
   ``guideline``, ``consensus``, ``recommendation``) restricted to neonatal /
   newborn / preterm records.  Open-access records are fetched as section
   chunks (``content_mode="full_text"``, ``evidence_type`` =
   ``clinical_practice_guideline``).
2. If no open-access guideline full text is found for the query, the connector
   falls back to **peer-reviewed full text** (systematic review / meta-analysis
   / review) on the same topic.  These chunks are explicitly labelled
   ``evidence_type="systematic_review"`` or ``"narrative_review"`` and carry a
   warning that they are not guideline recommendations.

Licensing rules:

* ``isOpenAccess=Y`` + a PMCID -> fetch full text and emit section chunks with
  ``content_mode="full_text"`` and ``rights_basis="open_access"``.
* ``isOpenAccess=N`` -> return bibliographic metadata + abstract + link only,
  with ``full_text_allowed=False`` and an explicit warning.  The connector never
  scrapes or caches the non-open-access full text.
* No disk cache; no redistribution.  The caller is responsible for checking the
  parsed ``license`` before reusing the text outside the retrieval response.
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from .config import get_config
from .connectors import KnowledgeConnector
from .models import QueryPlan, SearchResult, SourceDocument

EUROPEPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUROPEPMC_FULLTEXT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
USER_AGENT = "chinallm_rag/1.0"
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")
PMID_PATTERN = re.compile(r"(?:PMID|PubMed\s*ID)\s*[:：]?\s*(\d{6,9})", re.IGNORECASE)


class GuidelineFullTextError(RuntimeError):
    def __init__(self, message: str, *, stage: str, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.stage = stage
        self.status_code = status_code
        self.retryable = retryable


@dataclass
class LiveGuidelineFullTextConnector(KnowledgeConnector):
    source_id: str = "guideline-fulltext-live"
    kinds: tuple[str, ...] = ("guideline",)
    #: Injectable for tests: (url, params, timeout) -> bytes.
    transport: Callable[[str, Mapping[str, str], float], bytes] | None = None
    timeout_seconds: float | None = None
    max_articles: int = 8
    max_chunks_per_article: int = 24
    chunk_chars: int = 1200

    def __post_init__(self) -> None:
        config = get_config()
        if self.timeout_seconds is None:
            self.timeout_seconds = float(config.request_timeout_seconds)
        if self.timeout_seconds <= 0:
            raise ValueError("guideline full-text timeout must be positive")
        if self.max_articles <= 0 or self.max_chunks_per_article <= 0 or self.chunk_chars <= 0:
            raise ValueError("guideline full-text limits must be positive")
        self.last_query: str | None = None

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        allowed = set(plan.filters.get("source_kinds") or ())
        if allowed and "guideline" not in allowed:
            return SearchResult(self.source_id, ())
        query = self._build_query(plan)
        self.last_query = query
        warnings: list[str] = []
        payload = self._search(query, limit)
        results = payload.get("resultList", {}).get("result", [])
        if not isinstance(results, list) or not results:
            results = []
        terms = _query_terms(plan)
        topic = _topic_phrase(plan)
        results = _guideline_first(results, terms, topic)
        requested_version = str(plan.filters.get("guideline_version") or "").strip() or None

        documents: list[SourceDocument] = []
        seen_articles: set[str] = set()
        # Iterate over more candidate articles than the final document budget so
        # one large article cannot consume every slot with its own chunks.
        for result in results[: max(self.max_articles, limit)]:
            if not isinstance(result, Mapping):
                continue
            metadata = _result_metadata(result)
            if requested_version and metadata.get("version_id") != requested_version:
                continue
            article_key = str(metadata.get("pmcid") or metadata.get("pmid") or metadata.get("title") or "")
            if article_key and article_key in seen_articles:
                continue
            try:
                produced = self._documents_for(result, metadata, terms, warnings)
            except GuidelineFullTextError as exc:
                warnings.append(
                    f"指南《{metadata['title']}》全文获取失败（{exc.stage}）；仅保留书目/摘要。"
                )
                produced = [_metadata_document(metadata, abstract_only=True, source_id=self.source_id)]
            except Exception as exc:  # pragma: no cover - defensive boundary
                warnings.append(
                    f"指南《{metadata['title']}》处理失败（{type(exc).__name__}）；仅保留书目/摘要。"
                )
                produced = [_metadata_document(metadata, abstract_only=True, source_id=self.source_id)]
            if article_key:
                seen_articles.add(article_key)
            documents.extend(produced)
            if len(documents) >= limit * 2:
                break

        guideline_fulltext = sum(
            1 for document in documents
            if document.metadata.get("content_mode") == "full_text"
            and document.metadata.get("evidence_type") == "clinical_practice_guideline"
        )
        # Always append real peer-reviewed full text as a separate, labelled
        # evidence set.  A guideline full text may exist but be topically wrong
        # for this question (e.g. a sepsis guideline retrieved for TTN); ranking
        # still decides, and the caller can see evidence_type on every chunk.
        if guideline_fulltext == 0:
            warnings.append(
                "未找到开放获取的新生儿指南全文；改用同主题同行评议全文（系统综述/综述）作为替代证据，"
                "证据类型已在 metadata.evidence_type 标注，不得当作指南推荐。"
            )
        reviews = self._peer_reviewed_fallback(plan, terms, warnings, max(6, limit // 2))
        known = {document.document_id for document in documents}
        documents.extend(document for document in reviews if document.document_id not in known)
        if not documents:
            return SearchResult(self.source_id, (), tuple(warnings + ["Europe PMC 未返回匹配的指南或综述全文。"]))
        # Full text first: metadata/abstract rows must not crowd out real section
        # chunks when the document limit is reached.
        documents.sort(key=_content_priority)
        return SearchResult(self.source_id, tuple(documents[:limit]), tuple(dict.fromkeys(warnings)))

    def _peer_reviewed_fallback(
        self,
        plan: QueryPlan,
        terms: Sequence[str],
        warnings: list[str],
        limit: int,
    ) -> list[SourceDocument]:
        query = self._build_review_query(plan)
        try:
            payload = self._search(query, limit)
        except GuidelineFullTextError as exc:
            warnings.append(f"同行评议全文回退检索失败（{exc.stage}）。")
            return []
        results = payload.get("resultList", {}).get("result", [])
        if not isinstance(results, list):
            return []
        documents: list[SourceDocument] = []
        for result in _review_first(results)[: self.max_articles]:
            if not isinstance(result, Mapping):
                continue
            metadata = _result_metadata(result)
            try:
                chunks = self._documents_for(result, metadata, terms, [])
            except GuidelineFullTextError:
                continue
            for document in chunks:
                document.metadata["evidence_type"] = _review_evidence_type(result, metadata)
                document.metadata["fallback_for_missing_guideline"] = True
                documents.append(document)
            if len(documents) >= limit:
                break
        if not documents:
            warnings.append("同行评议全文回退也没有返回可用的开放获取正文。")
        return documents

    def _build_review_query(self, plan: QueryPlan) -> str:
        terms = _query_terms(plan)
        groups = [
            '(PUB_TYPE:"systematic review" OR PUB_TYPE:"meta-analysis" OR PUB_TYPE:"review" '
            'OR TITLE:"systematic review" OR TITLE:"meta-analysis" OR TITLE:review)',
            "(neonatal OR newborn OR infant OR preterm)",
        ]
        if terms:
            term_groups: list[str] = []
            for term in terms[:6]:
                term_groups.append(f'TITLE_ABS:"{term}"')
                tokens = _query_tokens(term)
                if 2 <= len(tokens) <= 3:
                    term_groups.append("TITLE_ABS:(" + " AND ".join(tokens) + ")")
            groups.append("(" + " OR ".join(dict.fromkeys(term_groups)) + ")")
        return " AND ".join(groups)

    def _documents_for(
        self,
        result: Mapping[str, Any],
        metadata: dict[str, Any],
        terms: Sequence[str],
        warnings: list[str],
    ) -> list[SourceDocument]:
        is_open_access = str(result.get("isOpenAccess") or "").upper() == "Y"
        pmcid = str(result.get("pmcid") or "").strip()
        if not is_open_access or not pmcid:
            warnings.append(
                f"指南《{metadata['title']}》不是开放获取全文（isOpenAccess=N 或无 PMCID）；"
                "仅返回书目/摘要与链接，未抓取全文。"
            )
            return [_metadata_document(metadata, abstract_only=False, source_id=self.source_id)]

        raw = self._request(EUROPEPMC_FULLTEXT.format(pmcid=pmcid))
        chunks, license_text = _parse_jats(
            raw, metadata, terms, self.max_chunks_per_article, self.chunk_chars, source_id=self.source_id
        )
        if license_text:
            metadata["license"] = license_text
            metadata["license_verified"] = True
        else:
            metadata["license"] = "unknown"
            metadata["license_verified"] = False
            warnings.append(
                f"指南《{metadata['title']}》标记为开放获取，但未解析到 license；"
                "全文仅用于本次检索，不缓存、不再分发。"
            )
        if not chunks:
            warnings.append(f"指南《{metadata['title']}》全文 XML 未解析出正文段落；仅保留书目/摘要。")
            return [_metadata_document(metadata, abstract_only=False, source_id=self.source_id)]
        # Full text available: drop the duplicate abstract/metadata row so it
        # cannot outrank or crowd out the actual section chunks.
        return _limit_chunks_per_article(chunks, self.max_chunks_per_article)

    def _build_query(self, plan: QueryPlan) -> str:
        question = str(plan.original_question or "")
        doi = _extract_doi(question)
        if doi:
            return f'DOI:"{doi}"'
        pmid = _extract_pmid(question)
        if pmid:
            return f"EXT_ID:{pmid}"
        terms = _query_terms(plan)
        groups = [
            '(PUB_TYPE:"practice guideline" OR PUB_TYPE:"guideline" OR TITLE:guideline OR TITLE:consensus OR TITLE:recommendation)',
            "(neonatal OR newborn OR infant OR preterm)",
        ]
        if terms:
            term_groups: list[str] = []
            for term in terms[:6]:
                term_groups.append(f'TITLE_ABS:"{term}"')
                tokens = _query_tokens(term)
                if 2 <= len(tokens) <= 3:
                    term_groups.append("TITLE_ABS:(" + " AND ".join(tokens) + ")")
            groups.append("(" + " OR ".join(dict.fromkeys(term_groups)) + ")")
        return " AND ".join(groups)

    def _topic_relevance(self, result: Mapping[str, Any], terms: Sequence[str]) -> int:
        """Count how many query concepts appear in the title/abstract.

        Europe PMC's boolean query can match a record on a single generic token
        (e.g. "infant"), so a purely guideline-type-first sort lets unrelated
        adult guidelines crowd out the on-topic record.  Matching uses concept
        tokens ("hypoglycemia", "glucose") rather than only whole phrases;
        otherwise a phrase like "blood glucose" matches diabetes guidelines but
        misses a neonatal hypoglycemia guideline.
        """
        haystack = f"{result.get('title') or ''} {result.get('abstractText') or ''}".lower()
        matched: set[str] = set()
        for term in terms:
            phrase = str(term).strip().lower()
            if phrase and phrase in haystack:
                matched.add(phrase)
                continue
            for token in _query_tokens(term):
                if token.lower() in haystack:
                    matched.add(token.lower())
        return len(matched)

    def _search(self, query: str, limit: int) -> dict[str, Any]:
        params = {
            "query": query,
            "format": "json",
            "resultType": "core",
            "pageSize": str(min(25, max(10, int(limit) * 2))),
        }
        url = EUROPEPMC_SEARCH + "?" + urllib.parse.urlencode(params)
        raw = self._request(url)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GuidelineFullTextError("Europe PMC search JSON parse failed", stage="search_parse") from exc
        if not isinstance(payload, dict):
            raise GuidelineFullTextError("Europe PMC search returned a non-object payload", stage="search_parse")
        return payload

    def _request(self, url: str) -> bytes:
        if self.transport is not None:
            try:
                return self.transport(url, {}, float(self.timeout_seconds))
            except GuidelineFullTextError:
                raise
            except Exception as exc:
                raise GuidelineFullTextError("guideline transport failed", stage="transport", retryable=True) from exc
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            # The environment may export a stale proxy; Europe PMC is public HTTPS.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=float(self.timeout_seconds)) as response:
                return response.read(12_000_000)
        except urllib.error.HTTPError as exc:
            raise GuidelineFullTextError(
                "Europe PMC HTTP error", stage="http", status_code=exc.code,
                retryable=exc.code == 429 or exc.code >= 500,
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise GuidelineFullTextError("Europe PMC network or timeout error", stage="network", retryable=True) from exc


def _extract_doi(text: str) -> str | None:
    match = DOI_PATTERN.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,;)]}")


def _extract_pmid(text: str) -> str | None:
    match = PMID_PATTERN.search(text)
    return match.group(1) if match else None


def _query_tokens(term: str) -> list[str]:
    stop = {"and", "or", "the", "of", "for", "with", "in", "to", "a", "an", "on", "by", "from", "newborn"}
    return [
        token
        for token in re.split(r"[^A-Za-z0-9]+", str(term))
        if len(token) >= 3 and token.lower() not in stop
    ]


def _limit_chunks_per_article(documents: Sequence[SourceDocument], per_article: int) -> list[SourceDocument]:
    """Keep the most relevant chunks per article, not the first N of one article.

    ``_parse_jats`` already ranks chunks by query-term overlap, so slicing the
    top ``per_article`` keeps the on-topic sections and leaves room for other
    articles in the final candidate pool.
    """
    if per_article <= 0:
        return list(documents)
    return list(documents[:per_article])


def _content_priority(document: SourceDocument) -> tuple[int, str]:
    mode = document.metadata.get("content_mode")
    evidence = document.metadata.get("evidence_type")
    if mode == "full_text" and evidence == "clinical_practice_guideline":
        return (0, document.document_id)
    if mode == "full_text" and evidence == "systematic_review":
        return (1, document.document_id)
    if mode == "full_text":
        return (2, document.document_id)
    if evidence == "clinical_practice_guideline":
        return (3, document.document_id)
    if mode == "abstract":
        return (4, document.document_id)
    return (5, document.document_id)


def _review_first(results: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    def priority(item: Mapping[str, Any]) -> int:
        pub_types = " ".join(_pub_types(item)).lower()
        title = str(item.get("title") or "").lower()
        if "systematic review" in pub_types or "meta-analysis" in pub_types:
            return 0
        if "systematic review" in title or "meta-analysis" in title:
            return 1
        if "review" in pub_types:
            return 2
        if "review" in title:
            return 3
        return 4

    indexed = list(enumerate(results))
    indexed.sort(key=lambda pair: (priority(pair[1]), pair[0]))
    return [item for _, item in indexed]


def _review_evidence_type(result: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    pub_types = " ".join(_pub_types(result)).lower()
    title = str(metadata.get("title") or "").lower()
    if "systematic review" in pub_types or "meta-analysis" in pub_types:
        return "systematic_review"
    if "systematic review" in title or "meta-analysis" in title:
        return "systematic_review"
    return "narrative_review"


def _topic_phrase(plan: QueryPlan) -> str:
    """Pick the most specific clinical phrase from the reviewed terms.

    The longest English term is the best proxy for the disease topic
    ("neonatal hypoglycemia" over "blood glucose").
    """
    terms = [str(term).strip() for term in (*plan.english_terms, *plan.mesh_terms) if str(term).strip()]
    if not terms:
        return ""
    return max(terms, key=lambda term: (len(term.split()), len(term)))


def _topic_matches(item: Mapping[str, Any], topic: str) -> bool:
    if not topic:
        return False
    haystack = f"{item.get('title') or ''} {item.get('abstractText') or ''}".lower()
    return topic.lower() in haystack


def _guideline_first(
    results: Sequence[Mapping[str, Any]],
    terms: Sequence[str] = (),
    topic: str = "",
) -> list[Mapping[str, Any]]:
    def matched_concepts(item: Mapping[str, Any]) -> int:
        haystack = f"{item.get('title') or ''} {item.get('abstractText') or ''}".lower()
        matched: set[str] = set()
        for term in terms:
            phrase = str(term).strip().lower()
            if phrase and phrase in haystack:
                matched.add(phrase)
                continue
            for token in _query_tokens(term):
                if token.lower() in haystack:
                    matched.add(token.lower())
        return len(matched)

    def priority(item: Mapping[str, Any]) -> tuple[int, int, int]:
        pub_types = " ".join(_pub_types(item)).lower()
        title = str(item.get("title") or "").lower()
        is_practice = "practice guideline" in pub_types
        is_guideline = "guideline" in pub_types or "consensus statement" in pub_types
        has_guideline_title = any(
            word in title for word in ("guideline", "consensus", "recommendation")
        )
        if is_practice and has_guideline_title:
            rank = 0
        elif is_guideline and has_guideline_title:
            rank = 1
        elif is_practice or is_guideline:
            rank = 2
        elif has_guideline_title:
            rank = 3
        else:
            rank = 4
        # Topic phrase first, then concept overlap: a neonatal hypoglycemia
        # guideline must outrank an adult diabetes guideline that merely shares
        # the words "blood glucose".
        return (rank, 0 if _topic_matches(item, topic) else 1, -matched_concepts(item))

    indexed = list(enumerate(results))
    indexed.sort(key=lambda pair: (priority(pair[1]), pair[0]))
    return [item for _, item in indexed]


def _pub_types(result: Mapping[str, Any]) -> list[str]:
    listed = result.get("pubTypeList")
    if isinstance(listed, Mapping):
        values = listed.get("pubType")
        if isinstance(values, list):
            return [str(value) for value in values if str(value).strip()]
        if values:
            return [str(values)]
    raw = result.get("pubType")
    if isinstance(raw, list):
        return [str(value) for value in raw if str(value).strip()]
    return [str(raw)] if raw else []


def _query_terms(plan: QueryPlan) -> list[str]:
    terms: list[str] = []
    for term in (*plan.english_terms, *plan.question_english_terms, *plan.mesh_terms):
        value = str(term).strip()
        if not value or len(value) > 120 or value in terms:
            continue
        if value.isdigit() or len(value) < 2:
            continue
        terms.append(value)
    if not terms:
        for concept in plan.concepts:
            value = str(concept).strip()
            if value.isascii() and 2 <= len(value) <= 80 and value not in terms:
                terms.append(value)
    return terms


def _result_metadata(result: Mapping[str, Any]) -> dict[str, Any]:
    title = str(result.get("title") or "").strip().rstrip(".")
    published = str(result.get("firstPublicationDate") or "").strip()
    if not published:
        year = str(result.get("pubYear") or "").strip()
        published = f"{year}-01-01" if year.isdigit() else ""
    return {
        "pmid": str(result.get("pmid") or "").strip() or None,
        "pmcid": str(result.get("pmcid") or "").strip() or None,
        "doi": str(result.get("doi") or "").strip() or None,
        "title": title or "未命名指南",
        "journal": str(result.get("journalTitle") or "").strip() or None,
        "published_at": published or None,
        "abstract": str(result.get("abstractText") or "").strip(),
        "pub_type": "; ".join(_pub_types(result)) or None,
        "is_open_access": str(result.get("isOpenAccess") or "").upper() == "Y",
        "url": _article_url(result),
        "version_id": _version_id(title, result.get("pubYear")),
    }


def _article_url(result: Mapping[str, Any]) -> str | None:
    pmcid = str(result.get("pmcid") or "").strip()
    if pmcid:
        return f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
    pmid = str(result.get("pmid") or "").strip()
    if pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    doi = str(result.get("doi") or "").strip()
    return f"https://doi.org/{doi}" if doi else None


def _version_id(title: str, pub_year: Any) -> str | None:
    years = re.findall(r"\b(?:19|20)\d{2}\b", title)
    if years:
        return years[-1]
    value = str(pub_year or "").strip()
    return value if value.isdigit() else None


def _metadata_document(
    metadata: Mapping[str, Any],
    *,
    abstract_only: bool,
    source_id: str = "guideline-fulltext-live",
) -> SourceDocument:
    abstract = str(metadata.get("abstract") or "").strip()
    content_mode = "abstract" if abstract else "metadata_only"
    text = abstract or (
        "该指南不是开放获取全文；仅提供书目元数据与链接，未抓取或缓存全文。"
        if not metadata.get("is_open_access")
        else "该开放获取指南未解析出摘要；全文分块见同源文档。"
    )
    document_id = f"epmc-{metadata.get('pmcid') or metadata.get('pmid') or 'unknown'}-metadata"
    return SourceDocument(
        source_id=source_id,
        document_id=document_id,
        kind="guideline",
        title=str(metadata.get("title") or "未命名指南"),
        text=text,
        url=metadata.get("url"),
        published_at=metadata.get("published_at"),
        locator="Abstract" if abstract else "Metadata",
        pmid=metadata.get("pmid"),
        doi=metadata.get("doi"),
        authority=metadata.get("journal"),
        version_id=metadata.get("version_id"),
        population=None,
        status=None,
        superseded_by=None,
        index_snapshot=None,
        metadata={
            "synthetic": False,
            "content_mode": content_mode,
            "evidence_type": "clinical_practice_guideline",
            "full_text_allowed": bool(metadata.get("is_open_access")),
            "rights_basis": "open_access" if metadata.get("is_open_access") else "not_open_access",
            "license": metadata.get("license") or "unknown",
            "license_verified": bool(metadata.get("license_verified")),
            "live_source": True,
            "source": "Europe PMC",
            "is_open_access": bool(metadata.get("is_open_access")),
            "journal": metadata.get("journal"),
            "pub_type": metadata.get("pub_type"),
            "pmcid": metadata.get("pmcid"),
            "abstract_only": abstract_only,
        },
    )


# 外部 JATS 可能携带内部 DTD 子集；解析前拒绝实体声明并剥离 DOCTYPE，
# 阻断实体扩展（ElementTree 本身不加载外部 DTD）。
_JATS_ENTITY_DECL = re.compile(r"<!ENTITY\b", re.IGNORECASE)
_JATS_DOCTYPE = re.compile(r"<!DOCTYPE\b(?:[^>\[]|\[.*?\])*?>", re.DOTALL | re.IGNORECASE)


def _parse_jats(
    raw: bytes,
    metadata: dict[str, Any],
    terms: Sequence[str],
    max_chunks: int,
    chunk_chars: int,
    *,
    source_id: str = "guideline-fulltext-live",
) -> tuple[list[SourceDocument], str | None]:
    try:
        text = raw.decode("utf-8", errors="replace")
        if _JATS_ENTITY_DECL.search(text):
            raise GuidelineFullTextError("JATS full-text XML 包含不允许的实体声明", stage="fulltext_parse")
        text = _JATS_DOCTYPE.sub("", text, count=1)
        root = ET.fromstring(text)
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise GuidelineFullTextError("JATS full-text XML parse failed", stage="fulltext_parse") from exc

    article_title = _first_text(root, "article-title") or str(metadata.get("title") or "未命名指南")
    abstract = _abstract_text(root) or str(metadata.get("abstract") or "")
    if abstract:
        metadata["abstract"] = abstract
    license_text = _license_text(root)
    sections = list(_walk_sections(root))
    if not sections:
        return [], license_text

    chunk_rows: list[tuple[str, str, int]] = []
    for section_index, (section_title, section_text) in enumerate(sections):
        for chunk_index, chunk in enumerate(_chunk_text(section_text, chunk_chars), start=1):
            chunk_rows.append((section_title, chunk, section_index * 1000 + chunk_index))
    if not chunk_rows:
        return [], license_text

    ranked = _rank_chunks(chunk_rows, terms)[:max_chunks]
    documents: list[SourceDocument] = []
    for rank, (section_title, chunk, order) in enumerate(ranked, start=1):
        documents.append(SourceDocument(
            source_id=source_id,
            document_id=f"epmc-{metadata.get('pmcid')}-s{order:05d}",
            kind="guideline",
            title=f"{article_title} — {section_title} (part {rank})",
            text=f"{section_title}\n{chunk}",
            url=metadata.get("url"),
            published_at=metadata.get("published_at"),
            locator=f"{section_title} / chunk {rank}",
            pmid=None,
            doi=None,
            authority=metadata.get("journal"),
            version_id=metadata.get("version_id"),
            population=None,
            status=None,
            superseded_by=None,
            index_snapshot=None,
            metadata={
                "synthetic": False,
                "content_mode": "full_text",
                "evidence_type": "clinical_practice_guideline",
                "full_text_allowed": True,
                "rights_basis": "open_access",
                "license": license_text or "unknown",
                "license_verified": bool(license_text),
                "live_source": True,
                "source": "Europe PMC",
                "is_open_access": True,
                "journal": metadata.get("journal"),
                "pmcid": metadata.get("pmcid"),
                "pmid": metadata.get("pmid"),
                "doi": metadata.get("doi"),
                "article_title": article_title,
                "section": section_title,
                "chunk_index": rank,
            },
        ))
    return documents, license_text


def _rank_chunks(rows: Iterable[tuple[str, str, int]], terms: Sequence[str]) -> list[tuple[str, str, int]]:
    lowered_terms = [term.lower() for term in terms if term.strip()]
    scored: list[tuple[int, int, str, str]] = []
    for section_title, chunk, order in rows:
        haystack = f"{section_title}\n{chunk}".lower()
        score = sum(1 for term in lowered_terms if term in haystack)
        scored.append((-score, order, section_title, chunk))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [(title, chunk, order) for _, order, title, chunk in scored]


def _walk_sections(root: ET.Element) -> Iterable[tuple[str, str]]:
    body = _find_first(root, "body")
    if body is None:
        return
    yield from _walk_section(body, "")


def _walk_section(node: ET.Element, inherited_title: str) -> Iterable[tuple[str, str]]:
    """Yield each section's direct text exactly once, then recurse into child sections."""
    direct_text: list[str] = []
    for child in node:
        tag = _local(child.tag)
        if tag in {"p", "list-item", "disp-quote"} and inherited_title:
            text = _all_text(child)
            if text:
                direct_text.append(text)
        elif tag in {"table-wrap", "fig"} and inherited_title:
            caption = _first_text(child, "caption") or _first_text(child, "label")
            if caption:
                direct_text.append(caption)
    if direct_text:
        yield inherited_title or "正文", "\n".join(direct_text)
    for child in node:
        if _local(child.tag) == "sec":
            title = _first_text(child, "title") or inherited_title or "正文"
            yield from _walk_section(child, title)


def _abstract_text(root: ET.Element) -> str:
    abstract = _find_first(root, "abstract")
    if abstract is None:
        return ""
    parts = []
    for node in abstract.iter():
        if _local(node.tag) in {"p", "title"}:
            text = _all_text(node)
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _license_text(root: ET.Element) -> str | None:
    for node in root.iter():
        if _local(node.tag) == "license":
            text = _all_text(node)
            if text:
                return text[:500]
    return None


def _chunk_text(text: str, max_chars: int) -> list[str]:
    normalized = " ".join(str(text).split())
    if not normalized:
        return []
    sentences = re.split(r"(?<=[。！？.!?])\s+", normalized)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for index in range(0, len(sentence), max_chars):
                chunks.append(sentence[index:index + max_chars])
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_first(node: ET.Element, name: str) -> ET.Element | None:
    for item in node.iter():
        if _local(item.tag) == name:
            return item
    return None


def _first_text(node: ET.Element, name: str) -> str:
    found = _find_first(node, name)
    return _all_text(found) if found is not None else ""


def _all_text(node: ET.Element) -> str:
    return " ".join("".join(node.itertext()).split())
