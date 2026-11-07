"""Live PubMed E-utilities connector for the RAG package.

Network access is explicit and injectable for tests. The connector only sends
reviewed query terms; it never receives or serializes a PatientSnapshot.
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping

from .config import get_config
from .connectors import KnowledgeConnector
from .models import QueryPlan, SearchResult, SourceDocument
from .retrieval.query import (
    expanded_mapping_entries,
    is_generic_population,
    is_unit_term,
    mapping_active,
    population_clause,
    primary_mapping_entry,
    primary_topic,
)


PUBMED_SEARCH_CACHE: ContextVar[dict | None] = ContextVar("pubmed_search_cache", default=None)


class PubMedError(RuntimeError):
    def __init__(self, message: str, *, stage: str, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.stage = stage
        self.status_code = status_code
        self.retryable = retryable


@dataclass
class PubMedConnector(KnowledgeConnector):
    source_id: str = "pubmed-live"
    kinds: tuple[str, ...] = ("pubmed",)
    transport: Callable[[str, Mapping[str, str], float], bytes] | None = None
    timeout_seconds: float | None = None
    # 默认放大召回（ESearch retmax）；请求仍受调用方 limit 与 config.pubmed_max_results 约束。
    max_results: int = 200
    tool_name: str = "chinallm_rag"

    def __post_init__(self) -> None:
        config = get_config()
        if self.timeout_seconds is None:
            self.timeout_seconds = float(config.request_timeout_seconds)
        if self.timeout_seconds <= 0:
            raise ValueError("PubMed timeout must be positive")
        self.email = str(config.ncbi_email or "")
        self.api_key = str(config.ncbi_api_key or "")
        # 兼容旧 Agent：connector_trace 读取这两个字段。
        self.last_date_filter: dict[str, str] = {}
        self.last_cache_hit = False

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        """带调用方轮次缓存的入口；缓存由 ``PUBMED_SEARCH_CACHE`` ContextVar 注入。"""
        limit = max(1, min(int(limit), self.max_results))
        query = _safe_term(plan)
        cache = PUBMED_SEARCH_CACHE.get()
        key = (id(self), query, limit)
        self.last_cache_hit = cache is not None and key in cache
        if self.last_cache_hit:
            return cache[key]
        result = self._search_uncached(plan, limit=limit)
        if cache is not None:
            cache[key] = result
        return result

    def _search_uncached(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        limit = max(1, min(int(limit), self.max_results))
        warnings: list[str] = []
        if not self.email:
            warnings.append("PubMed 请求未在 config.py 设置 ncbi_email；建议配置有效邮箱以符合 NCBI 使用规范。")
        params = self._base_params()
        params.update({"db": "pubmed", "retmode": "json", "retmax": str(limit), "sort": "relevance"})
        query = _safe_term(plan)
        if not query:
            self.last_query = ""
            return SearchResult(self.source_id, (), (*warnings, "没有可用的英文检索词；未向 PubMed 发送查询。"))
        self.last_query = query
        esearch_error: PubMedError | None = None
        try:
            ids = self._esearch_ids(params, query)
        except PubMedError as exc:
            if self.transport is not None:
                raise
            esearch_error = exc
            warnings.append(f"PubMed ESearch 失败（{exc.stage}）：{exc}；尝试 Europe PMC 提供 PMID 列表。")
            ids = []
        fallback = None
        if not ids:
            # 主词过窄时退回同义词组，但单位词永远不会进入查询。
            fallback = _mapped_fallback_term(plan) if mapping_active(plan) else _fallback_term(plan)
            if fallback and fallback != query:
                self.last_query = fallback
                try:
                    ids = self._esearch_ids(params, fallback)
                except PubMedError as exc:
                    if self.transport is not None:
                        raise
                    esearch_error = esearch_error or exc
                    warnings.append(f"PubMed ESearch 同义词回退也失败（{exc.stage}）：{exc}。")
                    ids = []
        if not ids:
            # NCBI ESearch 后端曾返回 HTTP 200 + esearchresult.ERROR；此时用
            # Europe PMC 的 SRC:MED 记录补 PMID 列表，再由 PubMed EFetch 取摘要。
            for candidate in (query, fallback):
                if not candidate:
                    continue
                epmc_ids, epmc_warning = self._europepmc_ids(candidate, limit)
                if epmc_ids:
                    ids = epmc_ids
                    warnings.append(
                        "PubMed ESearch 后端未返回 PMID；本次 PMID 列表由 Europe PMC（SRC:MED）提供，"
                        "摘要仍由 PubMed EFetch 读取。"
                    )
                    break
                if epmc_warning:
                    warnings.append(epmc_warning)
        if not ids:
            # NCBI E-utilities 整体不可用时，Europe PMC 自己就有 SRC:MED 的
            # 书目与摘要；不必再依赖 EFetch。记录来源在 metadata 中标注。
            documents, epmc_warning = self._europepmc_documents(query, limit)
            if not documents and fallback and fallback != query:
                documents, epmc_warning = self._europepmc_documents(fallback, limit)
            if documents:
                warnings.append(
                    "NCBI E-utilities 未返回 PMID；本次 PubMed 记录改由 Europe PMC（SRC:MED）提供，"
                    "标题、摘要与书目字段来自 Europe PMC。"
                )
                return SearchResult(self.source_id, tuple(documents), tuple(warnings))
            if epmc_warning:
                warnings.append(epmc_warning)
            if esearch_error is not None:
                raise esearch_error
            return SearchResult(self.source_id, (), (*warnings, "PubMed 没有返回 PMID。"))
        fetch_params = self._base_params()
        fetch_params.update({"db": "pubmed", "retmode": "xml", "id": ",".join(ids)})
        try:
            xml = self._request("efetch", fetch_params)
        except PubMedError as exc:
            if self.transport is not None:
                raise
            documents, epmc_warning = self._europepmc_documents(
                " OR ".join(f"EXT_ID:{pmid}" for pmid in ids[:limit]), limit
            )
            if documents:
                warnings.append(
                    f"PubMed EFetch 失败（{exc.stage}）；本次记录改由 Europe PMC（SRC:MED）提供，"
                    "标题、摘要与书目字段来自 Europe PMC。"
                )
                return SearchResult(self.source_id, tuple(documents), tuple(warnings))
            if epmc_warning:
                warnings.append(epmc_warning)
            raise
        documents, skipped = _parse_efetch(xml, self.source_id)
        if skipped:
            warnings.append(f"PubMed 有 {skipped} 条记录缺少 PMID、标题或摘要，未加入证据列表。")
        return SearchResult(self.source_id, tuple(documents), tuple(warnings))

    def _esearch_ids(self, params: Mapping[str, str], query: str) -> list[str]:
        request_params = dict(params)
        request_params["term"] = query
        raw = self._request("esearch", request_params)
        try:
            payload = json.loads(raw.decode("utf-8"))
            result = payload.get("esearchresult", {})
            # NCBI 搜索后端故障时会返回 HTTP 200 + esearchresult.ERROR
            # （例如 "Cannot connect to SOLR"）；显式上报，而不是当作空结果。
            error = str(result.get("ERROR") or "").strip() if isinstance(result, Mapping) else ""
            if error:
                raise PubMedError(
                    f"PubMed ESearch 后端错误：{_short_reason(error, self.api_key)}",
                    stage="esearch_backend", retryable=True,
                )
            return [str(value) for value in result.get("idlist", []) if str(value).strip()]
        except PubMedError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
            raise PubMedError("PubMed ESearch JSON 解析失败", stage="esearch_parse") from exc

    def _europepmc_ids(self, query: str, limit: int) -> tuple[list[str], str]:
        """Resolve PubMed PMIDs through Europe PMC when NCBI ESearch is down."""
        terms = " ".join(re.sub(r"\[[^\]]*\]", "", query).split())
        if not terms:
            return [], "Europe PMC 回退查询为空。"
        params = urllib.parse.urlencode({
            "query": f"({terms}) AND SRC:MED",
            "format": "json",
            "resultType": "lite",
            "pageSize": str(min(max(int(limit), 5), 25)),
        })
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + params
        request = urllib.request.Request(url, headers={"User-Agent": f"{self.tool_name}/1.0"})
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=float(self.timeout_seconds)) as response:
                payload = json.loads(response.read(4_000_000).decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, socket.timeout, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return [], f"Europe PMC 回退检索失败（{type(exc).__name__}）。"
        results = payload.get("resultList", {}).get("result", []) if isinstance(payload, Mapping) else []
        ids: list[str] = []
        for row in results if isinstance(results, list) else []:
            if not isinstance(row, Mapping) or str(row.get("source") or "").upper() != "MED":
                continue
            pmid = str(row.get("pmid") or "").strip()
            if pmid and pmid not in ids:
                ids.append(pmid)
        if not ids:
            return [], "Europe PMC 回退未返回 PubMed 记录。"
        return ids[:limit], ""

    def _europepmc_documents(self, query: str, limit: int) -> tuple[list[SourceDocument], str]:
        """Fetch PubMed-equivalent records directly from Europe PMC (SRC:MED).

        Used when NCBI E-utilities cannot return PMIDs or abstracts at all.
        Only ``source == MED`` rows are kept so the result stays a PubMed
        record set; ``EXT_ID:`` queries let the caller resolve a known PMID
        list after a failed EFetch.
        """
        terms = " ".join(re.sub(r"\[[^\]]*\]", "", query).split())
        if not terms:
            return [], "Europe PMC 回退查询为空。"
        params = urllib.parse.urlencode({
            "query": f"({terms}) AND SRC:MED",
            "format": "json",
            "resultType": "core",
            "pageSize": str(min(max(int(limit), 5), 25)),
        })
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + params
        request = urllib.request.Request(url, headers={"User-Agent": f"{self.tool_name}/1.0"})
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=float(self.timeout_seconds)) as response:
                payload = json.loads(response.read(8_000_000).decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, socket.timeout, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return [], f"Europe PMC 回退检索失败（{type(exc).__name__}）。"
        documents, skipped = _parse_europepmc(payload, self.source_id)
        if not documents:
            return [], "Europe PMC 回退未返回可用的 PubMed 记录（缺标题或摘要）。"
        warning = f"Europe PMC 有 {skipped} 条记录缺少 PMID、标题或摘要，未加入证据列表。" if skipped else ""
        return documents[:limit], warning

    def _base_params(self) -> dict[str, str]:
        params = {"tool": self.tool_name}
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def _request(self, endpoint: str, params: Mapping[str, str]) -> bytes:
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/" + endpoint + ".fcgi"
        safe_params = dict(params)
        # API key is needed by NCBI but is never included in exception text.
        if self.transport is not None:
            try:
                return self.transport(url, safe_params, float(self.timeout_seconds))
            except PubMedError:
                raise
            except Exception as exc:
                raise PubMedError("PubMed transport failed", stage=endpoint, retryable=True) from exc
        query = urllib.parse.urlencode(safe_params)
        request = urllib.request.Request(url + "?" + query, headers={"User-Agent": f"{self.tool_name}/1.0"})
        try:
            # 运行环境可能导出失效的 HTTP(S)_PROXY；NCBI E-utilities 是公开 HTTPS，显式直连。
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=float(self.timeout_seconds)) as response:
                return response.read(8_000_000)
        except urllib.error.HTTPError as exc:
            reason = _upstream_error_text(exc, self.api_key)
            message = "PubMed upstream HTTP error" + (f"（{reason}）" if reason else "")
            raise PubMedError(message, stage=endpoint, status_code=exc.code, retryable=exc.code == 429 or exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise PubMedError("PubMed network or timeout error", stage=endpoint, retryable=True) from exc


def _upstream_error_text(exc: urllib.error.HTTPError, api_key: str = "") -> str:
    """Extract a short, redacted reason from an NCBI HTTP error body.

    NCBI returns either an ``<ERROR>`` XML fragment or an HTML diagnostic
    page.  The diagnostic page echoes the request URL, which may contain
    ``api_key``; both the configured key and any ``api_key=`` parameter are
    redacted before the reason reaches warnings or trace.
    """
    try:
        raw = exc.read(65536)
    except Exception:
        return ""
    text = raw.decode("utf-8", "replace")
    match = re.search(r"<ERROR>(.*?)</ERROR>", text, re.DOTALL | re.IGNORECASE)
    if match:
        reason = match.group(1)
    else:
        # NCBI 的 HTML 诊断页没有 <ERROR>，只给 Error/Server 两行。
        parts: list[str] = []
        for label in ("Error", "Server"):
            found = re.search(rf"{label}:\s*([^\n<]+)", text, re.IGNORECASE)
            if found:
                parts.append(f"{label} {found.group(1).strip()}")
        reason = ", ".join(parts)
    reason = re.sub(r"<[^>]+>", " ", reason)
    reason = re.sub(r"\s+", " ", reason).strip()
    if api_key:
        reason = reason.replace(api_key, "[REDACTED]")
    reason = re.sub(r"(api_key=)[^&\s\"']+", r"\1[REDACTED]", reason)
    return reason[:240]


def _short_reason(text: str, api_key: str = "") -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if api_key:
        value = value.replace(api_key, "[REDACTED]")
    value = re.sub(r"(api_key=)[^&\s\"']+", r"\1[REDACTED]", value)
    return value[:240]


def _safe_term(plan: QueryPlan) -> str:
    """Build a precise PubMed query: disease topic AND neonatal population.

    The first reviewed topic term is the disease anchor; synonyms are only a
    fallback when the anchor returns no PMIDs. Units such as ``mmol/L`` are
    removed because numeric conditions are not indexed as text.

    When a reviewed mapping table supplied the terms, the query keeps the
    concept binding and uses PubMed field tags (``[tiab]`` for English,
    ``[MeSH Terms]`` for MeSH) instead of one undifferentiated quoted list.
    """
    if mapping_active(plan):
        mapped = _mapped_safe_term(plan)
        if mapped:
            return mapped
    primary = primary_topic(plan)
    if primary:
        # The disease anchor, plus at most two non-unit English words that
        # actually appeared in the question. Reviewed synonyms stay fallback-only.
        terms = [primary]
        terms.extend(
            term for term in plan.question_english_terms
            if len(str(term).strip()) >= 4
            and not is_unit_term(term)
            and not is_generic_population(term)
        )
        terms = list(dict.fromkeys(str(term).strip() for term in terms if str(term).strip()))[:3]
        query = _quoted(terms[0]) if len(terms) == 1 else "(" + " OR ".join(_quoted(term) for term in terms) + ")"
        population = population_clause(plan)
        if population:
            query += f" AND ({population})"
        return query
    return _fallback_term(plan)


def _mapped_safe_term(plan: QueryPlan) -> str:
    """PubMed query for the reviewed mapping path (concept first, then tags)."""
    entry = primary_mapping_entry(plan)
    if entry is None:
        return ""
    clauses: list[str] = []
    for term in entry.english:
        cleaned = str(term).strip()
        if not cleaned or is_unit_term(cleaned) or is_generic_population(cleaned):
            continue
        clause = f"{_quoted(cleaned)}[tiab]"
        if clause not in clauses:
            clauses.append(clause)
    for term in entry.mesh:
        cleaned = str(term).strip()
        if not cleaned or is_unit_term(cleaned):
            continue
        clause = f"{_quoted(cleaned)}[MeSH Terms]"
        if clause not in clauses:
            clauses.append(clause)
    # Question English words still participate, but only as a bounded recall
    # extension; they never displace the reviewed concept anchor.
    for term in plan.question_english_terms:
        cleaned = str(term).strip()
        if len(cleaned) < 4 or is_unit_term(cleaned) or is_generic_population(cleaned):
            continue
        clause = f"{_quoted(cleaned)}[tiab]"
        if clause not in clauses:
            clauses.append(clause)
        if len(clauses) >= 5:
            break
    if not clauses:
        return ""
    query = "(" + " OR ".join(clauses) + ")"
    population = _mapped_population_clause(plan)
    if population:
        query += f" AND ({population})"
    return query


def _mapped_population_clause(plan: QueryPlan) -> str:
    """Population restriction derived from the mapping table when available.

    A population row (for example ``早产儿``) keeps its reviewed English/MeSH
    products; otherwise the legacy keyword heuristic is tagged with ``[tiab]``.
    """
    clauses: list[str] = []
    population_markers = ("新生儿", "早产", "婴儿", "newborn", "neonat", "infant", "preterm")
    for entry in expanded_mapping_entries(plan):
        english = [str(term).strip() for term in entry.english if str(term).strip() and not is_unit_term(term)]
        # Only a row made up entirely of generic population terms is a
        # population row.  A disease row may legitimately carry one population
        # synonym (e.g. ``preterm infant``) without becoming the population
        # filter for the whole query.
        is_population = bool(english) and all(is_generic_population(term) for term in english)
        if not is_population and not english:
            is_population = any(marker in entry.concept.lower() for marker in population_markers)
        if not is_population:
            continue
        for term in english:
            clause = f"{_quoted(term)}[tiab]"
            if clause not in clauses:
                clauses.append(clause)
        for term in entry.mesh:
            cleaned = str(term).strip()
            if not cleaned or is_unit_term(cleaned):
                continue
            clause = f"{_quoted(cleaned)}[MeSH Terms]"
            if clause not in clauses:
                clauses.append(clause)
    if clauses:
        return " OR ".join(clauses)
    legacy = population_clause(plan)
    if not legacy:
        return ""
    return " OR ".join(f"{_quoted(token.strip())}[tiab]" for token in legacy.split(" OR ") if token.strip())


def _mapped_fallback_term(plan: QueryPlan) -> str:
    """Broad OR fallback that keeps mapping concept products tagged."""
    clauses: list[str] = []
    for entry in expanded_mapping_entries(plan):
        for term in entry.english:
            cleaned = str(term).strip()
            if cleaned and not is_unit_term(cleaned):
                clause = f"{_quoted(cleaned)}[tiab]"
                if clause not in clauses:
                    clauses.append(clause)
        for term in entry.mesh:
            cleaned = str(term).strip()
            if cleaned and not is_unit_term(cleaned):
                clause = f"{_quoted(cleaned)}[MeSH Terms]"
                if clause not in clauses:
                    clauses.append(clause)
    for term in (*plan.question_english_terms, *plan.english_terms, *plan.mesh_terms):
        cleaned = str(term).strip()
        if cleaned and not is_unit_term(cleaned):
            clause = f"{_quoted(cleaned)}[tiab]"
            if clause not in clauses:
                clauses.append(clause)
    if not clauses:
        return _fallback_term(plan)
    expression = "(" + " OR ".join(clauses[:12]) + ")"
    population = _mapped_population_clause(plan)
    if population:
        expression += f" AND ({population})"
    return expression


def _fallback_term(plan: QueryPlan) -> str:
    candidates = [
        term for term in (*plan.english_terms, *plan.mesh_terms, *plan.question_english_terms)
        if not is_unit_term(str(term))
    ]
    if not candidates:
        # Snapshot-derived reviewed terms are already allowed to leave the
        # process; use the ASCII concepts only, never raw Chinese records.
        candidates = [
            str(item) for item in plan.concepts
            if str(item).isascii() and _safe_ascii_or_cjk(str(item)) and not is_unit_term(str(item))
        ]
    candidates = list(dict.fromkeys(str(term).strip() for term in candidates if str(term).strip()))[:8]
    dynamic = [text.strip() for text in plan.machine_translations if str(text).strip()][:3]
    if not candidates:
        return " OR ".join(f"({text})" for text in dynamic)
    expression = "(" + " OR ".join(_quoted(term) for term in candidates) + ")"
    population = population_clause(plan)
    if population:
        expression += f" AND ({population})"
    return expression


def _quoted(term: str) -> str:
    return '"' + str(term).replace('"', "") + '"'


def _safe_ascii_or_cjk(value: str) -> bool:
    return bool(value and len(value) <= 80 and not any(token in value for token in ("patient_ref", "病历", "身份证", "住院号")))


# EFetch 的 DOCTYPE 只声明外部 DTD；实体声明不是正常响应的一部分，
# 一旦出现就可能被上游用来触发实体扩展，因此在解析前直接拒绝。
_PUBMED_ENTITY_DECL = re.compile(rb"<!ENTITY\b", re.IGNORECASE)
_PUBMED_DOCTYPE = re.compile(rb"<!DOCTYPE\b(?:[^>\[]|\[.*?\])*?>", re.DOTALL | re.IGNORECASE)


def _parse_efetch(raw: bytes, source_id: str) -> tuple[list[SourceDocument], int]:
    if _PUBMED_ENTITY_DECL.search(raw):
        raise PubMedError("PubMed EFetch XML 包含不允许的实体声明", stage="efetch_parse")
    payload = _PUBMED_DOCTYPE.sub(b"", raw, count=1)
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise PubMedError("PubMed EFetch XML 解析失败", stage="efetch_parse") from exc
    documents: list[SourceDocument] = []
    skipped = 0
    for article in root.findall(".//PubmedArticle"):
        pmid = _text(article, ".//PMID")
        title = " ".join(_text_values(article.findall(".//ArticleTitle"))).strip()
        abstract = " ".join(_text_values(article.findall(".//AbstractText"))).strip()
        if not pmid or not title or not abstract:
            skipped += 1
            continue
        doi = None
        for item in article.findall(".//ArticleId"):
            if item.attrib.get("IdType") == "doi" and item.text:
                doi = item.text.strip()
                break
        published = _publication_date(article)
        journal = _text(article, ".//Journal/Title") or None
        documents.append(SourceDocument(
            source_id=source_id, document_id=f"pmid-{pmid}", kind="pubmed", title=title,
            text=abstract, url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", published_at=published,
            locator="Abstract", pmid=pmid, doi=doi, index_snapshot=None,
            metadata={"journal": journal, "live_source": True, "content_mode": "abstract"},
        ))
    return documents, skipped


def _parse_europepmc(payload: Any, source_id: str) -> tuple[list[SourceDocument], int]:
    """Map a Europe PMC ``resultType=core`` payload to PubMed-style documents."""
    results = payload.get("resultList", {}).get("result", []) if isinstance(payload, Mapping) else []
    documents: list[SourceDocument] = []
    skipped = 0
    seen: set[str] = set()
    for row in results if isinstance(results, list) else []:
        if not isinstance(row, Mapping) or str(row.get("source") or "").upper() != "MED":
            continue
        pmid = str(row.get("pmid") or "").strip()
        title = _epmc_text(row.get("title"))
        abstract = _epmc_text(row.get("abstractText"))
        if not pmid or not title or not abstract or pmid in seen:
            skipped += 1
            continue
        seen.add(pmid)
        doi = str(row.get("doi") or "").strip() or None
        journal = None
        info = row.get("journalInfo")
        if isinstance(info, Mapping):
            journal_info = info.get("journal")
            if isinstance(journal_info, Mapping):
                journal = str(journal_info.get("title") or "").strip() or None
        documents.append(SourceDocument(
            source_id=source_id, document_id=f"pmid-{pmid}", kind="pubmed", title=title,
            text=abstract, url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            published_at=_epmc_date(row.get("firstPublicationDate")),
            locator="Abstract", pmid=pmid, doi=doi, index_snapshot=None,
            metadata={
                "journal": journal,
                "live_source": True,
                "content_mode": "abstract",
                "upstream": "europepmc_fallback",
                "publication_types": _epmc_pub_types(row),
            },
        ))
    return documents, skipped


def _epmc_text(value: Any) -> str:
    text = str(value or "")
    if not text.strip():
        return ""
    # Europe PMC 的结构化摘要会带 <h4> 等标签；去掉标签再做空白归一。
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


def _epmc_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 4 and text.isdigit():
        return f"{text}-01-01"
    if len(text) == 7 and re.fullmatch(r"\d{4}-\d{2}", text):
        return f"{text}-01"
    if len(text) >= 10 and re.match(r"\d{4}-\d{2}-\d{2}", text[:10]):
        return text[:10]
    return None


def _epmc_pub_types(row: Mapping[str, Any]) -> list[str]:
    listed = row.get("pubTypeList")
    if isinstance(listed, Mapping):
        values = listed.get("pubType")
        if isinstance(values, list):
            return [str(value) for value in values if str(value).strip()]
        if values:
            return [str(values)]
    raw = row.get("pubType")
    if isinstance(raw, list):
        return [str(value) for value in raw if str(value).strip()]
    return [str(raw)] if raw else []


def _text(node: ET.Element, path: str) -> str:
    found = node.find(path)
    return " ".join(found.itertext()).strip() if found is not None else ""


def _text_values(nodes) -> list[str]:
    return [" ".join(node.itertext()).strip() for node in nodes if " ".join(node.itertext()).strip()]


def _publication_date(article: ET.Element) -> str | None:
    year = _text(article, ".//PubDate/Year")
    month = _text(article, ".//PubDate/Month")
    day = _text(article, ".//PubDate/Day")
    if year and year.isdigit():
        try:
            month_number = _month_number(month)
            return date(int(year), month_number, int(day) if day.isdigit() else 1).isoformat()
        except ValueError:
            return f"{year}-01-01"
    medline = _text(article, ".//PubDate/MedlineDate")
    for token in medline.split():
        if token.isdigit() and len(token) == 4:
            return f"{token}-01-01"
    return None


def _month_number(value: str) -> int:
    if value.isdigit():
        return max(1, min(12, int(value)))
    names = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    return names.get(value[:3].lower(), 1)
