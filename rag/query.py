"""Transparent, controlled bilingual query planning for the RAG package."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .his import PatientSnapshot, minimize_patient_snapshot
from .models import QueryPlan, RetrievalRequest

DEFAULT_SOURCE_ORDER = ("guideline", "pubmed", "drug", "clinical_trial", "local_knowledge")
_RECOMMENDATION_MARKERS = ("指南", "推荐", "应当", "适应证", "禁忌", "剂量", "治疗", "guideline", "recommend")
_UPDATE_MARKERS = ("更新", "最新", "update", "recent", "new evidence")
# 问题内英文词会先过滤常见功能词（what/is/the/...）与不含字母的纯数值；
# 调用方显式提供的 english_terms 不做任何过滤（那是已审核术语）。
_QUESTION_EN_STOPWORDS = frozenset(
    """a an and are as at be been being both but by can could did do does doing down during each few for from
    further had has have having he her here hers him his how i if in into is it its just may me might more most
    my no nor not of off on once only or other our out over own same she should so some such than that the
    their them then there these they this those through to too under until up very was we were what when where
    which while who whom why will with would you your""".split()
)


def build_query_plan(request: RetrievalRequest, known_sources: list[dict[str, object]]) -> QueryPlan:
    reviewed_external = minimize_patient_snapshot(request.patient_snapshot) if isinstance(request.patient_snapshot, PatientSnapshot) else ()
    extracted = (*_extract_terms(request.question), *request.patient_terms, *reviewed_external, *_facet_values(request.facets))
    request_text = " ".join((request.question, *request.patient_terms, *reviewed_external, *_facet_values(request.facets))).lower()
    reviewed_concepts = tuple(
        key for key in request.user_terms
        if str(key).strip() and str(key).lower() in request_text
    )
    concepts = _unique((*extracted, *reviewed_concepts))
    english: list[str] = list(request.english_terms)
    mesh: list[str] = list(request.mesh_terms)
    question_english = _question_english_terms(request.question)
    expansion_trace: list[Mapping[str, Any]] = []
    if question_english:
        expansion_trace.append({"concept": "question", "terms": list(question_english), "source": "question_ascii"})
    for concept in concepts:
        mapped = tuple(_strings(request.user_terms.get(concept, ())))
        if mapped:
            english.extend(mapped)
            expansion_trace.append({"concept": concept, "terms": list(mapped), "source": "reviewed_user_terms"})
    english = list(_unique(english))
    mesh = list(_unique(mesh))
    variants = _unique((request.question, " ".join(concepts), " ".join((*concepts, *english)), " ".join((*english, *mesh))))

    requested = request.source_kinds or DEFAULT_SOURCE_ORDER
    source_order: list[str] = []
    for kind in requested:
        for source in known_sources:
            source_id = str(source["source_id"])
            kinds = {str(value) for value in source.get("kinds", [])}
            if (kind in kinds or kind == source_id) and source_id not in source_order:
                source_order.append(source_id)
    if not source_order:
        source_order = [str(item["source_id"]) for item in known_sources]

    question_lower = request.question.lower()
    recommendation = any(marker in question_lower for marker in _RECOMMENDATION_MARKERS)
    update = any(marker in question_lower for marker in _UPDATE_MARKERS)
    guideline_sources = [
        str(item["source_id"]) for item in known_sources
        if "guideline" in {str(value) for value in item.get("kinds", [])}
    ]
    if recommendation and guideline_sources and not request.source_kinds:
        source_order = guideline_sources + [item for item in source_order if item not in guideline_sources]
    rationale = ["保留原始问题作为精确查询。", "仅扩展调用方提供的审核术语、英文词和 MeSH 词。"]
    if question_english:
        rationale.append("问题中的英文词已直接加入英文检索词（已过滤常见功能词与纯数值）。")
    rationale.append("问题中的中文片段用于中文来源匹配；中文概念展开为英文仅使用已审核的术语映射，不自动翻译。")
    if recommendation:
        rationale.append("问题含推荐/指南线索，优先路由指南来源；这不是绝对正确性排序。")
    if update:
        rationale.append("问题含更新线索，保留时间窗口并同时检索指南版本与 PubMed 新证据。")
    if request.patient_terms:
        rationale.append("患者相关词仅作为最小化术语，不发送原始病历。")
    if request.facets:
        rationale.append("临床问题槽位以结构化 facets 保留，避免把人群、结局和时间压成不可审计长句。")
    filters = dict(request.filters)
    filters["source_kinds"] = list(request.source_kinds)
    filters["as_of"] = request.as_of
    return QueryPlan(
        original_question=request.question,
        variants=tuple(item for item in variants if item.strip()),
        concepts=concepts,
        source_order=tuple(source_order),
        filters=filters,
        rationale=tuple(rationale),
        query_language=request.query_language,
        clinical_intent=request.clinical_intent or _infer_intent(request.question),
        facets={key: tuple(value) for key, value in request.facets.items()},
        english_terms=tuple(english),
        mesh_terms=tuple(mesh),
        expansion_trace=tuple(expansion_trace),
        question_english_terms=question_english,
    )


def _infer_intent(question: str) -> str:
    lower = question.lower()
    if any(marker in lower for marker in _UPDATE_MARKERS):
        return "evidence_update"
    if any(marker in lower for marker in _RECOMMENDATION_MARKERS):
        return "guideline_recommendation"
    if any(marker in lower for marker in ("安全", "不良反应", "禁忌", "adverse", "safety")):
        return "drug_safety"
    return "clinical_question"


def _facet_values(facets: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for value in facets.values():
        values.extend(_strings(value))
    return tuple(values)


def _extract_terms(text: str) -> tuple[str, ...]:
    ascii_terms = re.findall(r"[A-Za-z][A-Za-z0-9+./-]{1,}", text)
    cjk_terms = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    return _unique((*ascii_terms, *cjk_terms))


def _question_english_terms(question: str) -> tuple[str, ...]:
    """问题中可直接用于英文检索的词。

    规则：≥2 字符、含至少一个字母（排除 "38.9" 这类数值）、不在功能词表内；
    去重后保持出现顺序。仅作用于问题文本；调用方审核术语不做过滤。
    """
    words = re.findall(r"[A-Za-z][A-Za-z0-9+./-]{1,}", question)
    kept = [
        word.lower() for word in words
        if any(ch.isalpha() for ch in word) and word.lower() not in _QUESTION_EN_STOPWORDS
    ]
    return _unique(kept)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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
