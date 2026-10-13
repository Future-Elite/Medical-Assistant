"""编译器：IR -> GMF。

重点测三类东西：

1. **结构合法性** —— 产出的 GMF 必须能被 Synthea 的加载器接受
2. **概率归一** —— 每个非终态的出边概率之和恒为 1（Synthea 靠这个选分支）
3. **回环不重复开诊断** —— Delay 的落点必须跳过 ConditionOnset
"""

from __future__ import annotations

import json

import pytest

from hisgen.compiler import CompileError, compile_module, validate_gmf
from hisgen.compiler.compile import (
    _attach_transitions,
    _entry_of,
    _loop_target,
    ModuleBuilder,
)
from hisgen.compiler.conditions import describe, to_logic
from hisgen.ir.models import Condition, Disease
from hisgen.ir import load_ir


EPS = 1e-9


@pytest.fixture
def gmf(t2dm_ir_path, terminology) -> dict:
    return compile_module(load_ir(t2dm_ir_path), terminology)


def _outgoing_probabilities(body: dict) -> list[float]:
    out: list[float] = []
    for key in ("distributed_transition", "conditional_transition", "complex_transition"):
        for opt in body.get(key) or []:
            if "distribution" in opt:
                out.append(float(opt["distribution"]))
            for dist in opt.get("distributions") or []:
                out.append(float(dist["distribution"]))
        # conditional_transition 的分支没有 distribution —— 它是"条件成立就走"
        if key == "conditional_transition":
            return out or [1.0]
    return out


# ------------------------------------------------------------------ 结构合法性


def test_compiles_and_passes_structure_check(gmf):
    validate_gmf(gmf, origin="test")


def test_module_metadata(gmf):
    assert gmf["gmf_version"] == 1
    assert gmf["name"] == "sh_t2dm"
    # remarks 必须是**字符串数组**：Module.java:365 直接 getAsJsonArray()，
    # 写成字符串会报 `Not a JSON Array: <整段备注>`
    assert isinstance(gmf["remarks"], list)
    assert all(isinstance(r, str) for r in gmf["remarks"])
    assert any("ir/sh_t2dm.yaml" in r for r in gmf["remarks"])


def test_exactly_one_initial(gmf):
    initials = [n for n, b in gmf["states"].items() if b["type"] == "Initial"]
    assert len(initials) == 1


def test_has_terminal(gmf):
    types = {b["type"] for b in gmf["states"].values()}
    assert types & {"Death", "Terminal"}


def test_every_non_terminal_has_outgoing_edge(gmf):
    for name, body in gmf["states"].items():
        if body["type"] in ("Death", "Terminal"):
            continue
        assert any(
            body.get(k)
            for k in ("direct_transition", "distributed_transition",
                      "conditional_transition", "complex_transition")
        ), f"{name} 没有出边"


def test_all_transition_targets_exist(gmf):
    states = gmf["states"]
    for name, body in states.items():
        for key in ("direct_transition", "distributed_transition",
                    "conditional_transition", "complex_transition"):
            value = body.get(key)
            if key == "direct_transition":
                # direct_transition 的值是裸字符串而不是列表，不能当列表遍历
                if value:
                    assert value in states, f"{name} -> {value}"
                continue
            for opt in value or []:
                if opt.get("transition"):
                    assert opt["transition"] in states
                for d in opt.get("distributions") or []:
                    assert d["transition"] in states


