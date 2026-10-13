"""术语白名单：查表、兜底链、未核实告警。

核心不变量：**IR 里不出现码值**。查不到的键名必须是硬错误 ——
放过一个查不到的键，产物里就会出现一个没有出处的编码。
"""

from __future__ import annotations

import pytest

from hisgen.terminology import Terminology
from hisgen.terminology.registry import TerminologyError


# ------------------------------------------------------------------ 加载


def test_loads_all_five_whitelists(terminology):
    assert terminology.systems
    assert terminology.diagnoses
    assert terminology.labs
    assert terminology.medications
    assert terminology.procedures


def test_missing_directory_is_an_error(tmp_path):
    with pytest.raises(TerminologyError):
        Terminology.load(tmp_path / "nope")


def test_system_uris_are_never_invented(terminology):
    """未核实的体系一律用自描述的 urn:hisgen:unverified:* 占位符。

    绝不允许出现"看起来像真的 OID"的 URI —— 那种东西没人能一眼看出是编的。
    """
    for key, sysdef in terminology.systems.items():
        if sysdef.verified:
            continue
        assert sysdef.uri.startswith("urn:hisgen:unverified:"), (
            f"{key} 标为未核实，但 URI 是 {sysdef.uri}；"
            "未核实的体系必须用自描述占位符"
        )


def test_verified_systems_have_real_looking_uris(terminology):
    for key, sysdef in terminology.systems.items():
        if not sysdef.verified:
            continue
        assert sysdef.uri.startswith(("http://", "https://", "urn:")), key
        assert "hisgen:unverified" not in sysdef.uri, key


# ------------------------------------------------------------------ 查表


def test_diagnosis_lookup_returns_a_code(terminology):
    code = terminology.diagnosis("t2dm")
    assert code.code == "E11"
    assert code.display == "2型糖尿病"


def test_lab_lookup(terminology):
    lab = terminology.lab("fasting_plasma_glucose")
    assert lab.code.code == "1558-6"
    assert lab.unit_zh == "mmol/L"


def test_medication_lookup(terminology):
    med = terminology.medication("metformin")
    assert med.code.code == "A10BA02"
    assert med.dosage_zh


def test_procedure_lookup(terminology):
    proc = terminology.procedure("fundus_examination")
    assert proc.name_zh


def test_unknown_key_lists_the_known_ones(terminology):
    with pytest.raises(TerminologyError) as exc:
        terminology.medication("no_such_drug")
    message = str(exc.value)
    assert "no_such_drug" in message
    assert "medications.yaml" in message
    # 报错必须带上已知键，否则改起来只能靠猜
    assert "metformin" in message


def test_unknown_system_lists_the_known_ones(terminology):
    with pytest.raises(TerminologyError) as exc:
        terminology.system("NOPE")
    assert "ICD10_WHO" in str(exc.value)


# ------------------------------------------------------------------ 兜底链


def test_diagnosis_falls_back_to_who_with_a_caveat(terminology):
    """没填 gb14396 时必须走 WHO ICD-10，并且留下可追溯的提示。"""
    code = terminology.diagnosis("t2dm")
    assert code.system == "ICD10_WHO"
    assert code.caveat
    assert "GB/T 14396" in code.caveat


def test_medication_falls_back_to_atc_with_a_caveat(terminology):
    med = terminology.medication("metformin")
    assert med.code.system == "ATC"
    assert "WS/T 778-2021" in med.code.caveat


def test_procedure_falls_back_to_local_zh(terminology):
    proc = terminology.procedure("fundus_examination")
    assert proc.code.system == "LOCAL_ZH"
    assert "ICD-9-CM-3" in proc.code.caveat


def test_local_code_carries_the_chinese_name_as_its_code(terminology):
    """LOCAL_ZH 的 code 就是中文名 —— 一眼能看出不是真实编码。"""
    code = terminology.local("内分泌科")
    assert code.code == "内分泌科"
    assert code.uri == "urn:hisgen:local:zh"


def test_gmf_shape_is_exactly_three_keys(terminology):
    gm = terminology.diagnosis("t2dm").to_gmf()
    assert set(gm) == {"system", "code", "display"}


# ------------------------------------------------------------------ 使用记录


def test_unverified_system_use_is_recorded(terminology):
    terminology.local("某科室")  # LOCAL_ZH 是 verified，不该进未核实清单
    terminology.diagnosis("t2dm")  # WHO ICD-10 已核实，但缺国标码 -> 进 caveat
    assert "LOCAL_ZH" not in terminology.used_unverified
    assert any("GB/T 14396" in c for c in terminology.used_caveats)


def test_caveats_are_reported_once_each(terminology):
    terminology.diagnosis("t2dm")
    terminology.diagnosis("t2dm")
    report = terminology.unverified_report()
    # 重复使用同一个码，报告里不该出现两遍
    assert report.count("WHO ICD-10 E11（非 GB/T 14396-2016 六位码）") == 1


def test_report_is_markdown(terminology):
    terminology.diagnosis("t2dm")
    report = terminology.unverified_report()
    assert report.startswith("# 待核实编码体系")
    assert "| 体系 |" in report or "## 缺国标码" in report


def test_report_says_so_when_nothing_is_used():
    fresh = Terminology.load()
    report = fresh.unverified_report()
    assert "未使用任何未核实的编码体系" in report


# ------------------------------------------------------------ 白名单自身的完整性


def test_every_lab_has_a_reference_range(terminology):
    for key, lab in terminology.labs.items():
        low, high = lab.reference_range
        assert low <= high, key


def test_every_medication_has_a_dosage(terminology):
    for key, med in terminology.medications.items():
        assert med.dosage_zh, f"{key} 没有 default_dosage_zh"


def test_every_entry_has_a_source(terminology):
    for group in (
        terminology.diagnoses,
        terminology.labs,
        terminology.medications,
        terminology.procedures,
    ):
        for key, entry in group.items():
            assert entry.source, f"{key} 没有 source"


def test_all_chinese_names_are_actually_chinese(terminology):
    """display 必须是中文 —— 出现英文说明白名单里漏翻了。"""
    import re

    has_cjk = re.compile(r"[一-鿿]")

    def name_of(entry) -> str:
        # diagnoses 存的是 Code（字段叫 display），其余是各自的结构体（叫 name_zh）
        return getattr(entry, "name_zh", None) or getattr(entry, "display", "")

    for group in (
        terminology.diagnoses,
        terminology.labs,
        terminology.medications,
        terminology.procedures,
    ):
        for key, entry in group.items():
            name = name_of(entry)
            assert has_cjk.search(name), f"{key}: {name}"