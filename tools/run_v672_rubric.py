#!/usr/bin/env python3
"""Run the R1-R8 structural rubric on the v6.7.2 test cases.

The test cases live in the private dataset directory.  Only aggregate metrics
are written to ``rag/validation`` (no patient references, no evidence spans).

Example::

    cd <repository root>
    PYTHONPATH=. python3 tools/run_v672_rubric.py \
        --dataset-dir "<repository root>/private_data/v6.7.2_dataset_2026-11-07"
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from rag.eval.qrels.gold_rules import load_criteria_fixture
from rag.eval.qrels.rubric import evaluate

MODULE_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, help="private dataset directory")
    args = parser.parse_args()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    cases_path = dataset_dir / "test_cases.jsonl"
    if not cases_path.is_file():
        print(f"test cases not found: {cases_path}", file=sys.stderr)
        return 2
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    fixture = load_criteria_fixture()
    metrics = evaluate(cases, fixture)
    metrics["dataset_dir_name"] = dataset_dir.name
    metrics["generated_at"] = date.today().isoformat()
    private_report = dataset_dir / "rubric_report.json"
    private_report.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = _unique_path(VALIDATION_DIR / f"v672_rubric_{date.today().isoformat()}.json")
    repo_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"private rubric report: {private_report}")
    print(f"repository aggregate: {repo_path}")
    return 0


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(2, 100):
        candidate = path.with_name(f"{stem}_run{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise SystemExit(f"cannot find a free output name for {path}")


if __name__ == "__main__":
    raise SystemExit(main())
