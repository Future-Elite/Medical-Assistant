"""从 FHIR 资源里取字段的小工具。

全部**防御式**：FHIR 里几乎每个字段都是可选的，Synthea 在不同配置下产出的
字段也不完全一样。这里的原则是"取不到就返回空串"，而不是抛异常 ——
少一列数据比整套导出跑不出来好得多。真正必须有的字段由 tables.py 单独把关。
"""

from __future__ import annotations

from typing import Any

#: 就诊类型：FHIR Encounter.class 的 code -> 中文
ENCOUNTER_CLASS_ZH = {
    "AMB": "门诊",
    "AMBULATORY": "门诊",
    "OUTPATIENT": "门诊",
    "IMP": "住院",
    "INPATIENT": "住院",
    "EMER": "急诊",
    "EMERGENCY": "急诊",
    "URGENT": "急诊",
    "URGENTCARE": "急诊",
    "VR": "互联网诊疗",
    "VIRTUAL": "互联网诊疗",
    "HH": "家庭病床",
    "HOME": "家庭病床",
    "HOSPICE": "临终关怀",
    "SNF": "护理院",
}

#: 结算类别码集。
#:
#: 同一件事有两套写法，都要认：
#:
#: * FHIR 标准短码（``benefit`` / ``copay`` / ``deductible`` / ``submitted``）
#: * Synthea 实际产出的 Blue Button **完整 URL**
#:   （``https://bluebutton.cms.gov/resources/variables/line_prvdr_pmt_amt`` …）
#:
#: 只认短码的话，费用会静默地全变成 0.00 —— 因为一个短码都匹配不上。
ADJUDICATION_SUBMITTED = frozenset(
    {
        "submitted",
        "line_sbmtd_chrg_amt",  # 申报金额 -> 总费用
    }
)
ADJUDICATION_BENEFIT = frozenset(
    {
        "benefit",
        "line_prvdr_pmt_amt",  # 医保（支付方）支付金额
    }
)
ADJUDICATION_COPAY = frozenset(
    {
        "copay",
        "line_coinsrnc_amt",  # 共付段个人自付
    }
)
ADJUDICATION_DEDUCTIBLE = frozenset(
    {
        "deductible",
        "line_bene_ptb_ddctbl_amt",  # 起付线以下个人自付
    }
)


