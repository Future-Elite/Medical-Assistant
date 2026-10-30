"""Offline retrieval evaluation for manually annotated guideline evidence."""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from statistics import mean
from typing import Any

from .run_medical_assistant import build_rag


def load_queries(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError(f"评测集为空：{path}")
    for index, row in enumerate(rows, start=1):
        if not row.get("question"):
            raise ValueError(f"第 {index} 条缺少 question")
        anchors = row.get("gold_evidence")
        legacy = row.get("relevant_document_ids")
        if anchors is None and not isinstance(legacy, dict):
            raise ValueError(f"第 {index} 条必须包含 gold_evidence 或 relevant_document_ids")
        if anchors is not None:
            if not isinstance(anchors, list) or not anchors:
                raise ValueError(f"第 {index} 条 gold_evidence 必须是非空数组")
            for anchor in anchors:
                if not isinstance(anchor, dict) or not anchor.get("guide_title"):
                    raise ValueError(f"第 {index} 条证据锚点必须提供 guide_title")
                if not anchor.get("anchor_terms") or not all(anchor["anchor_terms"]):
                    raise ValueError(f"第 {index} 条证据锚点必须提供非空 anchor_terms")
        if isinstance(legacy, dict):
            for system, document_ids in legacy.items():
                if not isinstance(document_ids, list) or not document_ids:
                    raise ValueError(f"第 {index} 条 system={system} 必须标注至少一个 relevant document ID")
    return rows


def _reciprocal_rank(relevant: set[str], ranked_ids: list[str]) -> float:
    for rank, document_id in enumerate(ranked_ids, start=1):
        if document_id in relevant:
            return 1.0 / rank
    return 0.0


def _ndcg(relevant: set[str], ranked_ids: list[str], k: int) -> float:
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, document_id in enumerate(ranked_ids[:k], start=1)
        if document_id in relevant
    )
    ideal_hits = min(len(relevant), k)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / ideal if ideal else 0.0


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _load_library_documents(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    sources = raw.get("sources", []) if isinstance(raw, dict) else raw
    return [doc for source in sources for doc in source.get("documents", [])]


def _resolve_gold(row: dict[str, Any], system_name: str, documents: list[dict[str, Any]]) -> tuple[set[str], list[dict[str, Any]]]:
    legacy = row.get("relevant_document_ids")
    if legacy is not None:
        relevant = set(legacy.get(system_name, []))
        if not relevant:
            raise ValueError(
                f"评测题 {row.get('query_id') or row['question']} 缺少方案 {system_name} 的旧格式 chunk ID 标注"
            )
        return relevant, []

    relevant: set[str] = set()
    diagnostics = []
    for anchor in row["gold_evidence"]:
        expected_title = _normalize(anchor["guide_title"])
        section_hint = _normalize(anchor.get("section_contains", ""))
        terms = [_normalize(term) for term in anchor["anchor_terms"]]
        matches = []
        for doc in documents:
            metadata = doc.get("metadata") or {}
            title = _normalize(metadata.get("guide_title") or doc.get("title", ""))
            section = _normalize(" ".join(metadata.get("section_path") or []))
            text = _normalize(doc.get("text", ""))
            if expected_title not in title:
                continue
            if section_hint and section_hint not in section:
                continue
            if not all(term in text for term in terms):
                continue
            matches.append(doc)
        relevant.update(str(doc["document_id"]) for doc in matches)
        diagnostics.append({
            "guide_title": anchor["guide_title"],
            "section_contains": anchor.get("section_contains", ""),
            "anchor_terms": anchor["anchor_terms"],
            "element_type": anchor.get("element_type", ""),
            "matched_document_ids": [str(doc["document_id"]) for doc in matches],
            "matched": bool(matches),
        })
    return relevant, diagnostics


def evaluate_systems(
    queries: list[dict[str, Any]],
    systems: list[dict[str, str]],
    k_values: tuple[int, ...] = (1, 3, 5, 8),
    benchmark: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not systems:
        raise ValueError("至少需要一个知识库方案")
    if not k_values or min(k_values) < 1:
        raise ValueError("k_values 必须是正整数")
    max_k = max(k_values)
    reports = []

    for system in systems:
        name = system["name"]
        library_path = Path(system["library"])
        documents = _load_library_documents(library_path)
        rag = build_rag(library_path)
        per_query = []
        elapsed_values = []
        resolved_anchor_count = 0
        total_anchor_count = 0
        unmatched_anchors = []

        for row in queries:
            relevant, diagnostics = _resolve_gold(row, name, documents)
            if diagnostics:
                total_anchor_count += len(diagnostics)
                failed = [item for item in diagnostics if not item["matched"]]
                unmatched_anchors.extend({"query_id": row.get("query_id", ""), **item} for item in failed)
                resolved_anchor_count += len(diagnostics) - len(failed)
                if failed:
                    per_query.append({
                        "query_id": row.get("query_id", ""),
                        "question": row["question"],
                        "excluded_from_metrics": True,
                        "gold_evidence": diagnostics,
                        "warnings": ["gold evidence anchor did not resolve in this library"],
                    })
                    continue

            started = time.perf_counter()
            result = rag.retrieve_evidence({
                "request_id": f"eval-{name}-{row.get('query_id', len(per_query) + 1)}",
                "question": row["question"],
                "source_mode": "custom",
                "source_kinds": ["guideline"],
                "top_k": max_k,
            })
            elapsed_ms = (time.perf_counter() - started) * 1000
            elapsed_values.append(elapsed_ms)
            ranked = result["ranked_documents"]
            ranked_ids = [item["document"]["document_id"] for item in ranked]
            retrieved_docs = [item["document"] for item in ranked]
            matched = relevant.intersection(ranked_ids)
            context_chars = {
                k: sum(len(item["document"].get("text") or "") for item in ranked[:k])
                for k in k_values
            }
            expected_types = {
                item["element_type"] for item in diagnostics if item.get("element_type")
            } if diagnostics else set(row.get("relevant_element_types") or [])
            element_hits = {
                k: int(any(
                    doc.get("document_id") in relevant
                    and (not expected_types or doc.get("metadata", {}).get("element_type") in expected_types)
                    for doc in retrieved_docs[:k]
                )) if expected_types else None
                for k in k_values
            }
            per_query.append({
                "query_id": row.get("query_id", ""),
                "question": row["question"],
                "excluded_from_metrics": False,
                "relevant_document_ids": sorted(relevant),
                "gold_evidence": diagnostics,
                "ranked_document_ids": ranked_ids,
                "first_hit_rank": next((i for i, item in enumerate(ranked_ids, start=1) if item in relevant), None),
                "relevant_found_at_max_k": len(matched),
                "relevant_total": len(relevant),
                "latency_ms": round(elapsed_ms, 2),
                "metrics": {
                    str(k): {
                        "recall": len(relevant.intersection(ranked_ids[:k])) / len(relevant),
                        "hit": int(bool(relevant.intersection(ranked_ids[:k]))),
                        "mrr": _reciprocal_rank(relevant, ranked_ids[:k]),
                        "ndcg": _ndcg(relevant, ranked_ids, k),
                        "element_hit": element_hits[k],
                        "context_chars": context_chars[k],
                    }
                    for k in k_values
                },
                "warnings": result.get("warnings", []),
            })

        valid_rows = [item for item in per_query if not item.get("excluded_from_metrics")]
        if not valid_rows:
            raise ValueError(f"方案 {name} 没有可用于评估的题目；请检查 gold_evidence 锚点和指南名称")
        summary = {}
        for k in k_values:
            entries = [item["metrics"][str(k)] for item in valid_rows]
            summary[str(k)] = {
                metric: round(mean(entry[metric] for entry in entries), 4)
                for metric in ("recall", "hit", "mrr", "ndcg", "context_chars")
            }
            element_values = [entry["element_hit"] for entry in entries if entry["element_hit"] is not None]
            if element_values:
                summary[str(k)]["element_hit"] = round(mean(element_values), 4)

        reports.append({
            "system": name,
            "library": str(library_path.resolve()),
            "query_count": len(queries),
            "evaluated_query_count": len(valid_rows),
            "query_coverage": round(len(valid_rows) / len(queries), 4),
            "gold_anchor_coverage": round(
                resolved_anchor_count / total_anchor_count, 4
            ) if total_anchor_count else None,
            "unmatched_anchors": unmatched_anchors,
            "average_latency_ms": round(mean(elapsed_values), 2) if elapsed_values else None,
            "summary": summary,
            "queries": per_query,
        })
    return {
        "benchmark": benchmark or {},
        "metric_definition": {
            "recall": "Relevant chunks resolved from gold evidence anchors and retrieved in top K / all resolved relevant chunks.",
            "hit": "Whether at least one relevant chunk appears in top K.",
            "mrr": "Reciprocal rank of the first relevant chunk.",
            "ndcg": "Binary-relevance nDCG using resolved evidence anchors.",
            "element_hit": "Whether a relevant chunk with the expected element_type occurs in top K.",
            "context_chars": "Total retrieved text characters in top K; proxy for context size, not tokenizer tokens.",
            "latency": "Wall-clock retrieval latency; includes local ranking only.",
        },
        "k_values": list(k_values),
        "systems": reports,
    }


def save_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
