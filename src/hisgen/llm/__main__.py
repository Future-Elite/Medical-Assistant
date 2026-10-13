"""命令行：疾病资料 -> IR，以及生成待核实数字清单。

    python -m hisgen.llm --brief briefs/t2dm.md
    python -m hisgen.llm --from-file ir/sh_t2dm.yaml    # 不调模型，只重生成清单
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .._console import force_utf8_console
from ..ir import load_ir
from ..terminology import Terminology
from .generate import LlmError, generate_disease, write_ir, write_review
from .review import collect, render


def main(argv: list[str] | None = None) -> int:
    force_utf8_console()
    from .. import PROJECT_ROOT

    parser = argparse.ArgumentParser(
        prog="python -m hisgen.llm",
        description="把疾病资料整理成临床路径 IR（需要大模型）",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--brief", help="疾病资料的 Markdown 文件")
    source.add_argument(
        "--from-file",
        dest="from_file",
        help="已有的 IR 文件：不调模型，只重新生成核对清单",
    )
    parser.add_argument("-o", "--out", default=None, help="IR 输出路径，默认 ir/<id>.yaml")
    parser.add_argument(
        "--review",
        default=None,
        help="核对清单输出路径，默认 <项目根>/review/待核实数字清单.md",
    )
    parser.add_argument("--terminology", default=None, help="术语目录")
    parser.add_argument("--model", default=None, help="模型名，默认取 HISGEN_LLM_MODEL 或 deepseek-chat")
    args = parser.parse_args(argv)

    review_path = (
        Path(args.review)
        if args.review
        else PROJECT_ROOT / "review" / "待核实数字清单.md"
    )

    try:
        if args.from_file:
            disease = load_ir(args.from_file)
            print(f"[OK] 读入 IR {args.from_file}")
        else:
            brief_path = Path(args.brief)
            if not brief_path.is_file():
                print(f"[FAIL] 找不到疾病资料文件：{brief_path}", file=sys.stderr)
                return 1
            term = Terminology.load(args.terminology)
            print(f"正在调用模型整理 {brief_path.name} ……")
            disease = generate_disease(
                brief_path.read_text(encoding="utf-8"), term, model=args.model
            )
            out = write_ir(disease, args.out)
            print(f"[OK] IR 已写入 {out}")

        entries = collect(disease)
        unverified = sum(1 for e in entries if e.unverified)
        write_review(disease, review_path)
        print(
            f"[OK] 核对清单已写入 {review_path}"
            f"（{len(entries)} 条，其中 {unverified} 条待核实）"
        )
        if unverified:
            print(
                "  [注意] 待核实条目必须逐条查原文后才能投产，"
                "清单方法见文件末尾。"
            )
        return 0

    except LlmError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1


def render_only(path: str | Path) -> str:
    """给测试和脚本用的便捷入口：IR 文件 -> 核对清单 Markdown。"""
    return render(load_ir(path))


if __name__ == "__main__":
    raise SystemExit(main())