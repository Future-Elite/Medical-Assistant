"""Adapt hospital-export rows to the HIS-shaped snapshot used by the RAG engine.

The adapter never keeps the raw admission number in the output.  A reference
is ``hmac-sha256:<16 hex>`` derived from a local salt; the salt is read from
``CHINALLM_PATIENT_SALT`` and is never written to disk by this code.  Note that
``patient_ref`` identifies an **admission** (住院号), not a person: the export
has no person-level master index, so splits are admission-level.

The snapshot shape mirrors ``data/snapshots.json`` (``patient`` /
``admission`` / ``neonatal_record`` / ``neonatal_summary`` plus derived facts),
so the applicability engine can read it without a special path.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import hmac
import re
from typing import Any, Iterable, Mapping, Sequence

from .xlsx_reader import XlsxSheet

SHEET_NAME = "Sheet1"
TEXT_COLUMNS = (
    "入院时主要症状及体征",
    "实验室检查及主要会诊",
    "特殊检验及重要会诊",
    "诊疗过程",
    "合并症",
    "出院时情况",
    "出院医嘱",
    "出院指导",
)
COLUMNS = (
    "性别", "年龄", "住院号", "入院时间", "出院时间", "入院诊断", "出院诊断",
    "入院时主要症状及体征", "实验室检查及主要会诊", "特殊检验及重要会诊",
    "诊疗过程", "合并症", "出院时情况", "出院医嘱", "出院指导", "治疗结果",
    "来源文件", "孕周",
)
_DATETIME_PATTERNS = (
    re.compile(r"^(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})(?:[ T](\d{1,2})[:：](\d{2})(?::(\d{2}))?)?\s*(?:上午|下午)?$"),
    re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})(?:[ T](\d{1,2})[:：](\d{2})(?::(\d{2}))?)?\s*(?:上午|下午)?$"),
    re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2})[:：](\d{2}))?$"),
)


@dataclass(frozen=True)
class DischargeRow:
    row_index: int
    sex: str
    age_text: str
    admission_no: str
    admission_time: str
    discharge_time: str
    admission_diagnosis: str
    discharge_diagnosis: str
    presenting_symptoms: str
    laboratory_findings: str
    special_examinations: str
    treatment_course: str
    complications: str
    discharge_condition: str
    discharge_orders: str
    discharge_guidance: str
    outcome: str
    source_file: str
    gestational_age_raw: str
    row_hash: str

    def text_columns(self) -> str:
        return " ".join(
            part for part in (
                self.presenting_symptoms, self.laboratory_findings,
                self.special_examinations, self.treatment_course,
                self.complications, self.admission_diagnosis, self.discharge_diagnosis,
            ) if part
        )


def rows_from_sheet(sheet: XlsxSheet) -> list[DischargeRow]:
    """Convert a worksheet into typed rows; unknown columns are ignored."""
    header = list(sheet.header)
    missing = [name for name in COLUMNS if name not in header]
    if missing:
        raise ValueError("worksheet is missing columns: " + ", ".join(missing))
    positions = {name: header.index(name) for name in COLUMNS}
    rows: list[DischargeRow] = []
    for offset, raw in enumerate(sheet.rows[1:]):
        values = {name: (raw[positions[name]] if positions[name] < len(raw) else "").strip()
                  for name in COLUMNS}
        if not any(values.values()):
            continue
        row_index = offset + 2  # Excel row number, header is row 1
        digest = hashlib.sha256(
            "\x1f".join(values[name] for name in COLUMNS).encode("utf-8")
        ).hexdigest()
        rows.append(DischargeRow(
            row_index=row_index,
            sex=values["性别"],
            age_text=values["年龄"],
            admission_no=values["住院号"],
            admission_time=values["入院时间"],
            discharge_time=values["出院时间"],
            admission_diagnosis=values["入院诊断"],
            discharge_diagnosis=values["出院诊断"],
            presenting_symptoms=values["入院时主要症状及体征"],
            laboratory_findings=values["实验室检查及主要会诊"],
            special_examinations=values["特殊检验及重要会诊"],
            treatment_course=values["诊疗过程"],
            complications=values["合并症"],
            discharge_condition=values["出院时情况"],
            discharge_orders=values["出院医嘱"],
            discharge_guidance=values["出院指导"],
            outcome=values["治疗结果"],
            source_file=values["来源文件"],
            gestational_age_raw=values["孕周"],
            row_hash=digest,
        ))
    return rows


def dedupe(rows: Sequence[DischargeRow]) -> list[DischargeRow]:
    """Drop exact duplicate rows by whole-row hash, preserving first order."""
    seen: set[str] = set()
    result: list[DischargeRow] = []
    for row in rows:
        if row.row_hash in seen:
            continue
        seen.add(row.row_hash)
        result.append(row)
    return result


def select_patient_rows(rows: Sequence[DischargeRow]) -> list[DischargeRow]:
    """One row per admission number.

    Duplicate admission numbers are re-admissions or repeated exports.  Keep the
    row with the latest discharge time; tie-break on the longest free text.  The
    merge is recorded in the manifest by count only.
    """
    grouped: dict[str, list[DischargeRow]] = {}
    for row in rows:
        grouped.setdefault(row.admission_no, []).append(row)
    selected: list[DischargeRow] = []
    for group in grouped.values():
        group.sort(
            key=lambda row: (
                _sort_datetime(row.discharge_time),
                sum(len(getattr(row, name)) for name in (
                    "presenting_symptoms", "laboratory_findings",
                    "treatment_course", "admission_diagnosis", "discharge_diagnosis",
                )),
            ),
            reverse=True,
        )
        selected.append(group[0])
    selected.sort(key=lambda row: row.admission_no)
    return selected


def patient_ref(admission_no: str, salt: bytes) -> str:
    if not salt:
        raise ValueError("patient salt must not be empty")
    digest = hmac.new(salt, admission_no.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"hmac-sha256:{digest}"


def row_to_snapshot(row: DischargeRow, *, salt: bytes) -> dict[str, Any]:
    """Build the HIS-shaped snapshot for one discharge row.

    Discharge-time facts carry ``start`` equal to the discharge date so that an
    ``as_of`` before discharge suppresses them.  Derived facts carry the
    controlled value plus ``source_paths`` pointing at the export columns.
    """
    from .fact_rules import build_derived_facts

    admission = normalize_datetime(row.admission_time)
    discharge = normalize_datetime(row.discharge_time)
    as_of = (discharge or admission or "1970-01-01")[:10]
    warnings: list[str] = []
    if not admission:
        warnings.append("admission_time_not_parseable")
    if not discharge:
        warnings.append("discharge_time_not_parseable")
    derived = build_derived_facts(
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
    ga = derived["ga"]
    bw = derived["birth_weight"]
    pna = derived["pna"]
    resp = derived["respiratory_support"]
    labs = derived["labs"]
    treatments = derived["treatments"]
    jaundice = derived["jaundice"]
    if ga.extra and ga.extra.get("ga_text_conflict"):
        warnings.append("ga_column_text_conflict")
    if ga.extra and ga.extra.get("column_missing") and ga.extra.get("text_present"):
        warnings.append("ga_column_missing_text_present")
    if bw.reason_code == "implausible_value":
        warnings.append("birth_weight_implausible")

    base = f"xlsx:{SHEET_NAME}:r{row.row_index}"
    facts: list[dict[str, Any]] = []

    def add(
        collection: str, fields: Mapping[str, Any], *, time: str | None,
        start: str | None = None, source_type: str = "patient_record",
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        payload = {
            "collection": collection,
            "event_type": collection.upper(),
            "record_path": f"{base}:{collection}",
            "field_path": f"{base}:{collection}",
            "fields": dict(fields),
            "time": time,
            "start": start,
            "end": None,
            "source_type": source_type,
        }
        if extra:
            payload.update(dict(extra))
        facts.append(payload)

    add("patient", {
        "gender": row.sex,
        "ageText": row.age_text,
        "pna_days": pna.extra.get("pna_days") if pna.extra else None,
        "pna_hours_range": pna.extra.get("pna_hours_range") if pna.extra else None,
        "subject_role": "patient",
        "data_status": pna.status,
    }, time=admission or as_of)
    add("admission", {
        "admissionDiagnosis": row.admission_diagnosis,
        "admissionTime": admission,
        "dischargeTime": discharge,
    }, time=admission or as_of)
    add("discharge", {
        "dischargeDiagnosis": row.discharge_diagnosis,
        "dischargeCondition": row.discharge_condition,
        "dischargeOrders": row.discharge_orders,
        "dischargeGuidance": row.discharge_guidance,
        "treatmentResult": row.outcome,
    }, time=discharge or as_of, start=discharge or as_of)
    add("neonatal_record", {
        "gestationalAgeRaw": row.gestational_age_raw,
        "gestationalWeeks": ga.extra.get("ga_weeks") if ga.extra else None,
        "sourceFile": row.source_file,
    }, time=admission or as_of)
    add("neonatal_summary", {
        "presentingSymptoms": row.presenting_symptoms,
        "laboratoryFindings": row.laboratory_findings,
        "specialExaminations": row.special_examinations,
        "complications": row.complications,
    }, time=admission or as_of)
    add("treatment_course", {
        "treatmentCourse": row.treatment_course,
        "time_precision": "episode",
    }, time=discharge or as_of, start=discharge or as_of)

    birth_fields: dict[str, Any] = {
        "subject_role": "patient",
        "data_status": (
            "observed_positive"
            if ga.status == "observed_positive" or bw.status == "observed_positive"
            else "not_recorded"
        ),
        "derivation": "derived_from_hospital_export",
        "ga_days": ga.extra.get("ga_days") if ga.extra else None,
        "ga_days_range": ga.extra.get("ga_days_range") if ga.extra else None,
        "ga_precision": ga.extra.get("ga_precision") if ga.extra else None,
        "ga_text": _ga_text(ga.extra),
        "birth_weight_g": bw.value if bw.status == "observed_positive" else None,
        "birth_weight_status": bw.status,
        "pna_days": pna.extra.get("pna_days") if pna.extra else None,
        "pna_hours_range": pna.extra.get("pna_hours_range") if pna.extra else None,
        "source_paths": [f"{base}:neonatal_record", f"{base}:neonatal_summary"],
    }
    add("derived_birth", birth_fields, time=admission or as_of,
        source_type="derived_from_hospital_export")

    resp_fields: dict[str, Any] = {
        "subject_role": "patient",
        "data_status": resp.status,
        "derivation": "derived_from_hospital_export",
        "mode": resp.value if resp.status != "not_recorded" else None,
        "mapping_status": resp.extra.get("mapping_status") if resp.extra else None,
        "respiratory_categories": resp.extra.get("categories") if resp.extra else [],
        "source_paths": [f"{base}:treatment_course", f"{base}:neonatal_summary"],
    }
    add("derived_respiratory_support", resp_fields, time=discharge or as_of,
        start=discharge or as_of, source_type="derived_from_hospital_export")

    add("derived_labs", {
        "subject_role": "patient",
        "data_status": "observed_positive" if labs.get("max_tbil_value") is not None else "not_recorded",
        "max_tbil_value": labs.get("max_tbil_value"),
        "max_tbil_unit": labs.get("max_tbil_unit"),
        "max_tbil_umol_l": labs.get("max_tbil_umol_l"),
        "crp_value": labs.get("crp_value"),
        "blood_culture_status": labs.get("blood_culture_status"),
        "jaundice_mentioned": bool(jaundice.value),
        "source_paths": [f"{base}:neonatal_summary"],
    }, time=admission or as_of, source_type="derived_from_hospital_export")

    treatment_fields: dict[str, Any] = {
        "subject_role": "patient",
        "data_status": "observed_positive" if any(
            item.status == "observed_positive" for item in treatments.values()
        ) else "not_recorded",
        "source_paths": [f"{base}:treatment_course"],
    }
    for name, item in treatments.items():
        treatment_fields[name] = item.value
        treatment_fields[f"{name}_status"] = item.status
    add("derived_treatment", treatment_fields, time=discharge or as_of,
        start=discharge or as_of, source_type="derived_from_hospital_export")

    return {
        "patient_ref": patient_ref(row.admission_no, salt),
        "as_of": as_of,
        "source": "hospital_export",
        "reviewed_external_terms": [],
        "suppressed_future": 0,
        "warnings": warnings,
        "facts": facts,
        "extra": {
            "export_sheet": SHEET_NAME,
            "row_ref": base,
            "derived_version": "v672-facts-v1",
        },
    }


def normalize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return None
    for pattern in _DATETIME_PATTERNS:
        match = pattern.match(text)
        if match:
            year, month, day = (int(match.group(index)) for index in (1, 2, 3))
            hour = int(match.group(4) or 0) if match.lastindex and match.lastindex >= 4 else 0
            minute = int(match.group(5) or 0) if match.lastindex and match.lastindex >= 5 else 0
            second = int(match.group(6) or 0) if match.lastindex and match.lastindex >= 6 else 0
            try:
                parsed = datetime(year, month, day, hour, minute, second)
            except ValueError:
                return None
            return parsed.strftime("%Y-%m-%dT%H:%M:%S")
    return None


def _sort_datetime(value: str) -> str:
    normalized = normalize_datetime(value)
    return normalized or ""


def _ga_text(extra: Mapping[str, Any] | None) -> str | None:
    if not extra:
        return None
    weeks = extra.get("ga_weeks")
    days = extra.get("ga_extra_days")
    if weeks is None:
        return None
    if days:
        return f"{weeks}周+{days}天"
    return f"{weeks}周"
