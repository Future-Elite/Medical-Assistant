"""Rule-based fact extraction from Chinese neonatal discharge summaries.

Every extractor is deterministic and returns a small evidence span in addition
to the derived value.  The span is stored only in the private dataset directory
(the project's ``datasets/`` folder); git-repository artifacts carry
aggregate counts only.

Design choices that matter for the applicability gold:

* ``孕周`` is an integer week or the literal ``无提及孕周``.  An integer week is a
  *range* ``[7w, 7w+6]`` days, not an exact day.  Free text may carry ``孕32+3周``
  and then the exact day is used.
* ``出生体重`` is parsed only from an explicit birth-weight phrase.  Diagnostic
  phrases such as ``出生体重儿(1500-2500g)`` are categories, not measured
  values, and are deliberately not parsed.
* Respiratory support is normalised onto the four labels used by the local
  POCUS scope criterion: 机械通气 / CPAP / 氧疗.  A generic "呼吸支持" mention
  without a type is kept as ``unclassified_support`` rather than being forced
  into one of the four labels.
* A missing mention is ``not_recorded``; only an explicit negation
  (``未行光疗``, ``无呼吸支持``) becomes ``observed_negative``.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import re
from typing import Any

NEGATORS = (
    "未", "无", "否认", "未予", "未行", "暂未", "未用", "未上", "未进行",
    "不需要", "无需", "未使用", "未接受", "未见",
)

_GA_COLUMN_WEEK = re.compile(r"^\s*(\d{1,2})\s*$")
_GA_TEXT_PREFIXED = re.compile(r"(?:孕|胎龄)\s*(\d{1,2})(?:\s*[+＋]\s*(\d))?\s*周")
_GA_TEXT_PLUS = re.compile(r"(\d{1,2})\s*[+＋]\s*(\d)\s*周")

_BIRTH_WEIGHT = re.compile(
    r"(?:出生体重|出生时体重)\s*[:：]?\s*(\d{1,5}(?:\.\d+)?)\s*(kg|KG|千克|克|g|G)?"
)
_AGE_DAYS = re.compile(r"(\d{1,3})\s*(?:天|日)")
_AGE_HOURS = re.compile(r"(\d{1,3})\s*(?:小时|h|H)")

_MECHANICAL = ("机械通气", "气管插管", "有创通气", "有创呼吸机", "SIMV", "呼吸机")
_CPAP = ("CPAP", "NCPAP", "nCPAP", "NIPPV", "无创通气", "无创呼吸机", "无创辅助通气", "HFNC", "BiPAP")
_OXYGEN = ("吸氧", "给氧", "头罩", "鼻导管", "氧疗")
_GENERIC_SUPPORT = ("呼吸支持", "辅助通气")

_JAUNDICE = ("黄疸", "胆红素", "高胆")
_TBIL = re.compile(
    r"(?:总胆红素|血清总胆红素|(?<!直接)(?<!间接)胆红素|TBIL|TSB)\s*[:：]?\s*(\d{1,4}(?:\.\d+)?)\s*"
    r"(μmol/L|umol/L|µmol/L|mg/dL|mg/dl|mg/L|mg/l)?"
)
_CRP = re.compile(r"(?:C\s*反应蛋白|CRP)\s*[:：]?\s*(\d{1,4}(?:\.\d+)?)\s*(mg/L|mg/l)?")

_TREATMENTS: dict[str, tuple[str, ...]] = {
    "phototherapy": ("光疗", "蓝光", "光照疗法"),
    "antibiotics": ("抗生素", "抗菌药物", "抗感染", "头孢", "青霉素", "美罗培南", "万古霉素", "氨苄西林"),
    "parenteral_nutrition": ("静脉营养", "肠外营养", "TPN", "静脉输注营养"),
    "caffeine": ("咖啡因",),
    "surfactant": ("肺表面活性物质", "固尔苏", "珂立苏", "猪肺磷脂"),
    "exchange_transfusion": ("换血",),
    "hearing_screen": ("听力", "耳声发射", "AABR", "OAE"),
}

_EXPLICIT_NEGATIVE_SUPPORT = re.compile(
    r"(?:未|无|未行|未予|未使用|未上|不需要|无需)[^。；;\n]{0,10}"
    r"(?:呼吸支持|机械通气|CPAP|吸氧|氧疗|呼吸机|辅助通气)"
    r"|(?:呼吸支持|机械通气|CPAP|吸氧|氧疗|呼吸机)[^。；;\n]{0,6}"
    r"(?:未行|未用|未上|不需要|无需)"
)

_TBIL_UNIT_TO_UMOL = {"μmol/l": 1.0, "umol/l": 1.0, "µmol/l": 1.0, "mg/dl": 17.1, "mg/l": 0.0113}


@dataclass(frozen=True)
class Extracted:
    value: Any = None
    status: str = "not_recorded"
    reason_code: str | None = None
    evidence_span: str | None = None
    source_path: str | None = None
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["extra"] is None:
            data.pop("extra")
        return data


def evidence_span(text: str, start: int, end: int, *, radius: int = 18) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    prefix = "…" if left else ""
    suffix = "…" if right < len(text) else ""
    return (prefix + text[left:right].replace("\n", " ") + suffix).strip()


def negated(text: str, start: int, *, window: int = 8) -> bool:
    """Return True when a negator sits immediately before ``start``.

    The window is deliberately short: ``未行光疗`` and ``无呼吸支持`` match, while
    a negated sentence elsewhere in the same paragraph does not.
    """
    left = text[max(0, start - window):start]
    return any(token in left for token in NEGATORS)


def _first_positive(text: str, terms: tuple[str, ...]) -> tuple[str, int, int] | None:
    best: tuple[str, int, int] | None = None
    for term in terms:
        for match in re.finditer(re.escape(term), text):
            if negated(text, match.start()):
                continue
            candidate = (term, match.start(), match.end())
            if best is None or candidate[1] < best[1]:
                best = candidate
            break
    return best


def parse_gestational_age(column_value: str, text: str) -> Extracted:
    """Parse gestational age from the column and free text.

    Returns ``ga_days`` (lower bound / exact day), ``ga_days_range``,
    ``ga_precision`` (``week`` / ``day``), ``column_missing`` and
    ``text_present`` in ``extra``.
    """
    column = (column_value or "").strip()
    text_match = _GA_TEXT_PREFIXED.search(text) or _GA_TEXT_PLUS.search(text)
    text_present = text_match is not None
    text_weeks = int(text_match.group(1)) if text_match else None
    text_days = int(text_match.group(2) or 0) if text_match else None

    column_week: int | None = None
    week_match = _GA_COLUMN_WEEK.match(column)
    if week_match:
        value = int(week_match.group(1))
        if 21 <= value <= 45:
            column_week = value

    if column_week is not None:
        if text_match is not None and text_weeks == column_week:
            exact = 7 * column_week + text_days
            return Extracted(
                value=exact, status="observed_positive", reason_code="value_observed",
                evidence_span=evidence_span(text, text_match.start(), text_match.end()),
                extra={
                    "ga_days": exact, "ga_days_range": [exact, exact],
                    "ga_precision": "day", "ga_weeks": column_week, "ga_extra_days": text_days,
                    "column_missing": False, "text_present": True,
                },
            )
        low = 7 * column_week
        return Extracted(
            value=low, status="observed_positive", reason_code="value_observed_week_precision",
            evidence_span=None,
            extra={
                "ga_days": low, "ga_days_range": [low, low + 6],
                "ga_precision": "week", "ga_weeks": column_week, "ga_extra_days": None,
                "column_missing": False, "text_present": text_present,
                "ga_text_conflict": bool(text_present and text_weeks != column_week),
            },
        )
    if text_match is not None:
        exact = 7 * int(text_weeks) + int(text_days or 0)
        return Extracted(
            value=exact, status="observed_positive", reason_code="value_from_free_text",
            evidence_span=evidence_span(text, text_match.start(), text_match.end()),
            extra={
                "ga_days": exact, "ga_days_range": [exact, exact],
                "ga_precision": "day", "ga_weeks": int(text_weeks), "ga_extra_days": int(text_days or 0),
                "column_missing": True, "text_present": True,
            },
        )
    return Extracted(
        value=None, status="not_recorded", reason_code="not_recorded",
        extra={"ga_days": None, "ga_days_range": None, "ga_precision": None,
               "column_missing": True, "text_present": False},
    )


def parse_birth_weight(text: str) -> Extracted:
    match = _BIRTH_WEIGHT.search(text)
    if match is None:
        return Extracted(value=None, status="not_recorded", reason_code="not_recorded")
    raw = float(match.group(1))
    unit = (match.group(2) or "g").lower()
    if unit in ("kg", "千克") or raw < 10:
        raw *= 1000
    value = int(round(raw))
    if not 300 <= value <= 6000:
        return Extracted(
            value=None, status="unknown", reason_code="implausible_value",
            evidence_span=evidence_span(text, match.start(), match.end()),
        )
    return Extracted(
        value=value, status="observed_positive", reason_code="value_observed",
        evidence_span=evidence_span(text, match.start(), match.end()),
        extra={"birth_weight_g": value, "raw_unit": unit or "g"},
    )


def parse_postnatal_age(age_text: str) -> Extracted:
    text = age_text or ""
    hours = _AGE_HOURS.search(text)
    if hours:
        value = int(hours.group(1))
        return Extracted(
            value=value, status="observed_positive", reason_code="value_observed_hours",
            extra={"pna_hours": value, "pna_hours_range": [value, value],
                   "pna_days": value // 24, "pna_precision": "hour"},
        )
    days = _AGE_DAYS.search(text)
    if days:
        value = int(days.group(1))
        return Extracted(
            value=value, status="observed_positive", reason_code="value_observed_days",
            extra={"pna_days": value, "pna_hours_range": [24 * value, 24 * value + 23],
                   "pna_precision": "day"},
        )
    return Extracted(value=None, status="not_recorded", reason_code="not_recorded")


def detect_respiratory_support(text: str) -> Extracted:
    categories: list[tuple[str, tuple[str, int, int]]] = []
    for label, terms in (("机械通气", _MECHANICAL), ("CPAP", _CPAP), ("氧疗", _OXYGEN)):
        hit = _first_positive(text, terms)
        if hit is not None:
            categories.append((label, hit))
    if categories:
        labels = [label for label, _ in categories]
        first = min(categories, key=lambda item: item[1][1])[1]
        return Extracted(
            value="；".join(labels), status="observed_positive", reason_code="value_observed",
            evidence_span=evidence_span(text, first[1], first[2]),
            extra={"mode": "；".join(labels), "mapping_status": "mapped", "categories": labels},
        )
    generic = _first_positive(text, _GENERIC_SUPPORT)
    if generic is not None:
        return Extracted(
            value="呼吸支持（未分型）", status="observed_positive",
            reason_code="unclassified_support",
            evidence_span=evidence_span(text, generic[1], generic[2]),
            extra={"mode": "呼吸支持（未分型）", "mapping_status": "unclassified_support",
                   "categories": []},
        )
    if _EXPLICIT_NEGATIVE_SUPPORT.search(text):
        match = _EXPLICIT_NEGATIVE_SUPPORT.search(text)
        return Extracted(
            value="无", status="observed_negative", reason_code="value_contradicted",
            evidence_span=evidence_span(text, match.start(), match.end()),
            extra={"mode": "无", "mapping_status": "mapped_negative", "categories": []},
        )
    return Extracted(value=None, status="not_recorded", reason_code="not_recorded")


def detect_jaundice(text: str) -> Extracted:
    hit = _first_positive(text, _JAUNDICE)
    if hit is None:
        return Extracted(value=False, status="observed_negative", reason_code="not_mentioned")
    return Extracted(
        value=True, status="observed_positive", reason_code="mentioned",
        evidence_span=evidence_span(text, hit[1], hit[2]),
        extra={"terms": [hit[0]]},
    )


def extract_labs(text: str) -> dict[str, Any]:
    values: list[tuple[float, str]] = []
    span: str | None = None
    for match in _TBIL.finditer(text):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        unit = (match.group(2) or "").strip()
        if not unit:
            continue
        values.append((value, unit))
        if span is None:
            span = evidence_span(text, match.start(), match.end())
    max_value = None
    max_unit = None
    max_umol = None
    if values:
        normalised = []
        for value, unit in values:
            key = unit.lower().replace("µ", "μ")
            factor = _TBIL_UNIT_TO_UMOL.get(key)
            if factor is not None:
                normalised.append((value * factor, value, unit))
        if normalised:
            max_umol, max_value, max_unit = max(normalised, key=lambda item: item[0])
    crp_match = _CRP.search(text)
    crp_value = float(crp_match.group(1)) if crp_match else None
    culture = "not_recorded"
    if "血培养" in text:
        if re.search(r"血培养[^。；;\n]{0,12}(?:待回报|待报|未回|未出)", text):
            culture = "pending"
        elif re.search(r"血培养[^。；;\n]{0,12}(?:阴性|无生长|未见生长)", text):
            culture = "negative"
        elif re.search(r"血培养[^。；;\n]{0,12}(?:阳性|生长)", text):
            culture = "positive"
        else:
            culture = "mentioned"
    return {
        "max_tbil_value": max_value,
        "max_tbil_unit": max_unit,
        "max_tbil_umol_l": max_umol,
        "tbil_evidence_span": span,
        "crp_value": crp_value,
        "crp_unit": "mg/L" if crp_match else None,
        "blood_culture_status": culture,
    }


def detect_hearing_screen(text: str) -> Extracted:
    """Hearing screening needs its own polarity rules.

    ``未见异常`` / ``通过`` are normal screening results, not a negation of the
    screening.  Only explicit "not done" phrases become ``observed_negative``.
    """
    terms = _TREATMENTS["hearing_screen"]
    hit = _first_positive(text, terms)
    if hit is None and not any(term in text for term in terms):
        return Extracted(value=None, status="not_recorded", reason_code="not_recorded")
    positive = re.search(
        r"(?:听力|耳声发射|AABR|OAE|脑干听觉)[^。；;\n]{0,16}"
        r"(?:通过|正常|未见异常|无异常|阴性|达标|已查)",
        text,
    )
    not_done = re.search(
        r"(?:未|未行|未做|未查|拒绝|暂未)[^。；;\n]{0,10}"
        r"(?:听力|耳声发射|AABR|OAE|脑干听觉)",
        text,
    )
    if positive:
        return Extracted(
            value=True, status="observed_positive", reason_code="mentioned",
            evidence_span=evidence_span(text, positive.start(), positive.end()),
        )
    if not_done:
        return Extracted(
            value=False, status="observed_negative", reason_code="negated",
            evidence_span=evidence_span(text, not_done.start(), not_done.end()),
        )
    if hit is not None:
        return Extracted(
            value=True, status="observed_positive", reason_code="mentioned",
            evidence_span=evidence_span(text, hit[1], hit[2]),
        )
    return Extracted(value=None, status="not_recorded", reason_code="not_recorded")


def detect_treatments(text: str) -> dict[str, Extracted]:
    result: dict[str, Extracted] = {}
    for name, terms in _TREATMENTS.items():
        hit = _first_positive(text, terms)
        if hit is not None:
            result[name] = Extracted(
                value=True, status="observed_positive", reason_code="mentioned",
                evidence_span=evidence_span(text, hit[1], hit[2]),
            )
            continue
        negative = None
        for term in terms:
            for match in re.finditer(re.escape(term), text):
                if negated(text, match.start()):
                    negative = (term, match.start(), match.end())
                    break
            if negative:
                break
        if negative:
            result[name] = Extracted(
                value=False, status="observed_negative", reason_code="negated",
                evidence_span=evidence_span(text, negative[1], negative[2]),
            )
        else:
            result[name] = Extracted(value=None, status="not_recorded", reason_code="not_recorded")
    return result


def detect_uncertain_diagnosis(*texts: str) -> dict[str, Any]:
    joined = " ".join(texts)
    marks = [mark for mark in ("？", "?") if mark in joined]
    uncertain_terms = [term for term in ("疑似", "待排", "待查", "可能", "考虑") if term in joined]
    return {
        "uncertain": bool(marks or uncertain_terms),
        "question_marks": len(marks),
        "uncertain_terms": uncertain_terms,
    }


def build_derived_facts(
    *,
    age_text: str,
    gestational_age_column: str,
    admission_diagnosis: str,
    discharge_diagnosis: str,
    presenting_symptoms: str,
    laboratory_findings: str,
    special_examinations: str,
    treatment_course: str,
    complications: str,
) -> dict[str, Any]:
    """Build all derived facts from the free-text columns of one row."""
    summary_text = " ".join(
        part for part in (
            presenting_symptoms, laboratory_findings, special_examinations,
            treatment_course, complications,
        ) if part
    )
    all_text = " ".join(
        part for part in (
            admission_diagnosis, discharge_diagnosis, presenting_symptoms,
            laboratory_findings, special_examinations, treatment_course, complications,
        ) if part
    )
    ga = parse_gestational_age(gestational_age_column, summary_text)
    bw = parse_birth_weight(summary_text)
    pna = parse_postnatal_age(age_text)
    resp = detect_respiratory_support(summary_text)
    jaundice = detect_jaundice(all_text)
    labs = extract_labs(laboratory_findings or summary_text)
    treatment_text = " ".join(part for part in (treatment_course, special_examinations) if part)
    treatments = detect_treatments(treatment_text or summary_text)
    treatments["hearing_screen"] = detect_hearing_screen(
        " ".join(part for part in (special_examinations, treatment_course) if part)
    )
    uncertain = detect_uncertain_diagnosis(admission_diagnosis, discharge_diagnosis)
    return {
        "ga": ga,
        "birth_weight": bw,
        "pna": pna,
        "respiratory_support": resp,
        "jaundice": jaundice,
        "labs": labs,
        "treatments": treatments,
        "uncertain_diagnosis": uncertain,
    }
