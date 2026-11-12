#!/usr/bin/env python3
"""Annotate v6.7.2 qrels candidates with an OpenAI-compatible LLM.

Input JSONL row:
  {"item_id": "...", "question": "...", "candidate": {"id": "...", "title": "...", "text": "..."},
   "context": {...}}

Output is written only under the project's private_data/ directory; the repo
gets an aggregate JSON without candidate text, patient ids, spans or API keys.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import argparse
import json
from pathlib import Path
import sys

from rag.eval.qrels.llm_annotation import (
    AnnotationError,
    AnnotationUnavailable,
    LLMAnnotator,
)

MODULE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="input JSONL (private data)")
    parser.add_argument("--output", required=True, help="output JSONL under private_data/")
    parser.add_argument("--track", required=True, choices=("external_evidence", "patient_fact", "applicability"))
    parser.add_argument("--limit", type=int, default=0, help="0 = all rows; >0 = pilot subset")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    if "private_data" not in output_path.parts:
        print("output must be under the project's private_data/ directory", file=sys.stderr)
        return 2
    if not input_path.is_file():
        print(f"missing input: {input_path}", file=sys.stderr)
        return 2
    rows = [json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit > 0:
        rows = rows[: args.limit]
    if not rows:
        print("input has no rows", file=sys.stderr)
        return 2

    try:
        annotator = LLMAnnotator()
    except AnnotationUnavailable as exc:
        print(f"LLM annotation unavailable: {exc}", file=sys.stderr)
        return 3

    output_path.parent.mkdir(parents=True, exist_ok=True)
    counts: Counter = Counter()
    labels: Counter = Counter()
    invalid = 0
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            item_id = str(row.get("item_id") or row.get("query_id") or "")
            question = str(row.get("question") or "")
            candidate = row.get("candidate")
            if not item_id or not question or not isinstance(candidate, dict):
                invalid += 1
                continue
            try:
                annotation = annotator.annotate(
                    track=args.track,
                    question=question,
                    candidate=candidate,
                    context=row.get("context") if isinstance(row.get("context"), dict) else None,
                )
            except AnnotationError as exc:
                invalid += 1
                counts["api_or_parse_error"] += 1
                handle.write(json.dumps({
                    "item_id": item_id, "track": args.track, "error": str(exc),
                    "label_source": "llm", "annotator_type": "llm",
                }, ensure_ascii=False) + "\n")
                continue
            handle.write(json.dumps({"item_id": item_id, **annotation}, ensure_ascii=False) + "\n")
            counts["rows"] += 1
            counts["needs_review"] += 1 if annotation.get("needs_review") else 0
            counts["abstain"] += 1 if annotation.get("abstain") else 0
            if annotation.get("label") is not None:
                labels[f"label_{annotation['label']}"] += 1
            if annotation.get("answerability"):
                labels[f"answerability_{annotation['answerability']}"] += 1
            if annotation.get("status"):
                labels[f"status_{annotation['status']}"] += 1
    output_path.chmod(0o600)

    summary = {
        "function": "v672_llm_annotation",
        "track": args.track,
        "model": annotator.model,
        "prompt_version": "v672-llm-annotate-v1",
        "input_rows": len(rows),
        "annotated": counts["rows"],
        "invalid_or_error": invalid,
        "needs_review": counts["needs_review"],
        "abstain": counts["abstain"],
        "labels": dict(sorted(labels.items())),
        "caveats": [
            "LLM labels are silver, not clinical gold",
            "span/numeric checks are structural, not semantic proof",
            "no patient text or API key in this aggregate",
        ],
    }
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = VALIDATION_DIR / f"v672_llm_annotation_{date.today().isoformat()}.json"
    repo_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"private annotations: {output_path}")
    print(f"repository aggregate: {repo_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
