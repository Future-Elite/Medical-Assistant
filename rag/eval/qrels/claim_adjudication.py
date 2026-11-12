"""Claim-level applicability adjudication with a minimal counterfactual certificate.

This is the project's own mechanism, built on the industrial graph pilot but not
a copy of a standard GAG/PEG stack.  For every guideline recommendation it
answers three separate questions:

1. does the visible patient record support the claim's factual premise?
2. does the guideline's scope/exception structure support applying it?
3. if the two sides do not meet, what is the *minimal* structured fact change
   that would make the claim supportable, and which criterion/locator proves it?

The certificate is deliberately limited: it searches the structured facts that
already exist in the snapshot (gestational age, birth weight, respiratory
support).  Time/text gaps are reported as ``requires_unstructured_data``
instead of being guessed.  A certificate is a rule-derived consistency artifact,
not a causal or clinical claim.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import random
from typing import Any, Mapping, Sequence

from ...his import PatientSnapshot, snapshot_from_dict
from ...models import SourceDocument
from ...quality.applicability import assess_applicability
from ...quality.guideline_graph import GuidelineGraph, assess_guideline_graph
from .decision_triage import SIM_VALUES, apply_value_to_snapshot
from .gold_rules import load_criteria_fixture
from .rubric import build_documents

_SEARCH_FIELDS = (
    ("derived_birth.birth_weight_g", "birth_weight_g"),
    ("derived_birth.ga_days", "ga_days"),
    ("derived_respiratory_support.mode", "respiratory_mode"),
)


@dataclass(frozen=True)
class Claim:
    claim_id: str
    text: str
    claim_type: str
    recommendation_id: str | None = None


def adjudicate_claims(
    snapshot: Mapping[str, Any] | PatientSnapshot,
    graph: GuidelineGraph,
    *,
    graph_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Adjudicate each recommendation as a claim with source separation."""
    parsed = snapshot if isinstance(snapshot, PatientSnapshot) else snapshot_from_dict(snapshot)
    result = graph_result or assess_guideline_graph(snapshot, graph)
    claims = [
        _adjudicate_recommendation(item, result, parsed, graph)
        for item in result.get("recommendations", ())
    ]
    summary: dict[str, int] = {}
    certificates: dict[str, int] = {}
    for claim in claims:
        summary[claim["verdict"]] = summary.get(claim["verdict"], 0) + 1
        cert_status = claim["counterfactual_certificate"]["status"]
        certificates[cert_status] = certificates.get(cert_status, 0) + 1
    return {
        "schema_version": "claim-adjudication-v1",
        "guideline_id": graph.guideline_id,
        "claims": claims,
        "summary": summary,
        "certificate_summary": certificates,
        "not_clinical_advice": True,
        "boundaries": [
            "主张级裁定只针对指南建议的适用性，不是临床结论。",
            "患者侧与指南侧分开；一侧支持不等于另一侧支持。",
            "反事实证书只搜索已有结构化字段，不猜时间/文本缺失。",
            "证书是规则一致性工件，不是因果解释，也不是临床建议。",
        ],
    }


def _adjudicate_recommendation(
    item: Mapping[str, Any], graph_result: Mapping[str, Any],
    snapshot: PatientSnapshot, graph: GuidelineGraph,
) -> dict[str, Any]:
    scope = graph_result.get("scope", {})
    patient_status = _patient_side_status(item, scope)
    guideline_status = _guideline_side_status(item)
    verdict = _verdict(patient_status, guideline_status)
    certificate = _certificate(
        snapshot=snapshot, graph=graph, item=item, scope=scope,
        patient_status=patient_status, verdict=verdict,
    )
    return {
        "claim_id": f"claim:{item.get('recommendation_id')}",
        "recommendation_id": item.get("recommendation_id"),
        "text": f"该患儿满足「{item.get('statement', '')}」的适用条件。",
        "claim_type": "recommendation_applicability",
        "verdict": verdict,
        "patient_side": {
            "status": patient_status,
            "evidence": _patient_evidence(item),
        },
        "guideline_side": {
            "status": guideline_status,
            "recommendation_id": item.get("recommendation_id"),
            "evidence_level": item.get("evidence_level"),
            "locators": (item.get("audit") or {}).get("source_locators", []),
            "exceptions": [
                {
                    "exception_id": exc.get("exception_id"),
                    "kind": exc.get("kind"),
                    "status": exc.get("status"),
                    "action": exc.get("action"),
                }
                for exc in item.get("exceptions", ())
                if exc.get("status") != "not_found"
            ],
        },
        "counterfactual_certificate": certificate,
        "next_action": _next_action(verdict, certificate, item),
        "audit": {
            "scope_status": scope.get("status"),
            "recommendation_status": item.get("status"),
            "patient_record_paths": _patient_record_paths(item),
            "guideline_locators": (item.get("audit") or {}).get("source_locators", []),
            "fusion_rule": (item.get("audit") or {}).get("fusion_rule"),
        },
    }


