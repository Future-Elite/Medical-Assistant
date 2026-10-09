"""Stable JSON-first contracts for the evidence retrieval layer."""
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
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class RetrievalRequest:
    """Agent input; patient_terms are reviewed minimal terms, never raw notes."""
    request_id: str
    question: str
    task: str = "clinical_question"
    as_of: str | None = None
    patient_terms: tuple[str, ...] = ()
    source_kinds: tuple[str, ...] = ()
    filters: Mapping[str, Any] = field(default_factory=dict)
    top_k: int = 8
    user_terms: Mapping[str, Sequence[str]] = field(default_factory=dict)
    query_language: str = "zh"
    clinical_intent: str | None = None
    facets: Mapping[str, Sequence[str]] = field(default_factory=dict)
    english_terms: tuple[str, ...] = ()
    mesh_terms: tuple[str, ...] = ()
    patient_snapshot: Any | None = None
    source_mode: str = "simulated"
    # 检索方法：bm25（默认）/ ql / rm3 / dense / xlingual / splade / colbert /
    # query2doc / translate，或 "bm25+dense" 这类多路组合（RRF 融合）。
    # None 表示未指定：默认 bm25；
    # 若管线上注册了 legacy semantic_retriever，则自动并入 dense（向后兼容）。
    # 神经方法需本地模型可用；不可用时报告 not_configured，不静默降级。
    # dense=MedCPT（英文域）；xlingual=multilingual-e5（中文问题直接编码，跨语言）；
    # translate=zh→en 机器翻译后的英文查询变体（只增加检索分支，不替换原问题）。
    retrieval_method: str | tuple[str, ...] | None = None
    # 可选：应用 cross-encoder 重排（在召回之后、引用生成之前）。
    rerank: bool = False

    METHOD_TOKENS = ("bm25", "ql", "rm3", "dense", "xlingual", "splade", "colbert", "query2doc", "translate")

    @staticmethod
    def parse_methods(value: Any) -> tuple[str, ...]:
        """Normalize retrieval_method input into a tuple of known method tokens.

        Accepts: "bm25" | "ql" | "rm3" | "dense" | "xlingual" | "translate" |
        "bm25+xlingual" | ["bm25","xlingual"] | [] (defaults to bm25).
        """
        if value is None:
            return ("bm25",)
        raw_items: list[str] = []
        if isinstance(value, str):
            raw_items = [part.strip().lower() for part in value.replace("+", ",").split(",")]
        elif isinstance(value, Sequence):
            for item in value:
                raw_items.extend(part.strip().lower() for part in str(item).replace("+", ",").split(","))
        tokens = [item for item in raw_items if item]
        if not tokens:
            return ("bm25",)
        unknown = [item for item in tokens if item not in RetrievalRequest.METHOD_TOKENS]
        if unknown:
            raise ValueError("unknown retrieval_method: " + ", ".join(unknown))
        out: list[str] = []
        for item in tokens:
            if item not in out:
                out.append(item)
        return tuple(out)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RetrievalRequest":
        question = str(raw.get("question", "")).strip()
        if not question:
            raise ValueError("question must be a non-empty string")
        top_k = int(raw.get("top_k", 8))
        if not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        facets = {
            str(key): tuple(_strings(value))
            for key, value in (raw.get("facets") or {}).items()
        }
        patient_snapshot = raw.get("patient_snapshot")
        if patient_snapshot is not None and not hasattr(patient_snapshot, "facts"):
            from .his import snapshot_from_dict
            patient_snapshot = snapshot_from_dict(patient_snapshot)
        raw_method = raw.get("retrieval_method")
        if raw_method is None:
            raw_method = raw.get("method")
        methods = cls.parse_methods(raw_method) if raw_method is not None else None
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
            query_language=str(raw.get("query_language") or raw.get("language") or "zh"),
            clinical_intent=_optional_string(raw.get("clinical_intent")),
            facets=facets,
            english_terms=tuple(_strings(raw.get("english_terms"))),
            mesh_terms=tuple(_strings(raw.get("mesh_terms"))),
            patient_snapshot=patient_snapshot,
            source_mode=str(raw.get("source_mode") or "simulated"),
            retrieval_method=methods,
            rerank=_boolean(raw.get("rerank", False)),
        )


