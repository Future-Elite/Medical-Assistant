"""ChinaLLM RAG v5: agent-facing, evidence-only retrieval primitives.

The package deliberately has no dependency on the frozen ``rag`` package.  It
can therefore be adopted by an Agent, a PubMed connector, or a guideline
database independently.
"""

from .agent import EvidenceSessionStore, V5RAG
from .audit import GroundedAnswerAuditor, MiniCheckVerifier
from .connectors import ConnectorRegistry, InMemoryConnector, KnowledgeConnector
from .config import V5Config, get_config
from .models import EvidencePackage, RetrievalRequest, SourceDocument

__all__ = [
    "ConnectorRegistry",
    "V5Config",
    "EvidencePackage",
    "EvidenceSessionStore",
    "GroundedAnswerAuditor",
    "InMemoryConnector",
    "KnowledgeConnector",
    "MiniCheckVerifier",
    "RetrievalRequest",
    "SourceDocument",
    "V5RAG",
    "get_config",
]

