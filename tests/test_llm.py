"""层 1：提示词与核对清单。

**不调模型** —— 网络调用既慢又不确定，不适合放进测试套件。
这里测的是那些能确定验证的部分：提示词里的硬规则齐不齐、
白名单给全没给全、核对清单挑不挑得出待核实项、IR 能不能往返。
"""

from __future__ import annotations

import pytest

from hisgen.ir import load_ir, load_ir_text
from hisgen.ir.models import Disease
from hisgen.llm import (
    UNVERIFIED_MARKER,
    build_user_prompt,
    collect,
    dump_yaml,
    render,
    system_prompt,
    write_ir,
)
from hisgen.llm.generate import LlmError, build_llm


@pytest.fixture
def disease(t2dm_ir_path) -> Disease:
    return load_ir(t2dm_ir_path)


# ------------------------------------------------------------------ 提示词


def test_system_prompt_has_no_placeholder_left():
    prompt = system_prompt()
    assert "{marker}" not in prompt
    assert UNVERIFIED_MARKER in prompt


def test_system_prompt_states_the_four_hard_rules():
    prompt = system_prompt()
    assert "出处" in prompt            # 规则 1：每个数字要带 source
    assert UNVERIFIED_MARKER in prompt  # 规则 2：没把握就标记
    assert "不要写任何编码值" in prompt  # 规则 3：不许写码值
    assert "action 只能是" in prompt     # 规则 4：动作受控词表


def test_system_prompt_forbids_code_values_explicitly():
    """规则 3 必须点名几类码，否则模型不知道"编码值"包括什么。"""
    prompt = system_prompt()
    for token in ("E11", "A10BA02", "LOINC", "ATC", "ICD"):
        assert token in prompt, token


def test_system_prompt_explains_probability_semantics():
    """模型最容易犯的错是硬凑概率到 1 —— 提示词必须说清差额怎么办。"""
    prompt = system_prompt()
    assert "随访回环吸收" in prompt or "自动解释为" in prompt


def test_user_prompt_lists_every_whitelist_key(terminology):
    prompt = build_user_prompt("2型糖尿病", terminology)
    for key in terminology.diagnoses:
        assert key in prompt, key
    for key in terminology.labs:
        assert key in prompt, key
    for key in terminology.medications:
        assert key in prompt, key
    for key in terminology.procedures:
        assert key in prompt, key


def test_user_prompt_carries_the_brief(terminology):
    prompt = build_user_prompt("这份指南说了很多事", terminology)
    assert "这份指南说了很多事" in prompt


def test_user_prompt_never_leaks_code_values(terminology):
    """白名单里给的是键名 + 中文名。

    码值不给 —— 给了模型就会抄，抄错一个就是一条假编码进了产物。
    """
    prompt = build_user_prompt("x", terminology)
    assert "A10BA02" not in prompt
    assert "1558-6" not in prompt


# ------------------------------------------------------------------ 核对清单


def test_collect_finds_every_sourced_entry(disease):
    entries = collect(disease)
    assert entries
    # 每条都必须有位置、数值、出处
    for e in entries:
        assert e.where
        assert e.value
        assert e.source


def test_collect_covers_all_transitions(disease):
    where = {e.where for e in collect(disease)}
    for t in disease.transitions:
        assert f"transitions[{t.from_} → {t.to}]" in where


def test_collect_covers_risk_factors_and_diagnoses(disease):
    text = " ".join(e.where for e in collect(disease))
    assert "risk_factors[has_obesity]" in text
    assert "states[t2dm].diagnosis" in text


def test_unverified_entries_are_flagged(disease):
    entries = collect(disease)
    unverified = [e for e in entries if e.unverified]
    assert unverified, "sh_t2dm.yaml 里应当有标记待核实的占位概率"
    for e in unverified:
        assert e.source.strip().startswith(UNVERIFIED_MARKER)


def test_render_puts_unverified_in_their_own_section(disease):
    report = render(disease)
    head, _, tail = report.partition("## 二、")
    assert "待核实" in head
    # 待核实的条目必须出现在第一节里
    for e in collect(disease):
        if e.unverified:
            assert e.where in head
        else:
            assert e.where in tail


def test_render_counts_match_reality(disease):
    entries = collect(disease)
    unverified = sum(1 for e in entries if e.unverified)
    report = render(disease)
    assert f"共 {len(entries)} 条" in report
    assert f"{unverified} 条标记为待核实" in report


