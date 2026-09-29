"""Dependency-free hybrid ranking, deduplication, and evidence diversification."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import replace
from datetime import date
from typing import Iterable, Sequence

from .models import EvidenceStatus, QueryPlan, RankedDocument, SourceDocument, parse_iso_date


SOURCE_PRIOR = {"guideline": 1.0, "drug": 0.80, "pubmed": 0.70, "clinical_trial": 0.65, "local_knowledge": 0.40}


def rank_documents(
    plan: QueryPlan, documents: Iterable[SourceDocument], *, as_of: str | None, top_k: int
) -> tuple[list[RankedDocument], list[str]]:
    """Rank source results using lexical relevance + reciprocal-rank fusion.

    Vector/cross-encoder scores can later be supplied through ``metadata`` as
    ``connector_rank`` or ``semantic_score``.  The fallback remains completely
    functional with the standard library, keeping deployment compatible.
    """
    deduped, duplicate_count = _deduplicate(documents)
    status_docs = [(doc, _status(doc, as_of)) for doc in deduped]
    eligible = [(doc, status) for doc, status in status_docs if status not in {EvidenceStatus.FUTURE_AT_QUERY_TIME, EvidenceStatus.WITHDRAWN}]
    lexical = {doc.identity_key(): _relevance(plan.variants, doc) for doc, _ in eligible}
    per_source = defaultdict(list)
    for doc, _ in eligible:
        per_source[doc.source_id].append(doc)
    rrf: dict[str, float] = defaultdict(float)
    for docs in per_source.values():
        for rank, doc in enumerate(sorted(docs, key=lambda d: lexical[d.identity_key()], reverse=True), 1):
            rrf[doc.identity_key()] += 1.0 / (60 + rank)
    candidates: list[RankedDocument] = []
    for doc, status in eligible:
        key = doc.identity_key()
        authority = _authority(doc)
        freshness = _freshness(doc, as_of)
        semantic = _bounded_number(doc.metadata.get("semantic_score"))
        score = 0.55 * lexical[key] + 0.20 * _normalise_rrf(rrf[key]) + 0.15 * authority + 0.10 * freshness
        if semantic is not None:
            score = 0.70 * score + 0.30 * semantic
        matched = tuple(query for query in plan.variants if _token_overlap(query, _searchable(doc)) > 0)
        explanation = [f"lexical={lexical[key]:.3f}", f"authority={authority:.3f}", f"freshness={freshness:.3f}"]
        if semantic is not None:
            explanation.append("使用连接器提供的 semantic_score。")
        candidates.append(RankedDocument(doc, score, lexical[key], rrf[key], authority, freshness, status, matched, tuple(explanation)))
    selected = _mmr_select(sorted(candidates, key=lambda item: item.score, reverse=True), top_k)
    warnings: list[str] = []
    if duplicate_count:
        warnings.append(f"已按 DOI/PMID/标题去重 {duplicate_count} 条候选。")
    excluded = len(status_docs) - len(eligible)
    if excluded:
        warnings.append(f"已排除 {excluded} 条在查询时间之后或已撤回的证据。")
    if not selected:
        warnings.append("没有满足当前时间和状态过滤条件的外部证据。")
    return selected, warnings


def _deduplicate(documents: Iterable[SourceDocument]) -> tuple[list[SourceDocument], int]:
    by_key: dict[str, SourceDocument] = {}
    duplicates = 0
    for document in documents:
        key = document.identity_key()
        current = by_key.get(key)
        if current is None:
            by_key[key] = document
        else:
            duplicates += 1
            if _authority(document) > _authority(current):
                by_key[key] = document
    return list(by_key.values()), duplicates


def _status(document: SourceDocument, as_of: str | None) -> EvidenceStatus:
    if str(document.metadata.get("status", "")).lower() in {"withdrawn", "retracted"}:
        return EvidenceStatus.WITHDRAWN
    doc_date, cutoff = parse_iso_date(document.effective_date()), parse_iso_date(as_of)
    if doc_date is None:
        return EvidenceStatus.UNKNOWN_DATE
    if cutoff is not None and doc_date > cutoff:
        return EvidenceStatus.FUTURE_AT_QUERY_TIME
    return EvidenceStatus.CURRENT


def _relevance(queries: Sequence[str], document: SourceDocument) -> float:
    text = _searchable(document)
    return max((_token_overlap(query, text) for query in queries), default=0.0)


def _searchable(document: SourceDocument) -> str:
    return " ".join((document.title, document.text, str(document.metadata.get("keywords", ""))))


def _token_overlap(query: str, text: str) -> float:
    q_tokens, d_tokens = set(_tokens(query)), set(_tokens(text))
    if not q_tokens or not d_tokens:
        return 0.0
    return len(q_tokens & d_tokens) / math.sqrt(len(q_tokens) * len(d_tokens))


def _tokens(text: str) -> list[str]:
    ascii_tokens = re.findall(r"[a-z0-9+./-]{2,}", text.lower())
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    cjk_bigrams = [cjk[index:index + 2] for index in range(max(0, len(cjk) - 1))]
    return ascii_tokens + cjk_bigrams


def _authority(document: SourceDocument) -> float:
    value = SOURCE_PRIOR.get(document.kind, 0.30)
    if str(document.metadata.get("official", "")).lower() in {"true", "1", "yes"}:
        value = min(1.0, value + 0.15)
    return value


def _freshness(document: SourceDocument, as_of: str | None) -> float:
    doc_date, cutoff = parse_iso_date(document.effective_date()), parse_iso_date(as_of)
    if doc_date is None or cutoff is None:
        return 0.5
    days = max(0, (cutoff - doc_date).days)
    return max(0.10, 1.0 - days / (365.25 * 10))


def _normalise_rrf(value: float) -> float:
    return min(1.0, value * 61.0)


def _bounded_number(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if 0.0 <= value <= 1.0 else None


def _mmr_select(candidates: list[RankedDocument], top_k: int) -> list[RankedDocument]:
    selected: list[RankedDocument] = []
    remaining = list(candidates)
    while remaining and len(selected) < top_k:
        def mmr(candidate: RankedDocument) -> float:
            redundancy = max((_token_overlap(_searchable(candidate.document), _searchable(other.document)) for other in selected), default=0.0)
            return 0.82 * candidate.score - 0.18 * redundancy
        choice = max(remaining, key=mmr)
        selected.append(choice)
        remaining.remove(choice)
    return selected
