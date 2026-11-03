from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ehrworld.schema import naive_utc
from ehrworld.model_policy import DEFAULT_MODEL, ModelName


class PatientInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="新生儿 001", min_length=1, max_length=60)
    age: int | None = Field(default=None, ge=0, le=110, strict=True)
    postnatal_age_hours: float = Field(default=48, ge=0, lt=672, allow_inf_nan=False)
    gestational_weeks: int = Field(default=38, ge=22, le=42, strict=True)
    gestational_days: int = Field(default=2, ge=0, le=6, strict=True)
    birth_weight_g: int = Field(default=3200, ge=200, le=6500, strict=True)
    weight_g: int = Field(default=3100, ge=200, le=6500, strict=True)
    sex: Literal["男", "女", "其他", "未知"] = "男"
    chief_complaint: str = Field(default="生后呼吸急促、呻吟，伴喂养减少", min_length=1, max_length=1200)
    primary_diagnosis: str = Field(default="新生儿感染性肺炎（疑似）", min_length=1, max_length=300)
    history: str = Field(default="生后出现呼吸急促，需鉴别感染性肺炎与其他呼吸窘迫病因。", max_length=2000)
    perinatal_history: str = Field(default="母亲胎膜早破约 20 小时，母体感染及 GBS 筛查情况需核实。", max_length=2000)
    allergies: str = Field(default="过敏史未明确", max_length=300)

    @classmethod
    def for_scenario(cls, scenario_id):
        if scenario_id == "pneumonia":
            return cls(name="模拟患者 001", age=65, sex="女", chief_complaint="发热、咳嗽伴气促 2 天", primary_diagnosis="社区获得性肺炎",
                       history="既往高血压病史，近期出现发热及咳嗽。", allergies="过敏史未记录")
        if scenario_id == "diabetes":
            return cls(name="模拟患者 001", age=58, chief_complaint="口渴、多尿，伴乏力 3 天",
                       primary_diagnosis="2 型糖尿病伴高血糖", history="既往 2 型糖尿病病史 8 年，近期血糖控制欠佳。")
        if scenario_id == "neonatal_rds":
            return cls(postnatal_age_hours=2, gestational_weeks=30, gestational_days=2, birth_weight_g=1450,
                       weight_g=1450, primary_diagnosis="早产儿呼吸窘迫综合征（RDS）",
                       chief_complaint="生后即出现呻吟、三凹征及呼吸急促",
                       history="早产，生后呼吸窘迫；需随访氧需求及呼吸支持反应。",
                       perinatal_history="母亲因先兆早产分娩，产前激素完成情况需核实；无明确感染证据。")
        return cls()


class InitialState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spo2: float = Field(default=90, ge=50, le=100, allow_inf_nan=False)
    respiratory_rate: int = Field(default=72, ge=10, le=140, strict=True)
    fio2: float = Field(default=.30, ge=.21, le=1, allow_inf_nan=False)
    cpap_cm_h2o: float = Field(default=6, ge=0, le=12, allow_inf_nan=False)
    respiratory_support: Literal["room_air", "oxygen", "cpap", "nippv", "invasive"] = "oxygen"
    spontaneous_breathing: bool = True
    recurrent_apnea: bool = False
    infection_suspected: bool = True
    suspected_pathogen: Literal["bacterial", "viral", "undetermined"] = "bacterial"
    bacterial_pneumonia_supported: bool = False
    heart_rate: int | None = Field(default=None, ge=0, le=300, strict=True)
    temperature: float | None = Field(default=None, ge=30, le=43, allow_inf_nan=False)
    ph: float | None = Field(default=None, ge=6, le=8, allow_inf_nan=False)
    paco2: float | None = Field(default=None, ge=5, le=200, allow_inf_nan=False)
    glucose: float | None = Field(default=None, ge=0, le=80, allow_inf_nan=False)

    @classmethod
    def for_scenario(cls, scenario_id):
        return cls(spo2=88, respiratory_rate=78, fio2=.35, respiratory_support="cpap", infection_suspected=False,
                   suspected_pathogen="undetermined") if scenario_id == "neonatal_rds" else cls()


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient: PatientInput = Field(default_factory=PatientInput)
    scenario_id: Literal["neonatal_pneumonia", "neonatal_rds", "diabetes", "pneumonia"] = "neonatal_pneumonia"
    initial_state: InitialState = Field(default_factory=InitialState)
    care_setting: Literal["nicu", "primary"] = "nicu"
    backend: Literal["api", "demo"] = "api"
    model: ModelName = DEFAULT_MODEL
    steps: int = Field(default=10, ge=1, le=10, strict=True)
    source_seed: dict | None = None
    interval_hours: int = Field(default=3, ge=1, le=24, strict=True)
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None, hour=8, minute=0, second=0, microsecond=0))
    _normalize_time = field_validator("start_time")(naive_utc)

    @model_validator(mode="after")
    def choose_default_patient(self):
        if "patient" not in self.model_fields_set:
            self.patient = PatientInput.for_scenario(self.scenario_id)
        if "initial_state" not in self.model_fields_set:
            self.initial_state = InitialState.for_scenario(self.scenario_id)
        if self.scenario_id.startswith("neonatal_"):
            if self.patient.age not in (None, 0):
                raise ValueError("Neonatal scenarios use postnatal hours, not adult age")
            if self.scenario_id == "neonatal_rds" and self.patient.gestational_weeks >= 37:
                raise ValueError("This RDS guideline adapter covers preterm infants")
        elif self.patient.age is None or self.patient.age < 18:
            raise ValueError("Legacy adult scenarios require adult age")
        return self


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: ModelName = DEFAULT_MODEL
    backend: Literal['api','demo'] = 'api'
    seed: int = Field(default=42, ge=0, le=2147483647, strict=True)
    interval_hours: int = Field(default=3, ge=1, le=24, strict=True)
    care_setting: Literal['nicu','primary'] = 'nicu'
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None,hour=8,minute=0,second=0,microsecond=0))
    source_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    _normalize_time = field_validator('start_time')(naive_utc)


class JobError(BaseModel):
    code: str
    message: str
    retryable: bool = False
