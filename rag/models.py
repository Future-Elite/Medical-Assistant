"""Stable, JSON-first contracts for an evidence retrieval service.

This layer is intentionally independent from any vector store, LLM framework,
or HTTP framework.  External systems exchange mappings through ``from_dict``
and ``to_dict``; unknown metadata is retained on the document rather than
silently discarded.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date
from enum import Enum
from typing import Any, Mapping, Sequence
from uuid import uuid4


class SourceKind(str, Enum):
    GUIDELINE = "guideline"
    PUBMED = "pubmed"
    DRUG = "drug"
    CLINICAL_TRIAL = "clinical_trial"
    LOCAL_KNOWLEDGE = "local_knowledge"


class EvidenceStatus(str, Enum):
    CURRENT = "current"
    UNKNOWN_DATE = "unknown_date"
    FUTURE_AT_QUERY_TIME = "future_at_query_time"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True)
class RetrievalRequest:
    """Agent input. ``patient_terms`` must contain no raw medical record text.

    Patient data remains in the HIS-side tool.  The agent may pass a manually
    reviewed, minimal term list (for example, diagnosis and medication names)
    to make external retrieval patient-aware without copying a chart into an
    external provider.
    """

    request_id: str
    question: str
    task: str = "clinical_question"
    as_of: str | None = None
    patient_terms: tuple[str, ...] = ()
    source_kinds: tuple[str, ...] = ()
    filters: Mapping[str, Any] = field(default_factory=dict)
    top_k: int = 8
    user_terms: Mapping[str, Sequence[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RetrievalRequest":
        question = str(raw.get("question", "")).strip()
        if not question:
            raise ValueError("question must be a non-empty string")
        top_k = int(raw.get("top_k", 8))
        if not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        return cls(
            request_id=str(raw.get("request_id") or f"request-{uuid4().hex}"),
            question=question,
            task=str(raw.get("task") or "clinical_question"),
            as_of=_optional_string(raw.get("as_of")),
            patient_terms=tuple(_strings(raw.get("patient_terms"))),
            source_kinds=tuple(_strings(raw.get("source_kinds"))),
            filters=dict(raw.get("filters") or {}),
            top_k=top_k,
            user_terms={str(k): tuple(_strings(v)) for k, v in (raw.get("user_terms") or {}).items()},
        )


@dataclass(frozen=True)
class SourceDocument:
    """A retrievable unit with provenance sufficient for a citation action."""

    source_id: str
    document_id: str
    kind: str
    title: str
    text: str
    url: str | None = None
    published_at: str | None = None
    updated_at: str | None = None
    locator: str | None = None
    pmid: str | None = None
    doi: str | None = None
    authority: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, source_id: str | None = None) -> "SourceDocument":
        required = ("document_id", "kind", "title", "text")
        absent = [key for key in required if not str(raw.get(key, "")).strip()]
        if absent:
            raise ValueError("source document missing: " + ", ".join(absent))
        known = {
            "source_id", "document_id", "kind", "title", "text", "url", "published_at",
            "updated_at", "locator", "pmid", "doi", "authority", "metadata",
        }
        metadata = dict(raw.get("metadata") or {})
        metadata.update({k: v for k, v in raw.items() if k not in known})
        return cls(
            source_id=str(source_id or raw.get("source_id") or "unknown"),
            document_id=str(raw["document_id"]), kind=str(raw["kind"]),
            title=str(raw["title"]), text=str(raw["text"]),
            url=_optional_string(raw.get("url")), published_at=_optional_string(raw.get("published_at")),
            updated_at=_optional_string(raw.get("updated_at")), locator=_optional_string(raw.get("locator")),
            pmid=_optional_string(raw.get("pmid")), doi=_optional_string(raw.get("doi")),
            authority=_optional_string(raw.get("authority")), metadata=metadata,
        )

    def identity_key(self) -> str:
        if self.doi:
            return "doi:" + self.doi.lower().strip()
        if self.pmid:
            return "pmid:" + self.pmid.strip()
        normal = " ".join(self.title.lower().split())
        return f"title:{normal}|{self.kind}"

    def effective_date(self) -> str | None:
        return self.updated_at or self.published_at


@dataclass(frozen=True)
class QueryPlan:
    original_question: str
    variants: tuple[str, ...]
    concepts: tuple[str, ...]
    source_order: tuple[str, ...]
    filters: Mapping[str, Any]
    rationale: tuple[str, ...]


@dataclass(frozen=True)
class SearchResult:
    source_id: str
    documents: tuple[SourceDocument, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankedDocument:
    document: SourceDocument
    score: float
    lexical_score: float
    fusion_score: float
    authority_score: float
    freshness_score: float
    evidence_status: EvidenceStatus
    matched_queries: tuple[str, ...]
    rank_explanation: tuple[str, ...]


@dataclass(frozen=True)
class Citation:
    citation_id: str
    source_id: str
    document_id: str
    kind: str
    title: str
    quote: str
    locator: str | None
    url: str | None
    pmid: str | None
    doi: str | None
    evidence_status: EvidenceStatus
    actions: tuple[Mapping[str, Any], ...]
    key_evidence_sentences: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class EvidencePackage:
    retrieval_id: str
    request_id: str
    query_plan: QueryPlan
    ranked_documents: tuple[RankedDocument, ...]
    citations: tuple[Citation, ...]
    warnings: tuple[str, ...]
    trace: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, Sequence):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _optional_string(value: Any) -> str | None:
    value = str(value).strip() if value is not None else ""
    return value or None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value
