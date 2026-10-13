"""临床路径 IR 的 pydantic 模型。

三条硬约束（见设计文档 §5.3）在这里落地：
  1. ``source`` 必填 —— 任何概率、参考区间、诊断都必须能追到出处
  2. ``when`` 是结构化条件表达式，不是 Groovy 字符串
  3. ``action`` 是受控词表（``Literal``），LLM 不能自创新动词

额外的第四条（§5.4 编码白名单的落地方式）：
  **IR 里不出现任何码值。** 诊断/检验/药品/操作一律用 ``term`` 引用
  ``terminology/*.yaml`` 中的键，由编译器查表换成码。
  这样 LLM 根本没有编造码值的机会，而不是靠事后校验去抓。
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Optional, Self, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IRError(ValueError):
    """IR 内容不合法。"""


#: IR 状态 id 的命名规范。``__`` 是编译器内部的命名空间分隔符，禁止出现在 id 中。
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

Operator = Literal[">=", "<=", ">", "<", "==", "!="]

EncounterClass = Literal[
    "ambulatory",
    "outpatient",
    "inpatient",
    "emergency",
    "urgentcare",
    "virtual",
    "home",
    "hospice",
    "snf",
]


class Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class Condition(Base):
    """结构化条件表达式。

    两种形态，二选一（由 ``_exactly_one_form`` 强制）：

    * 组合式：``all`` / ``any`` / ``not`` 之一
    * 叶子式：``attribute`` 或 ``vital_sign``，配 ``operator`` + ``value``

    例::

        when: { all: [ {attribute: age, operator: ">=", value: 45},
                       {not: {vital_sign: BMI, operator: ">=", value: 28}} ] }

    叶子有三种，对应 Synthea 的三类判定：

    ``{attribute: age, ...}``
        编译成 ``Logic.Age``。年龄不是 ``person.attributes`` 里的键，
        是实时算出来的，只能用专门的条件类型。

    ``{attribute: gender, ...}`` / ``{attribute: has_t2dm, ...}``
        编译成 ``Logic.Attribute``。指向 Synthea 内置属性
        （``gender`` / ``race`` / ``socioeconomic_status`` …）
        或本模块 ``risk_factors`` 声明的属性。

    ``{vital_sign: BMI, ...}``
        编译成 ``Logic.VitalSign``。指向 Synthea 自己算出来的体征，
        用这个就不用为了拿 BMI 去造属性。可用的有
        ``BMI`` / ``Systolic Blood Pressure`` / ``Blood Glucose`` / ``Creatinine`` 等。
    """

    # 自引用必须写成 Optional["Condition"]，不能写成 "Condition" | None：
    # 有了 `from __future__ import annotations`，后者会先被求值成 str | None
    # 而在类创建时直接 TypeError。泛型别名（list[...]）不受影响。
    all: Optional[list["Condition"]] = None
    any: Optional[list["Condition"]] = None
    not_: Optional["Condition"] = Field(default=None, alias="not")
    attribute: str | None = None
    vital_sign: str | None = None
    operator: Operator | None = None
    value: float | int | str | bool | None = None

    @model_validator(mode="after")
    def _exactly_one_form(self) -> Self:
        combinators = [
            self.all is not None,
            self.any is not None,
            self.not_ is not None,
        ]
        leaf_kinds = [self.attribute is not None, self.vital_sign is not None]
        if sum(combinators) + (1 if any(leaf_kinds) else 0) != 1:
            raise IRError(
                "条件必须恰好是 all / any / not 之一，或一个叶子"
                "（attribute 或 vital_sign，配 operator + value）"
            )
        if any(leaf_kinds):
            if self.operator is None or self.value is None:
                raise IRError("叶子条件必须同时给出 operator 和 value")
            if all(leaf_kinds):
                raise IRError("叶子条件不能同时给 attribute 和 vital_sign")
        else:
            if self.operator is not None or self.value is not None:
                raise IRError("组合条件不能同时带 operator / value")
        for name, children in (("all", self.all), ("any", self.any)):
            if children is not None and len(children) == 0:
                raise IRError(f"{name} 不能为空列表")
        return self


Condition.model_rebuild()


class TermRef(Base):
    """对 ``terminology/*.yaml`` 中某个条目的引用。"""

    term: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    source: str = Field(min_length=1)


class DiseaseMeta(Base):
    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    name_zh: str = Field(min_length=1)
    name_en: str = ""
    source: str = Field(min_length=1)


class Defaults(Base):
    """模块级默认值。"""

    #: 出边概率之和不足 1 时，剩余概率走「等这么久之后重新评估」的回环。
    followup_interval_days: int = Field(default=365, gt=0)


class RiskFactor(Base):
    """一个危险因素，编译成 ``SetAttribute``，供 ``when`` 里的叶子条件使用。"""

    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    name_zh: str = Field(min_length=1)
    #: Synthea 属性名，例如 bmi / has_obesity
    attribute: str = Field(min_length=1)
    #: 满足条件时把 attribute 设成这个值
    value: bool | int | float | str
    condition: Condition
    source: str = Field(min_length=1)


# --------------------------------------------------------------------------
# on_enter 动作。action 是判别字段，编译器按它选 emitter。
# --------------------------------------------------------------------------


class EncounterAction(Base):
    """一次就诊。``department_zh`` 会成为 FHIR ``Encounter.type`` 的 display。"""

    action: Literal["encounter"]
    encounter_class: EncounterClass = Field(alias="class")
    department_zh: str = Field(min_length=1)


class ObservationAction(Base):
    """一项检验/观察。``term`` 必须是 ``terminology/labs.yaml`` 的键。"""

    action: Literal["observation"]
    term: str = Field(min_length=1)
    source: str = Field(min_length=1)
    #: 覆盖 labs.yaml 的中文项目名
    name_zh: str | None = None
    #: 覆盖 labs.yaml 的单位
    unit_zh: str | None = None
    #: 覆盖 labs.yaml 的参考区间
    range: tuple[float, float] | None = None

    @model_validator(mode="after")
    def _range_ordered(self) -> Self:
        if self.range is not None:
            low, high = self.range
            if low > high:
                raise IRError(f"参考区间下界 {low} 大于上界 {high}")
        return self


#: Synthea 认得的给药周期单位（UCUM 时间单位）。Synthea 会过 convertUcumCode。
DosageUnit = Literal["days", "weeks", "months", "hours", "minutes", "seconds"]


class Dosage(BaseModel):
    """结构化用法用量，对应 Synthea 的 ``prescription.dosage``。

    这一块**必须**是结构化的：``FhirR4.medicationRequest`` 无条件读
    ``amount`` / ``frequency`` / ``period`` / ``unit``，缺一个就 NPE，而且异常
    发生在患者线程里被 FutureTask 吞掉 —— 表现成"返回码 0、患者文件少了几个"，
    现场离原因非常远。所以这里用 pydantic 卡死形状与取值范围。

    ``amount`` 是**每次给药的单位数**（1 片、1 支），不是规格：
    "0.5g" 属于药品字典里的展示文本，不进这里。
    """

    model_config = ConfigDict(extra="forbid")

    #: 每次给药的单位数
    amount: float = Field(gt=0)
    #: 每个周期给药次数
    frequency: int = Field(gt=0)
    #: 周期长度
    period: float = Field(gt=0)
    #: 周期单位，UCUM
    unit: DosageUnit = "days"

    def to_gmf(self) -> dict:
        return {
            "amount": self.amount,
            "frequency": self.frequency,
            "period": self.period,
            "unit": self.unit,
        }


class MedicationAction(Base):
    """一条用药医嘱。``drug`` 是 ``terminology/medications.yaml`` 的键。

    用法用量**不在这里写**：一个药品的标准用法是该药品的属性，不是某次临床
    决策的属性，所以它在 ``medications.yaml`` 的 ``default_dosage`` 里，全项目
    一份。要覆盖就写结构化的 ``dosage``，别写自由文本 —— Synthea 的
    ``prescription.dosage`` 要的是 ``{amount, frequency, period, unit}``，
    往那儿塞一句"口服 0.5g 每日2次"会让 FhirR4 直接 NPE。
    """

    action: Literal["medication"]
    drug: str = Field(min_length=1)
    source: str = Field(min_length=1)
    #: 覆盖 medications.yaml 的 default_dosage
    dosage: Dosage | None = None


class ProcedureAction(Base):
    """一项操作/检查。``term`` 是 ``terminology/procedures.yaml`` 的键。"""

    action: Literal["procedure"]
    term: str = Field(min_length=1)
    source: str = Field(min_length=1)


class CarePlanAction(Base):
    """一份照护计划（非药物干预）。"""

    action: Literal["care_plan"]
    name_zh: str = Field(min_length=1)
    activities_zh: list[str] = []


class ReferralAction(Base):
    """转诊到另一个科室 —— 本质上是一次独立的就诊。"""

    action: Literal["referral"]
    department_zh: str = Field(min_length=1)
    encounter_class: EncounterClass = Field(default="ambulatory", alias="class")


ActionSpec = Annotated[
    Union[
        EncounterAction,
        ObservationAction,
        MedicationAction,
        ProcedureAction,
        CarePlanAction,
        ReferralAction,
    ],
    Field(discriminator="action"),
]


class Repeat(Base):
    """随访回环。``interval_days`` 天后回到本状态重新评估。"""

    interval_days: int = Field(gt=0)


class State(Base):
    id: str = Field(min_length=1)
    name_zh: str = Field(min_length=1)

    #: initial  —— 人群入口（有且仅有一个）
    #: chronic  —— 长期共存状态，通常带 repeat
    #: acute    —— 一次性事件
    #: terminal —— 终点
    kind: Literal["initial", "chronic", "acute", "terminal"]

    #: 本状态对应的诊断。``term`` 引用 terminology/diagnoses.yaml。
    diagnosis: TermRef | None = None
    repeat: Repeat | None = None
    on_enter: list[ActionSpec] = []

    @model_validator(mode="after")
    def _terminal_is_bare(self) -> Self:
        if self.kind == "terminal":
            if self.on_enter:
                raise IRError(f"终态 {self.id} 不应有 on_enter 动作")
            if self.diagnosis is not None:
                raise IRError(f"终态 {self.id} 不应有 diagnosis")
            if self.repeat is not None:
                raise IRError(f"终态 {self.id} 不应有 repeat")
        return self


class Transition(Base):
    """一条转移。

    ``probability`` 是**每次评估**的发生概率，而评估频率由源状态的
    ``repeat.interval_days`` 决定。二者不一致时结果会静默地差好几倍：

    源状态每 90 天回环一次，而 ``probability`` 填的是「年发生率 0.05」，
    实际年发生率就变成 ``1-(1-0.05)^4 ≈ 18.5%`` —— 十年下来几乎人人中招。
    这种错误看不出来，所以要么让 ``probability`` 就是每次评估的值，
    要么用 ``rate_period_days`` 说明这个概率是多长时间口径的，由编译器换算。
    """

    from_: str = Field(alias="from")
    to: str = Field(min_length=1)
    probability: float = Field(ge=0.0, le=1.0)
    when: Condition | None = None
    source: str = Field(min_length=1)
    #: ``probability`` 的时间口径（天）。例如指南给的是年发生率就写 365，
    #: 编译器会按源状态的 ``repeat.interval_days`` 折算成每次评估的概率。
    #: 不写表示"这个数就是每次评估的概率"。
    rate_period_days: int | None = Field(default=None, gt=0)


class Disease(Base):
    disease: DiseaseMeta
    defaults: Defaults = Defaults()
    risk_factors: list[RiskFactor] = []
    states: list[State]
    transitions: list[Transition]

    @model_validator(mode="after")
    def _graph_is_sane(self) -> Self:
        # --- 状态 id ---
        seen: set[str] = set()
        for st in self.states:
            if not ID_PATTERN.match(st.id):
                raise IRError(f"状态 id 必须匹配 {ID_PATTERN.pattern}，实际为 {st.id!r}")
            if "__" in st.id:
                raise IRError(f"状态 id 不能包含 '__'（编译器保留分隔符）：{st.id!r}")
            if st.id in seen:
                raise IRError(f"状态 id 重复：{st.id!r}")
            seen.add(st.id)

        # --- 入口唯一 ---
        initials = [s.id for s in self.states if s.kind == "initial"]
        if len(initials) != 1:
            raise IRError(
                f"必须有且仅有一个 initial 状态，实际 {len(initials)} 个：{initials}"
            )

        # --- 必须有终点 ---
        if not any(s.kind == "terminal" for s in self.states):
            raise IRError("必须至少有一个 terminal 状态，否则模拟不会结束")

        # --- 风险因素 ---
        rf_ids = [r.id for r in self.risk_factors]
        if len(set(rf_ids)) != len(rf_ids):
            raise IRError(f"risk_factors id 重复：{rf_ids}")
        rf_attrs = [r.attribute for r in self.risk_factors]
        if len(set(rf_attrs)) != len(rf_attrs):
            raise IRError(f"risk_factors 的 attribute 重复：{rf_attrs}")

        # --- 转移端点必须存在 ---
        for t in self.transitions:
            if t.from_ not in seen:
                raise IRError(f"转移引用了不存在的源状态：{t.from_!r}")
            if t.to not in seen:
                raise IRError(f"转移引用了不存在的目标状态：{t.to!r}")

        # --- 非终态必须有出边 ---
        has_out = {t.from_ for t in self.transitions}
        for st in self.states:
            if st.kind != "terminal" and st.id not in has_out:
                raise IRError(f"状态 {st.id!r} 不是终态，但没有出边，模拟会卡死")

        # --- 同一源状态的非条件出边概率之和不能超过 1 ---
        from collections import defaultdict

        grouped: dict[str, list[Transition]] = defaultdict(list)
        for t in self.transitions:
            grouped[t.from_].append(t)
        for sid, edges in grouped.items():
            plain = sum(t.probability for t in edges if t.when is None)
            if plain > 1.0 + 1e-9:
                raise IRError(
                    f"状态 {sid!r} 的无条件出边概率之和为 {plain:.4f}，超过 1.0"
                )

        # --- rate_period_days 只对会重复评估的状态有意义 ---
        # 不重复的状态只评估一次，"年发生率"与"每次发生率"无从区分，
        # 写了只会让人以为做了换算。
        by_id = {st.id: st for st in self.states}
        for t in self.transitions:
            if t.rate_period_days is None:
                continue
            src = by_id.get(t.from_)
            if src is None or src.repeat is None:
                raise IRError(
                    f"转移 {t.from_!r} → {t.to!r} 标了 rate_period_days="
                    f"{t.rate_period_days}，但状态 {t.from_!r} 没有 repeat —— "
                    "只评估一次的状态无所谓时间口径，去掉这个字段"
                )

        return self