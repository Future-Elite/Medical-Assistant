"""Single construction point for the RAG connectors used by every caller."""

import os

from .agent import EvidenceRAG
from .connectors import ConnectorRegistry
from .pipeline import EvidenceRetrievalPipeline
from .pubmed import PubMedConnector


def create_evidence_rag() -> EvidenceRAG:
    registry = ConnectorRegistry()
    registry.register(PubMedConnector(
        email=os.getenv("NCBI_EMAIL") or None,
        tool_name=os.getenv("NCBI_TOOL", "medical_agent_rag"),
    ))
    return EvidenceRAG(EvidenceRetrievalPipeline(registry))
