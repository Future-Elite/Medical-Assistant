"""ChinaLLM RAG: agent-facing, evidence-only retrieval primitives.

The package deliberately has no dependency on another RAG implementation. It
can therefore be adopted by an Agent, a PubMed connector, a guideline
database, or a local read-only HIS snapshot independently.

The version below is the single version declaration for this package; runtime
identifiers (trace ``rag_version``, ``/api/health``) read it instead of
hard-coding any version string.
"""

__version__ = "6.8.0"

from .agent import EvidenceSessionStore, EvidenceRAG
from .quality.applicability import assess_applicability
from .agent.audit import ClaimVerifier, GroundedAnswerAuditor
from .quality.claim_verification import (
    CrossLingualMiniCheckVerifier,
    MiniCheckVerifier,
    build_claim_verifier,
)
from .sources.connectors import (
    CallableConnector,
    ConnectorRegistry,
    InMemoryConnector,
    KnowledgeConnector,
    VersionedGuidelineConnector,
)
from .core.config import RAGConfig, get_config
from .sources.custom_sources import (
    CustomSource,
    CustomSourceConnector,
    CustomSourceLibrary,
    SOURCE_MODES,
    custom_source_from_dict,
    load_custom_library,
)
from .sources.guideline_chunks import (
    GuidelineChunkConnector,
    GuidelineChunkLibrary,
    load_guideline_chunk_library,
)
from .sources.guideline_source import GuidelineParsingError, GuidelineParsingSource
from .core.his import HISAdapter, InMemoryHISAdapter, PatientRecord, PatientSnapshot, snapshot_from_dict
from .patient_graph import (
    PatientRecordLexicon,
    PatientGraph,
    RetrievalBudget,
    build_patient_graph,
    load_lexicon,
    retrieve_patient_facts,
    retrieve_patient_graph,
)
from .knowledge import (
    ConditionEvaluation,
    FactSnapshot,
    FactStore,
    FactValue,
    KnowledgeBudgetError,
    KnowledgeError,
    KnowledgeGraph,
    KnowledgeRelease,
    KnowledgeReleaseRegistry,
    KnowledgeService,
    KnowledgeSession,
    PlanningError,
    ReleaseLifecycleError,
    build_maintenance_comparator,
    build_review_plan,
    episode_end_gate,
    evaluate,
    evaluate_condition_ids,
    get_default_service,
    load_knowledge_graph,
    patient_fact_store,
    red_flags,
    retrieve_knowledge,
    select_candidate,
    stable_clinical_assessment,
    validate_actions,
    validate_knowledge_graph,
)
from .retrieval.agentic import AGENTIC_SCHEMA, AgenticBudget, AgenticIterativeRetriever
from .quality.fidelity import (
    FidelityFinding,
    FidelityLexicon,
    FidelityReport,
    FidelitySlot,
    QueryFidelityAuditor,
    RouteAudit,
    TermEntry,
    extract_slots,
)
from .quality.gate import FidelityGate, GateDecision, GATE_POLICIES
from .sources.guideline_lifecycle import (
    GuidelineLifecycleRecord,
    GuidelineLifecycleResolver,
    load_lifecycle_resolver,
)
from .quality.mesh_linking import MeshDescriptor, MeshLexicon, MeshLinker, MeshTerm, load_linker
from .quality.metrics import METRICS
from .core.models import (
    ApplicabilityAssessment,
    ApplicabilityCriterionResult,
    EvidencePackage,
    PatientHit,
    RetrievalRequest,
    SourceDocument,
    TermMappingEntry,
    parse_term_mapping,
)
from .concept_fidelity import (
    ConceptIdentity,
    ConceptIndex,
    ConceptQuery,
    ConceptState,
    ConversionProduct,
    conversion_product_from_dict,
    load_concept_index,
    load_query_set,
    run_phase0,
)
from .retrieval.pipeline import EvidenceRetrievalPipeline
from .retrieval.retrieval_adapters import (
    DeterministicSemanticStub,
    Reranker,
    SemanticRetriever,
    SemanticSearchResult,
)

