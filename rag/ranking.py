"""Fielded BM25, optional semantic scores, auditable RRF ranking, and joint
source-separated selection.

Two layers:

- ``rank_documents`` ranks a single pool (one source kind) and is used for
  per-source ranking: IDF, relevance gate, RRF fusion and MMR are computed
  inside that pool, so guideline vocabulary and PubMed vocabulary never
  contaminate each other's term statistics.
- ``merge_joint_selection`` merges per-source ranked lists into the final
  selection with a visibility guarantee (every relevant source keeps at least
  one slot), an intent-based share for the preferred source, and merit fill by
  within-source normalized score. Raw scores from different sources are not
  comparable, so cross-source merit only compares each candidate against the
  best candidate of its own source.
"""
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
NEURAL_METHODS = ("dense", "xlingual", "m3", "splade", "colbert")

# Intent -> source kind that receives the guaranteed share during joint selection.
INTENT_PREFERRED_KIND = {"guideline_recommendation": "guideline", "evidence_update": "pubmed", "drug_safety": "pubmed"}


def rank_documents(plan: QueryPlan, documents: Iterable[SourceDocument], *, as_of: str | None, top_k: int,
                   methods: Sequence[str] = ("bm25",),
                   semantic_scores: Mapping[str, float] | None = None,
                   neural_scores: Mapping[str, Mapping[str, float]] | None = None,
                   field_weights: Mapping[str, float] | None = None,
                   source_label: str = "") -> tuple[list[RankedDocument], list[str]]:
    """Rank one source pool with one or more retrieval signals + RRF fusion.

    ``methods`` selects lexical scorers (bm25/ql/rm3; computed here) and neural
    signals (dense/xlingual/m3/splade/colbert; supplied via ``neural_scores`` as
    [0,1] values per method). ``semantic_scores`` is kept for backward
    compatibility and is treated as ``neural_scores["dense"]``. With two or more
    active signals the per-signal ranks are fused via RRF (k=60); authority and
    freshness never resurrect a candidate that no signal retrieved (relevance
    gate). ``source_label`` only prefixes warning messages (e.g. "指南：…").
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

    # RRF over per-signal ranks. 只有在该路获得正分的候选参与该路的排名：零分候选
    # 在该路"名次"只是按文档 ID 的任意顺序（如中文问题对英文语料的 BM25 全零），
    # 计入 RRF 等于注入噪声；全零路线整体跳过（实测会把纯 xlingual 结果显著拉低）。
    rrf: dict[str, float] = defaultdict(float)
    primary_rank: dict[str, int] = {}
    if primary:
        scored = [doc for doc, _ in relevant if lexical_score(primary, doc.identity_key()) > 0.0]
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
    prefix = f"{source_label}：" if source_label else ""
    if duplicate_count:
        warnings.append(f"{prefix}已按 DOI/PMID/标题去重 {duplicate_count} 条候选。")
    if conflicts:
        warnings.append(f"{prefix}检测到同来源内共享 DOI/PMID/标题的候选；已保留权威来源，但不能据此消除语义冲突。")
    excluded = len(statuses) - len(eligible)
    if excluded:
        warnings.append(f"{prefix}已排除 {excluded} 条未来、撤回或已被替代的证据。")
    irrelevant = len(eligible) - len(relevant)
    if irrelevant:
        warnings.append(f"{prefix}已排除 {irrelevant} 条与查询无检索相关度（词面/语义均无命中）的候选。")
    semantic_only = [
        item for item in selected
        if item.lexical_score <= 0.0 and item.semantic_score is not None and item.semantic_score > 0.0
    ]
    if semantic_only:
        # 稠密方法对全部候选给正分，可能把"语义相近但病种不同"的条目带回结果
        # （实测：高血压问题带上中文哮喘指南）。这是已知失败模式，不做静默过滤，
        # 显式列出条目并提示人工核对实体一致性。
        titles = "、".join(item.document.title[:40] for item in semantic_only[:3])
        warnings.append(
            f"{prefix}有 {len(semantic_only)} 条入选候选仅由语义方法召回（词面零分）：{titles}；"
            "语义相似不代表同一疾病/药物实体，请核对实体一致性（相似但错误是已知失败模式）。"
        )
    if not selected:
        warnings.append(f"{prefix}没有满足当前时间、版本、状态和查询相关度条件的外部证据。")
    return selected, warnings


def deduplicate_documents(documents: Iterable[SourceDocument]) -> tuple[list[SourceDocument], int, bool]:
    """Global dedup across all sources (DOI > PMID > title|kind).

    Returns (deduplicated documents, duplicate count, cross-source conflict flag).
    The conflict flag is True only when two *different* sources share an
    identity key; within-source duplicates are just counted.
    """
    return _deduplicate(documents)


def merge_joint_selection(
    per_source_ranked: Mapping[str, Sequence[RankedDocument]],
    *,
    top_k: int,
    intent: str,
    source_order: Sequence[str],
) -> tuple[list[RankedDocument], dict, list[str]]:
    """Merge per-source ranked lists into one auditable joint selection.

    Rules (deterministic, recorded in the returned trace):

    1. **Visibility guarantee** — every source with at least one relevant
       candidate keeps a minimum of one slot (while ``top_k`` allows it).
    2. **Intent share** — for ``guideline_recommendation`` the guideline source
       keeps ``ceil(top_k / 2)`` slots when it has candidates; for
       ``evidence_update`` / ``drug_safety`` the PubMed source keeps that share.
    3. **Rank-only round robin** — remaining slots are filled by rotating over
       the sources (preferred source first), each contributing its next
       within-source rank. Raw scores from different sources are never
       compared; only within-source rank order is used.
    4. **Interleaving** — the returned list interleaves the selected lists by
       within-source rank, so the top of the list shows every source instead of
       one source consuming all slots.
    """
    kinds = [kind for kind in source_order if per_source_ranked.get(kind)]
    if not kinds:
        return [], {
            "strategy": "per_source_ranking + visibility_guarantee + merit_fill",
            "participating_sources": [], "intent_preference": None,
            "guaranteed_minimums": {}, "selected_per_source": {},
            "merit_filled_per_source": {}, "rationale": ["没有可参与联合选择的相关候选。"],
        }, ["没有满足当前时间、版本、状态和查询相关度条件的外部证据。"]

    preferred = INTENT_PREFERRED_KIND.get(intent)
    priority = ([preferred] if preferred in kinds else []) + [kind for kind in kinds if kind != preferred]

    # --- Phase 1: guaranteed minimums (visibility + intent share) ----------------
    desired: dict[str, int] = {kind: 1 for kind in priority}
    if preferred in kinds and top_k >= 2:
        desired[preferred] = max(1, math.ceil(top_k / 2))
    guaranteed: dict[str, int] = {}
    remaining = top_k
    for kind in priority:
        want = max(0, min(desired.get(kind, 1), len(per_source_ranked[kind]), remaining))
        guaranteed[kind] = want
        remaining -= want
    warnings: list[str] = []
    for kind in priority:
        if guaranteed.get(kind, 0) == 0:
            warnings.append(f"top_k={top_k} 不足以覆盖全部有相关候选的来源；{_kind_label(kind)}未获得名额，来源可见性无法保证。")
    # --- Phase 2: merit fill by rank-only round robin ----------------------------
    # 只用"来源内排名"补齐：不比较跨来源原始分数（RRF 的哲学是只融合排名）。
    # 轮转从首选来源开始，每轮每个仍有候选的来源各得一个名额（来源内按排名取下一个），
    # 直到 top_k 填满。这样领先来源不会被另一种来源的低分噪声挤掉名额，
    # 也不会因为两种来源的分数尺度不同而让一方垄断剩余名额。
    entries: dict[str, list[int]] = {
        kind: list(range(len(per_source_ranked[kind]))) for kind in priority
    }
    picked: dict[str, list[int]] = {kind: list(range(guaranteed[kind])) for kind in priority}
    cursor = {kind: guaranteed[kind] for kind in priority}
    merit_filled = {kind: 0 for kind in priority}
    remaining = top_k - sum(guaranteed.values())
    while remaining > 0:
        progressed = False
        for kind in priority:
            if remaining <= 0:
                break
            if cursor[kind] < len(entries[kind]):
                picked[kind].append(cursor[kind])
                cursor[kind] += 1
                merit_filled[kind] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break

    # --- Interleave by within-source rank ---------------------------------------
    merged: list[RankedDocument] = []
    ordered_picks = {kind: sorted(picked[kind]) for kind in priority}
    depth = max((len(ordered_picks[kind]) for kind in priority), default=0)
    for slot in range(depth):
        for kind in priority:
            if slot < len(ordered_picks[kind]):
                merged.append(per_source_ranked[kind][ordered_picks[kind][slot]])
    merged = [replace(item, fusion_rank=index) for index, item in enumerate(merged, 1)]

    rationale = ["每个有相关候选的来源至少保留 1 个名额（来源可见性保证）。"]
    if preferred in kinds:
        rationale.append(f"意图 {intent}：{_kind_label(preferred)}至少保留 {guaranteed.get(preferred, 1)} 个名额。")
    rationale.append("其余名额按来源轮转补齐，每轮各来源用其来源内排名交出一名（只比较来源内排名，不比较跨来源分数）。")
    rationale.append("最终列表按来源内排名交错排列，避免单一来源占满 top_k。")
    trace = {
        "strategy": "per_source_ranking + visibility_guarantee + rank_round_robin",
        "participating_sources": list(kinds),
        "intent_preference": preferred if preferred in kinds else None,
        "source_priority": list(priority),
        "guaranteed_minimums": {kind: guaranteed.get(kind, 0) for kind in priority},
        "selected_per_source": {kind: len(ordered_picks[kind]) for kind in priority},
        "merit_filled_per_source": {kind: merit_filled.get(kind, 0) for kind in priority},
        "rationale": rationale,
    }
    return merged, trace, warnings


def _kind_label(kind: str) -> str:
    return {"guideline": "指南", "pubmed": "PubMed", "drug": "药品", "clinical_trial": "临床试验", "local_knowledge": "本地知识"}.get(kind, kind)


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
