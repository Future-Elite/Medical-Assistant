"""Guideline parsing source for RAG: local parser + parser data layout.

This module calls the in-repository :mod:`guideline_parsing` package directly
(no external checkout). The data layout follows the parsing workbench:

* ``<data_dir>/guidelines/``: manifest + PDF/Markdown inputs;
* ``<data_dir>/parsed_markdown/``: MinerU or caller-provided Markdown;
* ``<data_dir>/<document_id>_chunks.jsonl``: flat chunk output;
* ``<data_dir>/applicability/<document_id>.json``: optional structured
  criteria for HIS comparison.

It is the RAG-side source of truth for "guideline source is the local parser":
``create_tool("custom")`` uses it when ``RAG_GUIDELINE_DATA`` is configured.
Parsing options (chunk/table/figure strategy, size, overlap, MinerU tier) are
passed per call by the workbench, not frozen in the manifest.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..connectors import ConnectorRegistry
from .guideline_chunks import GuidelineChunkLibrary, load_guideline_chunk_library, summarize_chunks

MANIFEST_NAMES = ("manifest.json", "guideline_pdfs.json")
INPUT_SUFFIXES = {".pdf", ".md", ".markdown", ".txt", ".json"}
DEFAULT_CHUNK_STRATEGY = "recursive"
DEFAULT_TABLE_STRATEGY = "markdown"
DEFAULT_FIGURE_STRATEGY = "reference"
DEFAULT_CHUNK_SIZE = 800
DEFAULT_OVERLAP = 120
CHUNK_STRATEGIES = ("structure", "recursive", "fixed")
TABLE_STRATEGIES = ("markdown", "row", "row_context")
FIGURE_STRATEGIES = ("reference", "caption_context")
TIERS = ("standard", "advanced")


class GuidelineParsingError(RuntimeError):
    """Raised when the parser or its data layout cannot be used."""


@dataclass
class GuidelineParsingSource:
    """Direct adapter over :mod:`guideline_parsing` and its data layout."""
    data_dir: str | Path
    chunk_strategy: str = DEFAULT_CHUNK_STRATEGY
    table_strategy: str = DEFAULT_TABLE_STRATEGY
    figure_strategy: str = DEFAULT_FIGURE_STRATEGY
    chunk_size: int = DEFAULT_CHUNK_SIZE
    overlap: int = DEFAULT_OVERLAP
    tier: str = "advanced"
    mineru_bin: str = ""
    reuse_parsed_markdown: bool = True
    manifest_name: str = ""
    parser_root: str | Path | None = None
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir).expanduser()
        for name, value, allowed in (
            ("chunk_strategy", self.chunk_strategy, CHUNK_STRATEGIES),
            ("table_strategy", self.table_strategy, TABLE_STRATEGIES),
            ("figure_strategy", self.figure_strategy, FIGURE_STRATEGIES),
        ):
            value = str(value).strip()
            if value not in allowed:
                raise ValueError(f"{name} must be one of: " + ", ".join(allowed))
        if self.chunk_size <= self.overlap or self.overlap < 0:
            raise ValueError("chunk_size 必须大于 overlap，且 overlap 不能小于 0")
        if self.tier not in TIERS:
            raise ValueError("MinerU tier must be one of: " + ", ".join(TIERS))

    @property
    def guidelines_dir(self) -> Path:
        return self.data_dir / "guidelines"

    @property
    def markdown_dir(self) -> Path:
        return self.data_dir / "parsed_markdown"

    @property
    def applicability_dir(self) -> Path:
        return self.data_dir / "applicability"

    # ------------------------------------------------------------------ parse
    def parse(
        self,
        *,
        document_ids: Sequence[str] | None = None,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """Parse manifest inputs and write ``<data_dir>/<id>_chunks.jsonl``.

        ``document_ids`` narrows the run to selected guidelines (the workbench
        parses one guide at a time). ``force=True`` re-runs MinerU even when a
        pre-parsed Markdown exists.
        """
        entries = self._manifest_entries()
        if self.mineru_bin:
            os.environ["RAG_MINERU_BIN"] = str(Path(self.mineru_bin).expanduser())
        if document_ids is not None:
            wanted = {str(item).strip() for item in document_ids if str(item).strip()}
            unknown = wanted - {str(entry.get("document_id") or entry.get("id") or "").strip() for entry in entries}
            if unknown:
                raise GuidelineParsingError("未知的指南 document_id：" + ", ".join(sorted(unknown)))
            entries = [
                entry for entry in entries
                if str(entry.get("document_id") or entry.get("id") or "").strip() in wanted
            ]
        chunker, pdf_to_markdown = self._parser_modules()
        self.markdown_dir.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, Any]] = []
        for entry in entries:
            document_id = str(entry.get("document_id") or entry.get("id") or "").strip()
            file_name = str(entry.get("file") or entry.get("path") or "").strip()
            if not document_id or not file_name:
                raise GuidelineParsingError("manifest 条目必须提供 document_id 和 file")
            input_path = (self.guidelines_dir / file_name).resolve()
            if not input_path.is_file():
                raise GuidelineParsingError(f"指南输入文件不存在：{input_path}")
            text, markdown_path = self._markdown_for(input_path, force=force, converter=pdf_to_markdown)
            if not text.strip():
                raise GuidelineParsingError(f"指南 Markdown 内容为空：{markdown_path or input_path}")
            chunks = chunker.split_markdown(
                text,
                source_file=input_path,
                source_id=document_id,
                source_title=str(entry.get("title") or entry.get("source_title") or document_id),
                specialty=str(entry.get("specialty") or entry.get("population") or ""),
                version=str(entry.get("version_id") or entry.get("version") or ""),
                source_url=str(entry.get("source_url") or _doi_url(entry.get("doi")) or ""),
                chunk_size=self.chunk_size,
                overlap=self.overlap,
                chunk_strategy=self.chunk_strategy,
                table_strategy=self.table_strategy,
                figure_strategy=self.figure_strategy,
            )
            output_path = self.chunk_path(document_id)
            chunker.write_jsonl(chunks, output_path)
            results.append({
                "document_id": document_id,
                "input": str(input_path),
                "markdown": str(markdown_path) if markdown_path else "",
                "chunks": len(chunks),
                "output": str(output_path),
            })
        return results

    def _manifest_entries(self) -> list[Mapping[str, Any]]:
        names = [self.manifest_name] if self.manifest_name else list(MANIFEST_NAMES)
        for name in names:
            path = self.guidelines_dir / name
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                rows = payload.get("guidelines") if isinstance(payload, Mapping) else payload
                if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
                    raise GuidelineParsingError(f"{path}: manifest 需要非空的 guidelines 数组")
                entries = [row for row in rows if isinstance(row, Mapping)]
                if len(entries) != len(rows):
                    raise GuidelineParsingError(f"{path}: guidelines 数组只能包含对象")
                seen: set[str] = set()
                for entry in entries:
                    document_id = str(entry.get("document_id") or entry.get("id") or "").strip()
                    if not document_id:
                        raise GuidelineParsingError(f"{path}: 每个条目必须提供 document_id")
                    if document_id in seen:
                        raise GuidelineParsingError(f"{path}: document_id 重复：{document_id}")
                    seen.add(document_id)
                return entries
        files = sorted(
            path for path in self.guidelines_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in INPUT_SUFFIXES
        ) if self.guidelines_dir.is_dir() else []
        files = [path for path in files if path.name not in MANIFEST_NAMES]
        if not files:
            raise GuidelineParsingError(
                f"{self.guidelines_dir} 下没有 manifest 或指南文件；请按布局放入 "
                "guidelines/manifest.json 与 PDF/Markdown"
            )
        self.warnings.append(
            f"未找到指南 manifest（{self.guidelines_dir}）；已按文件名推导 document_id/title，"
            "version 为空，结果会显式告警。"
        )
        return [{
            "document_id": path.stem,
            "file": path.name,
            "title": path.stem,
            "source_url": "",
            "version_id": "",
        } for path in files]

    def _markdown_for(self, input_path: Path, *, force: bool, converter) -> tuple[str, Path | None]:
        suffix = input_path.suffix.lower()
        if suffix in {".md", ".markdown", ".txt"}:
            return input_path.read_text(encoding="utf-8"), input_path
        if suffix == ".json":
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            if isinstance(payload, Mapping):
                return str(payload.get("text") or payload.get("content") or ""), input_path
            if isinstance(payload, list):
                return "\n".join(str(item) for item in payload), input_path
            raise GuidelineParsingError(f"{input_path}: JSON 需要对象或数组")
        if suffix != ".pdf":
            raise GuidelineParsingError(f"不支持的指南输入类型：{input_path.suffix}")
        markdown_path = self.markdown_dir / f"{input_path.stem}.md"
        if self.reuse_parsed_markdown and markdown_path.is_file() and not force:
            text = markdown_path.read_text(encoding="utf-8")
            if "<!-- page:" not in text.lower():
                from guideline_parsing.page_markers import add_page_markers

                annotated = add_page_markers(text, input_path)
                if annotated != text:
                    markdown_path.write_text(annotated, encoding="utf-8")
                    text = annotated
            return text, markdown_path
        converted = converter.convert_pdf(input_path, markdown_path, self.tier)
        return Path(converted).read_text(encoding="utf-8"), Path(converted)

    def _parser_modules(self):
        """Import the in-repository parser (direct call, no external checkout)."""
        version_root = (
            Path(self.parser_root).expanduser()
            if self.parser_root is not None
            else Path(__file__).resolve().parents[2]
        )
        package = version_root / "guideline_parsing"
        if not (package / "chunker.py").is_file():
            raise GuidelineParsingError(
                f"未找到 guideline_parsing：{package}；请确认它与 rag/ 位于同一版本目录"
            )
        # 始终把本仓库版本目录放在最前，避免临时目录中的同名包抢先命中。
        sys.path[:] = [entry for entry in sys.path if entry != str(version_root)]
        sys.path.insert(0, str(version_root))
        # 测试或调用方可能已加载其他目录下的同名包；逐个清理不在本仓库的模块。
        for key in list(sys.modules):
            if key != "guideline_parsing" and not key.startswith("guideline_parsing."):
                continue
            module_file = getattr(sys.modules.get(key), "__file__", "") or ""
            if not Path(module_file).resolve().is_relative_to(package):
                del sys.modules[key]
        try:
            import guideline_parsing.chunker as chunker  # type: ignore[import-not-found]
            import guideline_parsing.pdf_to_markdown as pdf_to_markdown  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - packaging boundary
            raise GuidelineParsingError(
                "无法导入 guideline_parsing；请确认它位于 rag/ 的同级目录"
            ) from exc
        return chunker, pdf_to_markdown

    # ------------------------------------------------------------------- load
    def list_guidelines(self) -> list[dict[str, Any]]:
        """Manifest entries plus on-disk chunk/markdown status for the GUI."""
        rows: list[dict[str, Any]] = []
        for entry in self._manifest_entries():
            document_id = str(entry.get("document_id") or entry.get("id") or "").strip()
            chunk_path = self.chunk_path(document_id)
            markdown_name = str(entry.get("file") or "")
            markdown_path = self.markdown_dir / f"{Path(markdown_name).stem}.md"
            stats = summarize_chunks(self.chunks(document_id)) if chunk_path.is_file() else {
                "total": 0, "retrievable": 0, "dropped_references": 0, "dropped_non_evidence": 0,
            }
            rows.append({
                "document_id": document_id,
                "file": str(entry.get("file") or entry.get("path") or ""),
                "title": str(entry.get("title") or entry.get("source_title") or document_id),
                "version_id": str(entry.get("version_id") or entry.get("version") or ""),
                "specialty": str(entry.get("specialty") or entry.get("population") or ""),
                "source_url": str(entry.get("source_url") or _doi_url(entry.get("doi")) or ""),
                "chunk_path": str(chunk_path),
                "chunk_count": stats["total"],
                "retrievable_count": stats["retrievable"],
                "dropped_references": stats["dropped_references"],
                "dropped_non_evidence": stats["dropped_non_evidence"],
                "markdown": str(markdown_path) if markdown_path.is_file() else "",
            })
        return rows

    def chunk_path(self, document_id: str) -> Path:
        return self.data_dir / f"{_safe_name(document_id)}_chunks.jsonl"

    def chunks(self, document_id: str) -> list[dict[str, Any]]:
        path = self.chunk_path(document_id)
        if not path.is_file():
            raise GuidelineParsingError(f"指南尚未解析：{document_id}（缺少 {path}）")
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise GuidelineParsingError(f"{path}:{line_number}: JSON 解析失败：{exc.msg}") from exc
        return rows

    def load_library(
        self,
        *,
        parse: bool = False,
        include_references: bool = False,
    ) -> GuidelineChunkLibrary:
        """Parse (optional) and load chunks with applicability sidecars."""
        if parse:
            self.parse()
        applicability_dir = self.applicability_dir if self.applicability_dir.is_dir() else None
        if applicability_dir is None:
            self.warnings.append(
                f"{self.applicability_dir} 不存在；指南可以检索，但不会进行结构化适用性核对。"
            )
        try:
            library = load_guideline_chunk_library(
                self.data_dir,
                include_references=include_references,
                applicability_dir=applicability_dir,
            )
        except ValueError as exc:
            raise GuidelineParsingError(
                f"指南数据目录没有可用分块：{self.data_dir}；"
                "先运行解析（或设置 RAG_GUIDELINE_AUTO_PARSE=1）生成 *_chunks.jsonl"
            ) from exc
        library.warnings = [*self.warnings, *library.warnings]
        return library

    def build_registry(
        self, *, parse: bool = False, include_references: bool = False
    ) -> tuple[ConnectorRegistry, list[str]]:
        library = self.load_library(parse=parse, include_references=include_references)
        return library.build_registry()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "guideline"


def _doi_url(value: Any) -> str:
    text = str(value or "").strip()
    return f"https://doi.org/{text}" if text else ""


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_CHUNK_STRATEGY",
    "DEFAULT_FIGURE_STRATEGY",
    "DEFAULT_OVERLAP",
    "DEFAULT_TABLE_STRATEGY",
    "GuidelineParsingError",
    "GuidelineParsingSource",
]
