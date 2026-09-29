"""Deterministic Chinese-oriented query planning with controlled expansion."""

from __future__ import annotations

import re
from collections.abc import Mapping

from .models import QueryPlan, RetrievalRequest


DEFAULT_SOURCE_ORDER = ("guideline", "pubmed", "drug", "clinical_trial", "local_knowledge")
_RECOMMENDATION_MARKERS = ("指南", "推荐", "应当", "适应证", "禁忌", "剂量", "治疗", "诊疗")


def build_query_plan(request: RetrievalRequest, known_sources: list[dict[str, object]]) -> QueryPlan:
    """Build transparent variants without an LLM or an uncontrolled synonym API.

    ``user_terms`` is a reviewed terminology map supplied by the caller, such
    as ``{"高血压": ["hypertension"]}``.  This avoids silently substituting a
    medically different diagnosis while still supporting Chinese-English sources.
    """
    concepts = _unique((*_extract_terms(request.question), *request.patient_terms))
    expansion: list[str] = []
    for concept in concepts:
        expansion.extend(request.user_terms.get(concept, ()))
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
    return QueryPlan(request.question, variants, concepts, tuple(source_order_list), filters, tuple(rationale))


def _extract_terms(text: str) -> tuple[str, ...]:
    ascii_terms = re.findall(r"[A-Za-z][A-Za-z0-9+./-]{1,}", text)
    cjk_terms = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    return _unique((*ascii_terms, *cjk_terms))


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
