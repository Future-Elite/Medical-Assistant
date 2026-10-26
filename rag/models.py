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
class TermMappingEntry:
    """One reviewed concept row: Chinese concept + English/MeSH products.

    The concept (or one of its aliases) gates expansion: a row is used only when
    it appears in the question, patient terms, reviewed external terms or
    facets.  English and MeSH stay bound to the same concept so the query plan
    can build concept-scoped variants and audit which concept produced which
    external term.
    """
    concept: str
    english: tuple[str, ...] = ()
    mesh: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    notes: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def surfaces(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            str(item).strip() for item in (self.concept, *self.aliases) if str(item).strip()
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "english": list(self.english),
            "mesh": list(self.mesh),
            "aliases": list(self.aliases),
            "notes": self.notes,
            "metadata": dict(self.metadata),
        }


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
    term_mapping: tuple[TermMappingEntry, ...] = ()
    english_terms: tuple[str, ...] = ()
    mesh_terms: tuple[str, ...] = ()
    patient_snapshot: Any | None = None
    # 来源模式仅两种：live（实时 PubMed + 官方指南）与 custom（调用方注册的数据库）。
    source_mode: str = "live"
    # 检索方法：bm25（默认）/ ql / rm3 / dense / xlingual / m3 / splade / colbert /
    # query2doc / translate，或 "bm25+xlingual" 这类多路组合（RRF 融合）。
    # None 表示未指定：默认 bm25；
    # 若管线上注册了 legacy semantic_retriever，则自动并入 dense（向后兼容）。
    # 神经方法需本地模型可用；不可用时报告 not_configured，不静默降级。
    # dense=MedCPT（英文域）；xlingual=multilingual-e5（中文问题直接编码，跨语言）；
    # m3=BGE-M3（多语言，CLS 池化；MMed-Bench-IR 的强多语言基线）；
    # translate=zh→en 机器翻译后的英文查询变体（只增加检索分支，不替换原问题）。
    retrieval_method: str | tuple[str, ...] | None = None
    # 可选：应用 cross-encoder 重排（在召回之后、引用生成之前）。
    rerank: bool = False
    # 查询保真门控（研究层）：off 时不审计也不改变信号（与冻结基线一致）；
    # audit=审计并记录、不改信号；warn=审计+显式警告；downweight=按错误级别对
    # 受影响路线降权；reject=按错误级别将受影响路线退出检索融合。审计与处置
    # 全过程写入 trace.query_fidelity；不静默改写查询计划。
    # 依据与语义见论文方法章节。
    fidelity_gate: str = "off"

    METHOD_TOKENS = ("bm25", "ql", "rm3", "dense", "xlingual", "m3", "splade", "colbert", "query2doc", "translate")
    FIDELITY_GATE_POLICIES = ("off", "audit", "warn", "downweight", "reject")

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
            term_mapping=parse_term_mapping(raw.get("term_mapping")),
            english_terms=tuple(_strings(raw.get("english_terms"))),
            mesh_terms=tuple(_strings(raw.get("mesh_terms"))),
            patient_snapshot=patient_snapshot,
            source_mode=_source_mode(raw.get("source_mode")),
            retrieval_method=methods,
            rerank=_boolean(raw.get("rerank", False)),
            fidelity_gate=_fidelity_gate(raw.get("fidelity_gate")),
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
    # 审核术语映射表的实际使用情况：命中的概念、英文/MeSH 产物与未命中概念。
    term_mapping: tuple[TermMappingEntry, ...] = ()
    mapping_trace: tuple[Mapping[str, Any], ...] = ()
    # 查询变体的角色与来源（映射表路径才生成）；variants 仍是实际打分输入。
    variant_trace: tuple[Mapping[str, Any], ...] = ()
    # 问题文本中直接提取的英文词（已过滤常见功能词与纯数值），
    # 直接参与英文检索/实时 PubMed 查询；与调用方审核的 english_terms 分开记录。
    question_english_terms: tuple[str, ...] = ()
    # Machine-generated query variants, separate from operator-reviewed terms.
    machine_translations: tuple[str, ...] = ()


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
    # 与 EvidencePackage.citations 的稳定外键：本条 RankedDocument 对应的 citation_id
    # （格式 <retrieval_id>:cit-N）。调用方按它关联引用，不要依赖两份列表的下标同序。
    # 少数路径（例如只做排序、不生成引用的内部调用）可能为 None。
    citation_id: str | None = None


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
class ApplicabilityCriterionResult:
    """One structured guideline criterion checked against patient facts.

    ``status`` is deliberately only ``match``/``mismatch``/``unknown``:
    missing, pending and not-recorded data never become a negative finding.
    """
    criterion_id: str
    field: str
    operator: str
    expected: tuple[str, ...] = ()
    status: str = "unknown"
    observed_value: str | None = None
    patient_record_paths: tuple[str, ...] = ()
    reason: str = ""
    required: bool = True
    label: str | None = None
    source_locator: str | None = None
    review_status: str = "draft"


