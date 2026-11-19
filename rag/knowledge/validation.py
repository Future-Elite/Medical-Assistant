"""Action validation against a retrieved knowledge context.

A candidate action is only ``supported`` when the cited recommendation is in
the current retrieval context, its direction/operation match, all necessary
conditions are ``met``, no exclusion is ``met``, evidence is reviewed and the
runtime binding supports the exact action/parameters.  ``eligible_for_simulation``
means "passed this knowledge and runtime check", not clinical approval.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from .conditions import FactStore
from .model import KnowledgeGraph
from .planning import red_flags, stable_clinical_assessment

_ALLOWED_PARAMETERS = {"fio2", "pressure_cm_h2o"}


def validate_actions(
    graph: KnowledgeGraph,
    context: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    *,
    facts: FactStore | None = None,
) -> dict[str, Any]:
    context_release = str(context.get("release_sha256") or "")
    if context_release and context_release != graph.release.content_sha256:
        return {
            "schema_version": "action-validation",
            "knowledge_context_id": context.get("knowledge_context_id"),
            "knowledge_release_id": graph.release.release_id,
            "release_sha256": graph.release.content_sha256,
            "snapshot_sha256": context.get("snapshot_sha256"),
            "eligible_for_simulation": False,
            "status": "blocked",
            "actions": [],
            "timeline": [],
            "combination_errors": [],
            "errors": [{
                "code": "SOURCE_SUPERSEDED",
                "message": "知识 release 已变化，本轮上下文不能用于新动作校验；请重新检索。",
            }],
            "boundary": "release hash mismatch; re-run knowledge search before validation",
        }
    actions = candidate_plan.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("candidate_plan.actions must be a non-empty list")
    context_by_id = {row["recommendation_id"]: row for row in context.get("recommendations") or ()}
    excluded_by_id = {row["id"]: row for row in context.get("excluded_recommendations") or ()}
    fact_store = facts
    results: list[dict[str, Any]] = []
    all_errors: list[dict[str, Any]] = []
    offsets = [_offset(raw) for raw in actions]
    if offsets and offsets[0] != 0:
        all_errors.append(_error("PLAN_NOT_TIME_ORDERED", "候选动作必须从当前时刻（offset_hours=0）开始"))
    if offsets != sorted(offsets):
        all_errors.append(_error("PLAN_NOT_TIME_ORDERED", "候选动作必须按 offset_hours 升序排列"))
    for index, raw in enumerate(actions):
        if not isinstance(raw, Mapping):
            raise ValueError("candidate action must be an object")
        result = _validate_one(
            graph, context_by_id, excluded_by_id, raw, index, fact_store,
        )
        results.append(result)
        all_errors.extend(result["errors"])
    combination_errors = _check_combination(graph, actions, facts)
    all_errors.extend(combination_errors)
    eligible = (
        all(row["status"] == "supported" for row in results)
        and not combination_errors
        and not any(error["code"] == "PLAN_NOT_TIME_ORDERED" for error in all_errors)
    )
    timeline = [
        {
            "index": row["index"],
            "offset_hours": row["offset_hours"],
            "operation": row["operation"],
            "target": row["target"],
            "status": row["status"],
            "error_codes": [error["code"] for error in row["errors"]],
        }
        for row in results
    ]
    return {
        "schema_version": "action-validation",
        "knowledge_context_id": context.get("knowledge_context_id"),
        "knowledge_release_id": graph.release.release_id,
        "release_sha256": graph.release.content_sha256,
        "snapshot_sha256": context.get("snapshot_sha256"),
        "eligible_for_simulation": eligible,
        "status": "supported" if eligible else ("needs_information" if any(row["status"] == "needs_information" for row in results) else "blocked"),
        "actions": results,
        "timeline": timeline,
        "combination_errors": combination_errors,
        "errors": all_errors,
        "boundary": "eligible_for_simulation only means this knowledge/runtime check passed; it is not clinical safety, best treatment or physician approval.",
    }


def _validate_one(
    graph: KnowledgeGraph,
    context_by_id: Mapping[str, Mapping[str, Any]],
    excluded_by_id: Mapping[str, Mapping[str, Any]],
    raw: Mapping[str, Any],
    index: int,
    facts: FactStore | None,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    operation = str(raw.get("operation") or "").strip()
    target_text = str(raw.get("target") or "").strip()
    offset = _offset(raw)
    if offset < 0:
        errors.append(_error("PLAN_NOT_TIME_ORDERED", "offset_hours 不能为负数", index=index, action=operation))
    if facts is not None and operation in {"wait", "continue"}:
        flags = red_flags(facts)
        if flags:
            errors.append(_error("RED_FLAG_PRESENT", f"已有危险征象：{','.join(flags)}", index=index, action=operation))
    if operation not in {"start", "adjust", "stop", "continue", "wait", "order_test"}:
        return _blocked(index, operation, target_text, "ACTION_NOT_SUPPORTED", "operation 不在运行时目录", offset=offset)
    target_id, target_error = _resolve_target(graph, target_text, operation)
    if target_error:
        errors.append(target_error)
    target = graph.actions.get(target_id or "", {})
    recommendations = _cited_recommendations(graph, context_by_id, excluded_by_id, raw, target_id, operation, errors)
    if not recommendations:
        errors.append(_error(
            "RECOMMENDATION_NOT_RETRIEVED",
            "候选动作没有引用本轮检索到的推荐，且上下文没有唯一匹配项",
            index=index, action=operation, target=target_id,
        ))
        return _result(index, operation, target_id, errors, offset=offset)
    recommendation_ids = [row["recommendation_id"] for row in recommendations]
    for recommendation in recommendations:
        rec = graph.recommendations[recommendation["recommendation_id"]]
        direction = str(rec.get("direction") or "")
        supported_operations = set(rec.get("supported_operations") or ())
        if operation not in supported_operations:
            errors.append(_error("DIRECTION_MISMATCH", f"{rec['id']} 不支持操作 {operation}", index=index, action=operation, recommendation_id=rec["id"]))
            continue
        if direction not in {"support", "consider", "monitor"}:
            errors.append(_error("DIRECTION_MISMATCH", f"{rec['id']} 的方向 {direction} 不能授权 {operation}", index=index, action=operation, recommendation_id=rec["id"]))
            continue
        for dimension, status in (recommendation.get("applicability") or {}).items():
            if dimension in {"population", "setting", "indication", "temporal"} and status == "not_met":
                if dimension == "population":
                    code = "POPULATION_MISMATCH"
                elif dimension == "setting":
                    code = "SETTING_MISMATCH"
                elif dimension == "temporal":
                    code = "TEMPORAL_CONDITION_UNMET"
                else:
                    code = "INDICATION_UNKNOWN"
                errors.append(_error(code, f"{rec['id']} 的 {dimension} 不满足", index=index, action=operation, recommendation_id=rec["id"]))
            elif dimension in {"population", "setting", "indication", "temporal"} and status == "unknown":
                errors.append(_error("INDICATION_UNKNOWN", f"{rec['id']} 的 {dimension} 未知，不能当已满足", index=index, action=operation, recommendation_id=rec["id"]))
            elif dimension in {"setting", "unformalized"} and status == "conflict":
                errors.append(_error("SETTING_CONFLICT", f"{rec['id']} 的 {dimension} 存在冲突", index=index, action=operation, recommendation_id=rec["id"]))
            elif dimension == "unformalized" and status != "met":
                errors.append(_error("NEEDS_CLINICAL_REVIEW", f"{rec['id']} 存在不可形式化条件", index=index, action=operation, recommendation_id=rec["id"]))
            elif dimension == "exclusion" and status == "met":
                errors.append(_error("CONTRAINDICATION_PRESENT", f"{rec['id']} 存在排除条件", index=index, action=operation, recommendation_id=rec["id"]))
            elif dimension == "exclusion" and status == "unknown":
                errors.append(_error("INDICATION_UNKNOWN", f"{rec['id']} 的排除条件未知，不能放行", index=index, action=operation, recommendation_id=rec["id"]))
            elif dimension == "evidence_status" and status != "met":
                errors.append(_error("EVIDENCE_UNREVIEWED", f"{rec['id']} 的证据片段未审核或缺失", index=index, action=operation, recommendation_id=rec["id"]))
        runtime = _runtime_check(graph, rec, target_id, operation, raw.get("parameters") or {}, index, errors)
        if runtime and operation == "order_test":
            target_key = runtime["runtime_action_ids"][0]
            if facts is not None and target_key in facts.list_values("pending_test"):
                errors.append(_error("PENDING_TEST_DUPLICATE", f"{target_key} 仍待完成，不能重复申请", index=index, action=operation, recommendation_id=rec["id"]))
        if operation == "continue" and facts is not None:
            active = [str(item).casefold() for item in facts.list_values("active_treatment")]
            if not active:
                errors.append(_error("NO_ACTIVE_TREATMENT", "尚无活动治疗，不能 continue", index=index, action=operation, recommendation_id=rec["id"]))
            elif runtime and runtime["runtime_action_ids"][0].casefold() not in active:
                errors.append(_error("ACTION_NOT_SUPPORTED", f"{runtime['runtime_action_ids'][0]} 不是当前活动治疗", index=index, action=operation, recommendation_id=rec["id"]))
        if facts is not None and operation in {"wait", "continue"} and str(rec.get("action_id") or "") == "action:observation":
            if operation == "wait" and facts.list_values("active_treatment"):
                errors.append(_error("ACTIVE_TREATMENT_PRESENT", "仍有活动治疗，不能伪装为未治疗观察", index=index, action=operation, recommendation_id=rec["id"]))
            stable, reasons = stable_clinical_assessment(facts)
            if not stable:
                errors.append(_error("CLINICAL_REVIEW_REQUIRED", f"观察需要明确稳定临床评估：{'；'.join(reasons)}", index=index, action=operation, recommendation_id=rec["id"]))
    return _result(index, operation, target_id, errors, recommendation_ids, offset=offset)


def _resolve_target(graph: KnowledgeGraph, target_text: str, operation: str) -> tuple[str | None, dict[str, Any] | None]:
    if operation == "wait" and not target_text:
        return "action:observation", None
    if not target_text:
        return None, _error("ACTION_NOT_SUPPORTED", "动作缺少 target")
    resolution = graph.resolve_term(target_text)
    if resolution["status"] == "unresolved":
        return None, _error("TERM_UNRESOLVED", f"未识别的动作术语：{target_text}")
    if resolution["status"] == "ambiguous":
        return None, _error("TERM_AMBIGUOUS", f"动作术语存在多个候选：{target_text}")
    node_id = resolution["candidates"][0]["id"]
    if graph.nodes.get(node_id, {}).get("type") != "action":
        return None, _error("ACTION_NOT_SUPPORTED", f"{target_text} 不是可执行动作")
    return node_id, None


def _cited_recommendations(
    graph: KnowledgeGraph,
    context_by_id: Mapping[str, Mapping[str, Any]],
    excluded_by_id: Mapping[str, Mapping[str, Any]],
    raw: Mapping[str, Any],
    target_id: str | None,
    operation: str,
    errors: list[dict[str, Any]],
) -> list[Mapping[str, Any]]:
    cited_ids = [str(item) for item in raw.get("guideline_rule_ids") or ()]
    cited = []
    for rec_id in cited_ids:
        if rec_id in excluded_by_id:
            errors.append(_error("CONTRAINDICATION_PRESENT", f"{rec_id} 在本轮被排除", recommendation_id=rec_id))
            continue
        if rec_id not in context_by_id:
            errors.append(_error("RECOMMENDATION_NOT_RETRIEVED", f"{rec_id} 不在本轮知识上下文", recommendation_id=rec_id))
            continue
        cited.append(context_by_id[rec_id])
    if cited:
        return cited
    if target_id is None:
        return []
    matches = [
        row for row in context_by_id.values()
        if row.get("action_id") == target_id and operation in set(row.get("supported_operations") or ())
    ]
    return matches if len(matches) == 1 else []


def _runtime_check(
    graph: KnowledgeGraph,
    rec: Mapping[str, Any],
    target_id: str | None,
    operation: str,
    parameters: Mapping[str, Any],
    index: int,
    errors: list[dict[str, Any]],
) -> Mapping[str, Any] | None:
    if not target_id:
        return None
    action = graph.actions.get(target_id, {})
    for binding_id in rec.get("runtime_binding_ids") or ():
        binding = graph.bindings.get(str(binding_id))
        if not binding or not bool(binding.get("executable")):
            continue
        if str(binding.get("action_id") or "") != target_id:
            continue
        runtime_ids = [str(item) for item in binding.get("runtime_action_ids") or ()]
        operations = set(binding.get("operations") or ())
        if operation not in operations:
            errors.append(_error("ACTION_NOT_SUPPORTED", f"{target_id} 的运行时绑定不支持 {operation}", index=index, action=operation))
            continue
        allowed = set(binding.get("parameters") or ())
        bad = [name for name in parameters if name not in allowed or name not in _ALLOWED_PARAMETERS]
        if bad:
            errors.append(_error("PARAMETER_NOT_SUPPORTED", f"{target_id} 不支持参数：{','.join(bad)}", index=index, action=operation))
            continue
        return binding
    errors.append(_error("ACTION_NOT_SUPPORTED", f"{target_id} 没有可执行的运行时绑定", index=index, action=operation))
    return None


def _check_combination(graph: KnowledgeGraph, actions: Sequence[Mapping[str, Any]], facts: FactStore | None = None) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    zero = [raw for raw in actions if _offset(raw) == 0]
    by_target: dict[str, set[str]] = defaultdict(set)
    for raw in zero:
        operation = str(raw.get("operation") or "")
        target = str(raw.get("target") or "")
        by_target[target].add(operation)
    for target, operations in by_target.items():
        if "start" in operations and "stop" in operations:
            errors.append(_error("RECOMMENDATION_CONFLICT", f"同一时点对 {target} 同时 start 与 stop", offset_hours=0))

    active = _active_action_ids(graph, facts)
    ordered = sorted(enumerate(actions), key=lambda item: (_offset(item[1]), item[0]))
    for index, raw in ordered:
        operation = str(raw.get("operation") or "")
        target_id, _ = _resolve_target(graph, str(raw.get("target") or ""), operation)
        if not target_id:
            continue
        offset = _offset(raw)
        if operation == "start":
            active.add(target_id)
        elif operation == "stop":
            if target_id not in active:
                errors.append(_error("ACTION_NOT_SUPPORTED", f"{target_id} 不是当前活动治疗，不能 stop", index=index, offset_hours=offset))
            active.discard(target_id)
        groups: dict[str, list[str]] = defaultdict(list)
        for action_id in active:
            for binding_id in _bindings_for_action(graph, action_id):
                binding = graph.bindings.get(binding_id) or {}
                group = str(binding.get("exclusive_group") or "")
                if group:
                    groups[group].append(action_id)
        for group, targets in groups.items():
            unique = sorted(set(targets))
            if len(unique) > 1:
                errors.append(_error(
                    "RECOMMENDATION_CONFLICT",
                    f"offset {offset:g}h 启动的 {target_id} 与当前互斥治疗冲突：{','.join(unique)}",
                    index=index, offset_hours=offset,
                ))
    return errors


def _active_action_ids(graph: KnowledgeGraph, facts: FactStore | None) -> set[str]:
    active: set[str] = set()
    if facts is None:
        return active
    for value in facts.list_values("active_treatment"):
        resolution = graph.resolve_term(str(value))
        if resolution["status"] == "resolved":
            candidate = resolution["candidates"][0]
            if candidate["type"] == "action":
                active.add(candidate["id"])
    return active


def _bindings_for_action(graph: KnowledgeGraph, action_id: str) -> list[str]:
    return [
        binding_id for binding_id, binding in graph.bindings.items()
        if str(binding.get("action_id") or "") == action_id
    ]


def _result(
    index: int,
    operation: str,
    target_id: str | None,
    errors: Sequence[Mapping[str, Any]],
    recommendation_ids: Sequence[str] = (),
    *,
    offset: float = 0,
) -> dict[str, Any]:
    status = "blocked"
    if errors and all(error["code"] in {"INDICATION_UNKNOWN", "EVIDENCE_UNREVIEWED", "CLINICAL_REVIEW_REQUIRED"} for error in errors):
        status = "needs_information"
    if not errors:
        status = "supported"
    return {
        "index": index,
        "offset_hours": offset,
        "operation": operation,
        "target": target_id,
        "recommendation_ids": list(recommendation_ids),
        "status": status,
        "errors": list(errors),
    }


def _blocked(index: int, operation: str, target: str, code: str, message: str, *, offset: float = 0) -> dict[str, Any]:
    return _result(index, operation, None, [_error(code, message, index=index, action=operation, target=target)], offset=offset)


def _offset(raw: Mapping[str, Any]) -> float:
    try:
        return float(raw.get("offset_hours") or 0)
    except (TypeError, ValueError):
        return -1.0


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **{key: value for key, value in extra.items() if value not in (None, "")}}
