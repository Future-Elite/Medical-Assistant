"""All application writes are confined to this checkout, including staging files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = next((p for p in Path(__file__).resolve().parents
             if (p / "pyproject.toml").is_file() and (p / "EHRWorld.pdf").is_file()),
            Path(__file__).resolve().parent.parent)


def output_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    candidate = candidate.resolve()
    if not candidate.is_relative_to(ROOT):
        raise ValueError(f"Output must remain inside the project: {ROOT}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def read_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    with Path(path).open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc


def jsonable(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def write_json(path: str | Path, value: Any) -> Path:
    target = output_path(path)
    target.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return target


def write_jsonl(path: str | Path, values: Iterable[Any]) -> Path:
    target = output_path(path)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(json.dumps(jsonable(value), ensure_ascii=False, allow_nan=False) + "\n")
    return target
