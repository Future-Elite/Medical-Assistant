"""Patient-fact retrieval backed by the patient knowledge graph.

v6.7.3 removes the old flat term-overlap patient retriever.  Every patient
fact request builds a graph from the visible snapshot and runs the bounded
graph retrieval in :mod:`rag.patient_graph.retrieval`; there is no legacy
mode or feature switch.

Configuration (``RAG_PATIENT_GRAPH_MAX_HOPS`` /
``RAG_PATIENT_GRAPH_MAX_NODES`` / ``RAG_PATIENT_GRAPH_LEXICON``) only changes
the retrieval budget and vocabulary, not the retrieval engine.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Iterable, Sequence

from ..core.his import PatientSnapshot
from .builder import build_patient_graph
from .lexicon import PatientRecordLexicon, default_lexicon, load_lexicon
from .retrieval import GraphHit, RetrievalBudget, retrieve_patient_graph

PATIENT_GRAPH_ENGINE = "patient_graph"


def patient_fact_query_terms(
    hits: Iterable[GraphHit],
    lexicon: PatientRecordLexicon,
    *,
    limit: int = 12,
) -> tuple[str, ...]:
    """Non-identifying query terms derived from patient-graph hits.

    Only lexicon vocabulary is used — matched aliases and concept labels, never
    ``evidence_text`` (raw record content). These are concept-level terms of the
    same category as ``patient_terms``, suitable for feeding back into local
    retrieval (guideline cross-check) but not pushed to external connectors.
    """
    label_by_id = {spec.concept_id: spec.label for spec in lexicon.concepts}
    terms: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            terms.append(text)

    for hit in hits:
        for term in hit.matched_terms:
            add(term)
        for concept_id in hit.concept_ids:
            add(label_by_id.get(concept_id))
    return tuple(terms[: max(0, int(limit))])


@dataclass(frozen=True)
class PatientGraphSettings:
    max_hops: int = 2
    max_nodes: int = 240
    lexicon_path: str = ""

    def __post_init__(self) -> None:
        if self.max_hops < 0 or self.max_nodes < 1:
            raise ValueError("patient graph max_hops must be >= 0 and max_nodes > 0")

    @classmethod
    def from_env(cls) -> "PatientGraphSettings":
        # 配置值来自 rag/.env + 环境变量（core/config.py 统一解析），
        # 不在此重复读环境变量。
        from ..core.config import get_config
        config = get_config()
        return cls(
            max_hops=int(config.patient_graph_max_hops),
            max_nodes=int(config.patient_graph_max_nodes),
            lexicon_path=str(config.patient_graph_lexicon_path or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": PATIENT_GRAPH_ENGINE,
            "max_hops": self.max_hops,
            "max_nodes": self.max_nodes,
            "lexicon": self.lexicon_path or "builtin",
            "contract": "patient-snapshot",
            "accepts_ehr_view": True,
            "search_endpoint": "/api/patient-graph/search",
        }


def retrieve_patient_facts(
    snapshot: PatientSnapshot,
    queries: Sequence[str],
    *,
    top_k: int = 20,
    max_hops: int | None = None,
    max_nodes: int | None = None,
    lexicon: PatientRecordLexicon | None = None,
    lexicon_path: str | None = None,
) -> tuple[tuple[GraphHit, ...], dict[str, Any]]:
    """Return graph hits and a trace with the stable patient-context keys.

    ``trace`` keeps ``patient_context_id``, ``visible_fact_count``,
    ``patient_hit_count``, ``suppressed_future`` and ``warnings`` for existing
    callers, and always adds ``patient_graph`` with query resolution, seeds,
    expansion, budget, paths and truncation state.
    """
    settings = PatientGraphSettings.from_env()
    hops = settings.max_hops if max_hops is None else int(max_hops)
    nodes = settings.max_nodes if max_nodes is None else int(max_nodes)
    lex = lexicon or _lexicon(lexicon_path or settings.lexicon_path)
    graph = build_patient_graph(snapshot, lexicon=lex)
    graph_hits, graph_trace = retrieve_patient_graph(
        graph, queries, top_k=top_k,
        budget=RetrievalBudget(max_hops=hops, max_nodes=nodes), lexicon=lex,
    )
    graph_trace = {
        **graph_trace,
        "engine": PATIENT_GRAPH_ENGINE,
        "graph_lexicon_version": lex.schema_version,
        "query_terms": list(patient_fact_query_terms(graph_hits, lex)),
    }
    return graph_hits, {
        "patient_context_id": snapshot.context_id,
        "visible_fact_count": len(snapshot.visible_records()),
        "patient_hit_count": len(graph_hits),
        "suppressed_future": snapshot.suppressed_future,
        "warnings": list(dict.fromkeys((*snapshot.warnings, *graph_trace.get("warnings", ())))),
        "patient_graph": graph_trace,
    }


def build_patient_graph_for_snapshot(
    snapshot: PatientSnapshot,
    *,
    lexicon_path: str | None = None,
) -> dict[str, Any]:
    """Public helper for the workbench graph endpoint."""
    lex = _lexicon(lexicon_path or PatientGraphSettings.from_env().lexicon_path)
    return build_patient_graph(snapshot, lexicon=lex).to_dict()


def search_patient_graph(
    snapshot: PatientSnapshot,
    queries: Sequence[str],
    *,
    top_k: int = 20,
    max_hops: int | None = None,
    max_nodes: int | None = None,
    lexicon_path: str | None = None,
) -> dict[str, Any]:
    """Build a graph and run one retrieval; return a JSON-ready payload."""
    settings = PatientGraphSettings.from_env()
    hops = settings.max_hops if max_hops is None else int(max_hops)
    nodes = settings.max_nodes if max_nodes is None else int(max_nodes)
    lex = _lexicon(lexicon_path or settings.lexicon_path)
    graph = build_patient_graph(snapshot, lexicon=lex)
    hits, trace = retrieve_patient_graph(
        graph, queries, top_k=top_k,
        budget=RetrievalBudget(max_hops=hops, max_nodes=nodes), lexicon=lex,
    )
    return {
        "graph": graph.to_dict(),
        "hits": [hit_to_dict(hit) for hit in hits],
        "trace": {**trace, "engine": PATIENT_GRAPH_ENGINE},
    }


def hit_to_dict(hit: GraphHit) -> dict[str, Any]:
    return {
        "record_path": hit.record_path,
        "field_path": hit.field_path,
        "collection": hit.collection,
        "event_type": hit.event_type,
        "time": hit.time,
        "start": hit.start,
        "end": hit.end,
        "matched_terms": list(hit.matched_terms),
        "score": hit.score,
        "evidence_text": hit.evidence_text,
        "source_type": hit.source_type,
        "status": hit.status,
        "concept_ids": list(hit.concept_ids),
        "graph_path": list(hit.graph_path),
        "match_method": hit.match_method,
    }


def _lexicon(path: str) -> PatientRecordLexicon:
    return load_lexicon(path) if path else default_lexicon()


