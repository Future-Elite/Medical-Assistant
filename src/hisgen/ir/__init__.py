"""临床路径 IR（Intermediate Representation）—— 层 1 的产物形态。

IR 描述的是**临床语义**，不是 GMF。设计目标是让临床人员能读懂并挑错；
把它编译成 Synthea 可加载的 GMF 是 compiler/ 的职责。
"""

from .models import (
    ActionSpec,
    CarePlanAction,
    Condition,
    Defaults,
    Disease,
    DiseaseMeta,
    EncounterAction,
    IRError,
    MedicationAction,
    ObservationAction,
    Operator,
    ProcedureAction,
    ReferralAction,
    Repeat,
    RiskFactor,
    State,
    TermRef,
    Transition,
)
from .load import load_ir, load_ir_text, resolve_ir

__all__ = [
    "ActionSpec",
    "CarePlanAction",
    "Condition",
    "Defaults",
    "Disease",
    "DiseaseMeta",
    "EncounterAction",
    "IRError",
    "MedicationAction",
    "ObservationAction",
    "Operator",
    "ProcedureAction",
    "ReferralAction",
    "Repeat",
    "RiskFactor",
    "State",
    "TermRef",
    "Transition",
    "load_ir",
    "load_ir_text",
    "resolve_ir",
]