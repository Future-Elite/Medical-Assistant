"""IR 模型的校验规则。

这些测试守的是**硬约束**：source 必填、条件表达式结构合法、
图必须有唯一入口和至少一个终点、无条件出边概率不超过 1。
每一条都对应设计文档里的一个决定，改坏了必须有人知道。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hisgen.ir.models import Condition, Disease, IRError


# ------------------------------------------------------------------ Condition


def test_leaf_condition_needs_operator_and_value():
    with pytest.raises(ValidationError):
        Condition(attribute="age")


def test_leaf_condition_accepts_attribute():
    cond = Condition(attribute="age", operator=">=", value=45)
    assert cond.attribute == "age"
    assert cond.value == 45


def test_leaf_condition_accepts_vital_sign():
    cond = Condition(vital_sign="BMI", operator=">=", value=28)
    assert cond.vital_sign == "BMI"


def test_leaf_cannot_have_both_attribute_and_vital_sign():
    with pytest.raises(ValidationError):
        Condition(attribute="age", vital_sign="BMI", operator=">=", value=1)


def test_combinator_cannot_carry_operator():
    with pytest.raises(ValidationError):
        Condition(all=[Condition(attribute="age", operator=">=", value=1)], operator=">=")


def test_empty_all_is_rejected():
    with pytest.raises(ValidationError):
        Condition(all=[])


def test_nested_not_roundtrip():
    """``not`` 是关键字，字段名是 ``not_`` + alias —— 两边都得能用。"""
    cond = Condition.model_validate(
        {"not": {"vital_sign": "BMI", "operator": ">=", "value": 28}}
    )
    assert cond.not_ is not None
    assert cond.not_.vital_sign == "BMI"


def test_deeply_nested_combination():
    cond = Condition.model_validate(
        {
            "all": [
                {"attribute": "age", "operator": ">=", "value": 45},
                {"not": {"vital_sign": "BMI", "operator": ">=", "value": 28}},
            ]
        }
    )
    assert len(cond.all) == 2
    assert cond.all[1].not_ is not None


def test_unknown_key_is_rejected():
    """extra="forbid"：写错的键必须炸，不能被静默忽略。"""
    with pytest.raises(ValidationError):
        Condition(attribut="age", operator=">=", value=1)


# --------------------------------------------------------------------- Disease


def _minimal_disease(**overrides) -> dict:
    base = {
        "disease": {"id": "x", "name_zh": "测试病", "source": "某指南"},
        "states": [
            {"id": "start", "name_zh": "起点", "kind": "initial"},
            {"id": "end", "name_zh": "终点", "kind": "terminal"},
        ],
        "transitions": [
            {"from": "start", "to": "end", "probability": 1.0, "source": "设定"}
        ],
    }
    base.update(overrides)
    return base


def test_minimal_disease_validates():
    d = Disease.model_validate(_minimal_disease())
    assert d.disease.id == "x"


def test_transition_source_is_required():
    """source 是硬约束 —— 去掉必须炸。"""
    payload = _minimal_disease()
    del payload["transitions"][0]["source"]
    with pytest.raises(ValidationError):
        Disease.model_validate(payload)


def test_diagnosis_source_is_required():
    payload = _minimal_disease()
    payload["states"][1]["kind"] = "acute"
    payload["states"][1]["diagnosis"] = {"term": "t2dm"}
    payload["transitions"] = [
        {"from": "start", "to": "end", "probability": 1.0, "source": "设定"},
        {"from": "end", "to": "start", "probability": 1.0, "source": "设定"},
    ]
    with pytest.raises(ValidationError):
        Disease.model_validate(payload)


def test_two_initials_rejected():
    payload = _minimal_disease()
    payload["states"].append({"id": "start2", "name_zh": "起点2", "kind": "initial"})
    with pytest.raises(ValidationError) as exc:
        Disease.model_validate(payload)
    assert "initial" in str(exc.value)


def test_no_terminal_rejected():
    payload = _minimal_disease()
    payload["states"] = [
        {"id": "start", "name_zh": "起点", "kind": "initial"},
        {"id": "other", "name_zh": "中间", "kind": "chronic"},
    ]
    payload["transitions"] = [
        {"from": "start", "to": "other", "probability": 1.0, "source": "设定"},
        {"from": "other", "to": "start", "probability": 1.0, "source": "设定"},
    ]
    with pytest.raises(ValidationError) as exc:
        Disease.model_validate(payload)
    assert "terminal" in str(exc.value)


def test_dangling_transition_endpoint_rejected():
    payload = _minimal_disease()
    payload["transitions"][0]["to"] = "nope"
    with pytest.raises(ValidationError) as exc:
        Disease.model_validate(payload)
    assert "不存在的目标状态" in str(exc.value)


def test_state_without_outgoing_edge_rejected():
    payload = _minimal_disease()
    payload["states"].insert(
        1, {"id": "trapped", "name_zh": "卡死", "kind": "chronic"}
    )
    with pytest.raises(ValidationError) as exc:
        Disease.model_validate(payload)
    assert "卡死" in str(exc.value) or "trapped" in str(exc.value)


def test_double_underscore_in_id_rejected():
    """``__`` 是编译器的命名空间分隔符，IR 里禁用。"""
    payload = _minimal_disease()
    payload["states"][0]["id"] = "start__x"
    with pytest.raises(ValidationError) as exc:
        Disease.model_validate(payload)
    assert "__" in str(exc.value)


def test_plain_probability_sum_over_one_rejected():
    payload = _minimal_disease()
    payload["states"].append({"id": "mid", "name_zh": "中间", "kind": "chronic"})
    payload["transitions"] = [
        {"from": "start", "to": "end", "probability": 0.7, "source": "设定"},
        {"from": "start", "to": "mid", "probability": 0.6, "source": "设定"},
        {"from": "mid", "to": "end", "probability": 1.0, "source": "设定"},
    ]
    with pytest.raises(ValidationError) as exc:
        Disease.model_validate(payload)
    assert "超过 1.0" in str(exc.value)


def test_probability_sum_under_one_is_allowed():
    """不足 1 是合法的 —— 编译器会用 Delay 回环补足。"""
    payload = _minimal_disease()
    payload["transitions"][0]["probability"] = 0.3
    d = Disease.model_validate(payload)
    assert d.transitions[0].probability == 0.3


def test_terminal_cannot_have_on_enter():
    payload = _minimal_disease()
    payload["states"][1]["on_enter"] = [
        {"action": "encounter", "class": "ambulatory", "department_zh": "内科"}
    ]
    with pytest.raises(ValidationError):
        Disease.model_validate(payload)


def test_duplicate_risk_factor_attribute_rejected():
    payload = _minimal_disease()
    payload["risk_factors"] = [
        {
            "id": "a",
            "name_zh": "甲",
            "attribute": "same",
            "value": True,
            "condition": {"attribute": "age", "operator": ">=", "value": 1},
            "source": "设定",
        },
        {
            "id": "b",
            "name_zh": "乙",
            "attribute": "same",
            "value": True,
            "condition": {"attribute": "age", "operator": ">=", "value": 2},
            "source": "设定",
        },
    ]
    with pytest.raises(ValidationError) as exc:
        Disease.model_validate(payload)
    assert "attribute 重复" in str(exc.value)


def test_operator_must_be_from_vocabulary():
    with pytest.raises(ValidationError):
        Condition(attribute="age", operator="=~", value=1)


def test_ire_error_is_value_error():
    """IRError 必须是 ValueError 的子类 —— pydantic 的验证器靠这个传播。"""
    assert issubclass(IRError, ValueError)