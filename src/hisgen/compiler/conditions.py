"""IR 的结构化条件 -> Synthea 的 Logic。

为什么不让 LLM 直接写 Groovy：模型写结构化条件远比写 Groovy 字符串可靠，
写错了 pydantic 当场拦下，而不是等到 Synthea 加载时才炸。

映射规则（依据 engine/Logic.java 的实际字段）：
  * ``attribute: age``  -> ``Logic.Age``，因为年龄不是 person.attributes 里的键，
    是实时算出来的，只能用专门的 Age 条件
  * 其余 attribute      -> ``Logic.Attribute``，走
    ``Utilities.compare(person.attributes.get(attr), value, operator)``
"""

from __future__ import annotations

from ..ir.models import Condition
from .errors import CompileError

#: 只能靠 Logic.Age 表达的属性
_AGE_ATTRIBUTES = {"age", "age_years"}


def to_logic(cond: Condition, *, where: str = "") -> dict:
    """把 IR 条件表达式编译成 Synthea 的 Logic JSON。"""
    if cond.all is not None:
        return {
            "condition_type": "And",
            "conditions": [
                to_logic(c, where=where) for c in cond.all
            ],
        }
    if cond.any is not None:
        return {
            "condition_type": "Or",
            "conditions": [
                to_logic(c, where=where) for c in cond.any
            ],
        }
    if cond.not_ is not None:
        return {"condition_type": "Not", "condition": to_logic(cond.not_, where=where)}

    # --- 叶子 ---
    if cond.vital_sign is not None:
        try:
            value = float(cond.value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise CompileError(
                f"{where}: 体征条件的 value 必须是数字，实际为 {cond.value!r}"
            ) from None
        return {
            "condition_type": "VitalSign",
            "vital_sign": cond.vital_sign,
            "operator": cond.operator,
            "value": value,
        }

    assert cond.attribute is not None  # pydantic 已保证
    if cond.attribute in _AGE_ATTRIBUTES:
        try:
            quantity = float(cond.value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise CompileError(
                f"{where}: age 条件的 value 必须是数字，实际为 {cond.value!r}"
            ) from None
        return {
            "condition_type": "Age",
            "quantity": quantity,
            "unit": "years",
            "operator": cond.operator,
        }

    return {
        "condition_type": "Attribute",
        "attribute": cond.attribute,
        "operator": cond.operator,
        "value": cond.value,
    }


def describe(cond: Condition) -> str:
    """把条件渲染成人话，用于编译日志与待核实清单。"""
    if cond.all is not None:
        return " 且 ".join(f"({describe(c)})" for c in cond.all)
    if cond.any is not None:
        return " 或 ".join(f"({describe(c)})" for c in cond.any)
    if cond.not_ is not None:
        return f"非({describe(cond.not_)})"
    if cond.vital_sign is not None:
        return f"体征 {cond.vital_sign} {cond.operator} {literal(cond.value)}"
    return f"{cond.attribute} {cond.operator} {literal(cond.value)}"


def literal(value: object) -> str:
    """把值渲染成 IR 里的写法，而不是 Python 的 ``repr``。

    ``True`` 应该是 ``true`` —— 这张清单是给人对着 YAML 核对的，
    两边写法不一致会让人多绕一道弯。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)