def obj(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def arr(value: Any) -> list:
    return value if isinstance(value, list) else []


def first(value: Any) -> Any:
    """取列表首元素；不是列表就原样返回；空列表返回 None。"""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def s(value: Any) -> str:
    """转成字符串。None -> 空串；浮点数去掉无意义的小数尾巴。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:g}"
    return str(value)


def num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------- 编码


def coding_of(codeable: Any) -> dict:
    """从 CodeableConcept / Coding / code 里取出第一个 Coding。

    见得到的形态有三种，都要认：

    * ``{"coding": [{...}]}`` —— 标准 CodeableConcept
    * ``{"system": ..., "code": ...}`` —— 裸 Coding（如 ``Encounter.class``）
    * 一个**列表** —— 如 ``PractitionerRole.specialty``。FHIR 里它是
      ``0..*``，传进来的常常是列表而非单个。这里统一取第一个，
      免得每个调用点都要记得先 ``first()``。

    取不到就给空 dict —— 调用方一律按"没有编码"处理。
    """
    if isinstance(codeable, list):
        codeable = first(codeable)
    cc = obj(codeable)
    if not cc:
        return {}
    if cc.get("coding"):
        return obj(first(cc["coding"]))
    if cc.get("system") or cc.get("code"):
        return cc
    return {}


def code_system(codeable: Any) -> str:
    return s(coding_of(codeable).get("system"))


def code_value(codeable: Any) -> str:
    return s(coding_of(codeable).get("code"))


def code_display(codeable: Any, *, fallback_text: bool = True) -> str:
    """显示名。display 优先，其次 code，最后 ``text``。

    还要认 ``{"display": "..."}`` 这种**只有显示名**的写法 ——
    Synthea 给 ``Claim.insurer`` / ``ExplanationOfBenefit.insurer``
    写的就是这个形态，而它就是我们要的中文医保名。
    """
    coding = coding_of(codeable)
    if coding.get("display"):
        return s(coding["display"])
    if coding.get("code"):
        return s(coding["code"])
    container = obj(first(codeable) if isinstance(codeable, list) else codeable)
    if container.get("display"):
        return s(container["display"])
    if fallback_text:
        return s(container.get("text"))
    return ""


def code_triple(codeable: Any) -> tuple[str, str, str]:
    """返回 ``(体系 URI, 码值, 显示名)``。"""
    return code_system(codeable), code_value(codeable), code_display(codeable)


def adjudication_code(category: Any) -> str:
    """``adjudication`` / ``total`` 条目的类别码，归一成**最后一段**。

    Synthea 写的是 Blue Button 的完整 URL，我们只关心末尾那段
    （``https://bluebutton.cms.gov/resources/variables/line_prvdr_pmt_amt``
    -> ``line_prvdr_pmt_amt``），归一之后才能和短码放在一张表里比。
    """
    raw = code_value(category) or s(category)
    return raw.rsplit("/", 1)[-1] if raw else ""


# --------------------------------------------------------------------- 时间


def date_of(value: Any) -> str:
    """``"2024-01-02T03:04:05+08:00"`` -> ``"2024-01-02"``。"""
    text = s(value)
    return text[:10] if len(text) >= 10 else text


def datetime_of(value: Any) -> str:
    """ISO 8601 -> ``"2024-01-02 03:04:05"``。

    只保留到秒，把时区后缀去掉 —— 关系型表里存本地时间对 HIS 场景更自然，
    Synthea 输出的时间本来就是本机时区。
    """
    text = s(value).replace("Z", "+00:00")
    if not text:
        return ""
    if "T" not in text:
        return text
    date, rest = text.split("T", 1)
    # 去掉时区：+08:00 / -05:00
    for sep in ("+", "-"):
        idx = rest.find(sep)
        if idx > 0:
            rest = rest[:idx]
    rest = rest.rstrip(".")
    return f"{date} {rest[:8]}" if len(rest) >= 8 else f"{date} {rest}".strip()


def period_start(period: Any) -> str:
    return datetime_of(obj(period).get("start"))


def period_end(period: Any) -> str:
    return datetime_of(obj(period).get("end"))


# --------------------------------------------------------------------- 数量


def quantity_value(quantity: Any) -> str:
    return s(obj(quantity).get("value"))


def quantity_unit(quantity: Any) -> str:
    return s(obj(quantity).get("unit"))


def money_value(money: Any) -> float:
    """``{"value": 12.5, "currency": "CNY"}`` -> ``12.5``。"""
    return num(obj(money).get("value"))


# --------------------------------------------------------------------- 地址


def address_lines(address: Any) -> str:
    return "".join(s(x) for x in arr(obj(address).get("line")))


def human_name(name: Any) -> str:
    """人名。中文里姓在前、名在后，不留空格。

    ``name.text`` 优先 —— Synthea 自己拼好的，省得我们在 family/given
    的组合规则上再猜一遍。没有 text 才回退到 family + given。
    """
    n = obj(name)
    if n.get("text"):
        return s(n["text"])
    family = s(n.get("family"))
    given = "".join(s(x) for x in arr(n.get("given")))
    return f"{family}{given}"


def identifier_by_system(patient: dict, system: str) -> str:
    for ident in arr(patient.get("identifier")):
        if s(obj(ident).get("system")) == system:
            return s(obj(ident).get("value"))
    return ""


def first_phone(patient: dict) -> str:
    for contact in arr(patient.get("telecom")):
        c = obj(contact)
        if c.get("system") == "phone":
            return s(c.get("value"))
    return ""


def refs_of(container: dict, key: str) -> list[str]:
    """取 ``[{"reference": "X/y"}, ...]`` 里的 id 列表。"""
    out: list[str] = []
    for item in arr(container.get(key)):
        ref = s(obj(item).get("reference"))
        if "/" in ref:
            out.append(ref.split("/", 1)[1])
    return out