"""Fielded BM25, optional semantic scores, and auditable RRF ranking."""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import replace
from typing import Iterable, Mapping, Sequence

from .bm25 import BM25Score, score_documents, tokenize
from .lexical import score_documents_ql, score_documents_rm3
from .models import EvidenceStatus, QueryPlan, RankedDocument, SourceDocument, parse_iso_date

SOURCE_PRIOR = {"guideline": 1.0, "drug": 0.80, "pubmed": 0.70, "clinical_trial": 0.65, "local_knowledge": 0.40}


LEXICAL_METHODS = ("bm25", "ql", "rm3")
NEURAL_METHODS = ("dense", "xlingual", "splade", "colbert")


def rank_documents(plan: QueryPlan, documents: Iterable[SourceDocument], *, as_of: str | None, top_k: int,
                   methods: Sequence[str] = ("bm25",),
                   semantic_scores: Mapping[str, float] | None = None,
                   neural_scores: Mapping[str, Mapping[str, float]] | None = None,
                   field_weights: Mapping[str, float] | None = None) -> tuple[list[RankedDocument], list[str]]:
    """Rank candidates with one or more retrieval signals + RRF fusion.

    ``methods`` selects lexical scorers (bm25/ql/rm3; computed here) and neural
    signals (dense/splade/colbert; supplied via ``neural_scores`` as [0,1]
    values per method). ``semantic_scores`` is kept for backward compatibility
    and is treated as ``neural_scores["dense"]``. With two or more active
    signals the per-signal ranks are fused via RRF (k=60); authority/freshness
    never resurrect a candidate that no signal retrieved (relevance gate).
    """
    deduped, duplicate_count, conflicts = _deduplicate(documents)
    statuses = [(doc, _status(doc, as_of)) for doc in deduped]
    eligible = [(doc, status) for doc, status in statuses if status not in {
        EvidenceStatus.FUTURE_AT_QUERY_TIME, EvidenceStatus.WITHDRAWN, EvidenceStatus.SUPERSEDED,
    }]
    lexical_methods = [method for method in methods if method in LEXICAL_METHODS]
    neural: dict[str, dict[str, float]] = {}
    if semantic_scores is not None:
        neural.setdefault("dense", {}).update(dict(semantic_scores))
    for method, scores in (neural_scores or {}).items():
        neural.setdefault(str(method), {}).update(dict(scores or {}))
    neural = {
        method: {str(key): value for key, value in ((k, _bounded_number(v)) for k, v in scores.items()) if value is not None}
        for method, scores in neural.items()
    }
    neural = {method: scores for method, scores in neural.items() if scores}

    eligible_docs = [doc for doc, _ in eligible]
    lexical: dict[str, dict[str, BM25Score]] = {}
    for method in lexical_methods:
        if method == "bm25":
            lexical[method] = score_documents(plan, eligible_docs, field_weights=field_weights)
        elif method == "ql":
            lexical[method] = score_documents_ql(plan, eligible_docs, field_weights=field_weights)
        elif method == "rm3":
            lexical[method] = score_documents_rm3(plan, eligible_docs, field_weights=field_weights)

    def lexical_score(method: str, key: str) -> float:
        return lexical.get(method, {}).get(key, BM25Score(0.0, (), (), "")).score

    # Query-relevance gate across every active signal.
    def relevant_candidate(key: str) -> bool:
        if any(lexical_score(method, key) > 0.0 for method in lexical_methods):
            return True
        return any(scores.get(key, 0.0) > 0.0 for scores in neural.values())

    relevant = [(doc, status) for doc, status in eligible if relevant_candidate(doc.identity_key())]

    primary = lexical_methods[0] if lexical_methods else None
    primary_max = max((item.score for item in lexical.get(primary, {}).values()), default=0.0) if primary else 0.0
    lexical_norm = {
        key: (item.score / primary_max if primary_max else 0.0)
        for key, item in lexical.get(primary, {}).items()
    }

    # RRF over per-signal ranks: lexical methods ranked within each source
    # (mirroring the original behavior), neural methods ranked globally.
    # 只有在该路获得正分的候选参与该路的排名：零分候选在该路"名次"只是按文档
    # ID 的任意顺序（如中文问题对英文语料的 BM25 全零），计入 RRF 等于注入噪声；
    # 全零路线整体跳过（实测会把纯 xlingual 结果显著拉低，见 eval 报告）。
    rrf: dict[str, float] = defaultdict(float)
    primary_rank: dict[str, int] = {}
    if primary:
        per_source: dict[str, list[SourceDocument]] = defaultdict(list)
        for doc, _ in relevant:
            per_source[doc.source_id].append(doc)
        for source_docs in per_source.values():
            scored = [doc for doc in source_docs if lexical_score(primary, doc.identity_key()) > 0.0]
            ordered = sorted(scored, key=lambda doc: (-lexical_score(primary, doc.identity_key()), doc.identity_key()))
            primary_rank.update({doc.identity_key(): index for index, doc in enumerate(ordered, 1)})
    for method in lexical_methods:
        if method == primary:
            ranks = primary_rank
        else:
            scored = [doc for doc, _ in relevant if lexical_score(method, doc.identity_key()) > 0.0]
            ordered = sorted(scored, key=lambda doc: (-lexical_score(method, doc.identity_key()), doc.identity_key()))
            ranks = {doc.identity_key(): index for index, doc in enumerate(ordered, 1)}
        if not ranks:
            continue
        for key, rank in ranks.items():
            rrf[key] += 1.0 / (60 + rank)
    neural_rank: dict[str, dict[str, int]] = {}
    for method, scores in neural.items():
        positive = {key: value for key, value in scores.items() if value > 0.0}
        if not positive:
            continue
        rank_map = _rank_map(positive)
        neural_rank[method] = rank_map
        for key, rank in rank_map.items():
            rrf[key] += 1.0 / (60 + rank)
    rrf_norm = _normalise_rrf(rrf)

    candidates: list[RankedDocument] = []
    field_token_cache: dict[str, set[str]] = {}
    variant_token_cache = {query: set(tokenize(query)) for query in plan.variants}
    for doc, status in relevant:
        key = doc.identity_key()
        lexical_item = lexical.get(primary, {}).get(key) if primary else None
        base = 0.55 * lexical_norm.get(key, 0.0) + 0.20 * rrf_norm.get(key, 0.0) + 0.15 * _authority(doc) + 0.10 * _freshness(doc, as_of)
        best_neural_method, best_neural_score = None, None
        for method, scores in neural.items():
            value = scores.get(key)
            if value is not None and (best_neural_score is None or value > best_neural_score):
                best_neural_method, best_neural_score = method, value
        if best_neural_score is None:
            score = base
        elif primary:
            # 词面+神经：保持既有 0.70/0.30 组合语义。
            score = 0.70 * base + 0.30 * best_neural_score
        else:
            # 纯神经方法：神经分数为主分位，权威性/新鲜度作为小幅调整，
            # 保持 RRF/权威性不复活无信号候选的既有边界。
            score = 0.70 * best_neural_score + 0.20 * rrf_norm.get(key, 0.0) + 0.10 * _freshness(doc, as_of)
        doc_tokens = field_token_cache.get(key)
        if doc_tokens is None:
            doc_tokens = set(tokenize(" ".join(doc.field_values().values())))
            field_token_cache[key] = doc_tokens
        matched = tuple(query for query, tokens in variant_token_cache.items() if _token_overlap(tokens, doc_tokens) > 0)
        explanation = [f"authority={_authority(doc):.3f}", f"freshness={_freshness(doc, as_of):.3f}"]
        if primary:
            explanation.insert(0, f"{primary}={lexical_item.score:.4f}" if lexical_item else f"{primary}=0.0000")
        for method in lexical_methods:
            if method != primary:
                explanation.append(f"{method}={lexical_score(method, key):.4f}")
        for method, scores in neural.items():
            if key in scores:
                explanation.append(f"{method}={scores[key]:.4f}")
        explanation.append("RRF 仅融合排名，不代表证据质量或推荐强度。")
        signals = [*lexical_methods, *[m for m, scores in neural.items() if key in scores]]
        fusion_method = "+".join(signals) if signals else "none"
        if len(signals) > 1:
            fusion_method += "_rrf"
        candidates.append(RankedDocument(
            document=doc, score=score,
            lexical_score=lexical_item.score if lexical_item else 0.0,
            fusion_score=rrf.get(key, 0.0), authority_score=_authority(doc),
            freshness_score=_freshness(doc, as_of), evidence_status=status,
            matched_queries=matched, rank_explanation=tuple(explanation),
            retriever_id=primary or (best_neural_method or "none"),
            retriever_rank=(primary_rank.get(key) if primary else None) or (neural_rank.get(best_neural_method, {}).get(key) if best_neural_method else None),
            retriever_score=lexical_item.score if lexical_item else best_neural_score,
            semantic_rank=neural_rank.get(best_neural_method, {}).get(key) if best_neural_method else None,
            semantic_score=best_neural_score,
            fusion_method=fusion_method,
        ))
    selected = _mmr_select(sorted(candidates, key=lambda item: (-item.score, item.document.identity_key())), top_k)
    selected = [replace(item, fusion_rank=index) for index, item in enumerate(selected, 1)]
    warnings: list[str] = []
    if duplicate_count:
        warnings.append(f"已按 DOI/PMID/标题去重 {duplicate_count} 条候选。")
    if conflicts:
        warnings.append("检测到跨来源共享 DOI/PMID/标题的候选；已保留权威来源，但不能据此消除指南与文献的语义冲突。")
    excluded = len(statuses) - len(eligible)
    if excluded:
        warnings.append(f"已排除 {excluded} 条未来、撤回或已被替代的证据。")
    irrelevant = len(eligible) - len(relevant)
    if irrelevant:
        warnings.append(f"已排除 {irrelevant} 条与查询无检索相关度（词面/语义均无命中）的候选。")
    if not selected:
        warnings.append("没有满足当前时间、版本、状态和查询相关度条件的外部证据。")
    return selected, warnings


