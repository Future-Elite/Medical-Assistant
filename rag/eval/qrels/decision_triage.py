"""Decision-relevant information triage for guideline applicability.

The workflow question is not "which fact is missing" but "which missing fact
would change the answer to *this* question, and when should we stop asking".
The function separates two lists:

* ``target_priorities``: facts that can change the target guideline's status;
* ``other_priorities``: facts that can change *other* guidelines but not the
  target.  They are shown as "different question" signals, not as over-asking.

The simulation is rule-based and small on purpose; the point is the workflow
contract, not another model.  A model can later replace the ranking, but the
output contract stays the same.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .followup import build_followup
from .gold_rules import load_criteria_fixture, patient_gold

SIM_VALUES: dict[str, tuple[Any, ...]] = {
    "derived_birth.ga_days": tuple(range(168, 294, 7)),  # 24w to 41w
    "derived_birth.birth_weight_g": (500, 750, 1000, 1250, 1500, 2000, 2500, 3000, 3500),
    "derived_respiratory_support.mode": ("无", "氧疗", "CPAP", "机械通气"),
}
FIELD_LABELS = {
    "derived_birth.ga_days": "胎龄",
    "derived_birth.birth_weight_g": "出生体重",
    "derived_respiratory_support.mode": "呼吸支持方式",
}
FIELD_COST = {
    "derived_birth.ga_days": ("low", 1),
    "derived_birth.birth_weight_g": ("low", 1),
    "derived_respiratory_support.mode": ("medium", 2),
}
FIELD_SOURCE = {
    "derived_birth.ga_days": "病历文本/母亲记录",
    "derived_birth.birth_weight_g": "病历文本/出生记录",
    "derived_respiratory_support.mode": "护理/医嘱记录",
}


def triage_case(
    case: Mapping[str, Any], fixture: Mapping[str, Any] | None = None,
    *, include_followup: bool = True,
) -> dict[str, Any]:
    """Return a question-specific next-step card for one test case."""
    fixture = fixture or load_criteria_fixture()
    baseline_derived = derived_from_snapshot(case["snapshot"])
    baseline_gold = patient_gold(fixture, baseline_derived)
    baseline_outcome = _outcome(baseline_gold, case["case_type"])
    baseline_coverage = tuple(sorted(baseline_gold["applicable"]))
    target_id = case["gold"].get("guideline_id")
    target_status = None
    if target_id:
        target_status = next(
            guideline["status"] for guideline in baseline_gold["guidelines"]
            if guideline["guideline_id"] == target_id
        )
    missing_fields = sorted({
        criterion["field"]
        for guideline in baseline_gold["guidelines"]
        for criterion in guideline["criteria"]
        if criterion["status"] == "unknown" and criterion.get("reason_code") == "not_recorded"
    })

    priorities: list[dict[str, Any]] = []
    for field in missing_fields:
        values = SIM_VALUES.get(field)
        if not values:
            continue
        candidates = [
            patient_gold(fixture, _with_value(baseline_derived, field, value))
            for value in values
        ]
        target_statuses = []
        if target_id:
            target_statuses = [
                next(
                    guideline["status"] for guideline in candidate["guidelines"]
                    if guideline["guideline_id"] == target_id
                )
                for candidate in candidates
            ]
        target_changes = sorted(set(target_statuses) - {target_status})
        target_relevant = bool(target_changes)
        target_unlocked = target_status == "unknown" and any(status != "unknown" for status in target_statuses)
        other_unlocked = [
            guideline["guideline_id"]
            for guideline in baseline_gold["guidelines"]
            if guideline["guideline_id"] != target_id
            and guideline["status"] == "unknown"
            and any(
                next(item for item in candidate["guidelines"] if item["guideline_id"] == guideline["guideline_id"])["status"] != "unknown"
                for candidate in candidates
            )
        ]
        coverage_options = sorted({
            tuple(sorted(candidate["applicable"])) for candidate in candidates
        })
        outcomes = sorted({_outcome(candidate, case["case_type"]) for candidate in candidates})
        outcome_changes = [outcome for outcome in outcomes if outcome != baseline_outcome]
        impact = len(other_unlocked) + (1 if any(coverage_options) else 0) + (1 if outcome_changes else 0)
        cost, cost_rank = FIELD_COST.get(field, ("medium", 2))
        priorities.append({
            "field": field,
            "label": FIELD_LABELS.get(field, field),
            "source": FIELD_SOURCE.get(field, "病历/追问"),
            "cost": cost,
            "cost_rank": cost_rank,
            "target_relevant": target_relevant,
            "target_changes": target_changes,
            "target_unlocked": target_unlocked,
            "other_unlocked_guidelines": other_unlocked,
            "impact": impact,
            "coverage_options": [list(option) for option in coverage_options],
            "outcome_changes": outcome_changes,
            "why": _why(field, target_relevant, target_changes, other_unlocked,
                        coverage_options, baseline_outcome, outcome_changes),
        })
    priorities.sort(key=lambda item: (not item["target_relevant"], -item["impact"], item["cost_rank"], item["field"]))
    target_priorities = [item for item in priorities if item["target_relevant"]]
    other_priorities = [item for item in priorities if not item["target_relevant"]]
    topic_gap = case["case_type"] == "D"
    target_stop = topic_gap or target_id is None or not target_priorities

    if topic_gap:
        stop_reason = "本地 4 份指南没有该主题；补更多患者事实不会改变这个结论。"
        next_step = "停止追问患者事实；把问题转成外部证据/会诊问题，或明确报告本地语料未找到。"
    elif target_stop and other_priorities:
        top = other_priorities[0]
        stop_reason = "没有缺失事实会改变目标指南的结论；但其他指南可能因补事实而改变。"
        next_step = (
            f"目标问题可以停止追问；如果你同时关心其他指南，可查{top['label']}"
            f"（会解锁 {len(top['other_unlocked_guidelines'])} 份其他指南）。"
        )
    elif target_stop:
        stop_reason = "没有缺失事实会改变目标指南的结论。"
        next_step = "停止补事实；按当前证据给出目标问题的结论。"
    else:
        top = target_priorities[0]
        stop_reason = None
        next_step = (
            f"先查{top['label']}（{top['source']}，成本 {top['cost']}）："
            f"它会把目标指南从 unknown 变为可判定。"
        )
    followup = (
        build_followup(
            snapshot=case["snapshot"],
            derived=baseline_derived,
            target_guideline_id=target_id,
            target_topic=case["gold"].get("topic"),
            gold=baseline_gold,
            fixture=fixture,
        )
        if include_followup else None
    )
    return {
        "case_id": case["case_id"],
        "case_type": case["case_type"],
        "followup": followup,
        "target": {
            "guideline_id": target_id,
            "topic": case["gold"].get("topic"),
            "status": target_status,
        },
        "baseline": {
            "outcome": baseline_outcome,
            "statuses": {
                guideline["guideline_id"]: guideline["status"]
                for guideline in baseline_gold["guidelines"]
            },
            "coverage": list(baseline_coverage),
            "missing_fields": missing_fields,
        },
        "target_priorities": target_priorities,
        "other_priorities": other_priorities,
        "priorities": priorities,
        "target_stop": target_stop,
        "stop": target_stop,
        "stop_reason": stop_reason,
        "topic_gap": topic_gap,
        "next_step": next_step,
    }


def evaluate_triage(cases: Sequence[Mapping[str, Any]], fixture: Mapping[str, Any] | None = None) -> dict[str, Any]:
    fixture = fixture or load_criteria_fixture()
    cases = [case for case in cases if case["case_type"] != "GA"]
    cards = [triage_case(case, fixture) for case in cases]
    by_type: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}
    for case, card in zip(cases, cards):
        by_type.setdefault(case["case_type"], []).append((case, card))

    def _c_metrics() -> dict[str, Any]:
        rows = by_type.get("C", [])
        top1 = 0
        unlocked = 0
        target_counts = []
        other_counts = []
        for case, card in rows:
            target_fields = set(case["gold"].get("missing_fields") or [])
            target_guideline = case["gold"].get("guideline_id")
            top = card["target_priorities"][0] if card["target_priorities"] else None
            top1 += bool(top and top["field"] in target_fields)
            unlocked += bool(top and top["target_unlocked"])
            target_counts.append(len(card["target_priorities"]))
            other_counts.append(len(card["other_priorities"]))
        n = len(rows)
        return {
            "n": n,
            "top1_target_field": top1,
            "top1_target_accuracy": (top1 / n) if n else None,
            "target_unlocked_top1": unlocked,
            "target_unlocked_rate": (unlocked / n) if n else None,
            "mean_target_priorities": _mean(target_counts),
            "mean_other_priorities": _mean(other_counts),
        }

    def _stop_metrics(case_type: str, topic: bool = False) -> dict[str, Any]:
        rows = by_type.get(case_type, [])
        if topic:
            correct = sum(1 for _, card in rows if card["target_stop"] and card["topic_gap"])
        else:
            correct = sum(1 for _, card in rows if card["target_stop"])
        over_asking = sum(1 for _, card in rows if card["target_stop"] and card["target_priorities"])
        other = [len(card["other_priorities"]) for _, card in rows]
        n = len(rows)
        return {
            "n": n,
            "stop_correct": correct,
            "stop_accuracy": (correct / n) if n else None,
            "over_asking_target": over_asking,
            "mean_other_priorities": _mean(other),
        }

    followup = {
        "coverage_cases": sum(1 for card in cards if card.get("followup") and card["followup"]["coverage_guidelines"]),
        "qualifier_cases": sum(1 for card in cards if card.get("followup") and card["followup"]["population_qualifiers"]),
        "conflict_cases": sum(1 for card in cards if card.get("followup") and any(family["conflict"] for family in card["followup"]["status_families"])),
        "external_query_cases": sum(1 for card in cards if card.get("followup") and card["followup"]["external_query"]),
    }
    return {
        "case_count": len(cases),
        "C": _c_metrics(),
        "B": _stop_metrics("B"),
        "D": _stop_metrics("D", topic=True),
        "A": _stop_metrics("A"),
        "followup": followup,
        "cards": cards,
    }


def derived_from_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    facts = {fact.get("collection"): fact.get("fields") or {} for fact in snapshot.get("facts", [])}
    birth = facts.get("derived_birth") or {}
    resp = facts.get("derived_respiratory_support") or {}
    labs = facts.get("derived_labs") or {}
    treatment = facts.get("derived_treatment") or {}
    return {
        "ga_days": birth.get("ga_days"),
        "ga_days_range": birth.get("ga_days_range"),
        "ga_precision": birth.get("ga_precision"),
        "ga_weeks": birth.get("ga_weeks"),
        "ga_extra_days": birth.get("ga_extra_days"),
        "birth_weight_g": birth.get("birth_weight_g"),
        "birth_weight_status": birth.get("birth_weight_status"),
        "pna_days": birth.get("pna_days"),
        "respiratory_status": resp.get("data_status"),
        "respiratory_mode": resp.get("mode"),
        "respiratory_mapping_status": resp.get("mapping_status"),
        "jaundice_mentioned": bool(labs.get("jaundice_mentioned")),
        "uncertain_diagnosis": {"uncertain": bool(treatment.get("uncertain_diagnosis"))},
        "labs": labs,
        "treatments": {
            "phototherapy": {"status": treatment.get("phototherapy_status")},
            "antibiotics": {"status": treatment.get("antibiotics_status")},
            "hearing_screen": {"status": treatment.get("hearing_screen_status")},
        },
    }


def _with_value(derived: Mapping[str, Any], field: str, value: Any) -> dict[str, Any]:
    candidate = deepcopy(dict(derived))
    if field == "derived_birth.ga_days":
        candidate["ga_days"] = int(value)
        candidate["ga_days_range"] = [int(value), int(value)]
        candidate["ga_precision"] = "day"
        candidate["ga_weeks"] = int(value) // 7
        candidate["ga_extra_days"] = int(value) % 7
    elif field == "derived_birth.birth_weight_g":
        candidate["birth_weight_g"] = int(value)
        candidate["birth_weight_status"] = "observed_positive"
    elif field == "derived_respiratory_support.mode":
        candidate["respiratory_mode"] = value
        candidate["respiratory_status"] = "observed_negative" if value == "无" else "observed_positive"
        candidate["respiratory_mapping_status"] = "mapped_negative" if value == "无" else "mapped"
    return candidate


def _outcome(gold: Mapping[str, Any], case_type: str) -> str:
    if case_type == "D":
        return "topic_gap"
    if gold["applicable"]:
        return "resolved"
    if any(guideline["status"] == "unknown" for guideline in gold["guidelines"]):
        return "needs_information"
    return "evidence_gap"


def _why(field: str, target_relevant: bool, target_changes: Sequence[str],
         other_unlocked: Sequence[str], coverage_options: Sequence[Sequence[str]],
         baseline_outcome: str, outcome_changes: Sequence[str]) -> str:
    label = FIELD_LABELS.get(field, field)
    if target_relevant:
        parts = [f"补{label}会改变目标指南的状态（{'/'.join(target_changes)}）"]
    else:
        parts = [f"补{label}不会改变目标指南"]
    if other_unlocked:
        parts.append(f"但会解锁 {len(other_unlocked)} 份其他指南")
    if any(coverage_options):
        parts.append(f"可能出现 {len(coverage_options)} 种覆盖组合")
    if outcome_changes:
        parts.append(f"总体结论会从“{baseline_outcome}”变为 {'/'.join(outcome_changes)}")
    return "；".join(parts) + "。"


def _mean(values: Sequence[int]) -> float | None:
    return (sum(values) / len(values)) if values else None

def apply_value_to_snapshot(snapshot: Mapping[str, Any], field: str, value: Any) -> dict[str, Any]:
    """Return a copy of the snapshot with one missing fact filled.

    This is the closed-loop test helper: the triage card recommends a fact,
    the caller supplies a value, and the card is recomputed.  It does not
    write to HIS; it only updates the in-memory snapshot for the test.
    """
    updated = deepcopy(dict(snapshot))
    found = False
    for fact in updated.get("facts", []):
        collection = fact.get("collection")
        fields = fact.setdefault("fields", {})
        if collection == "derived_birth" and field.startswith("derived_birth."):
            found = True
            if field.endswith("ga_days"):
                days = int(value)
                fields["ga_days"] = days
                fields["ga_days_range"] = [days, days]
                fields["ga_precision"] = "day"
                fields["ga_weeks"] = days // 7
                fields["ga_extra_days"] = days % 7
                fields["data_status"] = "observed_positive"
            elif field.endswith("birth_weight_g"):
                fields["birth_weight_g"] = int(value)
                fields["birth_weight_status"] = "observed_positive"
                fields["data_status"] = "observed_positive"
        elif collection == "derived_respiratory_support" and field == "derived_respiratory_support.mode":
            found = True
            fields["mode"] = value
            fields["data_status"] = "observed_negative" if value == "无" else "observed_positive"
            fields["mapping_status"] = "mapped_negative" if value == "无" else "mapped"
        elif collection == "derived_labs" and field == "derived_labs.max_tbil_umol_l":
            found = True
            fields["max_tbil_umol_l"] = float(value)
            fields["data_status"] = "observed_positive"
    if not found:
        if field == "derived_birth.ga_days":
            updated.setdefault("facts", []).append({
                "collection": "derived_birth",
                "fields": {
                    "ga_days": int(value), "ga_days_range": [int(value), int(value)],
                    "ga_precision": "day", "ga_weeks": int(value) // 7,
                    "ga_extra_days": int(value) % 7, "data_status": "observed_positive",
                },
            })
        elif field == "derived_birth.birth_weight_g":
            updated.setdefault("facts", []).append({
                "collection": "derived_birth",
                "fields": {"birth_weight_g": int(value), "birth_weight_status": "observed_positive", "data_status": "observed_positive"},
            })
        elif field == "derived_respiratory_support.mode":
            updated.setdefault("facts", []).append({
                "collection": "derived_respiratory_support",
                "fields": {"mode": value, "data_status": "observed_negative" if value == "无" else "observed_positive", "mapping_status": "mapped_negative" if value == "无" else "mapped"},
            })
        elif field == "derived_labs.max_tbil_umol_l":
            updated.setdefault("facts", []).append({
                "collection": "derived_labs",
                "fields": {"max_tbil_umol_l": float(value), "data_status": "observed_positive"},
            })
    return updated


def triage_snapshot(
    snapshot: Mapping[str, Any],
    *,
    target_guideline_id: str | None = None,
    target_topic: str | None = None,
    case_type: str | None = None,
) -> dict[str, Any]:
    """Triage a live Agent-provided snapshot instead of an evaluation case.

    ``target_topic`` is used for a topic not present in the local guideline
    corpus (for example ``hyperbilirubinemia``); otherwise the caller provides
    the target guideline ID, or the first non-matching guideline is used.
    """
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("facts"), list) or not snapshot.get("as_of"):
        raise ValueError("patient_snapshot must contain as_of and a facts list")
    fixture = load_criteria_fixture()
    derived = derived_from_snapshot(snapshot)
    gold = patient_gold(fixture, derived)
    if target_topic:
        target_id = None
        case_type = "D"
    else:
        if target_guideline_id is None:
            target_id = next(
                (guideline["guideline_id"] for guideline in gold["guidelines"]
                 if guideline["status"] != "possible_match"),
                gold["guidelines"][0]["guideline_id"],
            )
        else:
            target_id = target_guideline_id
        try:
            target_status = next(
                guideline["status"] for guideline in gold["guidelines"]
                if guideline["guideline_id"] == target_id
            )
        except StopIteration:
            raise ValueError(f"unknown target_guideline_id: {target_id}") from None
        if case_type is None:
            case_type = "C" if target_status == "unknown" else ("A" if target_status == "possible_match" else "B")
    if case_type not in {"A", "B", "C", "D"}:
        raise ValueError("case_type must be one of A/B/C/D")
    case = {
        "case_id": "api",
        "case_type": case_type,
        "snapshot": snapshot,
        "gold": {
            "guideline_id": target_id,
            "topic": target_topic,
            "all_guidelines": [
                {
                    "guideline_id": guideline["guideline_id"],
                    "status": guideline["status"],
                    "failed_criteria": guideline["failed_criteria"],
                    "missing_fields": guideline["missing_fields"],
                    "criteria": guideline["criteria"],
                }
                for guideline in gold["guidelines"]
            ],
        },
    }
    return triage_case(case, fixture)
