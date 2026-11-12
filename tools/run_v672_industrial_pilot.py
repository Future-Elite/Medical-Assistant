#!/usr/bin/env python3
"""Run the VLBW feeding guideline-graph pilot against real derived snapshots.

The pilot compares the existing one-criterion field engine with the graph
engine on the same 180 VLBW admissions.  Per-case rows stay in
``private_data/``; the repository gets an aggregate without patient refs,
free text or evidence spans.

This is a retrospective documentation audit, not a clinical evaluation.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import argparse
import json
import os
from pathlib import Path
import sys

from rag.eval.qrels.gold_rules import load_criteria_fixture
from rag.eval.qrels.rubric import build_documents
from rag.his import snapshot_from_dict
from rag.quality.applicability import assess_applicability
from rag.quality.guideline_graph import assess_guideline_graph, load_guideline_graph

MODULE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = (REPO_ROOT / "private_data").resolve()
TARGET_ID = "pdf-neo-feeding-vlbw-2015"
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, help="directory with patients_all.jsonl")
    parser.add_argument("--limit", type=int, default=0, help="0 = all VLBW admissions")
    parser.add_argument("--output-name", default="industrial_pilot/cases.jsonl")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    if not dataset_dir.is_dir():
        print(f"missing dataset directory: {dataset_dir}", file=sys.stderr)
        return 2
    private_root = Path(
        os.environ.get("CHINALLM_PRIVATE_DATA_DIR") or PRIVATE_ROOT
    ).expanduser().resolve()
    if private_root != PRIVATE_ROOT and PRIVATE_ROOT not in private_root.parents:
        print("CHINALLM_PRIVATE_DATA_DIR must stay under the project's private_data/", file=sys.stderr)
        return 2
    output_path = (private_root / args.output_name).resolve()
    if PRIVATE_ROOT not in output_path.parents:
        print("output must stay under the project's private_data/", file=sys.stderr)
        return 2
    if not (dataset_dir / "patients_all.jsonl").is_file():
        print(f"missing patients_all.jsonl in {dataset_dir}", file=sys.stderr)
        return 2

    graph = load_guideline_graph()
    documents = build_documents(load_criteria_fixture())
    field_document = documents[TARGET_ID]
    rows = [
        json.loads(line)
        for line in (dataset_dir / "patients_all.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    vlbw_rows = []
    for row in rows:
        derived = row.get("derived") or {}
        weight = derived.get("birth_weight_g")
        if isinstance(weight, (int, float)) and float(weight) < 1500:
            vlbw_rows.append(row)
    if args.limit > 0:
        vlbw_rows = vlbw_rows[: args.limit]
    if not vlbw_rows:
        print("no VLBW rows found", file=sys.stderr)
        return 2

    field_counts: Counter = Counter()
    graph_counts: Counter = Counter()
    recommendation_counts: dict[str, Counter] = defaultdict(Counter)
    hard_exception_cases = 0
    related_condition_cases = 0
    time_missing_cases = 0
    milk_counts: Counter = Counter()
    tolerance_counts: Counter = Counter()
    audit_record_path_cases = 0
    audit_locator_cases = 0
    private_rows = []
    for row in vlbw_rows:
        snapshot = snapshot_from_dict(row["snapshot"])
        field = assess_applicability(field_document, snapshot, [])
        field_counts[field.status] += 1
        graph_result = assess_guideline_graph(
            row["snapshot"], graph,
            question="极低出生体重儿喂养：开奶时间、奶类、喂养耐受和达到全量喂养时间。",
        )
        graph_counts[graph_result["scope"]["status"]] += 1
        for item in graph_result["recommendations"]:
            recommendation_counts[item["recommendation_id"]][item["status"]] += 1
        if any(item["status"] == "exception_triggered" for item in graph_result["recommendations"]):
            hard_exception_cases += 1
        if any(item["status"] == "needs_review" for item in graph_result["recommendations"]):
            related_condition_cases += 1
        if any(
            item["time"]["status"] == "unknown"
            for item in graph_result["recommendations"]
        ):
            time_missing_cases += 1
        milk = graph_result["patient_evidence"]["milk"]["value"] or "not_recorded"
        tolerance = graph_result["patient_evidence"]["tolerance"]["value"] or "not_recorded"
        milk_counts[milk] += 1
        tolerance_counts[tolerance] += 1
        if any(item["audit"]["record_paths"] for item in graph_result["recommendations"]):
            audit_record_path_cases += 1
        if all(item["audit"]["source_locators"] for item in graph_result["recommendations"]):
            audit_locator_cases += 1
        private_rows.append({
            "patient_ref": row.get("patient_ref"),
            "as_of": row.get("as_of"),
            "field_status": field.status,
            "graph_scope": graph_result["scope"]["status"],
            "graph_summary": graph_result["summary"],
            "recommendations": {
                item["recommendation_id"]: {
                    "status": item["status"],
                    "fusion_rule": item["audit"]["fusion_rule"],
                    "time_status": item["time"]["status"],
                    "missing_facts": item["missing_facts"],
                }
                for item in graph_result["recommendations"]
            },
            "milk": milk,
            "tolerance": tolerance,
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for item in private_rows:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    output_path.chmod(0o600)

    aggregate = {
        "function": "v672_industrial_pilot",
        "graph_guideline": graph.guideline_id,
        "graph_version": graph.version,
        "cases": len(private_rows),
        "field_engine": dict(field_counts),
        "graph_scope": dict(graph_counts),
        "recommendation_status": {
            key: dict(value) for key, value in sorted(recommendation_counts.items())
        },
        "extra_findings": {
            "hard_exception_cases": hard_exception_cases,
            "related_condition_cases": related_condition_cases,
            "time_window_not_evaluable_cases": time_missing_cases,
        },
        "documented_evidence": {
            "milk": dict(milk_counts),
            "tolerance": dict(tolerance_counts),
        },
        "audit": {
            "cases_with_record_paths": audit_record_path_cases,
            "cases_with_all_recommendation_locators": audit_locator_cases,
            "cases_total": len(private_rows),
        },
        "caveats": [
            "单份指南、单病种范围；不是指南库。",
            "文本证据来自出院小结，是回顾性文档审计，不是床旁判断。",
            "exception/related 是待复核信号，不是停喂或临床不适用结论。",
            "时间窗需要的出生/开奶/全量喂养时间多数缺失，缺失记 unknown。",
            "未调用 LLM 判断；模型槽位为空。",
            "BM25 是词面检索，不是语义模型。",
        ],
    }
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = VALIDATION_DIR / f"v672_industrial_pilot_{date.today().isoformat()}.json"
    repo_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    print(f"private rows: {output_path}")
    print(f"repository aggregate: {repo_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