def _deduplicate(documents: Iterable[SourceDocument]) -> tuple[list[SourceDocument], int, bool]:
    by_key: dict[str, SourceDocument] = {}
    source_by_key: dict[str, set[str]] = defaultdict(set)
    duplicates = 0
    conflicts = False
    for document in documents:
        key = document.identity_key()
        source_by_key[key].add(document.source_id)
        current = by_key.get(key)
        if current is None:
            by_key[key] = document
        else:
            duplicates += 1
            if current.source_id != document.source_id:
                conflicts = True
            if _authority(document) > _authority(current) or (document.updated_at or "") > (current.updated_at or ""):
                by_key[key] = document
    return list(by_key.values()), duplicates, conflicts


def _status(document: SourceDocument, as_of: str | None) -> EvidenceStatus:
    raw = (document.status or str(document.metadata.get("status", ""))).lower()
    if raw in {"withdrawn", "retracted"}:
        return EvidenceStatus.WITHDRAWN
    if raw in {"superseded", "replaced", "retired"} or document.superseded_by:
        return EvidenceStatus.SUPERSEDED
    doc_date, cutoff = parse_iso_date(document.effective_date()), parse_iso_date(as_of)
    if doc_date is None:
        return EvidenceStatus.UNKNOWN_DATE
    if cutoff is not None and doc_date > cutoff:
        return EvidenceStatus.FUTURE_AT_QUERY_TIME
    return EvidenceStatus.CURRENT


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


