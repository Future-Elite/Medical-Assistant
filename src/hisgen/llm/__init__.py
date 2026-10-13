"""层 1：疾病资料 -> 临床路径 IR（唯一使用大模型的地方）。

CLI::

    python -m hisgen.llm --brief briefs/t2dm.md        # 调模型生成 IR
    python -m hisgen.llm --from-file ir/sh_t2dm.yaml   # 只重生成核对清单，不调模型
"""

from .generate import (
    DEFAULT_MODEL,
    LlmError,
    build_llm,
    dump_yaml,
    generate_disease,
    write_ir,
    write_review,
)
from .prompts import UNVERIFIED_MARKER, build_user_prompt, system_prompt
from .review import Entry, collect, render

__all__ = [
    "DEFAULT_MODEL",
    "Entry",
    "LlmError",
    "UNVERIFIED_MARKER",
    "build_llm",
    "build_user_prompt",
    "collect",
    "dump_yaml",
    "generate_disease",
    "render",
    "system_prompt",
    "write_ir",
    "write_review",
]