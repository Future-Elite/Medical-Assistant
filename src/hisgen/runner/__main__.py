"""命令行：生成数据。

    python -m hisgen.runner -p 100 -s 12345
    python -m hisgen.runner -p 20 -s 1 -o output/trial
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .._console import force_utf8_console
from .run import RunSpec, SyntheaNotFound, run_synthea


def main(argv: list[str] | None = None) -> int:
    # Synthea 的日志里混着中文和 GBK 字节，解码后会出现替换字符；
    # 控制台不是 UTF-8 的话回写时会 UnicodeEncodeError 直接把命令打断。
    force_utf8_console()
    parser = argparse.ArgumentParser(
        prog="python -m hisgen.runner",
        description="调用 run_synthea 生成上海地区患者数据",
    )
    parser.add_argument("-p", "--population", type=int, default=100)
    parser.add_argument("-s", "--seed", type=int, default=12345)
    parser.add_argument("-a", "--age-range", default=None, help='年龄范围，如 "20-40"')
    parser.add_argument("-g", "--gender", default=None, choices=["M", "F", "m", "f"])
    parser.add_argument("--state", default="上海市", help="Synthea 的州参数，默认 上海市")
    parser.add_argument("--city", default=None, help="可选：只生成某个区")
    parser.add_argument("-c", "--properties", default=None, help="properties 文件")
    parser.add_argument("-d", "--modules-dir", default=None, help="模块目录，默认 <项目根>/modules")
    parser.add_argument("-m", "--module-filter", default="sh_*", help="模块白名单，默认 sh_*")
    parser.add_argument("-o", "--output-dir", default=None, help="输出目录")
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="额外的 --config 覆盖，可重复",
    )
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args(argv)

    from .. import PROJECT_ROOT

    overrides: dict[str, str] = {}
    for item in args.config:
        if "=" not in item:
            print(f"[FAIL] --config 需要 KEY=VALUE 形式，收到 {item!r}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        overrides[key.strip()] = value.strip()

    spec = RunSpec(
        population=args.population,
        seed=args.seed,
        state=args.state,
        city=args.city,
        age_range=args.age_range,
        gender=args.gender,
        properties=(
            Path(args.properties)
            if args.properties
            else PROJECT_ROOT / "shanghai" / "shanghai.properties"
        ),
        modules_dir=(
            Path(args.modules_dir) if args.modules_dir else PROJECT_ROOT / "modules"
        ),
        module_filter=args.module_filter,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        config_overrides=overrides,
        timeout_seconds=args.timeout,
    )

    try:
        result = run_synthea(spec)
    except SyntheaNotFound as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    if result.returncode != 0:
        # Synthea 通过 gradlew 启动，Windows 上返回码偶有噪声：
        # 只要真的产出了患者文件就认为成功，但把返回码如实打出来。
        n = len(result.patient_files)
        if n > 0:
            print(
                f"[WARN] Synthea 返回码 {result.returncode}，但已产出 {n} 个患者文件，"
                "按成功处理。日志尾部："
            )
            print(result.tail(20))
            return 0
        print(f"[FAIL] Synthea 运行失败，返回码 {result.returncode}", file=sys.stderr)
        print(result.tail(60), file=sys.stderr)
        return 1

    # 导出是在 patient 线程里做的，异常被 FutureTask 吞掉，JVM 照样退出 0。
    # 于是"生了患者但一个都没导出"表现为返回码 0 + 没有文件 ——
    # 不看这一条就会把整批导出失败当成成功。
    #
    # 判定要看 **Synthea 的 alive 计数**，不是文件数：``-p N`` 的含义是
    # 「存活 N 人」，中途死亡的患者照样导出（Generator 会补生成一个），
    # 所以文件数 ≥ N 是正常的，只有 alive < N 才是真失败。
    if result.population_wanted > 0:
        records = result.records
        n = len(result.patient_files)
        if records is None:
            print(
                f"[WARN] 请求 {result.population_wanted} 人，但日志里没有 Synthea 的"
                f"Records 汇总；落到 {n} 个患者文件上判断。",
                file=sys.stderr,
            )
            if n == 0:
                print("[FAIL] 没有导出任何患者文件。日志：", file=sys.stderr)
                print(result.tail(60), file=sys.stderr)
                return 1
        elif records[1] < result.population_wanted:
            print(
                f"[FAIL] 请求存活 {result.population_wanted} 人，"
                f"实际只有 {records[1]} 人存活"
                f"（total={records[0]}, dead={records[2]}）。日志：",
                file=sys.stderr,
            )
            print(result.tail(60), file=sys.stderr)
            return 1

    records = result.records
    if records:
        print(
            f"[OK] 生成完成：存活 {records[1]} 人、死亡 {records[2]} 人"
            f"（共 {records[0]} 份记录，死者的记录也会导出，所以文件数会多于此）"
            f" -> {result.fhir_dir}"
        )
    else:
        print(f"[OK] 生成完成，{len(result.patient_files)} 个患者 -> {result.fhir_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())