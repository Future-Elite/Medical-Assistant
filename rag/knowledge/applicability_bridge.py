"""Attach bidirectional graph evidence to an applicability-chain result.

The applicability resolver evaluates reviewed structured criteria from a small
guideline fixture.  The knowledge graph retrieval is wider: it returns
recommendation candidates and the patient-graph records that were matched in
either direction.  This bridge joins the two without changing their semantics:

* criteria keep their ``match/mismatch/unknown`` status;
* the chain outcome and stop reason are not rewritten;
* graph records are attached only as ``graph_support``/``graph_candidates`` and
  are explicitly marked for human review.

The bridge is a read-only presentation/audit layer.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable, Mapping, Sequence

BRIDGE_VERSION = "applicability-graph-bridge-1"
GRAPH_DIMENSIONS = (
    "population", "indication", "exclusion", "setting", "unformalized",
    "temporal", "evidence_status", "runtime_capability",
)
_IGNORED_TOKENS = {"derived", "ehr", "his", "record", "patient", "record_type"}


def attach_fusion_to_applicability(
    result: Mapping[str, Any],
    fusion: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a copy of ``result`` enriched with graph evidence from ``fusion``."""
    data = deepcopy(dict(result))
    if not isinstance(fusion, Mapping) or not fusion:
        return data

    fusion_block = fusion.get("fusion") if isinstance(fusion.get("fusion"), Mapping) else {}
    stats = fusion_block.get("stats") if isinstance(fusion_block.get("stats"), Mapping) else {}
    candidates = [_candidate(row) for row in fusion.get("recommendations") or ()]
    candidates = [row for row in candidates if row]
    target = data.get("target") if isinstance(data.get("target"), Mapping) else {}
    target_detail = data.get("target_detail") if isinstance(data.get("target_detail"), Mapping) else {}
    target_id = str(target.get("guideline_id") or target_detail.get("guideline_id") or "")
    for candidate in candidates:
        candidate["same_target"] = bool(target_id and target_id in candidate["source_ids"])
        candidate["scope"] = "target" if candidate["same_target"] else "cross_guideline"
    candidates.sort(key=lambda row: (not row["same_target"], row["recommendation_id"]))
    evidence_paths = sorted({
        path for row in candidates for path in row["patient_record_paths"]
    })

    support_count = 0
    for detail in _detail_blocks(data):
        for criterion in detail.get("criteria") or ():
            support = _criterion_support(criterion, candidates)
            criterion["graph_support"] = support["status"]
            criterion["graph_record_paths"] = support["record_paths"]
            criterion["graph_concept_ids"] = support["concept_ids"]
            criterion["graph_origin"] = support["origin"]
            if support["status"] != "none":
                support_count += 1

    target_candidates = [row for row in candidates if row["same_target"]]
    graph_unknown = sorted({
        dimension for row in target_candidates
        for dimension, value in (row.get("applicability") or {}).items()
        if value in {"unknown", "conflict"}
    })
    bridge = {
        "schema_version": BRIDGE_VERSION,
        "retrieval_id": fusion.get("retrieval_id"),
        "knowledge_context_id": fusion.get("knowledge_context_id"),
        "knowledge_release_id": fusion.get("knowledge_release_id"),
        "release_sha256": fusion.get("release_sha256"),
        "forward": _direction(fusion_block.get("forward")),
        "backward": _direction(fusion_block.get("backward")),
        "stats": dict(stats),
        "patient_record_paths": evidence_paths,
        "candidates": candidates[:8],
        "criteria_with_graph_support": support_count,
        "graph_unknown_dimensions": graph_unknown,
        "boundary": (
            "双向图证据只定位患者记录和指南候选，不改变条件状态、链路结论或临床授权；"
            "图候选必须人工复核。"
        ),
    }
    data["knowledge_fusion"] = bridge
    data["graph_candidates"] = candidates[:8]

    next_action = data.get("next_action")
    if isinstance(next_action, dict) and candidates:
        supported = [row for row in candidates if row["patient_record_paths"]]
        next_action["graph_candidate_count"] = len(candidates)
        next_action["graph_candidates_with_patient_evidence"] = len(supported)
        next_action["graph_same_target_count"] = len(target_candidates)
        next_action["graph_text"] = (
            f"指南图谱返回 {len(candidates)} 条候选（同目标指南 {len(target_candidates)} 条），"
            f"其中 {len(supported)} 条带患者图记录；只提示复核，不是已判定适用。"
        )
        if data.get("outcome") in {"needs_information", "evidence_gap", "scope_mismatch"} and supported:
            next_action["graph_action"] = "review_graph_candidates"
    return data


