"""Evaluate the retrieval stack on real BEIR SciFact human qrels.

This is an external, public benchmark: queries and qrels are the official BEIR
SciFact release (AllenAI).  It exercises the project's own
``rank_documents`` / RRF / relevance-gate code and, for the dense arm, the real
BGE-M3 adapter.  It is **not** a ChinaLLM clinical evaluation and must not be
reported as one.

Methods:
* ``bm25`` — lexical BM25 through ``rank_documents``;
* ``dense`` — BGE-M3 bi-encoder scores passed through the same ranking path;
* ``hybrid`` — BM25 + BGE-M3 RRF fusion (zero-signal routes are skipped by the
  existing RRF rule).

Usage (from the repository root; dense needs optional dependencies + BGE-M3 cache)::

    python3 tools/evaluate_public_retrieval.py --dataset data/datasets/scifact --limit 50
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from rag.models import RetrievalRequest, SourceDocument  # noqa: E402
from rag.neural_adapters import BgeM3Retriever  # noqa: E402
from rag.retrieval.query import build_query_plan  # noqa: E402
from rag.retrieval.ranking import rank_documents  # noqa: E402

DEFAULT_DATASET = REPO_ROOT / "data" / "datasets" / "scifact"
DEFAULT_OUTPUT = REPO_ROOT / "rag" / "validation" / "public_retrieval_scifact_2026-11-01.json"
KNOWN_SOURCES = [{"source_id": "scifact", "kinds": ["pubmed"]}]


def load_corpus(dataset: Path, *, max_docs: int = 0) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    for line in (dataset / "corpus.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        documents.append(SourceDocument(
            source_id="scifact",
            document_id=str(row["_id"]),
            kind="pubmed",
            title=str(row.get("title") or ""),
            text=str(row.get("text") or ""),
            metadata={"dataset": "beir-scifact"},
        ))
        if max_docs and len(documents) >= max_docs:
            break
    return documents


def load_queries(dataset: Path, qrels: Mapping[str, set[str]], *, limit: int = 0) -> list[dict[str, str]]:
    wanted = set(qrels)
    rows: list[dict[str, str]] = []
    for line in (dataset / "queries.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        query_id = str(row["_id"])
        if query_id not in wanted:
            continue
        rows.append({"query_id": query_id, "text": str(row.get("text") or "")})
        if limit and len(rows) >= limit:
            break
    return rows


def load_qrels(dataset: Path) -> dict[str, set[str]]:
    qrels: dict[str, set[str]] = {}
    path = dataset / "qrels" / "test.tsv"
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        parts = line.split("\t")
        if index == 0 and parts[0].lower() in {"query-id", "query_id"}:
            continue
        if len(parts) < 3:
            continue
        query_id, corpus_id, score = parts[0], parts[1], parts[2]
        try:
            relevance = int(score)
        except ValueError:
            continue
        if relevance > 0:
            qrels.setdefault(query_id, set()).add(corpus_id)
    return qrels


def ndcg_at_k(ranked_ids: Sequence[str], relevant: set[str], k: int) -> float:
    dcg = 0.0
    for index, document_id in enumerate(ranked_ids[:k], 1):
        if document_id in relevant:
            dcg += 1.0 / math.log2(index + 1)
    ideal = sum(1.0 / math.log2(index + 1) for index in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


def evaluate_method(
    name: str,
    plans: Mapping[str, Any],
    documents: Sequence[SourceDocument],
    queries: Sequence[Mapping[str, str]],
    qrels: Mapping[str, set[str]],
    *,
    neural_scores: Mapping[str, float] | None,
    top_k: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    for query in queries:
        started = time.perf_counter()
        methods = ("bm25",) if name == "bm25" else (("m3",) if name == "dense" else ("bm25", "m3"))
        ranked, warnings = rank_documents(
            plans[query["query_id"]],
            documents,
            as_of=None,
            top_k=top_k,
            methods=methods,
            neural_scores={"m3": neural_scores} if neural_scores else {},
            source_label="scifact",
        )
        elapsed = (time.perf_counter() - started) * 1000
        latencies.append(elapsed)
        ranked_ids = [item.document.document_id for item in ranked]
        relevant = set(qrels.get(query["query_id"], set()))
        rows.append({
            "query_id": query["query_id"],
            "query": query["text"],
            "ranked": ranked_ids,
            "relevant": sorted(relevant),
            "ndcg_at_10": round(ndcg_at_k(ranked_ids, relevant, top_k), 4),
            "recall_at_10": round(len(set(ranked_ids[:top_k]) & relevant) / len(relevant), 4) if relevant else 0.0,
            "mrr_at_10": _mrr(ranked_ids, relevant, top_k),
            "warnings": list(warnings),
        })
    latencies_sorted = sorted(latencies)
    return {
        "method": name,
        "queries": len(rows),
        "ndcg_at_10": round(mean(row["ndcg_at_10"] for row in rows), 4) if rows else None,
        "recall_at_10": round(mean(row["recall_at_10"] for row in rows), 4) if rows else None,
        "mrr_at_10": round(mean(row["mrr_at_10"] for row in rows), 4) if rows else None,
        "latency_ms": {
            "mean": round(mean(latencies), 2) if latencies else None,
            "p50": _percentile(latencies_sorted, 0.5),
            "p95": _percentile(latencies_sorted, 0.95),
            "max": round(max(latencies), 2) if latencies else None,
        },
        "rows": rows,
    }


def _mrr(ranked_ids: Sequence[str], relevant: set[str], k: int) -> float:
    for index, document_id in enumerate(ranked_ids[:k], 1):
        if document_id in relevant:
            return round(1.0 / index, 4)
    return 0.0


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    position = (len(values) - 1) * quantile
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return round(values[lower], 2)
    value = values[lower] * (upper - position) + values[upper] * (position - lower)
    return round(value, 2)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--max-docs", type=int, default=0)
    parser.add_argument("--doc-max-length", type=int, default=512, help="BGE-M3 document token limit (real model setting; 512=full, 256=faster run)")
    parser.add_argument("--batch-size", type=int, default=8, help="BGE-M3 document batch size")
    parser.add_argument("--methods", default="bm25,dense,hybrid")
    args = parser.parse_args(argv)

    qrels = load_qrels(args.dataset)
    documents = load_corpus(args.dataset, max_docs=args.max_docs)
    queries = load_queries(args.dataset, qrels, limit=args.limit)
    if not documents or not queries:
        print("no corpus or qrels-matched queries", file=sys.stderr)
        return 1
    plans = {
        query["query_id"]: build_query_plan(
            RetrievalRequest(request_id=f"scifact-{query['query_id']}", question=query["text"], top_k=args.top_k),
            KNOWN_SOURCES,
        )
        for query in queries
    }
    methods = [item.strip() for item in args.methods.split(",") if item.strip()]
    results: list[dict[str, Any]] = []
    dense_warmup_ms = None
    neural_scores = None
    if any(method in {"dense", "hybrid"} for method in methods):
        retriever = BgeM3Retriever()
        retriever.max_length = args.doc_max_length
        retriever.batch_size = args.batch_size
        if not retriever.available:
            print(f"BGE-M3 unavailable: {retriever.unavailable_reason}", file=sys.stderr)
            return 2
        first = queries[0]
        started = time.perf_counter()
        neural_scores = retriever.search(plans[first["query_id"]], tuple(documents), limit=len(documents)).scores
        dense_warmup_ms = (time.perf_counter() - started) * 1000

    for method in methods:
        if method == "bm25":
            results.append(evaluate_method("bm25", plans, documents, queries, qrels, neural_scores=None, top_k=args.top_k))
        elif method in {"dense", "hybrid"}:
            # Reuse the first query's scores; recompute scores for subsequent plans is
            # required because the query text changes.  The encoder caches document
            # vectors, so only the query is encoded per call.
            per_query_scores: dict[str, Mapping[str, float]] = {}
            for query in queries:
                per_query_scores[query["query_id"]] = retriever.search(
                    plans[query["query_id"]], tuple(documents), limit=len(documents)
                ).scores
            rows: list[dict[str, Any]] = []
            latencies: list[float] = []
            for query in queries:
                started = time.perf_counter()
                ranked, warnings = rank_documents(
                    plans[query["query_id"]], documents, as_of=None, top_k=args.top_k,
                    methods=("m3",) if method == "dense" else ("bm25", "m3"),
                    neural_scores={"m3": per_query_scores[query["query_id"]]},
                    source_label="scifact",
                )
                latencies.append((time.perf_counter() - started) * 1000)
                ranked_ids = [item.document.document_id for item in ranked]
                relevant = set(qrels.get(query["query_id"], set()))
                rows.append({
                    "query_id": query["query_id"],
                    "query": query["text"],
                    "ranked": ranked_ids,
                    "relevant": sorted(relevant),
                    "ndcg_at_10": round(ndcg_at_k(ranked_ids, relevant, args.top_k), 4),
                    "recall_at_10": round(len(set(ranked_ids[:args.top_k]) & relevant) / len(relevant), 4) if relevant else 0.0,
                    "mrr_at_10": _mrr(ranked_ids, relevant, args.top_k),
                    "warnings": list(warnings),
                })
            latencies_sorted = sorted(latencies)
            results.append({
                "method": method,
                "queries": len(rows),
                "ndcg_at_10": round(mean(row["ndcg_at_10"] for row in rows), 4),
                "recall_at_10": round(mean(row["recall_at_10"] for row in rows), 4),
                "mrr_at_10": round(mean(row["mrr_at_10"] for row in rows), 4),
                "latency_ms": {
                    "mean": round(mean(latencies), 2),
                    "p50": _percentile(latencies_sorted, 0.5),
                    "p95": _percentile(latencies_sorted, 0.95),
                    "max": round(max(latencies), 2),
                },
                "warmup_ms": round(dense_warmup_ms, 2) if dense_warmup_ms else None,
                "rows": rows,
            })
        else:
            print(f"unknown method: {method}", file=sys.stderr)
            return 1

    artifact = {
        "tool": "tools/evaluate_public_retrieval.py",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "dataset": "BEIR SciFact (official human qrels, test split)",
        "dataset_path": str(args.dataset),
        "corpus_documents": len(documents),
        "queries": len(queries),
        "top_k": args.top_k,
        "dense_warmup_ms": round(dense_warmup_ms, 2) if dense_warmup_ms else None,
        "dense_settings": {"model": "BAAI/bge-m3", "doc_max_length": args.doc_max_length, "batch_size": args.batch_size} if neural_scores is not None else None,
        "results": results,
        "limitations": [
            "Public English scientific claims; not a Chinese clinical or ChinaLLM patient evaluation.",
            "Dense scores come from BGE-M3 with CLS pooling; document vectors are cached after the first query.",
            "No reranker is applied in this run; RRF is the existing zero-signal-aware implementation.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    for result in results:
        print(json.dumps({key: value for key, value in result.items() if key != "rows"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
