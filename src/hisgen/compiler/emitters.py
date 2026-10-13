"""每种 IR 动作 -> 一个或多个 GMF 状态。

一个 emitter 只负责产出**状态体**，不碰转移 —— 转移全部由 compile.py
统一连线。这样「谁连谁」这件事只有一处，不会散落在六个 emitter 里。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union

from ..ir.models import (
    CarePlanAction,
    EncounterAction,
    MedicationAction,
    ObservationAction,
    ProcedureAction,
    ReferralAction,
    State,
)
from ..terminology import Terminology

#: emitter 返回 (后缀, 状态体) 列表。后缀为 "" 表示不需要额外命名空间。
#: 完整状态名 = ctx.prefix + 后缀。
EmitResult = list[tuple[str, dict]]


@dataclass
class EmitContext:
    """emitter 需要的上下文。"""

    ir_state: State
    term: Terminology
    #: 本动作的状态名前缀，形如 ``<state_id>__act<序号>``
    prefix: str
    ordinal: int

    @property
    def where(self) -> str:
        """错误信息里的位置描述。"""
        return f"状态 {self.ir_state.id!r} 的第 {self.ordinal + 1} 个 on_enter 动作"


# --------------------------------------------------------------------- 各 emitter


def emit_encounter(action: EncounterAction, ctx: EmitContext) -> EmitResult:
    """一次就诊。

    ``codes`` 里放科室名 —— Synthea 把 ``Encounter.codes`` 写成 FHIR 的
    ``Encounter.type``，所以我们用科室名当 display，导出时它就是「科室」列。
    就诊类型（门诊/急诊…）另由 ``Encounter.class`` 给出。
    """
    department = ctx.term.local(action.department_zh)
    return [
        (
            "",
            {
                "type": "Encounter",
                "wellness": False,
                "encounter_class": action.encounter_class,
                "codes": [department.to_gmf()],
            },
        )
    ]


def emit_referral(action: ReferralAction, ctx: EmitContext) -> EmitResult:
    """转诊。本质上就是换一个科室再开一次就诊。"""
    department = ctx.term.local(action.department_zh)
    return [
        (
            "",
            {
                "type": "Encounter",
                "wellness": False,
                "encounter_class": action.encounter_class,
                "codes": [department.to_gmf()],
            },
        )
    ]


def emit_observation(action: ObservationAction, ctx: EmitContext) -> EmitResult:
    """一项检验。值从参考区间里随机取（Synthea 的 Range）。"""
    lab = ctx.term.lab(action.term)
    unit = action.unit_zh or lab.unit_zh
    low, high = action.range or lab.reference_range

    code = lab.code.to_gmf()
    code["display"] = action.name_zh or lab.name_zh

    return [
        (
            "",
            {
                "type": "Observation",
                "category": "laboratory",
                "codes": [code],
                "unit": unit,
                # Observation 带 range 时 Synthea 强制要求 unit 非空，上面已给
                "range": {"low": low, "high": high},
            },
        )
    ]


def emit_medication(action: MedicationAction, ctx: EmitContext) -> EmitResult:
    """一条用药医嘱。

    ``prescription`` 整块被 Synthea 当成 ``prescriptionDetails``（一个裸
    JsonObject）存进 Medication，再由 FhirR4 展开成 ``dosageInstruction``。
    其中 ``dosage`` 必须是 ``{amount, frequency, period, unit}`` 的**剂量结构**，
    ``unit`` 还得是 UCUM 时间单位 —— FhirR4 无条件读这四个键，缺一个就 NPE。

    中文用法用量（"口服 0.5g 每日2次"）是给关系型表用的展示文本，**不能**放进
    ``dosage``：它既不是剂量结构，也就没有 Synthea 要的键。它留在术语表的
    ``dosage_zh`` 里，由导出层按药品编码去药品字典取。

    ``chronic`` 跟着 IR 状态的 kind 走：慢性病状态下长期用药要标成
    chronic，Synthea 才会让这条医嘱跨就诊持续有效（导出成
    MedicationRequest 的 ``status=active``），而不是开一次就结束。
    """
    med = ctx.term.medication(action.drug)
    dosage = action.dosage or med.dosage

    prescription: dict = {"refills": 0}
    if dosage is not None:
        prescription["dosage"] = dosage.to_gmf()

    return [
        (
            "",
            {
                "type": "MedicationOrder",
                "codes": [med.code.to_gmf()],
                "prescription": prescription,
                "administration": False,
                "chronic": ctx.ir_state.kind == "chronic",
            },
        )
    ]


def emit_procedure(action: ProcedureAction, ctx: EmitContext) -> EmitResult:
    """一项操作/检查。"""
    proc = ctx.term.procedure(action.term)
    return [("", {"type": "Procedure", "codes": [proc.code.to_gmf()]})]


def emit_care_plan(action: CarePlanAction, ctx: EmitContext) -> EmitResult:
    """一份照护计划（生活方式干预、健康教育之类）。"""
    plan = ctx.term.local(action.name_zh)
    body: dict = {"type": "CarePlanStart", "codes": [plan.to_gmf()]}
    if action.activities_zh:
        body["activities"] = [ctx.term.local(a).to_gmf() for a in action.activities_zh]
    return [("", body)]


EMITTERS: dict[str, Callable[..., EmitResult]] = {
    "encounter": emit_encounter,
    "referral": emit_referral,
    "observation": emit_observation,
    "medication": emit_medication,
    "procedure": emit_procedure,
    "care_plan": emit_care_plan,
}

ActionType = Union[
    EncounterAction,
    ReferralAction,
    ObservationAction,
    MedicationAction,
    ProcedureAction,
    CarePlanAction,
]