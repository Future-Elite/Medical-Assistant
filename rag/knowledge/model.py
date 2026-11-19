"""Immutable knowledge graph, release manifest and term resolution.

The graph is the EHR-style layer: concepts, actions/examinations, monitoring
panels, recommendations, populations, conditions, evidence spans, source
revisions and runtime bindings.  It is separate from the per-request patient
graph in :mod:`rag.patient_graph`; the patient graph supplies facts, this graph
supplies guideline knowledge and conditions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "neonatal_graph_rag_demo.json"

NODE_TYPES = (
    "concept",
    "action",
    "panel",
    "population",
    "condition",
    "recommendation",
    "source",
    "evidence_span",
    "runtime_binding",
    "care_setting",
    "lab_analyte",
    "examination",
    "reference_definition",
    "alias",
)

RELATION_TYPES = (
    "IS_A",
    "HAS_ALIAS",
    "HAS_ANALYTE",
    "ADDRESSED_BY",
    "RECOMMENDS",
    "CONSTRAINS",
    "APPLIES_TO",
    "REQUIRES",
    "EXCLUDES",
    "MONITOR_WITH",
    "SUPPORTED_BY",
    "PART_OF_SOURCE",
    "CONFLICTS_WITH",
    "SUPERSEDES",
    "BINDS_TO",
)

_DIRECTIONS = {"support", "against", "not_routine", "consider", "monitor", "background"}
_OPERATIONS = {"start", "adjust", "stop", "continue", "wait", "order_test"}


class KnowledgeError(ValueError):
    """Raised when a knowledge fixture is malformed or inconsistent."""


@dataclass(frozen=True)
class KnowledgeRelease:
    release_id: str
    knowledge_version: str
    schema_version: str
    created_at: str
    status: str
    review_status: str
    publishable: bool
    content_sha256: str
    notes: str = ""
    coverage: tuple[str, ...] = ()
    dependencies: Mapping[str, str] = field(default_factory=dict)
    change_summary: str = ""
    known_gaps: tuple[str, ...] = ()
    evaluation: Mapping[str, Any] = field(default_factory=dict)
    index_config: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "knowledge_version": self.knowledge_version,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "status": self.status,
            "review_status": self.review_status,
            "publishable": self.publishable,
            "content_sha256": self.content_sha256,
            "notes": self.notes,
            "coverage": list(self.coverage),
            "dependencies": dict(self.dependencies),
            "change_summary": self.change_summary,
            "known_gaps": list(self.known_gaps),
            "evaluation": dict(self.evaluation),
            "index_config": dict(self.index_config),
        }


class KnowledgeGraph:
    def __init__(self, data: Mapping[str, Any], *, source_path: str = "") -> None:
        if not isinstance(data, Mapping):
            raise KnowledgeError("knowledge fixture must be an object")
        self.source_path = source_path
        self.raw = dict(data)
        release = data.get("release")
        if not isinstance(release, Mapping):
            raise KnowledgeError("knowledge fixture requires a release object")
        self.release = KnowledgeRelease(
            release_id=str(release.get("release_id") or ""),
            knowledge_version=str(release.get("knowledge_version") or ""),
            schema_version=str(release.get("schema_version") or ""),
            created_at=str(release.get("created_at") or ""),
            status=str(release.get("status") or ""),
            review_status=str(release.get("review_status") or ""),
            publishable=bool(release.get("publishable")),
            content_sha256=self._content_hash(data),
            notes=str(release.get("notes") or ""),
            coverage=tuple(str(item) for item in release.get("coverage") or ()),
            dependencies={str(key): str(value) for key, value in (release.get("dependencies") or {}).items()},
            change_summary=str(release.get("change_summary") or ""),
            known_gaps=tuple(str(item) for item in release.get("known_gaps") or ()),
            evaluation=dict(release.get("evaluation") or {}),
            index_config=dict(release.get("index_config") or {}),
        )
        if not self.release.release_id:
            raise KnowledgeError("release_id is required")
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.concepts: dict[str, dict[str, Any]] = {}
        self.actions: dict[str, dict[str, Any]] = {}
        self.panels: dict[str, dict[str, Any]] = {}
        self.populations: dict[str, dict[str, Any]] = {}
        self.conditions: dict[str, dict[str, Any]] = {}
        self.recommendations: dict[str, dict[str, Any]] = {}
        self.sources: dict[str, dict[str, Any]] = {}
        self.spans: dict[str, dict[str, Any]] = {}
        self.bindings: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, dict[str, Any]] = {}
        self.analytes: dict[str, dict[str, Any]] = {}
        self.examinations: dict[str, dict[str, Any]] = {}
        self.reference_definitions: dict[str, dict[str, Any]] = {}
        self.source_events: list[dict[str, Any]] = [
            dict(row) for row in (data.get("source_events") or []) if isinstance(row, Mapping)
        ]
        self._load_collection("concepts", "concept", self.concepts)
        self._load_collection("actions", "action", self.actions)
        self._load_collection("panels", "panel", self.panels)
        self._load_collection("populations", "population", self.populations)
        self._load_collection("conditions", "condition", self.conditions)
        self._load_collection("recommendations", "recommendation", self.recommendations)
        self._load_collection("sources", "source", self.sources, id_key="source_id")
        self._load_collection("spans", "evidence_span", self.spans, id_key="span_id")
        self._load_collection("bindings", "runtime_binding", self.bindings)
        self._load_collection("settings", "care_setting", self.settings)
        self._load_collection("analytes", "lab_analyte", self.analytes)
        self._load_collection("examinations", "examination", self.examinations)
        self._load_collection("reference_definitions", "reference_definition", self.reference_definitions)
        self._build_edges()
        self._validate()
        self.aliases = self._build_aliases()
        self._build_alias_nodes()
        self.adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for edge in self.edges:
            self.adjacency.setdefault(edge["source"], []).append((edge["target"], edge))
            self.adjacency.setdefault(edge["target"], []).append((edge["source"], edge))

    def _load_collection(self, key: str, node_type: str, target: dict[str, dict[str, Any]], *, id_key: str = "id") -> None:
        rows = self.raw.get(key) or []
        if not isinstance(rows, list):
            raise KnowledgeError(f"{key} must be a list")
        for row in rows:
            if not isinstance(row, Mapping):
                raise KnowledgeError(f"{key} entries must be objects")
            node_id = str(row.get(id_key) or "").strip()
            if not node_id:
                raise KnowledgeError(f"{key} entry is missing id")
            if node_id in self.nodes:
                raise KnowledgeError(f"duplicate node id: {node_id}")
            label = str(row.get("label") or node_id)
            aliases = tuple(_strings(row.get("aliases")))
            node = {
                "id": node_id,
                "type": node_type,
                "label": label,
                "aliases": aliases,
                "data": dict(row),
            }
            self.nodes[node_id] = node
            target[node_id] = node["data"]

    def _build_edges(self) -> None:
        def add(source: str, relation: str, target: str) -> None:
            if source not in self.nodes or target not in self.nodes:
                raise KnowledgeError(f"edge references unknown node: {source} -[{relation}]-> {target}")
            self.edges.append({"source": source, "relation": relation, "target": target})

        for rec_id, rec in self.recommendations.items():
            for concept_id in rec.get("concepts") or ():
                add(str(concept_id), "ADDRESSED_BY", rec_id)
            action_id = str(rec.get("action_id") or "")
            if not action_id:
                raise KnowledgeError(f"{rec_id} is missing action_id")
            direction = str(rec.get("direction") or "")
            relation = "RECOMMENDS" if direction in {"support", "consider", "monitor"} else "CONSTRAINS"
            add(rec_id, relation, action_id)
            if rec.get("population_id"):
                add(rec_id, "APPLIES_TO", str(rec["population_id"]))
            for condition_id in rec.get("indications") or ():
                add(rec_id, "REQUIRES", str(condition_id))
            for condition_id in rec.get("exclusions") or ():
                add(rec_id, "EXCLUDES", str(condition_id))
            for panel_id in rec.get("monitoring_panels") or ():
                add(rec_id, "MONITOR_WITH", str(panel_id))
            for span_id in rec.get("evidence_span_ids") or ():
                add(rec_id, "SUPPORTED_BY", str(span_id))
            for other_id in rec.get("conflicts_with") or ():
                add(rec_id, "CONFLICTS_WITH", str(other_id))
            if rec.get("setting_id"):
                add(rec_id, "APPLIES_TO", str(rec["setting_id"]))
            if rec.get("superseded_by"):
                add(rec_id, "SUPERSEDES", str(rec["superseded_by"]))
        for span_id, span in self.spans.items():
            add(span_id, "PART_OF_SOURCE", str(span.get("source_id") or ""))
        for binding_id, binding in self.bindings.items():
            add(str(binding.get("action_id") or ""), "BINDS_TO", binding_id)
        for panel_id, panel in self.panels.items():
            for member in panel.get("members") or ():
                key = str(member.get("key") or "")
                if key and f"analyte:{key}" in self.analytes:
                    add(panel_id, "HAS_ANALYTE", f"analyte:{key}")
        for index, event in enumerate(self.source_events):
            if str(event.get("source_id") or "") not in self.sources:
                raise KnowledgeError(f"source_events[{index}] references an unknown source")
            for field in ("kind", "at", "status"):
                if not str(event.get(field) or "").strip():
                    raise KnowledgeError(f"source_events[{index}] is missing {field}")
        for source_id, source in self.sources.items():
            for old_id in source.get("supersedes") or ():
                add(source_id, "SUPERSEDES", str(old_id))
        for ref_id, ref in self.reference_definitions.items():
            if ref.get("source_id"):
                add(ref_id, "PART_OF_SOURCE", str(ref["source_id"]))

    def _validate(self) -> None:
        for rec_id, rec in self.recommendations.items():
            direction = str(rec.get("direction") or "")
            if direction not in _DIRECTIONS:
                raise KnowledgeError(f"{rec_id} has an invalid direction: {direction}")
            operations = [str(item) for item in rec.get("supported_operations") or ()]
            if not operations or any(item not in _OPERATIONS for item in operations):
                raise KnowledgeError(f"{rec_id} has invalid supported_operations")
            if str(rec.get("action_id") or "") not in self.actions:
                raise KnowledgeError(f"{rec_id} references an unknown action")
            if rec.get("population_id") and str(rec["population_id"]) not in self.populations:
                raise KnowledgeError(f"{rec_id} references an unknown population")
            for field in ("indications", "exclusions", "temporal_conditions"):
                for condition_id in rec.get(field) or ():
                    if str(condition_id) not in self.conditions:
                        raise KnowledgeError(f"{rec_id} references an unknown condition: {condition_id}")
            for panel_id in rec.get("monitoring_panels") or ():
                if str(panel_id) not in self.panels:
                    raise KnowledgeError(f"{rec_id} references an unknown monitoring panel: {panel_id}")
            for span_id in rec.get("evidence_span_ids") or ():
                if str(span_id) not in self.spans:
                    raise KnowledgeError(f"{rec_id} references an unknown evidence span: {span_id}")
            for binding_id in rec.get("runtime_binding_ids") or ():
                binding = self.bindings.get(str(binding_id))
                if binding is None:
                    raise KnowledgeError(f"{rec_id} references an unknown runtime binding: {binding_id}")
                if str(binding.get("action_id") or "") != str(rec.get("action_id") or ""):
                    raise KnowledgeError(f"{rec_id} binding {binding_id} points to a different action")
            for concept_id in rec.get("concepts") or ():
                if str(concept_id) not in self.concepts:
                    raise KnowledgeError(f"{rec_id} references an unknown concept: {concept_id}")
            for other_id in rec.get("conflicts_with") or ():
                if str(other_id) not in self.recommendations:
                    raise KnowledgeError(f"{rec_id} references an unknown conflict: {other_id}")
            for field in ("evidence_grade_original", "recommendation_strength_original",
                          "restriction_kind", "restriction_severity", "care_phase"):
                if field not in rec:
                    raise KnowledgeError(f"{rec_id} is missing {field}")
            if not isinstance(rec.get("unformalized_conditions", []), list):
                raise KnowledgeError(f"{rec_id} unformalized_conditions must be a list")
            if not isinstance(rec.get("review_records", []), list):
                raise KnowledgeError(f"{rec_id} review_records must be a list")
            if rec.get("setting_id") and str(rec["setting_id"]) not in self.settings:
                raise KnowledgeError(f"{rec_id} references an unknown setting: {rec['setting_id']}")
            if rec.get("superseded_by") and str(rec["superseded_by"]) not in self.recommendations:
                raise KnowledgeError(f"{rec_id} superseded_by references an unknown recommendation")
        for binding_id, binding in self.bindings.items():
            if str(binding.get("action_id") or "") not in self.actions:
                raise KnowledgeError(f"{binding_id} references an unknown action")
        for index, event in enumerate(self.source_events):
            if str(event.get("source_id") or "") not in self.sources:
                raise KnowledgeError(f"source_events[{index}] references an unknown source")
            for field in ("kind", "at", "status"):
                if not str(event.get(field) or "").strip():
                    raise KnowledgeError(f"source_events[{index}] is missing {field}")
        for source_id, source in self.sources.items():
            for old_id in source.get("supersedes") or ():
                if str(old_id) not in self.sources:
                    raise KnowledgeError(f"{source_id} supersedes an unknown source: {old_id}")
        for ref_id, ref in self.reference_definitions.items():
            if str(ref.get("source_id") or "") not in self.sources:
                raise KnowledgeError(f"{ref_id} references an unknown source")
        for span_id, span in self.spans.items():
            if str(span.get("source_id") or "") not in self.sources:
                raise KnowledgeError(f"{span_id} references an unknown source")

    def _build_aliases(self) -> dict[str, tuple[str, ...]]:
        index: dict[str, list[str]] = {}
        for node in self.nodes.values():
            if node["type"] not in {"concept", "action", "panel", "care_setting", "lab_analyte", "examination"}:
                continue
            labels = [node["label"], node["id"], *node.get("aliases", ())]
            for label in labels:
                key = _normalize(label)
                if key:
                    index.setdefault(key, [])
                    if node["id"] not in index[key]:
                        index[key].append(node["id"])
        return {key: tuple(values) for key, values in index.items()}

    def _build_alias_nodes(self) -> None:
        """Materialise alias records and HAS_ALIAS edges for schema completeness."""
        for key, node_ids in self.aliases.items():
            for node_id in node_ids:
                alias_id = f"alias:{node_id.split(':', 1)[-1]}:{hashlib.md5(key.encode('utf-8')).hexdigest()[:8]}"
                if alias_id in self.nodes:
                    continue
                self.nodes[alias_id] = {
                    "id": alias_id, "type": "alias", "label": key,
                    "aliases": (), "data": {"alias": key, "target": node_id},
                }
                self.edges.append({"source": node_id, "relation": "HAS_ALIAS", "target": alias_id})

    def resolve_term(self, text: str) -> dict[str, Any]:
        normalized = _normalize(text)
        candidates = list(self.aliases.get(normalized, ()))
        method = "exact"
        if not candidates:
            method = "contained"
            hits: list[tuple[int, str]] = []
            for alias, node_ids in self.aliases.items():
                position = _find(normalized, alias)
                if position >= 0:
                    hits.append((position, alias))
                    for node_id in node_ids:
                        if node_id not in candidates:
                            candidates.append(node_id)
            candidates = [node_id for _position, _alias in sorted(hits) for node_id in self.aliases.get(_alias, ()) if node_id in candidates]
        # Deduplicate while preserving order.
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            status = "unresolved"
        elif len(candidates) == 1:
            status = "resolved"
        else:
            status = "ambiguous"
        needed_context = ""
        if status == "ambiguous":
            needed_context = "候选跨类型或同义；请提供概念类型/期望动作上下文，或由审核别名表消歧。"
        elif status == "unresolved":
            needed_context = "当前 release 无匹配概念/动作/面板；请补充规范词或先检索候选来源。"
        return {
            "input": text,
            "normalized": normalized,
            "status": status,
            "method": method if candidates else "none",
            "needed_context": needed_context,
            "candidates": [
                {
                    "id": node_id,
                    "type": self.nodes[node_id]["type"],
                    "label": self.nodes[node_id]["label"],
                    "standard_codes": dict(self.nodes[node_id].get("standard_codes") or {}),
                }
                for node_id in candidates
            ],
        }

    def resolve_terms(self, terms: Sequence[str]) -> list[dict[str, Any]]:
        return [self.resolve_term(str(term)) for term in terms if str(term or "").strip()]

    def stats(self) -> dict[str, Any]:
        return {
            **self.release.to_dict(),
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "node_types": {
                node_type: sum(1 for node in self.nodes.values() if node["type"] == node_type)
                for node_type in NODE_TYPES
            },
            "recommendations": len(self.recommendations),
            "sources": len(self.sources),
            "evidence_spans": len(self.spans),
            "runtime_bindings": len(self.bindings),
            "care_settings": len(self.settings),
            "lab_analytes": len(self.analytes),
            "examinations": len(self.examinations),
            "reference_definitions": len(self.reference_definitions),
            "source_events": len(self.source_events),
            "retrieval": "canonical term resolution + bounded typed graph traversal + four-state conditions",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "release": self.release.to_dict(),
            "nodes": [
                {"id": node["id"], "type": node["type"], "label": node["label"], **node["data"]}
                for node in self.nodes.values()
            ],
            "edges": list(self.edges),
        }

    def get_evidence(self, span_id: str) -> dict[str, Any]:
        span = self.spans.get(span_id)
        if span is None:
            raise KnowledgeError(f"unknown evidence span: {span_id}")
        source = self.sources.get(str(span.get("source_id") or ""), {})
        return {"span": dict(span), "source": dict(source)}

    def recommendation_record(self, recommendation_id: str) -> dict[str, Any]:
        try:
            return self.recommendations[recommendation_id]
        except KeyError:
            raise KnowledgeError(f"unknown recommendation: {recommendation_id}") from None

    def _content_hash(self, data: Mapping[str, Any]) -> str:
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_knowledge_graph(path: str | Path | None = None) -> KnowledgeGraph:
    target = Path(path) if path else DEFAULT_FIXTURE_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise KnowledgeError(f"knowledge fixture not found: {target}") from None
    except json.JSONDecodeError as exc:
        raise KnowledgeError(f"knowledge fixture is not valid JSON: {exc}") from None
    return KnowledgeGraph(data, source_path=str(target))


_VARIANT_MAP = str.maketrans({
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁻": "-", "⁺": "+", "µ": "u", "μ": "u", "×": "x",
})


def _normalize(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).translate(_VARIANT_MAP).casefold()
    return re.sub(r"\s+", " ", value).strip()


def _find(text: str, alias: str) -> int:
    if not alias:
        return -1
    if re.fullmatch(r"[a-z0-9][a-z0-9 .+\-]*", alias):
        match = re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text)
        return match.start() if match else -1
    return text.find(alias)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
