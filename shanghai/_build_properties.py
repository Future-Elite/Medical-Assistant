# -*- coding: utf-8 -*-
"""生成 ``shanghai/shanghai.properties``。

两个必须注意的点：

1. **这是覆盖层，不是替换。** ``Config`` 的静态初始化先读 classpath 上的
   ``synthea.properties``，随后 ``App.java:144`` 调 ``Config.load(File)`` 覆盖同名键。
   所以这里只写与默认值不同的项，不必抄一份完整的 synthea.properties。

2. **路径必须用正斜杠。** Java 的 ``Properties.load`` 把反斜杠当转义符，
   ``F:\\deeplearningPython`` 会被吃成 ``F:deeplearningPython``。
   ``Utilities.readResource(name, true, true)`` 的 allowFreePath 分支走
   ``new File(name)``，正斜杠在 Windows 上没问题。

3. **值里的中文一律写成 ``\\uXXXX`` 转义。** ``Config.load`` 用 ``FileReader``
   按平台默认字符集解码，在非 UTF-8 平台上原样写中文会读成乱码；转义不受影响。
   注释里的中文不转义 —— 注释读错了也不影响行为，保留原文更有可读性。

    python shanghai/_build_properties.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ROOT / "shanghai"


def p(relative: str) -> str:
    """把相对于项目根的路径写成 Synthea 能读的绝对正斜杠路径。"""
    if not relative:
        return ""
    return (ROOT / relative).as_posix()


#: 支付方中文名，与 shanghai/payers/insurance_companies.csv 的 Name 列逐字一致。
EMPLOYEE_FUND = "上海市城镇职工基本医疗保险基金"
RESIDENT_FUND = "上海市城乡居民基本医疗保险基金"


def esc(value: str) -> str:
    """非 ASCII 字符转成 ``\\uXXXX``。"""
    return "".join(c if ord(c) < 128 else f"\\u{ord(c):04x}" for c in value)


#: (注释分组标题, [(键, 值, 是否转义), ...])
GROUPS: list[tuple[str, list[tuple[str, str, bool]]]] = [
    ("地理：把 Synthea 的美国人口/邮编/时区/SDoH 表换成上海的表", [
        ("generate.demographics.default_file", p("shanghai/demographics.csv"), False),
        ("generate.geography.zipcodes.default_file", p("shanghai/zipcodes.csv"), False),
        ("generate.geography.timezones.default_file", p("shanghai/timezones.csv"), False),
        ("generate.geography.sdoh.default_file", p("shanghai/sdoh.csv"), False),
        ("generate.geography.foreign.birthplace.default_file",
         p("shanghai/foreign_birthplace.json"), False),
        # FHIR Address.country / 地址国别
        ("generate.geography.country_code", "CN", False),
    ]),
    ("姓名与语言：需要先跑 shanghai/patches/apply_patches.py", [
        # 只叠加，不替换 —— names.yml 里的 english/spanish 词池还要留着，
        # 叠加文件里只允许出现新的顶层键（chinese）。
        ("generate.names.overlay_file", p("shanghai/names_zh.yml"), False),
        ("generate.names.chinese", "true", False),
        # 母语标记，同时驱动 Names 的中文分支与出生地选择
        ("generate.demographics.language.override", "chinese", True),
        # 中文姓名后面挂 3 位数字很难看，而且会把"王建国"变成"王建国202"
        ("generate.append_numbers_to_person_names", "false", False),
        # 中文姓名没有中间名这一说
        ("generate.middle_names", "0.0", False),
        # 11 位大陆手机号，替代 555-xxx-xxxx
        ("generate.lifecycle.phone.format", "cn", False),
        # 中文地址的城市前缀。写成配置值而非 Java 字面量：Synthea 的
        # build.gradle 没设 compileJava.options.encoding，源码里写死中文
        # 有被按平台字符集读成乱码的风险。
        ("generate.address.city_prefix", "上海市", True),
    ]),
    ("提供者：上海医疗机构", [
        ("generate.providers.hospitals.default_file",
         p("shanghai/providers/hospitals.csv"), False),
        ("generate.providers.primarycare.default_file",
         p("shanghai/providers/primary_care_facilities.csv"), False),
        ("generate.providers.urgentcare.default_file",
         p("shanghai/providers/urgent_care_facilities.csv"), False),
        # veterans 这一项是 optional=false —— 文件读不到会中断整个 provider 加载
        # try 块，把后面的 primarycare / urgentcare 一起丢掉。必须给个文件。
        ("generate.providers.veterans.default_file",
         p("shanghai/providers/va_facilities.csv"), False),
        # IHS 是印第安人卫生服务，上海没有对应机构。置空即跳过（optional=true）。
        ("generate.providers.ihs.hospitals.default_file", "", False),
        ("generate.providers.ihs.primarycare.default_file", "", False),
        # 居家/临终/护理机构暂不生成，置空跳过（都是 optional=true）
        ("generate.providers.homehealth.default_file", "", False),
        ("generate.providers.hospice.default_file", "", False),
        ("generate.providers.nursing.default_file", "", False),
    ]),
    ("支付方：上海基本医保", [
        ("generate.payers.insurance_companies.default_file",
         p("shanghai/payers/insurance_companies.csv"), False),
        ("generate.payers.insurance_plans.default_file",
         p("shanghai/payers/insurance_plans.csv"), False),
        # 这个文件读不到会在 PlanEligibilityFinder 里 NPE（csv 保持 null 仍调 hasNext）
        ("generate.payers.insurance_plans.eligibilities_file",
         p("shanghai/payers/insurance_eligibilities.csv"), False),
        # 这三个键原本指向 Medicare/Medicaid，只影响 Claim.coveredByMedicare()；
        # 指到我们自己的基金上以免输出里出现"Medicare"字样。
        ("generate.payers.insurance_companies.medicare", EMPLOYEE_FUND, True),
        ("generate.payers.insurance_companies.medicaid", RESIDENT_FUND, True),
        ("generate.payers.insurance_companies.dual_eligible", EMPLOYEE_FUND, True),
        ("generate.payers.selection_behavior", "priority", False),
    ]),
    ("费用：中国医疗服务价格口径", [
        # 我们的模块用的是中国编码，命中不了 Synthea 自带的美国价格表
        # （costs/*.csv 是硬编码路径、allowFreePath=false，改不了），
        # 所以实际生效的就是下面这几个兜底值。
        ("generate.costs.default_encounter_cost", "25.00", False),
        ("generate.costs.default_procedure_cost", "120.00", False),
        ("generate.costs.default_medication_cost", "0.30", False),
        ("generate.costs.default_lab_cost", "30.00", False),
        ("generate.costs.default_immunization_cost", "0.00", False),
        ("generate.costs.default_device_cost", "0.00", False),
        ("generate.costs.default_supply_cost", "0.00", False),
    ]),
    ("导出", [
        ("exporter.fhir.export", "true", False),
        # US Core IG 是按美国医保场景定的 profile 集，套在上海数据上没有意义
        ("exporter.fhir.use_us_core_ig", "false", False),
        ("exporter.fhir.transaction_bundle", "true", False),
        ("exporter.encoding", "UTF-8", False),
        # Synthea 自带的 CSV 导出器是美国人表结构，本项目不用（见 src/hisgen/export）
        ("exporter.csv.export", "false", False),
    ]),
]

HEADER = """\
# 上海地区 Synthea 运行配置
# ===========================================================================
# 用法：
#     run_synthea.bat -c <本文件> -d modules -m "sh_*" -p 100 -s 12345 上海市
#
# 本文件由 shanghai/_build_properties.py 生成，请勿手工编辑。
#
# 注意这是**覆盖层**：Java 侧 Config 的静态初始化先读 classpath 上的
# synthea.properties，之后 App.java:144 再调 Config.load(File) 覆盖同名键。
# 因此这里只列与默认值不同的项。
#
# 值里的中文写成 \\uXXXX 转义 —— Config.load 用 FileReader 按平台默认字符集
# 解码，原样写中文在非 UTF-8 平台上会读成乱码。注释不转义。
# ===========================================================================
"""


def main() -> None:
    lines = [HEADER]
    for title, entries in GROUPS:
        lines.append(f"\n# ---- {title} " + "-" * max(0, 68 - len(title)))
        for key, value, escape in entries:
            if value == "" and key.endswith("default_file"):
                # 空值必须写成 "key =" 后面什么都没有，注释说明为什么是空的
                lines.append(f"{key} =")
                lines.append("# ↑ 置空 = 跳过这类机构（Synthea 对 optional 项的行为）")
                continue
            lines.append(f"{key} = {esc(value) if escape else value}")

    out = SHANGHAI / "shanghai.properties"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写入 {out}（{out.stat().st_size} 字节）")

    # 自检：标记为转义的值，转义之后必须是纯 ASCII
    bad = [
        f"{k} = {v}"
        for _, entries in GROUPS
        for k, v, escape in entries
        if escape and any(ord(c) > 127 for c in esc(v))
    ]
    if bad:
        print("[FAIL] 以下值未正确转义：" + "; ".join(bad), file=sys.stderr)
        raise SystemExit(1)
    print("自检通过：所有标记为转义的值都是纯 ASCII。")


if __name__ == "__main__":
    main()