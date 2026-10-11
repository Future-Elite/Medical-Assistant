"""ChinaLLM RAG: agent-facing, evidence-only retrieval primitives.

The package deliberately has no dependency on another RAG implementation. It
can therefore be adopted by an Agent, a PubMed connector, a guideline
database, or a local read-only HIS snapshot independently.

The version below is the single version declaration for this package; runtime
identifiers (trace ``rag_version``, ``/api/health``) read it instead of
hard-coding any version string.
"""

__version__ = "6.6.2"

from .agent import EvidenceSessionStore, EvidenceRAG
from .audit import GroundedAnswerAuditor, MiniCheckVerifier
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
from .his import HISAdapter, InMemoryHISAdapter, PatientFact, PatientSnapshot, snapshot_from_dict
from .fidelity import (
    FidelityFinding,
    FidelityLexicon,
    FidelityReport,
    FidelitySlot,
    QueryFidelityAuditor,
    RouteAudit,
    TermEntry,
    extract_slots,
)
from .gate import FidelityGate, GateDecision, GATE_POLICIES
from .models import (
    ApplicabilityAssessment,
    EvidencePackage,
    PatientHit,
    RetrievalRequest,
    SourceDocument,
)
from .pipeline import EvidenceRetrievalPipeline
from .retrieval_adapters import (
    DeterministicSemanticStub,
    Reranker,
    SemanticRetriever,
    SemanticSearchResult,
)

__all__ = [
    "ApplicabilityAssessment",
    "CustomSource",
    "CustomSourceConnector",
    "CustomSourceLibrary",
    "SOURCE_MODES",
    "custom_source_from_dict",
    "load_custom_library",
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
    "MiniCheckVerifier",
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
