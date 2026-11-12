#!/usr/bin/env python3
"""Preliminary closed-loop test for the v6.7.2 triage workflow.

For each case: run the triage card, supply the top recommended fact with a
simulated plausible value, re-run the card, and check that the recommendation
actually moves the target question (C) or correctly leaves it stopped (A/B/D).

This is a workflow smoke test, not a clinical simulation and not a model.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import sys

from rag.eval.qrels.decision_triage import (
    SIM_VALUES,
    apply_value_to_snapshot,
    triage_case,
)

MODULE_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--per-type", type=int, default=10)
    args = parser.parse_args()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    cases_path = dataset_dir / "test_cases.jsonl"
    if not cases_path.is_file():
        print(f"missing {cases_path}", file=sys.stderr)
        return 2
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_type: dict[str, list[dict]] = {}
    for case in cases:
        by_type.setdefault(case["case_type"], []).append(case)

    results = []
    for case_type in ("C", "B", "D", "A"):
        for case in by_type.get(case_type, [])[: args.per_type]:
            results.append(_run_case(case))

    metrics = {
        "case_count": len(results),
        "per_type": {case_type: sum(1 for row in results if row["case_type"] == case_type)
                     for case_type in ("A", "B", "C", "D")},
        "C": _c_metrics([row for row in results if row["case_type"] == "C"]),
        "B": _stop_metrics([row for row in results if row["case_type"] == "B"]),
        "D": _d_metrics([row for row in results if row["case_type"] == "D"]),
        "A": _stop_metrics([row for row in results if row["case_type"] == "A"]),
        "function": "decision_relevant_triage_closed_loop",
        "caveats": [
            "simulated fact supply, not real doctor feedback",
            "rule-based, not a clinical decision model",
            "C uses the target guideline's missing field as the target",
            "not clinical validation",
        ],
    }
    private_dir = dataset_dir / "workflow_smoke"
    private_dir.mkdir(parents=True, exist_ok=True)
    private_path = private_dir / "transcript.jsonl"
    with private_path.open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    private_path.chmod(0o600)

    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = _unique_path(VALIDATION_DIR / f"v672_workflow_smoke_{date.today().isoformat()}.json")
    repo_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"private transcript: {private_path}")
    print(f"repository aggregate: {repo_path}")
    return 0


def _run_case(case: dict) -> dict:
    card = triage_case(case)
    target_before = card["target"]["status"]
    if case["case_type"] == "C":
        priorities = card["target_priorities"]
        supplied = _supply(priorities[0], case) if priorities else None
        after = _after(case, supplied) if supplied else card
        return {
            "case_id": case["case_id"],
            "case_type": "C",
            "target_before": target_before,
            "supplied": supplied,
            "target_after": after["target"]["status"],
            "target_stop_after": after["target_stop"],
            "other_priorities": len(card["other_priorities"]),
        }
    priorities = card["other_priorities"] if case["case_type"] in ("B", "D", "A") else []
    supplied = _supply(priorities[0], case) if priorities else None
    after = _after(case, supplied) if supplied else card
    return {
        "case_id": case["case_id"],
        "case_type": case["case_type"],
        "target_before": target_before,
        "supplied": supplied,
        "target_after": after["target"]["status"],
        "target_stop_after": after["target_stop"],
        "topic_gap_after": after["topic_gap"],
        "other_statuses_before": card["baseline"]["statuses"],
        "other_statuses_after": after["baseline"]["statuses"],
    }


def _supply(priority: dict, case: dict) -> dict | None:
    field = priority["field"]
    values = SIM_VALUES.get(field, ())
    baseline = triage_case(case)
    before_target = baseline["target"]["status"]
    for value in values:
        modified = _with_value(case, field, value)
        after = triage_case(modified)
        if priority.get("target_relevant"):
            if after["target"]["status"] != before_target:
                return {"field": field, "value": value}
        else:
            if _other_changed(baseline, after):
                return {"field": field, "value": value}
    return None


def _with_value(case: dict, field: str, value) -> dict:
    modified = deepcopy(case)
    modified["snapshot"] = apply_value_to_snapshot(case["snapshot"], field, value)
    return modified


def _after(case: dict, supplied: dict | None) -> dict | None:
    if not supplied:
        return None
    return triage_case(_with_value(case, supplied["field"], supplied["value"]))


def _other_changed(before: dict, after: dict) -> bool:
    before_statuses = before["baseline"]["statuses"]
    after_statuses = after["baseline"]["statuses"]
    return any(before_statuses[key] != after_statuses[key] for key in before_statuses)


def _c_metrics(rows: list[dict]) -> dict:
    total = len(rows)
    resolved = sum(1 for row in rows if row["target_after"] not in (None, "unknown"))
    stopped = sum(1 for row in rows if row["target_stop_after"])
    return {
        "n": total,
        "target_resolved": resolved,
        "target_resolved_rate": (resolved / total) if total else None,
        "target_stop_after": stopped,
        "target_stop_after_rate": (stopped / total) if total else None,
    }


def _stop_metrics(rows: list[dict]) -> dict:
    total = len(rows)
    unchanged = sum(1 for row in rows if row["target_after"] is None or row["target_after"] == row["target_before"])
    stopped = sum(1 for row in rows if row["target_stop_after"] is None or row["target_stop_after"])
    return {
        "n": total,
        "target_unchanged": unchanged,
        "target_unchanged_rate": (unchanged / total) if total else None,
        "target_stop_after": stopped,
        "target_stop_after_rate": (stopped / total) if total else None,
    }


def _d_metrics(rows: list[dict]) -> dict:
    total = len(rows)
    topic_kept = sum(1 for row in rows if row["topic_gap_after"] in (None, True))
    stopped = sum(1 for row in rows if row["target_stop_after"] in (None, True))
    return {
        "n": total,
        "topic_gap_after": topic_kept,
        "topic_gap_after_rate": (topic_kept / total) if total else None,
        "target_stop_after": stopped,
        "target_stop_after_rate": (stopped / total) if total else None,
    }


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
