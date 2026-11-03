from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True, allow_inf_nan=False)


def naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


class Diagnosis(Record):
    content: str = Field(alias="Content", min_length=1)
    reason: str = Field(alias="Reason", min_length=1)
    code: str | None = None


class Diagnoses(Record):
    primary: Diagnosis = Field(alias="Primary Diagnosis")
    secondary: tuple[Diagnosis, ...] = Field(default=(), alias="Secondary Diagnoses")


class PatientContext(Record):
    basic_information: str = Field(alias="Basic Information", min_length=1)
    history_information: str = Field(alias="History Information", min_length=1)
    diagnoses: Diagnoses = Field(alias="Diagnosis Results")
    demographics: dict[str, JsonValue] = Field(default_factory=dict)


class Action(Record):
    action_id: str = Field(min_length=1)
    kind: Literal["inquiry", "intervention"]
    name: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    source: str = Field(min_length=1)
    value_type: Literal["number", "label"] | None = None
    unit: str | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_kind(self):
        if (self.kind == "inquiry") != (self.value_type is not None):
            raise ValueError("Inquiries require a value_type; interventions require value_type=null")
        # Parameters are action attributes (dose, route, specimen...), never outcomes.
        forbidden = {"value", "valuenum", "result", "ground_truth", "reference_low", "reference_high"}
        if forbidden.intersection(self.parameters):
            raise ValueError("Outcome/reference fields cannot be action parameters")
        return self

    @property
    def measurement_key(self) -> tuple[str, str | None]:
        return self.concept, self.unit


def validate_value(action: Action, value: Any) -> None:
    if action.kind == "intervention":
        if value is not None:
            raise ValueError("Intervention outcomes must be null")
    elif action.value_type == "number":
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError("Numeric inquiries require finite numeric values")
    elif not isinstance(value, str) or not value.strip():
        raise ValueError("Label inquiries require nonempty strings")


class Event(Record):
    action: Action
    value: float | str | None
    reference_low: float | None = None
    reference_high: float | None = None

    @model_validator(mode="before")
    @classmethod
    def prevent_coercion(cls, value):
        if isinstance(value, dict) and "action" in value:
            action = value["action"] if isinstance(value["action"], Action) else Action.model_validate(value["action"])
            validate_value(action, value.get("value"))
        return value

    @model_validator(mode="after")
    def validate_event(self):
        validate_value(self.action, self.value)
        for bound in (self.reference_low, self.reference_high):
            if bound is not None and not math.isfinite(bound):
                raise ValueError("Reference bounds must be finite")
        if self.reference_low is not None and self.reference_high is not None and self.reference_low > self.reference_high:
            raise ValueError("Inverted reference range")
        return self

    def history_dict(self) -> dict:
        # Reference ranges are for scoring only and never enter the model history.
        return {"action": self.action.model_dump(mode="json"), "value": self.value}


class Step(Record):
    timestamp: datetime
    events: tuple[Event, ...] = Field(min_length=1)
    _timestamp_utc = field_validator("timestamp")(naive_utc)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [event.action.action_id for event in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate action_id within one action set")
        return self

    def history_dict(self) -> dict:
        return {"timestamp": self.timestamp.isoformat(), "events": [event.history_dict() for event in self.events]}


class Episode(Record):
    subject_id: str
    episode_id: str
    admitted_at: datetime
    discharged_at: datetime
    context: PatientContext
    steps: tuple[Step, ...] = Field(min_length=1)
    _timestamps_utc = field_validator("admitted_at", "discharged_at")(naive_utc)

    @model_validator(mode="after")
    def chronology(self):
        if self.discharged_at < self.admitted_at:
            raise ValueError("Discharge precedes admission")
        times = [step.timestamp for step in self.steps]
        if times != sorted(set(times)):
            raise ValueError("Steps must have strictly increasing timestamps")
        if times[0] < self.admitted_at or times[-1] > self.discharged_at:
            raise ValueError("Events outside the hospitalization")
        ids = [event.action.action_id for step in self.steps for event in step.events]
        if len(ids) != len(set(ids)):
            raise ValueError("Action IDs must be unique within an episode")
        return self


class PatientState(Record):
    timestamp: datetime
    context: PatientContext
    history: tuple[Step, ...] = ()
    _timestamp_utc = field_validator("timestamp")(naive_utc)

    @model_validator(mode="after")
    def check_history(self):
        times = [s.timestamp for s in self.history]
        if times != sorted(set(times)) or any(t > self.timestamp for t in times):
            raise ValueError("Invalid state history chronology")
        return self


class Rollout(Record):
    subject_id: str
    episode_id: str
    mode: Literal["full", "next"]
    backend: str
    steps: tuple[Step, ...]
    elapsed_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def chronology(self):
        times = [s.timestamp for s in self.steps]
        ids = [e.action.action_id for s in self.steps for e in s.events]
        if times != sorted(set(times)) or len(ids) != len(set(ids)):
            raise ValueError("Prediction timestamps and action IDs must be unique and chronological")
        return self
