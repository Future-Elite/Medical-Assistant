#!/usr/bin/env python3
"""Run the v6.7.2 S4 baseline: TF-IDF + logistic regression.

Input is the private dataset directory (``rule_labels_train.jsonl``,
``dev.jsonl``, ``test_cases.jsonl``).  The script trains on 1,000 train
admissions, tunes C on 200 dev admissions, and evaluates once on the
A/B/C/D test cases (GA is excluded: that subset is for text extraction).

Only aggregate metrics are written to ``rag/validation``.  Per-case
predictions (which contain ``patient_ref``) stay in the private directory.
Run with the project virtualenv because scikit-learn is not in the system
Python:

    cd <repository root>
    PYTHONPATH=. python3 tools/run_v672_model.py \
        --dataset-dir "<repository root>/private_data/v6.7.2_dataset_2026-11-07"
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from rag.eval.qrels.model_baseline import (
    build_samples_from_cases,
    build_samples_from_records,
    rule_baseline_metrics,
    train_and_evaluate,
)

MODULE_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, help="private dataset directory")
    args = parser.parse_args()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    for name in ("rule_labels_train.jsonl", "dev.jsonl", "test_cases.jsonl"):
        if not (dataset_dir / name).is_file():
            print(f"missing {name} under {dataset_dir}", file=sys.stderr)
            return 2

    train_records = _read(dataset_dir / "rule_labels_train.jsonl")
    dev_records = _read(dataset_dir / "dev.jsonl")
    test_cases = _read(dataset_dir / "test_cases.jsonl")
    train_samples = build_samples_from_records(train_records)
    dev_samples = build_samples_from_records(dev_records)
    test_samples = build_samples_from_cases(test_cases)

    try:
        result = train_and_evaluate(train_samples, dev_samples, test_samples)
    except ImportError as exc:
        print(f"scikit-learn/numpy unavailable: {exc}", file=sys.stderr)
        return 2

    result["rule_baseline"] = rule_baseline_metrics(test_cases, test_samples)
    result["caveats"] = [
        "labels are rule-derived silver, not clinical ground truth",
        "test cases were used for this baseline; no hidden subset",
        "GA extraction subset excluded from status classification",
        "R1-R8 and this baseline are not clinical validation",
    ]
    predictions = result.pop("predictions")
    private_dir = dataset_dir / "s4_model"
    private_dir.mkdir(parents=True, exist_ok=True)
    private_path = private_dir / "model_predictions.jsonl"
    with private_path.open("w", encoding="utf-8") as handle:
        for row in predictions:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    private_path.chmod(0o600)

    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = _unique_path(VALIDATION_DIR / f"v672_model_{date.today().isoformat()}.json")
    repo_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"private predictions: {private_path}")
    print(f"repository aggregate: {repo_path}")
    return 0


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


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
