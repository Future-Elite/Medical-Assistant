"""Bounded graph retrieval over one patient graph.

The retrieval plan is deliberately small and auditable:

1. **Resolve** the query into question intents (which record type is asked
   about) and clinical concepts (which fact is mentioned).  Unresolved text is
   reported, never forced onto a concept.
2. **Seed** record nodes from intent collections/fields, concept mentions and a
   conservative lexical fallback (the legacy tokenizer, so unknown queries do
   not regress to an empty result set).
3. **Expand** only over ``DERIVED_FROM`` (provenance) and, when the query asks
   for "latest"/"history", the ``TEMPORALLY_BEFORE`` chain.  There is no free
   walk over shared concepts.
4. **Rank** by match strength, record status, subject, temporal cue and hop
   distance; return the record path, the concept ids, the status and the graph
   path that produced the hit.

Scores are retrieval relevance, not probabilities, evidence quality or
clinical applicability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping, Sequence

from ..core.models import parse_iso_date
from .builder import fact_evidence_text
from .lexicon import ConceptSpec, IntentSpec, PatientRecordLexicon, QueryResolution, default_lexicon
from .model import EXPANSION_RELATIONS, PatientGraph

_STATUS_WEIGHT = {
    "observed_positive": 1.0,
    "observed_negative": 0.95,
    "uncertain": 0.7,
    "pending": 0.45,
    "not_recorded": 0.3,
}
_METHOD_ORDER = {"intent": 4, "concept": 3, "lexical": 2, "expansion": 1}


@dataclass(frozen=True)
class RetrievalBudget:
    max_hops: int = 2
    max_nodes: int = 240
    max_candidates: int = 400

    def __post_init__(self) -> None:
        if self.max_hops < 0 or self.max_nodes < 1 or self.max_candidates < 1:
            raise ValueError("retrieval budget requires max_hops >= 0 and positive node/candidate limits")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_hops": self.max_hops,
            "max_nodes": self.max_nodes,
            "max_candidates": self.max_candidates,
        }


@dataclass(frozen=True)
class GraphHit:
    record_path: str
    field_path: str
    collection: str
    event_type: str
    time: str | None
    start: str | None
    end: str | None
    matched_terms: tuple[str, ...]
    score: float
    evidence_text: str
    source_type: str = "patient_record"
    status: str = ""
    concept_ids: tuple[str, ...] = ()
    graph_path: tuple[str, ...] = ()
    match_method: str = "graph"


@dataclass
class _Candidate:
    node_id: str
    base: float
    method: str
    hop: int = 0
    matched: set[str] = field(default_factory=set)
    seed_node: str | None = None


def retrieve_patient_graph(
    graph: PatientGraph,
    queries: Sequence[str],
    *,
    top_k: int = 20,
    budget: RetrievalBudget | None = None,
    lexicon: PatientRecordLexicon | None = None,
) -> tuple[tuple[GraphHit, ...], dict[str, Any]]:
    lex = lexicon or default_lexicon()
    plan = budget or RetrievalBudget()
    limit = max(1, int(top_k))
    resolution = lex.resolve_queries(queries)
    root_id = _root_id(graph)
    candidates: dict[str, _Candidate] = {}
    warnings: list[str] = list(graph.warnings)

    def add(node_id: str, base: float, method: str, *, hop: int = 0,
            matched: Iterable[str] = (), seed_node: str | None = None) -> None:
        if node_id not in graph.node_by_id or graph.node_by_id[node_id].node_type != "record":
            return
        if base <= 0:
            return
        existing = candidates.get(node_id)
        if existing is None:
            candidates[node_id] = _Candidate(
                node_id=node_id, base=float(base), method=method, hop=hop,
                matched=set(matched), seed_node=seed_node,
            )
            return
        existing.base = max(existing.base, float(base))
        existing.matched.update(matched)
        if _METHOD_ORDER.get(method, 0) > _METHOD_ORDER.get(existing.method, 0):
            existing.method = method
        if existing.seed_node is None:
            existing.seed_node = seed_node

    # 1) Intent seeds: the record type the question is about.
    for intent in resolution.intents:
        aliases = _intent_alias_terms(resolution, intent)
        intent_subject = resolution.subject if set(intent.collections) <= {"maternal"} else None
        for record in graph.record_nodes:
            attrs = record.attributes
            collection = str(attrs.get("collection") or "")
            if collection not in intent.collections:
                continue
            if intent_subject and _subject(attrs) != intent_subject:
                continue
            rank = intent.collections.index(collection)
            base = 1.0 - 0.07 * rank
            presence = _presence(attrs)
            if any(presence.get(field.lower()) for field in intent.fields):
                base += 0.04
            if intent.aggregate and collection.startswith("derived_"):
                base += 0.03
            concepts = set(graph.concept_ids_for_record(record.node_id))
            intent_concepts = set(intent.concept_ids)
            if intent_concepts and not (concepts & intent_concepts):
                # A specimen- or method-specific intent (blood culture) may not
                # claim a record that only matches the collection name.  For
                # generic intents the concept edge is still a score bonus, not
                # a gate, so not_recorded records stay retrievable.
                if any(_specific_concept(lex, concept_id) for concept_id in intent_concepts):
                    continue
            if concepts & intent_concepts:
                base += 0.05
            add(record.node_id, base, "intent", matched=aliases)

    # 2) Clinical concept seeds (free-text queries and record tags).
    for concept in resolution.concepts:
        aliases = _concept_alias_terms(resolution, concept)
        concept_subject = resolution.subject if _maternal_concept(concept) else None
        for node_id in graph.record_ids_for_concept(concept.concept_id):
            attrs = graph.node_by_id[node_id].attributes
            if concept_subject and _subject(attrs) != concept_subject:
                continue
            base = 0.82
            if any(_presence(attrs).get(field.lower()) for field in concept.fields):
                base += 0.04
            add(node_id, base, "concept", matched=aliases)

    # 3) Lexical fallback: same tokenizer as the legacy path.  It only runs
    # when the vocabulary resolved nothing.  If a concept *did* resolve but the
    # chart has no record for it, the correct answer is "no evidence, fact not
    # recorded", not a different record that happens to share a bigram.
    query_terms: set[str] = set()
    for query in queries:
        query_terms.update(_terms(query))
    if query_terms and not resolution.intents and not resolution.concepts:
        for record in graph.record_nodes:
            attrs = record.attributes
            if resolution.subject and _subject(attrs) != resolution.subject:
                continue
            tokens = set(attrs.get("search_tokens") or ())
            matched = query_terms & tokens
            if not matched:
                continue
            add(
                record.node_id,
                0.45 * min(1.0, len(matched) / max(1, len(query_terms))),
                "lexical",
                matched=matched,
            )

    # 4) Bounded expansion over provenance and the chronological chain.
    seed_ids = sorted(
        candidates,
        key=lambda node_id: (-candidates[node_id].base, graph.record_path(node_id)),
    )[:12]
    expanded: list[dict[str, Any]] = []
    expanded_nodes = 0
    expansion_truncated = False
    for seed_id in seed_ids:
        seed = candidates[seed_id]
        frontier: list[tuple[str, float, int]] = [(seed_id, seed.base, 0)]
        visited = {seed_id}
        while frontier:
            current, base, hop = frontier.pop(0)
            if hop >= plan.max_hops:
                continue
            for direction, relation, factor in _expansion_steps(resolution):
                for other, _edge in graph.neighbors(current, relations={relation}, direction=direction):
                    if other in visited or graph.node_by_id[other].node_type != "record":
                        continue
                    visited.add(other)
                    if expanded_nodes >= plan.max_nodes:
                        expansion_truncated = True
                        break
                    expanded_nodes += 1
                    add(
                        other, base * factor, "expansion", hop=hop + 1,
                        matched=seed.matched, seed_node=seed_id,
                    )
                    expanded.append({
                        "record_path": graph.record_path(other),
                        "from": graph.record_path(current),
                        "relation": relation,
                        "hop": hop + 1,
                    })
                    frontier.append((other, base * factor, hop + 1))
                if expansion_truncated:
                    break
            if expansion_truncated:
                break
        if expansion_truncated:
            break
    if expansion_truncated:
        warnings.append(f"图扩展达到节点预算上限 {plan.max_nodes}，结果已截断。")

    # 5) Score.
    dated = [
        (node_id, _record_date(graph.node_by_id[node_id].attributes))
        for node_id in candidates
        if _record_date(graph.node_by_id[node_id].attributes) is not None
    ]
    latest_date = max((value for _node_id, value in dated), default=None)
    earliest_date = min((value for _node_id, value in dated), default=None)
    scored: list[tuple[float, str, _Candidate, str, tuple[str, ...]]] = []
    for node_id, candidate in candidates.items():
        attrs = graph.node_by_id[node_id].attributes
        status = _hit_status(graph, node_id, candidate, resolution)
        score = candidate.base + 0.08 * _status_weight(status)
        concept_ids = graph.concept_ids_for_record(node_id)
        if resolution.subject and _subject(attrs) == resolution.subject:
            score += 0.02
        if resolution.temporal == "latest" and latest_date is not None:
            if _record_date(attrs) == latest_date:
                score += 0.06
        if resolution.temporal == "history" and earliest_date is not None:
            if _record_date(attrs) == earliest_date:
                score += 0.04
        if resolution.negated and status == "observed_negative":
            score += 0.04
        if resolution.uncertain and status == "uncertain":
            score += 0.03
        if candidate.hop:
            score -= 0.05 * candidate.hop
        score = round(max(0.0, score), 6)
        scored.append((score, graph.record_path(node_id), candidate, status, concept_ids))

    # 6) Deduplicate by record path (a record may be reached by several seeds).
    merged: dict[str, tuple[float, str, _Candidate, str, tuple[str, ...]]] = {}
    for row in sorted(scored, key=lambda item: (-item[0], item[1])):
        existing = merged.get(row[1])
        if existing is None or row[0] > existing[0]:
            if existing is not None:
                row[2].matched.update(existing[2].matched)
            merged[row[1]] = row
        else:
            existing[2].matched.update(row[2].matched)
    ordered = sorted(merged.values(), key=lambda item: (-item[0], item[1]))
    if len(ordered) > plan.max_candidates:
        ordered = ordered[: plan.max_candidates]
        warnings.append(f"候选记录达到预算上限 {plan.max_candidates}，结果已截断。")
    selected = ordered[:limit]

    hits: list[GraphHit] = []
    hit_trace: list[dict[str, Any]] = []
    for score, record_path, candidate, status, concept_ids in selected:
        fact = graph.records_by_record_path.get(record_path)
        path = _hit_path(graph, root_id, candidate, concept_ids, resolution, plan)
        hit = GraphHit(
            record_path=record_path,
            field_path=str(graph.node_by_id[candidate.node_id].attributes.get("field_path") or record_path),
            collection=str(graph.node_by_id[candidate.node_id].attributes.get("collection") or ""),
            event_type=str(graph.node_by_id[candidate.node_id].attributes.get("event_type") or ""),
            time=graph.node_by_id[candidate.node_id].attributes.get("time"),
            start=graph.node_by_id[candidate.node_id].attributes.get("start"),
            end=graph.node_by_id[candidate.node_id].attributes.get("end"),
            matched_terms=tuple(sorted(candidate.matched))[:8],
            score=score,
            evidence_text=fact_evidence_text(fact) if fact is not None else "",
            status=status,
            concept_ids=tuple(concept_ids),
            graph_path=path,
            match_method=candidate.method,
        )
        hits.append(hit)
        hit_trace.append({
            "record_path": record_path,
            "score": score,
            "status": status,
            "match_method": candidate.method,
            "concept_ids": list(concept_ids),
            "graph_path": list(path),
        })

    truncated = len(merged) > len(selected) or expansion_truncated
    status = "ok"
    if not hits:
        status = "no_results"
    elif truncated:
        status = "partial"
    if not resolution.intents and not resolution.concepts and not hits:
        status = "needs_disambiguation"
    trace = {
        "mode": "active",
        "graph": graph.summary(),
        "query": resolution.to_dict(),
        "seeds": [
            {
                "record_path": graph.record_path(node_id),
                "match_method": candidates[node_id].method,
                "base_score": round(candidates[node_id].base, 6),
            }
            for node_id in seed_ids[:10]
        ],
        "expanded": expanded[:20],
        "hits": hit_trace,
        "budget": {
            **plan.to_dict(),
            "seed_count": len(seed_ids),
            "candidate_count": len(candidates),
            "expanded_count": expanded_nodes,
            "truncated": truncated,
        },
        "status": status,
        "warnings": list(dict.fromkeys(warnings)),
    }
    return tuple(hits), trace


def _root_id(graph: PatientGraph) -> str:
    for node in graph.nodes:
        if node.node_type == "patient":
            return node.node_id
    return "patient:root"


def _subject(attributes: Mapping[str, Any]) -> str:
    return str(attributes.get("subject") or "patient")


def _presence(attributes: Mapping[str, Any]) -> dict[str, bool]:
    raw = attributes.get("field_presence") or {}
    return {
        str(key).lower(): bool(value) for key, value in raw.items()
    }


def _record_date(attributes: Mapping[str, Any]):
    return parse_iso_date(attributes.get("start") or attributes.get("time"))


def _hit_status(graph: PatientGraph, node_id: str, candidate: _Candidate, resolution: QueryResolution) -> str:
    for intent in resolution.intents:
        for concept_id in intent.concept_ids:
            status = graph.concept_status(node_id, concept_id)
            if status:
                return status
    for concept in resolution.concepts:
        status = graph.concept_status(node_id, concept.concept_id)
        if status:
            return status
    return str(graph.node_by_id[node_id].attributes.get("status") or "observed_positive")


def _status_weight(status: str) -> float:
    return _STATUS_WEIGHT.get(status, 0.5)


def _specific_concept(lexicon: PatientRecordLexicon, concept_id: str) -> bool:
    spec = lexicon.concept(concept_id)
    return bool(spec and (spec.field_values or spec.exclude_aliases))


def _maternal_concept(concept: ConceptSpec) -> bool:
    """Only a maternal-specific concept may turn a subject cue into a filter.

    ``妈妈怀孕多少周生的`` is a question about the infant's birth record, not a
    request for the mother's own chart; a generic ``妈妈`` cue must not hide the
    patient-side GA record.
    """
    return concept.kind == "maternal_risk" or set(concept.collections) == {"maternal"}


def _intent_alias_terms(resolution: QueryResolution, intent: IntentSpec) -> tuple[str, ...]:
    target = f"intent:{intent.intent}"
    return tuple(alias for alias, resolved in resolution.matched_aliases if resolved == target)


def _concept_alias_terms(resolution: QueryResolution, concept: ConceptSpec) -> tuple[str, ...]:
    target = f"concept:{concept.concept_id}"
    aliases = [alias for alias, resolved in resolution.matched_aliases if resolved == target]
    if not aliases and concept.label:
        aliases.append(concept.label)
    return tuple(aliases)


def _expansion_steps(resolution: QueryResolution) -> tuple[tuple[str, str, float], ...]:
    steps: list[tuple[str, str, float]] = [
        ("out", "DERIVED_FROM", 0.9),
        ("in", "DERIVED_FROM", 0.92),
    ]
    if resolution.temporal is not None:
        steps.extend([
            ("out", "TEMPORALLY_BEFORE", 0.55),
            ("in", "TEMPORALLY_BEFORE", 0.55),
        ])
    return tuple(steps)


def _hit_path(
    graph: PatientGraph,
    root_id: str,
    candidate: _Candidate,
    concept_ids: tuple[str, ...],
    resolution: QueryResolution,
    plan: RetrievalBudget,
) -> tuple[str, ...]:
    allowed = {"HAS_RECORD", *EXPANSION_RELATIONS}
    path_nodes = graph.shortest_path(
        root_id, candidate.node_id, max_hops=plan.max_hops + 1, relations=allowed,
    )
    if candidate.method == "expansion" and candidate.seed_node:
        # Keep the provenance edge that caused the expansion instead of
        # collapsing to a direct HAS_RECORD path that hides it.
        root_path = graph.shortest_path(
            root_id, candidate.seed_node, max_hops=plan.max_hops + 1,
            relations={"HAS_RECORD", *EXPANSION_RELATIONS},
        )
        seed_path = graph.shortest_path(
            candidate.seed_node, candidate.node_id, max_hops=plan.max_hops,
            relations=EXPANSION_RELATIONS,
        )
        if root_path and seed_path:
            path_nodes = (*root_path, *seed_path[1:])
    if not path_nodes:
        return ()
    # When the query resolved a concept this record mentions, extend the audit
    # path by one attribution step; this is provenance, not causality.
    concept_targets = {
        concept.concept_id for concept in resolution.concepts
    } | {concept_id for intent in resolution.intents for concept_id in intent.concept_ids}
    for concept_id in concept_ids:
        if concept_id not in concept_targets:
            continue
        concept_node_id = f"concept:{concept_id.removeprefix('concept:')}"
        if concept_node_id in graph.node_by_id:
            extended = graph.shortest_path(
                candidate.node_id, concept_node_id, max_hops=1, relations={"MENTIONS"},
            )
            if extended:
                return (*graph.path_steps(path_nodes), *graph.path_steps(extended))
    return graph.path_steps(path_nodes)


def _terms(text: str) -> set[str]:
    ascii_terms = set(re.findall(r"[a-z0-9]+(?:[-./][a-z0-9]+)*", str(text).lower()))
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", str(text)))
    ascii_terms.update(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
    return ascii_terms
