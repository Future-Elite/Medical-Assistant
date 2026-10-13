"""层 1：疾病资料 -> 临床路径 IR。

**这是整个项目里唯一使用大模型的地方**，而且刻意把它的权力压到最小：

* 它产出的是 IR，不是 GMF。GMF 的机械复杂度由编译器负责，写一次测一次。
* 它只能引用 ``terminology/*.yaml`` 里的键名，不能写码值。
* 它写的每个数字都要带 ``source``，没把握的必须标 ``【待核实】``。

产物是**给人看的** YAML —— 临床人员应该能读懂并挑错，这是选 YAML 而非 JSON 的原因。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from ..ir.models import Disease
from ..ir.load import IR_DIR
from ..terminology import Terminology
from .prompts import build_user_prompt, system_prompt
from .review import render

#: 默认模型。DeepSeek 在国内可直连，且中文语料充足。
DEFAULT_MODEL = "deepseek-chat"


class LlmError(RuntimeError):
    """生成失败。"""


def build_llm(model: str | None = None, temperature: float = 0.0):
    """构造一个带结构化输出的 chat 模型。

    ``temperature=0`` 是刻意的：这一步要的是**可复现的资料整理**，
    不是创作。同一个输入跑两次得到不同的概率，人工核对就没法做。
    """
    try:
        from langchain_deepseek import ChatDeepSeek
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise LlmError(
            "未安装 langchain-deepseek。请 pip install -e .[llm]"
        ) from exc

    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise LlmError(
            "环境变量 DEEPSEEK_API_KEY 未设置。请先设置再运行；"
            "或改用 --from-file 只重生成核对清单（不调模型）。"
        )

    return ChatDeepSeek(
        model=model or os.environ.get("HISGEN_LLM_MODEL", DEFAULT_MODEL),
        temperature=temperature,
    )


def generate_disease(
    brief: str,
    term: Terminology,
    *,
    llm=None,
    model: str | None = None,
) -> Disease:
    """把疾病资料整理成 IR。

    用 ``with_structured_output(Disease)`` 让模型按 schema 出结构化结果 ——
    形状由 schema 保证，pydantic 再把内容规则（source 必填、图连通、
    概率之和）过一遍。任何一条不满足都会在这里就炸，不会流到编译器。
    """
    if llm is None:
        llm = build_llm(model).with_structured_output(Disease)

    messages = [
        ("system", system_prompt()),
        ("human", build_user_prompt(brief, term)),
    ]
    try:
        result = llm.invoke(messages)
    except Exception as exc:  # noqa: BLE001 - 统一包成 LlmError，附上上下文
        raise LlmError(f"模型调用失败：{type(exc).__name__}: {exc}") from exc

    if isinstance(result, Disease):
        return result
    if isinstance(result, dict):
        # 有些模型返回 dict 而不是模型实例
        return Disease.model_validate(result)
    raise LlmError(f"模型返回了预期之外的类型：{type(result).__name__}")


def dump_yaml(disease: Disease) -> str:
    """把 IR 渲染成 YAML —— 产物形态，人读这一份。"""
    payload = disease.model_dump(by_alias=True, exclude_none=True, mode="json")
    return yaml.safe_dump(
        payload,
        allow_unicode=True,   # 中文不转义
        sort_keys=False,      # 保持模型给出的顺序，读起来才像临床路径
        width=100,
    )


def write_ir(
    disease: Disease, path: str | Path | None = None
) -> Path:
    out = Path(path) if path else IR_DIR / f"{disease.disease.id}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump_yaml(disease), encoding="utf-8")
    return out


def write_review(disease: Disease, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(disease), encoding="utf-8")
    return out