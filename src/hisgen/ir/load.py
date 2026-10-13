"""从 YAML 加载并校验 IR。"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import Disease, IRError

#: 本项目根目录（HISdata/）
PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: IR 默认存放目录
IR_DIR = PROJECT_ROOT / "ir"


def load_ir_text(text: str, *, origin: str = "<memory>") -> Disease:
    """从 YAML 文本加载 IR，失败时抛出带定位信息的 :class:`IRError`。"""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise IRError(f"{origin}: YAML 解析失败\n{exc}") from exc

    if not isinstance(raw, dict):
        raise IRError(f"{origin}: IR 顶层必须是一个映射，实际是 {type(raw).__name__}")

    try:
        return Disease.model_validate(raw)
    except ValidationError as exc:
        raise IRError(f"{origin}: IR 校验失败\n{_format_validation_error(exc)}") from exc


def load_ir(path: str | Path) -> Disease:
    """从文件加载 IR。``path`` 为相对路径时按当前工作目录解析。"""
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    if not p.exists():
        raise IRError(f"IR 文件不存在：{p}")
    return load_ir_text(p.read_text(encoding="utf-8"), origin=str(p))


def resolve_ir(name: str) -> Path:
    """把 ``sh_t2dm`` / ``sh_t2dm.yaml`` / 绝对路径统一解析成存在的文件路径。"""
    p = Path(name)
    candidates = []
    if p.suffix == ".yaml":
        candidates.append(p)
    else:
        candidates.append(p.with_suffix(".yaml"))
        candidates.append(IR_DIR / f"{p.name}.yaml")
        candidates.append(p)

    for c in candidates:
        if not c.is_absolute():
            c = Path.cwd() / c
        if c.exists():
            return c

    raise IRError(
        f"找不到 IR：{name!r}。已尝试：\n  "
        + "\n  ".join(str(c) for c in candidates)
    )


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)