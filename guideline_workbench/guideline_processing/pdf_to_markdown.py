"""MinerU-backed PDF to Markdown conversion."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def _mineru_executable() -> str | None:
    executable = shutil.which("mineru-kit")
    if executable:
        return executable
    scripts = Path(sys.executable).parent / "Scripts"
    for name in ("mineru-kit.exe", "mineru-kit"):
        candidate = scripts / name
        if candidate.is_file():
            return str(candidate)
    return None


def convert_pdf(pdf_path: Path, markdown_path: Path, tier: str = "advanced") -> Path:
    """Convert one PDF and return its Markdown sidecar path."""
    pdf_path = pdf_path.resolve()
    markdown_path = markdown_path.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF 不存在：{pdf_path}")
    if tier not in {"standard", "advanced"}:
        raise ValueError("MinerU 质量档必须为 standard 或 advanced")
    executable = _mineru_executable()
    if executable is None:
        raise RuntimeError(
            "当前 Python 环境没有安装 MinerU。请在运行 GUI 的 Conda 环境安装：\n"
            "python -m pip install \"mineru>=4.0,<5\"\n"
            "然后确认命令 mineru-kit 可用。"
        )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    command = [executable, "parse", str(pdf_path), "-o", str(markdown_path.parent), "--tier", tier]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"MinerU 解析失败（{result.returncode}）：{detail[-3000:]}")
    generated = markdown_path if markdown_path.is_file() else None
    if generated is None:
        candidates = list(markdown_path.parent.rglob(f"{pdf_path.stem}.md"))
        if candidates:
            generated = max(candidates, key=lambda path: path.stat().st_mtime)
    if generated is None:
        raise RuntimeError(
            f"MinerU 未生成 {pdf_path.stem}.md。实际输出目录：{markdown_path.parent}\n"
            f"{result.stdout[-1500:]}"
        )
    return generated
