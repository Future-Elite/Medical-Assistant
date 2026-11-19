"""Bidirectional fusion between k local guideline graphs and one patient graph.

Pipeline order (deliberate):

1. **Offline** — local PDFs are parsed and chunked by ``guideline_parsing``;
   ``tools/build/build_guideline_graph.py`` builds one graph per guideline and writes
   the library.  Query time never re-parses a PDF.
2. **Query time** — the local chunk index is scored, the top-k guidelines are
   selected, their pre-built graphs are loaded, and only those graphs are fused
   with the single patient graph.

The module is a retrieval/audit layer.  Extracted condition nodes are not
clinical criteria: ``review_status=extracted`` and all applicability dimensions
stay ``unknown``.  It does not change patient facts, guideline text, or action
eligibility.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import math
import re
import threading
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from ..core.his import PatientSnapshot
from ..patient_graph.builder import build_patient_graph
from ..patient_graph.retrieval import GraphHit, retrieve_patient_graph
from .guideline_graph import (
    DEFAULT_LIBRARY_PATH,
    build_guideline_graph,
    load_guideline_graph_library,
    write_json,
)

FUSION_SCHEMA = "local-guideline-fusion-1"
MAX_GUIDELINES = 6

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "not", "use", "used", "using", "should", "may", "can", "have", "has", "had",
    "患者", "患儿", "指南", "推荐", "建议", "进行", "以及", "或者", "如果", "需要",
}

# Small, auditable bilingual lexicon for the neonatal demo.  It is a query
# expansion aid, not a clinical ontology.
_SYNONYM_GROUPS = (
    ("cpap", "ncpap", "持续气道正压", "无创通气"),
    ("rds", "respiratory distress syndrome", "呼吸窘迫综合征", "呼吸窘迫"),
    ("feeding", "enteral nutrition", "喂养", "肠内营养"),
    ("preterm", "premature", "早产儿", "早产"),
    ("jaundice", "hyperbilirubinemia", "黄疸", "高胆红素"),
    ("ultrasound", "pocus", "超声", "床旁超声"),
    ("surfactant", "肺表面活性物质", "固尔苏"),
    ("oxygen", "oxygen therapy", "氧疗", "吸氧"),
    ("ventilation", "mechanical ventilation", "机械通气", "通气"),
    ("antibiotic", "antimicrobial", "抗菌", "抗感染", "抗生素"),
    ("pneumonia", "肺炎"),
    ("parenteral nutrition", "肠外营养", "静脉营养"),
    ("human milk", "breast milk", "母乳", "人乳"),
)

_REFERENCE_CUES = (
    "reference", "references", "bibliography", "acknowledg", "参考文献", "致谢",
)

_FIELD_QUERIES = {
    "birth_weight_g": "出生体重",
    "ga_days": "胎龄",
    "pna_days": "日龄",
    "temp_c": "体温",
    "spo2_percent": "血氧饱和度",
    "pao2": "动脉氧分压",
    "paco2": "二氧化碳分压",
    "ph": "酸碱度",
    "crp": "C反应蛋白",
    "wbc": "白细胞",
    "platelet": "血小板",
    "total_bilirubin_umol_l": "总胆红素",
    "glucose": "血糖",
    "fio2": "吸入氧浓度",
    "pressure_cm_h2o": "气道压力",
}


_LIBRARY_LOCK = threading.Lock()


def build_local_guideline_graphs(
    source: Any,
    guideline_ids: Sequence[str],
    *,
    library_path: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build and persist graphs for an explicit retrieval top-k list.

    ``cached`` means the pre-built library already contains the graph.  New
    graphs are built from the current parsed chunks and written back so the
    explicit "检索 → Top-k → 建图" step is reproducible.
    """
    ids = [str(item).strip() for item in guideline_ids if str(item).strip()]
    target = Path(library_path) if library_path else DEFAULT_LIBRARY_PATH
    warnings: list[str] = []
    with _LIBRARY_LOCK:
        try:
            library = load_guideline_graph_library(target)
        except (FileNotFoundError, ValueError) as exc:
            library = {
                "schema_version": "guideline-graph-library",
                "graph_version": "runtime",
                "generated_at": "",
                "manifest": {},
                "guidelines": [],
                "topic_nodes": [],
                "relations": [],
                "stats": {},
                "review_status": "extracted",
                "publishable": False,
            }
            warnings.append(f"预建图库不可用（{type(exc).__name__}），使用空库并现场建图。")
        titles = {
            str(row.get("document_id") or ""): str(row.get("title") or "")
            for row in source.list_guidelines()
        }
        by_id = {
            str(graph.get("guideline_id") or ""): graph
            for graph in library.get("guidelines") or ()
        }
        rows: list[dict[str, Any]] = []
        built: list[str] = []
        cached: list[str] = []
        missing: list[str] = []
        for guideline_id in ids:
            if guideline_id in by_id and not force:
                graph = by_id[guideline_id]
                cached.append(guideline_id)
                rows.append(_graph_row(graph, "cached", titles.get(guideline_id, "")))
                continue
            try:
                chunks = source.chunks(guideline_id)
                graph = build_guideline_graph(
                    chunks,
                    {"document_id": guideline_id, "title": titles.get(guideline_id) or guideline_id},
                )
            except Exception as exc:
                missing.append(guideline_id)
                warnings.append(f"{guideline_id}: {type(exc).__name__}: {str(exc)[:160]}")
                rows.append({"guideline_id": guideline_id, "status": "missing"})
                continue
            if guideline_id in by_id:
                index = next(
                    index for index, item in enumerate(library["guidelines"])
                    if str(item.get("guideline_id") or "") == guideline_id
                )
                library["guidelines"][index] = graph
            else:
                library["guidelines"].append(graph)
            by_id[guideline_id] = graph
            built.append(guideline_id)
            rows.append(_graph_row(graph, "built", titles.get(guideline_id, "")))
        if built:
            library["generated_at"] = _now_iso()
            write_json(target, library)
        return {
            "schema_version": "guideline-graph-build-1",
            "library_path": str(target),
            "requested": ids,
            "built": built,
            "cached": cached,
            "missing": missing,
            "guidelines": rows,
            "warnings": warnings,
        }