@dataclass(frozen=True)
class ApplicabilityAssessment:
    document_id: str
    status: str
    reason: str
    matched_patient_fields: tuple[str, ...] = ()
    missing_patient_fields: tuple[str, ...] = ()
    patient_record_paths: tuple[str, ...] = ()
    criteria: tuple[ApplicabilityCriterionResult, ...] = ()


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


SOURCE_MODES = ("live", "custom")


def _fidelity_gate(value: Any) -> str:
    policy = str(value or "off").strip().lower()
    if policy not in RetrievalRequest.FIDELITY_GATE_POLICIES:
        raise ValueError("fidelity_gate must be one of: " + ", ".join(RetrievalRequest.FIDELITY_GATE_POLICIES))
    return policy


def _source_mode(value: Any) -> str:
    mode = str(value or "live").strip().lower()
    if mode not in SOURCE_MODES:
        raise ValueError("source_mode must be one of: " + ", ".join(SOURCE_MODES))
    return mode


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_term_mapping(value: Any) -> tuple[TermMappingEntry, ...]:
    """Normalize the reviewed mapping table into concept-bound rows.

    Accepted shapes::

        [{"concept": "新生儿呼吸窘迫综合征",
          "english": ["respiratory distress syndrome"],
          "mesh": ["Respiratory Distress Syndrome, Newborn"],
          "aliases": ["NRDS"]}]

        {"新生儿呼吸窘迫综合征": {
             "english": [...], "mesh": [...], "aliases": [...]}}

        {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]}

    The last two are the object form; a bare list value is the legacy
    ``user_terms`` shape and carries English terms only.  A row with neither
    English nor MeSH is rejected instead of being silently ignored.
    """
    if value in (None, "", {}, []):
        return ()
    rows: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        rows = [(str(concept), payload) for concept, payload in value.items()]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise ValueError(f"term_mapping[{index}] must be an object")
            concept = str(item.get("concept") or item.get("zh") or item.get("key") or "").strip()
            if not concept:
                raise ValueError(f"term_mapping[{index}] requires concept")
            rows.append((concept, item))
    else:
        raise ValueError("term_mapping must be an object or a list of concept rows")

    entries: list[TermMappingEntry] = []
    seen: set[str] = set()
    for concept, payload in rows:
        cleaned_concept = str(concept).strip()
        if not cleaned_concept:
            raise ValueError("term_mapping concept must be non-empty")
        key = cleaned_concept.casefold()
        if key in seen:
            raise ValueError(f"duplicate term_mapping concept: {cleaned_concept}")
        seen.add(key)
        english, mesh, aliases, notes, metadata = _term_mapping_fields(payload, cleaned_concept)
        if not english and not mesh:
            raise ValueError(f"term_mapping entry {cleaned_concept!r} requires english or mesh terms")
        entries.append(TermMappingEntry(
            concept=cleaned_concept, english=english, mesh=mesh, aliases=aliases,
            notes=notes, metadata=metadata,
        ))
    return tuple(entries)


def _term_mapping_fields(
    payload: Any, concept: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], str | None, Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        english = _strings(payload.get("english") or payload.get("english_terms") or payload.get("en"))
        mesh = _strings(payload.get("mesh") or payload.get("mesh_terms"))
        aliases = _strings(payload.get("aliases") or payload.get("alias") or payload.get("synonyms"))
        notes = _optional_string(payload.get("notes") or payload.get("note"))
        known = {
            "concept", "zh", "key", "english", "english_terms", "en",
            "mesh", "mesh_terms", "aliases", "alias", "synonyms", "notes", "note",
        }
        metadata = {str(key): item for key, item in payload.items() if key not in known}
        return tuple(english), tuple(mesh), tuple(aliases), notes, metadata
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return tuple(_strings(payload)), (), (), None, {}
    if isinstance(payload, str):
        return tuple(_strings(payload)), (), (), None, {}
    raise ValueError(f"term_mapping entry {concept!r} must be an object, list or string")


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
