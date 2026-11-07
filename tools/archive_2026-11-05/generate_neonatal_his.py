#!/usr/bin/env python3
"""Generate synthetic neonatal HIS snapshots for the v6.6.5 demo.

Why this generator exists
------------------------
The two upstream mock-data lines do not cover neonates:

* ``origin/his`` (Java/Spring Boot) generates 18--89 year-old outpatients from
  MySQL ontology tables (``dept_diagnosis`` / ``diagnosis_drug`` /
  ``diagnosis_lab`` / ``drug_dose``).  The ontology has a 儿科 department but
  no neonatal department, and the patient generator never goes below 18 years.
* ``origin/hisdata`` (Python + Synthea) is an IR -> GMF -> FHIR -> 8 CSV
  pipeline.  Its only clinical-path IR is adult T2DM, and running it needs a
  Synthea source tree plus JDK 21 and five Java patches.

This script keeps the useful discipline from both lines (deterministic seed,
source-bound clinical facts, stable row order, empty-not-zero for unavailable
fields, synthetic markers) and emits data that v6.6.5 already understands:

* ``neonatal_cases.json`` -- same profile/schema as ``rag/demo/neonatal_cases.json``
  (snapshot + preset), loadable with ``rag.demo.load_demo_cases(<dir>)``.
* ``snapshots.json`` -- raw snapshots for ``POST /api/his/snapshot``.
* ``requests.json`` -- complete ``RetrievalRequest`` payloads (snapshot included)
  ready for ``POST /api/retrieve``.
* ``tables/*.csv`` -- HIS-style relational export, UTF-8 BOM, Chinese headers.

All output is synthetic.  It is not clinical evidence, not a guideline, and
not derived from real patients.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

TZ = timezone(timedelta(hours=8))
PROFILE = "neonatal_demo_v1"
SNAPSHOT_PROFILE = "neonatal_his_snapshots_v1"
REQUEST_PROFILE = "neonatal_his_requests_v1"
DEFAULT_SEED = 20261018
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "rag" / "demo" / "his_generated"

DATA_STATUS_VOCABULARY = [
    "observed_positive",
    "observed_negative",
    "not_recorded",
    "pending",
    "unavailable",
    "hypothesis",
]
SUBJECT_ROLES = ["patient", "mother"]
AGE_RULES = {
    "gestational_age": "ga_days 为出生胎龄总天数，ga_text 保留原始‘周+天’文本；不得从出生体重反推。",
    "postnatal_age": "pna_hours 为生后小时数；急性呼吸场景至少保留小时。",
    "postmenstrual_age": "PMA = ga_days + floor(pna_hours/24)，由调用方/Agent 派生并标注 derived=true；RAG 只读，不推断。",
    "corrected_age": "随访场景使用，不与 PMA 混用。",
}

# Institution / staff are deliberately generic synthetic values.
INSTITUTION = {
    "机构ID": "INST-NEO-001",
    "机构名称": "示例市新生儿医学中心",
    "等级": "三级甲等（合成）",
    "类型": "专科医院（合成）",
    "地址": "示例市示例区示例路1号",
    "区县": "示例区",
    "电话": "000-00000000",
}
DOCTORS = (
    {"医生ID": "DOC-NEO-001", "姓名": "李建华", "性别": "男", "职称": "主任医师", "科室": "新生儿科", "所属机构ID": INSTITUTION["机构ID"]},
    {"医生ID": "DOC-NEO-002", "姓名": "杨文博", "性别": "女", "职称": "主治医师", "科室": "新生儿科", "所属机构ID": INSTITUTION["机构ID"]},
    {"医生ID": "DOC-NEO-003", "姓名": "王芳", "性别": "女", "职称": "住院医师", "科室": "新生儿科", "所属机构ID": INSTITUTION["机构ID"]},
)
SURNAMES = ("王", "李", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴")
GIVEN_NAMES = ("子涵", "一诺", "欣怡", "梓萱", "浩然", "宇轩", "沐辰", "若溪", "安然", "可欣")

#: Lab display names.  Codes are demonstration-only; the "待复核" marker is
#: deliberate (the hisdata branch uses the same convention for unverified codes).
LAB_DISPLAY: dict[str, tuple[str, str]] = {
    "crp_mg_l": ("1988-5（LOINC，待复核）", "C反应蛋白"),
    "wbc_10e9_l": ("6690-2（LOINC，待复核）", "白细胞计数"),
    "platelets_10e9_l": ("777-3（LOINC，待复核）", "血小板计数"),
    "glucose_mmol_l": ("2345-7（LOINC，待复核）", "血糖"),
    "total_bilirubin_umol_l": ("1975-2（LOINC，待复核）", "总胆红素"),
    "direct_bilirubin_umol_l": ("1968-7（LOINC，待复核）", "直接胆红素"),
    "hemoglobin_g_l": ("718-7（LOINC，待复核）", "血红蛋白"),
    "hematocrit": ("4544-3（LOINC，待复核）", "红细胞压积"),
    "reticulocyte": ("1788-2（LOINC，待复核）", "网织红细胞"),
    "coombs": ("LOCAL_ZH", "Coombs试验"),
    "blood_type": ("LOCAL_ZH", "血型"),
    "culture": ("LOCAL_ZH", "血培养"),
}
MED_CODES: dict[str, str] = {
    "caffeine citrate": "N06BC01（ATC，待复核）",
    "pulmonary surfactant": "R07AA02（ATC，待复核）",
    "ampicillin": "J01CA01（ATC，待复核）",
    "gentamicin": "J01GB03（ATC，待复核）",
    "inhaled nitric oxide": "R07AX01（ATC，待复核）",
}

# Numeric jitter: (relative fraction, absolute fraction, lower bound, upper bound).
# Values are deliberately small so a generated case stays inside its clinical
# scenario; they are not a substitute for a physiology model.
JITTER: dict[str, tuple[float, float, float, float]] = {
    "birth_weight_g": (0.08, 0.0, 400, 5000),
    "current_weight_g": (0.03, 0.0, 400, 5000),
    "hr_bpm": (0.04, 2, 90, 220),
    "rr_per_min": (0.06, 1, 20, 110),
    "spo2_percent": (0.0, 2, 70, 100),
    "preductal_spo2_percent": (0.0, 2, 70, 100),
    "postductal_spo2_percent": (0.0, 2, 70, 100),
    "temp_c": (0.0, 0.2, 34.0, 41.0),
    "bp_systolic_mmhg": (0.0, 3, 30, 100),
    "bp_diastolic_mmhg": (0.0, 2, 15, 70),
    "ph": (0.0, 0.02, 6.8, 7.6),
    "pco2_mmhg": (0.0, 3, 20, 100),
    "po2_mmhg": (0.0, 3, 20, 150),
    "hco3_mmol_l": (0.0, 1, 5, 40),
    "base_excess": (0.0, 1, -20, 20),
    "lactate_mmol_l": (0.0, 0.3, 0.5, 15),
    "crp_mg_l": (0.15, 1, 0, 300),
    "wbc_10e9_l": (0.08, 0.5, 1, 50),
    "platelets_10e9_l": (0.08, 10, 20, 600),
    "glucose_mmol_l": (0.04, 0.1, 0.5, 20),
    "total_bilirubin_umol_l": (0.08, 5, 20, 500),
    "direct_bilirubin_umol_l": (0.10, 1, 0, 100),
    "hemoglobin_g_l": (0.04, 5, 50, 250),
    "hematocrit": (0.04, 0.01, 0.15, 0.75),
    "reticulocyte": (0.10, 0.005, 0.0, 0.2),
    "prom_hours": (0.0, 2, 0, 72),
    "maternal_fever_c": (0.0, 0.2, 36.0, 41.0),
}


@dataclass(frozen=True)
class Scenario:
    case_id: str
    theme: str
    base_date: str
    ga_weeks: int | None
    ga_days_extra: int
    birth_weight_g: int
    pna_hours: int
    sex: str
    delivery_mode: str
    multiple_birth: bool
    maternal: Mapping[str, Any]
    diagnoses: tuple[Mapping[str, Any], ...]
    vitals: Mapping[str, Any]
    question: str
    patient_terms: tuple[str, ...]
    english_terms: tuple[str, ...]
    mesh_terms: tuple[str, ...]
    user_terms: Mapping[str, tuple[str, ...]]
    facets: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    filters: Mapping[str, Any] = field(default_factory=dict)
    clinical_intent: str = "clinical_question"
    fidelity_gate: str = "off"
    risk_factors: tuple[Mapping[str, Any], ...] = ()
    respiratory_support: Mapping[str, Any] | None = None
    blood_gas: Mapping[str, Any] | None = None
    imaging: tuple[Mapping[str, Any], ...] = ()
    labs: tuple[Mapping[str, Any], ...] = ()
    medications: tuple[Mapping[str, Any], ...] = ()
    feeding: Mapping[str, Any] | None = None
    therapies: tuple[Mapping[str, Any], ...] = ()
    future_report: bool = True
    pna_jitter_hours: int = 2
    ga_jitter_days: int = 2
    weight_change_pct: float = -0.03
    diagnosis_day: str = "as_of"  # "birth" or "as_of"
    key_phrase: str = ""
    label_suffix: str = ""


def _dx(name: str, code: str) -> dict[str, Any]:
    return {"name": name, "code": code, "code_system": "ICD10_WHO（待复核）", "data_status": "observed_positive"}


def _lab(**fields: Any) -> dict[str, Any]:
    return {"event_type": "measurement", "fields": dict(fields), "data_status": "observed_positive"}


def _culture(**fields: Any) -> dict[str, Any]:
    return {"event_type": "culture", "fields": dict(fields), "data_status": "pending"}


def _med(name: str, **fields: Any) -> dict[str, Any]:
    data = {"name": name, "data_status": "observed_positive"}
    data.update(fields)
    return data


def _imaging(modality: str, impression: str) -> dict[str, Any]:
    return {
        "modality": modality,
        "report_status": "signed",
        "impression": impression,
        "data_status": "observed_positive",
    }


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        case_id="neo-his-rds",
        theme="RDS",
        base_date="2026-03-10",
        ga_weeks=30,
        ga_days_extra=2,
        birth_weight_g=1450,
        pna_hours=6,
        sex="M",
        delivery_mode="cesarean",
        multiple_birth=False,
        maternal={
            "gbs_status": "not_recorded",
            "prom_hours": 18,
            "maternal_fever": False,
            "antibiotics_before_delivery": "ampicillin",
            "data_status": "not_recorded",
        },
        diagnoses=(_dx("新生儿呼吸窘迫综合征", "P22.0"),),
        vitals={"hr_bpm": 162, "rr_per_min": 68, "spo2_percent": 90, "temp_c": 36.5},
        respiratory_support={
            "mode": "CPAP",
            "fio2": "0.30",
            "fio2_unit": "fraction",
            "peep_cmh2o": 6,
            "flow_lpm": 7,
            "ongoing": True,
        },
        blood_gas={
            "sample_type": "capillary",
            "ph": 7.28,
            "pco2_mmhg": 55,
            "po2_mmhg": 42,
            "hco3_mmol_l": 24,
            "base_excess": -2,
            "lactate_mmol_l": 2.1,
        },
        imaging=(_imaging("lung_ultrasound", "双肺B线增多，未见实变"),),
        labs=(
            _lab(crp_mg_l=8, wbc_10e9_l=9.8, glucose_mmol_l=2.8),
            _culture(specimen="blood", culture_status="pending"),
        ),
        medications=(
            _med("caffeine citrate", dose="loading", frequency="q24h", route="IV", ongoing=True),
            _med("pulmonary surfactant", status="not_administered", data_status="observed_negative"),
        ),
        question=(
            "该患儿{ga_phrase}、{pna_phrase}，CPAP下FiO₂ {fio2}，"
            "新生儿呼吸窘迫综合征的肺表面活性物质指征和呼吸支持推荐是什么？"
        ),
        patient_terms=("新生儿呼吸窘迫综合征", "肺表面活性物质", "CPAP"),
        english_terms=("respiratory distress syndrome", "pulmonary surfactant", "CPAP", "preterm infant"),
        mesh_terms=("Respiratory Distress Syndrome, Newborn", "Pulmonary Surfactants"),
        user_terms={
            "新生儿呼吸窘迫综合征": ("respiratory distress syndrome",),
            "肺表面活性物质": ("pulmonary surfactant",),
            "呼吸支持": ("respiratory support",),
        },
        facets={"population": ("早产儿",), "outcome": ("肺表面活性物质指征",)},
        clinical_intent="guideline_recommendation",
        fidelity_gate="audit",
        diagnosis_day="birth",
        key_phrase="CPAP FiO₂ {fio2}",
    ),
    Scenario(
        case_id="neo-his-rds-severe",
        theme="重症 RDS",
        base_date="2026-04-22",
        ga_weeks=27,
        ga_days_extra=5,
        birth_weight_g=980,
        pna_hours=2,
        sex="F",
        delivery_mode="cesarean",
        multiple_birth=False,
        maternal={
            "gbs_status": "not_recorded",
            "prom_hours": 4,
            "maternal_fever": False,
            "antenatal_steroids": True,
            "data_status": "not_recorded",
        },
        diagnoses=(_dx("新生儿呼吸窘迫综合征", "P22.0"),),
        vitals={
            "hr_bpm": 170,
            "rr_per_min": 70,
            "spo2_percent": 88,
            "temp_c": 36.2,
            "bp_systolic_mmhg": 55,
            "bp_diastolic_mmhg": 30,
        },
        respiratory_support={
            "mode": "IMV",
            "fio2": "0.40",
            "fio2_unit": "fraction",
            "peep_cmh2o": 6,
            "rate_per_min": 40,
            "ongoing": True,
        },
        blood_gas={
            "sample_type": "arterial",
            "ph": 7.20,
            "pco2_mmhg": 60,
            "po2_mmhg": 45,
            "hco3_mmol_l": 22,
            "base_excess": -4,
            "lactate_mmol_l": 3.0,
        },
        imaging=(_imaging("chest_xray", "双肺透亮度减低，可见支气管充气征"),),
        labs=(
            _lab(crp_mg_l=12, wbc_10e9_l=12.0, glucose_mmol_l=3.0),
            _culture(specimen="blood", culture_status="pending"),
        ),
        medications=(
            _med("caffeine citrate", dose="maintenance", frequency="q24h", route="IV", ongoing=True),
            _med("pulmonary surfactant", dose="200 mg/kg", route="intratracheal", status="administered", data_status="observed_positive"),
        ),
        question=(
            "该患儿{ga_phrase}、{pna_phrase}，已气管插管机械通气，"
            "新生儿呼吸窘迫综合征的肺表面活性物质和通气策略推荐是什么？"
        ),
        patient_terms=("新生儿呼吸窘迫综合征", "肺表面活性物质", "机械通气"),
        english_terms=("respiratory distress syndrome", "pulmonary surfactant", "mechanical ventilation", "preterm infant"),
        mesh_terms=("Respiratory Distress Syndrome, Newborn", "Pulmonary Surfactants", "Respiration, Artificial"),
        user_terms={
            "新生儿呼吸窘迫综合征": ("respiratory distress syndrome",),
            "肺表面活性物质": ("pulmonary surfactant",),
            "机械通气": ("mechanical ventilation",),
        },
        facets={"population": ("早产儿",), "outcome": ("肺表面活性物质", "通气策略")},
        clinical_intent="guideline_recommendation",
        fidelity_gate="audit",
        diagnosis_day="birth",
        pna_jitter_hours=0,
        key_phrase="IMV FiO₂ {fio2}",
    ),
    Scenario(
        case_id="neo-his-rds-retro",
        theme="RDS 回顾时点",
        base_date="2026-02-25",
        ga_weeks=30,
        ga_days_extra=2,
        birth_weight_g=1450,
        pna_hours=6,
        sex="M",
        delivery_mode="cesarean",
        multiple_birth=False,
        maternal={
            "gbs_status": "not_recorded",
            "prom_hours": 18,
            "maternal_fever": False,
            "antibiotics_before_delivery": "ampicillin",
            "data_status": "not_recorded",
        },
        diagnoses=(_dx("新生儿呼吸窘迫综合征", "P22.0"),),
        vitals={"hr_bpm": 162, "rr_per_min": 68, "spo2_percent": 90, "temp_c": 36.5},
        respiratory_support={
            "mode": "CPAP",
            "fio2": "0.30",
            "fio2_unit": "fraction",
            "peep_cmh2o": 6,
            "flow_lpm": 7,
            "ongoing": True,
        },
        blood_gas={
            "sample_type": "capillary",
            "ph": 7.28,
            "pco2_mmhg": 55,
            "po2_mmhg": 42,
            "hco3_mmol_l": 24,
            "base_excess": -2,
            "lactate_mmol_l": 2.1,
        },
        imaging=(_imaging("lung_ultrasound", "双肺B线增多，未见实变"),),
        labs=(
            _lab(crp_mg_l=8, wbc_10e9_l=9.8, glucose_mmol_l=2.8),
            _culture(specimen="blood", culture_status="pending"),
        ),
        medications=(
            _med("caffeine citrate", dose="loading", frequency="q24h", route="IV", ongoing=True),
            _med("pulmonary surfactant", status="not_administered", data_status="observed_negative"),
        ),
        question=(
            "该患儿{ga_phrase}、{pna_phrase}，CPAP下FiO₂ {fio2}，"
            "新生儿呼吸窘迫综合征的肺表面活性物质指征和呼吸支持推荐是什么？"
        ),
        patient_terms=("新生儿呼吸窘迫综合征", "肺表面活性物质", "CPAP"),
        english_terms=("respiratory distress syndrome", "pulmonary surfactant", "CPAP", "preterm infant"),
        mesh_terms=("Respiratory Distress Syndrome, Newborn", "Pulmonary Surfactants"),
        user_terms={
            "新生儿呼吸窘迫综合征": ("respiratory distress syndrome",),
            "肺表面活性物质": ("pulmonary surfactant",),
            "呼吸支持": ("respiratory support",),
        },
        facets={"population": ("早产儿",), "outcome": ("肺表面活性物质指征",)},
        clinical_intent="guideline_recommendation",
        fidelity_gate="off",
        diagnosis_day="birth",
        key_phrase="早于 RDS 2025 发表",
    ),
    Scenario(
        case_id="neo-his-pneumonia",
        theme="感染性肺炎",
        base_date="2026-09-12",
        ga_weeks=39,
        ga_days_extra=1,
        birth_weight_g=3320,
        pna_hours=36,
        sex="F",
        delivery_mode="vaginal",
        multiple_birth=False,
        maternal={
            "gbs_status": "positive",
            "prom_hours": 26,
            "maternal_fever_c": 38.2,
            "antibiotics_before_delivery": "penicillin",
            "antibiotics_hours_before_delivery": 4,
            "data_status": "observed_positive",
        },
        diagnoses=(_dx("新生儿感染性肺炎", "P23.9"),),
        vitals={
            "temp_c": 38.9,
            "rr_per_min": 72,
            "hr_bpm": 170,
            "spo2_percent": 95,
            "respiratory_support": "room air",
        },
        labs=(
            _lab(crp_mg_l=48, wbc_10e9_l=18.2, platelets_10e9_l=210, glucose_mmol_l=3.2),
            _culture(specimen="blood", culture_status="pending"),
        ),
        imaging=(_imaging("chest_xray", "双肺纹理增粗，右下肺斑片影"),),
        medications=(
            _med("ampicillin", dose="50 mg/kg/dose", frequency="q12h", route="IV", ongoing=True),
            _med("gentamicin", dose="4 mg/kg/dose", frequency="q24h", route="IV", ongoing=True),
        ),
        question=(
            "该患儿{ga_phrase}、{pna_phrase}，发热{temp_c}℃，CRP升高，血培养尚未回报，"
            "新生儿感染性肺炎的诊断与影像检查推荐是什么？"
        ),
        patient_terms=("新生儿感染性肺炎", "CRP"),
        english_terms=("neonatal pneumonia", "chest radiograph", "C-reactive protein"),
        mesh_terms=("Pneumonia, Bacterial", "Infant, Newborn"),
        user_terms={
            "新生儿感染性肺炎": ("neonatal infectious pneumonia",),
            "影像检查": ("imaging",),
        },
        facets={"population": ("新生儿",), "outcome": ("肺炎诊断",)},
        clinical_intent="guideline_recommendation",
        fidelity_gate="audit",
        diagnosis_day="as_of",
        key_phrase="发热{temp_c}℃ · 血培养待回报",
    ),
    Scenario(
        case_id="neo-his-ttn",
        theme="暂时性呼吸增快",
        base_date="2026-08-20",
        ga_weeks=38,
        ga_days_extra=4,
        birth_weight_g=3100,
        pna_hours=8,
        sex="M",
        delivery_mode="vaginal",
        multiple_birth=False,
        maternal={
            "gbs_status": "not_recorded",
            "prom_hours": 2,
            "maternal_fever": False,
            "data_status": "not_recorded",
        },
        diagnoses=(_dx("暂时性呼吸增快", "P22.1"),),
        vitals={
            "rr_per_min": 78,
            "spo2_percent": 94,
            "respiratory_support": "room air",
            "temp_c": 36.8,
        },
        imaging=(_imaging("chest_xray", "肺门纹理增多，叶间裂积液"),),
        labs=(_lab(crp_mg_l=3, wbc_10e9_l=11.2, glucose_mmol_l=3.5),),
        question=(
            "该患儿{ga_phrase}、{pna_phrase}，呼吸增快，胸片提示叶间裂积液，"
            "暂时性呼吸增快的鉴别与处理证据是什么？"
        ),
        patient_terms=("暂时性呼吸增快",),
        english_terms=("transient tachypnea of the newborn", "TTN", "tachypnea"),
        mesh_terms=("Transient Tachypnea of the Newborn",),
        user_terms={
            "暂时性呼吸增快": ("transient tachypnea of the newborn",),
            "湿肺": ("TTN",),
        },
        diagnosis_day="birth",
        key_phrase="胸片叶间裂积液",
    ),
    Scenario(
        case_id="neo-his-jaundice",
        theme="新生儿黄疸",
        base_date="2026-07-18",
        ga_weeks=35,
        ga_days_extra=3,
        birth_weight_g=2400,
        pna_hours=72,
        sex="F",
        delivery_mode="vaginal",
        multiple_birth=False,
        maternal={
            "blood_type": "O+",
            "gbs_status": "not_recorded",
            "prom_hours": 4,
            "data_status": "not_recorded",
        },
        diagnoses=(_dx("新生儿黄疸", "P59.9"),),
        vitals={"hr_bpm": 150, "rr_per_min": 50, "spo2_percent": 97, "temp_c": 36.6},
        labs=(
            _lab(
                total_bilirubin_umol_l=285,
                direct_bilirubin_umol_l=12,
                hemoglobin_g_l=145,
                hematocrit=0.45,
                reticulocyte=0.04,
                coombs="negative",
                blood_type="A+",
            ),
        ),
        therapies=({"name": "phototherapy", "ongoing": True, "data_status": "observed_positive"},),
        question=(
            "该患儿{ga_phrase}、{pna_phrase}，总胆红素{total_bilirubin_umol_l} μmol/L，"
            "新生儿黄疸的光疗指征和胆红素监测推荐是什么？"
        ),
        patient_terms=("新生儿黄疸", "光疗", "总胆红素"),
        english_terms=("neonatal jaundice", "hyperbilirubinemia", "phototherapy", "total bilirubin"),
        mesh_terms=("Jaundice, Neonatal", "Phototherapy"),
        user_terms={
            "新生儿黄疸": ("neonatal jaundice",),
            "光疗": ("phototherapy",),
            "总胆红素": ("total bilirubin",),
        },
        filters={"guideline_version": "2025"},
        clinical_intent="guideline_recommendation",
        diagnosis_day="as_of",
        key_phrase="总胆红素{total_bilirubin_umol_l} μmol/L · 光疗",
    ),
    Scenario(
        case_id="neo-his-pphn",
        theme="PPHN",
        base_date="2026-06-12",
        ga_weeks=39,
        ga_days_extra=0,
        birth_weight_g=3500,
        pna_hours=12,
        sex="M",
        delivery_mode="vaginal",
        multiple_birth=False,
        maternal={
            "gbs_status": "not_recorded",
            "prom_hours": 6,
            "meconium_stained_fluid": True,
            "data_status": "not_recorded",
        },
        diagnoses=(
            _dx("持续性肺动脉高压", "P29.3"),
            _dx("胎粪吸入综合征", "P24.0"),
        ),
        vitals={
            "preductal_spo2_percent": 88,
            "postductal_spo2_percent": 82,
            "bp_systolic_mmhg": 60,
            "bp_diastolic_mmhg": 35,
            "hr_bpm": 170,
        },
        respiratory_support={
            "mode": "IMV",
            "fio2": "1.0",
            "fio2_unit": "fraction",
            "peep_cmh2o": 5,
            "rate_per_min": 40,
            "ongoing": True,
        },
        blood_gas={
            "sample_type": "arterial",
            "ph": 7.25,
            "pco2_mmhg": 48,
            "po2_mmhg": 50,
            "lactate_mmol_l": 4.2,
        },
        imaging=(
            _imaging("echocardiography", "estimated RV pressure systemic; no structural heart disease"),
            _imaging("chest_xray", "双肺斑片影，胎粪吸入改变"),
        ),
        labs=(_lab(crp_mg_l=5, wbc_10e9_l=13.0, glucose_mmol_l=3.8, platelets_10e9_l=180),),
        medications=(
            _med("inhaled nitric oxide", dose="20 ppm", route="inhaled", ongoing=True),
            _med("pulmonary surfactant", dose="200 mg/kg", route="intratracheal", status="administered", data_status="observed_positive"),
        ),
        question=(
            "该患儿{ga_phrase}，胎粪吸入，{pna_phrase}出现持续性肺动脉高压，"
            "吸入一氧化氮和氧合目标的相关证据是什么？"
        ),
        patient_terms=("持续性肺动脉高压", "一氧化氮", "胎粪吸入"),
        english_terms=(
            "persistent pulmonary hypertension of the newborn",
            "PPHN",
            "inhaled nitric oxide",
            "meconium aspiration syndrome",
        ),
        mesh_terms=("Persistent Fetal Circulation Syndrome", "Nitric Oxide"),
        user_terms={
            "持续性肺动脉高压": ("persistent pulmonary hypertension of the newborn",),
            "一氧化氮": ("inhaled nitric oxide",),
            "胎粪吸入综合征": ("meconium aspiration syndrome",),
        },
        facets={"population": ("新生儿",), "outcome": ("氧合",)},
        diagnosis_day="birth",
        key_phrase="iNO 20 ppm · 导管前后 SpO₂ 差异",
    ),
    Scenario(
        case_id="neo-his-bpd",
        theme="BPD",
        base_date="2026-05-20",
        ga_weeks=28,
        ga_days_extra=0,
        birth_weight_g=980,
        pna_hours=1344,
        sex="F",
        delivery_mode="cesarean",
        multiple_birth=False,
        maternal={
            "gbs_status": "not_recorded",
            "prom_hours": 10,
            "antenatal_steroids": True,
            "data_status": "not_recorded",
        },
        diagnoses=(_dx("支气管肺发育不良", "P27.1"),),
        vitals={"hr_bpm": 150, "rr_per_min": 55, "spo2_percent": 93, "temp_c": 36.7},
        respiratory_support={
            "mode": "CPAP",
            "fio2": "0.25",
            "fio2_unit": "fraction",
            "peep_cmh2o": 5,
            "ongoing": True,
        },
        blood_gas={
            "sample_type": "capillary",
            "ph": 7.35,
            "pco2_mmhg": 45,
            "po2_mmhg": 60,
        },
        imaging=(_imaging("chest_xray", "双肺透亮度不均，间质改变"),),
        labs=(_lab(crp_mg_l=2, wbc_10e9_l=9.0, glucose_mmol_l=4.0),),
        medications=(_med("caffeine citrate", dose="maintenance", frequency="q24h", route="IV", ongoing=True),),
        question=(
            "该早产儿{ga_phrase}、{pna_phrase}，经后年龄约{pma_weeks}周仍需呼吸支持，"
            "支气管肺发育不良的定义和评估时点是什么？"
        ),
        patient_terms=("早产儿", "经后年龄", "支气管肺发育不良"),
        english_terms=("bronchopulmonary dysplasia", "BPD", "postmenstrual age", "preterm infant"),
        mesh_terms=("Bronchopulmonary Dysplasia", "Infant, Premature"),
        user_terms={
            "支气管肺发育不良": ("bronchopulmonary dysplasia",),
            "经后年龄": ("postmenstrual age",),
            "早产儿": ("preterm infant",),
        },
        facets={"population": ("早产儿",), "outcome": ("支气管肺发育不良",)},
        clinical_intent="guideline_recommendation",
        diagnosis_day="as_of",
        pna_jitter_hours=12,
        weight_change_pct=0.10,
        key_phrase="经后年龄{pma_weeks}周 · 仍需CPAP",
    ),
    Scenario(
        case_id="neo-his-hypoglycemia",
        theme="新生儿低血糖",
        base_date="2026-04-05",
        ga_weeks=None,
        ga_days_extra=0,
        birth_weight_g=2100,
        pna_hours=2,
        sex="M",
        delivery_mode="vaginal",
        multiple_birth=False,
        maternal={
            "gestational_diabetes": "not_recorded",
            "gbs_status": "not_recorded",
            "prom_hours": 3,
            "data_status": "not_recorded",
        },
        diagnoses=(_dx("新生儿低血糖", "P70.4"),),
        vitals={"temp_c": 36.4, "hr_bpm": 150, "rr_per_min": 50},
        labs=(_lab(glucose_mmol_l=2.1),),
        feeding={"status": "initiated", "data_status": "observed_positive"},
        question=(
            "该低出生体重儿（{ga_phrase}）{pna_phrase}，血糖{glucose_mmol_l} mmol/L，"
            "新生儿低血糖的筛查和喂养推荐是什么？"
        ),
        patient_terms=("低出生体重儿", "新生儿低血糖", "血糖"),
        english_terms=("neonatal hypoglycemia", "blood glucose", "low birth weight infant"),
        mesh_terms=("Hypoglycemia", "Infant, Newborn"),
        user_terms={
            "新生儿低血糖": ("neonatal hypoglycemia",),
            "血糖": ("blood glucose",),
            "低出生体重儿": ("low birth weight infant",),
        },
        clinical_intent="guideline_recommendation",
        diagnosis_day="as_of",
        pna_jitter_hours=0,
        weight_change_pct=-0.01,
        key_phrase="血糖{glucose_mmol_l} mmol/L · 胎龄未记录",
    ),
    Scenario(
        case_id="neo-his-gbs",
        theme="GBS 暴露",
        base_date="2026-10-02",
        ga_weeks=40,
        ga_days_extra=2,
        birth_weight_g=3600,
        pna_hours=12,
        sex="F",
        delivery_mode="vaginal",
        multiple_birth=False,
        maternal={
            "gbs_status": "positive",
            "prom_hours": 20,
            "maternal_fever": False,
            "antibiotics_before_delivery": "penicillin",
            "antibiotics_hours_before_delivery": 4,
            "data_status": "observed_positive",
        },
        diagnoses=(),
        risk_factors=(
            {"name": "maternal group B streptococcus colonisation", "data_status": "observed_positive"},
        ),
        vitals={
            "temp_c": 36.8,
            "hr_bpm": 140,
            "rr_per_min": 48,
            "spo2_percent": 98,
            "respiratory_support": "room air",
        },
        labs=(
            _lab(crp_mg_l=2, wbc_10e9_l=10.5),
            _culture(specimen="blood", culture_status="pending"),
        ),
        question=(
            "母亲B族链球菌阳性、胎膜早破{prom_hours}小时，该无症状新生儿（{ga_phrase}，{pna_phrase}）"
            "是否需要早期感染评估？"
        ),
        patient_terms=("B族链球菌", "胎膜早破", "早期感染"),
        english_terms=("group B streptococcus", "early-onset sepsis", "asymptomatic", "blood culture"),
        mesh_terms=("Streptococcal Infections", "Infant, Newborn", "Sepsis"),
        user_terms={
            "B族链球菌": ("group B streptococcus",),
            "胎膜早破": ("premature rupture of membranes",),
            "早期感染": ("early-onset sepsis",),
        },
        facets={"population": ("无症状新生儿",), "outcome": ("早期感染评估",)},
        diagnosis_day="birth",
        key_phrase="母体GBS阳性 · 无症状 · 血培养待回报",
    ),
    Scenario(
        case_id="neo-his-apnea",
        theme="早产儿呼吸暂停",
        base_date="2026-11-08",
        ga_weeks=31,
        ga_days_extra=0,
        birth_weight_g=1500,
        pna_hours=48,
        sex="M",
        delivery_mode="vaginal",
        multiple_birth=False,
        maternal={
            "gbs_status": "not_recorded",
            "prom_hours": 8,
            "antenatal_steroids": True,
            "data_status": "not_recorded",
        },
        diagnoses=(_dx("早产儿呼吸暂停", "P28.4"),),
        vitals={
            "hr_bpm": 145,
            "rr_per_min": 45,
            "spo2_percent": 95,
            "temp_c": 36.6,
            "apnea_events_24h": 3,
            "respiratory_support": "CPAP",
        },
        respiratory_support={
            "mode": "CPAP",
            "fio2": "0.21",
            "fio2_unit": "fraction",
            "peep_cmh2o": 5,
            "flow_lpm": 6,
            "ongoing": True,
        },
        blood_gas={
            "sample_type": "capillary",
            "ph": 7.30,
            "pco2_mmhg": 52,
            "po2_mmhg": 48,
        },
        labs=(_lab(crp_mg_l=2, wbc_10e9_l=9.5, glucose_mmol_l=3.6),),
        medications=(_med("caffeine citrate", dose="maintenance", frequency="q24h", route="IV", ongoing=True),),
        question=(
            "该早产儿{ga_phrase}、{pna_phrase}，反复呼吸暂停，"
            "咖啡因治疗和呼吸监测的推荐是什么？"
        ),
        patient_terms=("早产儿呼吸暂停", "咖啡因", "呼吸监测"),
        english_terms=("apnea of prematurity", "caffeine citrate", "respiratory monitoring"),
        mesh_terms=("Apnea", "Infant, Premature", "Caffeine"),
        user_terms={
            "早产儿呼吸暂停": ("apnea of prematurity",),
            "咖啡因": ("caffeine citrate",),
            "呼吸监测": ("respiratory monitoring",),
        },
        facets={"population": ("早产儿",), "outcome": ("呼吸暂停",)},
        clinical_intent="guideline_recommendation",
        diagnosis_day="as_of",
        key_phrase="反复呼吸暂停",
    ),
)


#: Display precision for jittered numerics.  Keeping a lab value at four
#: decimals (e.g. 36.5159 °C) reads like a raw float, not a chart value.
DECIMALS: dict[str, int] = {
    "ph": 2,
    "temp_c": 1,
    "glucose_mmol_l": 1,
    "total_bilirubin_umol_l": 0,
    "direct_bilirubin_umol_l": 0,
    "wbc_10e9_l": 1,
    "lactate_mmol_l": 1,
    "hematocrit": 2,
    "reticulocyte": 2,
    "maternal_fever_c": 1,
    "crp_mg_l": 0,
    "platelets_10e9_l": 0,
    "hemoglobin_g_l": 0,
}


def _jitter_value(key: str, value: Any, rng: random.Random) -> Any:
    if key not in JITTER or isinstance(value, bool) or value is None:
        return value
    if not isinstance(value, (int, float)):
        return value
    pct, absolute, low, high = JITTER[key]
    delta = float(value) * pct * rng.uniform(-1.0, 1.0) + absolute * rng.uniform(-1.0, 1.0)
    jittered = float(value) + delta
    jittered = max(low, min(high, jittered))
    if isinstance(value, int) and key not in {"hematocrit", "reticulocyte"}:
        return int(round(jittered))
    decimals = DECIMALS.get(key, 2)
    if decimals == 0:
        return int(round(jittered))
    return round(jittered, decimals)


def _jitter_fields(fields: Mapping[str, Any], rng: random.Random) -> dict[str, Any]:
    return {key: _jitter_value(key, value, rng) for key, value in fields.items()}


def _ga_text(ga_days: int | None) -> str | None:
    if ga_days is None:
        return None
    return f"{ga_days // 7}周+{ga_days % 7}天"


def _pna_phrase(pna_hours: int) -> str:
    if pna_hours < 48:
        return f"生后{pna_hours}小时"
    days = pna_hours // 24
    if days < 14:
        return f"生后{days}天"
    return f"生后{days}天（约{round(days / 7)}周）"


def _fact(
    collection: str,
    event_type: str,
    index: int,
    fields: Mapping[str, Any],
    *,
    time: str | None = None,
    start: str | None = None,
    end: str | None = None,
    field_name: str | None = None,
) -> dict[str, Any]:
    record_path = f"{collection}[{index}]"
    field_path = f"{record_path}.{field_name}" if field_name else record_path
    fact: dict[str, Any] = {
        "collection": collection,
        "event_type": event_type,
        "record_path": record_path,
        "field_path": field_path,
        "fields": dict(fields),
    }
    if time is not None:
        fact["time"] = time
    if start is not None:
        fact["start"] = start
    if end is not None:
        fact["end"] = end
    return fact


def _event_dt(day: date, hour: int, minute: int) -> str:
    return datetime.combine(day, dtime(hour, minute), tzinfo=TZ).isoformat()


def _display_number(value: Any) -> Any:
    if isinstance(value, float):
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return value


def _format_values(
    facts: Iterable[Mapping[str, Any]],
    ga_days: int | None,
    ga_text: str | None,
    pna_hours: int,
) -> dict[str, Any]:
    """Actual generated values used by question/label templates.

    Templates must never quote a hard-coded number: the generator jitters the
    facts, so the question has to be formatted from the facts that were just
    written.  This keeps the narrative and the snapshot consistent.
    """
    values: dict[str, Any] = {
        "ga_phrase": ga_text or "胎龄未记录",
        "pna_phrase": _pna_phrase(pna_hours),
        "pna_hours": pna_hours,
        "fio2": "",
        "temp_c": "",
        "total_bilirubin_umol_l": "",
        "glucose_mmol_l": "",
        "prom_hours": "",
        "pma_weeks": "",
    }
    if ga_days is not None:
        values["pma_weeks"] = int(round((ga_days + pna_hours / 24) / 7))
    for fact in facts:
        fields = fact.get("fields") or {}
        collection = fact.get("collection")
        if collection == "respiratory_support":
            values["fio2"] = fields.get("fio2", "")
        elif collection == "vitals":
            if "temp_c" in fields:
                values["temp_c"] = _display_number(fields["temp_c"])
        elif collection == "labs":
            for key in ("total_bilirubin_umol_l", "glucose_mmol_l"):
                if key in fields and values[key] == "":
                    values[key] = _display_number(fields[key])
        elif collection == "maternal":
            if "prom_hours" in fields:
                values["prom_hours"] = fields["prom_hours"]
    return values


def _term_mapping_rows(scenario: Scenario, question: str) -> list[dict[str, Any]]:
    """Build the concept-bound term_mapping rows used by the v6.6.5 demo."""
    rows: list[dict[str, Any]] = []
    covered: set[str] = set()
    for concept, terms in scenario.user_terms.items():
        values = [str(term).strip() for term in terms if str(term).strip()]
        if not values:
            continue
        rows.append({"concept": concept, "english": values, "mesh": []})
        covered.update(value.casefold() for value in values)
    extras = [
        str(term).strip() for term in scenario.english_terms
        if str(term).strip() and str(term).strip().casefold() not in covered
    ]
    mesh = [str(term).strip() for term in scenario.mesh_terms if str(term).strip()]
    if not rows and (extras or mesh):
        concept = next(
            (str(term) for term in scenario.patient_terms if term and str(term).lower() in question.lower()),
            "",
        )
        if not concept:
            match = re.search(r"[\u4e00-\u9fff]{2,}", question)
            concept = match.group(0) if match else question[:8]
        rows.append({"concept": concept, "english": [], "mesh": []})
    if rows:
        text = " ".join([question, *[str(term) for term in scenario.patient_terms]]).lower()
        target = next(
            (row for row in rows if str(row["concept"]).lower() in text),
            rows[0],
        )
        target["english"] = list(dict.fromkeys([*target["english"], *extras]))
        target["mesh"] = list(dict.fromkeys([*target["mesh"], *mesh]))
    return [row for row in rows if row["english"] or row["mesh"]]


def _build_case(
    scenario: Scenario,
    rng: random.Random,
    variant: int,
    *,
    case_id_override: str | None = None,
    base_date_override: str | None = None,
    patient_ref_override: str | None = None,
) -> dict[str, Any]:
    suffix = "" if variant == 1 else f"-v{variant:02d}"
    case_id = case_id_override or f"{scenario.case_id}{suffix}"
    as_of_date = date.fromisoformat(base_date_override or scenario.base_date)

    if scenario.ga_weeks is None:
        ga_days: int | None = None
    else:
        ga_days = scenario.ga_weeks * 7 + scenario.ga_days_extra
        ga_days += rng.randint(-scenario.ga_jitter_days, scenario.ga_jitter_days)
        ga_days = max(22 * 7, min(42 * 7, ga_days))
    ga_text = _ga_text(ga_days)

    pna_hours = max(1, scenario.pna_hours + rng.randint(-scenario.pna_jitter_hours, scenario.pna_jitter_hours))
    birth_weight_g = int(round(scenario.birth_weight_g * (1.0 + rng.uniform(-0.08, 0.08))))
    birth_weight_g = max(400, min(5000, birth_weight_g))
    current_weight_g = int(round(birth_weight_g * (1.0 + scenario.weight_change_pct + rng.uniform(-0.02, 0.02))))
    current_weight_g = max(400, min(5000, current_weight_g))

    birth_date = as_of_date - timedelta(days=pna_hours // 24)
    birth_time = birth_date.isoformat()
    as_of_time = as_of_date.isoformat()
    diagnosis_time = birth_time if scenario.diagnosis_day == "birth" else as_of_time

    facts: list[dict[str, Any]] = []

    birth_fields: dict[str, Any] = {
        "subject_role": "patient",
        "sex": scenario.sex,
        "ga_days": ga_days,
        "ga_text": ga_text,
        "birth_weight_g": birth_weight_g,
        "delivery_mode": scenario.delivery_mode,
        "multiple_birth": scenario.multiple_birth,
        "data_status": "observed_positive",
    }
    if scenario.maternal.get("meconium_stained_fluid"):
        birth_fields["meconium_stained_fluid"] = True
    facts.append(_fact("birth", "birth", 0, birth_fields, time=birth_time, field_name="ga_days"))

    maternal_fields = _jitter_fields(scenario.maternal, rng)
    maternal_fields.setdefault("subject_role", "mother")
    facts.append(_fact("maternal", "pregnancy", 0, maternal_fields, time=birth_time, field_name="status"))

    for index, diagnosis in enumerate(scenario.diagnoses):
        fields = dict(diagnosis)
        fields.setdefault("subject_role", "patient")
        fields.setdefault("data_status", "observed_positive")
        facts.append(_fact("diagnoses", "diagnosis", index, fields, time=diagnosis_time, field_name="name"))

    for index, risk in enumerate(scenario.risk_factors):
        fields = dict(risk)
        fields.setdefault("subject_role", "patient")
        fields.setdefault("data_status", "observed_positive")
        facts.append(_fact("risk_factors", "risk_factor", index, fields, time=birth_time, field_name="name"))

    facts.append(_fact(
        "postnatal", "measurement", 0,
        {
            "subject_role": "patient",
            "pna_hours": pna_hours,
            "current_weight_g": current_weight_g,
            "data_status": "observed_positive",
        },
        time=as_of_time, field_name="pna_hours",
    ))

    if scenario.respiratory_support:
        support = dict(scenario.respiratory_support)
        support.setdefault("subject_role", "patient")
        support.setdefault("data_status", "observed_positive")
        support["started_event_time"] = _event_dt(birth_date, 4, 30)
        facts.append(_fact(
            "respiratory_support", "support", 0, support,
            start=birth_time, field_name="mode",
        ))

    if scenario.vitals:
        vitals = _jitter_fields(scenario.vitals, rng)
        vitals.setdefault("subject_role", "patient")
        vitals.setdefault("data_status", "observed_positive")
        vitals["event_time"] = _event_dt(as_of_date, 8, 0)
        facts.append(_fact(
            "vitals", "measurement", 0, vitals,
            time=as_of_time, field_name="measured_at",
        ))

    if scenario.blood_gas:
        gas = _jitter_fields(scenario.blood_gas, rng)
        gas.setdefault("subject_role", "patient")
        gas.setdefault("data_status", "observed_positive")
        gas["event_time"] = _event_dt(as_of_date, 7, 30)
        facts.append(_fact(
            "blood_gas", "measurement", 0, gas,
            time=as_of_time, field_name="ph",
        ))

    for index, image in enumerate(scenario.imaging):
        fields = dict(image)
        fields.setdefault("subject_role", "patient")
        fields.setdefault("data_status", "observed_positive")
        facts.append(_fact(
            "imaging", str(fields.get("modality") or "imaging"), index, fields,
            time=as_of_time, field_name="modality",
        ))

    for index, lab in enumerate(scenario.labs):
        event_type = str(lab.get("event_type") or "measurement")
        fields = _jitter_fields(lab.get("fields") or {}, rng)
        fields.setdefault("subject_role", "patient")
        fields.setdefault("data_status", lab.get("data_status", "observed_positive"))
        if event_type == "culture":
            fields.setdefault("culture_status", "pending")
            fields["collected_event_time"] = _event_dt(as_of_date, 5, 0)
        else:
            fields["event_time"] = _event_dt(as_of_date, 6, 0)
        first_key = next(iter(fields))
        facts.append(_fact(
            "labs", event_type, index, fields,
            time=as_of_time, field_name=first_key,
        ))

    for index, medication in enumerate(scenario.medications):
        fields = dict(medication)
        fields.setdefault("subject_role", "patient")
        fields.setdefault("data_status", "observed_positive")
        ongoing = bool(fields.get("ongoing"))
        if ongoing:
            facts.append(_fact(
                "medications", "medication", index, fields,
                start=birth_time, field_name="name",
            ))
        else:
            facts.append(_fact(
                "medications", "medication", index, fields,
                time=as_of_time, field_name="name",
            ))

    if scenario.feeding:
        fields = dict(scenario.feeding)
        fields.setdefault("subject_role", "patient")
        fields.setdefault("data_status", "observed_positive")
        facts.append(_fact(
            "feeding", "breastfeeding", 0, fields,
            start=as_of_time, field_name="status",
        ))

    for index, therapy in enumerate(scenario.therapies):
        fields = dict(therapy)
        fields.setdefault("subject_role", "patient")
        fields.setdefault("data_status", "observed_positive")
        fields["started_event_time"] = _event_dt(as_of_date, 9, 0)
        facts.append(_fact(
            "therapies", "phototherapy", index, fields,
            start=as_of_time, field_name="name",
        ))

    if scenario.future_report:
        future_day = (as_of_date + timedelta(days=5)).isoformat()
        facts.append(_fact(
            "reports", "report", 0,
            {
                "subject_role": "patient",
                "impression": "future record",
                "data_status": "observed_positive",
            },
            time=future_day, field_name="impression",
        ))

    format_values = _format_values(facts, ga_days, ga_text, pna_hours)
    question = scenario.question.format(**format_values)
    key_phrase = scenario.key_phrase.format(**format_values) if scenario.key_phrase else ""
    label_bits = [f"HIS生成 · {scenario.theme}"]
    if ga_text is not None:
        label_bits.append(ga_text)
    label_bits.append(format_values["pna_phrase"])
    if key_phrase:
        label_bits.append(key_phrase)
    label = " · ".join(label_bits) + f"（as_of {as_of_time}）" + scenario.label_suffix

    preset: dict[str, Any] = {
        "question": question,
        "task": "clinical_question",
        "query_language": "zh",
        "clinical_intent": scenario.clinical_intent,
        "patient_terms": list(scenario.patient_terms),
        "term_mapping": _term_mapping_rows(scenario, question),
        "english_terms": list(scenario.english_terms),
        "mesh_terms": list(scenario.mesh_terms),
        "user_terms": {key: list(value) for key, value in scenario.user_terms.items()},
        "facets": {key: list(value) for key, value in scenario.facets.items()},
        "filters": dict(scenario.filters),
        "source_kinds": ["guideline", "pubmed"],
        # 默认走真实联网来源：新生儿指南元数据 + Europe PMC 指南/同行评议全文 + PubMed。
        # 演示块（custom）仍需显式加载，不再是病例的默认来源。
        "source_mode": "live",
        "retrieval_method": "bm25",
        "rerank": False,
        "fidelity_gate": scenario.fidelity_gate,
        "top_k": 6,
        "as_of": as_of_time,
    }

    patient_ref = patient_ref_override or f"neo-his-{scenario.case_id.split('-', 2)[-1]}{suffix}-001"
    snapshot = {
        "patient_ref": patient_ref,
        "as_of": as_of_time,
        "source": "synthetic_neonatal_his",
        "reviewed_external_terms": list(scenario.english_terms),
        "facts": facts,
    }
    return {
        "case_id": case_id,
        "label": label,
        "synthetic": True,
        "snapshot": snapshot,
        "preset": preset,
    }


def _build_payload(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "profile": PROFILE,
        "synthetic": True,
        "note": (
            "HIS 风格合成新生儿数据；由 tools/generate_neonatal_his.py 确定性生成，"
            "仅用于界面、契约与流程测试，不是真实患者，也不构成诊疗建议。"
        ),
        "age_rules": AGE_RULES,
        "data_status_vocabulary": DATA_STATUS_VOCABULARY,
        "subject_roles": SUBJECT_ROLES,
        "cases": cases,
    }


def _build_snapshots(cases: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return {
        "profile": SNAPSHOT_PROFILE,
        "synthetic": True,
        "note": "可直接 POST /api/his/snapshot 的合成新生儿快照；不是真实患者数据。",
        "patients": [case["snapshot"] for case in cases],
    }


def _build_requests(cases: Iterable[dict[str, Any]]) -> dict[str, Any]:
    requests = []
    for case in cases:
        request = dict(case["preset"])
        request["request_id"] = f"req-{case['case_id']}"
        request["patient_snapshot"] = case["snapshot"]
        requests.append({"case_id": case["case_id"], "request": request})
    return {
        "profile": REQUEST_PROFILE,
        "synthetic": True,
        "note": "可直接 POST /api/retrieve 的完整请求；patient_snapshot 为合成数据。",
        "requests": requests,
    }


def _patient_name(case: dict[str, Any], rng: random.Random) -> str:
    return rng.choice(SURNAMES) + rng.choice(GIVEN_NAMES)


def _derive_tables(cases: list[dict[str, Any]], seed: int) -> dict[str, tuple[list[str], list[list[Any]]]]:
    rng = random.Random(seed ^ 0x5EED)
    patients: list[list[Any]] = []
    mothers: list[list[Any]] = []
    visits: list[list[Any]] = []
    diagnoses: list[list[Any]] = []
    medications: list[list[Any]] = []
    labs: list[list[Any]] = []
    vitals: list[list[Any]] = []
    supports: list[list[Any]] = []
    procedures: list[list[Any]] = []

    for case in sorted(cases, key=lambda item: item["case_id"]):
        case_id = case["case_id"]
        snapshot = case["snapshot"]
        patient_ref = snapshot["patient_ref"]
        as_of = snapshot["as_of"]
        facts = snapshot["facts"]
        birth = next(f for f in facts if f["collection"] == "birth")
        maternal = next(f for f in facts if f["collection"] == "maternal")
        postnatal = next(f for f in facts if f["collection"] == "postnatal")
        visit_id = f"VIS-{case_id.upper()}"
        mother_id = f"MOTHER-{case_id.upper()}"
        doctor = DOCTORS[rng.randrange(len(DOCTORS))]
        name = _patient_name(case, rng)

        sex_zh = {"M": "男", "F": "女"}.get(str(birth["fields"].get("sex") or ""), "")
        delivery_zh = {"vaginal": "顺产", "cesarean": "剖宫产"}.get(
            str(birth["fields"].get("delivery_mode") or ""), ""
        )
        patients.append([
            patient_ref,
            name,
            sex_zh,
            birth.get("time") or "",
            birth["fields"].get("ga_days"),
            birth["fields"].get("birth_weight_g"),
            delivery_zh,
            "是" if birth["fields"].get("multiple_birth") else "否",
            mother_id,
            "synthetic_neonatal_his",
            "true",
        ])
        mothers.append([
            mother_id,
            patient_ref,
            maternal["fields"].get("blood_type") or "",
            maternal["fields"].get("gbs_status") or "",
            maternal["fields"].get("prom_hours"),
            maternal["fields"].get("maternal_fever_c", maternal["fields"].get("maternal_fever")),
            maternal["fields"].get("antibiotics_before_delivery") or "",
            maternal["fields"].get("gestational_diabetes") or "",
            "synthetic_neonatal_his",
            "true",
        ])
        visits.append([
            visit_id,
            patient_ref,
            "住院",
            "新生儿科",
            INSTITUTION["机构ID"],
            doctor["医生ID"],
            birth.get("time") or "",
            as_of,
            case["label"],
            "synthetic_neonatal_his",
            "true",
        ])

        for index, fact in enumerate(f for f in facts if f["collection"] == "diagnoses"):
            fields = fact["fields"]
            diagnoses.append([
                f"DX-{case_id.upper()}-{index + 1:02d}",
                visit_id,
                fields.get("code_system") or "ICD10_WHO（待复核）",
                fields.get("code") or "",
                fields.get("name") or "",
                "出院诊断",
                fact.get("time") or as_of,
                "synthetic_neonatal_his",
                "true",
            ])

        for index, fact in enumerate(f for f in facts if f["collection"] == "medications"):
            fields = fact["fields"]
            medications.append([
                f"MED-{case_id.upper()}-{index + 1:02d}",
                visit_id,
                MED_CODES.get(str(fields.get("name") or ""), "LOCAL_ZH（待复核）"),
                fields.get("name") or "",
                fields.get("spec") or "",
                fields.get("dose") or "",
                fields.get("frequency") or "",
                fields.get("route") or "",
                fact.get("start") or fact.get("time") or "",
                "",
                "",
                "",
                "synthetic_neonatal_his",
                "true",
            ])

        lab_meta = {
            "subject_role", "data_status", "event_time", "collected_event_time",
            "culture_status", "specimen",
        }
        for index, fact in enumerate(f for f in facts if f["collection"] == "labs"):
            fields = fact["fields"]
            if fact["event_type"] == "culture":
                labs.append([
                    f"LAB-{case_id.upper()}-{index + 1:02d}",
                    visit_id,
                    LAB_DISPLAY["culture"][0],
                    LAB_DISPLAY["culture"][1],
                    fields.get("culture_status") or "",
                    "",
                    "",
                    "",
                    fields.get("collected_event_time") or "",
                    fact.get("time") or "",
                    "synthetic_neonatal_his",
                    "true",
                ])
                continue
            for analyte_index, (key, value) in enumerate(
                (key, value) for key, value in fields.items() if key not in lab_meta
            ):
                item_code, item_name = LAB_DISPLAY.get(key, ("LOCAL_ZH（待复核）", key))
                labs.append([
                    f"LAB-{case_id.upper()}-{index + 1:02d}-{analyte_index + 1:02d}",
                    visit_id,
                    item_code,
                    item_name,
                    "" if value is None else value,
                    "",
                    "",
                    "",
                    fields.get("event_time") or "",
                    fact.get("time") or "",
                    "synthetic_neonatal_his",
                    "true",
                ])

        for fact in (f for f in facts if f["collection"] == "vitals"):
            fields = fact["fields"]
            vitals.append([
                f"VIT-{case_id.upper()}-01",
                visit_id,
                fields.get("event_time") or fact.get("time") or "",
                fields.get("hr_bpm"),
                fields.get("rr_per_min"),
                fields.get("spo2_percent"),
                fields.get("temp_c"),
                fields.get("bp_systolic_mmhg"),
                fields.get("bp_diastolic_mmhg"),
                "synthetic_neonatal_his",
                "true",
            ])

        for fact in (f for f in facts if f["collection"] == "respiratory_support"):
            fields = fact["fields"]
            supports.append([
                f"SUP-{case_id.upper()}-01",
                visit_id,
                fields.get("mode") or "",
                fields.get("fio2") or "",
                fields.get("peep_cmh2o"),
                fields.get("rate_per_min"),
                fields.get("flow_lpm"),
                fields.get("started_event_time") or fact.get("start") or "",
                fact.get("end") or "",
                "synthetic_neonatal_his",
                "true",
            ])

        for fact in (f for f in facts if f["collection"] == "therapies"):
            fields = fact["fields"]
            procedures.append([
                f"PROC-{case_id.upper()}-01",
                visit_id,
                "LOCAL_ZH（占位，不是正式编码）",
                fields.get("name") or "",
                fields.get("started_event_time") or fact.get("start") or "",
                "synthetic_neonatal_his",
                "true",
            ])

    tables: dict[str, tuple[list[str], list[list[Any]]]] = {
        "患者.csv": (
            ["patient_id", "姓名", "性别", "出生日期", "胎龄天数", "出生体重g", "出生方式", "是否多胎", "母亲ID", "数据来源", "synthetic"],
            patients,
        ),
        "母亲.csv": (
            ["母亲ID", "patient_id", "血型", "GBS状态", "胎膜早破小时", "产前发热", "产前抗生素", "妊娠期糖尿病", "数据来源", "synthetic"],
            mothers,
        ),
        "机构.csv": (
            ["机构ID", "机构名称", "等级", "类型", "地址", "区县", "电话", "数据来源", "synthetic"],
            [[
                INSTITUTION["机构ID"], INSTITUTION["机构名称"], INSTITUTION["等级"], INSTITUTION["类型"],
                INSTITUTION["地址"], INSTITUTION["区县"], INSTITUTION["电话"], "synthetic_neonatal_his", "true",
            ]],
        ),
        "医生.csv": (
            ["医生ID", "姓名", "性别", "职称", "科室", "所属机构ID", "数据来源", "synthetic"],
            [[
                doctor["医生ID"], doctor["姓名"], doctor["性别"], doctor["职称"], doctor["科室"],
                doctor["所属机构ID"], "synthetic_neonatal_his", "true",
            ] for doctor in DOCTORS],
        ),
        "就诊.csv": (
            ["就诊ID", "patient_id", "就诊类型", "科室", "机构ID", "医生ID", "开始时间", "结束时间", "就诊原因", "数据来源", "synthetic"],
            visits,
        ),
        "诊断.csv": (
            ["诊断ID", "就诊ID", "编码体系", "诊断编码", "诊断名称", "诊断类型", "确诊时间", "数据来源", "synthetic"],
            diagnoses,
        ),
        "医嘱_药品.csv": (
            ["医嘱ID", "就诊ID", "药品编码", "药品通用名", "规格", "剂量", "频次", "给药途径", "开始时间", "数量", "单价", "金额", "数据来源", "synthetic"],
            medications,
        ),
        "检验.csv": (
            ["检验ID", "就诊ID", "项目编码", "项目名称", "结果值", "单位", "参考区间", "异常标志", "采样时间", "报告时间", "数据来源", "synthetic"],
            labs,
        ),
        "体征.csv": (
            ["体征ID", "就诊ID", "测量时间", "心率", "呼吸频率", "SpO2", "体温", "收缩压", "舒张压", "数据来源", "synthetic"],
            vitals,
        ),
        "呼吸支持.csv": (
            ["支持ID", "就诊ID", "模式", "FiO2", "PEEP_cmH2O", "频率", "流量", "开始时间", "结束时间", "数据来源", "synthetic"],
            supports,
        ),
        "手术操作.csv": (
            ["操作ID", "就诊ID", "编码", "名称", "时间", "数据来源", "synthetic"],
            procedures,
        ),
    }
    return tables


def _write_csv(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    def cell(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "是" if value else "否"
        return value

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([cell(value) for value in row])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate(out_dir: Path) -> dict[str, Any]:
    """Validate against the real v6.6.5 contracts.  Imports are local so the
    generator still works in a bare Python environment (with a warning)."""
    try:
        from rag.demo import PRESET_FIELDS, load_demo_cases, load_demo_guidelines, register_demo_guidelines
        from rag.his import snapshot_from_dict
        from rag.models import RetrievalRequest
        from rag.custom_sources import CustomSourceLibrary
        from rag.pipeline import EvidenceRetrievalPipeline
    except Exception as exc:  # pragma: no cover - depends on caller's PYTHONPATH
        return {"status": "skipped", "reason": f"cannot import rag: {exc}"}

    payload = load_demo_cases(out_dir)
    cases = payload["cases"]
    assert payload["profile"] == PROFILE
    assert payload["synthetic"] is True
    assert len(cases) == len({case["case_id"] for case in cases})

    future_total = 0
    for case in cases:
        case_id = case["case_id"]
        snapshot_raw = case["snapshot"]
        preset = case["preset"]
        unknown = sorted(set(preset) - PRESET_FIELDS)
        assert not unknown, f"{case_id}: unknown preset fields {unknown}"
        assert "request_id" not in preset and "patient_snapshot" not in preset, case_id
        request = RetrievalRequest.from_dict({**preset, "patient_snapshot": snapshot_raw})
        assert request.patient_snapshot is not None, case_id
        assert request.patient_snapshot.facts, case_id

        snapshot = snapshot_from_dict({"patient_snapshot": snapshot_raw})
        future = 0
        for fact in snapshot_raw["facts"]:
            stamp = fact.get("start") or fact.get("time")
            if stamp and stamp > snapshot_raw["as_of"]:
                future += 1
        assert snapshot.suppressed_future == future, (
            f"{case_id}: suppressed_future={snapshot.suppressed_future} expected {future}"
        )
        future_total += future

        for fact in snapshot_raw["facts"]:
            status = (fact.get("fields") or {}).get("data_status")
            assert status in DATA_STATUS_VOCABULARY, f"{case_id}: bad data_status {status!r}"
        birth = next(fact for fact in snapshot_raw["facts"] if fact["collection"] == "birth")
        ga_days = birth["fields"].get("ga_days")
        ga_text = birth["fields"].get("ga_text")
        if ga_days is not None:
            assert ga_text == f"{ga_days // 7}周+{ga_days % 7}天", f"{case_id}: ga_text mismatch"

    # End-to-end smoke: register the curated demo guidelines and retrieve once.
    # The preset now defaults to source_mode=live; this offline generator has no
    # network, so the smoke request explicitly uses the registered custom library
    # to verify the case payload still parses and retrieves.
    library = CustomSourceLibrary()
    register_demo_guidelines(library)
    pipeline = EvidenceRetrievalPipeline(library.build_registry()[0])
    smoke_case = next(
        (case for case in cases if "呼吸窘迫综合征" in case["preset"]["question"]),
        cases[0],
    )
    package = pipeline.retrieve(RetrievalRequest.from_dict({
        **smoke_case["preset"],
        "source_mode": "custom",
        "patient_snapshot": smoke_case["snapshot"],
    }))
    assert package.citations, "smoke retrieval returned no citations"

    return {
        "status": "ok",
        "cases": len(cases),
        "future_facts_suppressed": future_total,
        "smoke_citations": len(package.citations),
        "guideline_sources": len(load_demo_guidelines()["sources"]),
    }


def _validate_tables(out_dir: Path) -> None:
    tables = out_dir / "tables"
    def read(name: str) -> list[dict[str, str]]:
        with (tables / name).open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    patients = {row["patient_id"] for row in read("患者.csv")}
    visits = {row["就诊ID"]: row for row in read("就诊.csv")}
    institutions = {row["机构ID"] for row in read("机构.csv")}
    doctors = {row["医生ID"] for row in read("医生.csv")}
    assert all(row["patient_id"] in patients for row in read("母亲.csv"))
    assert all(row["patient_id"] in patients for row in visits.values())
    assert all(row["机构ID"] in institutions for row in visits.values())
    assert all(row["医生ID"] in doctors for row in visits.values())
    for name in ("诊断.csv", "医嘱_药品.csv", "检验.csv", "体征.csv", "呼吸支持.csv", "手术操作.csv"):
        assert all(row["就诊ID"] in visits for row in read(name)), name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="deterministic seed")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="output directory")
    parser.add_argument("--variants", type=int, default=1, help="cases per scenario (>=1)")
    parser.add_argument("--no-validate", action="store_true", help="skip rag contract validation")
    args = parser.parse_args(argv)
    if args.variants < 1:
        parser.error("--variants must be >= 1")

    rng = random.Random(args.seed)
    cases: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        for variant in range(1, args.variants + 1):
            cases.append(_build_case(scenario, rng, variant))

    out_dir: Path = args.output
    out_dir.mkdir(parents=True, exist_ok=True)
    cases_path = out_dir / "neonatal_cases.json"
    snapshots_path = out_dir / "snapshots.json"
    requests_path = out_dir / "requests.json"
    _write_json(cases_path, _build_payload(cases))
    _write_json(snapshots_path, _build_snapshots(cases))
    _write_json(requests_path, _build_requests(cases))

    tables = _derive_tables(cases, args.seed)
    for name, (headers, rows) in tables.items():
        _write_csv(out_dir / "tables" / name, headers, rows)

    validation: dict[str, Any]
    if args.no_validate:
        validation = {"status": "skipped", "reason": "--no-validate"}
    else:
        validation = _validate(out_dir)
    _validate_tables(out_dir)

    manifest = {
        "profile": "neonatal_his_synthetic_manifest_v1",
        "synthetic": True,
        "seed": args.seed,
        "variants_per_scenario": args.variants,
        "scenario_count": len(SCENARIOS),
        "case_count": len(cases),
        "patient_count": len({case["snapshot"]["patient_ref"] for case in cases}),
        "generator": "tools/generate_neonatal_his.py",
        "source_branches": {
            "his": "origin/his@0e6a2d1 (HIS subtree)",
            "hisdata": "origin/hisdata@e85f469 (HIS data worktree)",
        },
        "files": {
            "cases": {"name": cases_path.name, "sha256": _sha256(cases_path)},
            "snapshots": {"name": snapshots_path.name, "sha256": _sha256(snapshots_path)},
            "requests": {"name": requests_path.name, "sha256": _sha256(requests_path)},
            "tables": sorted(name for name in tables),
        },
        "validation": validation,
        "boundaries": [
            "合成数据，不是真实患者，不构成诊断或治疗建议。",
            "不是 Synthea/FHIR 产物，不声称 Synthea 溯源。",
            "ICD-10/LOINC/ATC 码值仅用于演示，未逐条核对国标/官方版本。",
            "数量/单价/金额留空而不是填 0；空值表示未建模。",
        ],
    }
    _write_json(out_dir / "manifest.json", manifest)

    print(json.dumps({
        "output": str(out_dir),
        "cases": len(cases),
        "patients": manifest["patient_count"],
        "tables": len(tables),
        "validation": validation,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
