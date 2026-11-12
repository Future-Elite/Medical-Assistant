#!/usr/bin/env python3
"""Audit whether A-case coverage is same-family or cross-family only.

A's frozen ``gold.coverage`` is "any local guideline that covers the patient".
That is not the same as "another guideline that answers the same question".
This audit adds the missing distinction without rebuilding the frozen dataset.

Only aggregate counts go to ``rag/validation``; per-case rows stay private.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import sys

from rag.eval.qrels.families import family_of

MODULE_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, help="private dataset directory")
    args = parser.parse_args()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    path = dataset_dir / "test_cases.jsonl"
    if not path.is_file():
        print(f"missing {path}", file=sys.stderr)
        return 2
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    counts: Counter = Counter()
    rows = []
    for case in cases:
        if case.get("case_type") != "A":
            continue
        target = case["gold"].get("guideline_id")
        target_family = family_of(target)
        same = [
            item["guideline_id"] for item in case["gold"]["all_guidelines"]
            if item["status"] == "possible_match" and family_of(item["guideline_id"]) == target_family
        ]
        cross = [
            item["guideline_id"] for item in case["gold"]["all_guidelines"]
            if item["status"] == "possible_match" and family_of(item["guideline_id"]) != target_family
        ]
        scope = "same_family" if same else ("cross_family_only" if cross else "none")
        counts[scope] += 1
        rows.append({
            "case_id": case["case_id"],
            "target_guideline_id": target,
            "coverage_scope": scope,
            "same_family": same,
            "cross_family": cross,
        })
    result = {
        "function": "a_coverage_scope_audit",
        "cases": len(rows),
        "counts": dict(sorted(counts.items())),
        "caveats": [
            "frozen test cases predate coverage_scope; this is an audit, not a relabel",
            "cross_family_only is not a same-question alternative",
            "no clinical review",
        ],
    }
    private_dir = dataset_dir / "audit"
    private_dir.mkdir(parents=True, exist_ok=True)
    private_path = private_dir / "a_coverage_scope.jsonl"
    with private_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    private_path.chmod(0o600)

    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = VALIDATION_DIR / f"v672_a_coverage_scope_{date.today().isoformat()}.json"
    repo_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"private audit: {private_path}")
    print(f"repository aggregate: {repo_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
