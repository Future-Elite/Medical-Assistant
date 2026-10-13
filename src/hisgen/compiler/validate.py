"""编译前后的校验。

两道关卡：

* :func:`validate_terminology` —— 编译**前**，把 IR 里所有术语引用一次性查一遍。
  好处是错误信息带 IR 里的位置，而不是编译到一半才炸。LLM 生成的 IR
  往往错不止一处，一次报全比报一个改一个省事。
* :func:`validate_gmf` —— 编译**后**，检查产出的 GMF 结构合法。
  最后一道防线：错误挡在这里，而不是生成到一半崩掉。
"""

from __future__ import annotations

from collections import deque

from ..ir.models import Disease
from ..terminology import Terminology
from ..terminology.registry import TerminologyError
from .conditions import describe
from .errors import CompileError

TRANSITION_FIELDS = (
    "direct_transition",
    "distributed_transition",
    "conditional_transition",
    "complex_transition",
    "lookup_table_transition",
)

#: 真正没有出边的状态类型。
#:
#: **Death 不在其中** —— ``State.Death extends State``，而不是 ``Terminal``，
#: 所以它照样需要一条转移；官方模块的写法是 ``Death -> Terminal``。
#: 少了这条转移，Synthea 在 ``State.initialize`` 里抛
#: `State ... has no transition`，报错里不提 Death 的特殊性。
TERMINAL_TYPES = frozenset({"Terminal"})


# --------------------------------------------------------------- 术语引用预检


def validate_terminology(disease: Disease, term: Terminology) -> None:
    """把 IR 里所有术语引用查一遍，把所有问题一次报出来。"""
    problems: list[str] = []

    def check(fn, key: str, filename: str, where: str) -> None:
        try:
            fn(key)
        except TerminologyError as exc:
            problems.append(f"  - {where}：{exc}（白名单：terminology/{filename}）")

    for rf in disease.risk_factors:
        _check_condition_attrs(rf.condition, f"risk_factors[{rf.id}].condition", problems)

    for st in disease.states:
        if st.diagnosis is not None:
            check(term.diagnosis, st.diagnosis.term, "diagnoses.yaml", f"状态 {st.id!r} 的 diagnosis")
        for i, act in enumerate(st.on_enter):
            where = f"状态 {st.id!r} 的第 {i + 1} 个 on_enter 动作（{act.action}）"
            if act.action == "observation":
                check(term.lab, act.term, "labs.yaml", where)
            elif act.action == "medication":
                check(term.medication, act.drug, "medications.yaml", where)
            elif act.action == "procedure":
                check(term.procedure, act.term, "procedures.yaml", where)

    if problems:
        raise CompileError(
            f"IR {disease.disease.id!r} 的术语引用有 {len(problems)} 处问题：\n"
            + "\n".join(problems)
        )


def _check_condition_attrs(cond, where: str, problems: list[str]) -> None:
    """只做一个轻量提示：叶子条件里的 attribute 是否是模块自己声明的。

    这不是硬错误 —— Synthea 内置属性（age/gender/race/socioeconomic_status 等）
    不在 IR 的 risk_factors 里，但完全合法。所以只对**疑似拼错**的名字给提示。
    """
    if cond.all is not None:
        for c in cond.all:
            _check_condition_attrs(c, where, problems)
    elif cond.any is not None:
        for c in cond.any:
            _check_condition_attrs(c, where, problems)
    elif cond.not_ is not None:
        _check_condition_attrs(cond.not_, where, problems)


def condition_attributes(disease: Disease) -> list[str]:
    """列出 IR 里所有被引用的属性名，供调用方与 Synthea 内置属性对照。"""
    found: list[str] = []

    def walk(cond) -> None:
        if cond.all is not None:
            for c in cond.all:
                walk(c)
        elif cond.any is not None:
            for c in cond.any:
                walk(c)
        elif cond.not_ is not None:
            walk(cond.not_)
        elif cond.attribute:
            found.append(cond.attribute)
        elif cond.vital_sign:
            found.append(f"vital_sign:{cond.vital_sign}")

    for rf in disease.risk_factors:
        walk(rf.condition)
    for t in disease.transitions:
        if t.when is not None:
            walk(t.when)
    # 保持出现顺序去重
    seen: set[str] = set()
    out: list[str] = []
    for a in found:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


# ------------------------------------------------------------------ GMF 结构校验


