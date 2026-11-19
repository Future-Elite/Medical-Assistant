"""Bidirectional fusion of patient-fact graph retrieval and guideline graph retrieval.

Forward direction: patient graph hits (facts) seed the guideline graph.
Backward direction: candidate recommendations expose condition summaries; those
summaries query the patient graph again, and the resulting record paths are
attached to the recommendation as patient evidence.

The fusion is a retrieval/audit layer only. It does not change condition
semantics, action eligibility, or clinical authority; those remain in
``retrieve_knowledge`` and ``validate_actions``.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..core.his import PatientSnapshot
from ..patient_graph.builder import build_patient_graph, fact_evidence_text
from ..patient_graph.retrieval import GraphHit
from ..patient_graph.service import retrieve_patient_facts
from .model import KnowledgeGraph
from .retrieval import retrieve_knowledge

FUSION_VERSION = "bidirectional-graph-fusion"


def retrieve_bidirectional_knowledge(
    graph: KnowledgeGraph,
    snapshot: PatientSnapshot,
    queries: Sequence[str],
    *,
    intent: str | None = None,
    limit: int = 18,
    max_hops: int = 3,
    patient_top_k: int = 20,
    patient_max_hops: int = 2,
    forward: bool = True,
    backward: bool = True,
) -> dict[str, Any]:
    """Run patient -> guideline and guideline -> patient retrieval in one call."""
    forward_hits: tuple[GraphHit, ...] = ()
    forward_trace: dict[str, Any] = {}
    forward_seeds: list[str] = []
    if forward:
        forward_queries = _forward_queries(snapshot, queries)
        forward_hits, forward_trace = retrieve_patient_facts(
            snapshot, forward_queries, top_k=patient_top_k, max_hops=patient_max_hops,
        )
        forward_seeds = _forward_seeds(graph, forward_hits)
    context = retrieve_knowledge(
        graph, snapshot, queries,
        intent=intent, limit=limit, max_hops=max_hops, extra_seeds=forward_seeds,
    )
    candidates = list(context.get("recommendations") or ()) + list(
        context.get("excluded_recommendations") or ()
    )
    backward_terms = _backward_terms(graph, candidates) if backward else []
    backward_hits: tuple[GraphHit, ...] = ()
    backward_trace: dict[str, Any] = {}
    if backward and backward_terms:
        backward_hits, backward_trace = retrieve_patient_facts(
            snapshot, backward_terms, top_k=patient_top_k, max_hops=patient_max_hops,
        )
    forward_by_path = {hit.record_path: hit for hit in forward_hits}
    backward_by_path = {hit.record_path: hit for hit in backward_hits}
    patient_graph = build_patient_graph(snapshot)
    for row in context.get("recommendations") or ():
        _attach_patient_evidence(row, forward_by_path, backward_by_path, patient_graph)
    for row in context.get("excluded_recommendations") or ():
        _attach_patient_evidence(row, forward_by_path, backward_by_path, patient_graph)

    stats = _fusion_stats(context, forward_hits, backward_hits)
    context["fusion"] = {
        "schema_version": FUSION_VERSION,
        "forward": {
            "query": list(queries),
            "patient_queries": _forward_queries(snapshot, queries) if forward else [],
            "hits": len(forward_hits),
            "seeds": sorted(forward_seeds),
            "trace": _trace_summary(forward_trace),
        },
        "backward": {
            "queries": backward_terms,
            "hits": len(backward_hits),
            "trace": _trace_summary(backward_trace),
        },
        "stats": {**stats, "forward_enabled": forward, "backward_enabled": backward},
        "boundary": "双向融合只补充患者事实证据与图路径；不改变四态条件、动作资格或临床授权。",
    }
    context.setdefault("diagnostics", {})["fusion"] = {
        "version": FUSION_VERSION,
        "forward_hits": len(forward_hits),
        "backward_hits": len(backward_hits),
        "recommendations_with_patient_evidence": stats["recommendations_with_patient_evidence"],
    }
    return context


def _forward_queries(snapshot: PatientSnapshot, queries: Sequence[str]) -> list[str]:
    values: list[str] = [str(item) for item in queries if str(item).strip()]
    for record in snapshot.visible_records():
        fields = record.fields
        for key in ("name", "symptom", "symptoms", "mode", "care_setting", "culture_status"):
            raw = fields.get(key)
            items = raw if isinstance(raw, (list, tuple)) else (raw,)
            for item in items:
                text = str(item or "").strip()
                if text and text not in values:
                    values.append(text)
        if len(values) >= 12:
            break
    return values[:12]


def _forward_seeds(graph: KnowledgeGraph, hits: Sequence[GraphHit]) -> list[str]:
    seeds: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        terms = [
            *hit.concept_ids, *hit.matched_terms, hit.collection, hit.event_type,
        ]
        for term in terms:
            text = str(term or "").strip()
            if not text:
                continue
            if text in graph.nodes and text not in seen:
                seen.add(text)
                seeds.append(text)
                continue
            resolution = graph.resolve_term(text)
            if resolution["status"] != "resolved":
                continue
            node_id = resolution["candidates"][0]["id"]
            if node_id not in seen:
                seen.add(node_id)
                seeds.append(node_id)
        if len(seeds) >= 20:
            break
    return seeds


def _backward_terms(graph: KnowledgeGraph, recommendations: Sequence[Mapping[str, Any]]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for row in recommendations:
        rec = graph.recommendations.get(str(row.get("recommendation_id") or ""))
        if not rec:
            continue
        population_id = str(rec.get("population_id") or "")
        population = graph.populations.get(population_id) or {}
        for text in (population.get("summary"),):
            _append_term(terms, seen, text)
        for condition_id in (
            *list(rec.get("indications") or ()),
            *list(rec.get("exclusions") or ()),
            *list(rec.get("temporal_conditions") or ()),
        ):
            condition = graph.conditions.get(str(condition_id)) or {}
            _append_term(terms, seen, condition.get("summary"))
        if len(terms) >= 20:
            break
    return terms


def _append_term(terms: list[str], seen: set[str], value: Any) -> None:
    text = str(value or "").strip()
    if not text or text in seen:
        return
    seen.add(text)
    terms.append(text)


def _attach_patient_evidence(
    row: Mapping[str, Any],
    forward_by_path: Mapping[str, GraphHit],
    backward_by_path: Mapping[str, GraphHit],
    patient_graph: Any,
) -> None:
    refs = _fact_refs(row)
    evidence = []
    forward_count = backward_count = condition_ref_count = 0
    for ref in refs:
        hit = forward_by_path.get(ref) or backward_by_path.get(ref)
        if hit is not None:
            origin = "forward" if ref in forward_by_path else "backward"
            forward_count += int(origin == "forward")
            backward_count += int(origin == "backward")
            evidence.append({
                "record_path": hit.record_path,
                "record_type": hit.collection,
                "event_type": hit.event_type,
                "status": hit.status,
                "concept_ids": list(hit.concept_ids),
                "graph_path": list(hit.graph_path),
                "evidence_text": hit.evidence_text,
                "origin": origin,
            })
            continue
        # The condition engine already resolved this record id.  Use the patient
        # graph's exact record node so evidence does not depend on whether the
        # backward query happened to rank that record in its top-k.
        node_id = f"record:{ref}"
        node = patient_graph.node_by_id.get(node_id)
        record = patient_graph.records_by_record_path.get(ref)
        if node is None or record is None:
            continue
        concepts = [
            edge.target for edge in patient_graph.outgoing.get(node_id, ())
            if edge.relation == "MENTIONS" and edge.target in patient_graph.node_by_id
        ][:2]
        condition_ref_count += 1
        evidence.append({
            "record_path": ref,
            "record_type": str(node.attributes.get("collection") or ""),
            "event_type": str(node.attributes.get("event_type") or ""),
            "status": str(node.attributes.get("status") or ""),
            "concept_ids": concepts,
            "graph_path": [node_id, *concepts],
            "evidence_text": fact_evidence_text(record),
            "origin": "condition_ref",
        })
    total = len(refs)
    row["patient_graph_evidence"] = evidence
    row["fusion"] = {
        "fact_references": refs,
        "matched_references": len(evidence),
        "total_references": total,
        "coverage": round(len(evidence) / total, 4) if total else 0.0,
        "forward_matched": forward_count,
        "backward_matched": backward_count,
        "condition_ref_matched": condition_ref_count,
    }


def _fact_refs(row: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for values in (row.get("fact_references") or {}).values():
        for value in values or ():
            text = str(value)
            if text and text not in seen:
                seen.add(text)
                refs.append(text)
    return refs


def _trace_summary(trace: Mapping[str, Any]) -> dict[str, Any]:
    graph_trace = trace.get("patient_graph") or {}
    return {
        "engine": graph_trace.get("engine") or trace.get("engine"),
        "status": graph_trace.get("status") or trace.get("status"),
        "snapshot_sha256": (graph_trace.get("graph") or {}).get("snapshot_sha256"),
        "seed_count": len(graph_trace.get("seeds") or ()),
        "budget": graph_trace.get("budget"),
        "truncated": graph_trace.get("truncated"),
    }


def _fusion_stats(
    context: Mapping[str, Any],
    forward_hits: Sequence[GraphHit],
    backward_hits: Sequence[GraphHit],
) -> dict[str, Any]:
    rows = list(context.get("recommendations") or ()) + list(
        context.get("excluded_recommendations") or ()
    )
    with_evidence = sum(1 for row in rows if row.get("patient_graph_evidence"))
    matched = sum(int((row.get("fusion") or {}).get("matched_references", 0)) for row in rows)
    total = sum(int((row.get("fusion") or {}).get("total_references", 0)) for row in rows)
    return {
        "forward_hits": len(forward_hits),
        "backward_hits": len(backward_hits),
        "recommendations": len(rows),
        "recommendations_with_patient_evidence": with_evidence,
        "matched_fact_references": matched,
        "total_fact_references": total,
        "reference_coverage": round(matched / total, 4) if total else 0.0,
    }
