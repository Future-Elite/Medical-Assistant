"""Reusable term-mapping library (domain-agnostic).

v6.6.5 的 ``term_mapping`` 只能逐请求手写（演示预设在用，真实调用方每问都要
手编）；本模块把它变成**可复用资源**：调用方或数据集构建一份概念—英文/MeSH
映射库文件，配置一次后对所有请求生效——概念或别名命中问题、患者术语、审核
外部术语或 facets 时即展开，无需请求级手写。机制对任意领域通用；首个实例由
研发侧构建工具 ``build_term_mapping_library.py`` 从新生儿数据标签构建（工具不随本分支发布）。

边界：库行只是查询扩展候选，不是医学金标准。每行带 provenance
（``metadata.source`` / ``metadata.review_status``），展开记录写入
``query_plan.mapping_trace``（``origin=library``），逐行可审计；
``dataset_derived`` / ``needs_review`` 不等于人工审核术语。默认不配置库
（``config.term_mapping_library_path=""``）时查询计划与 v6.6.5 完全一致。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import TermMappingEntry, parse_term_mapping

MAPPING_ORIGIN_REQUEST = "request"
MAPPING_ORIGIN_LIBRARY = "library"


@dataclass(frozen=True)
class TermMappingLibrary:
    """Concept-bound mapping rows loaded once and reused across requests."""

    entries: tuple[TermMappingEntry, ...]
    source: str = ""

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("term mapping library must contain at least one row")
        seen: set[str] = set()
        for entry in self.entries:
            key = entry.concept.casefold()
            if key in seen:
                raise ValueError(f"duplicate concept in term mapping library: {entry.concept}")
            seen.add(key)

    @classmethod
    def from_rows(cls, rows: Sequence[Any], *, source: str = "inline") -> "TermMappingLibrary":
        entries: list[TermMappingEntry] = []
        for row in rows:
            if isinstance(row, TermMappingEntry):
                entries.append(row)
            else:
                parsed = parse_term_mapping([row])
                entries.extend(parsed)
        return cls(entries=tuple(entries), source=str(source or "inline"))

    @classmethod
    def from_json_file(cls, path: str | Path) -> "TermMappingLibrary":
        """Load a library file.

        Accepted shapes: ``{"library": name, "rows": [...]}`` (builder output)
        or a bare list of mapping rows (same shape as ``term_mapping``).
        A missing/invalid file raises — a configured-but-broken library is a
        configuration error, never silently ignored.
        """
        file_path = Path(str(path)).expanduser()
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError(f"term mapping library file not found: {file_path}") from None
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"term mapping library file unreadable: {file_path} ({type(exc).__name__})") from None
        if isinstance(payload, Mapping):
            rows = payload.get("rows") or payload.get("entries")
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
                raise ValueError("term mapping library object requires a rows array")
            source = str(payload.get("library") or file_path.name)
        elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
            rows = payload
            source = file_path.name
        else:
            raise ValueError("term mapping library must be a rows array or {library, rows} object")
        return cls.from_rows(list(rows), source=source)

    def describe(self) -> dict[str, Any]:
        mesh_status: dict[str, int] = {}
        for entry in self.entries:
            status = str((entry.metadata or {}).get("mesh_review_status") or "absent")
            mesh_status[status] = mesh_status.get(status, 0) + 1
        return {
            "source": self.source,
            "rows": len(self.entries),
            "concepts": [entry.concept for entry in self.entries],
            "mesh_status": mesh_status,
        }
