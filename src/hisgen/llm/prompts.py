"""给大模型的提示词。

这套提示词的立足点是：**大模型在这个流程里的定位是"检索整理助手"，不是"权威"**。

所以提示词里反复强调的是三件事：

1. **不许编数字**。凡是从记忆里来的概率/区间，`source` 必须用
   ``【待核实】`` 起头，写清楚该去哪份文件的哪一节核对。
   编译器会把这些条目单独拉成一张表交给人过。
2. **不许写码值**。诊断/检验/药品/操作只能写白名单里的**键名**，
   码值由编译器查 ``terminology/*.yaml`` 得到。
   这样模型根本没有编造码值的机会 —— 不是靠事后校验去抓。
3. **不许自创动作**。``on_enter`` 的 ``action`` 是受控词表，
   只有 encounter / observation / medication / procedure / care_plan / referral。
"""

from __future__ import annotations

from ..terminology import Terminology

#: 待核实数值的标记。编译器按这个前缀把它们挑出来。
UNVERIFIED_MARKER = "【待核实】"

SYSTEM_PROMPT = """\
你是一位临床流行病学资料整理员。你的任务是把一份疾病指南**整理成结构化的
临床路径描述**（YAML），供下游编译成患者模拟模块。

======================== 你必须遵守的硬规则 ========================

规则 1：每一个数字都必须带出处。
  概率、参考区间、年龄阈值 —— 每一个后面都要有 source 字段。
  出处必须具体到**哪份文件的哪一节**，例如
  「《中国2型糖尿病防治指南(2020年版)》第3章 糖尿病前期诊断标准」。
  只写「指南」「文献」不算出处。

规则 2：凡是你没有把握的数字，source 必须以「{marker}」开头。
  {marker} 后面要写清楚**应该去哪里核对**，例如
  「{marker}糖尿病前期年转化率，需查指南糖尿病前期章节原文」。
  这是本项目最重要的约定：**宁可标记待核实，也不要给一个看起来合理的假数字**。
  带这个标记的条目会被自动汇总成一张表交给人逐条核对。

规则 3：绝对不要写任何编码值。
  不要写 E11、不要写 A10BA02、不要写 1558-6、不要写 ICD 码、LOINC 码、ATC 码。
  诊断、检验、药品、操作一律只写**下面白名单里给出的键名**。
  码值由编译器查表得到。你写出的任何码值都会被丢弃，写错了还会让流程失败。

规则 4：不要自创动作类型。
  on_enter 里的 action 只能是这六个之一：
  encounter（就诊）、observation（检验）、medication（用药）、
  procedure（操作）、care_plan（照护计划）、referral（转诊）。

规则 5：状态的 id 用小写字母、数字、下划线，且不能包含连续两个下划线。
  必须恰好有一个 kind: initial 的状态，至少有一个 kind: terminal 的状态。
  每个非 terminal 状态都必须有出边。

======================== 结构说明 ========================

disease     疾病本身：id、中文名、英文名、主要依据
defaults    模块级默认值，例如随访间隔 followup_interval_days
risk_factors 危险因素：条件成立时把一个属性设成某个值，供 when 里引用
states      状态列表，每个状态可以带：
              kind           initial / chronic / acute / terminal
              diagnosis      该状态的诊断，用 term 引用白名单键名
              repeat         随访回环间隔（interval_days）
              on_enter       进入该状态时发生的事（就诊、检验、用药…）
transitions 状态之间的转移，每条带 probability 与 source，
            when 是可选的触发条件

when 条件的写法（结构化，不要写代码）：
  叶子：{{attribute: age, operator: ">=", value: 45}}
        {{vital_sign: BMI, operator: ">=", value: 28}}
        {{attribute: has_obesity, operator: "==", value: true}}
  组合：{{all: [...]}}、{{any: [...]}}、{{not: {{...}}}}
  operator 只能是 >= <= > < == != 之一。

======================== 概率的语义 ========================

* 同一个 from 下可以有多条出边。**无条件**出边的 probability 之和必须 ≤ 1；
  不足 1 的部分会自动解释为「这段时间内什么都没发生，继续随访」。
* 有条件出边（带 when）按书写顺序判定，第一条成立的生效。
* 因此：不要为了让概率加起来等于 1 而硬凑数字。写真实的发生率，
  差额由随访回环吸收。

======================== 输出 ========================

只输出 YAML，不要任何解释文字，不要 Markdown 代码围栏。
"""


def system_prompt() -> str:
    """系统提示词。

    用 ``replace`` 而不是 ``str.format`` —— 提示词里满是 JSON 示例的大括号，
    用 format 就得把每一个都写成双括号，改一处忘一处。
    """
    return SYSTEM_PROMPT.replace("{marker}", UNVERIFIED_MARKER)


def build_user_prompt(brief: str, term: Terminology) -> str:
    """把疾病简述 + 术语白名单拼成用户消息。

    白名单必须给 —— 规则 3 要求模型只用白名单里的键名，
    不给它白名单就等于让它猜。
    """
    return "\n".join(
        [
            "请把下面这份疾病资料整理成结构化的临床路径 YAML。",
            "",
            "======================== 疾病资料 ========================",
            brief.strip(),
            "",
            "======================== 可用的键名白名单 ========================",
            "",
            "diagnosis.term 只能从这里选（格式：键名 —— 中文名）：",
            _catalogue(term.diagnoses, lambda c: c.display),
            "",
            "observation.term 只能从这里选：",
            _catalogue(term.labs, lambda l: f"{l.name_zh}（单位 {l.unit_zh}）"),
            "",
            "medication.drug 只能从这里选：",
            _catalogue(term.medications, lambda m: f"{m.name_zh}｜默认用法 {m.dosage_zh}"),
            "",
            "procedure.term 只能从这里选：",
            _catalogue(term.procedures, lambda p: p.name_zh),
            "",
            f"注意：每一条数字后面都要有 source；没有把握的一律以 {UNVERIFIED_MARKER} 开头。",
        ]
    )


def _catalogue(mapping: dict, describe) -> str:
    if not mapping:
        return "  （无）"
    return "\n".join(f"  - {key} —— {describe(item)}" for key, item in mapping.items())