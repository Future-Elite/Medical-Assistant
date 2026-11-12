#!/usr/bin/env python3
"""Evaluate patient-fact record retrieval on the v6.7.2 qrels.

This is the first B-track evaluator: given one patient snapshot and a Chinese
fact question, can a deterministic BM25 baseline find the record that the qrels
mark as the source?  It measures record localization and answer-string hit; it
does not measure clinical correctness or a trained model.

Per-row predictions stay in private_data/; the repository gets only aggregates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import argparse
import json
import math
import os
from pathlib import Path
import sys

from rag.his import snapshot_from_dict
from rag.models import SourceDocument
from rag.retrieval.bm25 import tokenize

MODULE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = (REPO_ROOT / "private_data").resolve()
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"
TEMPLATES_PATH = MODULE_ROOT / "rag" / "eval" / "criteria_fixture" / "fact_question_templates.json"
OBSERVED_STATUSES = {"observed_positive", "observed_negative", "uncertain"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output-name", default="fact_eval/predictions.jsonl")
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

    templates = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))["templates"]
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

    metric_rows = 0
    missing_gold = 0
    missing_patient = 0
    not_recorded_rows = 0
    not_recorded_zero = 0
    recall = Counter()
    mrr = 0.0
    ndcg = 0.0
    answer_hit = 0
    by_type: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    statuses: Counter = Counter()
    predictions = []
    snapshot_cache: dict[str, object] = {}
    index_cache: dict[str, _PatientBM25] = {}
    for qrel in qrels:
        patient = patients.get(qrel.get("patient_ref"))
        if patient is None:
            missing_patient += 1
            continue
        fact_type = str(qrel.get("fact_type") or "")
        status = str(qrel.get("fact_status") or "")
        statuses[status] += 1
        patient_ref = str(qrel.get("patient_ref"))
        if patient_ref not in snapshot_cache:
            snapshot_cache[patient_ref] = snapshot_from_dict(patient["snapshot"])
        snapshot = snapshot_cache[patient_ref]
        if patient_ref not in index_cache:
            index_cache[patient_ref] = _PatientBM25(_fact_documents(snapshot))
        index = index_cache[patient_ref]
        fact_templates = templates.get(fact_type) or ["fact"]
        gold_path = str(qrel.get("record_path") or "")
        if status == "not_recorded":
            not_recorded_rows += 1
            top_score = _best_score(index, fact_templates[0])
            if top_score <= 0:
                not_recorded_zero += 1
            predictions.append({
                "patient_ref": qrel.get("patient_ref"),
                "fact_type": fact_type,
                "fact_status": status,
                "gold_record_path": gold_path,
                "rank": None,
                "top1_record_path": _top1(index, fact_templates[0]),
            })
            continue
        if not gold_path:
            missing_gold += 1
            continue
        ranks = []
        scores = []
        answer_values = []
        for template_id in (qrel.get("query_template_ids") or [])[:3]:
            template_index = _template_index(template_id)
            templates_for_type = fact_templates
            query = templates_for_type[template_index] if template_index < len(templates_for_type) else templates_for_type[0]
            ranked = index.rank(query)
            rank = next(
                (i + 1 for i, (doc, _) in enumerate(ranked) if doc.document_id == gold_path),
                None,
            )
            if rank is None:
                continue
            ranks.append(rank)
            scores.append(ranked[rank - 1][1])
            if rank == 1:
                answer_values.append(_fact_value(ranked[0][0]))
        if not ranks:
            missing_gold += 1
            continue
        metric_rows += 1
        best_rank = min(ranks)
        for k in (1, 3, 5):
            recall[k] += 1 if best_rank <= k else 0
        mrr += 1.0 / best_rank
        ndcg += 1.0 / math.log2(best_rank + 1) if best_rank <= 10 else 0.0
        if answer_values and any(
            str(qrel.get("answer_value")) in str(value)
            for value in answer_values if value is not None
        ):
            answer_hit += 1
        bucket = by_type[fact_type]
        bucket[0] += 1
        bucket[1] += 1 if best_rank == 1 else 0
        bucket[2] += 1.0 / best_rank
        bucket[3] += 1.0 / math.log2(best_rank + 1) if best_rank <= 10 else 0.0
        predictions.append({
            "patient_ref": qrel.get("patient_ref"),
            "fact_type": fact_type,
            "fact_status": status,
            "gold_record_path": gold_path,
            "rank": best_rank,
            "top1_record_path": _top1(index, fact_templates[0]),
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in predictions:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    output_path.chmod(0o600)

    def rate(value: int, total: int) -> float | None:
        return round(value / total, 4) if total else None

    aggregate = {
        "function": "v672_fact_eval",
        "dataset": str(dataset_dir.name),
        "qrel_rows": len(qrels),
        "evaluated_rows": metric_rows,
        "missing_gold": missing_gold,
        "missing_patient": missing_patient,
        "not_recorded_rows": not_recorded_rows,
        "not_recorded_top1_zero_score": not_recorded_zero,
        "metrics": {
            "recall@1": rate(recall[1], metric_rows),
            "recall@3": rate(recall[3], metric_rows),
            "recall@5": rate(recall[5], metric_rows),
            "mrr@10": round(mrr / metric_rows, 4) if metric_rows else None,
            "ndcg@10": round(ndcg / metric_rows, 4) if metric_rows else None,
            "answer_hit@1": rate(answer_hit, metric_rows),
        },
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
            "BM25 是词面检索基线，不是训练模型；只测病历记录定位，不测临床答案正确性。",
            "问题来自模板；模板与字段的措辞差异会限制上限。",
            "answer_hit@1 只表示答案串出现在 top1 记录文本里，不是结构化答案正确性。",
            "not_recorded 行没有可检索记录；只报告 top-1 零分比例，不当作拒答准确率。",
            "规则派生 qrels，不是临床金标准。",
        ],
    }
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = VALIDATION_DIR / f"v672_fact_eval_{date.today().isoformat()}.json"
    repo_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    print(f"private predictions: {output_path}")
    print(f"repository aggregate: {repo_path}")
    return 0


def _fact_documents(snapshot) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
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
        document_id = base if seen[base] == 1 else f"{base}#{seen[base]}"
        documents.append(SourceDocument(
            source_id="patient_fact", document_id=document_id, kind="patient_fact",
            title=f"{fact.collection}:{fact.event_type}", text=text,
            metadata={"chunk_id": document_id, "record_path": fact.record_path},
        ))
    return documents


class _PatientBM25:
    """One-patient BM25 index shared across all fact questions.

    Building the index once per patient avoids rebuilding document frequencies
    for every qrel row; the scoring formula matches the project BM25 baseline.
    """

    _WEIGHTS = {"title": 2.5, "text": 1.0}
    _K1 = 1.2
    _B = 0.75

    def __init__(self, documents: list[SourceDocument]):
        self.documents = documents
        self.fields = {
            doc.identity_key(): {
                "title": tokenize(doc.title),
                "text": tokenize(doc.text),
            }
            for doc in documents
        }
        self.idf: dict[tuple[str, str], float] = {}
        for field, weight in self._WEIGHTS.items():
            df: Counter = Counter()
            for fields in self.fields.values():
                for token in set(fields[field]):
                    df[token] += 1
            for token, count in df.items():
                self.idf[(field, token)] = math.log(
                    1.0 + (len(documents) - count + 0.5) / (count + 0.5)
                )
        self.averages = {
            field: (
                sum(len(fields[field]) for fields in self.fields.values())
                / max(1, len(documents))
            )
            for field in self._WEIGHTS
        }

    def rank(self, query: str) -> list[tuple[SourceDocument, float]]:
        query_tokens = tuple(dict.fromkeys(tokenize(query)))
        ranked: list[tuple[SourceDocument, float]] = []
        for document in self.documents:
            fields = self.fields[document.identity_key()]
            score = 0.0
            for field, weight in self._WEIGHTS.items():
                values = fields[field]
                if not values:
                    continue
                counts = Counter(values)
                average = self.averages[field] or 1.0
                for token in query_tokens:
                    tf = counts.get(token, 0)
                    if not tf:
                        continue
                    denominator = tf + self._K1 * (
                        1.0 - self._B + self._B * len(values) / average
                    )
                    score += weight * self.idf[(field, token)] * tf * (self._K1 + 1.0) / denominator
            ranked.append((document, score))
        return sorted(ranked, key=lambda pair: (-pair[1], pair[0].document_id))


def _best_score(index: _PatientBM25, query: str) -> float:
    ranked = index.rank(query)
    return ranked[0][1] if ranked else 0.0


def _top1(index: _PatientBM25, query: str) -> str | None:
    ranked = index.rank(query)
    return ranked[0][0].document_id if ranked else None


def _fact_value(document: SourceDocument):
    return document.text


def _template_index(template_id: str) -> int:
    text = str(template_id or "")
    if "-" not in text:
        return 0
    try:
        return int(text.rsplit("-", 1)[1])
    except ValueError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
