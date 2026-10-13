"""命令行：IR -> GMF。

    python -m hisgen.compiler --ir ir/sh_t2dm.yaml -o modules/sh_t2dm.json
    python -m hisgen.compiler --ir sh_t2dm --smoke          # 编译后用 Synthea 加载一次
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .._console import force_utf8_console
from ..ir import load_ir, resolve_ir
from ..terminology import Terminology
from .compile import compile_to_file
from .errors import CompileError
from .validate import condition_attributes, summarize, validate_terminology


def main(argv: list[str] | None = None) -> int:
    force_utf8_console()
    parser = argparse.ArgumentParser(
        prog="python -m hisgen.compiler",
        description="把临床路径 IR 编译成 Synthea GMF 模块",
    )
    parser.add_argument("--ir", required=True, help="IR 文件路径，或 ir/ 下的模块名")
    parser.add_argument("-o", "--out", default=None, help="输出路径，默认 modules/<id>.json")
    parser.add_argument(
        "--terminology", default=None, help="术语目录，默认 <项目根>/terminology"
    )
    parser.add_argument(
        "--report-dir",
        default=None,
        help="未核实编码体系报告的输出目录，默认 <项目根>/review",
    )
    parser.add_argument(
        "--smoke", action="store_true", help="编译后用 Synthea 真实加载一次（慢）"
    )
    args = parser.parse_args(argv)

    from .. import PROJECT_ROOT

    try:
        ir_path = resolve_ir(args.ir)
        disease = load_ir(ir_path)
        term = Terminology.load(args.terminology)
        validate_terminology(disease, term)

        out = args.out or str(PROJECT_ROOT / "modules" / f"{disease.disease.id}.json")
        written = compile_to_file(disease, term, out)

        gmf = __import__("json").loads(written.read_text(encoding="utf-8"))
        print(f"[OK] {ir_path} -> {written}")
        print(summarize(gmf))

        attrs = condition_attributes(disease)
        if attrs:
            print(f"  条件里用到的属性：{', '.join(attrs)}")

        # 未核实编码体系报告
        report_dir = Path(args.report_dir) if args.report_dir else PROJECT_ROOT / "review"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "待核实编码体系.md"
        report_path.write_text(term.unverified_report(), encoding="utf-8")
        n_unverified = len(term.used_unverified)
        n_caveats = len(set(term.used_caveats))
        if n_unverified or n_caveats:
            print(
                f"  [注意] 用到 {n_unverified} 个未核实的编码体系、"
                f"{n_caveats} 条兜底码，详见 {report_path}"
            )

        if args.smoke:
            from .smoke import smoke_load

            print("正在用 Synthea 加载冒烟……")
            rc, log = smoke_load(written)
            if rc != 0:
                print(log)
                print("[FAIL] Synthea 加载失败", file=sys.stderr)
                return 1
            print("[OK] Synthea 加载通过")

        return 0

    except CompileError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())