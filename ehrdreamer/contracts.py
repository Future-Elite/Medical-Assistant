from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from ehrworld.model_policy import DEFAULT_MODEL, ModelName

Phase = Literal['assessment', 'treatment', 'discharge']
ConditionCode = Literal['pneumonia', 'rds', 'infection_risk', 'respiratory_distress', 'jaundice', 'neonatal_observation']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)


class Intake(Contract):
    sex: Literal['男', '女', '未知'] = '未知'
    gestational_weeks: int | None = Field(default=None, ge=20, le=44)
    gestational_days: int | None = Field(default=None, ge=0, le=6)
    postnatal_age_hours: float | None = Field(default=None, ge=0, le=1440)
    birth_weight_g: int | None = Field(default=None, ge=200, le=6500)
    chief_complaint: str = Field(max_length=500)
    symptoms: tuple[str, ...] = ()
    perinatal_risks: tuple[str, ...] = ()
    delivery_mode: str = 'unknown'
    known_conditions: tuple[str, ...] = ()


class Parameters(Contract):
    intensity: float = Field(default=.5, ge=0, le=1)
    fio2: float | None = Field(default=None, ge=.21, le=1)
    pressure_cm_h2o: float | None = Field(default=None, ge=0, le=12)
    duration_hours: float = Field(default=24, ge=.25, le=168,
                                  description='等待时的推进时长；治疗时仅为建议复评间隔，治疗持续至明确停止。')
    wait_for_all_reports: bool = False


class FollowupTest(Contract):
    target: str
    metrics: tuple[str,...] = Field(min_length=1,max_length=3)


class Decision(Contract):
    action_type: Literal['order_assessment','order_followup','review_treatment','finish_treatment','order_exam', 'diagnose', 'start_treatment', 'adjust_treatment',
                         'stop_treatment', 'wait', 'consult', 'propose_discharge', 'propose_transfer']
    target: str = Field(default='', max_length=80)
    rationale: str = Field(min_length=1, max_length=700)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=24)
    parameters: Parameters = Field(default_factory=Parameters)
    diagnosis: ConditionCode | None = None
    certainty: Literal['suspected', 'working'] = 'working'
    followup_tests: tuple[FollowupTest,...] = Field(default=(),max_length=3)


class AgentView(Contract):
    """An allowlisted projection. No environment, source row, or future outcomes."""
    intake: Intake
    phase: Phase
    time_hours: float
    observations: tuple[dict, ...]
    conditions: tuple[dict, ...]
    orders: tuple[dict, ...]
    active_treatments: tuple[dict, ...]
    pending_tests: tuple[dict, ...]
    last_receipt: dict | None
    next_due_hours: float | None
    operations_remaining: int
    guidance: dict
    workflow: dict = Field(default_factory=dict)


class ConditionOutcome(Contract):
    condition_id: str
    status: Literal['resolved', 'improved', 'unresolved', 'unknown']
    evidence_ids: tuple[str, ...] = ()


class OutcomeProposal(Contract):
    disposition: Literal['home', 'transfer', 'ongoing']
    overall_outcome: Literal['resolved', 'improved', 'unresolved', 'unknown']
    per_condition: tuple[ConditionOutcome, ...]
    selected_evidence_ids: tuple[str, ...]
    follow_up_codes: tuple[Literal['neonatal_review', 'respiratory_review', 'feeding_weight_review',
                                  'infection_review', 'jaundice_review', 'receiving_hospital'], ...] = ()


class GenerateRequest(Contract):
    case_id: str = Field(min_length=1, max_length=80)
    backend: Literal['api', 'demo'] = 'api'
    model: ModelName = DEFAULT_MODEL
    policy_model: ModelName | None = None
    dynamics_model: ModelName | None = Field(default=None,deprecated=True,description='兼容旧请求；患者动力学使用本地已训练模型，此字段不参与选择。')
    outcome_model: ModelName | None = None
    seed: int = Field(default=42, ge=0, le=2147483647, strict=True)
    max_operations: int = Field(default=96, ge=8, le=160, strict=True)
    max_hours: float = Field(default=168, ge=6, le=720)
    max_followups: int | None = Field(default=None,ge=1,le=4,strict=True)
    source_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))).replace(hour=8, minute=0, second=0, microsecond=0))


class BatchRequest(Contract):
    backend: Literal['api', 'demo'] = 'api'
    model: ModelName = DEFAULT_MODEL
    policy_model: ModelName | None = None
    dynamics_model: ModelName | None = Field(default=None,deprecated=True,description='兼容旧请求；患者动力学使用本地已训练模型，此字段不参与选择。')
    outcome_model: ModelName | None = None
    seed: int = Field(default=42, ge=0, le=2147483647, strict=True)
    max_hours: float = Field(default=168, ge=6, le=720)
    max_followups: int | None = Field(default=None,ge=1,le=4,strict=True)
    source_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class InverseRequest(Contract):
    before_event_id: str
    after_event_id: str


class InverseCandidate(Contract):
    target: str
    explanation: str = Field(max_length=500)


class InverseProposal(Contract):
    candidates: tuple[InverseCandidate, ...] = Field(max_length=6)
    uncertainty: str = Field(min_length=1, max_length=700)
