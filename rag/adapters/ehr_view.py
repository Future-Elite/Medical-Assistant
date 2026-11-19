"""Project an EHR ``PatientEnvironment.view()`` into ``PatientSnapshot``.

``PatientSnapshot`` is the stable RAG-facing, time-bounded fact contract; it is
not the EHR runtime state.  The EHR view is organized as intake / observations /
active treatments / pending tests, while RAG needs flat facts with source
locations and times.  This adapter is the explicit boundary between them:

* raw EHR-facing facts are kept (``ehr_intake``, ``ehr_observation``,
  ``ehr_condition``, ``ehr_action``);
* normalized facts for the existing RAG readers are also emitted
  (``derived_birth``, ``patient``, ``labs``, ``respiratory_support``);
* no hidden state, future report, or synthetic value is invented.  A missing
  anchor time is an error rather than a fabricated date.

The adapter does not import the EHR implementation; callers pass a plain
mapping so the RAG package stays independent of the EHR runtime.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from ..core.his import PatientRecord, PatientSnapshot, filter_snapshot

_EHR_METRIC_KEYS = {
    "ph": "ph", "paco2(mmhg)": "paco2", "paco2": "paco2",
    "pao2(mmhg)": "pao2", "pao2": "pao2", "spo2(%)": "spo2", "spo2": "spo2",
    "wbc(10^9/l)": "wbc", "wbc": "wbc", "platelet(10^9/l)": "platelet",
    "hemoglobin(g/l)": "hemoglobin", "crp(mg/l)": "crp", "crp": "crp",
    "sodium(mmol/l)": "sodium", "na": "sodium", "glucose(mmol/l)": "glucose",
    "creatinine(umol/l)": "creatinine", "urea(mmol/l)": "urea",
    "temperature(c)": "temperature_c", "temperature": "temperature_c",
}


def _optional(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _metric_key(raw: str) -> str:
    key = str(raw or "").strip().casefold().replace(" ", "")
    return _EHR_METRIC_KEYS.get(key, key)


def _absolute_time(start_time: datetime | None, hours: Any, fallback: str) -> str:
    if start_time is not None:
        try:
            return (start_time + timedelta(hours=float(hours or 0))).isoformat()
        except (TypeError, ValueError):
            pass
    return fallback


def snapshot_from_ehr_view(
    view: Mapping[str, Any],
    *,
    patient_ref: str,
    as_of: str | None = None,
    start_time: datetime | str | None = None,
    source: str = "ehr_view",
) -> PatientSnapshot:
    """Convert one EHR view mapping into a ``PatientSnapshot``.

    ``as_of`` is required unless ``start_time`` and ``view['time_hours']`` are
    both available.  The returned snapshot is filtered by ``as_of`` exactly like
    any other HIS snapshot.
    """
    if not isinstance(view, Mapping):
        if hasattr(view, "model_dump"):
            view = view.model_dump(mode="json")
        elif hasattr(view, "dict") and callable(view.dict):
            view = view.dict()
        else:
            raise ValueError("EHR view must be a mapping or pydantic model")
    if not str(patient_ref or "").strip():
        raise ValueError("patient_ref is required")
    anchor: datetime | None
    if isinstance(start_time, str):
        anchor = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    else:
        anchor = start_time
    if not as_of:
        if anchor is None:
            raise ValueError("as_of or start_time is required for an EHR view")
        as_of = _absolute_time(anchor, view.get("time_hours", 0), anchor.date().isoformat())

    facts: list[PatientRecord] = []
    intake = view.get("intake")
    if isinstance(intake, Mapping):
        ga_weeks = intake.get("gestational_weeks", intake.get("ga_weeks"))
        ga_days = intake.get("gestational_days", intake.get("ga_days"))
        birth_weight = intake.get("birth_weight_g", intake.get("birthWeightG"))
        if ga_weeks is not None:
            try:
                total_days = int(ga_weeks) * 7 + int(ga_days or 0)
            except (TypeError, ValueError):
                total_days = None
            if total_days is not None:
                facts.append(PatientRecord(
                    record_type="derived_birth", event_type="EHR_INTAKE",
                    record_id="ehr:intake:birth",
                    fields={"ga_days": total_days, "ga_text": f"{ga_weeks}周{int(ga_days or 0)}天",
                            "birth_weight_g": birth_weight, "data_status": "observed_positive"},
                    event_time=as_of, source_system="ehr",
                ))
        facts.append(PatientRecord(
            record_type="ehr_intake", event_type="EHR_INTAKE",
            record_id="ehr:intake",
            fields=dict(intake), event_time=as_of, source_system="ehr",
        ))
        age_hours = intake.get("age_hours", intake.get("ageHours"))
        if age_hours is not None:
            try:
                pna_days = float(age_hours) / 24.0
            except (TypeError, ValueError):
                pna_days = None
            if pna_days is not None:
                facts.append(PatientRecord(
                    record_type="patient", event_type="EHR_INTAKE",
                    record_id="ehr:intake:patient",
                    fields={"pna_days": pna_days, "data_status": "observed_positive"},
                    event_time=as_of, source_system="ehr",
                ))

    for index, report in enumerate(view.get("observations") or ()):
        if not isinstance(report, Mapping):
            continue
        items = report.get("items") or ()
        normalized: dict[str, Any] = {}
        raw_items = []
        status = "observed_positive"
        for item in items:
            if not isinstance(item, Mapping):
                continue
            raw_items.append(dict(item))
            key = _metric_key(str(item.get("key") or ""))
            value = item.get("value")
            if key and value is not None:
                normalized[key] = value
            if str(item.get("status") or "") in {"pending", "uncertain", "not_recorded"}:
                status = str(item["status"])
        event_id = str(report.get("event_id") or report.get("report_id") or f"obs-{index}")
        time_text = _absolute_time(anchor, report.get("collected_at_hours", report.get("time_hours")), as_of)
        fields = {**normalized, "data_status": status}
        facts.append(PatientRecord(
            record_type="labs", event_type="EHR_OBSERVATION",
            record_id=f"ehr:observation:{event_id}",
            fields=fields, event_time=time_text, source_system="ehr",
            extra={"ehr_items": raw_items, "session_type": report.get("session_type"),
                   "target": report.get("target"), "time_hours": report.get("time_hours"),
                   "collected_at_hours": report.get("collected_at_hours")},
        ))

    for index, condition in enumerate(view.get("conditions") or ()):
        if isinstance(condition, Mapping):
            facts.append(PatientRecord(
                record_type="ehr_condition", event_type="EHR_CONDITION",
                record_id=f"ehr:condition:{index}",
                fields=dict(condition), event_time=as_of, source_system="ehr",
            ))

    for index, treatment in enumerate(view.get("active_treatments") or ()):
        if not isinstance(treatment, Mapping):
            continue
        mode = treatment.get("target") or treatment.get("mode") or treatment.get("name")
        if mode:
            facts.append(PatientRecord(
                record_type="respiratory_support", event_type="EHR_ACTIVE_TREATMENT",
                record_id=f"ehr:active-treatment:{index}",
                fields={"mode": str(mode), "data_status": "observed_positive"},
                event_time=as_of, source_system="ehr",
            ))

    for index, action in enumerate(view.get("executed_actions") or ()):
        if isinstance(action, Mapping):
            facts.append(PatientRecord(
                record_type="ehr_action", event_type="EHR_ACTION",
                record_id=f"ehr:action:{index}",
                fields=dict(action), event_time=as_of, source_system="ehr",
            ))

    for index, pending in enumerate(view.get("pending_tests") or ()):
        if not isinstance(pending, Mapping):
            continue
        target = str(pending.get("target") or pending.get("kind") or "").casefold()
        if "culture" in target:
            fields = {"culture_status": "pending", "data_status": "pending"}
        elif "imaging" in target or "x-ray" in target or "xray" in target:
            fields = {"report_status": "pending", "data_status": "pending"}
        else:
            fields = {"test_status": "pending", "data_status": "pending"}
        facts.append(PatientRecord(
            record_type="labs", event_type="EHR_PENDING_TEST",
            record_id=f"ehr:pending-test:{index}",
            fields=fields, event_time=as_of, source_system="ehr",
            extra={"pending_test": dict(pending)},
        ))

    snapshot = PatientSnapshot(
        patient_ref=str(patient_ref),
        as_of=str(as_of),
        records=tuple(facts),
        source=source,
        extra={"ehr_view": {"phase": view.get("phase"), "time_hours": view.get("time_hours"),
                            "remaining_cycles": view.get("remaining_cycles")}},
    )
    return filter_snapshot(snapshot, str(as_of))


__all__ = ["snapshot_from_ehr_view"]
