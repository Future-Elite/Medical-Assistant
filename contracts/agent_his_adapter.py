"""Agent-side reference: read HIS and build the RAG request payload.

Architecture (see ``contracts/his_rag.md``): **HIS -> Agent -> RAG**.

- the **Agent** (not RAG) reads HIS over its read-only GET endpoints, and owns the
  HIS connection, its errors and the mapping below;
- the Agent converts the responses into the ``patient_snapshot`` payload defined
  by the contract and passes that structured data to RAG;
- **RAG never connects to HIS** and never imports this module. This file is a
  reference implementation of the *Agent side*, kept next to the contract it
  implements; it is not part of the ``rag`` package.

``as_of`` is supplied by the Agent and enforced by RAG
(``rag.his.filter_snapshot``), so this module carries every fact as fetched and
applies no time filtering -- the boundary has exactly one implementation.

Safety posture:

- read-only: only ``GET`` is issued (the transport only receives a URL and a
  timeout, so no write request can be constructed);
- identifiers never enter the payload: ``name`` / ``idCard`` / ``phone`` /
  ``address`` / ``patientId`` are dropped, and the patient fact keeps only
  ``gender`` / ``birthDate``;
- no rewriting: clinical fields pass through unchanged; nothing is translated,
  inferred or defaulted;
- auditable gaps: events without a payload or without a ``type`` are kept and
  reported in the payload's ``warnings`` instead of being dropped silently.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

DEFAULT_HIS_BASE_URL = "http://127.0.0.1:8080"
HIS_SOURCE = "his_demo"

# Direct identifiers and the patient key must never reach RAG or an external connector.
IDENTIFIER_FIELDS = frozenset({"name", "idCard", "phone", "address", "patientId"})

# The patient record is mostly identifiers and bookkeeping; only these are facts.
# ``createdAt`` is not copied as a field but serves as the fact's time bound.
PATIENT_RECORD_KEEP = ("gender", "birthDate")

# Range events: (start field, end field) inside the entity payload.
RANGE_FIELDS = {
    "ORDER": ("startTime", "stopTime"),
    "ADMISSION": ("admissionTime", "dischargeTime"),
}

# Event types whose payload carries the admission id of the admission feed.
ADMISSION_EVENT_TYPES = ("ADMISSION", "DISCHARGE")

Transport = Callable[[str, float], Mapping[str, Any]]

# HIS runs on localhost in this project; a stray HTTP_PROXY must not intercept it.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class HISClientError(RuntimeError):
    """Agent-side HIS access failure, carrying where it happened."""

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
        raise HISClientError(
            f"HIS returned HTTP {error.code} for {url}", stage="http", status_code=error.code, retryable=error.code >= 500
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise HISClientError(f"cannot reach HIS at {url}: {error}", stage="http", retryable=True) from None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HISClientError(f"HIS returned a non-JSON body for {url}", stage="decode") from None
    if not isinstance(payload, Mapping):
        raise HISClientError(f"HIS returned JSON that is not an object for {url}", stage="decode")
    return payload


def envelope_data(payload: Mapping[str, Any], *, label: str, stage: str) -> Any:
    """Validate the ``{code,message,data}`` envelope and return ``data``."""
    if "code" not in payload:
        raise HISClientError(f"HIS payload for {label} is missing the {{code,message,data}} envelope", stage=stage)
    if payload.get("code") != 0:
        raise HISClientError(f"HIS error code={payload.get('code')} for {label}: {payload.get('message')}", stage=stage)
    return payload.get("data")


def build_payload(
    patient_response: Mapping[str, Any],
    patient_timeline_response: Mapping[str, Any],
    admission_timeline_responses: Sequence[Mapping[str, Any]] = (),
    *,
    patient_ref: str,
    as_of: str,
    source: str = HIS_SOURCE,
) -> dict[str, Any]:
    """Map HIS responses (already fetched by the Agent) to the RAG payload.

    The result is the exact ``patient_snapshot`` dict the Agent passes to RAG.
    Responses are the raw ``{code,message,data}`` envelopes, so this works with
    recorded payloads (offline tests) as well as with any HTTP client.
    """
    patient = envelope_data(patient_response, label=f"/api/patients/{patient_ref}", stage="patient")
    if not isinstance(patient, Mapping) or not patient:
        raise ValueError(f"unknown patient_ref: {patient_ref}")
    warnings: list[str] = []
    label = "/api/patients/{id}/timeline"
    events = list(
        _events_of(envelope_data(patient_timeline_response, label=label, stage="timeline"), label=label, stage="timeline", warnings=warnings)
    )
    admission_label = "/api/admissions/{id}/timeline"
    for response in admission_timeline_responses:
        events.extend(
            _events_of(envelope_data(response, label=admission_label, stage="admission_timeline"), label=admission_label,
                       stage="admission_timeline", warnings=warnings)
        )
    counters: dict[str, int] = {}
    facts = [_patient_fact(patient)]
    for event in _by_time(events):
        facts.append(_event_fact(event, counters, warnings))
    return {
        "patient_ref": str(patient_ref),
        "as_of": str(as_of),
        "source": source,
        "facts": facts,
        "reviewed_external_terms": [],
        "warnings": warnings,
    }


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _events_of(data: Any, *, label: str, stage: str, warnings: list[str]) -> tuple[Mapping[str, Any], ...]:
    events = data.get("events") if isinstance(data, Mapping) else None
    if events is None:
        return ()
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise HISClientError(f"HIS timeline 'events' must be a JSON array ({label})", stage=stage)
    usable = tuple(event for event in events if isinstance(event, Mapping))
    if len(usable) != len(events):
        warnings.append(f"HIS 时间线返回了 {len(events) - len(usable)} 个非对象事件（{label}），已忽略。")
    return usable


def _by_time(events: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    """Stable sort by event time; events without a time keep their order last."""
    return tuple(sorted(events, key=lambda event: (event.get("time") is None, str(event.get("time") or ""))))


def _window(event_type: str, data: Any, time: str | None) -> tuple[str | None, str | None]:
    keys = RANGE_FIELDS.get(event_type.upper())
    if keys is None or not isinstance(data, Mapping):
        return None, None
    start = _text(data.get(keys[0])) or time
    end = _text(data.get(keys[1]))
    if start is None and end is None:
        return None, None
    return start, end


def _admission_ids(events: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    seen: list[str] = []
    for event in events:
        if str(event.get("type") or "").strip().upper() not in ADMISSION_EVENT_TYPES:
            continue
        data = event.get("data")
        if isinstance(data, Mapping):
            admission_id = _text(data.get("id"))
            if admission_id and admission_id not in seen:
                seen.append(admission_id)
    return tuple(seen)


def _patient_fact(patient: Mapping[str, Any]) -> dict[str, Any]:
    fields = {key: patient[key] for key in PATIENT_RECORD_KEEP if key in patient}
    return {
        "collection": "patient",
        "event_type": "PATIENT",
        "record_path": "his:patient",
        "field_path": "his:patient",
        "fields": fields,
        "time": _text(patient.get("createdAt")),
        "start": None,
        "end": None,
    }


def _event_fact(event: Mapping[str, Any], counters: dict[str, int], warnings: list[str]) -> dict[str, Any]:
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
    return {
        "collection": event_type.lower(),
        "event_type": event_type,
        "record_path": record_path,
        "field_path": record_path,
        "fields": fields,
        "time": time,
        "start": start,
        "end": end,
    }


@dataclass(frozen=True)
class AgentHisClient:
    """Agent-side HIS client: fetch the responses and build the RAG payload.

    ``transport`` is the test seam -- a callable ``(url, timeout) -> JSON object``.
    :meth:`snapshot_payload` returns the dict to place in the RAG request's
    ``patient_snapshot`` field.
    """

    base_url: str = DEFAULT_HIS_BASE_URL
    timeout_seconds: float = 30.0
    transport: Transport | None = None
    include_admission_timelines: bool = True

    def snapshot_payload(self, patient_ref: str, as_of: str) -> dict[str, Any]:
        ref = str(patient_ref).strip()
        if not ref:
            raise ValueError("patient_ref must be a non-empty string")
        quoted = urllib.parse.quote(ref, safe="")
        patient_response = self._get(f"/api/patients/{quoted}", stage="patient")
        timeline_response = self._get(f"/api/patients/{quoted}/timeline", stage="timeline")
        admission_responses: list[Mapping[str, Any]] = []
        notes: list[str] = []
        if self.include_admission_timelines:
            patient_events = _events_of(
                envelope_data(timeline_response, label="/api/patients/{id}/timeline", stage="timeline"),
                label="/api/patients/{id}/timeline", stage="timeline", warnings=notes,
            )
            for admission_id in _admission_ids(patient_events):
                response = self._get(
                    f"/api/admissions/{urllib.parse.quote(admission_id, safe='')}/timeline", stage="admission_timeline"
                )
                response_data = response.get("data") if isinstance(response, Mapping) else None
                owner = _text(response_data.get("patientId")) if isinstance(response_data, Mapping) else None
                if owner is not None and owner != ref:
                    notes.append(
                        f"HIS 住院时间线（admission {admission_id}）返回了患者 {owner} 的数据，"
                        f"与本快照患者 {ref} 不一致，已忽略。"
                    )
                    continue
                admission_responses.append(response)
        payload = build_payload(
            patient_response, timeline_response, admission_responses, patient_ref=ref, as_of=str(as_of)
        )
        payload["warnings"] = list(dict.fromkeys([*payload["warnings"], *notes]))
        return payload

    def _get(self, path: str, *, stage: str) -> Mapping[str, Any]:
        url = self.base_url.rstrip("/") + path
        transport = self.transport or default_transport
        payload = transport(url, self.timeout_seconds)
        if not isinstance(payload, Mapping):
            raise HISClientError(f"HIS payload for {path} must be a JSON object", stage=stage)
        return payload
