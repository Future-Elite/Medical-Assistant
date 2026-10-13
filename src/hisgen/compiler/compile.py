"""IR -> GMF 编译主体。

编译器负责的机械职责（这些正是不该交给大模型的部分）：

* 拓扑与连线：把每个 IR 状态展开成一条线性状态链，用 ``direct_transition`` 串起来
* 自动插入：``EncounterEnd``、``Delay``、末尾的决定状态
* 引用完整性：``reason`` 指向本状态的 ``ConditionOnset``；
  ``ConditionOnset.target_encounter`` 指向本状态的 ``Encounter``
* 概率归一：出边概率之和不足 1 时，剩余概率走 ``Delay`` 回环，
  于是每个状态的分支之和恒为 1
* 结构合法性：注入 ``gmf_version`` / ``name`` / ``remarks``

链的形状（以带就诊的慢性状态为例）::

    <id>__onset  ->  <id>__act0 (Encounter)  ->  <id>__act1 (Observation)
                 ->  <id>__encend0  ->  <id>__tail
                                            ├─ distributed_transition / complex_transition
                                            └─(剩余概率)─> <id>__delay -> 回环落点

命名规则是确定性的：``<IR状态id>__<后缀>``。因为 IR 校验时已禁止 id 里出现
``__``，所以不会撞名 —— 不需要去重器，也就不需要去重器出错的可能。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
import json

from ..ir.models import Disease, RiskFactor, State, Transition
from ..terminology import Terminology
from ..terminology.registry import TerminologyError
from .conditions import describe, to_logic
from .emitters import EMITTERS, EmitContext
from .errors import CompileError

GMF_VERSION = 1

#: 浮点比较容差
EPS = 1e-9

#: 状态名后缀
#: Initial 状态的名字。Synthea 在 ``Module.initialState()`` 里写死查这个名字，
#: 不能跟着 ``<id>__<后缀>`` 的命名规则走。
INITIAL_STATE = "Initial"
SFX_ONSET = "onset"
SFX_ENCEND = "encend"
SFX_DELAY = "delay"
SFX_TAIL = "tail"
SFX_DEATH = "death"
SFX_TERMINAL = "terminal"

#: Synthea 中带 ``reason`` 字段、且会把它解析成 ConditionOnset 名的状态类型。
#: 只往这些类型上挂 reason，避免给不认识该字段的状态塞多余属性。
REASON_STATES = frozenset(
    {"Encounter", "MedicationOrder", "Procedure", "CarePlanStart", "ImagingStudy"}
)

#: 回环落点要跳过的类型：重复进入 Initial 无意义，重复 ConditionOnset 会重复开诊断
LOOP_SKIP = frozenset({"Initial", "ConditionOnset", "Death", "Terminal"})


@dataclass
class ModuleBuilder:
    """收集 GMF 状态并产出最终模块 JSON。"""

    name: str
    #: GMF 的 remarks 是**字符串数组**，不是字符串。写成一个字符串会让
    #: Synthea 在 Module.<init> 里抛 `Not a JSON Array`，而且报错信息把
    #: 整段备注当元素打印出来，一眼看不出是哪个字段的问题。
    remarks: list[str] = field(default_factory=list)
    states: dict[str, dict] = field(default_factory=dict)

    def add(self, state_name: str, body: dict) -> str:
        if state_name in self.states:
            raise CompileError(
                f"状态名冲突：{state_name!r}。"
                "这通常意味着 IR 里有重复的 id 或非法的动作序号。"
            )
        self.states[state_name] = body
        return state_name

    def gmf(self) -> dict:
        return {
            "name": self.name,
            "remarks": self.remarks,
            "gmf_version": GMF_VERSION,
            "states": self.states,
        }


# --------------------------------------------------------------------------- 入口


def compile_module(
    disease: Disease,
    term: Terminology,
    *,
    module_name: str | None = None,
    remarks: str | Sequence[str] | None = None,
) -> dict:
    """把一份 IR 编译成 Synthea GMF 模块（一个 dict）。

    ``remarks`` 允许传单个字符串（会包装成单元素数组）或字符串序列。
    """
    mb = ModuleBuilder(
        name=module_name or disease.disease.id,
        remarks=_as_remarks(remarks) if remarks else _default_remarks(disease),
    )

    chains: dict[str, list[tuple[str, dict]]] = {}
    for st in disease.states:
        chains[st.id] = _build_chain(st, term, disease)

    for st in disease.states:
        _wire_chain(st, chains[st.id], disease)
        for name, body in chains[st.id]:
            mb.add(name, body)

    _attach_transitions(disease, mb, chains)

    gmf = mb.gmf()
    # 编译期最后一道自检：产出必须是结构合法的 GMF
    from .validate import validate_gmf  # 避免循环导入

    validate_gmf(gmf, origin=f"IR {disease.disease.id!r}")
    return gmf


def compile_to_file(
    disease: Disease,
    term: Terminology,
    out_path: str | Path,
    *,
    module_name: str | None = None,
    remarks: str | Sequence[str] | None = None,
) -> Path:
    """编译并写盘。返回实际写入的路径。"""
    gmf = compile_module(disease, term, module_name=module_name, remarks=remarks)
    out = Path(out_path)
    if not out.is_absolute():
        out = Path.cwd() / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(gmf, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _as_remarks(remarks: str | Sequence[str]) -> list[str]:
    """统一成 GMF 要的字符串数组。"""
    if isinstance(remarks, str):
        return [remarks]
    return [str(item) for item in remarks]


# ----------------------------------------------------------------------- 链的构造


def _cycle_probability(t: Transition, by_id: dict[str, State]) -> float:
    """把转移概率折算成**每次评估**的概率。

    ``rate_period_days`` 是概率自己的时间口径。源状态每 ``interval_days``
    回环一次，所以口径与回环周期不一致时必须换算 —— 否则「年发生率 0.05」
    挂在一个 90 天回环的状态上，实际会变成 ``1-(1-0.05)^4 ≈ 18.5%``/年，
    而且从产出数据里完全看不出来。

    换算式就是复利：把「``rate_period_days`` 天里发生一次」的概率
    ``q`` 摊到 ``cycle`` 天里。
    """
    if t.rate_period_days is None:
        return t.probability

    src = by_id.get(t.from_)
    if src is None or src.repeat is None:
        # IR 校验已经挡过这一条；这里再确认一次，避免绕过校验直接调编译器
        raise CompileError(
            f"转移 {t.from_!r} → {t.to!r} 标了 rate_period_days，"
            f"但状态 {t.from_!r} 没有 repeat"
        )

    cycle = src.repeat.interval_days
    if cycle == t.rate_period_days:
        return t.probability
    return 1.0 - (1.0 - t.probability) ** (cycle / t.rate_period_days)


def _default_remarks(disease: Disease) -> list[str]:
    return [
        f"{disease.disease.name_zh}（{disease.disease.name_en or disease.disease.id}）。"
        f"由 hisgen 从临床路径 IR 编译生成，请勿手改；"
        f"要改请改 ir/{disease.disease.id}.yaml 后重新编译。",
        f"主要依据：{disease.disease.source}",
    ]


def _build_chain(
    st: State, term: Terminology, disease: Disease
) -> list[tuple[str, dict]]:
    """把一个 IR 状态展开成线性的 GMF 状态链（此时还没连转移）。"""
    chain: list[tuple[str, dict]] = []

    if st.kind == "initial":
        # 名字必须**正好**是 "Initial"：Synthea 的 ``Module.initialState()``
        # 写死了 ``states.get("Initial")``，取不到就是 NPE，且报错里看不出
        # 是谁的名字不对。IR 校验保证只有一个 initial 状态，所以不会撞名。
        chain.append((INITIAL_STATE, {"type": "Initial"}))
        chain.extend(_risk_factor_states(st, disease))

    if st.diagnosis is not None:
        where = f"状态 {st.id!r} 的 diagnosis"
        code = _lookup(term.diagnosis, st.diagnosis.term, "diagnoses.yaml", where)
        chain.append(
            (
                f"{st.id}__{SFX_ONSET}",
                {"type": "ConditionOnset", "codes": [code.to_gmf()]},
            )
        )

    for i, act in enumerate(st.on_enter):
        ctx = EmitContext(ir_state=st, term=term, prefix=f"{st.id}__act{i}", ordinal=i)
        emitter = EMITTERS.get(act.action)
        if emitter is None:
            raise CompileError(f"{ctx.where}：不支持的动作 {act.action!r}")
        for suffix, body in emitter(act, ctx):
            chain.append((f"{ctx.prefix}{suffix}", body))

    if st.kind == "terminal":
        # Death 不是终点：``State.Death extends State``（不是 Terminal），
        # 没有出边时 Synthea 在 ``State.initialize`` 里直接抛
        # `State ... has no transition`。官方模块的写法也是 Death -> Terminal。
        chain.append((f"{st.id}__{SFX_DEATH}", {"type": "Death"}))
        chain.append(
            (f"{st.id}__{SFX_TERMINAL}", {"type": "Terminal"})
        )
    else:
        chain.append((f"{st.id}__{SFX_TAIL}", {"type": "Simple"}))

    return chain


def _rf_names(st_id: str, rf: RiskFactor, i: int) -> dict[str, str]:
    """危险因素 i 展开出的状态名。

    布尔型危险因素多一个 ``set_false`` 分支：属性是**快照**，不显式置回 false，
    第一次判定为真之后就永远是 true（``Utilities.compare(null, ...)`` 返回 false，
    所以未设置本身是安全的，已设置的旧值却不是）。
    非布尔型没有"反值"可言，条件不成立时就不碰属性。
    """
    base = f"{st_id}__rf{i}"
    names = {"decide": f"{base}_decide", "set_true": f"{base}_set_true"}
    if isinstance(rf.value, bool):
        names["set_false"] = f"{base}_set_false"
    return names


def _risk_factor_states(st: State, disease: Disease) -> list[tuple[str, dict]]:
    """入口处的危险因素判定：按条件把属性设成当前的真值（``SetAttribute``）。

    每个危险因素展开成一个小盒子::

        rf<i>_decide (Simple)  --条件成立-->  rf<i>_set_true  --> 下一个
                               --否则------>  rf<i>_set_false --> 下一个
                                             （非布尔型时直接到下一个）

    用 ``Simple`` + ``conditional_transition`` 而不是 ``Guard``：Guard 会一直
    等到条件成立为止，条件永不成立的人就永远卡在模块入口出不来。
    条件分支则是一次判定，不成立就直接跳过。

    只在 ``kind: initial`` 的状态上生成。它同时是随访回环的落点
    （见 ``_loop_target``），所以每轮回环都会按当时的 BMI / 年龄重算一次 ——
    属性因此始终反映最近一次评估的结果，而不是某一次的陈旧快照。
    """
    if not disease.risk_factors:
        return []

    states: list[tuple[str, dict]] = []
    for i, rf in enumerate(disease.risk_factors):
        names = _rf_names(st.id, rf, i)
        states.append((names["decide"], {"type": "Simple"}))
        states.append((
            names["set_true"],
            {"type": "SetAttribute", "attribute": rf.attribute, "value": rf.value},
        ))
        if "set_false" in names:
            states.append((
                names["set_false"],
                {
                    "type": "SetAttribute",
                    "attribute": rf.attribute,
                    "value": not rf.value,
                },
            ))
    return states


def _wire_risk_factors(st: State, chain: list[tuple[str, dict]], disease: Disease) -> None:
    """把危险因素判定接到 Initial 之后、原链头之前。"""
    if not disease.risk_factors:
        return

    def body_of(name: str) -> dict:
        return chain_body(chain, name)

    # 判定盒子占掉的状态数（不含 Initial），后面第一个才是原本的链头
    box_len = len(_risk_factor_states(st, disease))
    after = chain[1 + box_len][0]

    for i, rf in enumerate(disease.risk_factors):
        names = _rf_names(st.id, rf, i)
        nxt = (
            _rf_names(st.id, disease.risk_factors[i + 1], i + 1)["decide"]
            if i + 1 < len(disease.risk_factors)
            else after
        )
        where = f"risk_factors[{rf.id}].condition（{describe(rf.condition)}）"
        # 条件不成立时的去处：布尔型走"置回 false"，其余直接跳过
        miss = names.get("set_false", nxt)

        decision = body_of(names["decide"])
        decision["conditional_transition"] = [
            {
                "condition": to_logic(rf.condition, where=where),
                "transition": names["set_true"],
            },
            {"transition": miss},
        ]
        # 顺序连线时给判定状态留下的 direct_transition 必须清掉：
        # 一个状态同时带 direct_transition 和 conditional_transition，
        # Synthea 挑哪条是未定义的。
        decision.pop("direct_transition", None)

        body_of(names["set_true"])["direct_transition"] = nxt
        if "set_false" in names:
            body_of(names["set_false"])["direct_transition"] = nxt

    # Initial 直接进第一个判定
    chain_body(chain, INITIAL_STATE)["direct_transition"] = _rf_names(
        st.id, disease.risk_factors[0], 0
    )["decide"]


def _wire_chain(st: State, chain: list[tuple[str, dict]], disease: Disease) -> None:
    """补上链内的引用关系：ConditionOnset.target_encounter、各状态的 reason、EncounterEnd。"""
    onset = next((n for n, b in chain if b["type"] == "ConditionOnset"), None)
    first_encounter = next((n for n, b in chain if b["type"] == "Encounter"), None)

    # 诊断落在哪次就诊上。Synthea 的 OnsetState 支持延迟到目标 Encounter 才确诊。
    if onset and first_encounter:
        chain_body(chain, onset)["target_encounter"] = first_encounter

    # 病因：reason 必须是本状态下已存在的 ConditionOnset 状态名
    if onset:
        for name, body in chain:
            if body["type"] in REASON_STATES and name != onset:
                body.setdefault("reason", onset)

    _insert_encounter_ends(st.id, chain)

    # 顺序连线
    for (a_name, a_body), (b_name, _) in zip(chain, chain[1:]):
        a_body["direct_transition"] = b_name

    # 危险因素判定要覆盖顺序连线留下的转移，所以放在最后
    if st.kind == "initial":
        _wire_risk_factors(st, chain, disease)


def _insert_encounter_ends(st_id: str, chain: list[tuple[str, dict]]) -> None:
    """在每次就诊的末尾补 EncounterEnd。

    规则：第一个 Encounter 之外的每个 Encounter 之前插一个（关掉上一次就诊），
    链尾再补一个收口。Synthea 的 EncounterEnd 只在模块自己持有 encounter 锁时
    生效，多余的那个会被安全忽略。
    """
    encounter_idx = [i for i, (_, b) in enumerate(chain) if b["type"] == "Encounter"]
    if not encounter_idx:
        return

    out: list[tuple[str, dict]] = []
    counter = 0
    for i, (name, body) in enumerate(chain):
        if body["type"] == "Encounter" and i != encounter_idx[0]:
            out.append((f"{st_id}__{SFX_ENCEND}{counter}", {"type": "EncounterEnd"}))
            counter += 1
        out.append((name, body))

    if out[-1][1]["type"] != "Encounter":
        out.insert(
            len(out) - 1,
            (f"{st_id}__{SFX_ENCEND}{counter}", {"type": "EncounterEnd"}),
        )

    chain[:] = out


# --------------------------------------------------------------------- 转移的挂接


def _attach_transitions(
    disease: Disease,
    mb: ModuleBuilder,
    chains: dict[str, list[tuple[str, dict]]],
) -> None:
    """把 IR 的出边编译成 GMF 的 distributed / conditional / complex transition。"""
    grouped: dict[str, list[Transition]] = defaultdict(list)
    for t in disease.transitions:
        grouped[t.from_].append(t)

    # 概率先统一折算成"每次评估"的口径，下面只认这一个数。
    by_id = {st.id: st for st in disease.states}
    probs = {id(t): _cycle_probability(t, by_id) for t in disease.transitions}

    def p(t: Transition) -> float:
        return probs[id(t)]

    for st in disease.states:
        chain = chains[st.id]
        _, tail_body = chain[-1]

        if st.kind == "terminal":
            if grouped.get(st.id):
                raise CompileError(f"终态 {st.id!r} 不能有出边")
            continue

        edges = grouped.get(st.id, [])
        plain = [t for t in edges if t.when is None]
        conditional = [t for t in edges if t.when is not None]

        plain_sum = sum(p(t) for t in plain)
        residual = 1.0 - plain_sum

        # 只有当真的需要一条「什么也没发生，过阵子再看」的分支时，
        # 才创建 Delay 状态 —— 否则会留下 Synthea 会告警的孤立状态。
        needs_fallback = (
            not plain
            or residual > EPS
            or any(p(t) < 1.0 - EPS for t in conditional)
        )
        fallback = _ensure_delay(st, disease, mb, chains) if needs_fallback else None

        if not conditional:
            if len(plain) == 1 and abs(p(plain[0]) - 1.0) <= EPS:
                tail_body["direct_transition"] = _entry_of(chains, plain[0].to)
                continue

            branches: list[dict] = [
                {
                    "distribution": p(t),
                    "transition": _entry_of(chains, t.to),
                }
                for t in plain
            ]
            if residual > EPS:
                branches.append({"distribution": round(residual, 6), "transition": fallback})
            tail_body["distributed_transition"] = branches
            continue

        # --- 有条件出边 -> complex_transition ---
        entries: list[dict] = []
        for t in conditional:
            where = f"状态 {st.id!r} 到 {t.to!r} 的转移条件（{describe(t.when)}）"
            dists = [
                {
                    "distribution": p(t),
                    "transition": _entry_of(chains, t.to),
                }
            ]
            if p(t) < 1.0 - EPS:
                dists.append(
                    {"distribution": round(1.0 - p(t), 6), "transition": fallback}
                )
            entries.append(
                {"condition": to_logic(t.when, where=where), "distributions": dists}
            )

        default_branches = [
            {"distribution": p(t), "transition": _entry_of(chains, t.to)}
            for t in plain
        ]
        if residual > EPS:
            default_branches.append(
                {"distribution": round(residual, 6), "transition": fallback}
            )
        if not default_branches:
            # 全是条件出边，没有一条无条件出边 —— 默认分支必须有去处
            default_branches = [{"distribution": 1.0, "transition": fallback}]
        entries.append({"distributions": default_branches})

        tail_body["complex_transition"] = entries


def _ensure_delay(
    st: State,
    disease: Disease,
    mb: ModuleBuilder,
    chains: dict[str, list[tuple[str, dict]]],
) -> str:
    """创建（或复用）本状态的随访 Delay，返回其状态名。

    Delay 把「剩余概率」变成「过 N 天再评估一次」。落点跳过 Initial 与
    ConditionOnset（见 ``_loop_target``），所以不会重复开诊断。
    """
    name = f"{st.id}__{SFX_DELAY}"
    if name in mb.states:
        return name

    if st.repeat is not None:
        days = st.repeat.interval_days
    else:
        days = disease.defaults.followup_interval_days

    mb.add(
        name,
        {
            "type": "Delay",
            "exact": {"quantity": days, "unit": "days"},
            "direct_transition": _loop_target(chains[st.id]),
        },
    )
    return name


# ------------------------------------------------------------------------- 工具


def chain_body(chain: list[tuple[str, dict]], state_name: str) -> dict:
    for name, body in chain:
        if name == state_name:
            return body
    raise CompileError(f"内部错误：链里找不到状态 {state_name!r}")


def _entry_of(
    chains: dict[str, list[tuple[str, dict]]], state_id: str
) -> str:
    """从别的状态转移进某个 IR 状态时的落点。

    跳过 ``Initial``（入口只走一次），保留 ``ConditionOnset``（首次确诊要开）。
    """
    chain = chains[state_id]
    for name, body in chain:
        if body["type"] != "Initial":
            return name
    return chain[-1][0]


def _loop_target(chain: list[tuple[str, dict]]) -> str:
    """随访回环的落点：跳过 Initial 和 ConditionOnset，避免重复开诊断。"""
    for name, body in chain:
        if body["type"] not in LOOP_SKIP:
            return name
    return chain[-1][0]


def _lookup(fn, key: str, filename: str, where: str):
    try:
        return fn(key)
    except TerminologyError as exc:
        raise CompileError(f"{where}：{exc}\n  （白名单文件：terminology/{filename}）") from None