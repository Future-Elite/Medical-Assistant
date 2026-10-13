"""从 IR 里把**每一个数字和它声称的出处**拉成一张表，供人逐条核对。

这是整套方案里人对数值负责的落点。设计文档 §5.3 说 ``source`` 必填、
LLM 从"权威"降级为"检索整理助手" —— 那么就必须有一张表让人真的能核。

纯函数：不调模型、不联网。可以拿任何一份 IR 单独跑。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..compiler.conditions import describe, literal
from ..ir.models import Disease
from .prompts import UNVERIFIED_MARKER


@dataclass(frozen=True)
class Entry:
    """一条待核对项。"""

    #: 在 IR 里的位置，例如 ``transitions[healthy → prediabetes]``
    where: str
    #: 那个数字本身
    value: str
    #: 出处原文
    source: str

    @property
    def unverified(self) -> bool:
        return self.source.strip().startswith(UNVERIFIED_MARKER)


def collect(disease: Disease) -> list[Entry]:
    """按 IR 的书写顺序收集所有带 source 的条目。"""
    out: list[Entry] = []

    out.append(Entry("disease", disease.disease.name_zh, disease.disease.source))

    for rf in disease.risk_factors:
        out.append(
            Entry(
                f"risk_factors[{rf.id}]",
                f"{rf.name_zh}：{rf.attribute} = {literal(rf.value)}",
                rf.source,
            )
        )

    for st in disease.states:
        if st.diagnosis is not None:
            out.append(
                Entry(
                    f"states[{st.id}].diagnosis",
                    f"诊断 {st.diagnosis.term}",
                    st.diagnosis.source,
                )
            )
        for i, act in enumerate(st.on_enter):
            where = f"states[{st.id}].on_enter[{i}]（{act.action}）"
            if act.action == "observation":
                value = act.term
                if act.range is not None:
                    low, high = act.range
                    value = f"{act.term} 参考区间 {low:g}–{high:g}"
                if act.unit_zh:
                    value = f"{value}，单位 {act.unit_zh}"
                out.append(Entry(where, value, act.source))
            elif act.action == "medication":
                # 药品的标准用法用量不在 IR 里（见 MedicationAction 的说明），
                # 它有自己的 source，在 medications.yaml 里单列。
                out.append(Entry(where, act.drug, act.source))
            elif act.action == "procedure":
                out.append(Entry(where, act.term, act.source))
            # encounter / referral / care_plan 没有 source —— 它们是结构而非数值

    for t in disease.transitions:
        value = f"{t.from_} → {t.to}：{t.probability:g}"
        if t.when is not None:
            value += f"，条件 {_describe_when(t.when)}"
        out.append(Entry(f"transitions[{t.from_} → {t.to}]", value, t.source))

    return out


def _describe_when(cond) -> str:
    return describe(cond)


def render(disease: Disease, *, title: str | None = None) -> str:
    """渲染成 Markdown。"""
    entries = collect(disease)
    unverified = [e for e in entries if e.unverified]

    lines = [
        f"# {title or '待核实数字清单'} —— {disease.disease.name_zh}",
        "",
        f"来源：IR `{disease.disease.id}`。本清单由 `hisgen.llm.review` 从 IR 自动生成，",
        "**每一个数字都必须有人核对过才能投产**。",
        "",
        f"共 {len(entries)} 条带出处的条目，其中 **{len(unverified)} 条标记为待核实**。",
        "",
    ]

    if unverified:
        lines += [
            "## 一、待核实（**这些必须先查原文**）",
            "",
            "| # | 位置 | 数值 | 该去哪里核对 |",
            "|---|---|---|---|",
        ]
        for i, e in enumerate(unverified, 1):
            lines.append(
                f"| {i} | `{e.where}` | {e.value} "
                f"| {_strip_marker(e.source)} |"
            )
        lines.append("")
    else:
        lines += ["## 一、待核实", "", "本 IR 没有标记为待核实的条目。", ""]

    rest = [e for e in entries if not e.unverified]
    lines += [
        "## 二、已声明出处的条目（**仍需抽查**）",
        "",
        "| # | 位置 | 数值 | 出处 |",
        "|---|---|---|---|",
    ]
    for i, e in enumerate(rest, 1):
        lines.append(f"| {i} | `{e.where}` | {e.value} | {e.source} |")
    lines.append("")

    lines += [
        "---",
        "",
        "## 核对方法",
        "",
        "1. 先过第一节。每一条都要打开它给出的文件与章节，确认数值无误。",
        f"2. 核对完成后，把 `source` 里的 `{UNVERIFIED_MARKER}` 前缀删掉，"
        "并补上具体的章节名。",
        "3. 再过第二节。这些条目声称有出处，但**声称不等于核实**，",
        "按比例抽查；抽查发现问题说明整份 IR 都需要重过。",
        "4. 改完 IR 后重新编译（`python -m hisgen.compiler --ir <IR>`），",
        "编译器会重新做引用完整性检查。",
    ]
    return "\n".join(lines)


def _strip_marker(source: str) -> str:
    return source.strip().removeprefix(UNVERIFIED_MARKER).strip()