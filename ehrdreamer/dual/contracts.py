from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Annotated, Literal
from pydantic import ConfigDict, Field, RootModel, StrictFloat, StrictInt, StringConstraints, model_validator
from ehrdreamer.contracts import Contract
from ehrworld.model_policy import DEFAULT_MODEL, ModelName


class ReferenceRule(Contract):
    key: str
    unit: str
    lower: float | None = None
    upper: float | None = None
    upper_inclusive: bool = True
    source: str = Field(min_length=1, max_length=500)
    label: str = Field(default='用户指定的研究评价范围', max_length=150)

    @model_validator(mode='after')
    def bounds(self):
        if self.lower is None and self.upper is None:
            raise ValueError('A reference bound is required')
        if self.lower is not None and self.upper is not None and self.lower >= self.upper:
            raise ValueError('Invalid reference bounds')
        return self


class GenerationRequest(Contract):
    case_id: str = Field(min_length=1, max_length=80)
    backend: Literal['api'] = 'api'
    model: ModelName = DEFAULT_MODEL
    policy_model: ModelName | None = None
    dynamics_model: ModelName | None = None
    outcome_model: ModelName | None = None
    seed: int = Field(default=42, ge=0, le=2147483647, strict=True)
    max_operations: int = Field(default=64, ge=8, le=160, strict=True)
    max_hours: float = Field(default=72, ge=1, le=720)
    max_cycles: int = Field(default=3, ge=1, le=10, strict=True)
    decision_hours: float = Field(default=12, ge=1, le=48)
    forecast_horizon_hours: float = Field(default=24, ge=1, le=168)
    max_candidates: int = Field(default=3, ge=2, le=4, strict=True)
    planning_samples: int = Field(default=1, ge=1, le=3, strict=True)
    max_followups: int | None = Field(default=None, ge=1, le=8, strict=True,
                                    description='兼容旧字段；显式设置时限制决策复评轮次，不代表疗程。')
    reference_rules: tuple[ReferenceRule, ...] = Field(default=(), max_length=256)
    source_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))).replace(hour=8, minute=0, second=0, microsecond=0))

    @model_validator(mode='after')
    def settings(self):
        if self.decision_hours > self.forecast_horizon_hours:
            raise ValueError('Decision window cannot exceed forecast horizon')
        if len({r.key for r in self.reference_rules}) != len(self.reference_rules):
            raise ValueError('Duplicate reference metric')
        return self


class BatchRequest(GenerationRequest):
    case_id: str = ''
    max_cycles: int = Field(default=10, ge=1, le=10, strict=True)
    max_hours: float = Field(default=168, ge=1, le=720)


class SupportParameters(Contract):
    fio2: float | None = Field(default=None, ge=.21, le=1)
    pressure_cm_h2o: float | None = Field(default=None, ge=0, le=12)


class PlanAction(Contract):
    operation: Literal['start', 'adjust', 'stop', 'continue', 'wait', 'order_test']
    target: str = Field(default='', max_length=60)
    offset_hours: float = Field(default=0, ge=0, le=168)
    parameters: SupportParameters = Field(default_factory=SupportParameters)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=12)
    guideline_rule_ids: tuple[str, ...] = Field(default=(), max_length=12)


class CandidatePlan(Contract):
    candidate_id: str = Field(min_length=1, max_length=60)
    actions: tuple[PlanAction, ...] = Field(min_length=1, max_length=6)
    review_metrics: tuple[str, ...] = Field(default=(), max_length=256)
    review_panels: tuple[str, ...] = Field(default=(), max_length=16)
    reassess_after_hours: float | None = Field(default=None, gt=0, le=48)
    rationale: str = Field(min_length=1, max_length=600)
    uncertainty: str = Field(default='', max_length=400)


class ClinicalAssessment(Contract):
    mode: Literal['treatment_required', 'maintain_current', 'observe_without_treatment', 'needs_clinical_review']
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=12)
    guideline_rule_ids: tuple[str, ...] = Field(min_length=1, max_length=12)
    rationale: str = Field(min_length=1, max_length=400)
    missing_information: tuple[str, ...] = Field(default=(), max_length=12)


class PlanProposal(Contract):
    status: Literal['ready', 'needs_evidence', 'insufficient_information', 'no_feasible_plan']
    clinical_assessment: ClinicalAssessment | None = None
    knowledge_queries: tuple[str, ...] = Field(default=(), max_length=5)
    plans: tuple[CandidatePlan, ...] = Field(default=(), max_length=4)
    working_diagnoses: tuple[str, ...] = Field(default=(), max_length=4)
    information_request: tuple[str, ...] = Field(default=(), max_length=20)
    issue: str = Field(default='', max_length=500)


class PlanSelection(Contract):
    status: Literal['selected', 'blocked', 'needs_evaluation', 'no_feasible_plan']
    selected_plan_id: str | None = None
    rationale: str = Field(default='', max_length=600)


CensoredValue = Annotated[str, StringConstraints(
    strict=True, pattern=r'^(?:<=|>=|<|>|≤|≥)\s*(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$')]
LabValue = StrictInt | StrictFloat | CensoredValue | None


class TransitionReply(RootModel[dict[str, LabValue]]):
    """The entire model reply is one flat lab dictionary, without an envelope."""
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)


class ResultSelection(Contract):
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=16)
    unresolved_metric_keys: tuple[str, ...] = Field(default=(), max_length=256)