def test_no_state_is_unreachable(gmf):
    states = gmf["states"]
    index: dict[str, list[str]] = {}
    for name, body in states.items():
        targets = []
        if body.get("direct_transition"):
            targets.append(body["direct_transition"])
        for key in ("distributed_transition", "conditional_transition",
                    "complex_transition"):
            for opt in body.get(key) or []:
                if opt.get("transition"):
                    targets.append(opt["transition"])
                targets.extend(
                    d["transition"] for d in opt.get("distributions") or []
                )
        index[name] = targets

    start = next(n for n, b in states.items() if b["type"] == "Initial")
    seen, queue = {start}, [start]
    while queue:
        for nxt in index.get(queue.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    assert set(states) - seen == set()


def test_reason_points_at_a_condition_onset_in_the_same_module(gmf):
    states = gmf["states"]
    onsets = {n for n, b in states.items() if b["type"] == "ConditionOnset"}
    for name, body in states.items():
        if "reason" in body:
            assert body["reason"] in onsets, f"{name} 的 reason 不是 ConditionOnset"


def test_target_encounter_points_at_an_encounter(gmf):
    states = gmf["states"]
    for name, body in states.items():
        if "target_encounter" in body:
            assert states[body["target_encounter"]]["type"] == "Encounter"


def test_every_condition_onset_has_codes(gmf):
    for name, body in gmf["states"].items():
        if body["type"] == "ConditionOnset":
            assert body.get("codes"), name


def test_observations_with_range_have_a_unit(gmf):
    """Synthea 的硬性要求：带 range/exact 的 Observation 必须有 unit。"""
    for name, body in gmf["states"].items():
        if body["type"] != "Observation":
            continue
        if body.get("range") or body.get("exact"):
            assert str(body.get("unit") or "").strip(), name


# ------------------------------------------------------------------ 概率归一


def test_non_terminal_probabilities_sum_to_one(gmf):
    """每个非终态的分布之和必须是 1 —— 否则 Synthea 会按最后一项兜底。"""
    for name, body in gmf["states"].items():
        if body["type"] in ("Death", "Terminal"):
            continue
        if body.get("conditional_transition"):
            # 条件分支：每条都是一次判定，不参与归一
            continue
        probs = _outgoing_probabilities(body)
        if not probs:
            continue
        assert abs(sum(probs) - 1.0) < 1e-6, f"{name}: {probs} 和为 {sum(probs)}"


def test_complex_transition_branches_sum_to_one(gmf):
    for name, body in gmf["states"].items():
        for opt in body.get("complex_transition") or []:
            dists = opt.get("distributions") or []
            assert dists, f"{name} 的 complex_transition 分支没有 distributions"
            assert abs(sum(d["distribution"] for d in dists) - 1.0) < 1e-6, name


def test_default_branch_is_last_in_complex_transition(gmf):
    """Synthea 的 pickDistributedTransition 取"没匹配上就用最后一项"，
    所以无条件的兜底分支必须排在最后。"""
    for name, body in gmf["states"].items():
        entries = body.get("complex_transition")
        if not entries:
            continue
        assert "condition" not in entries[-1], f"{name} 的最后一个分支带条件"


# ------------------------------------------------------------------ 回环语义


def test_delay_skips_condition_onset(gmf):
    """回环落点跳过 ConditionOnset —— 否则每轮随访都会重复开一条诊断。"""
    states = gmf["states"]
    for name, body in states.items():
        if body["type"] != "Delay":
            continue
        target = body["direct_transition"]
        assert states[target]["type"] != "ConditionOnset", f"{name} -> {target}"


def test_delay_is_not_initial(gmf):
    states = gmf["states"]
    for name, body in states.items():
        if body["type"] == "Delay":
            assert states[body["direct_transition"]]["type"] != "Initial"


def test_delay_loop_target_helper():
    chain = [
        ("s__onset", {"type": "ConditionOnset"}),
        ("s__act0", {"type": "Encounter"}),
        ("s__tail", {"type": "Simple"}),
    ]
    assert _loop_target(chain) == "s__act0"


def test_delay_loop_target_falls_back_to_last():
    chain = [("s__onset", {"type": "ConditionOnset"})]
    assert _loop_target(chain) == "s__onset"


def test_entry_of_keeps_condition_onset():
    """从别的状态转移进来时**要**保留 ConditionOnset —— 首次确诊得开。"""
    chain = [
        ("s__initial", {"type": "Initial"}),
        ("s__onset", {"type": "ConditionOnset"}),
    ]
    assert _entry_of({"s": chain}, "s") == "s__onset"


# ------------------------------------------------------------------ 危险因素


def test_risk_factor_uses_conditional_not_guard(gmf):
    """Guard 会永远等人满足条件；这里必须是一次判定。"""
    assert not any(b["type"] == "Guard" for b in gmf["states"].values())
    decides = [n for n in gmf["states"] if n.endswith("_decide")]
    assert decides, "没有生成危险因素判定状态"
    for name in decides:
        assert gmf["states"][name]["conditional_transition"]


def test_risk_factor_decision_has_no_direct_transition(gmf):
    """同时带 direct_transition 和 conditional_transition，Synthea 选哪条是未定义的。"""
    for name, body in gmf["states"].items():
        if name.endswith("_decide"):
            assert "direct_transition" not in body


def test_boolean_risk_factor_sets_both_values(gmf):
    """布尔危险因素必须能置回 false，否则第一次为真之后就永远是 true。"""
    sets = [n for n in gmf["states"] if n.endswith("_set_true")]
    assert sets
    for name in sets:
        assert gmf["states"][name]["value"] is True
        false_name = name.replace("_set_true", "_set_false")
        assert false_name in gmf["states"], f"{name} 缺少对应的 set_false"
        assert gmf["states"][false_name]["value"] is False


def test_risk_factor_uses_vital_sign_logic(gmf):
    body = gmf["states"]["healthy__rf0_decide"]
    cond = body["conditional_transition"][0]["condition"]
    assert cond["condition_type"] == "VitalSign"
    assert cond["vital_sign"] == "BMI"
    assert cond["value"] == 28.0


# ------------------------------------------------------------------ 条件编译


def test_age_compiles_to_age_logic():
    logic = to_logic(Condition(attribute="age", operator=">=", value=45))
    assert logic["condition_type"] == "Age"
    assert logic["unit"] == "years"
    assert logic["quantity"] == 45.0


def test_plain_attribute_compiles_to_attribute_logic():
    logic = to_logic(Condition(attribute="has_obesity", operator="==", value=True))
    assert logic["condition_type"] == "Attribute"
    assert logic["value"] is True


def test_all_compiles_to_and():
    logic = to_logic(
        Condition.model_validate(
            {"all": [{"attribute": "age", "operator": ">=", "value": 1}]}
        )
    )
    assert logic["condition_type"] == "And"
    assert len(logic["conditions"]) == 1


def test_not_compiles_to_not():
    logic = to_logic(
        Condition.model_validate(
            {"not": {"vital_sign": "BMI", "operator": ">=", "value": 28}}
        )
    )
    assert logic["condition_type"] == "Not"
    assert logic["condition"]["condition_type"] == "VitalSign"


def test_vital_sign_value_must_be_numeric():
    with pytest.raises(CompileError):
        to_logic(Condition(vital_sign="BMI", operator=">=", value="abc"))


def test_describe_is_human_readable():
    text = describe(Condition(attribute="age", operator=">=", value=45))
    assert "age" in text and "45" in text


# --------------------------------------------------------------- 确定性与报错


def test_compilation_is_deterministic(t2dm_ir_path, terminology):
    from hisgen.ir import load_ir as _load

    d = _load(t2dm_ir_path)
    a = compile_module(d, terminology)
    b = compile_module(d, terminology)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_unknown_terminology_key_fails_with_location(t2dm_ir_path, terminology):
    from hisgen.ir import load_ir as _load

    payload = t2dm_ir_path.read_text(encoding="utf-8")
    payload = payload.replace("drug: metformin", "drug: no_such_drug")
    d = Disease.model_validate(__import__("yaml").safe_load(payload))
    from hisgen.compiler import validate_terminology

    with pytest.raises(CompileError) as exc:
        validate_terminology(d, terminology)
    assert "no_such_drug" in str(exc.value)
    assert "medications.yaml" in str(exc.value)


def test_state_name_collision_is_detected():
    mb = ModuleBuilder(name="x")
    mb.add("a", {"type": "Simple"})
    with pytest.raises(CompileError):
        mb.add("a", {"type": "Simple"})