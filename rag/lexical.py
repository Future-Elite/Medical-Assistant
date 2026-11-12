"""Dependency-free lexical retrieval methods for RAG retrieval experiments.

Implements, over the same fielded tokenization as ``bm25.py``:

- **QL** (Query Likelihood with Dirichlet smoothing, ``mu=2000``): every query
  term must appear in the document; scores are language-model probabilities.
- **RM3** (Relevance Model 3): pseudo-relevance feedback that runs BM25 over
  the candidate set, expands the query with the top feedback documents'
  terms (interpolated with the original query distribution), and rescores.

Both are deterministic, dependency-free, and only operate on the candidate
documents supplied by connectors. They produce the same ``BM25Score`` value
object as ``bm25.score_documents`` so ``ranking.py`` can treat them uniformly.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import replace
from typing import Iterable, Mapping

from .bm25 import BM25Score, score_documents, tokenize
from .models import QueryPlan, SourceDocument

QL_MU = 2000.0
RM3_FEEDBACK_DOCS = 5
RM3_FEEDBACK_TERMS = 10
RM3_ORIGINAL_WEIGHT = 0.5


def _field_documents(documents: Iterable[SourceDocument]) -> dict[str, dict[str, tuple[str, ...]]]:
    return {
        doc.identity_key(): {field: tokenize(value) for field, value in doc.field_values().items()}
        for doc in documents
    }


def _collection_stats(
    token_fields: Mapping[str, Mapping[str, tuple[str, ...]]],
    weights: Mapping[str, float],
) -> tuple[dict[str, int], dict[str, int], float]:
    """Return (collection term counts per weighted field, field lengths, total length)."""
    collection: dict[str, int] = {}
    lengths: dict[str, int] = {}
    total = 0
    for fields in token_fields.values():
        for field, weight in weights.items():
            values = fields.get(field, ())
            if not values:
                continue
            for token in values:
                collection[token] = collection.get(token, 0) + 1
            lengths[field] = lengths.get(field, 0) + len(values)
            total += len(values)
    return collection, lengths, max(1, total)


def score_documents_ql(
    plan: QueryPlan,
    documents: Iterable[SourceDocument],
    *,
    field_weights: Mapping[str, float] | None = None,
    mu: float = QL_MU,
) -> dict[str, BM25Score]:
    """Fielded query likelihood with Dirichlet smoothing.

    Score = sum over query terms of weight * log(1 + tf / (mu * P(t|C))).
    Terms absent from a document contribute 0 (we do not subtract log(0)),
    so the ordering is driven by the terms a document does contain, matching
    the additive-style scoring used by the other methods.
    """
    docs = tuple(documents)
    weights = dict(field_weights or {"title": 2.5, "mesh": 2.0, "keywords": 1.5, "text": 1.0, "population": 1.0})
    token_fields = _field_documents(docs)
    collection, _lengths, total = _collection_stats(token_fields, weights)
    query_terms = tuple(dict.fromkeys(token for variant in plan.variants for token in tokenize(variant)))
    output: dict[str, BM25Score] = {}
    for doc in docs:
        key = doc.identity_key()
        best = None
        for variant in plan.variants:
            terms = tuple(dict.fromkeys(tokenize(variant)))
            total_score = 0.0
            matched: set[str] = set()
            fields_hit: set[str] = set()
            for field, weight in weights.items():
                values = token_fields[key].get(field, ())
                if not values:
                    continue
                length = len(values)
                counts = Counter(values)
                for token in terms:
                    tf = counts.get(token, 0)
                    if not tf:
                        continue
                    matched.add(token)
                    fields_hit.add(field)
                    p_collection = collection.get(token, 0) / total
                    if p_collection <= 0:
                        continue
                    total_score += weight * math.log(1.0 + tf / (mu * p_collection))
            candidate = BM25Score(total_score, tuple(sorted(matched)), tuple(sorted(fields_hit)), variant)
            if best is None or candidate.score > best.score:
                best = candidate
        output[key] = best or BM25Score(0.0, (), (), "")
    return output


def _feedback_terms(
    plan: QueryPlan,
    documents: tuple[SourceDocument, ...],
    lexical: Mapping[str, BM25Score],
    *,
    n_docs: int = RM3_FEEDBACK_DOCS,
    n_terms: int = RM3_FEEDBACK_TERMS,
    original_weight: float = RM3_ORIGINAL_WEIGHT,
) -> dict[str, float]:
    """RM1-style relevance model over top pseudo-relevant documents, interpolated
    with the original query distribution (RM3)."""
    query_tokens = Counter(token for variant in plan.variants for token in tokenize(variant))
    query_total = max(1, sum(query_tokens.values()))
    original = {token: count / query_total for token, count in query_tokens.items()}
    ranked = sorted(documents, key=lambda doc: (-lexical[doc.identity_key()].score, doc.identity_key()))
    feedback_docs = [doc for doc in ranked if lexical[doc.identity_key()].score > 0][:n_docs]
    if not feedback_docs:
        return original
    model: Counter[str] = Counter()
    doc_weight = 1.0 / len(feedback_docs)
    for doc in feedback_docs:
        tokens = Counter()
        for field, value in doc.field_values().items():
            field_weight = {"title": 2.5, "mesh": 2.0, "keywords": 1.5}.get(field, 1.0)
            tokens.update({token: field_weight for token in tokenize(value)})
        doc_total = max(1, sum(tokens.values()))
        for token, count in tokens.items():
            model[token] += doc_weight * count / doc_total
    top_feedback = [token for token, _ in model.most_common(n_terms)]
    mixed: dict[str, float] = {token: original_weight * weight for token, weight in original.items()}
    feedback_total = max(1e-12, sum(model.values()))
    for token in top_feedback:
        mixed[token] = mixed.get(token, 0.0) + (1.0 - original_weight) * model[token] / feedback_total
    return mixed


def score_documents_rm3(
    plan: QueryPlan,
    documents: Iterable[SourceDocument],
    *,
    field_weights: Mapping[str, float] | None = None,
    n_docs: int = RM3_FEEDBACK_DOCS,
    n_terms: int = RM3_FEEDBACK_TERMS,
) -> dict[str, BM25Score]:
    """BM25 ranking with one round of RM3 pseudo-relevance feedback.

    The expansion terms are appended to the first query variant so BM25's
    per-variant maximum still applies; the matched_terms/matched_fields
    fields reflect the expanded variant.
    """
    docs = tuple(documents)
    weights = dict(field_weights or {"title": 2.5, "mesh": 2.0, "keywords": 1.5, "text": 1.0, "population": 1.0})
    first_pass = score_documents(plan, docs, field_weights=weights)
    expanded = _feedback_terms(plan, docs, first_pass, n_docs=n_docs, n_terms=n_terms)
    expansion_tokens = tuple(token for token in expanded if token not in tokenize(" ".join(plan.variants)))
    if not expansion_tokens:
        return first_pass
    expanded_variant = " ".join((*plan.variants, *expansion_tokens))
    expanded_plan = replace(
        plan,
        variants=(expanded_variant, *plan.variants),
        expansion_trace=(*plan.expansion_trace, {"concept": "rm3", "terms": list(expansion_tokens), "source": "pseudo_relevance_feedback"}),
    )
    return score_documents(expanded_plan, docs, field_weights=weights)