def _patient_side_status(item: Mapping[str, Any], scope: Mapping[str, Any]) -> str:
    if scope.get("status") == "mismatch":
        return "contradicts"
    if scope.get("status") == "unknown":
        return "unknown"
    status = str(item.get("status") or "")
    if status in {"applicable", "applicable_with_caution"}:
        return "supports"
    if status in {"exception_triggered", "needs_review"}:
        return "supports_scope_with_exception"
    return "unknown"


def _guideline_side_status(item: Mapping[str, Any]) -> str:
    status = str(item.get("status") or "")
    if status in {"applicable", "applicable_with_caution"}:
        return "supports"
    if status == "exception_triggered":
        return "contradicts"
    if status == "needs_review":
        return "review"
    if status == "not_applicable":
        return "not_applicable"
    return "unknown"


def _verdict(patient_status: str, guideline_status: str) -> str:
    if guideline_status == "contradicts":
        return "guideline_exception"
    if guideline_status == "review":
        return "guideline_review"
    if guideline_status == "not_applicable":
        return "not_applicable"
    if patient_status == "supports" and guideline_status == "supports":
        return "concordant"
    if patient_status == "contradicts" and guideline_status == "supports":
        return "patient_contradiction"
    return "insufficient_evidence"


def _certificate(
    *, snapshot: PatientSnapshot, graph: GuidelineGraph,
    item: Mapping[str, Any], scope: Mapping[str, Any],
    patient_status: str, verdict: str,
) -> dict[str, Any]:
    if verdict == "concordant":
        return _empty_certificate("not_needed", "患者侧与指南侧都已支持，无需反事实证书。")
    if verdict == "guideline_exception":
        return _empty_certificate("exception_dominates", "指南例外优先，补结构化事实不能覆盖例外。")
    if verdict == "guideline_review":
        return _empty_certificate("review_dominates", "指南侧有需要复核的相关情况；补结构化事实不能替代复核。")
    if verdict == "not_applicable":
        return _empty_certificate("not_applicable", "指南范围不适用，补事实前先确认问题是否问对。")
    if scope.get("status") == "match":
        missing = sorted(set(item.get("missing_facts") or ()))
        if missing:
            return _empty_certificate(
                "requires_unstructured_data",
                "范围已匹配，但结论依赖时间/文本字段；证书不猜这些字段。",
                missing_facts=missing,
            )
        return _empty_certificate("stable_under_search_space", "范围匹配且没有可翻转的结构化字段。")
    candidates, searched = _scope_flip_candidates(snapshot, graph)
    if candidates:
        return {
            "status": "flippable",
            "minimal_changes": candidates,
            "total_cost": searched.get("total_cost", sum(item["cost"] for item in candidates)),
            "search_space": searched,
            "stable": False,
            "note": "最小成本/最小基数结构化改动；达到范围匹配不等于整条建议已满足。",
        }
    missing = sorted(set(item.get("missing_facts") or ()))
    return _empty_certificate(
        "stable_under_search_space" if not missing else "requires_unstructured_data",
        "在当前结构化字段搜索空间内没有找到可翻转改动。" if not missing
        else "缺少时间/文本字段，结构化搜索无法翻转。",
        missing_facts=missing,
        search_space=searched,
    )


_FIELD_COST = {
    "derived_birth.birth_weight_g": 1.0,
    "derived_birth.ga_days": 1.0,
    "derived_respiratory_support.mode": 2.0,
}


