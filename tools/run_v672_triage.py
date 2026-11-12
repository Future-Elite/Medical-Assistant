#!/usr/bin/env python3
"""Run the v6.7.2 decision-relevant information triage on the test cases.

This is the workflow function: for each case it simulates plausible values of
each missing fact, recomputes all guideline statuses and coverage, and ranks the
facts by how many guideline decisions they unlock.  It also decides when to stop
asking (no missing fact changes the conclusion, or the topic is absent from the
local corpus).

Only aggregate metrics go to ``rag/validation``.  Per-case cards stay in the
private dataset directory.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from rag.eval.qrels.decision_triage import evaluate_triage

MODULE_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, help="private dataset directory")
    args = parser.parse_args()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    cases_path = dataset_dir / "test_cases.jsonl"
    if not cases_path.is_file():
        print(f"missing {cases_path}", file=sys.stderr)
        return 2
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = evaluate_triage(cases)
    cards = result.pop("cards")
    result["function"] = "decision_relevant_triage"
    result["caveats"] = [
        "rule-based simulation, not a clinical decision model",
        "C top-1 uses the target guideline's missing field as gold",
        "B/D stop correctness is defined against the 4 local guidelines",
        "not clinical validation",
    ]
    private_dir = dataset_dir / "triage"
    private_dir.mkdir(parents=True, exist_ok=True)
    private_path = private_dir / "triage_cards.jsonl"
    with private_path.open("w", encoding="utf-8") as handle:
        for card in cards:
            handle.write(json.dumps(card, ensure_ascii=False) + "\n")
    private_path.chmod(0o600)

    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = _unique_path(VALIDATION_DIR / f"v672_triage_{date.today().isoformat()}.json")
    repo_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"private triage cards: {private_path}")
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