@dataclass(frozen=True)
class SourceDocument:
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
    version_id: str | None = None
    recommendation_id: str | None = None
    population: str | None = None
    status: str | None = None
    superseded_by: str | None = None
    index_snapshot: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, source_id: str | None = None) -> "SourceDocument":
        required = ("document_id", "kind", "title", "text")
        absent = [key for key in required if not str(raw.get(key, "")).strip()]
        if absent:
            raise ValueError("source document missing: " + ", ".join(absent))
        known = {
            "source_id", "document_id", "kind", "title", "text", "url", "published_at",
            "updated_at", "locator", "pmid", "doi", "authority", "version_id",
            "recommendation_id", "population", "status", "superseded_by", "index_snapshot", "metadata",
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
            authority=_optional_string(raw.get("authority")), version_id=_optional_string(raw.get("version_id")),
            recommendation_id=_optional_string(raw.get("recommendation_id")),
            population=_optional_string(raw.get("population")), status=_optional_string(raw.get("status")),
            superseded_by=_optional_string(raw.get("superseded_by")),
            index_snapshot=_optional_string(raw.get("index_snapshot")), metadata=metadata,
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

    def field_values(self) -> Mapping[str, str]:
        metadata = self.metadata
        return {
            "title": self.title,
            "text": self.text,
            "mesh": str(metadata.get("mesh_terms") or metadata.get("mesh") or ""),
            "keywords": str(metadata.get("keywords") or ""),
            "population": self.population or "",
        }


@dataclass(frozen=True)
class QueryPlan:
    original_question: str
    variants: tuple[str, ...]
    concepts: tuple[str, ...]
    source_order: tuple[str, ...]
    filters: Mapping[str, Any]
    rationale: tuple[str, ...]
    query_language: str = "zh"
    clinical_intent: str = "clinical_question"
    facets: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    english_terms: tuple[str, ...] = ()
    mesh_terms: tuple[str, ...] = ()
    expansion_trace: tuple[Mapping[str, Any], ...] = ()
    # 问题文本中直接提取的英文词（已过滤常见功能词与纯数值），
    # 直接参与英文检索/实时 PubMed 查询；与调用方审核的 english_terms 分开记录。
    question_english_terms: tuple[str, ...] = ()


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
    retriever_id: str | None = None
    retriever_rank: int | None = None
    retriever_score: float | None = None
    semantic_rank: int | None = None
    semantic_score: float | None = None
    fusion_method: str = "bm25"
    fusion_rank: int | None = None
    reranker_id: str | None = None
    reranker_score: float | None = None


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
    version_id: str | None = None
    recommendation_id: str | None = None
    population: str | None = None
    retrieved_at: str | None = None
    index_snapshot: str | None = None


@dataclass(frozen=True)
class PatientHit:
    record_path: str
    field_path: str
    collection: str
    event_type: str
    time: str | None
    start: str | None
    end: str | None
    matched_terms: tuple[str, ...]
    score: float
    evidence_text: str
    source_type: str = "patient_record"


@dataclass(frozen=True)
class ApplicabilityAssessment:
    document_id: str
    status: str
    reason: str
    matched_patient_fields: tuple[str, ...] = ()
    missing_patient_fields: tuple[str, ...] = ()
    patient_record_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidencePackage:
    retrieval_id: str
    request_id: str
    query_plan: QueryPlan
    ranked_documents: tuple[RankedDocument, ...]
    citations: tuple[Citation, ...]
    warnings: tuple[str, ...]
    trace: Mapping[str, Any]
    patient_hits: tuple[PatientHit, ...] = ()
    evidence_groups: Mapping[str, tuple[RankedDocument, ...]] = field(default_factory=dict)
    applicability: tuple[ApplicabilityAssessment, ...] = ()
    source_separation: Mapping[str, Any] = field(default_factory=dict)
    patient_context: Mapping[str, Any] = field(default_factory=dict)

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


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


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
