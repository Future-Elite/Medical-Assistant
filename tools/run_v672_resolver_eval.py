#!/usr/bin/env python3
"""Evaluate the current offline applicability resolver on the frozen test set.

No external search is used.  Only aggregate counts and hashes are written to
``rag/validation``; patient snapshots and case identifiers stay private.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from rag.eval.qrels.resolver_evaluation import evaluate_resolver

MODULE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = MODULE_ROOT
PRIVATE_ROOT = PROJECT_ROOT / "private_data"
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, help="directory under project private_data/")
    args = parser.parse_args()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    if not dataset_dir.is_relative_to(PRIVATE_ROOT.resolve()):
        print("dataset-dir must be inside the project private_data/", file=sys.stderr)
        return 2
    cases_path = dataset_dir / "test_cases.jsonl"
    if not cases_path.is_file():
        print(f"test cases not found: {cases_path}", file=sys.stderr)
        return 2
    raw = cases_path.read_bytes()
    cases = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    result = evaluate_resolver(cases)
    result.update({
        "dataset_dir_name": dataset_dir.name,
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "offline_applicability_workflow",
        "external_search": False,
        "input_contract": "question, snapshot and target/topic identity; no gold status, missing fields or coverage",
        "scoring": {
            "A": "resolved with exactly the same-family coverage set; cross-family-only cases intentionally stop",
            "B": "scope_mismatch with no possible local coverage and no selected guideline",
            "C": "needs_information with exactly the gold missing-field set",
            "D": "evidence_gap without local substitution or an unconfigured external search",
            "GA": "unhandled because the current resolver has no free-text GA extraction action",
        },
        "caveats": [
            "rule-based local workflow evaluation, not a complete online RAG evaluation",
            "gold and workflow use related structured criteria; results are not independent clinical labels",
            "500 cases are a balanced diagnostic subset, not prevalence",
            "split is admission-level, not patient-level",
            "no external retrieval quality is measured in this offline run",
            "not clinical accuracy or clinical validation",
        ],
    })
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = _unique_path(VALIDATION_DIR / f"v672_resolver_eval_{date.today().isoformat()}.json")
    repo_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"repository aggregate: {repo_path}")
    return 0


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 100):
        candidate = path.with_name(f"{path.stem}_run{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise SystemExit(f"cannot find a free output name for {path}")


if __name__ == "__main__":
    raise SystemExit(main())
