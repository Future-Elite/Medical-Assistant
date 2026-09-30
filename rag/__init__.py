"""ChinaLLM RAG: agent-facing, evidence-only retrieval primitives.

The package deliberately has no dependency on another RAG implementation.  It
can therefore be adopted by an Agent, a PubMed connector, or a guideline
database independently.
"""

from .agent import EvidenceSessionStore, EvidenceRAG
from .audit import GroundedAnswerAuditor, MiniCheckVerifier
from .connectors import ConnectorRegistry, InMemoryConnector, KnowledgeConnector
from .config import RAGConfig, get_config
from .models import EvidencePackage, RetrievalRequest, SourceDocument

__all__ = [
    "ConnectorRegistry",
    "RAGConfig",
    "EvidencePackage",
    "EvidenceSessionStore",
    "GroundedAnswerAuditor",
    "InMemoryConnector",
    "KnowledgeConnector",
    "MiniCheckVerifier",
    "RetrievalRequest",
    "SourceDocument",
    "EvidenceRAG",
    "get_config",
]

