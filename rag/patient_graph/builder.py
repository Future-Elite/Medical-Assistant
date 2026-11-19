"""Build a patient knowledge graph from one visible HIS snapshot.

Every record node is one :class:`~rag.his.PatientRecord` that already passed the
``as_of`` visibility filter.  The builder never re-reads HIS, never adds a fact
that is not in the snapshot, and never turns a missing field into a normal
value.  The snapshot hash covers the visible facts only, so the same snapshot
and the same graph builder produce the same graph and the same hash.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from ..core.his import PatientRecord, PatientSnapshot
from ..core.models import parse_iso_date
from .lexicon import PatientRecordLexicon, default_lexicon
from .model import GraphEdge, GraphNode, PatientGraph

_META_FIELDS = {
    "subject_role",
    "data_status",
    "derivation",
    "source_paths",
    "source_path",
    "time_precision",
    "ga_precision",
    "mapping_status",
    "source_file",
}


def build_patient_graph(
    snapshot: PatientSnapshot,
    *,
    lexicon: PatientRecordLexicon | None = None,
) -> PatientGraph:
    """Build an immutable graph for ``snapshot``.

    Narrative values stay in :class:`~rag.his.PatientRecord`; the graph stores
    field names and provenance, not the raw narrative.  Retrieval renders a
    short evidence string from the fact when a hit is returned.
    """
    lex = lexicon or default_lexicon()
    facts = tuple(snapshot.visible_records())
    warnings: list[str] = []
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    root_id = f"patient:{_short_ref(snapshot.patient_ref)}"
    episode_id = f"episode:{snapshot.as_of}"
    nodes[root_id] = GraphNode(
        root_id, "patient", "患者快照",
        {"patient_ref": snapshot.patient_ref, "as_of": snapshot.as_of, "source": snapshot.source},
    )
    nodes[episode_id] = GraphNode(episode_id, "episode", f"决策时点 {snapshot.as_of}", {"as_of": snapshot.as_of})

    record_rows: list[tuple[str, PatientRecord, dict[str, Any]]] = []
    missing_source: set[str] = set()
    no_time: list[str] = []
    for fact in facts:
        record_id = f"record:{fact.record_id}"
        if record_id in nodes:
            warnings.append(f"重复 record_id 被合并：{fact.record_id}")
            continue
        status = _record_status(fact)
        subject = _subject(fact)
        time_value = _record_time(fact)
        node = GraphNode(
            record_id,
            "record",
            _record_label(fact),
            {
                "record_id": fact.record_id,
                "record_type": fact.record_type,
                "event_type": fact.event_type,
                "event_time": fact.event_time,
                "reported_time": fact.reported_time,
                "start": fact.start,
                "end": fact.end,
                "record_status": fact.status,
                "source_system": fact.source_system,
                "code": _json_safe(dict(fact.code)),
                "value": _json_safe(fact.value),
                "unit": fact.unit,
                "reference_range": fact.reference_range,
                "provenance": _json_safe(dict(fact.provenance)),
                # Stable locator aliases used by the retrieval/evidence API.
                "record_path": fact.record_id,
                "field_path": str(fact.provenance.get("field_path") or fact.record_id),
                "collection": fact.record_type,
                "time": fact.event_time,
                "time_precision": _time_precision(fact),
                "status": status,
                "subject": subject,
                "field_names": sorted(str(key) for key in fact.fields),
                "field_presence": {
                    str(key): _present(value) for key, value in fact.fields.items()
                },
                "search_tokens": sorted(_search_tokens(fact)),
                "source_type": fact.source_system,
            },
        )
        nodes[record_id] = node
        record_rows.append((record_id, fact, {"status": status, "subject": subject, "time": time_value}))
        edges.append(GraphEdge(root_id, record_id, "HAS_RECORD", {"status": status, "subject": subject}))
        subject_id = f"subject:{subject}"
        nodes.setdefault(subject_id, GraphNode(subject_id, "subject", subject, {"subject": subject}))
        edges.append(GraphEdge(record_id, subject_id, "HAS_SUBJECT", {"subject": subject}))
        if time_value is not None:
            time_id = f"time:{time_value.isoformat()}"
            nodes.setdefault(time_id, GraphNode(time_id, "time", time_value.isoformat(), {"date": time_value.isoformat()}))
            edges.append(GraphEdge(record_id, time_id, "RECORDED_AT", {"date": time_value.isoformat()}))
            edges.append(GraphEdge(record_id, episode_id, "OCCURS_IN", {"as_of": snapshot.as_of}))
        else:
            no_time.append(fact.record_id)
        concepts = lex.concepts_for_record(fact.record_type, fact.fields)
        for concept_id, provenance in sorted(concepts.items()):
            concept_node_id = f"concept:{concept_id.removeprefix('concept:')}"
            spec = lex.concept(concept_id)
            nodes.setdefault(concept_node_id, GraphNode(
                concept_node_id,
                "concept",
                spec.label if spec else concept_id,
                {"concept_id": concept_id, "kind": spec.kind if spec else "fact", "label": spec.label if spec else concept_id},
            ))
            edges.append(GraphEdge(record_id, concept_node_id, "MENTIONS", dict(provenance)))

    # Provenance edges: derived_* facts point at the raw records they summarise.
    known_paths = {fact.record_id for fact in facts}
    for record_id, fact, _meta in record_rows:
        for source_path in _source_paths(fact):
            if source_path not in known_paths:
                missing_source.add(source_path)
                continue
            target_id = f"record:{source_path}"
            if target_id != record_id:
                edges.append(GraphEdge(record_id, target_id, "DERIVED_FROM", {"source_path": source_path}))

    # Chronological chain.  Only dated records participate; undated records are
    # kept as nodes and reported, not assigned a default date.
    dated = [row for row in record_rows if row[2]["time"] is not None]
    dated.sort(key=lambda row: (row[2]["time"], row[0]))
    for (_left_id, left, _left_meta), (_right_id, right, _right_meta) in zip(dated, dated[1:]):
        left_date = _left_meta_date(left)
        right_date = _left_meta_date(right)
        attributes: dict[str, Any] = {}
        if left_date and right_date:
            attributes["days_apart"] = (right_date - left_date).days
        edges.append(GraphEdge(f"record:{left.record_id}", f"record:{right.record_id}", "TEMPORALLY_BEFORE", attributes))
    if no_time:
        warnings.append(f"{len(no_time)} 条可见事实缺少可解析时间，未进入时间链。")
    if missing_source:
        warnings.append(f"{len(missing_source)} 个派生来源记录不在当前可见快照中（未建边）。")
    if not facts:
        warnings.append("当前快照没有可见事实，患者图谱为空。")

    # Deterministic order: nodes by id, edges by (source, relation, target).
    ordered_nodes = tuple(nodes[node_id] for node_id in sorted(nodes))
    ordered_edges = tuple(sorted(edges, key=lambda edge: (edge.source, edge.relation, edge.target)))
    return PatientGraph(
        patient_ref=snapshot.patient_ref,
        as_of=snapshot.as_of,
        snapshot_sha256=_snapshot_hash(snapshot, facts),
        nodes=ordered_nodes,
        edges=ordered_edges,
        warnings=tuple(dict.fromkeys((*snapshot.warnings, *warnings))),
        fact_count=len(facts),
        source=snapshot.source,
        records=facts,
    )


def fact_evidence_text(fact: PatientRecord) -> str:
    """Short, source-preserving evidence line for one fact.

    Free-text/narrative fields are excluded from the graph hit text; callers
    that need the original narrative must read the HIS record through their own
    authorization, not through the retrieval evidence string.
    """
    parts = [fact.record_type, fact.event_type]
    code = fact.code if isinstance(fact.code, Mapping) else {}
    for value in (code.get("display"), code.get("code"), fact.value, fact.unit):
        if value not in (None, "", [], {}):
            parts.append(str(value))
    for key, value in fact.fields.items():
        if str(key).lower() in _NARRATIVE_KEYS:
            continue
        if isinstance(value, str) and len(value) > 160:
            continue
        if value in (None, "", [], {}):
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def _record_label(fact: PatientRecord) -> str:
    return f"{fact.record_type}/{fact.event_type} ({fact.record_id})"


def _short_ref(patient_ref: str) -> str:
    return patient_ref if len(patient_ref) <= 64 else hashlib.sha256(patient_ref.encode("utf-8")).hexdigest()[:16]


def _record_status(fact: PatientRecord) -> str:
    """Return the observation status used by retrieval.

    ``fields.data_status`` is the historical HIS-facing label and keeps
    precedence when present.  The record ``status`` is the fallback so an
    EHR record without ``data_status`` cannot be silently treated as positive.
    """
    raw = str(fact.fields.get("data_status") or "").strip()
    if raw in {"observed_positive", "observed_negative", "uncertain", "not_recorded", "pending"}:
        return raw
    record_status = str(fact.status or "").strip().lower()
    if record_status in {"pending", "not_recorded", "entered_in_error"}:
        return record_status
    if record_status == "preliminary":
        return "uncertain"
    for key, value in fact.fields.items():
        if str(key).endswith("_status") and str(value).strip() in {"pending"}:
            return "pending"
    values = [
        value for key, value in fact.fields.items()
        if str(key).lower() not in _META_FIELDS and not str(key).endswith("_status")
    ]
    if fact.value is not None:
        values.append(fact.value)
    return "observed_positive" if any(_present(value) for value in values) else "not_recorded"


def _subject(fact: PatientRecord) -> str:
    raw = str(fact.fields.get("subject_role") or fact.subject or "").strip().lower()
    if raw in {"mother", "maternal"}:
        return "mother"
    if raw in {"patient", "infant", "newborn", "neonate"}:
        return "patient"
    if fact.record_type == "maternal":
        return "mother"
    return "patient"


def _record_time(fact: PatientRecord) -> date | None:
    return parse_iso_date(fact.start or fact.event_time)


def _time_precision(fact: PatientRecord) -> str | None:
    raw = str(fact.fields.get("time_precision") or fact.fields.get("ga_precision") or "").strip()
    if raw:
        return raw
    if fact.start and fact.end:
        return "interval"
    if fact.event_time and "T" in fact.event_time:
        return "datetime"
    if fact.event_time:
        return "date"
    return None


def _source_paths(fact: PatientRecord) -> tuple[str, ...]:
    raw = fact.fields.get("source_paths") or fact.fields.get("source_path") or ()
    if isinstance(raw, str):
        return (raw,) if raw else ()
    if isinstance(raw, Iterable):
        return tuple(str(item) for item in raw if str(item))
    return ()


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        text = value.strip().lower()
        if not text or text in {"none", "null", "nan", "无"}:
            return False
        return not any(
            marker in text
            for marker in ("无提及", "未提及", "未记录", "无记录", "not recorded", "no record")
        )
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _left_meta_date(fact: PatientRecord) -> date | None:
    return parse_iso_date(fact.start or fact.event_time)


def _snapshot_hash(snapshot: PatientSnapshot, facts: Iterable[PatientRecord]) -> str:
    payload = {
        "patient_ref": snapshot.patient_ref,
        "as_of": snapshot.as_of,
        "source": snapshot.source,
        "records": [
            {
                "record_id": fact.record_id,
                "record_type": fact.record_type,
                "event_type": fact.event_type,
                "patient_ref": fact.patient_ref,
                "encounter_id": fact.encounter_id,
                "subject": fact.subject,
                "source_system": fact.source_system,
                "event_time": fact.event_time,
                "reported_time": fact.reported_time,
                "status": fact.status,
                "code": _json_safe(dict(fact.code)),
                "value": _json_safe(fact.value),
                "unit": fact.unit,
                "reference_range": fact.reference_range,
                "start": fact.start,
                "end": fact.end,
                "fields": {str(key): _json_safe(value) for key, value in sorted(fact.fields.items())},
                "provenance": _json_safe(dict(fact.provenance)),
                "extra": _json_safe(dict(fact.extra)),
            }
            for fact in facts
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _terms(text: str) -> set[str]:
    """ASCII tokens plus CJK bigrams; same tokenizer as the legacy path."""
    ascii_terms = set(re.findall(r"[a-z0-9]+(?:[-./][a-z0-9]+)*", str(text).lower()))
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", str(text)))
    ascii_terms.update(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
    return ascii_terms


_NARRATIVE_KEYS = {
    "text", "free_text", "narrative", "note", "history",
    "presentingsymptoms", "laboratoryfindings", "specialexaminations",
    "dischargecondition", "dischargeorders", "dischargeguidance",
    "treatmentcourse", "complications",
}


def _search_tokens(fact: PatientRecord) -> set[str]:
    """Lexical tokens for fallback retrieval.

    Narrative/long fields are excluded on purpose: they are not needed to
    locate a structured record, and keeping their tokens in the graph payload
    would echo chart text for no retrieval benefit.  Concept matching still
    scans those values when building ``MENTIONS`` edges; only the token copy is
    withheld.
    """
    parts = [fact.record_type, fact.event_type]
    code = fact.code if isinstance(fact.code, Mapping) else {}
    for value in (code.get("display"), code.get("code"), fact.value, fact.unit):
        if value not in (None, "", [], {}):
            parts.append(str(value))
    for key, value in fact.fields.items():
        if str(key).lower() in _NARRATIVE_KEYS:
            continue
        if isinstance(value, str) and len(value) > 160:
            continue
        if value in (None, "", [], {}):
            continue
        parts.append(f"{key}={value}")
    return _terms(" ".join(parts))
