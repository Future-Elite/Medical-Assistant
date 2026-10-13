"""控制台输出的编码处理。

Windows 的默认控制台编码是 cp936，而本项目的所有日志都是中文 ——
不显式改掉的话，``print`` 出去的中文会变成乱码，报错信息也就没法看了。
"""

from __future__ import annotations

import sys


def force_utf8_console() -> None:
    """把 stdout / stderr 切到 UTF-8（能切才切，切不了就算了）。

    被重定向到管道时 ``sys.stdout`` 可能不是 ``TextIOWrapper``，
    或者干脆是 ``None``（pythonw），这两种情况都直接跳过。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass