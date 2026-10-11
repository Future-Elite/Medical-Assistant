"""Read-only HTTP adapter for the HIS x RAG snapshot contract.

Implements the :class:`~rag.his.HISAdapter` protocol over the HIS demo system's
read-only REST endpoints; the field-level mapping is specified in
``contracts/his_rag.md`` at the repository root.

Endpoints used (all ``GET``):

- ``GET /api/patients/{id}``                       -- patient record;
- ``GET /api/patients/{id}/timeline``              -- visit/diagnosis/prescription/
  outpatient lab/admission/discharge/charge/dispense events;
- ``GET /api/admissions/{id}/timeline``            -- per-admission orders, vital
  signs and admission-linked labs (the patient feed does not carry those).

Safety posture (same as the rest of this package):

- **read-only**: the transport seam only ever receives a URL and a timeout, so
  this module cannot issue a write request even by accident;
- **identifiers never enter a snapshot**: direct identifiers (``name`` / ``idCard``
  / ``phone`` / ``address`` / ``patientId``) are dropped while mapping, and the
  patient fact keeps only ``gender`` / ``birthDate``;
- **no rewriting**: clinical fields pass through unchanged -- nothing is translated,
  inferred or defaulted, and missing values stay missing;
- **single time implementation**: ``as_of`` filtering is delegated to
  :func:`rag.his.filter_snapshot`, never re-implemented here;
- **auditable gaps**: events without a payload or without a time are kept and
  surfaced through snapshot warnings instead of being dropped silently.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .his import PatientFact, PatientSnapshot, filter_snapshot

DEFAULT_HIS_BASE_URL = "http://127.0.0.1:8080"
HIS_SOURCE = "his_demo"

# Direct identifiers (and the patient key, which the snapshot already carries as
# ``patient_ref``) must never reach an evidence package or an external connector.
IDENTIFIER_FIELDS = frozenset({"name", "idCard", "phone", "address", "patientId"})

# The patient record itself is mostly identifiers and bookkeeping; only these two
# fields are patient facts worth carrying. ``createdAt`` is not copied as a field
# but serves as the fact's time bound when present.
PATIENT_RECORD_KEEP = ("gender", "birthDate")

# Range events: (start field, end field) inside the entity payload.
RANGE_FIELDS = {
    "ORDER": ("startTime", "stopTime"),
    "ADMISSION": ("admissionTime", "dischargeTime"),
}

# Event types whose payload carries the admission id used to pull the richer
# admission-scoped timeline.
ADMISSION_EVENT_TYPES = ("ADMISSION", "DISCHARGE")

Transport = Callable[[str, float], Mapping[str, Any]]

# The HIS demo runs on localhost; a stray HTTP_PROXY in the environment must not
# intercept these calls (same lesson as the PubMed connector).
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class HISHttpError(RuntimeError):
    """Transport or envelope failure, carrying where it happened."""

    def __init__(self, message: str, *, stage: str, status_code: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.stage = stage
        self.status_code = status_code
        self.retryable = retryable


def default_transport(url: str, timeout: float) -> Mapping[str, Any]:
    """GET ``url`` and decode a JSON object (environment proxies disabled)."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with _NO_PROXY_OPENER.open(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        raise HISHttpError(
            f"HIS returned HTTP {error.code} for {url}",
            stage="http",
            status_code=error.code,
            retryable=error.code >= 500,
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise HISHttpError(f"cannot reach HIS at {url}: {error}", stage="http", retryable=True) from None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HISHttpError(f"HIS returned a non-JSON body for {url}", stage="decode") from None
    if not isinstance(payload, Mapping):
        raise HISHttpError(f"HIS returned JSON that is not an object for {url}", stage="decode")
    return payload


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _by_time(events: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    """Stable sort by event time; events without a time keep their relative order last."""
    return tuple(sorted(events, key=lambda event: (event.get("time") is None, str(event.get("time") or ""))))


def _admission_ids(events: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Unique admission ids referenced by admission/discharge events, in feed order."""
    seen: list[str] = []
    for event in events:
        if not str(event.get("type") or "").strip().upper() in ADMISSION_EVENT_TYPES:
            continue
        data = event.get("data")
        if not isinstance(data, Mapping):
            continue
        admission_id = _text(data.get("id"))
        if admission_id and admission_id not in seen:
            seen.append(admission_id)
    return tuple(seen)


@dataclass(frozen=True)
class HisHttpAdapter:
    """``HISAdapter`` over the HIS demo REST endpoints (read-only).

    ``transport`` is the test/tuning seam -- a callable ``(url, timeout) -> JSON
    object``. The default uses ``urllib`` with environment proxies disabled.

    A snapshot carries no caller terms: ``reviewed_external_terms`` stays empty
    because only the caller may decide which minimal terms leave the system (see
    ``rag.his.minimize_patient_snapshot``); attach them with
    ``dataclasses.replace(snapshot, reviewed_external_terms=(...))``.
    """

    base_url: str = DEFAULT_HIS_BASE_URL
    source: str = HIS_SOURCE
    timeout_seconds: float = 30.0
    transport: Transport | None = None
    include_admission_timelines: bool = True
    read_only: bool = True

    # -- public API ----------------------------------------------------------

    def get_snapshot(self, patient_ref: str, as_of: str) -> PatientSnapshot:
        ref = str(patient_ref).strip()
        if not ref:
            raise ValueError("patient_ref must be a non-empty string")
        patient = self._get(f"/api/patients/{urllib.parse.quote(ref, safe='')}", stage="patient")
        if not isinstance(patient, Mapping) or not patient:
            raise ValueError(f"unknown patient_ref: {ref}")
        warnings: list[str] = []
        events = list(self._timeline(f"/api/patients/{urllib.parse.quote(ref, safe='')}/timeline", stage="timeline", warnings=warnings))
        if self.include_admission_timelines:
            for admission_id in _admission_ids(events):
                events.extend(self._admission_timeline(admission_id, ref, warnings))
        counters: dict[str, int] = {}
        facts: list[PatientFact] = [self._patient_fact(patient)]
        for event in _by_time(events):
            facts.append(self._event_fact(event, counters, warnings))
        snapshot = PatientSnapshot(
            patient_ref=ref,
            as_of=str(as_of),
            facts=tuple(facts),
            source=self.source,
            warnings=tuple(warnings),
        )
        return filter_snapshot(snapshot, as_of)

    # -- HTTP ----------------------------------------------------------------

    def _get(self, path: str, *, stage: str) -> Any:
        url = self.base_url.rstrip("/") + path
        transport = self.transport or default_transport
        payload = transport(url, self.timeout_seconds)
        if not isinstance(payload, Mapping):
            raise HISHttpError(f"HIS payload for {path} must be a JSON object", stage=stage)
        if "code" not in payload:
            raise HISHttpError(f"HIS payload for {path} is missing the {{code,message,data}} envelope", stage=stage)
        if payload.get("code") != 0:
            raise HISHttpError(
                f"HIS error code={payload.get('code')} for {path}: {payload.get('message')}", stage=stage
            )
        return payload.get("data")

    def _timeline(self, path: str, *, stage: str, warnings: list[str]) -> tuple[Mapping[str, Any], ...]:
        data = self._get(path, stage=stage)
        return _events_of(data, stage=stage, warnings=warnings, label=path)

    def _admission_timeline(self, admission_id: str, patient_ref: str, warnings: list[str]) -> tuple[Mapping[str, Any], ...]:
        path = f"/api/admissions/{urllib.parse.quote(admission_id, safe='')}/timeline"
        data = self._get(path, stage="admission_timeline")
        owner = _text(data.get("patientId")) if isinstance(data, Mapping) else None
        if owner is not None and owner != patient_ref:
            warnings.append(
                f"HIS 住院时间线（admission {admission_id}）返回了患者 {owner} 的数据，与本快照患者 {patient_ref} 不一致，已忽略。"
            )
            return ()
        return _events_of(data, stage="admission_timeline", warnings=warnings, label=path)

    # -- mapping (contracts/his_rag.md §3) -----------------------------------

    def _patient_fact(self, patient: Mapping[str, Any]) -> PatientFact:
        fields = {key: patient[key] for key in PATIENT_RECORD_KEEP if key in patient}
        return PatientFact(
            collection="patient",
            event_type="PATIENT",
            record_path="his:patient",
            field_path="his:patient",
            fields=fields,
            time=_text(patient.get("createdAt")),
        )

    def _event_fact(self, event: Mapping[str, Any], counters: dict[str, int], warnings: list[str]) -> PatientFact:
        raw_type = _text(event.get("type"))
        if raw_type is None:
            warnings.append("HIS 时间线存在没有 type 的事件，按 UNKNOWN 记录。")
            event_type = "UNKNOWN"
        else:
            event_type = raw_type
        counters[event_type] = counters.get(event_type, 0) + 1
        record_path = f"his:timeline:{event_type}:{counters[event_type]}"
        data = event.get("data")
        fields: dict[str, Any] = {}
        if isinstance(data, Mapping):
            fields = {key: value for key, value in data.items() if key not in IDENTIFIER_FIELDS}
        elif data is not None:
            fields = {"value": data}
            warnings.append(f"HIS 事件载荷不是对象（{record_path}），原值记入 fields.value。")
        else:
            warnings.append(f"HIS 事件缺少实体载荷（{record_path}），该事实没有字段。")
        time = _text(event.get("time"))
        start, end = _window(event_type, data, time)
        return PatientFact(
            collection=event_type.lower(),
            event_type=event_type,
            record_path=record_path,
            field_path=record_path,
            fields=fields,
            time=time,
            start=start,
            end=end,
        )


def _events_of(data: Any, *, stage: str, warnings: list[str], label: str) -> tuple[Mapping[str, Any], ...]:
    events = data.get("events") if isinstance(data, Mapping) else None
    if events is None:
        return ()
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise HISHttpError(f"HIS timeline 'events' must be a JSON array ({label})", stage=stage)
    usable = tuple(event for event in events if isinstance(event, Mapping))
    if len(usable) != len(events):
        warnings.append(f"HIS 时间线返回了 {len(events) - len(usable)} 个非对象事件（{label}），已忽略。")
    return usable


def _window(event_type: str, data: Any, time: str | None) -> tuple[str | None, str | None]:
    keys = RANGE_FIELDS.get(event_type.upper())
    if keys is None or not isinstance(data, Mapping):
        return None, None
    start_key, end_key = keys
    start = _text(data.get(start_key)) or time
    end = _text(data.get(end_key))
    if start is None and end is None:
        return None, None
    return start, end