def _detail_blocks(data: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    target = data.get("target_detail")
    if isinstance(target, dict):
        yield target
    for detail in data.get("selected_guideline_details") or ():
        if isinstance(detail, dict):
            yield detail


def _candidate(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, Mapping):
        return None
    evidence = []
    for item in row.get("patient_graph_evidence") or ():
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("record_path") or "").strip()
        if not path:
            continue
        evidence.append({
            "record_path": path,
            "record_type": str(item.get("record_type") or ""),
            "event_type": str(item.get("event_type") or ""),
            "status": str(item.get("status") or ""),
            "origin": str(item.get("origin") or ""),
            "concept_ids": [str(value) for value in item.get("concept_ids") or ()],
            "graph_path": [str(value) for value in item.get("graph_path") or ()],
        })
    applicability = row.get("applicability") if isinstance(row.get("applicability"), Mapping) else {}
    candidate = {
        "recommendation_id": str(row.get("recommendation_id") or ""),
        "summary": str(row.get("summary") or ""),
        "direction": str(row.get("direction") or ""),
        "review_status": str(row.get("review_status") or ""),
        "guideline_id": str(row.get("guideline_id") or ((row.get("source_ids") or [""])[0])),
        "guideline_title": str(row.get("guideline_title") or ""),
        "source_ids": [str(value) for value in row.get("source_ids") or ()],
        "patient_record_paths": sorted({item["record_path"] for item in evidence}),
        "patient_evidence": evidence,
        "coverage": (row.get("fusion") or {}).get("coverage") if isinstance(row.get("fusion"), Mapping) else None,
        "applicability": {str(key): str(value) for key, value in applicability.items()},
        "missing_information": [str(value) for value in row.get("missing_information") or ()],
    }
    return candidate if candidate["recommendation_id"] else None


def _criterion_support(
    criterion: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    record_paths = {str(value) for value in criterion.get("record_paths") or ()}
    field = str(criterion.get("field") or "")
    field_tokens = _tokens(field)
    prefix_tokens = _tokens(field.split(".", 1)[0]) - _IGNORED_TOKENS
    best: dict[str, Any] = {"status": "none", "record_paths": [], "concept_ids": [], "origin": ""}
    for candidate in candidates:
        for item in candidate["patient_evidence"]:
            path = item["record_path"]
            path_tokens = _tokens(path)
            concepts = set(item["concept_ids"])
            if path in record_paths:
                return {
                    "status": "same_record",
                    "record_paths": [path],
                    "concept_ids": sorted(concepts),
                    "origin": item["origin"],
                }
            if prefix_tokens and prefix_tokens <= path_tokens:
                best = _prefer(best, {
                    "status": "same_record_type",
                    "record_paths": [path],
                    "concept_ids": sorted(concepts),
                    "origin": item["origin"],
                })
            elif field_tokens & concepts:
                best = _prefer(best, {
                    "status": "concept_overlap",
                    "record_paths": [path],
                    "concept_ids": sorted(concepts),
                    "origin": item["origin"],
                })
    return best


def _prefer(current: Mapping[str, Any], candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    rank = {"none": 0, "concept_overlap": 1, "same_record_type": 2, "same_record": 3}
    if rank[candidate["status"]] > rank[current["status"]]:
        return candidate
    if candidate["status"] == current["status"]:
        return {
            **current,
            "record_paths": sorted(set(current["record_paths"]) | set(candidate["record_paths"])),
            "concept_ids": sorted(set(current["concept_ids"]) | set(candidate["concept_ids"])),
        }
    return current


def _tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", value.lower()) if token}


def _direction(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        "hits": value.get("hits"),
        "seeds": list(value.get("seeds") or ()),
        "queries": list(value.get("queries") or ()),
    }
