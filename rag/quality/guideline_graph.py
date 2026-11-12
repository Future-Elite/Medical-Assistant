"""Industrial-grade guideline graph and hybrid applicability pilot.

This module is deliberately a *pilot*, not a replacement for the field-level
engine.  It adds four things the current criterion table does not carry:

1. recommendation nodes with evidence level, time window and setting;
2. exception nodes (hard contraindication / related condition / explicit
   non-contraindication);
3. a patient evidence graph that keeps record path, field, time and span;
4. a hybrid decision path: deterministic criteria first, text evidence second,
   optional model proposals last (proposals can only raise ``needs_review``).

Hard boundaries:

- one guideline is encoded (VLBW feeding 2015); this is not a guideline library;
- text rules are retrospective documentation audit, not clinical judgment;
- missing documentation is ``unknown``/``insufficient_data``, never negative;
- ``exception_triggered`` means "review the documented exception", not
  "the patient must not be fed";
- a model proposal never overrides a deterministic status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from .applicability import assess_applicability
from ..his import PatientSnapshot, snapshot_from_dict
from ..models import QueryPlan, SourceDocument, parse_iso_date
from ..retrieval.bm25 import score_documents

GRAPH_SCHEMA_VERSION = "guideline-graph-v1"
_EXCEPTION_KINDS = frozenset({"hard", "related", "negative"})
_TEXT_FIELDS = (
    "admissionDiagnosis", "dischargeDiagnosis", "dischargeCondition",
    "dischargeGuidance", "dischargeOrders", "complications",
    "treatmentCourse", "presentingSymptoms", "laboratoryFindings",
    "specialExaminations",
)
# Exception/milk/tolerance evidence is restricted to patient-level clinical
# fields.  ``presentingSymptoms`` often carries maternal history (e.g. a
# maternal "肠梗阻" surgical history), which must not be attributed to the baby.
_EVIDENCE_FIELDS = (
    "admissionDiagnosis", "dischargeDiagnosis", "dischargeCondition",
    "dischargeGuidance", "dischargeOrders", "complications", "treatmentCourse",
)
_NEGATION_CUES = ("无", "未见", "未发现", "未提示", "排除", "否认", "不存在")
_UNCERTAINTY_CUES = ("风险", "可能", "疑", "考虑", "警惕", "待排", "不排除")
_MILK_PATTERNS = (
    (("捐赠母乳", "捐赠乳"), (), "捐赠母乳"),
    (("母乳",), ("强化",), "母乳+强化"),
    (("母乳",), (), "母乳"),
    (("混合喂养",), (), "混合"),
    (("早产儿奶", "早产儿配方", "配方奶"), (), "配方奶"),
)
_TOLERANT_PATTERNS = ("喂养好", "喂养耐受", "腹软", "腹不胀", "无呕吐", "腹隆软不胀")
_INTOLERANCE_PATTERNS = ("喂养不耐受", "腹胀", "腹隆", "呕吐", "胃残余")
_HEMORRHAGIC_PATTERNS = ("血性残余", "血性胃残余", "咖啡色残余", "血性胃液")

SemanticRanker = Callable[[str, Sequence["GraphRecommendation"]], Mapping[str, float]]
JudgeFn = Callable[
    [Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]],
    Sequence[Mapping[str, Any]],
]


@dataclass(frozen=True)
class GraphException:
    exception_id: str
    kind: str
    terms: tuple[str, ...]
    action: str
    description: str
    source_locator: str | None = None


@dataclass(frozen=True)
class GraphRecommendation:
    recommendation_id: str
    statement: str
    evidence_level: str
    timeframe: Mapping[str, Any] | None = None
    conditions: tuple[Mapping[str, Any], ...] = ()
    exceptions: tuple[GraphException, ...] = ()
    actions: tuple[str, ...] = ()
    source_locator: str | None = None
    kind: str = "recommendation"
    check: str | None = None


@dataclass(frozen=True)
class GuidelineGraph:
    guideline_id: str
    title: str
    version: str
    effective_from: str | None
    effective_to: str | None
    setting: tuple[str, ...]
    population: str
    scope: tuple[Mapping[str, Any], ...]
    recommendations: tuple[GraphRecommendation, ...]
    source: Mapping[str, Any] = field(default_factory=dict)


def load_guideline_graph(path: str | Path | None = None) -> GuidelineGraph:
    """Load and validate one graph fixture.  Malformed nodes are rejected."""
    target = Path(path) if path else default_graph_path()
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("guideline graph must be a JSON object")
    if raw.get("schema_version") != GRAPH_SCHEMA_VERSION:
        raise ValueError(f"unsupported graph schema: {raw.get('schema_version')!r}")
    guideline_id = str(raw.get("guideline_id") or "").strip()
    if not guideline_id:
        raise ValueError("guideline graph requires guideline_id")
    scope = raw.get("scope") or {}
    criteria = scope.get("criteria") if isinstance(scope, Mapping) else scope
    if not isinstance(criteria, Sequence) or isinstance(criteria, (str, bytes)):
        raise ValueError("guideline graph scope.criteria must be a list")
    recommendations: list[GraphRecommendation] = []
    seen: set[str] = set()
    for index, row in enumerate(raw.get("recommendations") or ()):
        if not isinstance(row, Mapping):
            raise ValueError(f"recommendation[{index}] is not an object")
        recommendation_id = str(row.get("recommendation_id") or "").strip()
        if not recommendation_id or recommendation_id in seen:
            raise ValueError(f"recommendation[{index}] has missing/duplicate id")
        seen.add(recommendation_id)
        statement = str(row.get("statement") or "").strip()
        if not statement:
            raise ValueError(f"{recommendation_id} has no statement")
        exceptions: list[GraphException] = []
        for exc_index, exc in enumerate(row.get("exceptions") or ()):
            if not isinstance(exc, Mapping):
                raise ValueError(f"{recommendation_id} exception[{exc_index}] is not an object")
            kind = str(exc.get("kind") or "").strip().lower()
            if kind not in _EXCEPTION_KINDS:
                raise ValueError(f"{recommendation_id} exception kind must be hard/related/negative")
            terms = tuple(str(item).strip() for item in (exc.get("terms") or ()) if str(item).strip())
            if not terms:
                raise ValueError(f"{recommendation_id} exception {exc.get('exception_id')} has no terms")
            exceptions.append(GraphException(
                exception_id=str(exc.get("exception_id") or f"e{exc_index + 1}"),
                kind=kind,
                terms=terms,
                action=str(exc.get("action") or ""),
                description=str(exc.get("description") or ""),
                source_locator=_optional(exc.get("source_locator")),
            ))
        recommendations.append(GraphRecommendation(
            recommendation_id=recommendation_id,
            statement=statement,
            evidence_level=str(row.get("evidence_level") or "not_stated"),
            timeframe=dict(row.get("timeframe") or {}) if row.get("timeframe") else None,
            conditions=tuple(row.get("conditions") or ()),
            exceptions=tuple(exceptions),
            actions=tuple(str(item) for item in (row.get("actions") or ()) if str(item).strip()),
            source_locator=_optional(row.get("source_locator")),
            kind=str(row.get("kind") or "recommendation"),
            check=_optional(row.get("check")),
        ))
    if not recommendations:
        raise ValueError("guideline graph requires at least one recommendation")
    return GuidelineGraph(
        guideline_id=guideline_id,
        title=str(raw.get("title") or guideline_id),
        version=str(raw.get("version") or "unknown"),
        effective_from=_optional(raw.get("effective_from")),
        effective_to=_optional(raw.get("effective_to")),
        setting=tuple(str(item) for item in (raw.get("setting") or ()) if str(item).strip()),
        population=str(raw.get("population") or ""),
        scope=tuple(criteria),
        recommendations=tuple(recommendations),
        source=dict(raw.get("source") or {}),
    )


def default_graph_path() -> Path:
    return Path(__file__).resolve().parents[1] / "eval" / "industrial_fixture" / "vlbw_feeding_2015_graph.json"


def assess_guideline_graph(
    snapshot: Mapping[str, Any] | PatientSnapshot,
    graph: GuidelineGraph,
    *,
    question: str = "",
    semantic_ranker: SemanticRanker | None = None,
    judge_fn: JudgeFn | None = None,
) -> dict[str, Any]:
    """Assess one graph against one time-bounded patient snapshot."""
    parsed = snapshot if isinstance(snapshot, PatientSnapshot) else snapshot_from_dict(snapshot)
    evidence = _patient_evidence(parsed, graph)
    scope = _assess_conditions(
        graph.scope, parsed, document_id=f"{graph.guideline_id}::scope",
        title=f"{graph.title} scope", statement=graph.population,
    )
    ranked, retrieval = _rank_recommendations(
        question or _patient_query(parsed, evidence), graph.recommendations,
        semantic_ranker=semantic_ranker,
    )
    recommendations = []
    for recommendation, rank_score in ranked:
        recommendations.append(_assess_recommendation(
            recommendation, parsed, evidence, scope=scope,
            rank_score=rank_score, judge_fn=judge_fn,
        ))
    summary: dict[str, int] = {}
    for item in recommendations:
        summary[item["status"]] = summary.get(item["status"], 0) + 1
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "guideline_id": graph.guideline_id,
        "title": graph.title,
        "version": graph.version,
        "effective_from": graph.effective_from,
        "effective_to": graph.effective_to,
        "setting": list(graph.setting),
        "population": graph.population,
        "scope": scope,
        "recommendations": recommendations,
        "summary": summary,
        "retrieval": retrieval,
        "patient_evidence": {
            "as_of": parsed.as_of,
            "record_paths": sorted({item["record_path"] for item in evidence["documents"]}),
            "fact_count": len(parsed.facts),
            "milk": evidence["milk"],
            "tolerance": evidence["tolerance"],
            "time_anchors": evidence["time_anchors"],
        },
        "not_clinical_advice": True,
        "boundaries": [
            "单份指南试点；不是指南库，也不代表已接入 FHIR/CDS Hooks。",
            "文本证据来自出院小结，是回顾性文档审计，不是床旁判断。",
            "缺失记录写 unknown/insufficient_data，不写阴性。",
            "exception_triggered 只表示需要人工复核记录的例外，不是停喂医嘱。",
            "模型建议只能提高 needs_review，不能覆盖确定性结论。",
        ],
    }


def _assess_recommendation(
    recommendation: GraphRecommendation,
    snapshot: PatientSnapshot,
    evidence: Mapping[str, Any],
    *,
    scope: Mapping[str, Any],
    rank_score: float,
    judge_fn: JudgeFn | None,
) -> dict[str, Any]:
    conditions = _assess_conditions(
        recommendation.conditions, snapshot,
        document_id=recommendation.recommendation_id,
        title=recommendation.statement,
        statement=recommendation.statement,
    )
    timeframe = _assess_timeframe(recommendation.timeframe, snapshot, evidence)
    exceptions = [
        _assess_exception(exception, evidence)
        for exception in recommendation.exceptions
    ]
    text_check = _assess_text_check(recommendation, evidence)
    status, fusion_rule, missing = _fuse_status(
        scope=scope, conditions=conditions, timeframe=timeframe,
        exceptions=exceptions, text_check=text_check,
    )
    proposals, model_status = _apply_model_proposals(
        recommendation, snapshot, evidence, judge_fn=judge_fn,
    )
    if proposals and status in {"applicable", "applicable_with_caution"}:
        status = "needs_review"
        fusion_rule = "model_proposal_requires_review"
    missing = sorted(set(missing) | {
        item for proposal in proposals for item in (proposal.get("missing_facts") or ())
    })
    locators = [recommendation.source_locator] if recommendation.source_locator else []
    locators.extend(
        item["source_locator"] for item in exceptions if item.get("source_locator")
    )
    locators.extend(
        criterion.get("locator") for criterion in conditions.get("criteria", [])
        if criterion.get("locator")
    )
    record_paths = sorted({
        path for criterion in conditions.get("criteria", [])
        for path in criterion.get("record_paths", [])
    } | {
        hit["record_path"] for item in exceptions for hit in item.get("evidence", [])
    } | {
        hit["record_path"] for hit in (evidence["milk"].get("evidence") or [])
        + (evidence["tolerance"].get("evidence") or [])
    })
    return {
        "recommendation_id": recommendation.recommendation_id,
        "statement": recommendation.statement,
        "kind": recommendation.kind,
        "check": recommendation.check,
        "evidence_level": recommendation.evidence_level,
        "timeframe": recommendation.timeframe,
        "status": status,
        "actions": list(recommendation.actions),
        "conditions": conditions,
        "time": timeframe,
        "exceptions": exceptions,
        "text_check": text_check,
        "missing_facts": missing,
        "rank_score": round(rank_score, 6),
        "model_proposals": proposals,
        "audit": {
            "scope_status": scope.get("status"),
            "condition_status": conditions.get("status"),
            "time_status": timeframe.get("status"),
            "exception_statuses": {
                item["exception_id"]: item["status"] for item in exceptions
            },
            "text_check_status": text_check.get("status"),
            "deterministic_status": _deterministic_status(
                scope=scope, conditions=conditions, timeframe=timeframe,
                exceptions=exceptions, text_check=text_check,
            ),
            "model_judgment": model_status,
            "fusion_rule": fusion_rule,
            "source_locators": sorted({item for item in locators if item}),
            "record_paths": record_paths,
        },
    }


def _assess_conditions(
    conditions: Sequence[Mapping[str, Any]],
    snapshot: PatientSnapshot,
    *,
    document_id: str,
    title: str,
    statement: str,
) -> dict[str, Any]:
    rows = list(conditions or ())
    if not rows:
        return {"status": "match", "criteria": [], "reason": "没有附加结构化条件"}
    document = SourceDocument(
        source_id="guideline-graph", document_id=document_id,
        kind="guideline_recommendation", title=title, text=statement,
        metadata={"applicability_criteria": {"logic": "all_of", "criteria": rows}},
    )
    assessment = assess_applicability(document, snapshot, [])
    return {
        "status": {"possible_match": "match"}.get(assessment.status, assessment.status),
        "reason": assessment.reason,
        "criteria": [
            {
                "criterion_id": criterion.criterion_id,
                "field": criterion.field,
                "label": criterion.label,
                "status": criterion.status,
                "observed_value": criterion.observed_value,
                "reason": criterion.reason,
                "locator": criterion.source_locator,
                "record_paths": list(criterion.patient_record_paths),
            }
            for criterion in assessment.criteria
        ],
    }


def _assess_timeframe(
    timeframe: Mapping[str, Any] | None,
    snapshot: PatientSnapshot,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    if not timeframe:
        return {"status": "not_required", "reason": "该建议没有时间窗", "missing_facts": []}
    anchor = str(timeframe.get("anchor") or "")
    if anchor != "birth":
        return {
            "status": "unknown", "reason": f"不支持的时间锚点 {anchor!r}",
            "missing_facts": ["supported_time_anchor"],
        }
    birth_time = evidence["time_anchors"].get("birth_time")
    if not birth_time:
        return {
            "status": "unknown",
            "reason": "病历没有可解析的出生时间；不能判断是否在时间窗内。",
            "missing_facts": ["birth_time"],
        }
    if timeframe.get("start_within_hours") is not None:
        start = evidence["time_anchors"].get("feeding_start_time")
        if not start:
            return {
                "status": "unknown",
                "reason": "病历没有可解析的开奶时间；不能判断是否在 24 h 内。",
                "missing_facts": ["feeding_start_time"],
                "anchor_time": birth_time,
            }
        hours = _hours_between(birth_time, start)
        if hours is None:
            return {
                "status": "unknown", "reason": "出生/开奶时间无法比较",
                "missing_facts": ["parseable_birth_or_feeding_time"],
            }
        limit = float(timeframe["start_within_hours"])
        return {
            "status": "match" if hours <= limit else "mismatch",
            "observed_hours": round(hours, 2), "limit_hours": limit,
            "reason": f"开奶时间距出生 {hours:.1f} h，阈值 {limit:g} h。",
            "missing_facts": [],
        }
    if timeframe.get("target") == "full_enteral_feeding":
        full_feed = evidence["time_anchors"].get("full_feed_time")
        if not full_feed:
            return {
                "status": "unknown",
                "reason": "病历没有可解析的达到全肠内喂养时间；不能判断是否在目标天数内。",
                "missing_facts": ["full_feed_time"],
                "anchor_time": birth_time,
            }
        days = _days_between(birth_time, full_feed)
        weight = _numeric_field(snapshot, "birth_weight_g")
        target_days = _target_days(timeframe, weight)
        if days is None or target_days is None:
            return {
                "status": "unknown", "reason": "目标天数或时间无法计算",
                "missing_facts": ["target_days_or_full_feed_time"],
            }
        return {
            "status": "match" if days <= target_days else "mismatch",
            "observed_days": round(days, 2), "target_days": target_days,
            "reason": f"达到全肠内喂养距出生 {days:.1f} 天，目标约 {target_days} 天。",
            "missing_facts": [],
        }
    return {"status": "not_required", "reason": "没有可执行的时间判断", "missing_facts": []}


def _target_days(timeframe: Mapping[str, Any], weight: float | None) -> int | None:
    if weight is None:
        return None
    for row in timeframe.get("days_by_weight") or ():
        if not isinstance(row, Mapping):
            continue
        low = row.get("min_weight_g")
        high = row.get("max_weight_g")
        if low is not None and weight < float(low):
            continue
        if high is not None and weight > float(high):
            continue
        try:
            return int(row["days"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _assess_exception(exception: GraphException, evidence: Mapping[str, Any]) -> dict[str, Any]:
    hits = []
    for term in exception.terms:
        hits.extend(evidence["term_hits"].get(term, ()))
    active = [hit for hit in hits if not hit.get("negated")]
    confirmed = [hit for hit in active if not hit.get("uncertain")]
    uncertain = [hit for hit in active if hit.get("uncertain")]
    if exception.kind == "hard":
        status = "triggered" if confirmed else ("suspected" if uncertain else "not_found")
    elif exception.kind == "related":
        status = "related" if active else "not_found"
    else:
        status = "present_not_contraindication" if active else "not_found"
    return {
        "exception_id": exception.exception_id,
        "kind": exception.kind,
        "status": status,
        "action": exception.action,
        "description": exception.description,
        "source_locator": exception.source_locator,
        "evidence": active,
        "note": (
            "没有找到该词不等于没有该情况；缺失记录不写阴性。"
            if status == "not_found" else ""
        ),
    }


def _assess_text_check(
    recommendation: GraphRecommendation, evidence: Mapping[str, Any],
) -> dict[str, Any]:
    if recommendation.check == "milk_choice":
        milk = evidence["milk"]
        if milk["status"] != "observed":
            return {"status": "unknown", "field": "milk_type", "value": None,
                    "reason": "病历没有可解析的奶类记录。"}
        value = milk["value"]
        if value in {"母乳", "母乳+强化", "捐赠母乳"}:
            return {"status": "concordant", "field": "milk_type", "value": value,
                    "reason": "记录到母亲乳/捐赠乳，符合指南的首选/次选顺序。"}
        return {"status": "alternative", "field": "milk_type", "value": value,
                "reason": "记录到配方奶/混合喂养；配方奶是指南第三选择，不等于违规，需核对母亲乳/捐赠乳不可用的原因。"}
    if recommendation.check == "feeding_tolerance":
        tolerance = evidence["tolerance"]
        if tolerance["status"] != "observed":
            return {"status": "unknown", "field": "feeding_tolerance", "value": None,
                    "reason": "病历没有可解析的喂养耐受记录。"}
        if tolerance["value"] == "hemorrhagic_residual":
            return {"status": "exception", "field": "feeding_tolerance",
                    "value": tolerance["value"], "reason": "记录到血性残余，需停喂并人工核对。"}
        if tolerance["value"] == "intolerance":
            return {"status": "caution", "field": "feeding_tolerance",
                    "value": tolerance["value"], "reason": "记录到腹胀/喂养不耐受等信号，需复核。"}
        return {"status": "concordant", "field": "feeding_tolerance",
                "value": tolerance["value"], "reason": "记录到喂养耐受/腹部软等描述。"}
    return {"status": "not_required", "field": None, "value": None, "reason": "该建议没有文本核对项。"}


def _fuse_status(
    *, scope: Mapping[str, Any], conditions: Mapping[str, Any],
    timeframe: Mapping[str, Any], exceptions: Sequence[Mapping[str, Any]],
    text_check: Mapping[str, Any],
) -> tuple[str, str, list[str]]:
    missing: list[str] = []
    if scope.get("status") == "mismatch":
        return "not_applicable", "scope_mismatch", missing
    if scope.get("status") == "unknown":
        missing.extend(
            item["field"] for item in scope.get("criteria", ())
            if item.get("status") == "unknown"
        )
        return "insufficient_data", "scope_unknown", missing
    hard = [item for item in exceptions if item.get("kind") == "hard" and item.get("status") == "triggered"]
    if hard:
        return "exception_triggered", "hard_exception", missing
    suspected = [item for item in exceptions if item.get("kind") == "hard" and item.get("status") == "suspected"]
    if suspected:
        return "needs_review", "hard_exception_suspected", missing
    related = [item for item in exceptions if item.get("kind") == "related" and item.get("status") == "related"]
    if related:
        return "needs_review", "related_condition", missing
    if conditions.get("status") == "mismatch":
        return "not_applicable", "condition_mismatch", missing
    if conditions.get("status") == "unknown":
        missing.extend(
            item["field"] for item in conditions.get("criteria", ())
            if item.get("status") == "unknown"
        )
        return "insufficient_data", "condition_unknown", missing
    if timeframe.get("status") == "mismatch":
        return "needs_review", "time_mismatch", missing
    if timeframe.get("status") == "unknown":
        missing.extend(timeframe.get("missing_facts") or ())
        return "insufficient_data", "time_unknown", missing
    if text_check.get("status") == "unknown":
        missing.append(str(text_check.get("field") or "text_evidence"))
        return "insufficient_data", "text_evidence_unknown", missing
    if text_check.get("status") == "exception":
        return "exception_triggered", "text_exception", missing
    if text_check.get("status") in {"caution", "alternative"}:
        return "applicable_with_caution", f"text_{text_check.get('status')}", missing
    return "applicable", "all_checks_passed", missing


def _deterministic_status(
    *, scope: Mapping[str, Any], conditions: Mapping[str, Any],
    timeframe: Mapping[str, Any], exceptions: Sequence[Mapping[str, Any]],
    text_check: Mapping[str, Any],
) -> str:
    return _fuse_status(
        scope=scope, conditions=conditions, timeframe=timeframe,
        exceptions=exceptions, text_check=text_check,
    )[0]


def _apply_model_proposals(
    recommendation: GraphRecommendation,
    snapshot: PatientSnapshot,
    evidence: Mapping[str, Any],
    *,
    judge_fn: JudgeFn | None,
) -> tuple[list[dict[str, Any]], str]:
    if judge_fn is None:
        return [], "not_configured"
    try:
        proposals = judge_fn(
            {
                "recommendation_id": recommendation.recommendation_id,
                "statement": recommendation.statement,
                "evidence_level": recommendation.evidence_level,
            },
            {"as_of": snapshot.as_of, "record_paths": evidence["record_paths"]},
            {"text": evidence["evidence_text"], "milk": evidence["milk"], "tolerance": evidence["tolerance"]},
        )
    except Exception as exc:  # model failure must not break deterministic result
        return [], f"error: {type(exc).__name__}"
    accepted: list[dict[str, Any]] = []
    for item in proposals or ():
        if not isinstance(item, Mapping):
            continue
        if str(item.get("recommendation_id") or "") != recommendation.recommendation_id:
            continue
        if str(item.get("status") or "") not in {"needs_review", "caution"}:
            continue
        span = str(item.get("evidence_span") or "").strip()
        if not span or span not in evidence["evidence_text"]:
            continue
        accepted.append({
            "recommendation_id": recommendation.recommendation_id,
            "status": str(item["status"]),
            "evidence_span": span,
            "reason": str(item.get("reason") or ""),
            "source": "model",
        })
    return accepted, "executed" if accepted else "no_valid_proposal"


def _patient_evidence(
    snapshot: PatientSnapshot, graph: GuidelineGraph,
) -> dict[str, Any]:
    terms = sorted({
        term for recommendation in graph.recommendations
        for exception in recommendation.exceptions for term in exception.terms
    })
    documents: list[dict[str, Any]] = []
    for fact in snapshot.facts:
        for field_name in _TEXT_FIELDS:
            value = fact.fields.get(field_name)
            if isinstance(value, str) and value.strip():
                documents.append({
                    "record_path": fact.record_path,
                    "field": field_name,
                    "text": value,
                    "time": fact.time,
                })
    evidence_documents = [
        item for item in documents if item["field"] in _EVIDENCE_FIELDS
    ]
    joined = "\n".join(item["text"] for item in documents)
    evidence_text = "\n".join(item["text"] for item in evidence_documents)
    term_hits: dict[str, list[dict[str, Any]]] = {}
    for term in terms:
        hits = _find_hits(term, evidence_documents)
        if hits:
            term_hits[term] = hits
    return {
        "documents": documents,
        "evidence_documents": evidence_documents,
        "text": joined,
        "evidence_text": evidence_text,
        "term_hits": term_hits,
        "milk": _extract_milk(evidence_documents),
        "tolerance": _extract_tolerance(evidence_documents),
        "time_anchors": _extract_time_anchors(snapshot, evidence_documents),
        "record_paths": sorted({item["record_path"] for item in evidence_documents}),
    }


def _find_hits(term: str, documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for document in documents:
        text = str(document["text"])
        for match in re.finditer(re.escape(term), text):
            prefix = text[max(0, match.start() - 8):match.start()]
            negated = any(cue in prefix for cue in _NEGATION_CUES)
            uncertain = any(cue in prefix for cue in _UNCERTAINTY_CUES)
            hits.append({
                "term": term,
                "field": document["field"],
                "record_path": document["record_path"],
                "span": text[max(0, match.start() - 12):match.end() + 12],
                "negated": negated,
                "uncertain": uncertain,
            })
    return hits


def _extract_milk(documents: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for any_terms, all_terms, value in _MILK_PATTERNS:
        for document in documents:
            text = str(document["text"])
            if any(term in text for term in any_terms) and all(term in text for term in all_terms):
                start = text.find(next(term for term in any_terms if term in text))
                return {
                    "status": "observed",
                    "value": value,
                    "evidence": [{
                        "record_path": document["record_path"],
                        "field": document["field"],
                        "span": text[max(0, start - 12):start + 30],
                    }],
                }
    return {"status": "not_recorded", "value": None, "evidence": []}


def _extract_tolerance(documents: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    hemorrhagic = _first_pattern(documents, _HEMORRHAGIC_PATTERNS)
    if hemorrhagic:
        return {"status": "observed", "value": "hemorrhagic_residual", "evidence": [hemorrhagic]}
    tolerant = _first_pattern(documents, _TOLERANT_PATTERNS)
    if tolerant:
        return {"status": "observed", "value": "tolerant", "evidence": [tolerant]}
    intolerance = _first_pattern(documents, _INTOLERANCE_PATTERNS)
    if intolerance:
        return {"status": "observed", "value": "intolerance", "evidence": [intolerance]}
    return {"status": "not_recorded", "value": None, "evidence": []}


def _first_pattern(
    documents: Sequence[Mapping[str, Any]], patterns: Sequence[str],
) -> dict[str, Any] | None:
    for pattern in patterns:
        hits = [hit for hit in _find_hits(pattern, documents) if not hit["negated"]]
        if hits:
            return {
                "record_path": hits[0]["record_path"],
                "field": hits[0]["field"],
                "span": hits[0]["span"],
            }
    return None


def _extract_time_anchors(
    snapshot: PatientSnapshot, documents: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    keys = {
        "birth_time": ("birth_time", "birthTime", "出生时间", "出生日期"),
        "feeding_start_time": ("feeding_start_time", "feedingStartTime", "开奶时间", "首次喂养时间"),
        "full_feed_time": ("full_feed_time", "fullFeedTime", "全肠内喂养时间", "达到全量喂养时间"),
    }
    for name, candidates in keys.items():
        value = None
        for key in candidates:
            if key in snapshot.extra and snapshot.extra.get(key):
                value = str(snapshot.extra[key])
                break
            for fact in snapshot.facts:
                if fact.fields.get(key):
                    value = str(fact.fields[key])
                    break
            if value:
                break
        values[name] = value
    values["birth_time_source"] = "explicit" if values["birth_time"] else None
    if not values["birth_time"]:
        derived = _derive_birth_time(snapshot)
        if derived:
            values["birth_time"] = derived
            values["birth_time_source"] = "derived_from_admission_minus_pna"
    return values


def _derive_birth_time(snapshot: PatientSnapshot) -> str | None:
    """Derive an approximate birth timestamp from admission time and PNA.

    This is an auditable derivation, not a recorded birth time: it is labelled
    ``derived_from_admission_minus_pna`` and remains approximate.
    """
    admission = None
    pna_days = None
    for fact in snapshot.facts:
        for key in ("admissionTime", "admission_time", "入院时间"):
            if fact.fields.get(key):
                admission = str(fact.fields[key])
                break
        if pna_days is None:
            value = fact.fields.get("pna_days")
            if value is not None:
                try:
                    pna_days = float(value)
                except (TypeError, ValueError):
                    pass
        if admission and pna_days is not None:
            break
    if not admission or pna_days is None or pna_days < 0:
        return None
    parsed = _parse_datetime(admission)
    if parsed is None:
        return None
    return (parsed - timedelta(days=pna_days)).isoformat()


def _patient_query(snapshot: PatientSnapshot, evidence: Mapping[str, Any]) -> str:
    parts = ["极低出生体重儿 喂养"]
    weight = _numeric_field(snapshot, "birth_weight_g")
    if weight is not None:
        parts.append(f"出生体重 {weight:g} g")
    if evidence["milk"]["value"]:
        parts.append(str(evidence["milk"]["value"]))
    if evidence["tolerance"]["value"]:
        parts.append(str(evidence["tolerance"]["value"]))
    for item in evidence["documents"]:
        if item["field"] in {"admissionDiagnosis", "dischargeDiagnosis"}:
            parts.append(str(item["text"])[:160])
    return " ".join(parts)


def _rank_recommendations(
    query: str, recommendations: Sequence[GraphRecommendation],
    *, semantic_ranker: SemanticRanker | None,
) -> tuple[list[tuple[GraphRecommendation, float]], dict[str, Any]]:
    if semantic_ranker is not None:
        raw = semantic_ranker(query, recommendations)
        scores = {
            recommendation.recommendation_id: float(raw.get(recommendation.recommendation_id, 0.0))
            for recommendation in recommendations
        }
        ranker = "injected_semantic_ranker"
    else:
        documents = [
            SourceDocument(
                source_id="guideline-graph", document_id=recommendation.recommendation_id,
                kind="guideline_recommendation", title=recommendation.recommendation_id,
                text=recommendation.statement,
                metadata={"chunk_id": recommendation.recommendation_id},
            )
            for recommendation in recommendations
        ]
        plan = QueryPlan(
            original_question=query, variants=(query,), concepts=(),
            source_order=("guideline_graph",), filters={}, rationale=(),
        )
        scored = score_documents(plan, documents)
        scores = {
            recommendation.recommendation_id: scored[
                f"chunk:{recommendation.recommendation_id}"
            ].score
            for recommendation in recommendations
        }
        ranker = "bm25_lexical"
    ordered = sorted(
        zip(recommendations, (scores[item.recommendation_id] for item in recommendations)),
        key=lambda pair: (-pair[1], pair[0].recommendation_id),
    )
    return ordered, {
        "ranker": ranker,
        "query": query,
        "candidates": [
            {"recommendation_id": item.recommendation_id, "score": round(score, 6)}
            for item, score in ordered
        ],
        "note": "BM25 是词面检索，不是语义模型；semantic_ranker 槽位可注入嵌入/重排器。",
    }


def _numeric_field(snapshot: PatientSnapshot, field: str) -> float | None:
    for fact in snapshot.facts:
        if field in fact.fields:
            try:
                return float(fact.fields[field])
            except (TypeError, ValueError):
                continue
    return None


def _hours_between(start: str, end: str) -> float | None:
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if start_dt is None or end_dt is None or end_dt < start_dt:
        return None
    return (end_dt - start_dt).total_seconds() / 3600.0


def _days_between(start: str, end: str) -> float | None:
    hours = _hours_between(start, end)
    return None if hours is None else hours / 24.0


def _parse_datetime(value: str) -> datetime | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None)
    except ValueError:
        pass
    parsed_date = parse_iso_date(text)
    if parsed_date is None:
        return None
    return datetime.combine(parsed_date, time.min)


def _optional(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