# Backward-compatible module aliases.  The implementation now lives in
# subpackages; external Agent code may still import the historical paths
# (``rag.models`` / ``rag.pubmed`` / ...).  Aliases are registered here so both
# import styles resolve to the same module object.
import sys as _sys

from .agent import audit as _agent_audit
from .adapters import neural as _adapters_neural
from .core import config as _core_config
from .core import his as _core_his
from .core import models as _core_models
from .retrieval import pipeline as _retrieval_pipeline
from .sources import connectors as _sources_connectors
from .sources import custom_sources as _sources_custom
from .sources import demo as _sources_demo
from .sources import guidelines as _sources_guidelines
from .sources import pubmed as _sources_pubmed

for _alias, _module in {
    "rag.models": _core_models,
    "rag.config": _core_config,
    "rag.his": _core_his,
    "rag.pipeline": _retrieval_pipeline,
    "rag.connectors": _sources_connectors,
    "rag.custom_sources": _sources_custom,
    "rag.pubmed": _sources_pubmed,
    "rag.guidelines": _sources_guidelines,
    "rag.demo": _sources_demo,
    "rag.neural_adapters": _adapters_neural,
    "rag.audit": _agent_audit,
}.items():
    _sys.modules.setdefault(_alias, _module)
    setattr(_sys.modules[__name__], _alias.split(".")[-1], _module)

__all__ = [
    "ApplicabilityAssessment",
    "ApplicabilityCriterionResult",
    "TermMappingEntry",
    "parse_term_mapping",
    "assess_applicability",
    "ConceptIdentity",
    "ConceptIndex",
    "ConceptQuery",
    "ConceptState",
    "ConversionProduct",
    "conversion_product_from_dict",
    "load_concept_index",
    "load_query_set",
    "run_phase0",
    "CustomSource",
    "CustomSourceConnector",
    "CustomSourceLibrary",
    "SOURCE_MODES",
    "custom_source_from_dict",
    "load_custom_library",
    "GuidelineChunkConnector",
    "GuidelineChunkLibrary",
    "load_guideline_chunk_library",
    "GuidelineParsingError",
    "GuidelineParsingSource",
    "CallableConnector",
    "ConnectorRegistry",
    "DeterministicSemanticStub",
    "EvidencePackage",
    "FidelityFinding",
    "FidelityGate",
    "FidelityLexicon",
    "FidelityReport",
    "FidelitySlot",
    "GATE_POLICIES",
    "GateDecision",
    "QueryFidelityAuditor",
    "RouteAudit",
    "TermEntry",
    "extract_slots",
    "AGENTIC_SCHEMA",
    "AgenticBudget",
    "AgenticIterativeRetriever",
    "EvidenceRAG",
    "EvidenceRetrievalPipeline",
    "EvidenceSessionStore",
    "GroundedAnswerAuditor",
    "HISAdapter",
    "InMemoryConnector",
    "InMemoryHISAdapter",
    "KnowledgeConnector",
    "ClaimVerifier",
    "CrossLingualMiniCheckVerifier",
    "MiniCheckVerifier",
    "build_claim_verifier",
    "GuidelineLifecycleRecord",
    "GuidelineLifecycleResolver",
    "load_lifecycle_resolver",
    "MeshDescriptor",
    "MeshLexicon",
    "MeshLinker",
    "MeshTerm",
    "load_linker",
    "METRICS",
    "PatientRecord",
    "PatientRecordLexicon",
    "PatientGraph",
    "PatientHit",
    "PatientSnapshot",
    "RetrievalBudget",
    "build_patient_graph",
    "load_lexicon",
    "retrieve_patient_facts",
    "retrieve_patient_graph",
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
    "RAGConfig",
    "Reranker",
    "RetrievalRequest",
    "SemanticRetriever",
    "SemanticSearchResult",
    "SourceDocument",
    "VersionedGuidelineConnector",
    "get_config",
    "snapshot_from_dict",
]
