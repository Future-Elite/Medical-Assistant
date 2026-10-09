"""Deterministic Chinese-oriented query planning with controlled expansion."""

from __future__ import annotations

import re
from collections.abc import Mapping

from .models import QueryPlan, RetrievalRequest


DEFAULT_SOURCE_ORDER = ("guideline", "pubmed", "drug", "clinical_trial", "local_knowledge")
_RECOMMENDATION_MARKERS = ("指南", "推荐", "应当", "适应证", "禁忌", "剂量", "治疗", "诊疗")
_QUESTION_EN_STOPWORDS = frozenset(
    """a an and are as at be been being both but by can could did do does doing down during each few for from
    further had has have having he her here hers him his how i if in into is it its just may me might more most
    my no nor not of off on once only or other our out over own same she should so some such than that the
    their them then there these they this those through to too under until up very was we were what when where
    which while who whom why will with would you your""".split()
)
_QUERY_TASK_TERMS = frozenset(
    "recent latest current new study studies evidence evidences research researches literature article articles "
    "paper papers publication publications find search show summarize summary review reviews".split()
)
_RELATION_TERMS = frozenset(("associated", "related", "regarding", "concerning"))
_MEDICAL_PHRASES = tuple(
    tuple(phrase.split())
    for phrase in (
        "non small cell lung cancer",
        "interstitial lung disease",
        "type 1 diabetes",
        "type 2 diabetes",
        "adverse drug reactions",
        "adverse effects",
        "side effects",
        "drug interactions",
        "risk factors",
        "clinical trials",
    )
)


def build_query_plan(request: RetrievalRequest, known_sources: list[dict[str, object]]) -> QueryPlan:
    """Build transparent variants without an LLM or an uncontrolled synonym API.

    ``user_terms`` is a reviewed terminology map supplied by the caller, such
    as ``{"高血压": ["hypertension"]}``.  This avoids silently substituting a
    medically different diagnosis while still supporting Chinese-English sources.
    """
    concepts = _unique((*_extract_terms(request.question), *request.patient_terms))
    expansion: list[str] = []
    request_text = " ".join((request.question, *request.patient_terms)).lower()
    for concept, mapped_terms in request.user_terms.items():
        if str(concept).strip() and str(concept).lower() in request_text:
            expansion.extend(mapped_terms)
    variants = _unique((request.question, " ".join(concepts), " ".join((*concepts, *expansion))))
    variants = tuple(item for item in variants if item.strip())

    requested = request.source_kinds or DEFAULT_SOURCE_ORDER
    # Route by declared *kind*, never by a connector's implementation-specific
    # ID.  A future ``cn_guideline_vault`` and an existing ``guideline`` source
    # can therefore coexist without a special case in this package.
    source_order_list: list[str] = []
    for kind in requested:
        for source in known_sources:
            source_id = str(source["source_id"])
            kinds = {str(value) for value in source.get("kinds", [])}
            if (kind in kinds or kind == source_id) and source_id not in source_order_list:
                source_order_list.append(source_id)
    if not source_order_list:
        source_order_list = [str(item["source_id"]) for item in known_sources]
    recommendation = any(marker in request.question for marker in _RECOMMENDATION_MARKERS)
    if recommendation:
        guideline_sources = [
            str(item["source_id"]) for item in known_sources
            if "guideline" in {str(value) for value in item.get("kinds", [])}
        ]
        source_order_list = guideline_sources + [item for item in source_order_list if item not in guideline_sources]
    rationale = ["保留原始中文问题作为精确查询。", "仅扩展调用方提供的受控术语。"]
    if recommendation:
        rationale.append("问题含诊疗建议线索，优先检索指南来源。")
    if request.patient_terms:
        rationale.append("患者相关词仅作为最小化术语，不传递原始病历。")
    filters = dict(request.filters)
    filters["source_kinds"] = list(request.source_kinds)
    filters["pubmed_terms"] = list(_pubmed_terms(request.question, expansion))
    if filters["pubmed_terms"]:
        rationale.append("PubMed 查询已清理任务词、规范化连字符并保留常见医学短语。")
    return QueryPlan(request.question, variants, concepts, tuple(source_order_list), filters, tuple(rationale))


def _extract_terms(text: str) -> tuple[str, ...]:
    ascii_terms = _pubmed_terms(text)
    cjk_terms = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    return _unique((*ascii_terms, *cjk_terms))


def _pubmed_terms(text: str, reviewed_terms=()) -> tuple[str, ...]:
    """Return ordered PubMed concepts without using an LLM.

    Common request words are discarded, letter-to-letter hyphens become word
    boundaries, and a small auditable list keeps frequent medical phrases
    intact. Explicitly reviewed terms are retained as phrases.
    """
    normalized = re.sub(r"(?<=[A-Za-z])[-‐‑–—](?=[A-Za-z])", " ", str(text).lower())
    words = re.findall(r"[a-z][a-z0-9+./]*", normalized)
    tokens: list[str] = []
    for word in words:
        if word in _QUESTION_EN_STOPWORDS or word in _QUERY_TASK_TERMS:
            continue
        tokens.append("|" if word in _RELATION_TERMS else word)

    matches: dict[int, tuple[int, str]] = {}
    occupied: set[int] = set()
    for phrase in _MEDICAL_PHRASES:
        size = len(phrase)
        for start in range(len(tokens) - size + 1):
            indexes = set(range(start, start + size))
            if indexes & occupied or tuple(tokens[start:start + size]) != phrase:
                continue
            matches[start] = (size, " ".join(phrase))
            occupied.update(indexes)

    terms: list[str] = []
    index = 0
    while index < len(tokens):
        if index in matches:
            size, phrase = matches[index]
            terms.append(phrase)
            index += size
        elif index not in occupied and tokens[index] != "|":
            terms.append(tokens[index])
            index += 1
        else:
            index += 1

    for term in reviewed_terms:
        cleaned = re.sub(r"\s+", " ", re.sub(r"(?<=[A-Za-z])[-‐‑–—](?=[A-Za-z])", " ", str(term))).strip()
        if cleaned:
            terms.append(cleaned)
    return _unique(terms)


def _unique(items) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = str(item).strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            out.append(cleaned)
            seen.add(key)
    return tuple(out)
