"""编译期错误。"""

from __future__ import annotations


class CompileError(ValueError):
    """IR 无法编译成合法的 GMF。

    错误信息必须**可定位** —— 带上 IR 状态 id / 动作序号 / 键名，
    否则使用者面对一份自动生成的 IR 无从下手。
    """