#!/usr/bin/env python3
"""Run claim-level adjudication and the minimal counterfactual certificate.

Pilot scope: VLBW feeding 2015, 180 real VLBW admissions + 125 rule-derived
one-field counterfactual pairs.  The aggregate is repo-safe; per-case rows stay
in ``private_data/``.

This is a rule-consistency and auditability pilot, not clinical validation.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import argparse
import json
import os
from pathlib import Path
import sys

from rag.eval.qrels.claim_adjudication import (
    adjudicate_claims,
    validate_certificates_on_pairs,
)
from rag.his import snapshot_from_dict
from rag.quality.guideline_graph import load_guideline_graph

MODULE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = (REPO_ROOT / "private_data").resolve()
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output-name", default="claim_adjudication/cases.jsonl")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    private_root = Path(
        os.environ.get("CHINALLM_PRIVATE_DATA_DIR") or PRIVATE_ROOT
    ).expanduser().resolve()
    if private_root != PRIVATE_ROOT and PRIVATE_ROOT not in private_root.parents:
        print("CHINALLM_PRIVATE_DATA_DIR must stay under private_data/", file=sys.stderr)
        return 2
    output_path = (private_root / args.output_name).resolve()
    if PRIVATE_ROOT not in output_path.parents:
        print("output must stay under private_data/", file=sys.stderr)
        return 2
    patients_path = dataset_dir / "patients_all.jsonl"
    pairs_path = dataset_dir / "counterfactual_pairs.jsonl"
    if not patients_path.is_file():
        print(f"missing {patients_path}", file=sys.stderr)
        return 2

    graph = load_guideline_graph()
    rows = [
        json.loads(line)
        for line in patients_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    vlbw = [
        row for row in rows
        if isinstance((row.get("derived") or {}).get("birth_weight_g"), (int, float))
        and float((row.get("derived") or {})["birth_weight_g"]) < 1500
    ]
    if args.limit > 0:
        vlbw = vlbw[: args.limit]
    verdicts: Counter = Counter()
    certificates: Counter = Counter()
    claims_total = 0
    claims_with_patient_evidence = 0
    claims_with_guideline_locator = 0
    claims_with_record_path = 0
    private_rows = []
    for row in vlbw:
        snapshot = snapshot_from_dict(row["snapshot"])
        result = adjudicate_claims(row["snapshot"], graph)
        claims_total += len(result["claims"])
        for claim in result["claims"]:
            verdicts[claim["verdict"]] += 1
            certificates[claim["counterfactual_certificate"]["status"]] += 1
            if claim["patient_side"]["evidence"]:
                claims_with_patient_evidence += 1
            if claim["guideline_side"]["locators"]:
                claims_with_guideline_locator += 1
            if claim["audit"]["patient_record_paths"]:
                claims_with_record_path += 1
        private_rows.append({
            "patient_ref": row.get("patient_ref"),
            "as_of": row.get("as_of"),
            "claims": {
                claim["recommendation_id"]: {
                    "verdict": claim["verdict"],
                    "patient_side": claim["patient_side"]["status"],
                    "guideline_side": claim["guideline_side"]["status"],
                    "certificate": claim["counterfactual_certificate"]["status"],
                    "missing_facts": claim["counterfactual_certificate"].get("missing_facts", []),
                    "minimal_changes": [
                        {"field": item["field"], "value": item["value"]}
                        for item in claim["counterfactual_certificate"].get("minimal_changes", [])
                    ],
                }
                for claim in result["claims"]
            },
        })

    pair_validation = {
        "pairs_checked": 0, "forward_pairs": 0, "field_hit": 0,
        "direction_match": 0, "reverse_pairs": 0, "reverse_no_flip": 0,
    }
    heldout_validation = None
    if pairs_path.is_file():
        pairs = [
            json.loads(line)
            for line in pairs_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        pair_validation = validate_certificates_on_pairs(pairs, graph)
    heldout_path = private_root / "heldout_counterfactuals" / "cases.jsonl"
    if heldout_path.is_file():
        heldout = [
            json.loads(line)
            for line in heldout_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        heldout_validation = validate_certificates_on_pairs(heldout, graph)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in private_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    output_path.chmod(0o600)

    aggregate = {
        "function": "v672_claim_adjudication",
        "guideline_id": graph.guideline_id,
        "cases": len(private_rows),
        "claims": claims_total,
        "verdicts": dict(verdicts),
        "certificates": dict(certificates),
        "audit": {
            "claims_with_patient_evidence": claims_with_patient_evidence,
            "claims_with_guideline_locator": claims_with_guideline_locator,
            "claims_with_record_path": claims_with_record_path,
            "claims_total": claims_total,
        },
        "pair_certificate_validation": pair_validation,
        "heldout_certificate_validation": heldout_validation,
        "caveats": [
            "单份指南、VLBW 范围；不是指南库。",
            "主张级裁定只判指南建议适用性，不是临床结论。",
            "反事实证书只搜索已有结构化字段，不猜时间/文本缺失。",
            "证书与规则派生反事实对使用同一套规则，一致性检查不是临床因果验证。",
            "held-out 对来自 dev/test 患者，不参与训练；仍是规则派生，不是临床反事实。",
            "未调用 LLM；模型判断未执行。",
        ],
    }
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = VALIDATION_DIR / f"v672_claim_adjudication_{date.today().isoformat()}.json"
    repo_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    print(f"private rows: {output_path}")
    print(f"repository aggregate: {repo_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
