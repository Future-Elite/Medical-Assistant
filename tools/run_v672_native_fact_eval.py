#!/usr/bin/env python3
"""Evaluate the RAG's native patient-fact retriever on the v6.7.2 qrels.

This is not the independent BM25/e5 baseline.  It calls the same
``retrieve_patient_facts`` function used by the retrieval pipeline, so the
result describes the current local patient-fact path.  Only aggregate metrics
are written; patient snapshots and record paths stay private.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import json
import math
import os
from pathlib import Path
import sys

from rag.his import snapshot_from_dict
from rag.retrieval.patient_retrieval import retrieve_patient_facts

MODULE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = (REPO_ROOT / "private_data").resolve()
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"
TEMPLATES_PATH = MODULE_ROOT / "rag" / "eval" / "criteria_fixture" / "fact_question_templates.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True)
    args = parser.parse_args()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    if not dataset_dir.is_relative_to(PRIVATE_ROOT):
        print("dataset-dir must stay under private_data/", file=sys.stderr)
        return 2
    qrels_path = dataset_dir / "patient_fact_qrels.jsonl"
    patients_path = dataset_dir / "patients_all.jsonl"
    if not qrels_path.is_file() or not patients_path.is_file():
        print("missing qrels or patients_all.jsonl", file=sys.stderr)
        return 2

    templates = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))["templates"]
    patients = {
        row["patient_ref"]: row
        for row in map(json.loads, patients_path.read_text(encoding="utf-8").splitlines())
        if row
    }
    qrels = [json.loads(line) for line in qrels_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cache = {}
    statuses = Counter()
    evaluated = 0
    target_hit = 0
    not_recorded = 0
    not_recorded_no_hit = 0
    recall = Counter()
    mrr = 0.0
    ndcg = 0.0
    by_type = defaultdict(lambda: [0, 0, 0])

    for qrel in qrels:
        patient = patients.get(qrel.get("patient_ref"))
        if patient is None:
            continue
        status = str(qrel.get("fact_status") or "")
        statuses[status] += 1
        ref = str(qrel["patient_ref"])
        if ref not in cache:
            cache[ref] = snapshot_from_dict(patient["snapshot"])
        fact_type = str(qrel.get("fact_type") or "")
        if status == "not_recorded":
            not_recorded += 1
            hits, _ = retrieve_patient_facts(cache[ref], templates.get(fact_type, ["fact"])[:1], top_k=20)
            not_recorded_no_hit += not hits
            continue
        gold = str(qrel.get("record_path") or "")
        if not gold:
            continue
        hits, _ = retrieve_patient_facts(cache[ref], templates.get(fact_type, ["fact"]), top_k=20)
        rank = next((i + 1 for i, hit in enumerate(hits) if hit.record_path == gold), None)
        evaluated += 1
        by_type[fact_type][0] += 1
        if rank is None:
            continue
        target_hit += 1
        by_type[fact_type][1] += rank == 1
        by_type[fact_type][2] += 1
        for k in (1, 3, 5):
            recall[k] += rank <= k
        mrr += 1.0 / rank
        ndcg += 1.0 / math.log2(rank + 1) if rank <= 10 else 0.0

    rate = lambda value: round(value / evaluated, 4) if evaluated else None
    result = {
        "function": "v672_native_fact_eval",
        "dataset": dataset_dir.name,
        "qrel_rows": len(qrels),
        "evaluated_rows": evaluated,
        "target_hit_any_top20": target_hit,
        "target_hit_any_top20_rate": rate(target_hit),
        "not_recorded_rows": not_recorded,
        "not_recorded_no_hit": not_recorded_no_hit,
        "metrics": {
            "recall@1": rate(recall[1]),
            "recall@3": rate(recall[3]),
            "recall@5": rate(recall[5]),
            "mrr@10": round(mrr / evaluated, 4) if evaluated else None,
            "ndcg@10": round(ndcg / evaluated, 4) if evaluated else None,
        },
        "by_fact_type": {
            key: {
                "rows": value[0],
                "target_hit_any_top20": value[2],
                "target_hit_any_top20_rate": round(value[2] / value[0], 4) if value[0] else None,
                "recall@1": round(value[1] / value[0], 4) if value[0] else None,
            }
            for key, value in sorted(by_type.items())
        },
        "status_counts": dict(statuses),
        "caveats": [
            "calls the RAG native retrieve_patient_facts (term-overlap), not BM25/e5",
            "top-20 target-record localization only; not clinical answer accuracy",
            "record paths are never written to the repository aggregate",
            "qrels are rule-derived silver labels, not clinical gold",
        ],
    }
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    path = VALIDATION_DIR / f"v672_native_fact_eval_{date.today().isoformat()}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"repository aggregate: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
