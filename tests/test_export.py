"""导出层：FHIR -> 8 张关系型表。

用**手工构造的 FHIR** 来测：形状逐条照 ``output/`` 里真实跑出来的 FHIR 抄，
不依赖真的跑一遍 Synthea（真跑那一路另有一个慢测试），但也不许比真实输出
更"规范"。

这一条是踩过坑才立的规矩。早先的 fixture 用的是 ``Practitioner/prac-1`` 这种
**带资源类型的引用**，还带了一个 ``PractitionerRole`` 资源 —— 而 Synthea 两样
都不产出：它的引用是 ``urn:uuid:...`` 和 ``Organization?identifier=...|...``，
``PractitionerRole`` 只在 ``use_us_core_ig = true`` 时才出现。于是测试全绿，
真实数据里 就诊.patient_id、四张表的 就诊ID、所有费用**整列为空**。
fixture 比真实输出更理想，就等于什么都没测。
"""

from __future__ import annotations

import csv

import pytest

from hisgen.export.derive import is_cn_id
from hisgen.export.loader import ExportError, load_dir
from hisgen.export.lookups import load as load_lookups
from hisgen.export.tables import (
    Ctx,
    Deriver,
    _dosage_text,
    _full_address,
    _insurer_name,
    build_all,
    check_foreign_keys,
)
from hisgen.export.csvio import write_all

from conftest import TERMINOLOGY_DIR, write_bundle, write_resource

#: 一个通过 GB 11643 校验的身份证号（用于验证"只认合法号"的逻辑）
VALID_CN_ID = "310101199001010012"
#: Synthea 原版生成的美国 SSN 形态 —— 必须被拒
FAKE_SSN = "123-45-6789"

PATIENT_ID = "p-0001"
ORG_ID = "org-1"
ORG2_ID = "org-2"
#: 机构地址的 ``state`` 是 USPS 缩写码 ``SH``（不是「上海市」），
#: ``line`` 里含区名 —— 真实输出就是这样，见 :func:`_region`。
ORG3_ID = "org-3"
PRAC_ID = "prac-1"
PRAC_NPI = "9999991497"
ENC1 = "enc-1"
ENC2 = "enc-2"
COND_ID = "cond-1"
MED_ID = "med-1"
OBS_NORMAL = "obs-1"
OBS_HIGH = "obs-2"
PROC_ID = "proc-1"

#: Synthea 写引用用的两个 system。机构用他自己的，医生用 NPI。
SYNTHEA_SYSTEM = "https://github.com/synthetichealth/synthea"
NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"

#: Blue Button 的结算类别码。Synthea 写的是完整 URL，不是 benefit/copay 短码。
BB = "https://bluebutton.cms.gov/resources/variables"


def _urn(resource_id: str) -> str:
    """bundle 内部引用 —— Synthea 对 subject / encounter 用的形态，**没有斜杠**。"""
    return f"urn:uuid:{resource_id}"


def _org_ref(org_id: str) -> dict:
    return {"reference": f"Organization?identifier={SYNTHEA_SYSTEM}|{org_id}"}


def _prac_ref() -> dict:
    return {"reference": f"Practitioner?identifier={NPI_SYSTEM}|{PRAC_NPI}"}