def _graph_row(graph: Mapping[str, Any], status: str, title: str = "") -> dict[str, Any]:
    nodes = list(graph.get("nodes") or ())
    recommendations = sum(1 for node in nodes if node.get("type") == "recommendation")
    return {
        "guideline_id": str(graph.get("guideline_id") or ""),
        "title": title or str(graph.get("title") or ""),
        "status": status,
        "nodes": len(nodes),
        "edges": len(graph.get("edges") or ()),
        "recommendations": recommendations,
        "review_status": str(graph.get("review_status") or "extracted"),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def select_local_guidelines(
    source: Any,
    queries: Sequence[str],
    *,
    top_guidelines: int = 3,
    chunks_per_guideline: int = 3,
    required_guideline_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Score local chunks and return the top-k guidelines plus their hits.

    ``source`` is a ``GuidelineParsingSource``-like object with
    ``list_guidelines()`` and ``chunks(document_id)``.
    """
    limit = _clamp(top_guidelines, 1, MAX_GUIDELINES)
    query_tokens = _expand_query_tokens(queries)
    rows = list(source.list_guidelines())
    warnings: list[str] = []
    chunks: list[dict[str, Any]] = []
    all_chunks_by_guideline: dict[str, list[dict[str, Any]]] = defaultdict(list)
    titles: dict[str, str] = {}
    for row in rows:
        guideline_id = str(row.get("document_id") or "").strip()
        if not guideline_id:
            continue
        titles[guideline_id] = str(row.get("title") or guideline_id)
        try:
            document_chunks = source.chunks(guideline_id)
        except Exception as exc:  # a missing parse must not break other guidelines
            warnings.append(f"{guideline_id}: {type(exc).__name__}: {str(exc)[:160]}")
            continue
        for chunk in document_chunks:
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue
            section_text = " ".join(str(item) for item in chunk.get("section_path") or ()).lower()
            if str(chunk.get("element_type") or "").lower() in {"reference", "references"}:
                continue
            if any(cue in section_text for cue in _REFERENCE_CUES):
                continue
            item = {
                "guideline_id": guideline_id,
                "chunk_id": str(chunk.get("chunk_id") or ""),
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "section_path": [str(item) for item in chunk.get("section_path") or ()],
                "text": text,
                "tokens": _tokens(text),
            }
            chunks.append(item)
            all_chunks_by_guideline[guideline_id].append(item)

    total_chunks = len(chunks)
    document_frequency: Counter[str] = Counter()
    for chunk in chunks:
        document_frequency.update(chunk["tokens"])
    idf = {
        token: math.log((total_chunks + 1) / (count + 0.5)) + 1.0
        for token, count in document_frequency.items()
    }

    scored: list[dict[str, Any]] = []
    for chunk in chunks:
        matched = chunk["tokens"] & query_tokens
        if not matched:
            continue
        score = sum(idf.get(token, 1.0) for token in matched)
        score /= math.sqrt(max(1, len(chunk["tokens"]))) + 1.0
        scored.append({**chunk, "score": round(score, 6)})

    per_guideline: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in scored:
        per_guideline[chunk["guideline_id"]].append(chunk)
    selected: list[dict[str, Any]] = []
    for guideline_id, items in per_guideline.items():
        items.sort(key=lambda item: (-item["score"], item["chunk_id"]))
        top = items[: max(1, int(chunks_per_guideline))]
        selected.append({
            "guideline_id": guideline_id,
            "title": titles.get(guideline_id, guideline_id),
            "score": round(sum(item["score"] for item in top), 6),
            "matched_chunk_count": len(items),
            "chunks": [
                {
                    "chunk_id": item["chunk_id"],
                    "page_start": item["page_start"],
                    "page_end": item["page_end"],
                    "section_path": item["section_path"],
                    "score": item["score"],
                    "text": item["text"][:360],
                }
                for item in top
            ],
        })
    selected.sort(key=lambda item: (-item["score"], item["guideline_id"]))
    required = [str(item).strip() for item in required_guideline_ids if str(item).strip()]
    selected_by_id = {row["guideline_id"]: row for row in selected}
    required_rows: list[dict[str, Any]] = []
    for guideline_id in required:
        row = selected_by_id.get(guideline_id)
        if row is None:
            fallback = all_chunks_by_guideline.get(guideline_id) or []
            if not fallback:
                warnings.append(f"目标指南没有已解析 chunk：{guideline_id}")
                continue
            fallback = sorted(fallback, key=lambda item: item["chunk_id"])[: max(1, int(chunks_per_guideline))]
            row = {
                "guideline_id": guideline_id,
                "title": titles.get(guideline_id, guideline_id),
                "score": 0.0,
                "matched_chunk_count": 0,
                "chunks": [
                    {
                        "chunk_id": item["chunk_id"],
                        "page_start": item["page_start"],
                        "page_end": item["page_end"],
                        "section_path": item["section_path"],
                        "score": 0.0,
                        "text": item["text"][:360],
                    }
                    for item in fallback
                ],
            }
        if row["guideline_id"] not in {item["guideline_id"] for item in required_rows}:
            required_rows.append(row)
    selected = required_rows + [
        row for row in selected if row["guideline_id"] not in {item["guideline_id"] for item in required_rows}
    ]
    selected = selected[: max(limit, len(required_rows))]
    return {
        "schema_version": "local-guideline-selection-1",
        "required_guideline_ids": required,
        "queries": [str(item) for item in queries if str(item).strip()],
        "query_tokens": sorted(query_tokens),
        "guidelines": selected,
        "stats": {
            "guidelines_total": len(rows),
            "guidelines_selected": len(selected),
            "chunks_total": total_chunks,
            "chunks_matched": len(scored),
        },
        "warnings": warnings,
    }


def fuse_local_guidelines(
    source: Any,
    snapshot: PatientSnapshot,
    queries: Sequence[str],
    *,
    library: Mapping[str, Any] | None = None,
    top_guidelines: int = 3,
    limit: int = 12,
    patient_top_k: int = 20,
    chunks_per_guideline: int = 3,
    target_guideline_id: str | None = None,
    guideline_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Select top-k local guidelines, fuse their graphs with one patient graph.

    ``target_guideline_id`` is always kept in the selected set (when it has
    parsed chunks), so the current target guideline is compared with the other
    k-1 guidelines instead of being pushed out by the query score.
    """
    explicit = [str(item).strip() for item in guideline_ids if str(item).strip()]
    if explicit:
        selection = select_local_guidelines(
            source, queries,
            top_guidelines=max(1, min(MAX_GUIDELINES, len(explicit))),
            chunks_per_guideline=chunks_per_guideline,
            required_guideline_ids=explicit,
        )
        allowed = set(explicit)
        selection["guidelines"] = [
            row for row in selection["guidelines"] if row["guideline_id"] in allowed
        ]
    else:
        selection = select_local_guidelines(
            source, queries, top_guidelines=top_guidelines,
            chunks_per_guideline=chunks_per_guideline,
            required_guideline_ids=[target_guideline_id] if target_guideline_id else (),
        )
    selected = selection["guidelines"]
    context_id = f"local-guideline-{uuid4().hex}"
    retrieval_id = f"retrieval-{uuid4().hex}"
    if not selected:
        return _empty_result(selection, context_id, retrieval_id)

    library_warning = ""
    if library is not None:
        graph_library = dict(library)
    else:
        try:
            graph_library = load_guideline_graph_library()
        except (FileNotFoundError, ValueError) as exc:
            graph_library = {"guidelines": []}
            library_warning = (
                f"预建指南图库不可用（{type(exc).__name__}: {str(exc)[:160]}）；"
                "将对本次选中的指南从 chunk 现场建图。"
            )
    by_id = {
        str(graph.get("guideline_id") or ""): graph
        for graph in graph_library.get("guidelines") or ()
    }
    missing = [row["guideline_id"] for row in selected if row["guideline_id"] not in by_id]
    built_on_demand: list[str] = []
    graph_errors: list[str] = []
    for row in selected:
        guideline_id = row["guideline_id"]
        if guideline_id in by_id:
            continue
        try:
            chunks = source.chunks(guideline_id)
            by_id[guideline_id] = build_guideline_graph(
                chunks, {"document_id": guideline_id, "title": row.get("title") or guideline_id},
            )
            built_on_demand.append(guideline_id)
        except Exception as exc:
            graph_errors.append(f"{guideline_id}: {type(exc).__name__}: {str(exc)[:160]}")
    if built_on_demand:
        missing = [item for item in missing if item not in built_on_demand]
    selected = [row for row in selected if row["guideline_id"] in by_id]
    if not selected:
        return _empty_result(
            {**selection, "guidelines": []}, context_id, retrieval_id,
            warning="本地指南没有对应图谱，且无法从 chunk 现场建图；先运行 tools/build/build_guideline_graph.py。",
        )

    patient_graph = build_patient_graph(snapshot)
    forward_queries = _forward_queries(snapshot, queries)
    forward_hits, forward_trace = retrieve_patient_graph(
        patient_graph, forward_queries, top_k=patient_top_k,
    )
    forward_tokens = [_hit_tokens(hit) for hit in forward_hits]

    candidates: list[dict[str, Any]] = []
    for row in selected:
        graph = by_id[row["guideline_id"]]
        try:
            candidates.extend(
                _candidates_for_graph(
                    graph, row, queries, forward_hits, forward_tokens,
                )
            )
        except Exception as exc:  # one malformed graph must not hide the others
            graph_errors.append(f"{row['guideline_id']}: {type(exc).__name__}: {str(exc)[:180]}")

    candidates = _round_robin(candidates, selected, limit=max(1, int(limit)))
    for candidate in candidates:
        _attach_evidence(
            candidate, patient_graph, forward_hits, forward_tokens,
        )

    target_id = str(target_guideline_id or "")
    recommendations = [_recommendation_payload(item, target_id) for item in candidates]
    recommendations.sort(key=lambda item: (-item["score"], item["recommendation_id"]))
    stats = _stats(selected, recommendations, forward_hits, candidates)
    status = "partial" if recommendations else "no_results"
    return {
        "schema_version": FUSION_SCHEMA,
        "status": status,
        "retrieval_id": retrieval_id,
        "knowledge_context_id": context_id,
        "knowledge_release_id": "local-guideline-graph-library",
        "release_sha256": _library_sha256(graph_library),
        "snapshot_sha256": patient_graph.snapshot_sha256,
        "queries": selection["queries"],
        "effective_queries": selection["queries"],
        "guidelines": [
            {key: value for key, value in row.items() if key != "chunks"}
            for row in selected
        ],
        "guideline_selection": selection,
        "recommendations": recommendations,
        "excluded_recommendations": [],
        "term_resolutions": [],
        "evidence_spans": {},
        "conflicts": [],
        "missing_information": sorted({
            item for recommendation in recommendations
            for item in recommendation.get("missing_information") or ()
        }),
        "fusion": {
            "schema_version": FUSION_SCHEMA,
            "forward": {
                "queries": forward_queries,
                "hits": len(forward_hits),
                "seeds": sorted({
                    str(term)
                    for hit in forward_hits
                    for term in (*hit.matched_terms, *hit.concept_ids)
                    if str(term).strip()
                })[:12],
                "trace": _trace_summary(forward_trace),
            },
            "backward": {
                "queries": sorted({
                    query for candidate in candidates
                    for query in candidate.get("backward_queries") or ()
                })[:24],
                "hits": sum(candidate.get("backward_hits") or 0 for candidate in candidates),
            },
            "stats": stats,
            "boundary": (
                "k 个本地指南图与 1 个患者图的融合只做检索和复核提示；"
                "节点 review_status=extracted，不改变患者事实、指南原文或临床资格。"
            ),
        },
        "diagnostics": {
            "selection": selection["stats"],
            "missing_graphs": missing,
            "built_on_demand": built_on_demand,
            "library_warning": library_warning,
            "graph_errors": graph_errors,
            "pipeline_order": "offline parse/chunk/graph -> query-time top-k guideline selection -> per-guideline fusion",
            "boundary": "extracted_candidates_not_clinical_criteria",
        },
    }


def _empty_result(
    selection: Mapping[str, Any], context_id: str, retrieval_id: str,
    *, warning: str = "",
) -> dict[str, Any]:
    warnings = list(selection.get("warnings") or ())
    if warning:
        warnings.append(warning)
    return {
        "schema_version": FUSION_SCHEMA,
        "status": "no_results",
        "retrieval_id": retrieval_id,
        "knowledge_context_id": context_id,
        "knowledge_release_id": "local-guideline-graph-library",
        "release_sha256": "",
        "snapshot_sha256": "",
        "queries": list(selection.get("queries") or ()),
        "effective_queries": list(selection.get("queries") or ()),
        "guidelines": [],
        "guideline_selection": selection,
        "recommendations": [],
        "excluded_recommendations": [],
        "term_resolutions": [],
        "evidence_spans": {},
        "conflicts": [],
        "missing_information": ["没有匹配的本地指南块或指南图谱"],
        "fusion": {
            "schema_version": FUSION_SCHEMA,
            "forward": {"queries": [], "hits": 0, "trace": {}},
            "backward": {"queries": [], "hits": 0},
            "stats": {
                "guidelines_selected": 0,
                "recommendations": 0,
                "recommendations_with_patient_evidence": 0,
                "reference_coverage": 0.0,
            },
            "boundary": "no_results 是“没有查到”，不是阴性，也不表示指南不适用。",
        },
        "diagnostics": {
            "selection": selection.get("stats") or {},
            "warnings": warnings,
            "pipeline_order": "offline parse/chunk/graph -> query-time top-k guideline selection -> per-guideline fusion",
            "boundary": "extracted_candidates_not_clinical_criteria",
        },
        "warnings": warnings,
    }


def _candidates_for_graph(
    graph: Mapping[str, Any],
    selection: Mapping[str, Any],
    queries: Sequence[str],
    forward_hits: Sequence[GraphHit],
    forward_tokens: Sequence[set[str]],
) -> list[dict[str, Any]]:
    nodes = {str(node.get("id") or ""): node for node in graph.get("nodes") or ()}
    edges = list(graph.get("edges") or ())
    chunk_text: dict[str, str] = {
        str(chunk.get("chunk_id") or ""): str(chunk.get("text") or "")
        for chunk in (selection.get("chunks") or ())
    }
    query_tokens = _expand_query_tokens(queries)
    guidelines_title = str(selection.get("title") or graph.get("title") or "")
    output: list[dict[str, Any]] = []
    for node in graph.get("nodes") or ():
        if node.get("type") != "recommendation":
            continue
        rec_id = str(node.get("id") or "")
        if not rec_id:
            continue
        conditions = _linked_nodes(rec_id, edges, nodes, "requires", "condition")
        exceptions = _linked_nodes(rec_id, edges, nodes, "excludes", "exception")
        chunk_id = str((node.get("source") or {}).get("chunk_id") or "")
        text = " ".join([
            str(node.get("statement") or ""),
            str(node.get("title") or ""),
            " ".join(str(item.get("raw_text") or "") for item in conditions),
            " ".join(str(item.get("raw_text") or "") for item in exceptions),
            chunk_text.get(chunk_id, "")[:600],
        ])
        rec_tokens = _tokens(text)
        if not rec_tokens:
            continue
        query_score = _overlap(rec_tokens, query_tokens)
        patient_score = max((_overlap(rec_tokens, tokens) for tokens in forward_tokens), default=0.0)
        if query_score <= 0 and patient_score <= 0:
            continue
        output.append({
            "recommendation_id": rec_id,
            "guideline_id": selection["guideline_id"],
            "guideline_title": guidelines_title,
            "summary": str(node.get("statement") or node.get("title") or rec_id),
            "direction": _direction(str(node.get("statement") or "")),
            "review_status": "extracted",
            "evidence_span_ids": [chunk_id] if chunk_id else [],
            "chunk_id": chunk_id,
            "conditions": conditions,
            "exceptions": exceptions,
            "tokens": rec_tokens,
            "score": round(0.7 * query_score + 0.3 * patient_score + 0.1 * min(1.0, float(selection.get("score") or 0.0)), 6),
            "patient_graph_evidence": [],
            "backward_queries": [],
            "backward_hits": 0,
            "missing_information": [
                "条件为 PDF 提取候选，未映射到结构化患者字段，需人工核对原文"
            ],
        })
    output.sort(key=lambda item: (-item["score"], item["recommendation_id"]))
    return output[:3]


def _attach_evidence(
    candidate: dict[str, Any],
    patient_graph: Any,
    forward_hits: Sequence[GraphHit],
    forward_tokens: Sequence[set[str]],
) -> None:
    seen: dict[str, dict[str, Any]] = {}
    for hit, tokens in zip(forward_hits, forward_tokens):
        if not (candidate["tokens"] & tokens):
            continue
        _add_evidence(seen, hit, "forward")
    condition_hits = 0
    conditions = list(candidate.get("conditions") or ()) + list(candidate.get("exceptions") or ())
    for condition in conditions:
        queries = _condition_queries(condition)
        if not queries:
            continue
        candidate["backward_queries"].extend(queries)
        hits, _trace = retrieve_patient_graph(patient_graph, queries, top_k=8)
        if hits:
            condition_hits += 1
            candidate["backward_hits"] += len(hits)
            for hit in hits:
                _add_evidence(seen, hit, "backward")
    evidence = list(seen.values())
    candidate["patient_graph_evidence"] = evidence
    candidate["fusion"] = {
        "fact_references": [item["record_path"] for item in evidence],
        "matched_references": condition_hits,
        "total_references": len(conditions),
        "coverage": round(condition_hits / len(conditions), 4) if conditions else 0.0,
        "forward_matched": sum(1 for item in evidence if item["origin"] in {"forward", "bidirectional"}),
        "backward_matched": sum(1 for item in evidence if item["origin"] in {"backward", "bidirectional"}),
        "condition_ref_matched": condition_hits,
    }


def _add_evidence(seen: dict[str, dict[str, Any]], hit: GraphHit, origin: str) -> None:
    existing = seen.get(hit.record_path)
    if existing is not None:
        if existing["origin"] != origin:
            existing["origin"] = "bidirectional"
        return
    seen[hit.record_path] = {
        "record_path": hit.record_path,
        "record_type": hit.collection,
        "event_type": hit.event_type,
        "status": hit.status,
        "origin": origin,
        "concept_ids": list(hit.concept_ids),
        "matched_terms": list(hit.matched_terms),
        "graph_path": list(hit.graph_path),
        "score": round(float(hit.score), 6),
    }


def _recommendation_payload(
    candidate: Mapping[str, Any], target_guideline_id: str = "",
) -> dict[str, Any]:
    guideline_id = str(candidate["guideline_id"])
    return {
        "recommendation_id": candidate["recommendation_id"],
        "summary": candidate["summary"],
        "direction": candidate["direction"],
        "source_ids": [guideline_id],
        "guideline_id": guideline_id,
        "guideline_title": candidate["guideline_title"],
        "same_target": bool(target_guideline_id and guideline_id == target_guideline_id),
        "review_status": "extracted",
        "evidence_span_ids": list(candidate.get("evidence_span_ids") or ()),
        "patient_graph_evidence": list(candidate.get("patient_graph_evidence") or ()),
        "fusion": dict(candidate.get("fusion") or {}),
        "applicability": {
            "population": "unknown",
            "indication": "unknown",
            "exclusion": "unknown",
            "setting": "unknown",
            "temporal": "unknown",
            "evidence_status": "unknown",
            "runtime_capability": "unknown",
        },
        "missing_information": list(candidate.get("missing_information") or ()),
        "score": candidate.get("score"),
        "chunk_id": candidate.get("chunk_id"),
        "backward_queries": list(dict.fromkeys(candidate.get("backward_queries") or ()))[:6],
    }


def _stats(
    selected: Sequence[Mapping[str, Any]],
    recommendations: Sequence[Mapping[str, Any]],
    forward_hits: Sequence[GraphHit],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    with_evidence = sum(1 for item in recommendations if item.get("patient_graph_evidence"))
    coverages = [
        float((item.get("fusion") or {}).get("coverage") or 0.0)
        for item in recommendations
    ]
    return {
        "guidelines_selected": len(selected),
        "guidelines_with_patient_evidence": len({
            str(item.get("guideline_id") or "") for item in recommendations
            if item.get("patient_graph_evidence")
        }),
        "recommendations": len(recommendations),
        "recommendations_with_patient_evidence": with_evidence,
        "forward_hits": len(forward_hits),
        "backward_hits": sum(int(item.get("backward_hits") or 0) for item in candidates),
        "reference_coverage": round(sum(coverages) / len(coverages), 4) if coverages else 0.0,
    }


def _linked_nodes(
    recommendation_id: str, edges: Sequence[Mapping[str, Any]],
    nodes: Mapping[str, Mapping[str, Any]], edge_type: str, node_type: str,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for edge in edges:
        if edge.get("source") != recommendation_id or edge.get("type") != edge_type:
            continue
        node = nodes.get(str(edge.get("target") or ""))
        if node is not None and node.get("type") == node_type:
            output.append(dict(node))
    return output


def _condition_queries(node: Mapping[str, Any]) -> list[str]:
    queries: list[str] = []
    field = str(node.get("field") or "")
    if field in _FIELD_QUERIES:
        queries.append(_FIELD_QUERIES[field])
    raw = str(node.get("raw_text") or "").strip()
    if raw:
        lowered = raw.lower()
        for group in _SYNONYM_GROUPS:
            terms = [term for term in group if term in lowered]
            if terms:
                queries.extend(terms)
        # 只有中文片段适合直接作为患者图查询；英文长句保留同义词组的短语即可。
        if _CJK.search(raw) and len(raw) <= 24 and not re.search(r"[。；;，,（）()：:]", raw):
            queries.append(raw)
    return list(dict.fromkeys(item for item in queries if item.strip() and len(item) <= 80))[:4]


def _direction(statement: str) -> str:
    lowered = statement.lower()
    negative = ("do not", "should not", "not recommended", "avoid", "不推荐", "不宜", "避免", "禁用")
    return "against" if any(term in lowered for term in negative) else "support"


def _forward_queries(snapshot: PatientSnapshot, queries: Sequence[str]) -> list[str]:
    values: list[str] = [str(item) for item in queries if str(item).strip()]
    for record in snapshot.visible_records():
        fields = record.fields
        for key in ("name", "symptom", "symptoms", "mode", "care_setting", "culture_status"):
            raw = fields.get(key)
            items = raw if isinstance(raw, (list, tuple)) else (raw,)
            for item in items:
                text = str(item or "").strip()
                if text and text not in values:
                    values.append(text)
        if len(values) >= 12:
            break
    return values[:12]


def _hit_tokens(hit: GraphHit) -> set[str]:
    return _tokens(" ".join([
        hit.evidence_text,
        " ".join(hit.matched_terms),
        " ".join(hit.concept_ids),
        hit.collection,
        hit.event_type,
    ]))


def _expand_query_tokens(queries: Sequence[str]) -> set[str]:
    tokens = _tokens(" ".join(str(item) for item in queries))
    for group in _SYNONYM_GROUPS:
        group_tokens = set().union(*(_tokens(term) for term in group))
        if tokens & group_tokens:
            tokens |= group_tokens
    return tokens


def _tokens(value: str) -> set[str]:
    lowered = str(value or "").lower()
    output = {token for token in _LATIN.findall(lowered) if token not in _STOPWORDS}
    for run in _CJK.findall(lowered):
        if run not in _STOPWORDS:
            output.add(run)
        for index in range(len(run) - 1):
            bigram = run[index:index + 2]
            if bigram not in _STOPWORDS:
                output.add(bigram)
    return output


def _overlap(tokens: set[str], query: set[str]) -> float:
    if not tokens or not query:
        return 0.0
    matched = len(tokens & query)
    return matched / (math.sqrt(len(tokens)) * math.sqrt(len(query)) + 1e-9)


def _round_robin(
    candidates: Sequence[Mapping[str, Any]],
    selected: Sequence[Mapping[str, Any]],
    *, limit: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[str(candidate["guideline_id"])].append(dict(candidate))
    for items in grouped.values():
        items.sort(key=lambda item: (-float(item.get("score") or 0.0), item["recommendation_id"]))
    output: list[dict[str, Any]] = []
    rank = 0
    while len(output) < limit:
        added = False
        for row in selected:
            items = grouped.get(row["guideline_id"]) or []
            if rank < len(items):
                output.append(items[rank])
                added = True
                if len(output) >= limit:
                    break
        if not added:
            break
        rank += 1
    return output


def _trace_summary(trace: Mapping[str, Any]) -> dict[str, Any]:
    value = trace.get("patient_graph") if isinstance(trace, Mapping) else None
    if not isinstance(value, Mapping):
        return {}
    return {
        "intent": value.get("intent"),
        "seeds": value.get("seeds"),
        "budget": value.get("budget"),
        "truncated": value.get("truncated"),
    }


def _library_sha256(library: Mapping[str, Any]) -> str:
    payload = {
        "graph_version": library.get("graph_version"),
        "generated_at": library.get("generated_at"),
        "guideline_ids": [
            str(graph.get("guideline_id") or "")
            for graph in library.get("guidelines") or ()
        ],
    }
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


def _clamp(value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))
