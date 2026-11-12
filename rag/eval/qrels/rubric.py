"""Automatic R1-R8 checks for the v6.7.2 dataset test cases.

The baseline is the deterministic v6.7.2 rule engine (``assess_applicability``)
plus a small response assembler.  The rubric only checks structural
correctness/traceability; it does not score clinical correctness.

Expected behaviour of the rule baseline: it uses the same structured criteria as
the gold and should therefore be close to 100% on status/coverage checks.  That
is a dataset-consistency check, not evidence that a model or the RAG system is
clinically useful.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any, Mapping, Sequence

from ...quality.applicability import assess_applicability
from ...his import snapshot_from_dict
from ...models import SourceDocument

_POPULATION_TERMS = ("新生儿", "早产儿", "极低出生体重儿")
_FIELD_LABELS = {
    "derived_birth.ga_days": "胎龄",
    "derived_birth.birth_weight_g": "出生体重",
    "derived_respiratory_support.mode": "呼吸支持方式",
}
_GA_TEXT = re.compile(
    r"(?:孕|胎龄)\s*(\d{1,2})(?:\s*[+＋]\s*(\d))?\s*周"
    r"|(\d{1,2})\s*[+＋]\s*(\d)\s*周"
)


def build_documents(fixture: Mapping[str, Any]) -> dict[str, SourceDocument]:
    documents: dict[str, SourceDocument] = {}
    for guideline in fixture["guidelines"]:
        documents[guideline["document_id"]] = SourceDocument(
            source_id="local_guideline_fixture",
            document_id=guideline["document_id"],
            kind="guideline",
            title=guideline.get("title") or guideline["document_id"],
            text="",
            pmid=guideline.get("pmid"),
            doi=guideline.get("doi"),
            published_at=guideline.get("published_at"),
            metadata={
                "applicability_criteria": {
                    "logic": guideline.get("logic", "all_of"),
                    "criteria": guideline.get("criteria", []),
                }
            },
        )
    return documents


def run_baseline(
    case: Mapping[str, Any], documents: Mapping[str, SourceDocument],
) -> dict[str, Any]:
    snapshot = snapshot_from_dict(case["snapshot"], default_source="hospital_export")
    assessments = {
        document_id: assess_applicability(document, snapshot, [])
        for document_id, document in documents.items()
    }
    target_id = case["gold"].get("guideline_id")
    target = assessments.get(target_id) if target_id else None
    coverage = [
        document_id for document_id, assessment in assessments.items()
        if assessment.status == "possible_match"
    ]
    failed = _criteria(target, "mismatch") if target else []
    missing = _criteria(target, "unknown") if target else []
    response = {
        "case_id": case["case_id"],
        "case_type": case["case_type"],
        "assessments": {
            document_id: {
                "status": assessment.status,
                "failed_criteria": [item.criterion_id for item in _criteria(assessment, "mismatch")],
                "missing_fields": [item.field for item in _criteria(assessment, "unknown")],
                "criteria": [
                    {
                        "criterion_id": item.criterion_id,
                        "field": item.field,
                        "status": item.status,
                        "observed_value": item.observed_value,
                        "record_paths": list(item.patient_record_paths),
                        "locator": item.source_locator,
                        "reason": item.reason,
                    }
                    for item in assessment.criteria
                ],
            }
            for document_id, assessment in assessments.items()
        },
        "target": {
            "guideline_id": target_id,
            "status": target.status if target else None,
            "failed_criteria": [item.criterion_id for item in failed],
            "missing_fields": [item.field for item in missing],
            "failed_conditions": [
                {
                    "criterion_id": item.criterion_id,
                    "field": item.field,
                    "operator": item.operator,
                    "expected": list(item.expected),
                    "observed": item.observed_value,
                    "record_paths": list(item.patient_record_paths),
                    "locator": item.source_locator,
                }
                for item in failed
            ],
        },
        "coverage": coverage,
        "citations": [
            {"criterion_id": item.criterion_id, "record_path": path, "locator": item.source_locator}
            for item in failed for path in item.patient_record_paths
        ],
        "topic_gap": _topic_gap(case),
        "extracted_ga_days": _extract_ga(case) if case["case_type"] == "GA" else None,
    }
    response["follow_up_query"] = _follow_up_query(case, response)
    response["doctor_summary"] = _doctor_summary(case, response)
    return response


def evaluate(
    cases: Sequence[Mapping[str, Any]], fixture: Mapping[str, Any],
) -> dict[str, Any]:
    documents = build_documents(fixture)
    responses = [run_baseline(case, documents) for case in cases]
    metrics = {
        "case_count": len(cases),
        "R1_target_status": _ratio_metric(
            [(response["target"]["status"], case["gold"]["guideline_status"])
             for response, case in zip(responses, cases)
             if case["case_type"] in ("A", "B", "C")],
        ),
        "R1_all_guidelines": _all_guideline_metric(cases, responses),
        "R1_ga_extraction": _ga_metric(cases, responses),
        "R2_failure_conditions": _failure_metric(cases, responses),
        "R3_query_compliance": _query_metric(cases, responses),
        "R4_coverage": _coverage_metric(cases, responses),
        "R5_no_coverage_correct": _boolean_metric(
            [(_no_coverage_declared(response), case["case_type"] == "B")
             for response, case in zip(responses, cases) if case["case_type"] == "B"],
        ),
        "R5_topic_gap_correct": _boolean_metric(
            [(bool(response["topic_gap"] and response["topic_gap"].get("declared")), True)
             for response, case in zip(responses, cases) if case["case_type"] == "D"],
        ),
        "R6_missing_facts": _missing_metric(cases, responses),
        "R7_traceability": _traceability_metric(cases, responses),
        "R8_doctor_readable": _readability_metric(responses),
    }
    metrics["per_case_type"] = _per_case_type(cases, responses)
    return metrics


def _criteria(assessment: Any, status: str) -> list[Any]:
    return [item for item in assessment.criteria if item.status == status]


def _topic_gap(case: Mapping[str, Any]) -> dict[str, Any] | None:
    if case["case_type"] != "D":
        return None
    return {
        "topic": "hyperbilirubinemia",
        "declared": True,
        "text": "本地语料没有高胆红素血症指南；这是语料缺口，不是患者没有证据。",
    }


def _extract_ga(case: Mapping[str, Any]) -> int | None:
    text = _snapshot_text(case["snapshot"])
    match = _GA_TEXT.search(text)
    if not match:
        return None
    if match.group(1) is not None:
        weeks = int(match.group(1))
        days = int(match.group(2) or 0)
    else:
        weeks = int(match.group(3))
        days = int(match.group(4) or 0)
    return 7 * weeks + days


def _snapshot_text(snapshot: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for fact in snapshot.get("facts", []):
        fields = fact.get("fields") or {}
        for key in (
            "presentingSymptoms", "laboratoryFindings", "specialExaminations",
            "treatmentCourse", "complications",
        ):
            value = fields.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
    return " ".join(parts)


def _population_term(case: Mapping[str, Any]) -> str:
    derived = _derived_birth(case["snapshot"])
    ga_days = derived.get("ga_days")
    bw = derived.get("birth_weight_g")
    if bw is not None and bw < 1500:
        return "极低出生体重儿"
    if ga_days is not None and ga_days < 259:
        return "早产儿"
    return "新生儿"


def _derived_birth(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    for fact in snapshot.get("facts", []):
        if fact.get("collection") == "derived_birth":
            return fact.get("fields") or {}
    return {}


def _follow_up_query(case: Mapping[str, Any], response: Mapping[str, Any]) -> str:
    population = _population_term(case)
    if case["case_type"] == "D":
        return "新生儿 高胆红素血症 指南"
    if case["case_type"] == "GA":
        return "新生儿 胎龄 病历文本 抽取 后核对指南"
    target = response.get("target") or {}
    if target.get("failed_conditions"):
        labels = []
        for condition in target["failed_conditions"]:
            label = _FIELD_LABELS.get(condition["field"], condition["field"])
            expected = "、".join(str(item) for item in condition.get("expected") or [])
            labels.append(f"{label}{_operator_text(condition.get('operator'))}{expected}")
        return f"{population} " + " ".join(labels) + " 指南"
    fields = target.get("missing_fields") or []
    labels = "、".join(_FIELD_LABELS.get(field, field) for field in fields) or "适用条件"
    return f"{population} {labels} 指南"


def _operator_text(operator: str | None) -> str:
    return {
        "lt": "<", "le": "≤", "gt": ">", "ge": "≥", "eq": "=", "ne": "≠",
        "between": "介于", "contains": "包含", "contains_any": "包含任一",
    }.get(str(operator), "")


def _doctor_summary(case: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, str]:
    target = response.get("target") or {}
    if case["case_type"] == "D":
        return {
            "why_not_applicable": "本地四份指南均不是高胆红素血症指南，不能拿营养或 POCUS 证据硬凑。",
            "which_criterion_failed": "无对应指南主题，未做条件判定。",
            "other_guidelines": "本地指南库没有高胆红素血症主题。",
            "missing_information": "需要补充高胆红素血症相关指南或外部证据。",
            "next_step": "补入新生儿高胆红素血症指南，或明确报告本地语料未找到。",
        }
    if case["case_type"] == "GA":
        return {
            "why_not_applicable": "结构化孕周字段缺失，无法判断依赖胎龄的指南条件。",
            "which_criterion_failed": "孕周字段未记录。",
            "other_guidelines": "补出胎龄后才能判断早产相关指南。",
            "missing_information": "病历文本中的胎龄。",
            "next_step": "从病历文本抽取胎龄并回填结构化字段。",
        }
    failed = target.get("failed_conditions") or []
    if failed:
        failed_text = "；".join(
            f"{condition['criterion_id']}（{_FIELD_LABELS.get(condition['field'], condition['field'])}"
            f"{_operator_text(condition.get('operator'))}{'、'.join(str(item) for item in condition.get('expected') or [])}，"
            f"实际 {condition.get('observed')}）"
            for condition in failed
        )
        return {
            "why_not_applicable": "至少一项结构化适用条件与可见病历不符。",
            "which_criterion_failed": failed_text,
            "other_guidelines": "；".join(response.get("coverage") or []) or "无其他本地指南覆盖。",
            "missing_information": "无（失败条件已有可见事实）。",
            "next_step": "核对原文与失败条件；若需亚群证据，再检索对应指南。",
        }
    missing = target.get("missing_fields") or []
    if missing:
        return {
            "why_not_applicable": "存在未记录事实，结构化条件无法判定。",
            "which_criterion_failed": "、".join(_FIELD_LABELS.get(field, field) for field in missing),
            "other_guidelines": "；".join(response.get("coverage") or []) or "无其他本地指南覆盖。",
            "missing_information": "、".join(_FIELD_LABELS.get(field, field) for field in missing),
            "next_step": "先从 HIS 或病历文本补齐缺失字段，再重新核对。",
        }
    return {
        "why_not_applicable": "未发现条件不符。",
        "which_criterion_failed": "无。",
        "other_guidelines": "；".join(response.get("coverage") or []) or "无。",
        "missing_information": "无。",
        "next_step": "人工核对指南原文与患者条件。",
    }


def _no_coverage_declared(response: Mapping[str, Any]) -> bool:
    summary = response.get("doctor_summary") or {}
    return not response.get("coverage") and "无其他本地指南覆盖" in str(summary.get("other_guidelines"))


def _ratio_metric(pairs: Sequence[tuple[Any, Any]]) -> dict[str, Any]:
    total = len(pairs)
    if total == 0:
        return {"n": 0, "correct": 0, "accuracy": None}
    correct = sum(1 for predicted, gold in pairs if predicted == gold)
    return {"n": total, "correct": correct, "accuracy": correct / total}


def _boolean_metric(pairs: Sequence[tuple[bool, bool]]) -> dict[str, Any]:
    total = len(pairs)
    if total == 0:
        return {"n": 0, "correct": 0, "rate": None}
    correct = sum(1 for predicted, gold in pairs if predicted == gold)
    return {"n": total, "correct": correct, "rate": correct / total}


def _all_guideline_metric(
    cases: Sequence[Mapping[str, Any]], responses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    correct = 0
    total = 0
    excluded = 0
    for case, response in zip(cases, responses):
        if case["case_type"] == "GA":
            continue
        gold_by_id = {item["guideline_id"]: item for item in case["gold"]["all_guidelines"]}
        for document_id, assessment in response["assessments"].items():
            gold = gold_by_id.get(document_id)
            if not gold:
                continue
            if any(
                criterion.get("reason_code") == "unclassified_support"
                for criterion in _gold_criteria(case, document_id)
            ):
                excluded += 1
                continue
            total += 1
            if assessment["status"] == gold["status"]:
                correct += 1
    return {
        "n": total,
        "correct": correct,
        "accuracy": (correct / total) if total else None,
        "excluded_unclassified_support": excluded,
    }


def _gold_criteria(case: Mapping[str, Any], document_id: str) -> list[Mapping[str, Any]]:
    for guideline in case["gold"].get("all_guidelines", []):
        if guideline["guideline_id"] == document_id:
            return [
                criterion for criterion in guideline.get("criteria", [])
                if isinstance(criterion, Mapping)
            ]
    return []


def _ga_metric(
    cases: Sequence[Mapping[str, Any]], responses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    pairs = [
        (response["extracted_ga_days"], case["gold"]["ga_text_days"])
        for case, response in zip(cases, responses) if case["case_type"] == "GA"
    ]
    return _ratio_metric(pairs)


def _failure_metric(
    cases: Sequence[Mapping[str, Any]], responses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    pairs = []
    for case, response in zip(cases, responses):
        if case["case_type"] not in ("A", "B"):
            continue
        predicted = set(response["target"]["failed_criteria"])
        gold = set(case["gold"]["failed_criteria"])
        pairs.append((predicted == gold, True))
    return _boolean_metric(pairs)


def _query_metric(
    cases: Sequence[Mapping[str, Any]], responses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    passed = 0
    for case, response in zip(cases, responses):
        if _query_compliant(response["follow_up_query"], case):
            passed += 1
    total = len(cases)
    return {"n": total, "passed": passed, "rate": (passed / total) if total else None}


def _query_compliant(query: str, case: Mapping[str, Any]) -> bool:
    text = str(query or "")
    if not text.strip():
        return False
    if any(term in text for term in _POPULATION_TERMS) is False:
        return False
    if "指南" not in text and "证据" not in text:
        return False
    if case["patient_ref"] in text or "hmac-" in text:
        return False
    if "住院号" in text or re.search(r"\d{6,}", text):
        return False
    return True


def _coverage_metric(
    cases: Sequence[Mapping[str, Any]], responses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    tp = fp = fn = 0
    for case, response in zip(cases, responses):
        if case["case_type"] not in ("A", "B"):
            continue
        predicted = set(response["coverage"])
        gold = set(case["gold"]["coverage"])
        tp += len(predicted & gold)
        fp += len(predicted - gold)
        fn += len(gold - predicted)
    precision = (tp / (tp + fp)) if (tp + fp) else None
    recall = (tp / (tp + fn)) if (tp + fn) else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall}


def _missing_metric(
    cases: Sequence[Mapping[str, Any]], responses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    pairs = []
    for case, response in zip(cases, responses):
        if case["case_type"] != "C":
            continue
        predicted = set(response["target"]["missing_fields"])
        gold = set(case["gold"]["missing_fields"])
        pairs.append((predicted == gold, True))
    return _boolean_metric(pairs)


def _traceability_metric(
    cases: Sequence[Mapping[str, Any]], responses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    checks = 0
    passed = 0
    for case, response in zip(cases, responses):
        target = response.get("target") or {}
        for condition in target.get("failed_conditions") or []:
            checks += 1
            if condition.get("record_paths") and condition.get("locator"):
                passed += 1
        for citation in response.get("citations") or []:
            checks += 1
            if citation.get("record_path") and citation.get("locator"):
                passed += 1
    return {"checks": checks, "passed": passed, "rate": (passed / checks) if checks else None}


def _readability_metric(responses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    keys = ("why_not_applicable", "which_criterion_failed", "other_guidelines",
            "missing_information", "next_step")
    passed = 0
    for response in responses:
        summary = response.get("doctor_summary") or {}
        if all(isinstance(summary.get(key), str) and summary.get(key).strip() for key in keys):
            passed += 1
    total = len(responses)
    return {"n": total, "passed": passed, "rate": (passed / total) if total else None}


def _per_case_type(
    cases: Sequence[Mapping[str, Any]], responses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for case_type in ("A", "B", "C", "D", "GA"):
        subset_cases = [case for case in cases if case["case_type"] == case_type]
        subset_responses = [response for response in responses if response["case_type"] == case_type]
        if not subset_cases:
            continue
        result[case_type] = {
            "n": len(subset_cases),
            "target_status_accuracy": _ratio_metric([
                (response["target"]["status"], case["gold"]["guideline_status"])
                for case, response in zip(subset_cases, subset_responses)
                if case["gold"]["guideline_status"] is not None
            ])["accuracy"],
            "readable_rate": _readability_metric(subset_responses)["rate"],
        }
    return result
