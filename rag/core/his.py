"""Read-only patient-record contracts for the Agent/RAG boundary.

The contract is intentionally aligned with EHR/HIS record semantics rather
than a generic "fact" bag:

* identity: ``record_id``, ``patient_ref``, ``encounter_id``;
* type/coding: ``record_type``, ``event_type``, ``code``;
* time: ``event_time``, ``reported_time``, ``start``/``end``;
* status/provenance: ``status``, ``source_system``, ``subject``, ``provenance``;
* payload: heterogeneous ``fields`` for the record type.

``PatientSnapshot`` is a time-bounded, read-only projection.  Visibility at
``as_of`` requires both the event time and the reporting time to be no later
than the cutoff, so a result collected earlier but reported later is not
prematurely visible.  It never writes to a chart and never sends snapshot text
to external connectors.  The HIS/EHR adapters live on the caller side; this
package only consumes the contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timezone
from typing import Any, Mapping, Protocol, Sequence

from .models import parse_iso_date, parse_iso_datetime

PATIENT_SNAPSHOT_SCHEMA = "patient-snapshot"
RECORD_STATUSES = {
    "final", "preliminary", "amended", "pending", "not_recorded",
    "entered_in_error", "unknown",
}
_DATA_STATUS_TO_RECORD_STATUS = {
    "observed_positive": "final",
    "observed_negative": "final",
    "pending": "pending",
    "not_recorded": "not_recorded",
    "uncertain": "preliminary",
}


@dataclass(frozen=True)
class PatientRecord:
    """One EHR/HIS record with identity, time, status and provenance."""

    record_id: str
    record_type: str
    event_type: str = ""
    fields: Mapping[str, Any] = field(default_factory=dict)
    patient_ref: str = ""
    encounter_id: str | None = None
    subject: str = "patient"
    source_system: str = "his"
    event_time: str | None = None
    reported_time: str | None = None
    status: str = "unknown"
    code: Mapping[str, Any] = field(default_factory=dict)
    value: Any = None
    unit: str | None = None
    reference_range: str | None = None
    start: str | None = None
    end: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    extra: Mapping[str, Any] = field(default_factory=dict)

    def effective_dates(self) -> tuple[date | None, date | None]:
        start = parse_iso_date(self.start or self.event_time)
        end = parse_iso_date(self.end or self.event_time)
        return start, end

    def effective_datetimes(self) -> tuple[datetime | None, datetime | None]:
        start = parse_iso_datetime(self.start or self.event_time)
        end = parse_iso_datetime(self.end or self.event_time)
        return start, end

    def searchable_text(self) -> str:
        values = [self.record_type, self.event_type, str(self.code.get("display") or "")]
        values.extend(str(value) for key, value in self.fields.items()
                      if key not in {"text", "free_text", "narrative"})
        return " ".join(item for item in values if item)

    @classmethod
    def from_mapping(
        cls,
        item: Mapping[str, Any],
        *,
        patient_ref: str = "",
        encounter_id: str | None = None,
        index: int = 0,
    ) -> "PatientRecord":
        if not isinstance(item, Mapping):
            raise ValueError("patient records must be JSON objects")
        fields = dict(item.get("fields") or item.get("payload") or {})
        record_type = str(item.get("record_type") or item.get("collection") or "unknown").strip()
        record_id = str(item.get("record_id") or item.get("record_path") or f"{record_type}[{index}]").strip()
        event_type = str(item.get("event_type") or record_type).strip()
        status = str(item.get("status") or "").strip()
        if status not in RECORD_STATUSES:
            status = _DATA_STATUS_TO_RECORD_STATUS.get(
                str(fields.get("data_status") or "").strip(), "unknown"
            )
        code = item.get("code")
        if not isinstance(code, Mapping):
            code = {"system": "local", "code": event_type, "display": event_type}
        value = item.get("value")
        if value is None:
            value = fields.get("value")
        provenance = item.get("provenance")
        if not isinstance(provenance, Mapping):
            provenance = {"record_path": record_id, "source_type": str(item.get("source_type") or "patient_record")}
        return cls(
            record_id=record_id,
            record_type=record_type,
            event_type=event_type,
            fields=fields,
            patient_ref=str(item.get("patient_ref") or patient_ref),
            encounter_id=str(item.get("encounter_id") or encounter_id or "") or None,
            subject=str(item.get("subject") or fields.get("subject_role") or "patient"),
            source_system=str(item.get("source_system") or item.get("source_type") or "his"),
            event_time=_optional(item.get("event_time") or item.get("time")),
            reported_time=_optional(item.get("reported_time")),
            status=status,
            code=dict(code),
            value=value,
            unit=_optional(item.get("unit") or fields.get("unit")),
            reference_range=_optional(item.get("reference_range") or fields.get("reference_range")),
            start=_optional(item.get("start") or fields.get("start")),
            end=_optional(item.get("end") or fields.get("end")),
            provenance=dict(provenance),
            extra=dict(item.get("extra") or {}),
        )


@dataclass(frozen=True)
class PatientSnapshot:
    patient_ref: str
    as_of: str
    records: tuple[PatientRecord, ...] = ()
    encounter_id: str | None = None
    source_system: str = "his"
    schema_version: str = PATIENT_SNAPSHOT_SCHEMA
    reviewed_external_terms: tuple[str, ...] = ()
    suppressed_future: int = 0
    warnings: tuple[str, ...] = ()
    source: str = "synthetic_chart"
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def context_id(self) -> str:
        return f"{self.source}:{self.patient_ref}:{self.as_of}"

    def visible_records(self) -> tuple[PatientRecord, ...]:
        cutoff = _naive(_cutoff_datetime(self.as_of))
        if cutoff is None:
            return self.records
        cutoff_date = _date_cutoff(self.as_of)
        visible: list[PatientRecord] = []
        for record in self.records:
            start, _end = record.effective_datetimes()
            reported = parse_iso_datetime(record.reported_time)
            # An event that has not occurred, or a result not yet reported, is
            # not visible at ``as_of``.  Both checks matter: EHR records often
            # carry a collection time and a later reporting time.
            if _is_future(start, cutoff, cutoff_date):
                continue
            if _is_future(reported, cutoff, cutoff_date):
                continue
            visible.append(record)
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
        return filter_snapshot(original, as_of)


def _cutoff_datetime(value: str) -> datetime | None:
    """Date-only as_of means end of that day; datetime as_of is exact."""
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return None
    if len(str(value).strip()) <= 10:
        return datetime.combine(parsed.date(), dt_time.max)
    return parsed


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _date_cutoff(value: str) -> date | None:
    """Date-only as_of compares local record dates, not UTC instants."""
    return parse_iso_date(value) if len(str(value).strip()) <= 10 else None


def _is_future(value: datetime | None, cutoff: datetime, cutoff_date: date | None) -> bool:
    if value is None:
        return False
    if cutoff_date is not None:
        return value.date() > cutoff_date
    return _naive(value) > cutoff


def filter_snapshot(snapshot: PatientSnapshot, as_of: str) -> PatientSnapshot:
    cutoff = _naive(_cutoff_datetime(as_of))
    if cutoff is None:
        raise ValueError("as_of must be an ISO date/datetime")
    cutoff_date = _date_cutoff(as_of)
    visible: list[PatientRecord] = []
    suppressed = snapshot.suppressed_future
    warnings = list(snapshot.warnings)
    for record in snapshot.records:
        start, _end = record.effective_datetimes()
        reported = parse_iso_datetime(record.reported_time)
        if _is_future(start, cutoff, cutoff_date):
            suppressed += 1
            continue
        if _is_future(reported, cutoff, cutoff_date):
            suppressed += 1
            continue
        if (record.event_time is None and record.start is None and record.end is None
                and record.reported_time is None):
            warnings.append(f"患者记录没有可解析时间，保留但不参与时间窗口：{record.record_id}。")
        visible.append(record)
    return PatientSnapshot(
        patient_ref=snapshot.patient_ref,
        as_of=as_of,
        records=tuple(visible),
        encounter_id=snapshot.encounter_id,
        source_system=snapshot.source_system,
        schema_version=snapshot.schema_version,
        reviewed_external_terms=snapshot.reviewed_external_terms,
        suppressed_future=suppressed,
        warnings=tuple(dict.fromkeys(warnings)),
        source=snapshot.source,
        extra=snapshot.extra,
    )


def snapshot_from_dict(raw: Mapping[str, Any], *, default_source: str = "synthetic_chart") -> PatientSnapshot:
    """Parse a strict ``patient-snapshot`` mapping.

    The contract uses ``records`` (not ``facts``); legacy snapshots must be
    migrated by the caller's import layer.  Records may carry HIS-style
    ``collection/record_path/fields/time`` keys, which are normalized into the
    record fields by :meth:`PatientRecord.from_mapping`.
    """
    wrapper = raw.get("patient_snapshot") if isinstance(raw.get("patient_snapshot"), Mapping) else raw
    patient_ref = str(wrapper.get("patient_ref") or wrapper.get("patient_id") or "").strip()
    as_of = str(wrapper.get("as_of") or "").strip()
    if not patient_ref or not as_of:
        raise ValueError("patient_snapshot requires patient_ref and as_of")
    if parse_iso_date(as_of) is None:
        raise ValueError("patient_snapshot.as_of must be an ISO date/datetime")
    schema = str(wrapper.get("schema_version") or PATIENT_SNAPSHOT_SCHEMA).strip()
    if schema != PATIENT_SNAPSHOT_SCHEMA:
        raise ValueError(f"unsupported patient snapshot schema: {schema}")
    rows = wrapper.get("records")
    if rows is None:
        raise ValueError("patient-snapshot requires a records array")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("patient_snapshot.records must be an array")
    encounter_id = str(wrapper.get("encounter_id") or "").strip() or None
    records = tuple(
        PatientRecord.from_mapping(item, patient_ref=patient_ref, encounter_id=encounter_id, index=index)
        for index, item in enumerate(rows)
    )
    return filter_snapshot(PatientSnapshot(
        patient_ref=patient_ref,
        as_of=as_of,
        records=records,
        encounter_id=encounter_id,
        source_system=str(wrapper.get("source_system") or "his"),
        schema_version=schema,
        reviewed_external_terms=tuple(_strings(wrapper.get("reviewed_external_terms"))),
        source=str(wrapper.get("source") or default_source),
        extra=dict(wrapper.get("extra") or {}),
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
