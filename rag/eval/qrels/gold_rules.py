"""Independent rule gold for the four local guideline criteria.

This module deliberately does not import :mod:`rag.quality.applicability`.  The gold is
computed from the export-derived values with an independent implementation so
that the engine cannot "grade its own exam".

Status vocabulary follows the project contract:

* criterion level: ``match`` / ``mismatch`` / ``unknown``
* guideline level: ``possible_match`` / ``mismatch`` / ``unknown``

``unknown`` is never a negative finding.  ``mismatch`` means a structured
condition contradicts the visible value, not that the guideline is clinically
inapplicable.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "criteria_fixture" / "registry_scope_criteria.json"


def load_criteria_fixture(path: str | Path | None = None) -> dict[str, Any]:
    source = Path(path) if path else FIXTURE_PATH
    payload = json.loads(source.read_text(encoding="utf-8"))
    guidelines = payload.get("guidelines")
    if not isinstance(guidelines, list) or not guidelines:
        raise ValueError("criteria fixture requires a non-empty guidelines list")
    return payload


def criterion_gold(criterion: Mapping[str, Any], derived: Mapping[str, Any]) -> dict[str, Any]:
    criterion_id = str(criterion["criterion_id"])
    field = str(criterion["field"])
    operator = str(criterion["operator"])
    expected = criterion.get("value")
    label = criterion.get("label")
    locator = (criterion.get("source") or {}).get("locator")
    base = {
        "criterion_id": criterion_id,
        "field": field,
        "operator": operator,
        "expected": expected,
        "label": label,
        "locator": locator,
        "review_status": criterion.get("review_status", "draft"),
    }
    if field == "derived_birth.birth_weight_g":
        value = derived.get("birth_weight_g")
        if value is None:
            return {**base, "status": "unknown", "reason_code": "not_recorded",
                    "observed": None, "record_path": None}
        return {**base, **_compare_numeric(float(value), operator, expected),
                "observed": value}
    if field == "derived_birth.ga_days":
        interval = derived.get("ga_days_range")
        if not interval:
            return {**base, "status": "unknown", "reason_code": "not_recorded",
                    "observed": None, "record_path": None}
        low, high = float(interval[0]), float(interval[1])
        status, reason = _compare_interval(low, high, operator, expected)
        return {**base, "status": status, "reason_code": reason,
                "observed": {"range_days": [int(low), int(high)],
                             "precision": derived.get("ga_precision")}}
    if field == "derived_labs.max_tbil_umol_l":
        value = (derived.get("labs") or {}).get("max_tbil_umol_l")
        if value is None:
            return {**base, "status": "unknown", "reason_code": "not_recorded",
                    "observed": None, "record_path": None}
        if operator == "table_lookup":
            return {**base, "status": "unknown", "reason_code": "table_lookup_required",
                    "observed": value}
        return {**base, **_compare_numeric(float(value), operator, expected), "observed": value}
    if field == "derived_respiratory_support.mode":
        resp_status = derived.get("respiratory_status")
        if resp_status == "not_recorded" or resp_status is None:
            return {**base, "status": "unknown", "reason_code": "not_recorded",
                    "observed": None, "record_path": None}
        if resp_status == "observed_negative":
            return {**base, "status": "mismatch", "reason_code": "value_contradicted",
                    "observed": derived.get("respiratory_mode") or "无"}
        mapping = derived.get("respiratory_mapping_status")
        mode = derived.get("respiratory_mode")
        if mapping == "unclassified_support":
            return {**base, "status": "unknown", "reason_code": "unclassified_support",
                    "observed": mode}
        values = expected if isinstance(expected, list) else [expected]
        text = str(mode or "")
        matched = [str(item) for item in values if str(item) in text]
        return {**base,
                "status": "match" if matched else "mismatch",
                "reason_code": "value_satisfied" if matched else "value_contradicted",
                "observed": mode}
    return {**base, "status": "unknown", "reason_code": "unsupported_field",
            "observed": None, "record_path": None}


def guideline_gold(guideline: Mapping[str, Any], derived: Mapping[str, Any]) -> dict[str, Any]:
    results = [criterion_gold(criterion, derived) for criterion in guideline.get("criteria", [])]
    required = [item for item in results if item.get("required", True)]
    decisive = required or results
    if not decisive:
        status = "unknown"
    elif any(item["status"] == "mismatch" for item in decisive):
        status = "mismatch"
    elif any(item["status"] == "unknown" for item in decisive):
        status = "unknown"
    else:
        status = "possible_match"
    return {
        "guideline_id": guideline["document_id"],
        "title": guideline.get("title"),
        "status": status,
        "criteria": results,
        "failed_criteria": [item["criterion_id"] for item in results if item["status"] == "mismatch"],
        "unknown_criteria": [item["criterion_id"] for item in results if item["status"] == "unknown"],
        "missing_fields": sorted({item["field"] for item in results if item["status"] == "unknown"}),
        "reason_codes": sorted({item["reason_code"] for item in results}),
    }


def patient_gold(fixture: Mapping[str, Any], derived: Mapping[str, Any]) -> dict[str, Any]:
    guidelines = [guideline_gold(item, derived) for item in fixture["guidelines"]]
    applicable = [item["guideline_id"] for item in guidelines if item["status"] == "possible_match"]
    mismatched = [item["guideline_id"] for item in guidelines if item["status"] == "mismatch"]
    unknown = [item["guideline_id"] for item in guidelines if item["status"] == "unknown"]
    return {
        "guidelines": guidelines,
        "applicable": applicable,
        "mismatched": mismatched,
        "unknown": unknown,
        "coverage_empty": not applicable,
        "evidence_gap_expected": not applicable,
        "coverage_by_mismatched": {
            item["guideline_id"]: applicable for item in guidelines if item["status"] == "mismatch"
        },
    }


def derived_values_from_row(row: Any) -> dict[str, Any]:
    """Compute the controlled derived values for one :class:`DischargeRow`."""
    from .fact_rules import build_derived_facts

    built = build_derived_facts(
        age_text=row.age_text,
        gestational_age_column=row.gestational_age_raw,
        admission_diagnosis=row.admission_diagnosis,
        discharge_diagnosis=row.discharge_diagnosis,
        presenting_symptoms=row.presenting_symptoms,
        laboratory_findings=row.laboratory_findings,
        special_examinations=row.special_examinations,
        treatment_course=row.treatment_course,
        complications=row.complications,
    )
    ga = built["ga"]
    bw = built["birth_weight"]
    resp = built["respiratory_support"]
    pna = built["pna"]
    extra = ga.extra or {}
    resp_extra = resp.extra or {}
    return {
        "ga_days": extra.get("ga_days"),
        "ga_days_range": extra.get("ga_days_range"),
        "ga_precision": extra.get("ga_precision"),
        "ga_weeks": extra.get("ga_weeks"),
        "ga_extra_days": extra.get("ga_extra_days"),
        "ga_column_missing": extra.get("column_missing"),
        "ga_text_present": extra.get("text_present"),
        "ga_text_conflict": extra.get("ga_text_conflict", False),
        "birth_weight_g": bw.value if bw.status == "observed_positive" else None,
        "birth_weight_status": bw.status,
        "respiratory_status": resp.status,
        "respiratory_mode": resp.value,
        "respiratory_mapping_status": resp_extra.get("mapping_status"),
        "respiratory_categories": resp_extra.get("categories") or [],
        "pna_days": pna.extra.get("pna_days") if pna.extra else None,
        "jaundice_mentioned": bool(built["jaundice"].value),
        "uncertain_diagnosis": built["uncertain_diagnosis"],
        "labs": built["labs"],
        "treatments": {name: item.to_dict() for name, item in built["treatments"].items()},
    }


def _compare_numeric(observed: float, operator: str, expected: Any) -> dict[str, Any]:
    target = float(expected)
    if operator == "lt":
        ok = observed < target
    elif operator == "le":
        ok = observed <= target
    elif operator == "gt":
        ok = observed > target
    elif operator == "ge":
        ok = observed >= target
    elif operator == "between":
        low, high = (float(item) for item in expected)
        ok = low <= observed <= high
    else:
        return {"status": "unknown", "reason_code": "unsupported_operator"}
    return {
        "status": "match" if ok else "mismatch",
        "reason_code": "value_satisfied" if ok else "value_contradicted",
    }


def _compare_interval(low: float, high: float, operator: str, expected: Any) -> tuple[str, str]:
    """Compare an integer-week interval against a numeric threshold."""
    if operator == "lt":
        target = float(expected)
        if high < target:
            return "match", "value_satisfied"
        if low >= target:
            return "mismatch", "value_contradicted"
        return "unknown", "granularity_boundary"
    if operator == "le":
        target = float(expected)
        if high <= target:
            return "match", "value_satisfied"
        if low > target:
            return "mismatch", "value_contradicted"
        return "unknown", "granularity_boundary"
    if operator == "gt":
        target = float(expected)
        if low > target:
            return "match", "value_satisfied"
        if high <= target:
            return "mismatch", "value_contradicted"
        return "unknown", "granularity_boundary"
    if operator == "ge":
        target = float(expected)
        if low >= target:
            return "match", "value_satisfied"
        if high < target:
            return "mismatch", "value_contradicted"
        return "unknown", "granularity_boundary"
    if operator == "between":
        start, end = (float(item) for item in expected)
        if low >= start and high <= end:
            return "match", "value_satisfied"
        if high < start or low > end:
            return "mismatch", "value_contradicted"
        return "unknown", "granularity_boundary"
    return "unknown", "unsupported_operator"
