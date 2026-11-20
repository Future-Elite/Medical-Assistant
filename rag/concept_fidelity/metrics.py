"""Concept-bound retrieval and conversion metrics.

All metrics are reported per query and then aggregated; empty denominators are
``None`` (not measured), never zero.  Relevance is concept-bound: a document is
entity-correct only when it is tagged with the query's target MeSH descriptor.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .schema import (
    DRIFT_IDENTITY_LABELS,
    POSITIVE_IDENTITY_LABELS,
    ConceptIdentity,
)


def _best_rank(ranked_ids: Sequence[str], concepts_by_doc: Mapping[str, frozenset[str]], wanted: frozenset[str]) -> int | None:
    for rank, doc_id in enumerate(ranked_ids):
        if concepts_by_doc.get(doc_id, frozenset()) & wanted:
            return rank
    return None


def _ndcg(ranked_ids: Sequence[str], concepts_by_doc: Mapping[str, frozenset[str]], target: frozenset[str], total_target_docs: int, k: int) -> float | None:
    if total_target_docs <= 0 or k <= 0:
        return None
    gains = [
        1.0 if concepts_by_doc.get(doc_id, frozenset()) & target else 0.0
        for doc_id in ranked_ids[:k]
    ]
    dcg = sum(gain / math.log2(rank + 2) for rank, gain in enumerate(gains))
    ideal = sum(1.0 / math.log2(rank + 2) for rank in range(min(total_target_docs, k)))
    return dcg / ideal if ideal else None


def ranking_metrics(
    ranked_ids: Sequence[str],
    concepts_by_doc: Mapping[str, frozenset[str]],
    *,
    target_ids: Sequence[str],
    confusable_ids: Sequence[str],
    total_target_docs: int,
    k: int = 10,
) -> dict[str, Any]:
    target = frozenset(target_ids)
    confusable = frozenset(confusable_ids)
    top = list(ranked_ids[:k])
    target_hits = [doc_id for doc_id in top if concepts_by_doc.get(doc_id, frozenset()) & target]
    contaminated = [doc_id for doc_id in top if concepts_by_doc.get(doc_id, frozenset()) & confusable]
    target_rank = _best_rank(top, concepts_by_doc, target)
    confusable_rank = _best_rank(top, concepts_by_doc, confusable)
    if target_rank is None:
        discrimination = 0.0 if confusable_rank is not None else None
    else:
        discrimination = 1.0 if confusable_rank is None or target_rank < confusable_rank else 0.0
    recall = len(target_hits) / total_target_docs if total_target_docs > 0 else None
    contamination = len(contaminated) / k if k > 0 else None
    mrr = 1.0 / (target_rank + 1) if target_rank is not None else 0.0
    return {
        "entity_correct_recall": recall,
        "wrong_entity_contamination": contamination,
        "sibling_discrimination": discrimination,
        "ndcg": _ndcg(top, concepts_by_doc, target, total_target_docs, k),
        "mrr": mrr,
        "target_hit_count": len(target_hits),
        "confusable_hit_count": len(contaminated),
        "ranked": top,
    }


def aggregate_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    keys = (
        "entity_correct_recall",
        "wrong_entity_contamination",
        "sibling_discrimination",
        "ndcg",
        "mrr",
    )
    summary: dict[str, Any] = {"query_count": len(rows)}
    for key in keys:
        values = [row[key] for row in rows if row.get(key) is not None]
        summary[key] = sum(values) / len(values) if values else None
        summary[f"{key}_measured"] = len(values)
    return summary


def concept_identity_summary(identities: Sequence[ConceptIdentity]) -> dict[str, Any]:
    labels = [identity.label for identity in identities]
    total = len(labels)
    counts = {label: labels.count(label) for label in sorted(set(labels))}
    positive = sum(1 for label in labels if label in POSITIVE_IDENTITY_LABELS)
    drift = sum(1 for label in labels if label in DRIFT_IDENTITY_LABELS)
    not_checked = labels.count("not_checked")
    return {
        "slot_count": total,
        "label_counts": counts,
        "concept_identity_accuracy": positive / total if total else None,
        "drift_rate": drift / total if total else None,
        "not_checked_rate": not_checked / total if total else None,
    }
