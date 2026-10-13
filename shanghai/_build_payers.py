# -*- coding: utf-8 -*-
"""生成 ``shanghai/payers/`` 下的三个表。

三个表都是按**表头名**取值（``SimpleCSV`` 走 Jackson 的 ``withHeader()``），
所以列数对不上就是把值塞进了隔壁列 —— 而 Synthea 侧的报错会离现场很远
（比如把 Notes 当成 Logical Operator，然后报"无效的逻辑运算符"）。
手写 CSV 在这个文件上翻过一次车，所以改成程序生成 + 逐行列数校验。

另外 Notes 里**不能出现 ASCII 双引号** —— 那是 CSV 的引号字符，会把字段切开。

    python shanghai/_build_payers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "payers"

COMPANY_HEADER = [
    "Id", "Name", "Address", "City", "State Headquarterd", "Zip", "Phone",
    "States Covered", "Ownership",
]

#: (Id, 名称, 地址, 城市, 州, 邮编, 电话, 覆盖州, 性质)
COMPANIES = [
    (1, "上海市城镇职工基本医疗保险基金", "上海市黄浦区人民大道200号", "上海市", "上海市",
     "200003", "021-23111111", "*", "GOVERNMENT"),
    (2, "上海市城乡居民基本医疗保险基金", "上海市黄浦区人民大道200号", "上海市", "上海市",
     "200003", "021-23111111", "*", "GOVERNMENT"),
    (3, "上海市商业补充医疗保险", "上海市浦东新区世纪大道100号", "上海市", "上海市",
     "200120", "021-68888888", "*", "PRIVATE"),
]

ELIGIBILITY_HEADER = [
    "Name", "Age Threshold", "Poverty Multiplier", "Income Threshold",
    "Qualifying Codes", "Qualifying Attributes", "Poverty Multiplier File",
    "Spenddown File", "Acceptance Likelihood", "Sub-Eligibilities",
    "Logical Operator", "Notes",
]

#: 用 dict 写，键必须都在 ELIGIBILITY_HEADER 里；缺的列自动补空。
ELIGIBILITIES = [
    {
        "Name": "EMPLOYEE_MEDICAL",
        "Age Threshold": "16",
        "Notes": "按 16 周岁及以上简化近似职工医保参保条件；真实口径是在职职工与退休人员。见 SOURCES.md",
    },
    {
        "Name": "SUPPLEMENTAL",
        "Acceptance Likelihood": "0.20",
        "Notes": "商业补充医疗保险，按 20% 接受率近似企业团体投保比例。见 SOURCES.md",
    },
]

PLAN_HEADER = [
    "Payer Id", "Plan Id", "Services Covered", "Deductible", "Default Coinsurance",
    "Default Copay", "Monthly Premium", "Medicare Supplement", "Eligibility Policy",
    "Priority Level", "Start Year", "End Year", "Max Out of Pocket", "Is ACA Plan",
    "Income Based Premium", "Name", "Notes",
]

#: 除 Notes 外全部是 Synthea 必填项：Deductible 会被 new BigDecimal() 直接吃掉，
#: 留空就是 NumberFormatException。
PLANS = [
    {
        "Payer Id": "1", "Plan Id": "1", "Services Covered": "*",
        "Deductible": "1500.00", "Default Coinsurance": "0.15", "Default Copay": "0.00",
        "Monthly Premium": "0.00", "Medicare Supplement": "false",
        "Eligibility Policy": "EMPLOYEE_MEDICAL", "Priority Level": "1",
        "Max Out of Pocket": "630000.00", "Is ACA Plan": "false",
        "Income Based Premium": "false",
        "Name": "上海市城镇职工基本医疗保险",
        "Notes": "门诊起付线 1500 元、统筹基金最高支付限额 63 万元。见 SOURCES.md",
    },
    {
        "Payer Id": "2", "Plan Id": "2", "Services Covered": "*",
        "Deductible": "300.00", "Default Coinsurance": "0.30", "Default Copay": "0.00",
        "Monthly Premium": "58.33", "Medicare Supplement": "false",
        "Eligibility Policy": "GENERIC", "Priority Level": "2",
        "Max Out of Pocket": "340000.00", "Is ACA Plan": "false",
        "Income Based Premium": "false",
        "Name": "上海市城乡居民基本医疗保险",
        "Notes": "60 周岁以上门诊起付线 300 元、年个人缴费约 700 元。见 SOURCES.md",
    },
    {
        "Payer Id": "3", "Plan Id": "3", "Services Covered": "*",
        "Deductible": "20000.00", "Default Coinsurance": "0.30", "Default Copay": "0.00",
        "Monthly Premium": "10.75", "Medicare Supplement": "false",
        "Eligibility Policy": "SUPPLEMENTAL", "Priority Level": "3",
        "Max Out of Pocket": "1000000.00", "Is ACA Plan": "false",
        "Income Based Premium": "false",
        "Name": "上海市商业补充医疗保险",
        "Notes": "免赔额 2 万元、赔付比例 30%。见 SOURCES.md",
    },
]


def cell(value: object) -> str:
    """写一个 CSV 单元格。

    出现逗号或引号就必须引号包裹并把引号加倍 —— 但这三张表里都不该出现，
    所以直接报错让人去改数据，而不是悄悄包一层让 Synthea 那边行为诡异。
    """
    text = str(value)
    if any(ch in text for ch in ',"\r\n'):
        raise ValueError(f"值里不能出现逗号/引号/换行：{text!r}")
    return text


def render(header: list[str], rows: list[dict[str, str]]) -> str:
    lines = [",".join(header)]
    for i, row in enumerate(rows, 1):
        unknown = set(row) - set(header)
        if unknown:
            raise ValueError(f"第 {i} 行有表头里没有的列：{sorted(unknown)}")
        lines.append(",".join(cell(row.get(col, "")) for col in header))
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "insurance_companies.csv": render(
            COMPANY_HEADER,
            [dict(zip(COMPANY_HEADER, row)) for row in COMPANIES],
        ),
        "insurance_eligibilities.csv": render(ELIGIBILITY_HEADER, ELIGIBILITIES),
        "insurance_plans.csv": render(PLAN_HEADER, PLANS),
    }

    for name, text in files.items():
        path = OUT / name
        path.write_text(text, encoding="utf-8")

        # 自检：每行列数必须与表头一致，且不能含 ASCII 双引号
        lines = text.rstrip("\n").split("\n")
        expected = lines[0].count(",") + 1
        for n, line in enumerate(lines, 1):
            got = line.count(",") + 1
            if got != expected:
                print(f"[FAIL] {name} 第 {n} 行有 {got} 列，表头是 {expected} 列",
                      file=sys.stderr)
                raise SystemExit(1)
            if '"' in line:
                print(f"[FAIL] {name} 第 {n} 行含 ASCII 双引号", file=sys.stderr)
                raise SystemExit(1)
        print(f"{name:32s} {len(lines) - 1:2d} 行  {expected} 列")

    print("自检通过：列数一致，无 ASCII 双引号。")


if __name__ == "__main__":
    main()