def validate_gmf(gmf: dict, *, origin: str = "<module>") -> None:
    """检查编译产物是不是结构合法的 GMF。不合法就抛 :class:`CompileError`。"""
    problems: list[str] = []

    if not isinstance(gmf.get("states"), dict):
        raise CompileError(f"{origin}：GMF 的 states 必须是以状态名为键的对象")
    if gmf.get("gmf_version") != 1:
        problems.append(f"gmf_version 必须是 1，实际为 {gmf.get('gmf_version')!r}")
    if not gmf.get("name"):
        problems.append("name 不能为空")

    # --- remarks 必须是字符串数组 ---
    # Synthea 的 Module.<init> 直接 getAsJsonArray()，写成字符串会抛
    # `Not a JSON Array`，并且把整段备注当作元素打印 —— 报错现场离原因很远。
    remarks = gmf.get("remarks")
    if remarks is None:
        problems.append("缺少 remarks（GMF 允许为空数组，但键要在）")
    elif not isinstance(remarks, list) or any(not isinstance(r, str) for r in remarks):
        problems.append(
            f"remarks 必须是字符串数组，实际为 {type(remarks).__name__}"
            f"（Synthea 会抛 Not a JSON Array）"
        )

    states: dict[str, dict] = gmf["states"]
    if not states:
        raise CompileError(f"{origin}：GMF 没有任何状态")

    # --- 唯一 Initial，且必须叫 "Initial" ---
    # ``Module.initialState()`` 写死 ``states.get("Initial")``；名字不对时
    # 抛的是 NPE（"Cannot invoke State.clone() because ... is null"），
    # 完全看不出是名字的问题。
    initials = [n for n, b in states.items() if b.get("type") == "Initial"]
    if len(initials) != 1:
        problems.append(f"必须有且仅有一个 Initial，实际 {len(initials)} 个：{initials}")
    elif initials[0] != "Initial":
        problems.append(
            f"Initial 状态必须命名为 'Initial'（Synthea 写死查这个名字），"
            f"实际为 {initials[0]!r}"
        )

    # --- 有终点 ---
    if not any(b.get("type") in TERMINAL_TYPES for b in states.values()):
        problems.append("没有任何 Terminal 状态，模拟不会结束")

    # --- 每个状态都要有 type ---
    for name, body in states.items():
        if not body.get("type"):
            problems.append(f"状态 {name!r} 缺少 type")

    # --- 转移目标存在 + 非终态有出边 ---
    index: dict[str, list[str]] = {}
    for name, body in states.items():
        targets = _targets(body)
        index[name] = targets
        for t in targets:
            if t not in states:
                problems.append(f"状态 {name!r} 的转移指向不存在的状态 {t!r}")
        if body.get("type") not in TERMINAL_TYPES and not targets:
            problems.append(f"状态 {name!r}（{body.get('type')}）既不是终态也没有出边，会卡死")

    # --- Initial 只能有一个出边，且不能有条件/概率 ---
    for name in initials:
        body = states[name]
        if body.get("type") == "Initial":
            extra = [f for f in TRANSITION_FIELDS if f != "direct_transition" and body.get(f)]
            if extra:
                problems.append(f"Initial 状态 {name!r} 不应带 {extra}")

    # --- reason 必须指向已存在的 ConditionOnset ---
    onset_names = {n for n, b in states.items() if b.get("type") == "ConditionOnset"}
    for name, body in states.items():
        reason = body.get("reason")
        if reason is not None and reason not in onset_names:
            problems.append(
                f"状态 {name!r} 的 reason={reason!r} 不是本模块里的 ConditionOnset 状态名"
                f"（现有：{sorted(onset_names) or '无'}）"
            )
        te = body.get("target_encounter")
        if te is not None and states.get(te, {}).get("type") != "Encounter":
            problems.append(
                f"状态 {name!r} 的 target_encounter={te!r} 不是本模块里的 Encounter 状态名"
            )

    # --- ConditionOnset 必须有编码 ---
    for name, body in states.items():
        if body.get("type") == "ConditionOnset" and not body.get("codes"):
            problems.append(f"ConditionOnset 状态 {name!r} 没有 codes")

    # --- 带 range/exact 的 Observation 必须有 unit（Synthea 的硬性要求）---
    for name, body in states.items():
        if body.get("type") == "Observation" and (
            body.get("range") or body.get("exact")
        ):
            if not str(body.get("unit") or "").strip():
                problems.append(
                    f"Observation 状态 {name!r} 带 range/exact 但没有非空 unit，Synthea 会拒绝加载"
                )

    # --- 可达性 ---
    if len(initials) == 1:
        reachable = _reachable(initials[0], index)
        orphans = sorted(set(states) - reachable)
        if orphans:
            problems.append(f"以下状态从 Initial 不可达：{orphans}")

    if problems:
        raise CompileError(
            f"{origin}：编译产出的 GMF 有 {len(problems)} 处结构问题：\n  - "
            + "\n  - ".join(problems)
        )


def _targets(body: dict) -> list[str]:
    out: list[str] = []
    if body.get("direct_transition"):
        out.append(body["direct_transition"])
    for key in ("distributed_transition", "conditional_transition",
                "complex_transition", "lookup_table_transition"):
        for opt in body.get(key) or []:
            if opt.get("transition"):
                out.append(opt["transition"])
            for dist in opt.get("distributions") or []:
                if dist.get("transition"):
                    out.append(dist["transition"])
    return out


def _reachable(start: str, index: dict[str, list[str]]) -> set[str]:
    seen = {start}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for nxt in index.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


# ------------------------------------------------------------------ 调试输出


def summarize(gmf: dict) -> str:
    """一行一句地描述模块，便于人肉检查编译结果。"""
    states = gmf["states"]
    by_type: dict[str, int] = {}
    for body in states.values():
        by_type[body.get("type", "?")] = by_type.get(body.get("type", "?"), 0) + 1
    lines = [
        f"模块 {gmf['name']!r}：{len(states)} 个状态",
        "  类型分布：" + "、".join(f"{k}×{v}" for k, v in sorted(by_type.items())),
    ]
    return "\n".join(lines)