@pytest.fixture
def fhir_dir(tmp_path):
    root = tmp_path / "fhir"

    patient = {
        "resourceType": "Patient",
        "id": PATIENT_ID,
        "identifier": [
            {"system": SYNTHEA_SYSTEM, "value": PATIENT_ID},
            {"system": "http://hl7.org/fhir/sid/us-ssn", "value": VALID_CN_ID},
        ],
        "name": [{"text": "王冠杰", "family": "王", "given": ["冠杰"]}],
        "gender": "male",
        "birthDate": "1990-01-01",
        "address": [
            {
                # Synthea 把**完整地址**写在 line 里，city/state 是拆出来的副本
                "line": ["上海市静安区南京西路1234弄5号601室"],
                "city": "静安区",
                "state": "上海市",
                "postalCode": "200040",
            }
        ],
        "telecom": [{"system": "phone", "value": "13800138000"}],
    }

    org1 = {
        "resourceType": "Organization",
        "id": ORG_ID,
        "identifier": [{"system": SYNTHEA_SYSTEM, "value": ORG_ID}],
        "name": "上海市第六人民医院",
        "type": [{"coding": [{"system": "urn:hisgen:local:zh", "code": "Healthcare Provider"}]}],
        "address": [
            {
                "line": ["上海市徐汇区宜山路600号"],
                "city": "徐汇区",
                "state": "上海市",
                "postalCode": "200233",
            }
        ],
        "telecom": [{"system": "phone", "value": "021-64369181"}],
    }
    org2 = {
        "resourceType": "Organization",
        "id": ORG2_ID,
        "identifier": [{"system": SYNTHEA_SYSTEM, "value": ORG2_ID}],
        "name": "上海市静安区中心医院",
        "address": [{"city": "静安区", "state": "上海市"}],
    }
    # 机构的 state 是缩写码，患者的 state 才是「上海市」—— 两者的差别
    # 就是地址里会不会冒出个「SH」。
    org3 = {
        "resourceType": "Organization",
        "id": ORG3_ID,
        "identifier": [{"system": SYNTHEA_SYSTEM, "value": ORG3_ID}],
        "name": "上海市普陀区中心医院",
        "address": [
            {
                "line": ["上海市普陀区宛平南路414号"],
                "city": "普陀区",
                "state": "SH",
                "postalCode": "200062",
            }
        ],
    }

    # Practitioner 上**没有**科室、没有机构、没有 PractitionerRole ——
    # 真实输出就是这样，只有 NPI + 姓名 + 性别。
    practitioner = {
        "resourceType": "Practitioner",
        "id": PRAC_ID,
        "identifier": [{"system": NPI_SYSTEM, "value": PRAC_NPI}],
        "name": [{"text": "李明", "family": "李", "given": ["明"]}],
        "gender": "female",
    }

    def enc(eid, start, end, klass, dept, org, cond_ref):
        return {
            "resourceType": "Encounter",
            "id": eid,
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                      "code": klass},
            "type": [{"coding": [{"system": "urn:hisgen:local:zh", "code": dept,
                                 "display": dept}]}],
            "subject": {"reference": _urn(PATIENT_ID)},
            "participant": [{"individual": _prac_ref()}],
            "period": {"start": start, "end": end},
            "serviceProvider": _org_ref(org),
            "reasonCode": [{"coding": [{"code": "E11", "display": "2型糖尿病"}]}],
            "diagnosis": [{"condition": {"reference": _urn(cond_ref)}}],
        }

    encounter1 = enc(ENC1, "2024-03-01T09:00:00+08:00", "2024-03-01T10:30:00+08:00",
                     "AMB", "内分泌科", ORG_ID, COND_ID)
    encounter2 = enc(ENC2, "2025-03-01T09:00:00+08:00", "2025-03-01T10:00:00+08:00",
                     "AMB", "内分泌科", ORG2_ID, COND_ID)

    condition = {
        "resourceType": "Condition",
        "id": COND_ID,
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "code": {
            "coding": [{"system": "http://hl7.org/fhir/sid/icd-10", "code": "E11",
                        "display": "2型糖尿病"}]
        },
        "subject": {"reference": _urn(PATIENT_ID)},
        "encounter": {"reference": _urn(ENC1)},
        "onsetDateTime": "2024-03-01T09:05:00+08:00",
        "recordedDate": "2024-03-01T09:05:00+08:00",
    }

    medication = {
        "resourceType": "MedicationRequest",
        "id": MED_ID,
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {
            "coding": [{"system": "http://www.whocc.no/atc", "code": "A10BA02",
                        "display": "二甲双胍"}]
        },
        "subject": {"reference": _urn(PATIENT_ID)},
        "encounter": {"reference": _urn(ENC1)},
        "authoredOn": "2024-03-01T09:20:00+08:00",
        # 结构化用法用量。FhirR4 从 IR 的 dosage{amount,frequency,period,unit}
        # 翻译过来就是这三层，**doseQuantity 只有 value 没有 unit**。
        "dosageInstruction": [
            {
                "sequence": 1,
                "timing": {"repeat": {"frequency": 2, "period": 1.0, "periodUnit": "d"}},
                "asNeededBoolean": False,
                "doseAndRate": [
                    {"type": {"coding": [{"code": "ordered"}]},
                     "doseQuantity": {"value": 1.0}}
                ],
            }
        ],
    }

    def observation(oid, code, display, value, unit, when):
        # **没有 referenceRange** —— Synthea 不产出这个字段。
        # 参考区间在导出层从 terminology/labs.yaml 回填。
        return {
            "resourceType": "Observation",
            "id": oid,
            "status": "final",
            "category": [{"coding": [{"code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": code,
                                 "display": display}]},
            "subject": {"reference": _urn(PATIENT_ID)},
            "encounter": {"reference": _urn(ENC1)},
            "effectiveDateTime": when,
            "issued": when,
            "valueQuantity": {"value": value, "unit": unit,
                              "system": "http://unitsofmeasure.org", "code": unit},
        }

    obs_normal = observation(OBS_NORMAL, "1558-6", "空腹血糖", 5.4, "mmol/L",
                             "2024-03-01T09:10:00+08:00")
    obs_high = observation(OBS_HIGH, "4548-4", "糖化血红蛋白", 8.2, "%",
                           "2024-03-01T09:11:00+08:00")

    procedure = {
        "resourceType": "Procedure",
        "id": PROC_ID,
        "status": "completed",
        # LOCAL_ZH 占位体系：code 就是中文名本身，一眼看得出不是真实编码
        "code": {"coding": [{"system": "urn:hisgen:local:zh", "code": "眼底检查",
                             "display": "眼底检查"}]},
        "subject": {"reference": _urn(PATIENT_ID)},
        "encounter": {"reference": _urn(ENC1)},
        "performedPeriod": {"start": "2024-03-01T09:30:00+08:00"},
    }

    # Claim 与 EOB 金额**故意不同**：用来验证导出层只认 EOB，不会把两者相加。
    # Claim.total 是**单个 Money**（R4 如此），且没有顶层 insurer ——
    # 参保信息在 insurance[].coverage.display 上。
    claim = {
        "resourceType": "Claim",
        "id": "claim-1",
        "status": "active",
        "total": {"value": 999.0, "currency": "USD"},
        "insurance": [{"sequence": 1, "focal": True,
                       "coverage": {"display": "上海市城乡居民基本医疗保险"}}],
        "item": [
            {
                "sequence": 1,
                "encounter": [{"reference": _urn(ENC1)}],
                "productOrService": {"coding": [{"code": "99213"}]},
                "net": {"value": 999.0},
                "quantity": {"value": 1},
                "adjudication": [
                    {"category": {"coding": [{"code": f"{BB}/line_prvdr_pmt_amt"}]},
                     "amount": {"value": 999.0}},
                ],
            }
        ],
    }
    # EOB.total 是**列表**，adjudication 用 Blue Button 完整 URL
    eob = {
        "resourceType": "ExplanationOfBenefit",
        "id": "eob-1",
        "status": "active",
        "insurer": {"display": "上海市城乡居民基本医疗保险"},
        "total": [
            {"category": {"coding": [{"code": "submitted"}],
                          "text": "Submitted Amount"},
             "amount": {"value": 300.0, "currency": "USD"}},
        ],
        "payment": {"amount": {"value": 210.0}},
        "item": [
            {
                "sequence": 1,
                "encounter": [{"reference": _urn(ENC1)}],
                "net": {"value": 300.0},
                "adjudication": [
                    {"category": {"coding": [{"code": f"{BB}/line_sbmtd_chrg_amt"}]},
                     "amount": {"value": 300.0}},
                    {"category": {"coding": [{"code": f"{BB}/line_prvdr_pmt_amt"}]},
                     "amount": {"value": 210.0}},
                    {"category": {"coding": [{"code": f"{BB}/line_coinsrnc_amt"}]},
                     "amount": {"value": 70.0}},
                    {"category": {"coding": [{"code": f"{BB}/line_bene_ptb_ddctbl_amt"}]},
                     "amount": {"value": 20.0}},
                ],
            }
        ],
    }

    # 药品**不在就诊的 Claim 里** —— ``HealthRecord.medicationStart`` 写着
    # "Do not add medications to the Encounter claim. Medications submit
    # separate claims."。所以每条医嘱有自己的 pharmacy Claim，
    # 由 ``prescription`` 指回 MedicationRequest，**item 上没有 net**，
    # 钱全在 ``total`` 上。不认这一条，医嘱金额就永远是 0.00。
    pharmacy_claim = {
        "resourceType": "Claim",
        "id": "claim-2",
        "status": "active",
        "type": {"coding": [
            {"system": "http://terminology.hl7.org/CodeSystem/claim-type",
             "code": "pharmacy"}
        ]},
        "use": "claim",
        "patient": {"reference": _urn(PATIENT_ID)},
        "provider": _org_ref(ORG_ID),
        "prescription": {"reference": _urn(MED_ID)},
        "insurance": [{"sequence": 1, "focal": True,
                       "coverage": {"display": "上海市城乡居民基本医疗保险"}}],
        "item": [
            {
                "sequence": 1,
                "productOrService": {
                    "coding": [{"system": "http://www.whocc.no/atc",
                                "code": "A10BA02", "display": "二甲双胍"}]
                },
                "encounter": [{"reference": _urn(ENC1)}],
            }
        ],
        "total": {"value": 45.0, "currency": "USD"},
    }
    pharmacy_eob = {
        "resourceType": "ExplanationOfBenefit",
        "id": "eob-2",
        "status": "active",
        "claim": {"reference": _urn("claim-2")},
        "insurer": {"display": "上海市城乡居民基本医疗保险"},
        "total": [
            {"category": {"coding": [{"code": "submitted"}],
                          "text": "Submitted Amount"},
             "amount": {"value": 45.0, "currency": "USD"}},
        ],
        "item": [
            {
                "sequence": 1,
                "encounter": [{"reference": _urn(ENC1)}],
                "productOrService": {
                    "coding": [{"system": "http://www.whocc.no/atc",
                                "code": "A10BA02", "display": "二甲双胍"}]
                },
                "adjudication": [
                    {"category": {"coding": [{"code": f"{BB}/line_sbmtd_chrg_amt"}]},
                     "amount": {"value": 45.0}},
                    {"category": {"coding": [{"code": f"{BB}/line_prvdr_pmt_amt"}]},
                     "amount": {"value": 36.0}},
                    {"category": {"coding": [{"code": f"{BB}/line_coinsrnc_amt"}]},
                     "amount": {"value": 9.0}},
                ],
            }
        ],
    }

    write_bundle(
        root, PATIENT_ID,
        patient, encounter1, encounter2, condition, medication,
        obs_normal, obs_high, procedure, claim, eob,
        # 药品处方单与它的 EOB **排在就诊的后面** —— 顺序是有意的：
        # 费用归集若没把 pharmacy 排除掉，后写的这一张会把整次就诊的
        # 345 元覆盖成 45 元。排前面就测不出这个 bug。
        pharmacy_claim, pharmacy_eob,
    )
    write_resource(root, "hospitalInformation1", org1)
    write_resource(root, "hospitalInformation2", org2)
    write_resource(root, "hospitalInformation3", org3)
    # practitionerInformation 里**只有 Practitioner**，没有 PractitionerRole
    write_bundle(root, "practitionerInformation1", practitioner)
    return root


@pytest.fixture
def ctx(fhir_dir, tmp_path):
    """建好索引的导出上下文。给需要直接问 ctx 的测试用。"""
    shanghai = tmp_path / "shanghai"
    shanghai.mkdir()
    (shanghai / "organization_levels.csv").write_text(
        "机构名称,等级,类型\n上海市第六人民医院,三级甲等,综合医院\n",
        encoding="utf-8-sig",
    )
    (shanghai / "department_map.csv").write_text(
        "源科室名,中文科室\nENDOCRINOLOGY,内分泌科\n内分泌科,内分泌科\n",
        encoding="utf-8-sig",
    )

    lookups = load_lookups(shanghai)
    # 真实术语表（不是造的）—— 医嘱的规格/给药途径与检验的参考区间
    # 都是从 terminology/ 回填的，用真表才能测到真实值。
    lookups.load_terminology(TERMINOLOGY_DIR)

    return Ctx(
        data=load_dir(fhir_dir),
        derive=Deriver(),
        lookups=lookups,
        mask_national_id=False,
    )


@pytest.fixture
def tables(ctx):
    return build_all(ctx)


def table_named(tables, name):
    for t in tables:
        if t.name == name:
            return t
    raise AssertionError(f"没有表 {name}，实际有 {[t.name for t in tables]}")


def row_of(table, column, value):
    idx = table.headers.index(column)
    for row in table.rows:
        if row[idx] == value:
            return row
    raise AssertionError(f"{table.name} 里没有 {column}={value}")


def cell(table, column, value, wanted):
    row = row_of(table, column, value)
    return row[table.headers.index(wanted)]


# ------------------------------------------------------------------ 总体


def test_all_eight_tables_are_produced(tables):
    assert [t.name for t in tables] == [
        "患者", "机构", "医生", "就诊", "诊断", "医嘱_药品", "检验", "手术操作",
    ]


def test_headers_match_the_design_document(tables):
    assert table_named(tables, "患者").headers == [
        "patient_id", "姓名", "性别", "出生日期", "身份证号", "手机号", "民族",
        "医保类型", "医保号", "居住地址", "区县", "邮编",
    ]
    assert table_named(tables, "就诊").headers == [
        "就诊ID", "patient_id", "就诊类型", "科室", "机构ID", "医生ID",
        "开始时间", "结束时间", "就诊原因", "总费用", "医保支付", "个人自付",
    ]
    assert table_named(tables, "检验").headers[-2:] == ["采样时间", "报告时间"]


def test_no_flat_packed_columns_anywhere(tables):
    """现状的毛病是把多个值用 ``；`` 拍平成一格，这里必须一格一值。"""
    for t in tables:
        for row in t.rows:
            for value in row:
                assert "；" not in value, f"{t.name} 里出现了分号拍平：{value}"


def test_deterministic_output(fhir_dir, tmp_path):
    """同样的输入导出两次必须完全一致 —— 否则下游没法用 diff 做增量。"""
    from hisgen.export.lookups import Lookups

    def build():
        ctx = Ctx(load_dir(fhir_dir), Deriver(), Lookups(), mask_national_id=False)
        return build_all(ctx)

    assert build() == build()


# ------------------------------------------------------------------ 患者


def test_patient_demographics(tables):
    patient = row_of(table_named(tables, "患者"), "patient_id", PATIENT_ID)
    assert patient[1] == "王冠杰"
    assert patient[2] == "男"
    assert patient[3] == "1990-01-01"
    assert patient[5] == "13800138000"


def test_patient_address_is_shanghai(tables):
    patient = row_of(table_named(tables, "患者"), "patient_id", PATIENT_ID)
    assert patient[9] == "上海市静安区南京西路1234弄5号601室"
    assert patient[10] == "静安区"
    assert patient[11] == "200040"


def test_national_id_only_accepts_a_valid_gb11643_number(tables):
    patient = row_of(table_named(tables, "患者"), "patient_id", PATIENT_ID)
    assert patient[4] == VALID_CN_ID
    assert is_cn_id(VALID_CN_ID)


def test_fake_ssn_is_rejected():
    """美国 SSN 形态的九位数绝不能当成身份证号。"""
    assert not is_cn_id(FAKE_SSN)
    assert not is_cn_id("123456789")
    assert not is_cn_id("31010119900101001")  # 17 位
    assert not is_cn_id("310101199001010013")  # 校验位错


def test_national_id_is_blank_when_no_valid_id_exists(tmp_path):
    """没打 Java 补丁时 Synthea 只给 SSN —— 那一列必须是空的。

    这是**故意**的：把美国 SSN 当中国身份证号填进去，
    比留空严重得多。
    """
    root = tmp_path / "unpatched"
    write_bundle(
        root,
        "p-9",
        {
            "resourceType": "Patient",
            "id": "p-9",
            "identifier": [
                {"system": "http://hl7.org/fhir/sid/us-ssn", "value": FAKE_SSN},
            ],
            "name": [{"text": "张三"}],
            "gender": "male",
            "birthDate": "1980-05-05",
        },
    )
    ctx = Ctx(load_dir(root), Deriver(), load_lookups(None))
    patient = row_of(table_named(build_all(ctx), "患者"), "patient_id", "p-9")
    assert patient[4] == ""


def test_national_id_masking():
    derive = Deriver()
    masked = derive.mask_national_id(VALID_CN_ID)
    assert masked.startswith("310101")
    assert masked.endswith("0012")
    assert "*" in masked


def test_patient_table_masks_national_id_when_asked(tmp_path, fhir_dir):
    ctx = Ctx(load_dir(fhir_dir), Deriver(), load_lookups(None),
              mask_national_id=True)
    patient = row_of(table_named(build_all(ctx), "患者"), "patient_id", PATIENT_ID)
    assert patient[4] != VALID_CN_ID
    assert "*" in patient[4]


def test_ethnicity_is_stable_and_from_the_configured_distribution(tables):
    derive = Deriver()
    assert derive.ethnicity(PATIENT_ID) == derive.ethnicity(PATIENT_ID)
    assert derive.ethnicity(PATIENT_ID) in derive.ethnicity_weights


def test_insurance_id_encodes_the_district(tables):
    value = cell(table_named(tables, "患者"), "patient_id", PATIENT_ID, "医保号")
    assert value.startswith("SH310106"), value  # 静安区 = 310106
    assert len(value) == 14


def test_insurance_type_comes_from_the_payer_name(tables):
    value = cell(table_named(tables, "患者"), "patient_id", PATIENT_ID, "医保类型")
    assert value == "上海市城乡居民基本医疗保险"


def test_no_insurance_sentinel_is_not_an_insurance_type():
    """``NO_INSURANCE`` 是 Synthea 的哨兵，不是险种 —— 不能写进 医保类型。

    不滤掉的话，中途断保的患者会得到
    「上海市城镇职工基本医疗保险基金；NO_INSURANCE」这种四不像的值。
    """
    assert _insurer_name({"insurer": {"display": "NO_INSURANCE"}}) == ""
    assert _insurer_name(
        {"insurance": [{"coverage": {"display": "NO_INSURANCE"}}]}
    ) == ""
    assert _insurer_name(
        {"insurer": {"display": "上海市城乡居民基本医疗保险"}}
    ) == "上海市城乡居民基本医疗保险"


# ------------------------------------------------------------------ 机构 / 医生


def test_organization_gets_level_from_the_lookup_file(tables):
    org = table_named(tables, "机构")
    assert cell(org, "机构ID", ORG_ID, "等级") == "三级甲等"
    assert cell(org, "机构ID", ORG_ID, "机构名称") == "上海市第六人民医院"
    assert cell(org, "机构ID", ORG_ID, "区县") == "徐汇区"
    assert cell(org, "机构ID", ORG_ID, "电话") == "021-64369181"


def test_organization_without_a_lookup_row_has_a_blank_level(tables):
    assert cell(table_named(tables, "机构"), "机构ID", ORG2_ID, "等级") == ""


def test_address_does_not_leak_the_usps_state_code(tables):
    """机构的 ``state`` 是 ``SH`` 这种缩写码，不许拼进地址。

    ``line`` 已经含区名时原样返回；机构与患者的差别就在这个 state 上，
    早先无条件拼 ``state + line`` 拼出了「SH上海市普陀区宛平南路414号」。
    """
    org = table_named(tables, "机构")
    assert cell(org, "机构ID", ORG3_ID, "地址") == "上海市普陀区宛平南路414号"
    # 患者那边 state 是「上海市」，走的是另一条分支，也不能拼重
    patient = row_of(table_named(tables, "患者"), "patient_id", PATIENT_ID)
    assert patient[9] == "上海市静安区南京西路1234弄5号601室"


def test_address_prefixes_only_when_the_line_lacks_one(tables):
    """``line`` 里既没有省市也没有区时，才补前缀。"""
    assert _full_address({
        "line": ["长寿支路182弄23号"],
        "city": "宝山区",
        "state": "上海市",
    }) == "上海市宝山区长寿支路182弄23号"
    assert _full_address({"city": "静安区", "state": "上海市"}) == "上海市静安区"


def test_practitioner_department_is_mapped_to_chinese(tables):
    prac = table_named(tables, "医生")
    assert cell(prac, "医生ID", PRAC_ID, "科室") == "内分泌科"
    assert cell(prac, "医生ID", PRAC_ID, "所属机构ID") == ORG_ID
    assert cell(prac, "医生ID", PRAC_ID, "姓名") == "李明"
    assert cell(prac, "医生ID", PRAC_ID, "性别") == "女"


def test_practitioner_title_is_generated_and_stable(tables):
    title = cell(table_named(tables, "医生"), "医生ID", PRAC_ID, "职称")
    assert title in Deriver().title_weights
    assert title == Deriver().title(PRAC_ID)


# ------------------------------------------------------------------ 就诊


def test_encounter_row(tables):
    enc = table_named(tables, "就诊")
    row = row_of(enc, "就诊ID", ENC1)
    assert row[1] == PATIENT_ID
    assert row[2] == "门诊"
    assert row[3] == "内分泌科"
    assert row[4] == ORG_ID
    assert row[5] == PRAC_ID
    assert row[6] == "2024-03-01 09:00:00"
    assert row[7] == "2024-03-01 10:30:00"
    assert row[8] == "2型糖尿病"


def test_encounters_are_sorted_by_start_time(tables):
    enc = table_named(tables, "就诊")
    starts = [r[enc.headers.index("开始时间")] for r in enc.rows]
    assert starts == sorted(starts)


def test_cost_uses_eob_not_claim(tables):
    """Claim 与 EOB 说的是同一笔账，相加就翻倍。只认 EOB。

    药费（45）**并进来**：Synthea 把处方单的 ``billablePeriod`` 与
    ``item[].encounter`` 都指向这次就诊，药费属于这次就诊是它的语义；
    不并的话 ``医嘱_药品.金额`` 之和与 ``就诊.总费用`` 对不上账。
    """
    enc = table_named(tables, "就诊")
    row = row_of(enc, "就诊ID", ENC1)
    assert row[9] == "345.00"   # 总费用：EOB.total 300 + 药费 45，不是 Claim 的 999
    assert row[10] == "246.00"  # 医保支付：210 + 36
    assert row[11] == "99.00"   # 个人自付：(70+20) + 9


def test_pharmacy_claim_does_not_overwrite_the_encounter_cost(tables):
    """药品处方单排在就诊结算单**后面**时，不许把整次就诊的费用覆盖掉。

    这条是回归测试：归集费用时若不按 ``type = pharmacy`` 排除处方单，
    它那 45 元会盖掉 345 元 —— 而且因为按导入顺序「后写赢」，
    只在处方单恰好排后面时才发作，肉眼极难发现。
    """
    enc = table_named(tables, "就诊")
    assert row_of(enc, "就诊ID", ENC1)[9] == "345.00"


def test_encounter_without_a_claim_has_zero_cost(tables):
    enc = table_named(tables, "就诊")
    row = row_of(enc, "就诊ID", ENC2)
    assert row[9] == row[10] == row[11] == "0.00"


def test_encounter_class_is_translated(tables):
    enc = table_named(tables, "就诊")
    assert row_of(enc, "就诊ID", ENC1)[2] == "门诊"


# ------------------------------------------------------------------ 诊断


def test_diagnosis_row(tables):
    diag = table_named(tables, "诊断")
    row = row_of(diag, "诊断ID", COND_ID)
    assert row[1] == ENC1
    # 编码体系出的是标准名，不是 URI —— 建库时对照的是「该查哪份标准」。
    # URI 与标签的对照表在导出报告里。
    assert row[2] == "ICD-10 (WHO)"
    assert row[3] == "E11"
    assert row[4] == "2型糖尿病"
    assert row[5] == "主要诊断"
    assert row[6] == "2024-03-01 09:05:00"


# ------------------------------------------------------------------ 医嘱_药品


def test_medication_row(tables):
    med = table_named(tables, "医嘱_药品")
    row = row_of(med, "医嘱ID", MED_ID)
    assert row[1] == ENC1
    assert row[2] == "A10BA02"
    assert row[3] == "二甲双胍"
    # 规格与给药途径从 terminology/medications.yaml 回填 —— FHIR 里没有这两个位置
    assert row[4] == "0.5g"
    assert row[7] == "口服"
    # 频次从 dosageInstruction.timing.repeat 的结构读出来
    assert row[6] == "每日2次"
    assert row[8] == "2024-03-01 09:20:00"
    # 金额取自该医嘱的药品处方单（Claim.type = pharmacy）的 total。
    # **就诊的 EOB 里没有药品行**，早先在 item 里翻，翻出来永远是 0.00。
    assert row[11] == "45.00"


def test_medication_quantity_and_unit_price_are_blank(tables):
    """数量与单价**留空**，不留 0.00。

    Synthea 的药品成本是「一张处方一个总额」，``MedicationRequest`` 里
    没有 ``dispenseRequest``、没有包装数量 —— 这两个数在合成数据里
    没有出处。写 0.00 读起来像「这药不要钱」，比空值危险得多。
    """
    med = table_named(tables, "医嘱_药品")
    row = row_of(med, "医嘱ID", MED_ID)
    assert row[9] == ""   # 数量
    assert row[10] == ""  # 单价


def test_medication_without_a_pharmacy_claim_gets_zero(ctx):
    """没有处方单就写 0.00，不猜。"""
    assert ctx.amount_of_medication("no-such-med") == 0.0
    assert ctx.amount_of_medication("") == 0.0


@pytest.mark.parametrize(
    "repeat,expected",
    [
        ({"frequency": 2, "period": 1.0, "periodUnit": "d"}, "每日2次"),
        ({"frequency": 3, "period": 1.0, "periodUnit": "d"}, "每日3次"),
        ({"frequency": 1, "period": 1.0, "periodUnit": "d"}, "每日1次"),
        ({"frequency": 1, "period": 1.0, "periodUnit": "wk"}, "每周1次"),
        # period != 1 时要说清楚是一个周期几次
        ({"frequency": 2, "period": 7.0, "periodUnit": "d"}, "每7日2次"),
    ],
)
def test_dosage_frequency_from_structure(repeat, expected):
    assert _dosage_text({"timing": {"repeat": repeat}})[1] == expected


def test_dosage_frequency_falls_back_to_timing_text():
    """结构读不出来时退回 timing.code.text，而不是留空。"""
    instruction = {"timing": {"code": {"text": "q2d"}}}
    assert _dosage_text(instruction)[1] == "q2d"


def test_dosage_handles_empty_instruction():
    assert _dosage_text({}) == ("", "")


# ------------------------------------------------------------------ 检验


def test_normal_lab_has_no_abnormal_flag(tables):
    lab = table_named(tables, "检验")
    row = row_of(lab, "检验ID", OBS_NORMAL)
    assert row[3] == "空腹血糖"
    assert row[4] == "5.4"
    assert row[5] == "mmol/L"
    assert row[6] == "3.9-6.1"
    assert row[7] == ""  # 正常


def test_high_lab_is_flagged_up(tables):
    lab = table_named(tables, "检验")
    row = row_of(lab, "检验ID", OBS_HIGH)
    assert row[7] == "↑"
    assert row[8] == "2024-03-01 09:11:00"
    assert row[9] == "2024-03-01 09:11:00"


# ------------------------------------------------------------------ 手术操作


def test_procedure_row(tables):
    proc = table_named(tables, "手术操作")
    row = row_of(proc, "操作ID", PROC_ID)
    assert row[1] == ENC1
    assert row[3] == "眼底检查"
    assert row[4] == "2024-03-01 09:30:00"


# ------------------------------------------------------------------ 外键


def test_foreign_keys_are_complete(tables):
    assert check_foreign_keys(tables) == []


def test_orphan_foreign_key_is_detected(tables):
    enc = table_named(tables, "就诊")
    broken = type(enc)(
        enc.name, list(enc.headers), [list(r) for r in enc.rows]
    )
    idx = broken.headers.index("patient_id")
    broken.rows[0][idx] = "does-not-exist"
    swapped = [broken if t.name == "就诊" else t for t in tables]
    problems = check_foreign_keys(swapped)
    assert problems
    assert "patient_id" in problems[0]


def test_blank_foreign_key_is_not_an_orphan(tables):
    """**个别**空值表示"这个资源确实不挂在任何就诊上"，不是错误。"""
    enc = table_named(tables, "就诊")
    idx = enc.headers.index("医生ID")
    enc.rows[0][idx] = ""
    assert check_foreign_keys(tables) == []


def test_entirely_blank_foreign_key_column_is_reported(tables):
    """**整列**为空则是链接断了，必须报错。

    跟上面那条不矛盾：前者是"这条记录恰好没有"，后者是"一条都没有"。
    早先的实现只检查非空值，于是 ``urn:uuid:`` 引用切不出来时
    四张表的 就诊ID 整列为空、检查却一个字都不报。
    """
    enc = table_named(tables, "就诊")
    idx = enc.headers.index("医生ID")
    for row in enc.rows:
        row[idx] = ""
    problems = check_foreign_keys(tables)
    assert problems
    assert "医生ID" in problems[0]


# ------------------------------------------------------------------ 写盘


def test_csv_is_written_with_bom_for_excel(tables, tmp_path):
    paths = write_all(tables, tmp_path / "out")
    assert len(paths) == 8
    raw = paths[0].read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV 必须是 UTF-8 带 BOM"


def test_csv_roundtrips_chinese(tables, tmp_path):
    out = tmp_path / "out"
    write_all(tables, out)
    with (out / "患者.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["姓名"] == "王冠杰"
    assert rows[0]["区县"] == "静安区"


# ------------------------------------------------------------------ 加载器


def test_loader_reads_both_bundles_and_bare_resources(tables):
    # hospitalInformation / practitionerInformation 是裸资源文件
    assert table_named(tables, "机构").rows
    assert table_named(tables, "医生").rows


def test_missing_directory_is_an_error(tmp_path):
    with pytest.raises(ExportError):
        load_dir(tmp_path / "nope")


def test_directory_without_fhir_is_an_error(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ExportError):
        load_dir(tmp_path / "empty")


def test_ndjson_is_supported(tmp_path):
    root = tmp_path / "nd"
    root.mkdir()
    (root / "Patient.ndjson").write_text(
        '{"resourceType":"Patient","id":"x1","name":[{"text":"张三"}]}\n'
        '{"resourceType":"Patient","id":"x2","name":[{"text":"李四"}]}\n',
        encoding="utf-8",
    )
    data = load_dir(root)
    assert len(data.get("Patient")) == 2


def test_deref_resolves_references(fhir_dir):
    data = load_dir(fhir_dir)
    org = data.deref(f"Organization/{ORG_ID}")
    assert org is not None
    assert org["name"] == "上海市第六人民医院"
    assert data.deref("Organization/missing") is None
    assert data.deref(None) is None


def test_lookups_are_optional(tmp_path):
    """没有 shanghai/ 目录也要能跑完，只是等级/科室映射为空。"""
    assert load_lookups(None).organization == {}
    assert load_lookups(tmp_path / "nope").department == {}


def test_department_lookup_falls_back_to_the_source_name():
    lk = load_lookups(None)
    assert lk.dept_zh("CARDIOLOGY") == "CARDIOLOGY"