def _scope_flip_candidates(
    snapshot: PatientSnapshot, graph: GuidelineGraph, *, max_cardinality: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _minimal_flip_search(
        snapshot, list(graph.scope),
        document_id=f"{graph.guideline_id}::scope",
        title=graph.population, statement=graph.population,
        max_cardinality=max_cardinality,
    )


def _minimal_flip_search(
    snapshot: PatientSnapshot, criteria: Sequence[Mapping[str, Any]],
    *, document_id: str, title: str, statement: str, max_cardinality: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Uniform-cost search for a minimum-cost fact set that flips scope to match.

    The search space is finite and rule-defined.  It is deliberately small
    enough to enumerate exactly, so ``optimal_within_search_space`` means the
    returned set is cost-minimal in that space -- not clinically optimal.
    """
    base = _snapshot_dict(snapshot)
    searched: dict[str, Any] = {
        "algorithm": "uniform_cost_search",
        "max_cardinality": max_cardinality,
        "fields": [],
        "values_per_field": {},
        "nodes_expanded": 0,
    }
    if _criteria_status(base, criteria, document_id, title, statement) == "match":
        searched["already_match"] = True
        return [], searched
    candidates: list[tuple[str, Any, float]] = []
    for field, _ in _SEARCH_FIELDS:
        values = SIM_VALUES.get(field)
        if not values:
            continue
        searched["fields"].append(field)
        searched["values_per_field"][field] = len(values)
        for value in values:
            candidates.append((field, value, _FIELD_COST.get(field, 1.0)))
    searched["candidate_space"] = len(candidates)
    heap: list[tuple[float, int, str, tuple[tuple[str, Any], ...]]] = [
        (0.0, 0, "", ())
    ]
    visited = {()}
    while heap:
        cost, cardinality, _, state = heapq.heappop(heap)
        searched["nodes_expanded"] += 1
        if state:
            updated = base
            for field, value in state:
                updated = apply_value_to_snapshot(updated, field, value)
            if _criteria_status(updated, criteria, document_id, title, statement) == "match":
                changes = [
                    _change_record(snapshot, criteria, field, value)
                    for field, value in state
                ]
                searched["optimal_within_search_space"] = True
                searched["total_cost"] = round(cost, 4)
                return changes, searched
        if cardinality >= max_cardinality:
            continue
        used_fields = {field for field, _ in state}
        for field, value, field_cost in candidates:
            if field in used_fields:
                continue
            new_state = tuple(sorted(state + ((field, value),)))
            if new_state in visited:
                continue
            visited.add(new_state)
            heapq.heappush(
                heap,
                (cost + field_cost, cardinality + 1, repr(new_state), new_state),
            )
    searched["stable_under_search_space"] = True
    return [], searched


def _change_record(
    snapshot: PatientSnapshot, criteria: Sequence[Mapping[str, Any]],
    field: str, value: Any,
) -> dict[str, Any]:
    criterion = _criterion_for_field(criteria, field)
    return {
        "field": field,
        "current": _current_value(snapshot, field),
        "value": value,
        "cost": _FIELD_COST.get(field, 1.0),
        "predicted_scope_status": "match",
        "criterion_id": criterion.get("criterion_id"),
        "locator": (criterion.get("source") or {}).get("locator"),
        "rule": f"{field} {criterion.get('operator')} {criterion.get('value')}",
    }


def _criteria_status(
    snapshot: Mapping[str, Any], criteria: Sequence[Mapping[str, Any]],
    document_id: str, title: str, statement: str,
) -> str:
    document = SourceDocument(
        source_id="claim-adjudication", document_id=document_id,
        kind="guideline_scope", title=title, text=statement,
        metadata={"applicability_criteria": {"logic": "all_of", "criteria": list(criteria)}},
    )
    assessment = assess_applicability(document, snapshot_from_dict(snapshot), [])
    return {"possible_match": "match"}.get(assessment.status, assessment.status)


def _criterion_for_field(
    criteria: Sequence[Mapping[str, Any]], field: str,
) -> Mapping[str, Any]:
    for criterion in criteria:
        if criterion.get("field") == field:
            return criterion
    return {}


def _patient_evidence(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for exception in item.get("exceptions", ()):
        for hit in exception.get("evidence", ()):
            evidence.append({
                "source": "patient_record",
                "record_path": hit.get("record_path"),
                "field": hit.get("field"),
                "span": hit.get("span"),
                "relation": exception.get("kind"),
            })
    for criterion in (item.get("conditions") or {}).get("criteria", ()):
        if criterion.get("status") in {"match", "mismatch"}:
            evidence.append({
                "source": "patient_record",
                "record_path": (criterion.get("record_paths") or [None])[0],
                "field": criterion.get("field"),
                "span": criterion.get("observed_value"),
                "relation": criterion.get("status"),
            })
    text_check = item.get("text_check") or {}
    if text_check.get("value"):
        evidence.append({
            "source": "patient_record",
            "record_path": (item.get("audit") or {}).get("record_paths", [None])[0],
            "field": text_check.get("field"),
            "span": text_check.get("value"),
            "relation": text_check.get("status"),
        })
    return evidence


def _patient_record_paths(item: Mapping[str, Any]) -> list[str]:
    return sorted({
        path for path in (item.get("audit") or {}).get("record_paths", [])
        if path
    })


def _next_action(verdict: str, certificate: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    if verdict == "guideline_exception":
        return {"type": "review_exception", "text": "例外优先，需人工复核记录中的例外；不是停喂医嘱。"}
    if verdict == "guideline_review":
        return {"type": "review_exception", "text": "指南侧有相关情况需复核；不是硬例外，也不是停喂医嘱。"}
    if verdict == "not_applicable":
        return {"type": "confirm_question", "text": "范围不匹配；先确认问题是否指向这份指南。"}
    if verdict == "concordant":
        return {"type": "review_selected", "text": "患者侧与指南侧一致，按原文条件复核。", "locators": (item.get("audit") or {}).get("source_locators", [])}
    if certificate.get("status") == "flippable":
        change = certificate.get("minimal_changes", [{}])[0]
        return {
            "type": "supply_counterfactual_fact",
            "field": change.get("field"),
            "value": change.get("value"),
            "text": f"若 {change.get('field')} 为 {change.get('value')}，范围会翻转；需人工确认该值真实存在。",
        }
    missing = certificate.get("missing_facts") or []
    return {
        "type": "request_unstructured_data" if missing else "review_insufficient",
        "fields": missing,
        "text": "需要补时间/文本字段后才能判断；当前不猜。" if missing else "证据不足，需人工复核。",
    }


def _empty_certificate(status: str, note: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": status,
        "minimal_changes": [],
        "stable": status == "stable_under_search_space",
        "note": note,
        **extra,
    }


def _snapshot_dict(snapshot: PatientSnapshot) -> dict[str, Any]:
    return {
        "patient_ref": snapshot.patient_ref,
        "as_of": snapshot.as_of,
        "source": snapshot.source,
        "facts": [
            {
                "collection": fact.collection,
                "event_type": fact.event_type,
                "record_path": fact.record_path,
                "field_path": fact.field_path,
                "fields": dict(fact.fields),
                "time": fact.time,
                "start": fact.start,
                "end": fact.end,
                "source_type": fact.source_type,
                "extra": dict(fact.extra),
            }
            for fact in snapshot.facts
        ],
        "extra": dict(snapshot.extra),
    }


def _current_value(snapshot: PatientSnapshot, field: str) -> Any:
    for fact in snapshot.facts:
        if field.startswith("derived_birth.") and fact.collection == "derived_birth":
            return fact.fields.get(field.split(".", 1)[1])
        if field == "derived_respiratory_support.mode" and fact.collection == "derived_respiratory_support":
            return fact.fields.get("mode")
        if field == "derived_labs.max_tbil_umol_l" and fact.collection == "derived_labs":
            return fact.fields.get("max_tbil_umol_l")
    return None


def validate_certificates_on_pairs(
    pairs: Sequence[Mapping[str, Any]], graph: GuidelineGraph | None = None,
    *, documents: Mapping[str, SourceDocument] | None = None,
) -> dict[str, Any]:
    """Check the certificate against one-field rule-derived counterfactual pairs.

    This is a consistency check, not a causal validation: the pairs and the
    certificate use the same rule-derived criteria.  ``direction_ci95`` is a
    bootstrap interval over the forward pairs, not a clinical confidence claim.
    When ``documents`` is omitted, the four-guideline criteria fixture is used,
    so held-out pairs from all four guidelines can be validated.
    """
    documents = dict(documents or build_documents(load_criteria_fixture()))
    checked = 0
    forward_pairs = 0
    field_hit = 0
    direction_match = 0
    minimality_one = 0
    reverse_pairs = 0
    reverse_no_flip = 0
    nodes: list[int] = []
    costs: list[float] = []
    examples: list[dict[str, Any]] = []
    for pair in pairs:
        document = documents.get(str(pair.get("guideline_id") or ""))
        if document is None:
            continue
        criteria = (document.metadata.get("applicability_criteria") or {}).get("criteria") or []
        changed_field = str(pair.get("changed_field") or "")
        field = _pair_field(changed_field)
        if not field:
            continue
        before = pair.get("before") or {}
        snapshot = _pair_snapshot(pair, before)
        candidates, search = _minimal_flip_search(
            snapshot, criteria,
            document_id=document.document_id, title=document.title,
            statement=document.text or document.title,
        )
        checked += 1
        if pair.get("label_before") == "possible_match":
            reverse_pairs += 1
            if not candidates:
                reverse_no_flip += 1
            continue
        forward_pairs += 1
        nodes.append(int(search.get("nodes_expanded") or 0))
        fields = {item["field"] for item in candidates}
        if field in fields:
            field_hit += 1
        if candidates:
            direction_match += 1
            minimality_one += 1 if len(candidates) == 1 else 0
            costs.append(float(sum(item.get("cost", 0) for item in candidates)))
        if len(examples) < 3:
            examples.append({
                "pair_id": pair.get("pair_id"),
                "changed_field": changed_field,
                "certificate_fields": sorted(fields),
                "certificate_size": len(candidates),
                "predicted": "scope_match" if candidates else "no_flip",
                "expected": pair.get("label_after"),
            })
    return {
        "pairs_checked": checked,
        "forward_pairs": forward_pairs,
        "field_hit": field_hit,
        "direction_match": direction_match,
        "direction_ci95": _bootstrap_ci(direction_match, forward_pairs),
        "minimality_1field": minimality_one,
        "mean_nodes_expanded": round(sum(nodes) / len(nodes), 3) if nodes else 0.0,
        "mean_total_cost": round(sum(costs) / len(costs), 3) if costs else 0.0,
        "reverse_pairs": reverse_pairs,
        "reverse_no_flip": reverse_no_flip,
        "examples": examples,
        "note": "规则一致性检查；证书与反事实对使用同一套规则，不是临床因果验证。反向对（before 已匹配）不要求证书翻转。",
    }


def _bootstrap_ci(successes: int, total: int, *, seed: int = 42, iterations: int = 2000) -> list[float] | None:
    if total <= 0:
        return None
    rng = random.Random(seed)
    rates = []
    for _ in range(iterations):
        sample = [1 if rng.random() < successes / total else 0 for _ in range(total)]
        rates.append(sum(sample) / total)
    rates.sort()
    return [round(rates[int(0.025 * iterations)], 4), round(rates[int(0.975 * iterations) - 1], 4)]


def _pair_field(changed_field: str) -> str | None:
    return {
        "birth_weight_g": "derived_birth.birth_weight_g",
        "ga_days": "derived_birth.ga_days",
        "respiratory_mode": "derived_respiratory_support.mode",
    }.get(changed_field)


def _pair_snapshot(pair: Mapping[str, Any], before: Mapping[str, Any]) -> PatientSnapshot:
    facts: list[dict[str, Any]] = []
    if "birth_weight_g" in before:
        facts.append({
            "collection": "derived_birth", "event_type": "DERIVED",
            "record_path": f"pair:{pair.get('pair_id')}:birth",
            "fields": {"birth_weight_g": before["birth_weight_g"], "data_status": "observed_positive"},
        })
    if "ga_days" in before:
        facts.append({
            "collection": "derived_birth", "event_type": "DERIVED",
            "record_path": f"pair:{pair.get('pair_id')}:birth",
            "fields": {"ga_days": before["ga_days"], "data_status": "observed_positive"},
        })
    if "respiratory_mode" in before:
        facts.append({
            "collection": "derived_respiratory_support", "event_type": "DERIVED",
            "record_path": f"pair:{pair.get('pair_id')}:resp",
            "fields": {"mode": before["respiratory_mode"], "data_status": "observed_positive"},
        })
    return snapshot_from_dict({
        "patient_ref": f"pair:{pair.get('pair_id')}",
        "as_of": "2024-01-01",
        "source": "counterfactual_pair",
        "facts": facts,
    })
