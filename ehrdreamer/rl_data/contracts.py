"""Training response contract; values and rewards belong to the environment."""
from typing import Literal
from pydantic import Field, model_validator
from ehrdreamer.contracts import Contract

TREATMENTS = {
    'oxygen': '氧疗', 'cpap': '经鼻CPAP', 'nippv': 'NIPPV',
    'noninvasive_support': '无创支持（模式未注明）',
    'respiratory_support_unspecified': '呼吸支持（方式未注明）',
    'invasive_ventilation': '有创机械通气', 'surfactant': '肺表面活性物质',
    'antimicrobial': '抗感染治疗', 'phototherapy': '光疗',
    'thermal_nutrition': '保温与液体营养支持', 'feeding': '喂养调整',
    'caffeine': '咖啡因治疗', 'vasopressor': '循环支持', 'transfusion': '输血',
    'immunoglobulin': '免疫球蛋白', 'thyroid_replacement': '甲状腺激素治疗',
    'gi_decompression': '胃肠减压', 'iron_vitamins': '铁剂与维生素补充',
    'specialist_review': '专科评估', 'observation': '观察复评',
}


class TreatmentAction(Contract):
    operation: Literal['start', 'continue', 'adjust', 'stop', 'observe', 'consult']
    target: str
    at_hours: float = Field(default=0, ge=0, le=72)
    reassess_after_hours: float = Field(ge=1, le=72)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=16)
    guideline_rule_ids: tuple[str, ...] = Field(min_length=1, max_length=12)
    rationale: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def catalog(self):
        if self.target not in TREATMENTS:
            raise ValueError('Unknown treatment target')
        if self.operation == 'observe' and self.target != 'observation':
            raise ValueError('Observation requires the observation target')
        if self.operation == 'consult' and self.target != 'specialist_review':
            raise ValueError('Consultation requires the specialist_review target')
        if self.target == 'observation' and self.operation != 'observe':
            raise ValueError('Observation is not a drug treatment')
        return self


class PolicyResponse(Contract):
    actions: tuple[TreatmentAction, ...] = Field(min_length=1, max_length=6)
    monitoring_metric_keys: tuple[str, ...] = Field(min_length=1, max_length=80)
    uncertainty: str = Field(min_length=1, max_length=600)

    @model_validator(mode='after')
    def timing(self):
        times = [a.at_hours for a in self.actions]
        if times[0] != 0 or times != sorted(times):
            raise ValueError('Actions must start now and be ordered')
        if len(set(self.monitoring_metric_keys)) != len(self.monitoring_metric_keys):
            raise ValueError('Duplicate monitoring metric')
        return self


from .compact import POLICY_SYSTEM as SYSTEM_PROMPT
