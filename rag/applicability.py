"""Structured guideline applicability criteria checked against patient facts.

A guideline may carry ``metadata.applicability_criteria``: a list of condition
rows such as ``{"field": "ga_days", "operator": "lt", "value": 259}``.  This
module evaluates those rows against the *visible* patient snapshot and returns
one result per criterion plus an overall status.

Hard boundaries:

- only structured, already-curated criteria are evaluated; nothing is inferred
  from free-text guideline recommendations;
- ``not_recorded`` / ``pending`` / ``unavailable`` / ``hypothesis`` values are
  never turned into a negative finding;
- a ``mismatch`` means "a required structured condition contradicts the visible
  record", not "the guideline is clinically inapplicable";
- the legacy ``population_criteria`` mapping keeps its original substring
  semantics when no structured criteria are present.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Mapping, Sequence

from .his import PatientFact, PatientSnapshot
from .models import ApplicabilityAssessment, ApplicabilityCriterionResult, SourceDocument

_UNUSABLE_STATUSES = frozenset({"not_recorded", "pending", "unavailable", "hypothesis"})
_PARTICIPATING_REVIEW_STATUSES = frozenset({"reviewed", "synthetic_fixture"})
_UNARY_OPERATORS = frozenset({"exists"})
_NUMERIC_OPERATORS = frozenset({"gt", "ge", "lt", "le", "between"})
_EQUALITY_OPERATORS = frozenset({"eq", "ne", "in", "not_in"})
_TEXT_OPERATORS = frozenset({"contains", "contains_any", "contains_all"})
_SUPPORTED_OPERATORS = _UNARY_OPERATORS | _NUMERIC_OPERATORS | _EQUALITY_OPERATORS | _TEXT_OPERATORS
_CRITERIA_LOGIC = frozenset({"all_of", "any_of"})
_FIELD_ALIASES = {
    "diagnosis": ("diagnoses", "name"),
    "diagnoses": ("diagnoses", "name"),
    "diagnosis_name": ("diagnoses", "name"),
}
_UNIT_ALIASES = {
    "day": "days", "days": "days", "d": "days",
    "fraction": "fraction", "ratio": "fraction", "1": "fraction",
    "%": "percent", "percent": "percent",
    "mmol/l": "mmol/l", "mg/l": "mg/l", "g/l": "g/l",
    "mmhg": "mmhg", "cmh2o": "cmh2o", "bpm": "bpm", "c": "c", "°c": "°c",
}


@dataclass(frozen=True)
class _Criterion:
    criterion_id: str
    field: str
    operator: str
    expected: tuple[str, ...]
    required: bool = True
    subject_role: str = "patient"
    collection: str | None = None
    unit: str | None = None
    label: str | None = None
    source_locator: str | None = None
    review_status: str = "draft"


def assess_applicability(
    document: SourceDocument,
    snapshot: PatientSnapshot | None,
    patient_hits: Sequence[Any],
    *,
    preview: bool = False,
) -> ApplicabilityAssessment:
    """Assess one guideline document against the visible patient snapshot.

    ``preview=True`` additionally computes a clearly-labelled trial judgment for
    criteria blocked by the review gate (status stays ``unknown`` either way).
    """
    if snapshot is None:
        return ApplicabilityAssessment(document.document_id, "not_assessed", "未提供患者快照。")

    raw = document.metadata.get("applicability_criteria")
    criteria, parse_errors = parse_applicability_criteria(raw)
    if criteria:
        results = tuple(_evaluate(criterion, snapshot, preview=preview) for criterion in criteria)
        return _overall(document.document_id, results, parse_errors, _criteria_logic(raw))
    if raw not in (None, "", [], {}):
        return ApplicabilityAssessment(
            document.document_id, "unknown",
            "指南提供了 applicability_criteria，但无法解析出可用条件；未判定适用性。"
            + (f" 解析问题：{'; '.join(parse_errors)}" if parse_errors else ""),
        )

    legacy = document.metadata.get("population_criteria")
    if isinstance(legacy, Mapping):
        return _legacy_assessment(document, snapshot, patient_hits, legacy)
    return ApplicabilityAssessment(
        document.document_id, "not_assessed",
        "指南未提供结构化适用条件；仅展示原文人群字段。",
    )


def parse_applicability_criteria(value: Any) -> tuple[tuple[_Criterion, ...], tuple[str, ...]]:
    """Parse the curated criteria list; invalid rows are reported, not guessed."""
    if value in (None, "", [], {}):
        return (), ()
    rows = value.get("criteria") if isinstance(value, Mapping) and "criteria" in value else value
    if isinstance(rows, Mapping):
        rows = [rows]
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return (), ("applicability_criteria must be a list of criterion objects",)

    criteria: list[_Criterion] = []
    errors: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            errors.append(f"criterion[{index}] is not an object")
            continue
        field = str(row.get("field") or row.get("name") or "").strip()
        if not field:
            errors.append(f"criterion[{index}] has no field")
            continue
        criterion_id = str(row.get("criterion_id") or row.get("id") or f"c{index + 1}").strip()
        operator = str(row.get("operator") or "eq").strip().lower()
        expected = _expected_values(row.get("value", row.get("values", row.get("expected"))))
        if operator in _NUMERIC_OPERATORS and not expected:
            errors.append(f"criterion[{index}] ({criterion_id}) has no value")
            continue
        source = row.get("source")
        locator = None
        if isinstance(source, Mapping):
            locator = _optional(source.get("locator") or source.get("quote"))
        if locator is None:
            locator = _optional(row.get("locator"))
        criteria.append(_Criterion(
            criterion_id=criterion_id or f"c{index + 1}",
            field=field,
            operator=operator,
            expected=expected,
            required=_boolean(row.get("required", True)),
            subject_role=str(row.get("subject_role") or "patient").strip() or "patient",
            collection=_optional(row.get("collection")),
            unit=_optional(row.get("unit")),
            label=_optional(row.get("label")),
            source_locator=locator,
            review_status=str(row.get("review_status") or "draft").strip().lower() or "draft",
        ))
    return tuple(criteria), tuple(errors)


def _criteria_logic(value: Any) -> str:
    if isinstance(value, Mapping):
        logic = str(value.get("logic") or "all_of").strip().lower()
        if logic in _CRITERIA_LOGIC:
            return logic
    return "all_of"


def _evaluate(criterion: _Criterion, snapshot: PatientSnapshot, *, preview: bool = False) -> ApplicabilityCriterionResult:
    base = {
        "criterion_id": criterion.criterion_id,
        "field": criterion.field,
        "operator": criterion.operator,
        "expected": criterion.expected,
        "required": criterion.required,
        "label": criterion.label,
        "source_locator": criterion.source_locator,
        "review_status": criterion.review_status,
    }
    gate_reason: str | None = None
    if criterion.review_status not in _PARTICIPATING_REVIEW_STATUSES:
        gate_reason = (
            f"条件未审核（{criterion.review_status}），不参与判定；只有已审核条件可以核对。"
        )
    elif not criterion.source_locator:
        gate_reason = "条件缺少原文定位（source.locator/quote），不参与判定。"
    if gate_reason is not None:
        result = ApplicabilityCriterionResult(**base, status="unknown", reason=gate_reason)
        if preview:
            judged = _judge(criterion, snapshot, base)
            result = replace(result, preview={
                "status": judged.status,
                "observed_value": judged.observed_value,
                "patient_record_paths": judged.patient_record_paths,
                "unit": criterion.unit,
                "subject_role": criterion.subject_role,
                "note": "试算预览：按同一比对规则对未参与判定的条件试算，不构成判定；正式结论以人工审核后为准。",
            })
        return result
    return _judge(criterion, snapshot, base)


def _judge(criterion: _Criterion, snapshot: PatientSnapshot, base: Mapping[str, Any]) -> ApplicabilityCriterionResult:
    """The comparison itself (review gates already passed or bypassed for preview)."""
    observed, suppressed = _lookup_values(snapshot.visible_facts(), criterion)
    if criterion.operator not in _SUPPORTED_OPERATORS:
        return ApplicabilityCriterionResult(
            **base, status="unknown",
            reason=f"不支持的操作符 {criterion.operator!r}；未判定。",
        )
    if not observed:
        reason = "患者事实中没有可用的该字段记录。"
        if suppressed:
            reason = "该字段有记录，但状态为未记录/待回报/不可用/假设，或单位不一致；不能作为阴性或阳性证据。"
        return ApplicabilityCriterionResult(**base, status="unknown", reason=reason)

    matched: list[tuple[PatientFact, Any]] = []
    parse_failures = 0
    for fact, value in observed:
        outcome = _satisfies(value, criterion)
        if outcome is None:
            parse_failures += 1
        elif outcome:
            matched.append((fact, value))
    if matched:
        paths = tuple(dict.fromkeys(fact.record_path for fact, _ in matched))
        return ApplicabilityCriterionResult(
            **base, status="match",
            observed_value="; ".join(_render(value) for _, value in matched),
            patient_record_paths=paths,
            reason="结构化条件与可见患者事实匹配；仍需人工核对指南原文和临床条件。",
        )
    if parse_failures == len(observed):
        return ApplicabilityCriterionResult(
            **base, status="unknown",
            observed_value="; ".join(_render(value) for _, value in observed),
            patient_record_paths=tuple(dict.fromkeys(fact.record_path for fact, _ in observed)),
            reason="找到该字段记录，但数值/文本无法按该操作符比较（例如单位或格式不符）；未判定。",
        )
    return ApplicabilityCriterionResult(
        **base, status="mismatch",
        observed_value="; ".join(_render(value) for _, value in observed),
        patient_record_paths=tuple(dict.fromkeys(fact.record_path for fact, _ in observed)),
        reason="可见患者事实中的该字段不满足结构化条件；这是字段核对结果，不是临床不适用结论。",
    )


def _lookup_values(
    facts: Sequence[PatientFact], criterion: _Criterion,
) -> tuple[list[tuple[PatientFact, Any]], int]:
    collections: tuple[str, ...] | None = None
    field_name = criterion.field
    if "." in criterion.field:
        prefix, field_name = criterion.field.rsplit(".", 1)
        collections = (prefix.strip(),) if prefix.strip() else None
    elif criterion.field in _FIELD_ALIASES:
        alias_collection, alias_field = _FIELD_ALIASES[criterion.field]
        collections = (alias_collection,)
        field_name = alias_field
    if criterion.collection:
        collections = (criterion.collection.strip(),)

    observed: list[tuple[PatientFact, Any]] = []
    suppressed = 0
    for fact in facts:
        if collections and fact.collection not in collections:
            continue
        role = str(fact.fields.get("subject_role") or "patient").strip().lower()
        if criterion.subject_role.lower() == "patient":
            if role not in ("", "patient"):
                continue
        elif role != criterion.subject_role.lower():
            continue
        if field_name not in fact.fields:
            continue
        values = fact.fields.get(field_name)
        if values is None:
            suppressed += 1
            continue
        status = str(fact.fields.get("data_status") or "").strip().lower()
        if status in _UNUSABLE_STATUSES:
            suppressed += 1
            continue
        if not _unit_matches(fact, field_name, criterion.unit):
            suppressed += 1
            continue
        if isinstance(values, (list, tuple, set)):
            usable = [item for item in values if item is not None and str(item).strip()]
            if not usable:
                suppressed += 1
            observed.extend((fact, item) for item in usable)
        elif str(values).strip():
            observed.append((fact, values))
        else:
            suppressed += 1
    return observed, suppressed


def _unit_matches(fact: PatientFact, field_name: str, expected_unit: str | None) -> bool:
    if not expected_unit:
        return True
    actual = fact.fields.get(f"{field_name}_unit") or fact.fields.get("unit")
    if actual is None or not str(actual).strip():
        # Missing unit is not a contradiction; numeric comparison may still be meaningful.
        return True
    return _normalize_unit(actual) == _normalize_unit(expected_unit)


def _normalize_unit(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "")
    return _UNIT_ALIASES.get(text, text)


def _satisfies(value: Any, criterion: _Criterion) -> bool | None:
    operator = criterion.operator
    if operator == "exists":
        return True
    if operator in _NUMERIC_OPERATORS:
        return _numeric_satisfies(value, criterion)
    text = str(value).strip().casefold()
    expected = [str(item).strip().casefold() for item in criterion.expected]
    if operator == "eq":
        return any(_equals(value, item) for item in criterion.expected)
    if operator == "ne":
        return all(not _equals(value, item) for item in criterion.expected)
    if operator == "in":
        return any(_equals(value, item) for item in criterion.expected)
    if operator == "not_in":
        return all(not _equals(value, item) for item in criterion.expected)
    if operator == "contains":
        return bool(expected) and expected[0] in text
    if operator == "contains_any":
        return any(item in text for item in expected)
    if operator == "contains_all":
        return bool(expected) and all(item in text for item in expected)
    return None


def _numeric_satisfies(value: Any, criterion: _Criterion) -> bool | None:
    left = _number(value)
    if left is None:
        return None
    right = [_number(item) for item in criterion.expected]
    if any(item is None for item in right):
        return None
    if criterion.operator == "between":
        if len(right) != 2:
            return None
        return right[0] <= left <= right[1]  # type: ignore[operator]
    target = right[0]
    if criterion.operator == "gt":
        return left > target  # type: ignore[operator]
    if criterion.operator == "ge":
        return left >= target  # type: ignore[operator]
    if criterion.operator == "lt":
        return left < target  # type: ignore[operator]
    if criterion.operator == "le":
        return left <= target  # type: ignore[operator]
    return None


def _unknown_reason(results: tuple[ApplicabilityCriterionResult, ...], *, any_of: bool) -> str:
    unknown = [result for result in results if result.status == "unknown"]
    blocked = [
        result for result in unknown
        if result.review_status not in _PARTICIPATING_REVIEW_STATUSES or not result.source_locator
    ]
    if unknown and len(blocked) == len(unknown):
        detail = "部分必需条件未审核或缺少原文定位，未参与判定；不能判定适用。"
    else:
        detail = "部分必需适用条件缺少可用患者事实（未记录/待回报不等于阴性），不能判定适用。"
    return ("any_of 条件下没有匹配项；" if any_of else "") + detail


def _overall(
    document_id: str,
    results: tuple[ApplicabilityCriterionResult, ...],
    parse_errors: tuple[str, ...],
    logic: str = "all_of",
) -> ApplicabilityAssessment:
    required = tuple(result for result in results if result.required)
    decisive = required or results
    matched_fields = tuple(dict.fromkeys(result.field for result in results if result.status == "match"))
    unresolved_fields = tuple(
        dict.fromkeys(result.field for result in results if result.status in ("mismatch", "unknown"))
    )
    paths = tuple(
        dict.fromkeys(
            path for result in results if result.status == "match"
            for path in result.patient_record_paths
        )
    )
    if logic == "any_of":
        if any(result.status == "match" for result in decisive):
            status = "possible_match"
            reason = "任一必需适用条件与可见患者事实匹配（any_of）；仍需人工核对指南原文、时间范围和临床条件。"
        elif decisive and all(result.status == "mismatch" for result in decisive):
            status = "mismatch"
            reason = "全部必需适用条件均与可见患者事实不符（any_of）；这是结构化字段核对结果，不是临床不适用结论。"
        else:
            status = "unknown"
            reason = _unknown_reason(decisive, any_of=True)
    elif any(result.status == "mismatch" for result in decisive):
        status = "mismatch"
        reason = "至少一项必需适用条件与可见患者事实不符；这是结构化字段核对结果，不是临床不适用结论。"
    elif any(result.status == "unknown" for result in decisive):
        status = "unknown"
        reason = _unknown_reason(decisive, any_of=False)
    elif decisive:
        status = "possible_match"
        reason = "必需适用条件均与可见患者事实匹配；仍需人工核对指南原文、时间范围和临床条件。"
    else:
        status = "unknown"
        reason = "没有可评估的适用条件。"
    if parse_errors:
        reason += f" 另有 {len(parse_errors)} 条条件无法解析。"
    return ApplicabilityAssessment(
        document_id, status, reason, matched_fields, unresolved_fields, paths, results,
    )


def _legacy_assessment(
    document: SourceDocument,
    snapshot: PatientSnapshot,
    patient_hits: Sequence[Any],
    criteria: Mapping[str, Any],
) -> ApplicabilityAssessment:
    """Original substring semantics for documents that only carry the old map."""
    matched: list[str] = []
    missing: list[str] = []
    paths: list[str] = []
    visible_text = " ".join(fact.searchable_text().lower() for fact in snapshot.visible_facts())
    for field, expected in criteria.items():
        values = expected if isinstance(expected, (list, tuple, set)) else (expected,)
        if any(str(value).lower() in visible_text for value in values):
            matched.append(str(field))
            paths.extend(hit.record_path for hit in patient_hits if any(str(value).lower() in hit.evidence_text.lower() for value in values))
        else:
            missing.append(str(field))
    if missing and matched:
        status = "unknown"
        reason = "部分适用条件可在患者事实中找到，部分条件缺少结构化记录。"
    elif missing:
        status = "mismatch"
        reason = "未在当前患者事实中找到指南要求的适用条件；这不是临床不适用结论。"
    else:
        status = "possible_match"
        reason = "结构化条件与当前患者事实存在词面匹配；仍需人工核对原文和临床条件。"
    return ApplicabilityAssessment(
        document.document_id, status, reason, tuple(sorted(set(matched))),
        tuple(sorted(set(missing))), tuple(sorted(set(paths))),
    )


def _expected_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),) if str(value).strip() else ()


def _equals(value: Any, expected: str) -> bool:
    left_number = _number(value)
    right_number = _number(expected)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return str(value).strip().casefold() == str(expected).strip().casefold()


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.fullmatch(r"\s*[<>]=?\s*(-?\d+(?:\.\d+)?)\s*", str(value))
    if match:
        return float(match.group(1))
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _render(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def _optional(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
