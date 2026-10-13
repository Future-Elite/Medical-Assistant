"""命令行：FHIR -> 8 张关系型 CSV。

    python -m hisgen.export -i output/fhir -o output/tables
    python -m hisgen.export                 # 用 runner 的默认输出目录
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .._console import force_utf8_console
from .csvio import describe, write_all
from .derive import Deriver
from .loader import ExportError, load_dir
from .lookups import load as load_lookups
from .tables import Ctx, build_all, check_foreign_keys


def _system_table(lookups) -> str:
    """编码体系 URI <-> 中文标签的对照表。

    诊断/手术操作的 ``编码体系`` 列里放的是标签（建库时对照的是「该查哪份
    标准」），URI 本身就在这里打出来 —— 标签好读，URI 才是不含糊的那个。
    ``【待核实】`` 的条目是**出处没核对过**的，投产前必须逐个查证。
    """
    if not lookups.systems:
        return "  编码体系：（未加载 terminology/，编码体系列原样输出 URI）"
    uri_to_label = {
        uri: lookups.system_label(uri) for uri in sorted(lookups.systems)
    }
    lines = ["  编码体系对照（URI -> 产物里的标签）："]
    for uri, label in uri_to_label.items():
        lines.append(f"    {uri}  ->  {label}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    force_utf8_console()
    from .. import PROJECT_ROOT
    from ..runner.run import RunSpec

    parser = argparse.ArgumentParser(
        prog="python -m hisgen.export",
        description="把 Synthea 的 FHIR 输出导成 8 张关系型 CSV",
    )
    parser.add_argument(
        "-i",
        "--in",
        dest="src",
        default=None,
        help="FHIR 输出目录，默认为 runner 的默认输出目录下的 fhir/",
    )
    parser.add_argument(
        "-o",
        "--out",
        default=None,
        help="CSV 输出目录，默认为 <项目根>/output/tables",
    )
    parser.add_argument(
        "--shanghai",
        default=None,
        help="shanghai/ 目录，用于读机构等级与科室映射，默认 <项目根>/shanghai",
    )
    parser.add_argument(
        "--terminology",
        default=None,
        help=(
            "terminology/ 目录，用于回填 FHIR 里没有的字段"
            "（药品规格/给药途径、检验参考区间、编码体系标签），"
            "默认 <项目根>/terminology"
        ),
    )
    parser.add_argument(
        "--keep-national-id",
        action="store_true",
        help="身份证号不脱敏（默认脱敏，只留前 6 后 4）",
    )
    parser.add_argument(
        "--allow-orphans",
        action="store_true",
        help="外键检查失败也照样写盘（默认失败即中止）",
    )
    args = parser.parse_args(argv)

    src = Path(args.src) if args.src else RunSpec().resolved_output_dir() / "fhir"
    out = Path(args.out) if args.out else PROJECT_ROOT / "output" / "tables"
    shanghai = Path(args.shanghai) if args.shanghai else PROJECT_ROOT / "shanghai"
    terminology = (
        Path(args.terminology) if args.terminology else PROJECT_ROOT / "terminology"
    )

    try:
        data = load_dir(src)
        lookups = load_lookups(shanghai)
        lookups.load_terminology(terminology)
        ctx = Ctx(
            data=data,
            derive=Deriver(),
            lookups=lookups,
            mask_national_id=not args.keep_national_id,
        )
        tables = build_all(ctx)

        problems = check_foreign_keys(tables)
        print(f"[OK] 读入 {data.files_read} 个文件，来自 {src}")
        print("  资源计数：" + "、".join(f"{k}×{v}" for k, v in data.counts().items()))
        for path in lookups.loaded_files:
            print(f"  已加载映射表：{path}")
        for warning in lookups.load_warnings:
            print(f"  [WARN] {warning}", file=sys.stderr)
        print(describe(tables))
        print(_system_table(lookups))

        if problems:
            for p in problems:
                print(f"  [外键] {p}", file=sys.stderr)
            if not args.allow_orphans:
                print("[FAIL] 外键不完整，未写盘。加 --allow-orphans 可强制写出。")
                return 1

        for path in write_all(tables, out):
            print(f"  -> {path}")
        print(f"[OK] 8 张表已写入 {out}")
        return 0

    except ExportError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())