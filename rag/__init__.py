"""ChinaLLM RAG: agent-facing, evidence-only retrieval primitives.

The package deliberately has no dependency on another RAG implementation. It
can therefore be adopted by an Agent, a PubMed connector, a guideline
database, or a local read-only HIS snapshot independently.

The version below is the single version declaration for this package; runtime
identifiers (trace ``rag_version``, ``/api/health``) read it instead of
hard-coding any version string.
"""

__version__ = "6.7.1"

from .agent import EvidenceSessionStore, EvidenceRAG
from .quality.applicability import assess_applicability
from .audit import ClaimVerifier, GroundedAnswerAuditor
from .quality.claim_verification import (
    CrossLingualMiniCheckVerifier,
    MiniCheckVerifier,
    build_claim_verifier,
)
from .connectors import (
    CallableConnector,
    ConnectorRegistry,
    InMemoryConnector,
    KnowledgeConnector,
    VersionedGuidelineConnector,
)
from .config import RAGConfig, get_config
from .custom_sources import (
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
from .his import HISAdapter, InMemoryHISAdapter, PatientFact, PatientSnapshot, snapshot_from_dict
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
from .models import (
    ApplicabilityAssessment,
    ApplicabilityCriterionResult,
    EvidencePackage,
    PatientHit,
    RetrievalRequest,
    SourceDocument,
    TermMappingEntry,
    parse_term_mapping,
)
from .pipeline import EvidenceRetrievalPipeline
from .retrieval.retrieval_adapters import (
    DeterministicSemanticStub,
    Reranker,
    SemanticRetriever,
    SemanticSearchResult,
)

__all__ = [
    "ApplicabilityAssessment",
    "ApplicabilityCriterionResult",
    "TermMappingEntry",
    "parse_term_mapping",
    "assess_applicability",
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
    "PatientFact",
    "PatientHit",
    "PatientSnapshot",
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
