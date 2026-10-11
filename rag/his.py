"""Read-only HIS snapshot contracts for the Agent/RAG boundary.

This module accepts synthetic or explicitly authorized structured snapshots. It
never writes to a chart and never sends snapshot text to external connectors.

Layering (``contracts/his_rag.md``): HIS -> Agent -> RAG. This package never
connects to HIS; the Agent reads HIS and passes the structured snapshot in
(dict or :class:`PatientSnapshot`). Any HIS client -- including the reference
implementation in ``contracts/agent_his_adapter.py`` -- lives on the caller side.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Protocol, Sequence

from .models import parse_iso_date


@dataclass(frozen=True)
class PatientFact:
    collection: str
    event_type: str
    record_path: str
    field_path: str
    fields: Mapping[str, Any] = field(default_factory=dict)
    time: str | None = None
    start: str | None = None
    end: str | None = None
    source_type: str = "patient_record"
    extra: Mapping[str, Any] = field(default_factory=dict)

    def effective_dates(self) -> tuple[date | None, date | None]:
        start = parse_iso_date(self.start or self.time)
        end = parse_iso_date(self.end or self.time)
        return start, end

    def searchable_text(self) -> str:
        values = [self.collection, self.event_type]
        values.extend(str(value) for key, value in self.fields.items() if key not in {"text", "free_text", "narrative"})
        return " ".join(values)


@dataclass(frozen=True)
class PatientSnapshot:
    patient_ref: str
    as_of: str
    facts: tuple[PatientFact, ...] = ()
    reviewed_external_terms: tuple[str, ...] = ()
    suppressed_future: int = 0
    warnings: tuple[str, ...] = ()
    source: str = "synthetic_chart"
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def context_id(self) -> str:
        return f"{self.source}:{self.patient_ref}:{self.as_of}"

    def visible_facts(self) -> tuple[PatientFact, ...]:
        cutoff = parse_iso_date(self.as_of)
        if cutoff is None:
            return self.facts
        visible: list[PatientFact] = []
        for fact in self.facts:
            start, end = fact.effective_dates()
            if start is not None and start > cutoff:
                continue
            if fact.start and fact.end is None and start is not None and start <= cutoff:
                visible.append(fact)
                continue
            if end is not None and end > cutoff:
                visible.append(fact)
                continue
            visible.append(fact)
        return tuple(visible)


class HISAdapter(Protocol):
    read_only: bool
    source: str

    def get_snapshot(self, patient_ref: str, as_of: str) -> PatientSnapshot:
        """Return a time-bounded snapshot without write operations."""


@dataclass
class InMemoryHISAdapter:
    snapshots: Mapping[str, PatientSnapshot]
    source: str = "synthetic_chart"
    read_only: bool = True

    def get_snapshot(self, patient_ref: str, as_of: str) -> PatientSnapshot:
        try:
            original = self.snapshots[patient_ref]
        except KeyError:
            raise ValueError("unknown patient_ref") from None
        if original.as_of == as_of:
            return filter_snapshot(original, as_of)
        return filter_snapshot(original, as_of)


def filter_snapshot(snapshot: PatientSnapshot, as_of: str) -> PatientSnapshot:
    cutoff = parse_iso_date(as_of)
    if cutoff is None:
        raise ValueError("as_of must be an ISO date")
    visible: list[PatientFact] = []
    suppressed = snapshot.suppressed_future
    warnings = list(snapshot.warnings)
    for fact in snapshot.facts:
        start, end = fact.effective_dates()
        if start is not None and start > cutoff:
            suppressed += 1
            continue
        if fact.time is None and fact.start is None and fact.end is None:
            warnings.append(f"患者事实没有可解析时间，保留但不参与时间窗口：{fact.record_path}。")
        visible.append(fact)
    return PatientSnapshot(
        patient_ref=snapshot.patient_ref,
        as_of=as_of,
        facts=tuple(visible),
        reviewed_external_terms=snapshot.reviewed_external_terms,
        suppressed_future=suppressed,
        warnings=tuple(dict.fromkeys(warnings)),
        source=snapshot.source,
        extra=snapshot.extra,
    )


def snapshot_from_dict(raw: Mapping[str, Any], *, default_source: str = "synthetic_chart") -> PatientSnapshot:
    wrapper = raw.get("patient_snapshot") if isinstance(raw.get("patient_snapshot"), Mapping) else raw
    patient_ref = str(wrapper.get("patient_ref") or wrapper.get("patient_id") or "").strip()
    as_of = str(wrapper.get("as_of") or "").strip()
    if not patient_ref or not as_of:
        raise ValueError("patient_snapshot requires patient_ref and as_of")
    if parse_iso_date(as_of) is None:
        raise ValueError("patient_snapshot.as_of must be an ISO date")
    facts: list[PatientFact] = []
    for index, item in enumerate(wrapper.get("facts") or wrapper.get("records") or ()):
        if not isinstance(item, Mapping):
            raise ValueError("patient facts must be JSON objects")
        collection = str(item.get("collection") or "unknown").strip()
        event_type = str(item.get("event_type") or collection).strip()
        record_path = str(item.get("record_path") or f"{collection}[{index}]")
        field_path = str(item.get("field_path") or record_path)
        known = {"collection", "event_type", "record_path", "field_path", "fields", "time", "start", "end", "source_type", "extra"}
        fields = dict(item.get("fields") or {})
        fields.update({key: value for key, value in item.items() if key not in known})
        facts.append(PatientFact(
            collection=collection, event_type=event_type, record_path=record_path,
            field_path=field_path, fields=fields, time=_optional(item.get("time")),
            start=_optional(item.get("start")), end=_optional(item.get("end")),
            source_type=str(item.get("source_type") or "patient_record"), extra=dict(item.get("extra") or {}),
        ))
    return filter_snapshot(PatientSnapshot(
        patient_ref=patient_ref, as_of=as_of, facts=tuple(facts),
        reviewed_external_terms=tuple(_strings(wrapper.get("reviewed_external_terms"))),
        source=str(wrapper.get("source") or default_source), extra=dict(wrapper.get("extra") or {}),
    ), as_of)


def minimize_patient_snapshot(snapshot: PatientSnapshot) -> tuple[str, ...]:
    """Return only caller-reviewed external terms; never infer from raw fields."""
    return tuple(dict.fromkeys(term for term in snapshot.reviewed_external_terms if term.strip()))


def _optional(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, Sequence):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
