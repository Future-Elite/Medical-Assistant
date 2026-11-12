"""Custom (caller-provided) knowledge sources: the defined interface.

The retrieval layer supports exactly two source modes:

- ``live``   — real-time PubMed (NCBI E-utilities) + guideline source
               (neonatal guideline metadata registry by default);
- ``custom`` — a knowledge library supplied by the caller (for example a
               hospital's own guideline database or a frozen literature dump).

This module defines the ``custom`` interface end to end: the payload schema,
the validating loader, the in-memory library, and the connector used to serve
it. Sources registered here are held in memory only; nothing is written to
disk and a restart clears them. Registration replaces a source of the same
``source_id`` (explicit replace semantics, no silent merge).

Payload schema (JSON)::

    {
      "source_id": "hospital-guidelines-2026",     # required, unique per library
      "kinds": ["guideline"],                      # optional; inferred from documents
      "description": "...",                        # optional, for audit display
      "version": "2026-09",                        # optional caller snapshot label
      "documents": [ <SourceDocument>, ... ]       # required, non-empty
    }

Each ``SourceDocument`` follows ``contracts/agent_contract.json``
(``schemas.SourceDocument``): ``document_id`` / ``kind`` / ``title`` / ``text``
are required; ``pmid`` / ``doi`` / ``published_at`` / ``version_id`` /
``recommendation_id`` / ``population`` / ``status`` / ``locator`` and other
unknown keys are preserved (unknown keys go into ``metadata``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from .connectors import ConnectorRegistry
from .models import QueryPlan, SearchResult, SourceDocument

SOURCE_MODES = ("live", "custom")


@dataclass(frozen=True)
class CustomSource:
    """One caller-provided source: a named library of ``SourceDocument`` records."""
    source_id: str
    kinds: tuple[str, ...]
    documents: tuple[SourceDocument, ...]
    description: str = ""
    version: str | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "kinds": list(self.kinds),
            "document_count": len(self.documents),
            "description": self.description,
            "version": self.version,
        }


def custom_source_from_dict(raw: Mapping[str, Any]) -> CustomSource:
    """Validate one custom-source payload; raise ValueError with the exact field."""
    if not isinstance(raw, Mapping):
        raise ValueError("custom source payload must be a JSON object")
    source_id = str(raw.get("source_id") or "").strip()
    if not source_id:
        raise ValueError("custom source requires a non-empty source_id")
    documents_raw = raw.get("documents")
    if not isinstance(documents_raw, Sequence) or isinstance(documents_raw, (str, bytes)):
        raise ValueError("custom source requires a 'documents' array")
    if not documents_raw:
        raise ValueError("custom source 'documents' must be non-empty")
    documents: list[SourceDocument] = []
    for index, item in enumerate(documents_raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"documents[{index}] must be a JSON object")
        # 文档可省略 source_id（继承来源库的 id）；若显式声明则必须与来源一致，
        # 否则是调用方笔误，显式报错而不是悄悄改写归属。
        declared_owner = str(item.get("source_id") or "").strip()
        if declared_owner and declared_owner != source_id:
            raise ValueError(
                f"documents[{index}].source_id is '{declared_owner}' but the source is '{source_id}'; "
                "omit source_id to inherit it"
            )
        # SourceDocument.from_dict validates required fields (document_id/kind/title/text)
        # and preserves unknown keys into metadata; ownership is pinned to this source_id.
        document = SourceDocument.from_dict(item, source_id=source_id)
        documents.append(document)
    document_kinds = tuple(sorted({document.kind for document in documents}))
    declared = tuple(str(kind).strip() for kind in (raw.get("kinds") or ()) if str(kind).strip())
    kinds = declared or document_kinds
    unknown = sorted(set(document_kinds) - set(kinds))
    if unknown:
        raise ValueError("declared kinds do not cover document kinds: " + ", ".join(unknown))
    return CustomSource(
        source_id=source_id,
        kinds=kinds,
        documents=tuple(documents),
        description=str(raw.get("description") or ""),
        version=str(raw.get("version")).strip() or None if raw.get("version") is not None else None,
    )


@dataclass
class CustomSourceLibrary:
    """In-memory registry of caller-provided sources (no disk persistence).

    ``register`` replaces an existing source with the same ``source_id`` after
    recording the previous document count in the audit event, so callers can
    update their library without restarting the process. ``clear`` removes all
    custom sources.
    """
    _sources: dict[str, CustomSource] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def register(self, source: CustomSource) -> dict[str, Any]:
        # 文档归属固定为注册时的 source_id：调用方声明"这些文档属于来源 X"，
        # 与 CallableConnector 的归属校验一致（连接器只返回自己拥有的文档）。
        normalized = CustomSource(
            source_id=source.source_id,
            kinds=source.kinds,
            documents=tuple(
                replace(document, source_id=source.source_id)
                if document.source_id != source.source_id else document
                for document in source.documents
            ),
            description=source.description,
            version=source.version,
        )
        previous = self._sources.get(normalized.source_id)
        self._sources[normalized.source_id] = normalized
        event = {
            "action": "replace" if previous is not None else "register",
            "source_id": normalized.source_id,
            "kinds": list(normalized.kinds),
            "document_count": len(normalized.documents),
            "previous_document_count": len(previous.documents) if previous else 0,
        }
        self.events.append(event)
        return event

    def register_payload(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        return self.register(custom_source_from_dict(raw))

    def clear(self) -> int:
        removed = len(self._sources)
        self._sources.clear()
        self.events.append({"action": "clear", "removed_sources": removed})
        return removed

    def get(self, source_id: str) -> CustomSource | None:
        return self._sources.get(source_id)

    def describe(self) -> list[dict[str, Any]]:
        return [self._sources[key].describe() for key in sorted(self._sources)]

    def __len__(self) -> int:
        return len(self._sources)

    def build_registry(self) -> tuple[ConnectorRegistry, list[str]]:
        """Build a connector registry over the current library contents.

        Returns ``(registry, warnings)``; raises ValueError when the library is
        empty so the caller gets an explicit error instead of an empty search.
        """
        if not self._sources:
            raise ValueError(
                "no custom sources registered; register a library first "
                "(POST /api/sources/custom with {source_id, documents})"
            )
        warnings: list[str] = []
        for source_id in sorted(self._sources):
            source = self._sources[source_id]
            if source.version is None:
                warnings.append(f"自定义来源 {source_id} 未提供 version 快照标签；结果不标记 caller 库版本。")
        registry = ConnectorRegistry(warnings)
        for source_id in sorted(self._sources):
            registry.register(CustomSourceConnector(self._sources[source_id]))
        return registry, warnings


@dataclass
class CustomSourceConnector:
    """Read-only connector serving one caller-provided source.

    Filtering follows the live-source rules: ``source_kinds`` narrows the kinds;
    ``guideline_version`` narrows guideline records by ``version_id``. Guideline
    records that lack ``version_id``/``locator`` produce an explicit warning
    (same audit rule as the live guideline connector) instead of being silently
    treated as current.
    """
    source: CustomSource

    @property
    def source_id(self) -> str:
        return self.source.source_id

    @property
    def kinds(self) -> tuple[str, ...]:
        return self.source.kinds

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        allowed = set(plan.filters.get("source_kinds") or ())
        documents = [
            document for document in self.source.documents
            if not allowed or document.kind in allowed
        ]
        warnings: list[str] = []
        requested_version = plan.filters.get("guideline_version")
        if requested_version:
            documents = [
                document for document in documents
                if document.kind != "guideline" or document.version_id == str(requested_version)
            ]
            if not any(document.kind == "guideline" for document in documents):
                warnings.append(f"自定义来源 {self.source_id} 中未找到请求的指南版本：{requested_version}。")
        missing = [document.document_id for document in documents if document.kind == "guideline" and (not document.version_id or not document.locator)]
        if missing:
            warnings.append(f"自定义来源 {self.source_id} 的指南条目缺少 version_id 或 locator，需人工核验：{', '.join(missing[:5])}。")
        return SearchResult(source_id=self.source_id, documents=tuple(documents[:limit]), warnings=tuple(warnings))


def load_custom_library(path: str | Path) -> CustomSourceLibrary:
    """Load a JSON library file: ``{"sources": [<custom source payload>, ...]}``.

    Used by ``config.custom_sources_path`` at server startup; the same payload
    shape is accepted by ``POST /api/sources/custom`` for one source at a time.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, Mapping) and "sources" in raw:
        items = raw["sources"]
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("custom library file must be {'sources': [...]} or a JSON array")
    library = CustomSourceLibrary()
    for index, item in enumerate(items):
        try:
            library.register_payload(item)
        except ValueError as exc:
            raise ValueError(f"sources[{index}]: {exc}") from exc
    return library
