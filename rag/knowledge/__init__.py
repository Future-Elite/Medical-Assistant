"""EHR-style guideline knowledge graph and Graph-RAG service (v6.7.3).

This is the knowledge-side layer requested by the EHR knowledge-graph requirement:
sources/evidence spans, recommendations, populations, four-state conditions,
bounded graph retrieval, action validation and a fixed release manifest.  It
is bundled with a **synthetic fixture** only; no real guideline text or
clinical approval is included.
"""
from .conditions import (
    ConditionEvaluation,
    FactSnapshot,
    FactStore,
    FactValue,
    evaluate,
    evaluate_condition_ids,
)
from .model import (
    KnowledgeError,
    KnowledgeGraph,
    KnowledgeRelease,
    load_knowledge_graph,
)
from .patient_facts import patient_fact_store
from .guideline_graph import (
    GRAPH_SCHEMA,
    build_guideline_graph,
    build_guideline_library,
    guideline_graph_summary,
    load_chunks,
    load_guideline_graph_library,
    load_manifest,
    write_json,
)
from .quality import DEFAULT_CQ_PATH, render_markdown, validate_knowledge_graph
from .applicability_bridge import attach_fusion_to_applicability
from .local_guideline_fusion import fuse_local_guidelines, select_local_guidelines

# Explicit compatibility map for a future intervention:* naming migration.
NODE_ID_MIGRATION = {
    "action:cpap": "intervention:cpap",
    "action:nippv": "intervention:nippv",
    "action:invasive_ventilation": "intervention:invasive_ventilation",
    "action:surfactant": "intervention:surfactant",
    "action:antimicrobial": "intervention:antimicrobial",
    "action:oxygen": "intervention:oxygen",
}
from .planning import (
    PlanningError,
    build_maintenance_comparator,
    build_review_plan,
    episode_end_gate,
    red_flags,
    select_candidate,
    stable_clinical_assessment,
)
from .release import KnowledgeReleaseRegistry, ReleaseLifecycleError
from .retrieval import retrieve_knowledge
from .review import (
    ALL_STATES,
    REVIEW_STATES,
    TERMINAL_STATES,
    ReviewRecord,
    ReviewStateError,
    can_transition,
    publication_ready,
    validate_review_history,
)
from .service import KnowledgeService, get_default_service
from .session import KnowledgeBudgetError, KnowledgeSession
from .validation import validate_actions

__all__ = [
    "ConditionEvaluation",
    "FactSnapshot",
    "FactStore",
    "FactValue",
    "KnowledgeBudgetError",
    "KnowledgeError",
    "KnowledgeGraph",
    "KnowledgeRelease",
    "KnowledgeReleaseRegistry",
    "KnowledgeService",
    "KnowledgeSession",
    "PlanningError",
    "ReleaseLifecycleError",
    "GRAPH_SCHEMA",
    "attach_fusion_to_applicability",
    "build_guideline_graph",
    "build_guideline_library",
    "fuse_local_guidelines",
    "select_local_guidelines",
    "guideline_graph_summary",
    "load_chunks",
    "load_guideline_graph_library",
    "load_manifest",
    "write_json",
    "build_maintenance_comparator",
    "build_review_plan",
    "episode_end_gate",
    "evaluate",
    "evaluate_condition_ids",
    "get_default_service",
    "load_knowledge_graph",
    "patient_fact_store",
    "red_flags",
    "retrieve_knowledge",
    "select_candidate",
    "stable_clinical_assessment",
    "validate_actions",
    "validate_knowledge_graph",
    "render_markdown",
    "DEFAULT_CQ_PATH",
    "NODE_ID_MIGRATION",
    "ALL_STATES",
    "REVIEW_STATES",
    "TERMINAL_STATES",
    "ReviewRecord",
    "ReviewStateError",
    "can_transition",
    "publication_ready",
    "validate_review_history",
]
