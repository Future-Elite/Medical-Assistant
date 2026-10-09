"""ChinaLLM RAG: agent-facing, evidence-only retrieval primitives.

The package deliberately has no dependency on another RAG implementation. It
can therefore be adopted by an Agent, a PubMed connector, a guideline
database, or a local read-only HIS snapshot independently.

The version below is the single version declaration for this package; runtime
identifiers (trace ``rag_version``, ``/api/health``) read it instead of
hard-coding any version string.
"""

__version__ = "6.4.0"

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
from .his import HISAdapter, InMemoryHISAdapter, PatientFact, PatientSnapshot, snapshot_from_dict
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
    "CallableConnector",
    "ConnectorRegistry",
    "DeterministicSemanticStub",
    "EvidencePackage",
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
