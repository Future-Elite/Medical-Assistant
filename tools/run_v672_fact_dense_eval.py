#!/usr/bin/env python3
"""Zero-shot dense retrieval baseline for patient-fact qrels.

Compares the BM25 record-localization baseline with a cached multilingual E5
encoder.  No training: passages are prefixed with ``passage:``, questions with
``query:``, mean-pooled and normalized.  This is a generic multilingual model,
not a clinical model.

The model cache is read locally (offline); per-row predictions stay in
private_data/ and the repo gets only an aggregate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import argparse
import glob
import json
import math
import os
from pathlib import Path
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import torch
from transformers import AutoModel, AutoTokenizer

from rag.his import snapshot_from_dict

MODULE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = (REPO_ROOT / "private_data").resolve()
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"
TEMPLATES_PATH = MODULE_ROOT / "rag" / "eval" / "criteria_fixture" / "fact_question_templates.json"
DEFAULT_MODEL_GLOB = str(
    Path.home() / ".cache/huggingface/hub/models--intfloat--multilingual-e5-base/snapshots/*"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--model", default=os.environ.get("RAG_E5_MODEL") or "")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--output-name", default="fact_eval_dense/predictions.jsonl")
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
    qrels_path = dataset_dir / "patient_fact_qrels.jsonl"
    patients_path = dataset_dir / "patients_all.jsonl"
    if not qrels_path.is_file() or not patients_path.is_file():
        print("missing qrels or patients_all.jsonl", file=sys.stderr)
        return 2
    model_path = args.model or _latest_snapshot(DEFAULT_MODEL_GLOB)
    if not model_path:
        print("multilingual-e5-base cache not found; set RAG_E5_MODEL", file=sys.stderr)
        return 3

    templates = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))["templates"]
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    model = AutoModel.from_pretrained(model_path, local_files_only=True).eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)

    patients = {
        row["patient_ref"]: row
        for row in (
            json.loads(line)
            for line in patients_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    qrels = [
        json.loads(line)
        for line in qrels_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit > 0:
        qrels = qrels[: args.limit]

    query_vectors = {
        fact_type: _encode(
            [f"query: {question}" for question in questions],
            tokenizer, model, device, max_length=args.max_length, batch_size=args.batch_size,
        )
        for fact_type, questions in templates.items()
    }

    patient_docs: dict[str, tuple[list[str], list[str], torch.Tensor]] = {}
    metric_rows = 0
    missing_gold = 0
    missing_patient = 0
    not_recorded_rows = 0
    recall = Counter()
    mrr = 0.0
    ndcg = 0.0
    answer_hit = 0
    by_type: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    statuses: Counter = Counter()
    predictions = []
    for qrel in qrels:
        patient = patients.get(qrel.get("patient_ref"))
        if patient is None:
            missing_patient += 1
            continue
        patient_ref = str(qrel.get("patient_ref"))
        if patient_ref not in patient_docs:
            snapshot = snapshot_from_dict(patient["snapshot"])
            doc_ids, texts, matrix = _encode_patient(
                snapshot, tokenizer, model, device,
                max_length=args.max_length, batch_size=args.batch_size,
            )
            patient_docs[patient_ref] = (doc_ids, texts, matrix)
        doc_ids, texts, matrix = patient_docs[patient_ref]
        fact_type = str(qrel.get("fact_type") or "")
        status = str(qrel.get("fact_status") or "")
        statuses[status] += 1
        gold_path = str(qrel.get("record_path") or "")
        if status == "not_recorded":
            not_recorded_rows += 1
            predictions.append({
                "patient_ref": patient_ref, "fact_type": fact_type,
                "fact_status": status, "gold_record_path": gold_path,
                "rank": None, "top1_record_path": doc_ids[0] if doc_ids else None,
            })
            continue
        if not gold_path or gold_path not in doc_ids:
            missing_gold += 1
            continue
        gold_index = doc_ids.index(gold_path)
        ranks = []
        for question_index, question in enumerate(templates.get(fact_type, ["fact"])[:3]):
            if question_index >= query_vectors.get(fact_type, torch.empty(0)).shape[0]:
                continue
            scores = matrix @ query_vectors[fact_type][question_index]
            rank = int((scores > scores[gold_index]).sum().item()) + 1
            ranks.append(rank)
        if not ranks:
            missing_gold += 1
            continue
        metric_rows += 1
        best_rank = min(ranks)
        for k in (1, 3, 5):
            recall[k] += 1 if best_rank <= k else 0
        mrr += 1.0 / best_rank
        ndcg += 1.0 / math.log2(best_rank + 1) if best_rank <= 10 else 0.0
        top1 = int(torch.argmax(matrix @ query_vectors[fact_type][0]).item())
        if str(qrel.get("answer_value")) in texts[top1]:
            answer_hit += 1
        bucket = by_type[fact_type]
        bucket[0] += 1
        bucket[1] += 1 if best_rank == 1 else 0
        bucket[2] += 1.0 / best_rank
        bucket[3] += 1.0 / math.log2(best_rank + 1) if best_rank <= 10 else 0.0
        predictions.append({
            "patient_ref": patient_ref, "fact_type": fact_type,
            "fact_status": status, "gold_record_path": gold_path,
            "rank": best_rank, "top1_record_path": doc_ids[top1],
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in predictions:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    output_path.chmod(0o600)

    def rate(value: int, total: int) -> float | None:
        return round(value / total, 4) if total else None

    dense_metrics = {
        "recall@1": rate(recall[1], metric_rows),
        "recall@3": rate(recall[3], metric_rows),
        "recall@5": rate(recall[5], metric_rows),
        "mrr@10": round(mrr / metric_rows, 4) if metric_rows else None,
        "ndcg@10": round(ndcg / metric_rows, 4) if metric_rows else None,
        "answer_hit@1": rate(answer_hit, metric_rows),
    }
    aggregate = {
        "function": "v672_fact_dense_eval",
        "dataset": dataset_dir.name,
        "model": model_path,
        "device": device,
        "max_length": args.max_length,
        "qrel_rows": len(qrels),
        "evaluated_rows": metric_rows,
        "missing_gold": missing_gold,
        "missing_patient": missing_patient,
        "not_recorded_rows": not_recorded_rows,
        "metrics": dense_metrics,
        "by_fact_type": {
            key: {
                "rows": value[0],
                "recall@1": rate(value[1] if value[1] else 0, value[0]),
                "mrr@10": round(value[2] / value[0], 4) if value[0] else None,
                "ndcg@10": round(value[3] / value[0], 4) if value[0] else None,
            }
            for key, value in sorted(by_type.items())
        },
        "status_counts": dict(statuses),
        "caveats": [
            "零样本通用多语言 E5，不是临床模型，也没有在本数据上训练。",
            "只测病历记录定位；answer_hit@1 只是答案串出现在 top1 文本，不是结构化答案正确性。",
            "问题来自模板；模板与字段措辞差异会限制上限。",
            "not_recorded 行没有可检索记录，单独计数，不当作拒答准确率。",
            "CPU 推理，max_length=128；长文本被截断。",
        ],
    }
    bm25_path = VALIDATION_DIR / f"v672_fact_eval_{date.today().isoformat()}.json"
    if bm25_path.is_file():
        bm25 = json.loads(bm25_path.read_text(encoding="utf-8")).get("metrics", {})
        aggregate["bm25_comparison"] = {
            "bm25": bm25,
            "dense_minus_bm25": {
                key: round(dense_metrics[key] - bm25[key], 4)
                for key in ("recall@1", "recall@3", "recall@5", "mrr@10", "ndcg@10")
                if dense_metrics.get(key) is not None and bm25.get(key) is not None
            },
        }
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = VALIDATION_DIR / f"v672_fact_dense_eval_{date.today().isoformat()}.json"
    repo_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    print(f"private predictions: {output_path}")
    print(f"repository aggregate: {repo_path}")
    return 0


def _latest_snapshot(pattern: str) -> str | None:
    candidates = sorted(glob.glob(pattern))
    return candidates[-1] if candidates else None


def _encode(
    texts: list[str], tokenizer, model, device: str,
    *, max_length: int, batch_size: int,
) -> torch.Tensor:
    vectors = []
    with torch.inference_mode():
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            inputs = tokenizer(
                batch, padding=True, truncation=True, max_length=max_length,
                return_tensors="pt",
            ).to(device)
            output = model(**inputs).last_hidden_state
            mask = inputs["attention_mask"].unsqueeze(-1)
            pooled = (output * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            vectors.append(pooled.cpu())
    return torch.cat(vectors, dim=0) if vectors else torch.empty(0)


def _encode_patient(
    snapshot, tokenizer, model, device: str, *, max_length: int, batch_size: int,
) -> tuple[list[str], list[str], torch.Tensor]:
    doc_ids: list[str] = []
    texts: list[str] = []
    seen: Counter = Counter()
    for fact in snapshot.visible_facts():
        fields = {
            key: value for key, value in fact.fields.items()
            if key not in {"text", "free_text", "narrative"}
        }
        text = " ".join(f"{key} {value}" for key, value in fields.items() if value not in (None, ""))
        if not text.strip():
            text = f"{fact.collection} {fact.event_type}"
        base = fact.record_path
        seen[base] += 1
        doc_ids.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
        texts.append(text)
    vectors = _encode(
        [f"passage: {text}" for text in texts], tokenizer, model, device,
        max_length=max_length, batch_size=batch_size,
    )
    return doc_ids, texts, vectors


if __name__ == "__main__":
    raise SystemExit(main())
