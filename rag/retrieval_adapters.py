"""Optional semantic retrieval and reranking protocols for the RAG package.

The core package deliberately does not import torch, transformers, vector stores,
or model SDKs. Adapters can be implemented by a real MedCPT or cross-encoder
integration while keeping model availability explicit in the trace.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from .models import QueryPlan, RankedDocument, SourceDocument


@dataclass(frozen=True)
class SemanticSearchResult:
    scores: Mapping[str, float]
    retriever_id: str
    retriever_version: str | None = None
    index_snapshot: str | None = None


class SemanticRetriever(Protocol):
    retriever_id: str

    def search(self, plan: QueryPlan, documents: Sequence[SourceDocument], *, limit: int) -> SemanticSearchResult:
        """Return normalized [0, 1] scores keyed by SourceDocument.identity_key()."""


class Reranker(Protocol):
    reranker_id: str

    def rank(self, plan: QueryPlan, candidates: Sequence[RankedDocument]) -> Sequence[RankedDocument]:
        """Return candidates in reranked order without changing provenance/status."""


@dataclass(frozen=True)
class DeterministicSemanticStub:
    """Test-only adapter; it is not a medical embedding model."""
    scores: Mapping[str, float]
    retriever_id: str = "deterministic-semantic-stub"
    retriever_version: str = "test"
    index_snapshot: str = "synthetic"

    def search(self, plan: QueryPlan, documents: Sequence[SourceDocument], *, limit: int) -> SemanticSearchResult:
        selected = {doc.identity_key(): float(self.scores[doc.identity_key()]) for doc in documents if doc.identity_key() in self.scores}
        return SemanticSearchResult(selected, self.retriever_id, self.retriever_version, self.index_snapshot)
