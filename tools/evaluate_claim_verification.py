"""Evaluate real MiniCheck claim verification on the public SciFact claims.

SciFact supplies human SUPPORT / CONTRADICT labels for claim–abstract pairs
(AllenAI; BEIR release).  This script:

* pairs each labelled claim with the cited abstract from ``corpus.jsonl``;
* runs the official MiniCheck package with the configured checkpoint;
* reports the model label and a probability threshold sweep;
* writes every scored pair to a validation artifact.

The claims are English scientific claims, not Chinese clinical questions; this
is a real external benchmark, not a ChinaLLM clinical evaluation.

Usage (from the repository root, with optional torch/minicheck dependencies)::

    python3 tools/evaluate_claim_verification.py --dataset data/datasets/scifact --limit 200
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from rag.quality.claim_verification import MiniCheckVerifier  # noqa: E402

DEFAULT_DATASET = REPO_ROOT / "data" / "datasets" / "scifact"
DEFAULT_OUTPUT = REPO_ROOT / "rag" / "validation" / "claim_verification_scifact_2026-11-01.json"


def load_pairs(dataset: Path, *, limit: int = 0) -> list[dict[str, Any]]:
    corpus = {
        str(json.loads(line)["_id"]): json.loads(line)
        for line in (dataset / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    pairs: list[dict[str, Any]] = []
    for line in (dataset / "queries.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        query = json.loads(line)
        metadata = query.get("metadata") or {}
        for pmid, evidence_rows in metadata.items():
            document = corpus.get(str(pmid))
            if document is None or not evidence_rows:
                continue
            label = str(evidence_rows[0].get("label") or "").upper()
            if label not in {"SUPPORT", "CONTRADICT"}:
                continue
            text = (str(document.get("title") or "") + " " + str(document.get("text") or "")).strip()
            pairs.append({
                "query_id": str(query["_id"]),
                "claim": str(query.get("text") or ""),
                "pmid": str(pmid),
                "evidence_title": document.get("title"),
                "evidence": text,
                "gold": 1 if label == "SUPPORT" else 0,
                "gold_label": label,
            })
            break
        if limit and len(pairs) >= limit:
            break
    return pairs


def _metrics(rows: Sequence[Mapping[str, Any]], threshold: float) -> dict[str, Any]:
    tp = sum(1 for row in rows if row["prediction"] and row["gold"])
    fp = sum(1 for row in rows if row["prediction"] and not row["gold"])
    fn = sum(1 for row in rows if not row["prediction"] and row["gold"])
    tn = sum(1 for row in rows if not row["prediction"] and not row["gold"])
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else None
    return {
        "threshold": threshold,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "accuracy": round((tp + tn) / len(rows), 4) if rows else None,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--model", default="flan-t5-large")
    args = parser.parse_args(argv)

    pairs = load_pairs(args.dataset, limit=args.limit)
    if not pairs:
        print(f"no labelled pairs found under {args.dataset}", file=sys.stderr)
        return 1
    verifier = MiniCheckVerifier(model_name=args.model)
    if not verifier.available:
        print(f"MiniCheck unavailable: {verifier.unavailable_reason}", file=sys.stderr)
        return 2
    results = verifier.verify([(pair["claim"], pair["evidence"]) for pair in pairs])
    rows: list[dict[str, Any]] = []
    for pair, (label, probability) in zip(pairs, results):
        rows.append({
            **pair,
            "model_supported": bool(label),
            "probability": probability,
            "prediction": bool(label),
        })
    sweep = []
    for threshold in (0.3, 0.5, 0.7, 0.9, 0.95):
        for row in rows:
            row["prediction"] = bool(row["probability"] is not None and row["probability"] >= threshold)
        sweep.append(_metrics(rows, threshold))
    # Restore the model's own decision for the artifact rows.
    for row, (label, _) in zip(rows, results):
        row["prediction"] = bool(label)

    model_metrics = _metrics(rows, 0.5)
    model_metrics["threshold"] = None
    model_metrics["decision"] = "model_label"
    artifact = {
        "tool": "tools/evaluate_claim_verification.py",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "dataset": "BEIR SciFact (AllenAI human SUPPORT/CONTRADICT labels)",
        "dataset_path": str(args.dataset),
        "model": verifier.model_name,
        "pairs": len(rows),
        "model_metrics": model_metrics,
        "threshold_sweep": sweep,
        "rows": rows,
        "limitations": [
            "English scientific claims, not Chinese clinical questions.",
            "One labelled citation per query is used (the first in the release metadata).",
            "SUPPORT is treated as the positive class; CONTRADICT and unlabelled are not separated.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    print(json.dumps({"model_metrics": artifact["model_metrics"], "threshold_sweep": sweep}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