def test_render_tells_the_reader_how_to_clear_an_entry(disease):
    report = render(disease)
    assert "核对方法" in report
    assert UNVERIFIED_MARKER in report


def test_fully_sourced_ir_says_so(t2dm_ir_path):
    """把待核实标记清掉之后，报告应当明确说没有待核实项。"""
    text = t2dm_ir_path.read_text(encoding="utf-8")
    cleaned = load_ir_text(text.replace(UNVERIFIED_MARKER, ""), origin="cleaned")
    assert not any(e.unverified for e in collect(cleaned))
    assert "本 IR 没有标记为待核实的条目" in render(cleaned)


def test_marker_detection_is_prefix_only():
    """只有**开头**的标记才算待核实 —— 正文里提到这个词不算。"""
    from hisgen.llm.review import Entry

    assert Entry("w", "v", f"{UNVERIFIED_MARKER}待查").unverified
    assert Entry("w", "v", f"  {UNVERIFIED_MARKER}待查").unverified
    assert not Entry("w", "v", "已核对，见指南").unverified
    assert not Entry("w", "v", "指南第3章，另见【待核实】一节").unverified


# ------------------------------------------------------------------ IR 往返


def test_dump_yaml_roundtrips(disease):
    """IR -> YAML -> IR 必须等价。

    产物是给人读的 YAML，也必须是能被重新读回来的 YAML ——
    人改完这一份之后，编译器得能吃。
    """
    text = dump_yaml(disease)
    assert text.strip()
    again = load_ir_text(text, origin="roundtrip")
    assert again.model_dump() == disease.model_dump()


def test_dump_yaml_keeps_chinese_readable(disease):
    text = dump_yaml(disease)
    assert "2型糖尿病" in text
    assert "\\u" not in text


def test_dump_yaml_uses_ir_key_names_not_python_ones(disease):
    """``not`` / ``class`` / ``from`` 是 Python 关键字，字段名带下划线，
    但 IR 里必须是别名 —— 否则人改完读不回来。"""
    payload = disease.model_dump(by_alias=True, exclude_none=True, mode="json")
    for st in payload["states"]:
        for act in st.get("on_enter", []):
            if act["action"] in ("encounter", "referral"):
                assert "class" in act
                assert "encounter_class" not in act
    for t in payload["transitions"]:
        assert "from" in t
        assert "from_" not in t


def test_dump_yaml_is_recompileable(disease, terminology, tmp_path, t2dm_ir_path):
    """往返之后的 IR 必须还能编译出**完全相同**的 GMF。"""
    from hisgen.compiler import compile_module

    again = load_ir_text(dump_yaml(disease), origin="roundtrip")
    assert compile_module(again, terminology) == compile_module(disease, terminology)


def test_write_ir_creates_parent_directories(disease, tmp_path):
    out = write_ir(disease, tmp_path / "nested" / "deep" / "x.yaml")
    assert out.is_file()


# ------------------------------------------------------------------ CLI 边界


def test_build_llm_without_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(LlmError) as exc:
        build_llm()
    assert "DEEPSEEK_API_KEY" in str(exc.value)


def test_generate_rejects_a_non_model_result(terminology):
    """模型返回了乱七八糟的类型时必须报错，而不是把 None 当 IR 用。"""

    class Bad:
        def invoke(self, _messages):
            return 42

    from hisgen.llm import generate_disease

    with pytest.raises(LlmError) as exc:
        generate_disease("brief", terminology, llm=Bad())
    assert "预期之外" in str(exc.value)


def test_generate_accepts_a_dict_result(terminology, t2dm_ir_path):
    """有些模型返回 dict 而不是模型实例，也要接得住。"""
    payload = load_ir(t2dm_ir_path).model_dump(
        by_alias=True, exclude_none=True, mode="json"
    )

    class DictLike:
        def invoke(self, _messages):
            return payload

    from hisgen.llm import generate_disease

    assert isinstance(generate_disease("brief", terminology, llm=DictLike()), Disease)


def test_generate_wraps_model_errors(terminology):
    class Boom:
        def invoke(self, _messages):
            raise TimeoutError("网络超时")

    from hisgen.llm import generate_disease

    with pytest.raises(LlmError) as exc:
        generate_disease("brief", terminology, llm=Boom())
    assert "TimeoutError" in str(exc.value)