"""Patient knowledge graph and graph retrieval (v6.7.3).

The package builds a per-request graph from the visible HIS snapshot and
retrieves patient facts by intent/concept/record provenance instead of a flat
bag-of-words scan.  It never connects to HIS; the caller passes a
:class:`~rag.his.PatientSnapshot` that already passed its own authorization and
``as_of`` filter.
"""
from .builder import build_patient_graph, fact_evidence_text
from .client import PatientGraphClient, PatientGraphClientError
from .lexicon import (
    ConceptSpec,
    IntentSpec,
    LexiconError,
    PatientRecordLexicon,
    QueryResolution,
    default_lexicon,
    load_lexicon,
)
from .model import PatientGraph, GraphEdge, GraphNode
from .retrieval import GraphHit, RetrievalBudget, retrieve_patient_graph
from .service import (
    PATIENT_GRAPH_ENGINE,
    PatientGraphSettings,
    build_patient_graph_for_snapshot,
    hit_to_dict,
    retrieve_patient_facts,
    search_patient_graph,
)

__all__ = [
    "ConceptSpec",
    "GraphEdge",
    "GraphHit",
    "GraphNode",
    "IntentSpec",
    "LexiconError",
    "PATIENT_GRAPH_ENGINE",
    "PatientRecordLexicon",
    "PatientGraph",
    "PatientGraphClient",
    "PatientGraphClientError",
    "PatientGraphSettings",
    "QueryResolution",
    "RetrievalBudget",
    "build_patient_graph",
    "build_patient_graph_for_snapshot",
    "default_lexicon",
    "fact_evidence_text",
    "hit_to_dict",
    "load_lexicon",
    "retrieve_patient_facts",
    "retrieve_patient_graph",
    "search_patient_graph",
]
