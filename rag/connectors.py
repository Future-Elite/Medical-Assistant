"""Connector contracts and adapters for PubMed, guidelines, and future sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence

from .models import QueryPlan, SearchResult, SourceDocument


class KnowledgeConnector(Protocol):
    """Minimal integration contract; network transport belongs to the adapter."""

    source_id: str
    kinds: tuple[str, ...]

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        """Return only source-owned documents; never fabricate a source ID."""


@dataclass
class ConnectorRegistry:
    _connectors: dict[str, KnowledgeConnector]

    def __init__(self) -> None:
        self._connectors = {}

    def register(self, connector: KnowledgeConnector) -> None:
        source_id = str(connector.source_id).strip()
        if not source_id:
            raise ValueError("connector source_id must be non-empty")
        if source_id in self._connectors:
            raise ValueError(f"duplicate connector: {source_id}")
        self._connectors[source_id] = connector

    def selected(self, source_order: Sequence[str]) -> list[KnowledgeConnector]:
        ordered: list[KnowledgeConnector] = []
        seen: set[str] = set()
        for source_id in source_order:
            connector = self._connectors.get(source_id)
            if connector is not None and source_id not in seen:
                ordered.append(connector)
                seen.add(source_id)
        return ordered

    def describe(self) -> list[dict[str, object]]:
        return [
            {"source_id": key, "kinds": list(value.kinds)}
            for key, value in sorted(self._connectors.items())
        ]


@dataclass
class InMemoryConnector:
    """Deterministic connector for demos and offline test fixtures only."""

    source_id: str
    kinds: tuple[str, ...]
    documents: tuple[SourceDocument, ...]

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        allowed = set(plan.filters.get("source_kinds") or ())
        docs = tuple(
            doc for doc in self.documents
            if (not allowed or doc.kind in allowed) and (not self.kinds or doc.kind in self.kinds)
        )
        return SearchResult(source_id=self.source_id, documents=docs[:limit])


@dataclass
class CallableConnector:
    """Adapts an existing PubMed/guideline client without imposing its SDK."""

    source_id: str
    kinds: tuple[str, ...]
    fetch: Callable[[QueryPlan, int], Sequence[Mapping[str, object] | SourceDocument]]

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        docs: list[SourceDocument] = []
        for item in self.fetch(plan, limit):
            doc = item if isinstance(item, SourceDocument) else SourceDocument.from_dict(item, source_id=self.source_id)
            if doc.source_id != self.source_id:
                raise ValueError(f"connector {self.source_id} returned document owned by {doc.source_id}")
            docs.append(doc)
        return SearchResult(source_id=self.source_id, documents=tuple(docs))
