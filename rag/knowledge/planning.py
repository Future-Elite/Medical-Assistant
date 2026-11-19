"""Plan-level helpers derived from a retrieved knowledge context.

These helpers do not execute treatment.  They enforce the same conservative
boundaries as the action validator: only initialized review metrics may be
ordered, observation needs an explicit stable assessment, maintenance is a
comparator rather than an automatic winner, and episode end is never inferred
from normalized scores alone.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .conditions import FactStore

RED_FLAG_TERMS = (
    "呼吸暂停", "惊厥", "抽搐", "休克", "需心肺复苏", "发绀", "意识改变",
)


class PlanningError(ValueError):
    def __init__(self, message: str, code: str = "planning_error") -> None:
        super().__init__(message)
        self.code = code


def red_flags(facts: FactStore) -> list[str]:
    values = [str(value) for value in facts.list_values("symptom")]
    found = {
        value for value in values
        if any(term in value for term in RED_FLAG_TERMS)
    }
    return sorted(found)


def stable_clinical_assessment(facts: FactStore) -> tuple[bool, list[str]]:
    """Return whether an explicit stable assessment exists.

    ``clinically_well`` must be explicitly true.  Missing or negative values do
    not authorize observation, and a red flag always blocks it.
    """
    well = facts.latest("clinically_well")
    reasons: list[str] = []
    if well.status != "met" or well.value is not True:
        reasons.append("clinically_well 未明确记录为 true")
    flags = red_flags(facts)
    if flags:
        reasons.append(f"存在危险征象：{','.join(flags)}")
    return not reasons, reasons


def build_review_plan(
    context: Mapping[str, Any],
    *,
    panel_ids: Sequence[str] = (),
    metric_keys: Sequence[str] = (),
) -> dict[str, Any]:
    """Expand review panels only to metrics already initialized for this case."""
    available_panels = {
        str(row["id"]): row for row in context.get("available_monitoring_panels") or ()
    }
    requested_panels = list(dict.fromkeys(str(item) for item in panel_ids if str(item)))
    missing_panels = [item for item in requested_panels if item not in available_panels]
    if missing_panels:
        raise PlanningError(
            f"所选复查面板无已初始化项目：{','.join(missing_panels)}",
            "review_panel_uninitialized",
        )
    facts = context.get("patient_facts") or {}
    metrics: list[str] = []
    for panel_id in requested_panels:
        metrics.extend(str(key) for key in available_panels[panel_id].get("metric_keys") or ())
    for key in (str(item) for item in metric_keys if str(item)):
        status = (facts.get(key) or {}).get("status")
        if status not in {"met", "conflict"}:
            raise PlanningError(
                f"复查指标没有真实基线：{key}",
                "review_metric_uninitialized",
            )
        metrics.append(key)
    metrics = list(dict.fromkeys(metrics))
    if not metrics:
        raise PlanningError("请指定已初始化的复查指标或监测面板。", "review_metrics_missing")
    return {
        "schema_version": "review-plan",
        "knowledge_context_id": context.get("knowledge_context_id"),
        "review_panels": requested_panels,
        "review_metrics": metrics,
        "available_panels": list(available_panels),
        "boundary": "review metrics are limited to the current visible baseline; no fabricated results.",
    }


def build_maintenance_comparator(
    primary_plan: Mapping[str, Any],
    *,
    target: str | None = None,
    proposal_order: int = 1,
) -> dict[str, Any]:
    """Build a ``continue`` comparator that inherits the primary monitoring plan.

    The comparator is deliberately ordered after the proposed plan so an equal
    score does not make maintenance win by default.
    """
    actions = list(primary_plan.get("actions") or ())
    target = target or next(
        (str(item.get("target")) for item in actions if item.get("target")),
        "action:observation",
    )
    return {
        "candidate_id": "baseline-continue",
        "actions": [{"operation": "continue", "target": target, "offset_hours": 0}],
        "review_metrics": list(primary_plan.get("review_metrics") or ()),
        "review_panels": list(primary_plan.get("review_panels") or ()),
        "proposal_order": proposal_order,
        "is_comparator": True,
        "rationale": "已有活动治疗；沿用本轮明确监测安排；平局不默认胜出。",
    }


def select_candidate(evaluations: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Select the lowest abnormal count, breaking ties by proposal order.

    A maintenance comparator has a larger ``proposal_order``, so it does not win
    an exact tie against the proposed plan.
    """
    eligible = [
        item for item in evaluations
        if item.get("clinically_eligible", True)
        and item.get("expected_abnormal_count") is not None
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda item: (
            float(item["expected_abnormal_count"]),
            int(item.get("proposal_order") or 0),
        ),
    )


def episode_end_gate(
    facts: FactStore,
    *,
    explicit_clinical_end: bool = False,
    end_reason: str = "",
) -> dict[str, Any]:
    """Refuse automatic episode end from normalized scores alone.

    Explicit clinical resolution is required, and an active treatment still
    blocks closure.  This is a guard, not a model of disease resolution.
    """
    active = [str(item) for item in facts.list_values("active_treatment")]
    if not explicit_clinical_end:
        return {
            "allowed": False,
            "reason": "不自动结束病程：评分指标正常不等于临床缓解，需明确临床评估与处置。",
            "requires_clinical_review": True,
            "active_treatments": active,
        }
    if active:
        return {
            "allowed": False,
            "reason": f"仍有活动治疗：{','.join(active)}；不能仅凭指标正常结束病程。",
            "requires_clinical_review": True,
            "active_treatments": active,
        }
    return {
        "allowed": True,
        "reason": end_reason or "已有明确临床结束依据。",
        "requires_clinical_review": False,
        "active_treatments": [],
    }
