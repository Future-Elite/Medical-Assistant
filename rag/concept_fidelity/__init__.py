"""Concept-fidelity Phase 0: schemas, MeSH probes and offline diagnostics.

This package implements the evaluation scaffold for the wave10
"model-level cross-lingual concept fidelity" route.  It does **not** train a
model, call a network, use patient data, or make clinical claims.  See
``README.md`` for the frozen pilot data and metrics.
"""
from __future__ import annotations

from .harness import (
    BM25Retriever,
    CorpusDocument,
    DenseRetriever,
    FrozenCorpus,
    RetrievalHit,
    evaluate_query_set,
    run_phase0,
)
from .mesh import ConceptIndex, load_concept_index
from .metrics import aggregate_metrics, concept_identity_summary, ranking_metrics
from .probes import SiblingProbe, build_sibling_probes
from .queries import ConceptQuery, load_query_set
from .schema import (
    CONCEPT_STATUSES,
    CONVERSION_ROUTES,
    DRIFT_IDENTITY_LABELS,
    IDENTITY_LABELS,
    POSITIVE_IDENTITY_LABELS,
    ConceptIdentity,
    ConceptState,
    ConversionProduct,
    concept_identity_from_dict,
    concept_state_from_dict,
    conversion_product_from_dict,
)

__all__ = [
    "BM25Retriever",
    "CONCEPT_STATUSES",
    "CONVERSION_ROUTES",
    "ConceptIdentity",
    "ConceptIndex",
    "ConceptQuery",
    "ConceptState",
    "ConversionProduct",
    "CorpusDocument",
    "DRIFT_IDENTITY_LABELS",
    "DenseRetriever",
    "FrozenCorpus",
    "IDENTITY_LABELS",
    "POSITIVE_IDENTITY_LABELS",
    "RetrievalHit",
    "SiblingProbe",
    "aggregate_metrics",
    "build_sibling_probes",
    "concept_identity_from_dict",
    "concept_identity_summary",
    "concept_state_from_dict",
    "conversion_product_from_dict",
    "evaluate_query_set",
    "load_concept_index",
    "load_query_set",
    "ranking_metrics",
    "run_phase0",
]
