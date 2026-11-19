"""Graph-RAG retrieval over the EHR-style knowledge graph.

The retriever resolves terms, builds seeds from explicit queries and the
visible patient facts, traverses the typed graph with a bounded budget, and
evaluates every recommendation's population, indication, exclusion, temporal,
evidence and runtime dimensions with the four-state condition engine.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from ..patient_graph import build_patient_graph
from ..core.his import PatientSnapshot
from .conditions import ConditionEvaluation, evaluate_condition_ids, evaluate
from .model import KnowledgeGraph
from .patient_facts import patient_fact_store

ALLOWED_TRAVERSAL = {
    "ADDRESSED_BY", "RECOMMENDS", "CONSTRAINS", "APPLIES_TO", "REQUIRES",
    "EXCLUDES", "MONITOR_WITH", "SUPPORTED_BY", "PART_OF_SOURCE", "BINDS_TO",
    "CONFLICTS_WITH",
}


def retrieve_knowledge(
    graph: KnowledgeGraph,
    snapshot: PatientSnapshot,
    queries: Sequence[str],
    *,
    intent: str | None = None,
    limit: int = 18,
    max_hops: int = 3,
    extra_seeds: Sequence[str] = (),
) -> dict[str, Any]:
    limit = max(1, int(limit))
    facts = patient_fact_store(snapshot)
    resolutions = graph.resolve_terms(queries)
    resolved_intent, intent_method = classify_intent(queries, intent)
    seed_ids, seed_reasons = _seeds(graph, facts, resolutions)
    for seed_id in extra_seeds:
        if seed_id in graph.nodes:
            seed_ids.add(seed_id)
            seed_reasons.setdefault(seed_id, "patient_graph")
    ambiguous = [row for row in resolutions if row["status"] == "ambiguous"]
    paths: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for seed_id in seed_ids:
        _collect_paths(graph, seed_id, paths, max_hops=max_hops)
    superseded_skipped = [rid for rid in paths if _is_superseded(graph, rid)]
    candidate_ids = [rid for rid in paths if rid not in set(superseded_skipped)]
    query_seed_ids = {
        row["candidates"][0]["id"] for row in resolutions if row["status"] == "resolved"
    }
    evaluated = {rid: _evaluate_recommendation(graph, facts, rid) for rid in candidate_ids}
    deduped: list[str] = []
    seen_keys: dict[tuple, str] = {}
    for rid in sorted(
        candidate_ids,
        key=lambda item: (
            -_query_action_match(graph.recommendations[item], query_seed_ids),
            -_intent_operation_match(graph.recommendations[item], resolved_intent),
            -_intent_direction_match(graph.recommendations[item], resolved_intent),
            -_restriction_priority(evaluated[item]),
            -_rank_for_rec(graph, item, evaluated[item], paths[item], resolved_intent, query_seed_ids),
            min(len(path) for path in paths[item]),
            item,
        ),
    ):
        rec = graph.recommendations[rid]
        key = rec.get("canonical_recommendation_id") or (
            rec.get("action_id"), rec.get("direction"), rec.get("summary")
        )
        if key in seen_keys:
            continue
        seen_keys[key] = rid
        deduped.append(rid)
    candidate_ids = deduped
    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for rec_id in candidate_ids:
        if len(selected) + len(excluded) >= limit * 2:
            break
        evaluation = evaluated[rec_id]
        entry = _recommendation_entry(graph, rec_id, evaluation, paths[rec_id])
        if evaluation["dimensions"]["population"] == "not_met" or evaluation["dimensions"]["exclusion"] == "met":
            excluded.append({
                "id": rec_id,
                "direction": entry["direction"],
                "action_id": entry.get("action_id"),
                "reason": "POPULATION_MISMATCH" if evaluation["dimensions"]["population"] == "not_met" else "CONTRAINDICATION_PRESENT",
                "applicability": entry["applicability"],
                "missing_information": entry["missing_information"],
                "fact_references": entry.get("fact_references") or {},
            })
        elif len(selected) < limit:
            selected.append(entry)
    selected_ids = {row["recommendation_id"] for row in selected}
    for row in selected:
        for other in row.get("conflicts_with") or ():
            if other in selected_ids and other > row["recommendation_id"]:
                conflicts.append({
                    "recommendations": [row["recommendation_id"], other],
                    "status": "unresolved",
                    "reason": "两条推荐在相同动作/人群上给出不同方向；未按票数或年份自动选择。",
                })
    used_nodes, used_edges = _used_subgraph(graph, selected, paths)
    panels = _available_panels(graph, facts)
    recommended_panels = _recommended_panels(graph, selected, facts)
    available_ids = {row["id"] for row in panels}
    unavailable_monitoring = [
        row for row in recommended_panels if row["id"] not in available_ids
    ]
    metric_nodes, metric_edges = _panel_metric_nodes(panels)
    status = "ok"
    if not selected and not excluded:
        status = "needs_disambiguation" if ambiguous and not seed_ids else "no_results"
    elif any(row["missing_information"] for row in selected) or ambiguous:
        status = "partial"
    diagnostics = {
        "queries": list(queries),
        "intent": resolved_intent,
        "intent_method": intent_method,
        "term_resolutions": resolutions,
        "seeds": [
            {"node_id": node_id, "reason": seed_reasons.get(node_id, "query")}
            for node_id in sorted(seed_ids)
        ],
        "candidate_recommendations": len(candidate_ids),
        "budget": {"max_recommendations": limit, "max_hops": max_hops},
        "truncated": len(candidate_ids) > len(selected) + len(excluded),
        "ranking": "intent + applicability + evidence/source status + path length; not clinical authority",
        "deduped_candidates": len(paths) - len(candidate_ids) - len(superseded_skipped),
        "superseded_skipped": superseded_skipped,
        "intent_note": "intent does not bypass population/indication checks",
        "index_status": "ready",
        "source_status": "available",
        "system_error": None,
        "failure_kind": (
            "no_results" if status == "no_results"
            else "term_ambiguous" if status == "needs_disambiguation"
            else "condition_unknown" if status == "partial"
            else None
        ),
    }
    return {
        "schema_version": "knowledge-context",
        "knowledge_context_id": f"knowledge-{uuid4().hex}",
        "retrieval_id": f"retrieval-{uuid4().hex}",
        "knowledge_release_id": graph.release.release_id,
        "release_sha256": graph.release.content_sha256,
        "snapshot_id": snapshot.context_id,
        "snapshot_sha256": build_patient_graph(snapshot).snapshot_sha256,
        "last_visible_event_sequence": len(snapshot.visible_records()),
        "queries": list(queries),
        "effective_queries": [row["normalized"] for row in resolutions if row["status"] == "resolved"],
        "term_resolutions": resolutions,
        "recommendations": selected,
        "excluded_recommendations": excluded[:limit],
        "available_monitoring_panels": panels,
        "recommended_monitoring_panels": recommended_panels,
        "unavailable_monitoring": unavailable_monitoring,
        "conflicts": conflicts,
        "missing_information": sorted({
            item for row in selected for item in row.get("missing_information") or ()
        }),
        "sources": _used_sources(graph, used_nodes),
        "evidence_spans": _used_spans(graph, used_nodes),
        "graph": {
            "nodes": [_node_dict(graph, node_id) for node_id in sorted(used_nodes) if node_id in graph.nodes] + metric_nodes,
            "edges": [edge for edge in graph.edges if edge["source"] in used_nodes and edge["target"] in used_nodes] + metric_edges,
        },
        "patient_facts": facts.compact(),
        "diagnostics": diagnostics,
        "status": status,
        "knowledge_status": "ready",
        "index_status": "ready",
        "boundary": "DEMO/研究预览：患者事实图仅用于记录定位；推荐、条件和动作资格不是临床诊断或治疗授权。",
    }


INTENT_CATEGORIES = (
    "assess", "start", "adjust", "maintain", "stop", "observe", "review", "conflict_check",
)
_INTENT_PATTERNS = (
    ("conflict_check", ("冲突", "矛盾", "conflict", "contradict")),
    ("stop", ("停止", "停用", "撤机", "停药", "stop", "discontinue", "wean")),
    ("adjust", ("调整", "加量", "减量", "调参", "adjust", "titrate", "increase", "decrease")),
    ("start", ("开始", "启动", "启用", "start", "initiate")),
    ("maintain", ("维持", "继续", "continue", "maintain", "maintenance")),
    ("observe", ("观察", "等待", "observe", "wait")),
    ("review", ("复查", "复评", "监测", "review", "recheck", "monitor")),
    ("assess", ("评估", "诊断", "鉴别", "assess", "diagnose", "evaluation")),
)


def classify_intent(queries: Sequence[str], explicit: str | None = None) -> tuple[str, str]:
    """Classify the query into one of the P0 intent categories."""
    if explicit:
        value = str(explicit).strip()
        if value in INTENT_CATEGORIES:
            return value, "explicit"
    text = " ".join(str(item) for item in queries).casefold()
    for category, needles in _INTENT_PATTERNS:
        if any(needle.casefold() in text for needle in needles):
            return category, "rule"
    return "assess", "default"


def _intent_bonus(rec: Mapping[str, Any], intent: str) -> float:
    operations = set(rec.get("supported_operations") or ())
    direction = str(rec.get("direction") or "")
    if intent == "start":
        return 2.0 if direction in {"support", "consider"} and "start" in operations else -0.5
    if intent == "adjust":
        return 2.0 if "adjust" in operations else 0.0
    if intent == "maintain":
        return 2.0 if "continue" in operations else 0.0
    if intent == "stop":
        return 2.0 if "stop" in operations else 0.0
    if intent == "observe":
        return 2.0 if "wait" in operations or direction == "monitor" else 0.0
    if intent == "review":
        return 1.0 if rec.get("monitoring_panels") or "order_test" in operations else 0.0
    if intent == "conflict_check":
        return 1.0 if rec.get("conflicts_with") else 0.0
    return 0.0


def _restriction_priority(evaluation: Mapping[str, Any]) -> int:
    dims = evaluation.get("dimensions") or {}
    if dims.get("exclusion") == "met" or dims.get("indication") == "not_met":
        return 2
    if any(status in {"unknown", "conflict"} for status in dims.values()) or evaluation.get("missing"):
        return 1
    return 0


def _rank_score(
    graph: KnowledgeGraph,
    evaluation: Mapping[str, Any],
    paths: Sequence[Sequence[str]],
    intent: str,
) -> float:
    dims = evaluation.get("dimensions") or {}
    score = 0.0
    for key, value in (("population", 1.0), ("indication", 1.0), ("exclusion", 0.5),
                       ("setting", 0.3), ("evidence_status", 0.5), ("runtime_capability", 0.2)):
        if dims.get(key) == ("not_met" if key == "exclusion" else "met"):
            score += value
    score -= 0.1 * min(len(path) for path in paths) if paths else 0.0
    return score


def _rank_for_rec(
    graph: KnowledgeGraph,
    rec_id: str,
    evaluation: Mapping[str, Any],
    paths: Sequence[Sequence[str]],
    intent: str,
    query_seeds: set[str] | None = None,
) -> float:
    rec = graph.recommendations[rec_id]
    score = _rank_score(graph, evaluation, paths, intent) + _intent_bonus(rec, intent)
    operations = set(rec.get("supported_operations") or ())
    if intent == "stop" and operations == {"stop"}:
        score += 1.0
    elif intent == "start" and operations == {"start"}:
        score += 0.5
    if query_seeds:
        query_bonus = 0.0
        for path in paths:
            if not path or path[0] not in query_seeds:
                continue
            bonus = 1.5
            if (graph.nodes.get(path[0]) or {}).get("type") == "action":
                bonus += 3.0
            query_bonus = max(query_bonus, bonus)
        score += query_bonus
    if str(rec.get("review_status") or "") in {"reviewed", "approved"}:
        score += 0.3
    for span_id in rec.get("evidence_span_ids") or ():
        if str((graph.spans.get(str(span_id)) or {}).get("review_status") or "") in {"reviewed", "approved"}:
            score += 0.2
            break
    return score


def _is_superseded(graph: KnowledgeGraph, rec_id: str) -> bool:
    rec = graph.recommendations.get(rec_id) or {}
    target = str(rec.get("superseded_by") or "")
    return bool(target and target in graph.recommendations)


def _recommended_panels(graph: KnowledgeGraph, selected: Sequence[Mapping[str, Any]], facts: Any) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for rec in selected:
        for panel_id in rec.get("monitoring_panels") or ():
            panel = graph.panels.get(str(panel_id))
            if panel is None:
                continue
            row = rows.setdefault(str(panel_id), {
                "id": str(panel_id),
                "label": str(panel.get("label") or panel_id),
                "members": [],
                "available": False,
            })
            for member in panel.get("members") or ():
                key = str(member.get("key") or "")
                snapshot = facts.latest(key)
                row["members"].append({
                    "key": key,
                    "label": str(member.get("label") or key),
                    "initialized": snapshot.status in {"met", "conflict"},
                    "status": snapshot.status,
                })
    for row in rows.values():
        seen: set[str] = set()
        unique = []
        for member in row["members"]:
            if member["key"] in seen:
                continue
            seen.add(member["key"])
            unique.append(member)
        row["members"] = unique
        row["available"] = any(member["initialized"] for member in row["members"])
        row["missing_members"] = [member["key"] for member in row["members"] if not member["initialized"]]
    return list(rows.values())


def _seeds(
    graph: KnowledgeGraph,
    facts: Any,
    resolutions: Sequence[Mapping[str, Any]],
) -> tuple[set[str], dict[str, str]]:
    seeds: set[str] = set()
    reasons: dict[str, str] = {}
    for row in resolutions:
        if row["status"] == "resolved":
            node_id = row["candidates"][0]["id"]
            seeds.add(node_id)
            reasons[node_id] = "query"
    for name in ("diagnosis", "symptom", "active_treatment", "respiratory_support",
                 "care_setting", "pending_test"):
        for value in facts.list_values(name):
            resolved = graph.resolve_term(str(value))
            if resolved["status"] == "resolved":
                node_id = resolved["candidates"][0]["id"]
                seeds.add(node_id)
                reasons.setdefault(node_id, f"patient:{name}")
    if _infection_risk_present(facts) and "concept:infection_risk" in graph.nodes:
        seeds.add("concept:infection_risk")
        reasons.setdefault("concept:infection_risk", "patient:infection_risk")
    # Explicitly abnormal patient facts can seed a concept, but never a diagnosis.
    spo2 = facts.latest("spo2")
    if spo2.status == "met" and isinstance(spo2.value, (int, float)) and spo2.value <= 90:
        seeds.add("concept:hypoxemia")
        reasons["concept:hypoxemia"] = "patient:spo2"
    if not seeds and facts.has("ga_weeks"):
        seeds.add("concept:assessment")
        reasons["concept:assessment"] = "patient:assessment"
    return seeds, reasons


def _infection_risk_present(facts: Any) -> bool:
    gbs = facts.latest("gbs_status")
    if gbs.status == "met" and str(gbs.value).casefold() in {"positive", "pos", "阳性"}:
        return True
    for name, threshold in (("prom_hours", 18.0), ("maternal_fever_c", 38.0)):
        snap = facts.latest(name)
        if snap.status == "met" and isinstance(snap.value, (int, float)) and snap.value >= threshold:
            return True
    return False


def _collect_paths(
    graph: KnowledgeGraph,
    seed_id: str,
    paths: dict[str, list[tuple[str, ...]]],
    *,
    max_hops: int,
) -> None:
    if seed_id not in graph.nodes:
        return
    queue: deque[tuple[str, tuple[str, ...]]] = deque([(seed_id, (seed_id,))])
    seen: set[str] = {seed_id}
    while queue:
        node_id, path = queue.popleft()
        node = graph.nodes[node_id]
        if node["type"] == "recommendation":
            if len(paths[node_id]) < 3:
                paths[node_id].append(path)
            continue
        if len(path) - 1 >= max_hops:
            continue
        for other, edge in graph.adjacency.get(node_id, ()):
            if edge["relation"] not in ALLOWED_TRAVERSAL or other in seen:
                continue
            seen.add(other)
            queue.append((other, (*path, other)))


def _evaluate_recommendation(graph: KnowledgeGraph, facts: Any, rec_id: str) -> dict[str, Any]:
    rec = graph.recommendations[rec_id]
    dimensions: dict[str, str] = {}
    missing: list[str] = []
    refs: dict[str, list[str]] = {}
    population_id = str(rec.get("population_id") or "")
    if population_id and population_id in graph.populations:
        evaluation = evaluate(graph.populations[population_id].get("expr") or {}, facts)
    else:
        evaluation = ConditionEvaluation("unknown", detail="population missing")
    dimensions["population"] = evaluation.status
    refs["population"] = list(evaluation.fact_refs)
    if evaluation.status == "unknown":
        missing.append(f"人群条件未知：{population_id or '未绑定'}")
    elif evaluation.status == "conflict":
        missing.append(f"人群条件存在冲突值：{population_id or '未绑定'}")
    indications = evaluate_condition_ids(rec.get("indications") or (), graph.conditions, facts, mode="all")
    dimensions["indication"] = indications.status
    refs["indication"] = list(indications.fact_refs)
    if indications.status == "unknown":
        missing.append("临床指征仍有未知项")
    elif indications.status == "conflict":
        missing.append("临床指征存在冲突值")
    elif indications.status == "not_met":
        missing.append("临床指征未满足")
    exclusions = evaluate_condition_ids(rec.get("exclusions") or (), graph.conditions, facts, mode="any")
    dimensions["exclusion"] = exclusions.status
    refs["exclusion"] = list(exclusions.fact_refs)
    if exclusions.status == "met":
        missing.append("存在排除条件")
    elif exclusions.status == "unknown":
        missing.append("排除条件未知")
    elif exclusions.status == "conflict":
        missing.append("排除条件存在冲突值")
    setting = _evaluate_setting(graph, facts, rec)
    dimensions["setting"] = setting.status
    refs["setting"] = list(setting.fact_refs)
    if setting.status == "unknown":
        missing.append("诊疗场景条件未知")
    elif setting.status == "conflict":
        missing.append("诊疗场景条件存在冲突值")
    elif setting.status == "not_met":
        missing.append("诊疗场景不匹配")
    unformalized = [str(item) for item in rec.get("unformalized_conditions") or () if str(item).strip()]
    dimensions["unformalized"] = "unknown" if unformalized else "met"
    if unformalized:
        missing.append("存在不可形式化的临床条件：" + "；".join(unformalized))
    temporal = evaluate_condition_ids(rec.get("temporal_conditions") or (), graph.conditions, facts, mode="all")
    dimensions["temporal"] = temporal.status
    refs["temporal"] = list(temporal.fact_refs)
    if temporal.status in {"unknown", "not_met"}:
        missing.append("时间条件未满足或未知")
    elif temporal.status == "conflict":
        missing.append("时间条件存在冲突值")
    evidence_status = _evidence_status(graph, rec)
    dimensions["evidence_status"] = evidence_status
    if evidence_status != "met":
        missing.append("证据片段未审核或缺失")
    runtime_status = _runtime_status(graph, rec)
    dimensions["runtime_capability"] = runtime_status
    if runtime_status != "met":
        missing.append("运行时动作未绑定")
    return {"dimensions": dimensions, "missing": list(dict.fromkeys(missing)), "fact_refs": refs}


def _evaluate_setting(graph: KnowledgeGraph, facts: Any, rec: Mapping[str, Any]) -> ConditionEvaluation:
    setting_id = str(rec.get("setting_id") or "")
    if not setting_id:
        return ConditionEvaluation("met", detail="no setting restriction")
    setting = graph.settings.get(setting_id)
    if setting is None:
        return ConditionEvaluation("unknown", detail=f"unknown setting: {setting_id}")
    expr = setting.get("expr")
    if not expr:
        return ConditionEvaluation("met", detail=f"setting:{setting_id}")
    return evaluate(expr, facts)


def _evidence_status(graph: KnowledgeGraph, rec: Mapping[str, Any]) -> str:
    span_ids = [str(item) for item in rec.get("evidence_span_ids") or ()]
    if not span_ids:
        return "not_met"
    statuses = []
    for span_id in span_ids:
        span = graph.spans.get(span_id)
        if span is None:
            statuses.append("unknown")
        elif str(span.get("review_status") or "") in {"synthetic_fixture", "reviewed", "approved"}:
            statuses.append("met")
        else:
            statuses.append("not_met")
    if any(status == "not_met" for status in statuses):
        return "not_met"
    if any(status == "unknown" for status in statuses):
        return "unknown"
    return "met"


def _runtime_status(graph: KnowledgeGraph, rec: Mapping[str, Any]) -> str:
    binding_ids = [str(item) for item in rec.get("runtime_binding_ids") or ()]
    if not binding_ids:
        return "not_met"
    for binding_id in binding_ids:
        binding = graph.bindings.get(binding_id)
        if binding and bool(binding.get("executable")):
            operations = set(binding.get("operations") or ())
            if operations & set(rec.get("supported_operations") or ()):
                return "met"
    return "not_met"


def _recommendation_entry(
    graph: KnowledgeGraph,
    rec_id: str,
    evaluation: Mapping[str, Any],
    paths: Sequence[Sequence[str]],
) -> dict[str, Any]:
    rec = graph.recommendations[rec_id]
    action = graph.actions.get(str(rec.get("action_id") or ""), {})
    dims = dict(evaluation["dimensions"])
    eligible = _is_eligible(rec, dims)
    span_ids = [str(item) for item in rec.get("evidence_span_ids") or ()]
    source_ids = list(dict.fromkeys(
        str(graph.spans[span].get("source_id"))
        for span in span_ids if span in graph.spans and graph.spans[span].get("source_id")
    ))
    return {
        "recommendation_id": rec_id,
        "revision_id": str(rec.get("revision_id") or ""),
        "summary": str(rec.get("summary") or ""),
        "direction": str(rec.get("direction") or ""),
        "action_id": str(rec.get("action_id") or ""),
        "action_label": str(action.get("label") or ""),
        "supported_operations": list(rec.get("supported_operations") or ()),
        "applicability": dims,
        "eligible_for_simulation": eligible,
        "missing_information": list(evaluation["missing"]),
        "fact_references": evaluation["fact_refs"],
        "evidence_span_ids": span_ids,
        "evidence_paths": _path_strings(graph, paths),
        "source_ids": source_ids,
        "runtime_binding_ids": list(rec.get("runtime_binding_ids") or ()),
        "monitoring_panels": list(rec.get("monitoring_panels") or ()),
        "review_status": str(rec.get("review_status") or ""),
        "conflicts_with": list(rec.get("conflicts_with") or ()),
    }


def _query_action_match(rec: Mapping[str, Any], query_seeds: set[str]) -> int:
    return 2 if str(rec.get("action_id") or "") in query_seeds else 0


def _intent_operation_match(rec: Mapping[str, Any], intent: str) -> int:
    operations = set(rec.get("supported_operations") or ())
    direction = str(rec.get("direction") or "")
    if intent in {"start", "stop", "adjust", "maintain"} and direction not in {"support", "consider"}:
        return 0
    exact = {
        "start": {"start"}, "adjust": {"adjust"}, "maintain": {"continue"},
        "stop": {"stop"}, "observe": {"wait"}, "review": {"order_test"},
    }.get(intent)
    if exact is None:
        return 0
    if operations == exact:
        return 2
    if operations & exact:
        return 1
    return 0


def _intent_direction_match(rec: Mapping[str, Any], intent: str) -> int:
    direction = str(rec.get("direction") or "")
    if intent == "observe":
        return {"monitor": 2, "not_routine": 2, "background": 1}.get(direction, 0)
    if intent in {"start", "stop", "adjust", "maintain"}:
        return 1 if direction in {"support", "consider"} else 0
    if intent == "conflict_check":
        return 1 if rec.get("conflicts_with") else 0
    return 0


def _is_eligible(rec: Mapping[str, Any], dims: Mapping[str, str]) -> bool:
    return (
        str(rec.get("direction") or "") in {"support", "consider", "monitor"}
        and dims.get("population") == "met"
        and dims.get("indication") == "met"
        and dims.get("temporal") == "met"
        and dims.get("exclusion") == "not_met"
        and dims.get("setting", "met") == "met"
        and dims.get("unformalized", "met") == "met"
        and dims.get("evidence_status") == "met"
        and dims.get("runtime_capability") == "met"
    )


def _path_strings(graph: KnowledgeGraph, paths: Sequence[Sequence[str]]) -> list[list[str]]:
    rendered = []
    for path in paths[:3]:
        steps = []
        for source, target in zip(path, path[1:]):
            forward = next(
                (edge for edge in graph.edges
                 if edge["source"] == source and edge["target"] == target),
                None,
            )
            if forward is not None:
                steps.append(f"{source} -[{forward['relation']}]-> {target}")
                continue
            reverse = next(
                (edge for edge in graph.edges
                 if edge["source"] == target and edge["target"] == source),
                None,
            )
            if reverse is not None:
                # Render the real edge direction; a path must not claim a
                # relation that does not exist in the release.
                steps.append(f"{reverse['source']} -[{reverse['relation']}]-> {reverse['target']}")
            else:
                steps.append(f"{source} -[RELATED]-> {target}")
        rendered.append(steps)
    return rendered


def _used_subgraph(
    graph: KnowledgeGraph,
    selected: Sequence[Mapping[str, Any]],
    paths: Mapping[str, Sequence[Sequence[str]]],
) -> tuple[set[str], list[dict[str, Any]]]:
    used: set[str] = set()
    for row in selected:
        rec_id = row["recommendation_id"]
        used.add(rec_id)
        used.add(row["action_id"])
        for path in paths.get(rec_id, ()):
            used.update(path)
        for span_id in row.get("evidence_span_ids") or ():
            used.add(span_id)
        for source_id in row.get("source_ids") or ():
            used.add(source_id)
        for binding_id in row.get("runtime_binding_ids") or ():
            used.add(binding_id)
        rec = graph.recommendations[rec_id]
        if rec.get("population_id"):
            used.add(str(rec["population_id"]))
        used.update(str(item) for item in rec.get("indications") or ())
        used.update(str(item) for item in rec.get("exclusions") or ())
        used.update(str(item) for item in rec.get("monitoring_panels") or ())
    return {node_id for node_id in used if node_id in graph.nodes}, []


def _available_panels(graph: KnowledgeGraph, facts: Any) -> list[dict[str, Any]]:
    panels = []
    for panel_id, panel in graph.panels.items():
        members = []
        for member in panel.get("members") or ():
            key = str(member.get("key") or "")
            snapshot = facts.latest(key)
            if snapshot.status in {"met", "conflict"}:
                members.append({"key": key, "label": str(member.get("label") or key), "status": snapshot.status})
        if members:
            panels.append({
                "id": panel_id,
                "label": str(panel.get("label") or panel_id),
                "metric_count": len(members),
                "metric_keys": [item["key"] for item in members],
                "members": members,
            })
    return panels


def _panel_metric_nodes(panels: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for panel in panels:
        for member in panel.get("members") or ():
            metric_id = f"metric:{member['key']}"
            nodes.append({"id": metric_id, "type": "patient_metric", "label": member["label"]})
            edges.append({"source": panel["id"], "relation": "CONTAINS_INITIALIZED_METRIC", "target": metric_id})
    return nodes, edges


def _used_sources(graph: KnowledgeGraph, used_nodes: Iterable[str]) -> dict[str, Any]:
    return {
        node_id: dict(graph.sources[node_id])
        for node_id in used_nodes if node_id in graph.sources
    }


def _used_spans(graph: KnowledgeGraph, used_nodes: Iterable[str]) -> dict[str, Any]:
    return {
        node_id: dict(graph.spans[node_id])
        for node_id in used_nodes if node_id in graph.spans
    }


def _node_dict(graph: KnowledgeGraph, node_id: str) -> dict[str, Any]:
    node = graph.nodes[node_id]
    return {"id": node["id"], "type": node["type"], "label": node["label"], **node["data"]}
