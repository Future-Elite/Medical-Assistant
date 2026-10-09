"""Read-only source connector contracts for PubMed and versioned guidelines."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence

from .models import QueryPlan, SearchResult, SourceDocument


class KnowledgeConnector(Protocol):
    source_id: str
    kinds: tuple[str, ...]

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        """Return source-owned documents; connectors never fabricate provenance."""


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
        return [{"source_id": key, "kinds": list(value.kinds)} for key, value in sorted(self._connectors.items())]


@dataclass
class InMemoryConnector:
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


@dataclass
class VersionedGuidelineConnector:
    """Offline or authorized read-only guideline source with explicit metadata checks."""
    source_id: str
    documents: tuple[SourceDocument, ...]
    kinds: tuple[str, ...] = ("guideline",)

    def __post_init__(self) -> None:
        if "guideline" not in self.kinds:
            self.kinds = tuple((*self.kinds, "guideline"))
        for document in self.documents:
            if document.kind != "guideline":
                raise ValueError(f"guideline connector received non-guideline document: {document.document_id}")

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        allowed = set(plan.filters.get("source_kinds") or ())
        if allowed and not (allowed & set(self.kinds)):
            return SearchResult(source_id=self.source_id, documents=())
        warnings: list[str] = []
        selected = list(self.documents)
        requested_version = plan.filters.get("guideline_version")
        if requested_version:
            selected = [doc for doc in selected if doc.version_id == str(requested_version)]
            if not selected:
                warnings.append(f"未找到请求的指南版本：{requested_version}。")
        missing = [doc.document_id for doc in selected if not doc.version_id or not doc.locator]
        if missing:
            warnings.append(f"指南条目缺少 version_id 或 locator，需人工核验：{', '.join(missing[:5])}。")
        return SearchResult(source_id=self.source_id, documents=tuple(selected[:limit]), warnings=tuple(warnings))
