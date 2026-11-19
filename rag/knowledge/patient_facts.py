"""Extract a small, auditable fact store from a visible HIS snapshot.

Only structured fields are read.  Free-text narrative and hidden research state
are not parsed into facts; a missing structured field becomes ``unknown`` in
the condition engine rather than a normal value.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from ..core.his import PatientRecord, PatientSnapshot
from .conditions import FactStore

_METRIC_FIELDS = {
    "ph": "ph",
    "paco2": "paco2",
    "pco2": "paco2",
    "pco2_mm_hg": "paco2",
    "pao2": "pao2",
    "po2": "pao2",
    "po2_mm_hg": "pao2",
    "spo2": "spo2",
    "spo2_percent": "spo2",
    "preductal_spo2_percent": "spo2",
    "postductal_spo2_percent": "spo2",
    "wbc": "wbc",
    "wbc_10e9_l": "wbc",
    "platelet": "platelet",
    "platelet_10e9_l": "platelet",
    "hemoglobin": "hemoglobin",
    "hemoglobin_g_l": "hemoglobin",
    "crp": "crp",
    "crp_mg_l": "crp",
    "crp_value": "crp",
    "sodium": "sodium",
    "sodium_mmol_l": "sodium",
    "na": "sodium",
    "total_bilirubin": "total_bilirubin",
    "total_bilirubin_umol_l": "total_bilirubin",
    "max_tbil_umol_l": "total_bilirubin",
    "max_tbil_value": "total_bilirubin",
    "temperature_c": "temperature_c",
    "temperature": "temperature_c",
    "temp_c": "temperature_c",
    "heart_rate": "heart_rate",
    "hr": "heart_rate",
    "respiratory_rate": "respiratory_rate",
    "rr": "respiratory_rate",
    "creatinine": "creatinine",
    "creatinine_umol_l": "creatinine",
    "urea": "urea",
    "urea_mmol_l": "urea",
    "glucose": "glucose",
    "glucose_mmol_l": "glucose",
}
_METRIC_UNITS = {
    "ph": "", "paco2": "mmHg", "pao2": "mmHg", "spo2": "%",
    "wbc": "10^9/L", "platelet": "10^9/L", "hemoglobin": "g/L",
    "crp": "mg/L", "sodium": "mmol/L", "total_bilirubin": "umol/L",
    "temperature_c": "degC", "heart_rate": "bpm", "respiratory_rate": "bpm",
    "creatinine": "umol/L", "urea": "mmol/L", "glucose": "mmol/L",
}

_STATUS_VALUES = {"observed_positive", "observed_negative", "uncertain", "not_recorded", "pending"}


def patient_fact_store(snapshot: PatientSnapshot) -> FactStore:
    store = FactStore(as_of=getattr(snapshot, "as_of", None))
    for fact in snapshot.visible_records():
        _add_fact(store, fact)
    return store


def _record_status(fact: PatientRecord) -> str:
    """Map the record status to the observation status used by conditions."""
    record_status = str(fact.status or "").strip().lower()
    if record_status in {"pending", "not_recorded"}:
        return record_status
    if record_status in {"preliminary", "entered_in_error"}:
        return "uncertain"
    return ""


def _add_fact(store: FactStore, fact: PatientRecord) -> None:
    fields = dict(fact.fields)
    status = str(fields.get("data_status") or _record_status(fact) or _infer_status(fields))
    scope = str(fields.get("temporal_scope") or fields.get("scope") or "current")
    path = fact.record_id
    time = fact.start or fact.event_time
    collection = str(fact.record_type or "")
    if collection in {"birth", "derived_birth"}:
        ga_days = _number(fields.get("ga_days"))
        if ga_days is not None:
            store.add("ga_weeks", ga_days / 7.0, status=status, path=path, time=time)
            store.add("ga_precision", _ga_precision(fields), path=path, time=time)
        else:
            weeks = _number(fields.get("gestationalWeeks"))
            if weeks is not None:
                store.add("ga_weeks", weeks, status=status, path=path, time=time)
                store.add("ga_precision", "weeks_only", path=path, time=time)
            elif fields.get("gestationalAgeRaw"):
                store.add("ga_text", fields["gestationalAgeRaw"], status=status, path=path, time=time)
        weight = _number(fields.get("birth_weight_g"))
        if weight is not None:
            store.add("birth_weight_g", weight, status=status, path=path, time=time, unit="g")
    elif collection == "patient":
        pna_days = _number(fields.get("pna_days"))
        if pna_days is not None:
            store.add("age_hours", pna_days * 24.0, status=status, path=path, time=time, unit="h")
        if fields.get("gender"):
            store.add("gender", fields["gender"], status=status, path=path, time=time)
        setting = str(fields.get("care_setting") or fields.get("setting") or "").strip()
        if setting:
            store.add("care_setting", setting, status=status, path=path, time=time)
    elif collection == "maternal":
        for name in ("gbs_status", "prom_hours", "maternal_fever_c"):
            if name in fields and fields[name] not in (None, ""):
                store.add(name, fields[name], status=status, path=path, time=time)
    elif collection == "diagnoses":
        name = str(fields.get("name") or "").strip()
        if name:
            store.add("diagnosis", name, status=status, path=path, time=time, multi=True)
    elif collection == "respiratory_support":
        mode = str(fields.get("mode") or "").strip()
        if mode and mode not in {"无", "none", "None"}:
            store.add("respiratory_support", mode, status=status, path=path, time=time, multi=True)
            if _is_active(fact):
                store.add("active_treatment", mode, status=status, path=path, time=time, multi=True)
    elif collection in {"labs", "vitals", "derived_labs"}:
        for name, value in fields.items():
            canonical = _METRIC_FIELDS.get(str(name).lower())
            number, qualifier = _number_qualifier(value)
            if canonical and number is not None:
                store.add(canonical, number, status=status, path=path, time=time,
                          unit=_METRIC_UNITS.get(canonical), qualifier=qualifier, scope=scope)
        culture = str(fields.get("culture_status") or fields.get("blood_culture_status") or "").strip()
        if culture:
            store.add("culture_status", culture, status=status, path=path, time=time)
        if culture == "pending":
            store.add("pending_test", "culture", path=path, time=time, multi=True)
    elif collection == "imaging":
        if str(fields.get("report_status") or "").strip() == "pending":
            store.add("pending_test", "imaging", path=path, time=time, multi=True)
    if "clinically_well" in fields:
        store.add("clinically_well", bool(fields["clinically_well"]), path=path, time=time)
    for name in ("symptom", "symptoms"):
        for value in _strings(fields.get(name)):
            store.add("symptom", value, status=status, path=path, time=time, multi=True)


def _infer_status(fields: Mapping[str, Any]) -> str:
    declared = str(fields.get("data_status") or "").strip()
    if declared in _STATUS_VALUES:
        return declared
    if any(str(key).endswith("_status") and str(value).strip() == "pending" for key, value in fields.items()):
        return "pending"
    values = [value for key, value in fields.items() if key not in {"subject_role", "source_paths"}]
    return "observed_positive" if any(value not in (None, "", [], {}) for value in values) else "not_recorded"


def _is_active(fact: PatientRecord) -> bool:
    # The caller has already filtered by as_of; an open-ended support is active.
    return fact.end is None or not str(fact.end).strip()


def _ga_precision(fields: Mapping[str, Any]) -> str:
    explicit = str(fields.get("ga_precision") or "").strip()
    if explicit:
        return explicit
    text = str(fields.get("ga_text") or fields.get("gestationalAgeRaw") or "")
    if text:
        return "day" if ("+" in text or "天" in text) else "weeks_only"
    return "day" if _number(fields.get("ga_days")) is not None else "unknown"


def _number_qualifier(value: Any) -> tuple[float | None, str]:
    """Parse numeric values with detection-limit qualifiers (<, <=, >, >=)."""
    if isinstance(value, bool) or value is None:
        return None, ""
    try:
        return float(value), ""
    except (TypeError, ValueError):
        text = str(value).strip()
        match = re.fullmatch(r"(<=|>=|<|>)?\s*(\d+(?:\.\d*)?|\.\d+)", text)
        if not match:
            return None, ""
        return float(match.group(2)), match.group(1) or ""


def _number(value: Any) -> float | None:
    return _number_qualifier(value)[0]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
