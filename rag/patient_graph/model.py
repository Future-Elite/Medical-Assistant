"""Immutable, per-request patient knowledge graph.

The graph is built from the facts that already passed the HIS ``as_of``
visibility filter.  It is a *retrieval index*, not a clinical model: nodes and
edges say where a fact is recorded, when it was recorded, which subject it
belongs to, and which patient-fact concept it mentions.  No edge asserts a
diagnosis, a causal relation or a treatment indication.

Node types
----------
``patient``  one root node per snapshot
``record``   one node per visible :class:`~rag.his.PatientRecord`
``concept``  a patient-fact concept from ``patient_fact_lexicon.json``
``subject``  ``patient`` or ``mother`` (never silently merged)
``time``     a calendar day used by ``RECORDED_AT``
``episode``  the ``as_of`` decision point

Edge types
----------
``HAS_RECORD``       patient -> record
``HAS_SUBJECT``      record -> subject
``MENTIONS``         record -> concept (field/value provenance in attributes)
``DERIVED_FROM``     derived record -> raw source record
``RECORDED_AT``      record -> time
``OCCURS_IN``        record -> episode (visible at the decision point)
``TEMPORALLY_BEFORE`` earlier record -> later record (chronological chain)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Iterable, Mapping, Sequence

NODE_TYPES = ("patient", "record", "concept", "subject", "time", "episode")
RELATION_TYPES = (
    "HAS_RECORD",
    "HAS_SUBJECT",
    "MENTIONS",
    "DERIVED_FROM",
    "RECORDED_AT",
    "OCCURS_IN",
    "TEMPORALLY_BEFORE",
)
# Traversal whitelist for retrieval expansion.  IS_A-style or free graph walks
# are deliberately absent: only provenance and the chronological chain may be
# followed, so a shared concept cannot pull in the whole chart.
EXPANSION_RELATIONS = frozenset({"DERIVED_FROM", "TEMPORALLY_BEFORE"})


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    node_type: str
    label: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        # ``search_tokens`` is an internal lexical index; keep it out of the
        # API payload so the graph response does not echo chart tokens.
        attributes = {
            key: value for key, value in self.attributes.items()
            if key != "search_tokens"
        }
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "label": self.label,
            "attributes": attributes,
        }


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    relation: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "attributes": dict(self.attributes),
        }

    def path_step(self) -> str:
        return f"{self.source} -[{self.relation}]-> {self.target}"


@dataclass(frozen=True)
class PatientGraph:
    patient_ref: str
    as_of: str
    snapshot_sha256: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    warnings: tuple[str, ...] = ()
    fact_count: int = 0
    source: str = "synthetic_chart"
    # Local facts stay attached for evidence rendering but are deliberately
    # excluded from ``to_dict``/``summary`` so graph payloads never echo the
    # raw chart back to a caller that only asked for graph metadata.
    records: tuple[Any, ...] = field(default=(), repr=False, compare=False)

    # -- indexes ---------------------------------------------------------
    @cached_property
    def node_by_id(self) -> dict[str, GraphNode]:
        return {node.node_id: node for node in self.nodes}

    @cached_property
    def record_nodes(self) -> tuple[GraphNode, ...]:
        return tuple(node for node in self.nodes if node.node_type == "record")

    @cached_property
    def concept_nodes(self) -> dict[str, GraphNode]:
        return {
            str(node.attributes.get("concept_id") or node.node_id): node
            for node in self.nodes
            if node.node_type == "concept"
        }

    @cached_property
    def record_node_by_path(self) -> dict[str, str]:
        return {
            str(node.attributes.get("record_path")): node.node_id
            for node in self.record_nodes
            if node.attributes.get("record_path")
        }

    @cached_property
    def outgoing(self) -> dict[str, tuple[GraphEdge, ...]]:
        index: dict[str, list[GraphEdge]] = {}
        for edge in self.edges:
            index.setdefault(edge.source, []).append(edge)
        return {key: tuple(value) for key, value in index.items()}

    @cached_property
    def incoming(self) -> dict[str, tuple[GraphEdge, ...]]:
        index: dict[str, list[GraphEdge]] = {}
        for edge in self.edges:
            index.setdefault(edge.target, []).append(edge)
        return {key: tuple(value) for key, value in index.items()}

    @cached_property
    def records_by_concept(self) -> dict[str, tuple[str, ...]]:
        index: dict[str, list[str]] = {}
        for edge in self.edges:
            if edge.relation != "MENTIONS":
                continue
            concept_id = str(edge.attributes.get("concept_id") or "")
            if not concept_id:
                continue
            index.setdefault(concept_id, []).append(edge.source)
        return {key: tuple(dict.fromkeys(value)) for key, value in index.items()}

    @cached_property
    def concepts_by_record(self) -> dict[str, tuple[str, ...]]:
        index: dict[str, list[str]] = {}
        for edge in self.edges:
            if edge.relation != "MENTIONS":
                continue
            concept_id = str(edge.attributes.get("concept_id") or "")
            if concept_id:
                index.setdefault(edge.source, []).append(concept_id)
        return {key: tuple(dict.fromkeys(value)) for key, value in index.items()}

    @cached_property
    def records_by_record_path(self) -> dict[str, Any]:
        return {
            str(getattr(record, "record_id", "")): record
            for record in self.records
            if getattr(record, "record_id", "")
        }

    # -- queries ---------------------------------------------------------
    def record_path(self, node_id: str) -> str:
        node = self.node_by_id.get(node_id)
        return str(node.attributes.get("record_path") or "") if node else ""

    def record_ids_for_concept(self, concept_id: str) -> tuple[str, ...]:
        return self.records_by_concept.get(concept_id, ())

    def concept_ids_for_record(self, node_id: str) -> tuple[str, ...]:
        return self.concepts_by_record.get(node_id, ())

    def concept_status(self, record_node_id: str, concept_id: str) -> str:
        for edge in self.outgoing.get(record_node_id, ()):
            if edge.relation == "MENTIONS" and edge.attributes.get("concept_id") == concept_id:
                return str(edge.attributes.get("status") or "")
        return ""

    def neighbors(
        self,
        node_id: str,
        *,
        relations: Iterable[str] | None = None,
        direction: str = "out",
    ) -> tuple[tuple[str, GraphEdge], ...]:
        allowed = set(relations) if relations is not None else None
        index = self.outgoing if direction == "out" else self.incoming
        rows: list[tuple[str, GraphEdge]] = []
        for edge in index.get(node_id, ()):
            if allowed is not None and edge.relation not in allowed:
                continue
            other = edge.target if direction == "out" else edge.source
            rows.append((other, edge))
        return tuple(rows)

    def shortest_path(
        self,
        start: str,
        target: str,
        *,
        max_hops: int = 3,
        relations: Iterable[str] | None = None,
    ) -> tuple[str, ...]:
        """Return node ids on the shortest whitelisted path, or ``()``.

        Paths are used for audit only.  They are never interpreted as clinical
        causality; ``HAS_RECORD`` and ``DERIVED_FROM`` are attribution edges.
        """
        if start == target:
            return (start,)
        if max_hops < 1:
            return ()
        allowed = set(relations) if relations is not None else set(RELATION_TYPES)
        queue: list[tuple[str, tuple[str, ...]]] = [(start, (start,))]
        seen = {start}
        while queue:
            current, path = queue.pop(0)
            if len(path) - 1 >= max_hops:
                continue
            for edge in self.outgoing.get(current, ()):
                if edge.relation not in allowed or edge.target in seen:
                    continue
                next_path = (*path, edge.target)
                if edge.target == target:
                    return next_path
                seen.add(edge.target)
                queue.append((edge.target, next_path))
        return ()

    def path_steps(self, node_ids: Sequence[str]) -> tuple[str, ...]:
        """Turn a node-id path into auditable ``source -[REL]-> target`` steps."""
        steps: list[str] = []
        for source, target in zip(node_ids, node_ids[1:]):
            relation = next(
                (
                    edge.relation
                    for edge in self.outgoing.get(source, ())
                    if edge.target == target
                ),
                "UNKNOWN",
            )
            steps.append(f"{source} -[{relation}]-> {target}")
        return tuple(steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "patient_ref": self.patient_ref,
            "as_of": self.as_of,
            "snapshot_sha256": self.snapshot_sha256,
            "fact_count": self.fact_count,
            "source": self.source,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "record_count": len(self.record_nodes),
            "concept_count": len(self.concept_nodes),
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "warnings": list(self.warnings),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "snapshot_sha256": self.snapshot_sha256,
            "as_of": self.as_of,
            "fact_count": self.fact_count,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "record_count": len(self.record_nodes),
            "concept_count": len(self.concept_nodes),
            "warnings": list(self.warnings),
        }