def _rank_map(scores: Mapping[str, float]) -> dict[str, int]:
    return {key: index for index, (key, _) in enumerate(sorted(scores.items(), key=lambda item: (-item[1], item[0])), 1)}


def _normalise_rrf(scores: Mapping[str, float]) -> dict[str, float]:
    maximum = max(scores.values(), default=0.0)
    return {key: (value / maximum if maximum else 0.0) for key, value in scores.items()}


def _bounded_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0.0 <= number <= 1.0 else None


def _overlap(query: str, document: SourceDocument) -> float:
    q = set(tokenize(query))
    d = set(tokenize(" ".join(document.field_values().values())))
    return _token_overlap(q, d)


def _token_overlap(q: set[str], d: set[str]) -> float:
    return len(q & d) / math.sqrt(len(q) * len(d)) if q and d else 0.0



def _mmr_select(candidates: list[RankedDocument], top_k: int) -> list[RankedDocument]:
    selected: list[RankedDocument] = []
    remaining = list(candidates)
    token_cache: dict[str, set[str]] = {}

    def tokens_for(document: SourceDocument) -> set[str]:
        key = document.identity_key()
        cached = token_cache.get(key)
        if cached is None:
            cached = set(tokenize(document.text))
            token_cache[key] = cached
        return cached

    while remaining and len(selected) < top_k:
        def mmr(candidate: RankedDocument) -> float:
            redundancy = max((_token_overlap(tokens_for(candidate.document), tokens_for(other.document)) for other in selected), default=0.0)
            return 0.82 * candidate.score - 0.18 * redundancy
        choice = max(remaining, key=lambda item: (mmr(item), -item.score, item.document.identity_key()))
        selected.append(choice)
        remaining.remove(choice)
    return selected


def _overlap_docs(left: SourceDocument, right: SourceDocument) -> float:
    a = set(tokenize(left.text))
    b = set(tokenize(right.text))
    return _token_overlap(a, b)
