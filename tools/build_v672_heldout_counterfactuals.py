#!/usr/bin/env python3
"""Build held-out one-field counterfactual pairs from dev/test patients.

The existing 500 training pairs are train-only.  This tool builds a disjoint
200-pair set from dev/test patients for certificate validation.  Pairs are
rule-derived and contain patient_ref; the JSONL stays in private_data/, while
the repo gets only an aggregate manifest.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import argparse
import json
import os
from pathlib import Path
import sys

from rag.eval.qrels.build_dataset import build_counterfactual_pairs
from rag.eval.qrels.gold_rules import load_criteria_fixture

MODULE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = (REPO_ROOT / "private_data").resolve()
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--output-name", default="heldout_counterfactuals/cases.jsonl")
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
    if not patients_path.is_file():
        print(f"missing {patients_path}", file=sys.stderr)
        return 2

    records = [
        json.loads(line)
        for line in patients_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    heldout = [row for row in records if row.get("split") in {"dev", "test"}]
    if not heldout:
        print("no dev/test records found", file=sys.stderr)
        return 2
    pairs = build_counterfactual_pairs(
        heldout, load_criteria_fixture(), seed=43, count=args.count,
    )
    for index, pair in enumerate(pairs, 1):
        pair["pair_id"] = f"heldout-cf-{index:04d}"
        pair["split"] = next(
            (row.get("split") for row in heldout if row.get("patient_ref") == pair.get("patient_ref")),
            "dev_or_test",
        )
        if pair["split"] not in {"dev", "test"}:
            print("held-out pair leaked a non-dev/test split", file=sys.stderr)
            return 2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for pair in pairs:
            handle.write(json.dumps(pair, ensure_ascii=False) + "\n")
    output_path.chmod(0o600)

    counts = Counter(
        (pair["split"], pair["guideline_id"], pair["changed_field"])
        for pair in pairs
    )
    aggregate = {
        "function": "v672_heldout_counterfactuals",
        "source": "dev/test patients only; disjoint from training pairs",
        "pairs": len(pairs),
        "counts": {
            f"{split}|{guideline}|{field}": count
            for (split, guideline, field), count in sorted(counts.items())
        },
        "caveats": [
            "规则派生单字段反事实；不是临床反事实。",
            "只从 dev/test 患者生成；patient_ref 只在 private_data/。",
            "用于证书一致性检查，不是临床因果验证。",
        ],
    }
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = VALIDATION_DIR / f"v672_heldout_counterfactual_manifest_{date.today().isoformat()}.json"
    repo_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    print(f"private pairs: {output_path}")
    print(f"repository manifest: {repo_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
