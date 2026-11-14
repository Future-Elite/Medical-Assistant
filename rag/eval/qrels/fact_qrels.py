"""Patient-fact qrels derived from one discharge row.

Twelve fact types are emitted (structured + free-text rules), each with three
Chinese question templates and a rule-derived answer.  The qrel row carries an
evidence span; it therefore lives only in the private dataset directory.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from .schema import FACT_STATUSES, LABEL_SOURCE, QREL_SCHEMA

FACT_TYPES = (
    "sex", "pna_days", "ga", "admission_diagnosis", "discharge_diagnosis",
    "outcome", "los_days", "max_tbil", "phototherapy", "antibiotic",
    "respiratory_support_level", "hearing_screen",
)


def build_fact_qrels(record: Mapping[str, Any], templates: Mapping[str, Any]) -> list[dict[str, Any]]:
    snapshot = record["snapshot"]
    derived = record["derived"]
    base = snapshot["extra"]["row_ref"]
    text = record.get("source_text", {})
    rows: list[dict[str, Any]] = []

    def add(
        fact_type: str, status: str, value: Any, *, unit: str | None = None,
        record_path: str | None = None, evidence_span: str | None = None,
        confidence: str = "high", reason_code: str | None = None,
    ) -> None:
        if status not in FACT_STATUSES:
            raise ValueError(f"unsupported fact status: {status}")
        rows.append({
            "schema_version": QREL_SCHEMA,
            "fact_type": fact_type,
            "fact_status": status,
            "answer_value": value,
            "answer_unit": unit,
            "record_path": record_path,
            "evidence_span": evidence_span,
            "label_source": LABEL_SOURCE,
            "confidence": confidence,
            "reason_code": reason_code,
            "query_template_ids": [f"{fact_type}-{index}" for index in (0, 1, 2)],
            "patient_ref": record["patient_ref"],
            "as_of": record["as_of"],
        })

    add("sex", "observed_positive", text.get("sex") or None,
        record_path=f"{base}:patient", confidence="high", reason_code="value_observed")

    pna_days = derived.get("pna_days")
    add(
        "pna_days",
        "observed_positive" if pna_days is not None else "not_recorded",
        pna_days, unit="days", record_path=f"{base}:patient", confidence="high",
        reason_code="value_observed" if pna_days is not None else "not_recorded",
    )

    ga_value = derived.get("ga_days")
    ga_text = _ga_answer(derived)
    add(
        "ga",
        "observed_positive" if ga_value is not None else "not_recorded",
        ga_text, unit="weeks", record_path=f"{base}:derived_birth",
        confidence="high" if derived.get("ga_precision") == "day" else "medium",
        reason_code="value_observed" if ga_value is not None else "not_recorded",
    )

    _add_diagnosis(add, "admission_diagnosis", text.get("admission_diagnosis"), f"{base}:admission")
    _add_diagnosis(add, "discharge_diagnosis", text.get("discharge_diagnosis"), f"{base}:discharge")

    outcome = text.get("outcome")
    add(
        "outcome", "observed_positive" if outcome else "not_recorded", outcome or None,
        record_path=f"{base}:discharge", confidence="high",
        reason_code="value_observed" if outcome else "not_recorded",
    )

    los_days = _length_of_stay(snapshot)
    add(
        "los_days", "observed_positive" if los_days is not None else "not_recorded",
        los_days, unit="days", record_path=f"{base}:admission", confidence="high",
        reason_code="value_observed" if los_days is not None else "not_recorded",
    )

    labs = derived.get("labs") or {}
    tbil_value = labs.get("max_tbil_value")
    tbil_unit = labs.get("max_tbil_unit")
    if tbil_value is None:
        add("max_tbil", "not_recorded", None, record_path=f"{base}:derived_labs",
            confidence="medium", reason_code="not_recorded")
    elif not tbil_unit:
        add("max_tbil", "uncertain", tbil_value, record_path=f"{base}:derived_labs",
            evidence_span=labs.get("tbil_evidence_span"), confidence="medium",
            reason_code="unit_missing")
    else:
        add("max_tbil", "observed_positive", tbil_value, unit=tbil_unit,
            record_path=f"{base}:derived_labs", evidence_span=labs.get("tbil_evidence_span"),
            confidence="medium", reason_code="value_observed")

    _add_treatment(add, "phototherapy", derived, f"{base}:derived_treatment")
    _add_treatment(add, "antibiotic", derived, f"{base}:derived_treatment")
    _add_treatment(add, "hearing_screen", derived, f"{base}:derived_treatment")

    resp_status = derived.get("respiratory_status")
    resp_mode = derived.get("respiratory_mode")
    if resp_status == "observed_positive":
        add("respiratory_support_level", "observed_positive", resp_mode,
            record_path=f"{base}:derived_respiratory_support", confidence="medium",
            reason_code="value_observed")
    elif resp_status == "observed_negative":
        add("respiratory_support_level", "observed_negative", "无",
            record_path=f"{base}:derived_respiratory_support", confidence="medium",
            reason_code="value_contradicted")
    else:
        add("respiratory_support_level", "not_recorded", None,
            record_path=f"{base}:derived_respiratory_support", confidence="medium",
            reason_code="not_recorded")
    return rows


def _add_diagnosis(add: Any, fact_type: str, value: str | None, record_path: str) -> None:
    text = (value or "").strip()
    if not text:
        add(fact_type, "not_recorded", None, record_path=record_path, confidence="high",
            reason_code="not_recorded")
        return
    if "？" in text or "?" in text or "疑似" in text or "待排" in text:
        add(fact_type, "uncertain", text, record_path=record_path, confidence="medium",
            reason_code="uncertain_diagnosis")
    else:
        add(fact_type, "observed_positive", text, record_path=record_path,
            confidence="high", reason_code="value_observed")


def _add_treatment(add: Any, fact_type: str, derived: Mapping[str, Any], record_path: str) -> None:
    lookup = {"antibiotic": "antibiotics"}.get(fact_type, fact_type)
    item = (derived.get("treatments") or {}).get(lookup) or {}
    status = item.get("status") or "not_recorded"
    value = item.get("value")
    add(
        fact_type, status, value, record_path=record_path,
        evidence_span=item.get("evidence_span"), confidence="medium",
        reason_code=item.get("reason_code"),
    )


def _ga_answer(derived: Mapping[str, Any]) -> str | None:
    weeks = derived.get("ga_weeks")
    if weeks is None:
        return None
    days = derived.get("ga_extra_days")
    if days:
        return f"{weeks}周+{days}天"
    return f"{weeks}周"


def _length_of_stay(snapshot: Mapping[str, Any]) -> int | None:
    admission = None
    discharge = None
    for fact in snapshot.get("facts", []):
        fields = fact.get("fields") or {}
        if fact.get("collection") == "admission":
            admission = fields.get("admissionTime")
            discharge = fields.get("dischargeTime")
            break
    if not admission or not discharge:
        return None
    try:
        start = date.fromisoformat(str(admission)[:10])
        end = date.fromisoformat(str(discharge)[:10])
    except ValueError:
        return None
    days = (end - start).days
    return days if days >= 0 else None
