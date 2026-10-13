"""编译产物的冒烟验证：让 Synthea 真的加载一次。

为什么值得多花这几十秒：GMF 的错误绝大多数是**加载期**错误
（字段名写错、引用不存在、状态类型不认识）。放在这里就能把错误挡在
「生成 100 人数据跑到一半崩掉」之前。

做法是最小规模跑一次（1 个人），只看 Synthea 有没有报模块加载错误。
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from .. import PROJECT_ROOT
from ..runner.run import RunSpec, SyntheaNotFound, run_synthea

#: Synthea 加载模块失败时会打印的蛛丝马迹
_FAILURE_PATTERNS = (
    re.compile(r"Unable to load module", re.IGNORECASE),
    re.compile(r"Exception in thread", re.IGNORECASE),
    re.compile(r"com\.google\.gson\.JsonSyntaxException", re.IGNORECASE),
    re.compile(r"java\.lang\.RuntimeException", re.IGNORECASE),
    re.compile(r"java\.lang\.IllegalStateException", re.IGNORECASE),
    re.compile(r"Invalid module", re.IGNORECASE),
    re.compile(r"Module .* is malformed", re.IGNORECASE),
)


def smoke_load(
    module_path: str | Path,
    *,
    population: int = 1,
    seed: int = 1,
    timeout_seconds: int = 900,
) -> tuple[int, str]:
    """把单个模块放进临时目录，用 Synthea 加载一次。

    返回 ``(返回码, 日志)``；返回码 0 表示加载通过。
    """
    module_path = Path(module_path).resolve()
    if not module_path.exists():
        return 1, f"模块文件不存在：{module_path}"

    with tempfile.TemporaryDirectory(prefix="hisgen-smoke-") as tmp:
        tmp_dir = Path(tmp)
        modules = tmp_dir / "modules"
        modules.mkdir()
        (modules / module_path.name).write_text(
            module_path.read_text(encoding="utf-8"), encoding="utf-8"
        )

        spec = RunSpec(
            population=population,
            seed=seed,
            properties=_find_properties(),
            modules_dir=modules,
            module_filter=module_path.stem,
            output_dir=tmp_dir / "out",
            timeout_seconds=timeout_seconds,
        )
        try:
            result = run_synthea(spec, stream=False)
        except SyntheaNotFound as exc:
            return 1, str(exc)

    log = result.stdout + "\n" + result.stderr
    for pattern in _FAILURE_PATTERNS:
        match = pattern.search(log)
        if match:
            start = max(0, match.start() - 400)
            return 1, (
                f"日志中出现失败特征：{match.group(0)}\n"
                "……上下文……\n" + log[start : match.end() + 1200]
            )
    return 0, log


def _find_properties() -> Path:
    """冒烟用最小配置：只要地理数据能加载即可，机构/医保可用官方默认。"""
    candidate = PROJECT_ROOT / "shanghai" / "shanghai.properties"
    if candidate.exists():
        return candidate
    return PROJECT_ROOT / "shanghai" / "smoke.properties"