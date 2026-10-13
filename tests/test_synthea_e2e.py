"""端到端：真的跑一遍 Synthea，再把产物导成 8 张表。

默认**跳过** —— 这条测试要起 JVM、跑 gradle，几分钟起步。手工跑：

    HISDATA_RUN_SYNTHEA=1 python -m pytest tests/test_synthea_e2e.py -v -s

为什么非要有这么一条：``test_export.py`` 用的是**手工构造的 FHIR**，它只能
证明"给定这种形状的输入，导出层算得对"。而这一轮踩的坑几乎全是**形状本身**：

* 引用是 ``urn:uuid:`` 和 ``Organization?identifier=...|...``，不是 ``Type/id``
* ``Observation`` 不产出 ``referenceRange``
* 药品不在就诊的 EOB 里，它有自己的 ``type = pharmacy`` 的 Claim
* 机构的 ``state`` 是 ``SH`` 这种缩写码

fixture 写错了，测试全绿而真实产物整列为空。所以这里断言的是**真实产物
必须满足的那些"整列不能为空"**，不是具体数值 —— 具体数值随 Synthea 版本和人
数变化，写死了只会天天红。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hisgen import PROJECT_ROOT, SYNTHEA_HOME
from hisgen.export.csvio import write_all
from hisgen.export.derive import Deriver
from hisgen.export.loader import load_dir
from hisgen.export.lookups import load as load_lookups
from hisgen.export.tables import Ctx, build_all, check_foreign_keys

pytestmark = [
    pytest.mark.synthea,
    pytest.mark.skipif(
        os.environ.get("HISDATA_RUN_SYNTHEA") != "1",
        reason="慢测试，设 HISDATA_RUN_SYNTHEA=1 启用",
    ),
]

#: 人数压到最小 —— 这条测试验的是**通路**，不是统计量。
POPULATION = 5
SEED = 12345

#: 缺 Synthea 就跳过，而不是报错：不是每台机器都装了它。
_missing_synthea = not Path(SYNTHEA_HOME).is_dir()


@pytest.fixture(scope="module")
def run_result(tmp_path_factory):
    if _missing_synthea:
        pytest.skip(f"没有 Synthea：{SYNTHEA_HOME}")
    from hisgen.runner.run import RunSpec, SyntheaNotFound, run_synthea

    out = tmp_path_factory.mktemp("synthea")
    spec = RunSpec(
        population=POPULATION,
        seed=SEED,
        output_dir=out,
        timeout_seconds=1800,
    )
    try:
        result = run_synthea(spec, stream=False)
    except SyntheaNotFound as exc:
        pytest.skip(str(exc))

    if result.returncode != 0:
        pytest.fail(f"Synthea 返回 {result.returncode}\n{result.tail(60)}")
    return result


@pytest.fixture(scope="module")
def tables(run_result):
    lookups = load_lookups(PROJECT_ROOT / "shanghai")
    lookups.load_terminology(PROJECT_ROOT / "terminology")
    ctx = Ctx(
        data=load_dir(run_result.fhir_dir),
        derive=Deriver(),
        lookups=lookups,
    )
    return build_all(ctx)


def table_named(tables, name):
    for t in tables:
        if t.name == name:
            return t
    raise AssertionError(f"没有表 {name}")


def values_of(table, name) -> list[str]:
    idx = table.headers.index(name)
    return [r[idx] for r in table.rows]


# ------------------------------------------------------------ Synthea 那一侧


def test_synthea_ran_and_reported_records(run_result):
    """``-p N`` 是「存活 N 人」。用 Synthea 自己的汇总校验，别数文件。"""
    records = run_result.records
    assert records is not None, f"日志里没有 Records 汇总\n{run_result.tail()}"
    total, alive, dead = records
    assert alive == POPULATION
    assert total == alive + dead


def test_every_patient_was_exported(run_result):
    """患者文件数不许少于存活人数 —— "返回码 0 但一个患者都没导出"发生过。"""
    assert len(run_result.patient_files) >= POPULATION


def test_机构与医生信息文件都在(run_result):
    names = {p.name for p in run_result.fhir_files}
    assert any(n.startswith("hospitalInformation") for n in names)
    assert any(n.startswith("practitionerInformation") for n in names)


# ------------------------------------------------------------------ 产物那一侧


def test_all_eight_tables_are_produced(tables):
    assert [t.name for t in tables] == [
        "患者", "机构", "医生", "就诊", "诊断", "医嘱_药品", "检验", "手术操作"
    ]


def test_foreign_keys_are_complete(tables):
    """整列为空就报错 —— 这条是 ``urn:uuid:`` 引用解析断掉时的唯一哨兵。"""
    assert check_foreign_keys(tables) == []


@pytest.mark.parametrize(
    "table,column",
    [
        ("就诊", "patient_id"),
        ("就诊", "机构ID"),
        ("就诊", "医生ID"),
        ("就诊", "就诊ID"),
        ("诊断", "就诊ID"),
        ("医嘱_药品", "就诊ID"),
        ("检验", "就诊ID"),
        ("手术操作", "就诊ID"),
        ("患者", "医保类型"),
        ("医生", "科室"),
        ("医生", "所属机构ID"),
        ("机构", "等级"),
    ],
)
def test_column_is_not_entirely_blank(tables, table, column):
    """这些列**整列为空**都曾经真的发生过，每一列对应一个已修的 bug。"""
    values = values_of(table_named(tables, table), column)
    if not values:
        pytest.skip(f"{table} 这次一个患者都没产出该资源")
    assert any(values), (
        f"{table}.{column} 整列为空（{len(values)} 行）—— "
        "多半是 FHIR 的真实形状又和假设不一样了，"
        "别改这条断言，去查为什么。"
    )


def test_encounter_costs_are_not_all_zero(tables):
    """三重计提错误（引用切分 / EOB.total 是列表 / Blue Button 码）
    曾让 总费用/医保支付/个人自付 **整列为 0.00**。"""
    enc = table_named(tables, "就诊")
    totals = [float(v or 0) for v in values_of(enc, "总费用")]
    assert any(totals), "就诊.总费用 全是 0.00"


def test_drug_amounts_are_not_all_blank(tables):
    """药品不在就诊的 EOB 里，它有自己的 pharmacy Claim。"""
    med = table_named(tables, "医嘱_药品")
    assert any(values_of(med, "金额")), "医嘱_药品.金额 整列为空"


def test_addresses_do_not_contain_a_usps_code(tables):
    """机构的 ``state`` 是 ``SH``，拼进地址就是「SH上海市普陀区…」。"""
    for name in ("患者", "机构"):
        table = table_named(tables, name)
        for value in values_of(table, "地址" if name == "机构" else "居住地址"):
            assert "SH" not in value, f"{name} 地址里混进了缩写码：{value}"
            # line 已含全地址，无条件拼前缀会拼成「上海市宝山区上海市宝山区…」
            assert value.count("上海市") <= 1, f"{name} 地址前缀重复：{value}"


def test_medication_spec_and_route_came_from_the_terminology(tables):
    """规格与给药途径在 FHIR 里没有位置，从 terminology/ 回填。"""
    med = table_named(tables, "医嘱_药品")
    specs = [v for v in values_of(med, "规格") if v]
    routes = [v for v in values_of(med, "给药途径") if v]
    if not values_of(med, "医嘱ID"):
        pytest.skip("这次没有医嘱")
    assert specs, "医嘱_药品.规格 整列为空 —— terminology/medications.yaml 没接上"
    assert routes, "医嘱_药品.给药途径 整列为空"


def test_lab_reference_ranges_came_from_the_terminology(tables):
    """Synthea 的 Observation **不产出 referenceRange**，只能从术语表回填。"""
    lab = table_named(tables, "检验")
    ranges = [v for v in values_of(lab, "参考区间") if v]
    if not values_of(lab, "检验ID"):
        pytest.skip("这次没有检验")
    assert ranges, "检验.参考区间 整列为空 —— terminology/labs.yaml 没接上"


def test_operation_codes_are_visibly_placeholders(tables):
    """``LOCAL_ZH`` 占位体系的 code 就是中文名本身 —— 设计如此，
    产物里必须一眼看得出"这不是真实编码"。"""
    proc = table_named(tables, "手术操作")
    for code, name in zip(values_of(proc, "编码"), values_of(proc, "名称")):
        assert code == name, f"{code} != {name} —— 占位体系不该和名称不同"


def test_written_csv_roundtrips(tables, tmp_path):
    paths = write_all(tables, tmp_path / "out")
    assert len(paths) == 8
    for path in paths:
        assert path.read_bytes().startswith(b"\xef\xbb\xbf"), "CSV 必须带 BOM"