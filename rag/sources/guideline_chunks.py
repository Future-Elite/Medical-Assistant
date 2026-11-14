"""Structure-aware guideline chunks produced by the local parser.

The local ``guideline_parsing`` package parses guideline PDFs (MinerU), splits
them by section / table / figure, and writes one JSONL record per chunk. This
module turns that output into ``SourceDocument`` records and a read-only
connector.

Why not reuse ``custom_sources`` unchanged?  The exported library is already
accepted by ``CustomSourceLibrary`` (validated in the integration run), but
four behaviours make chunk-level retrieval worse:

* the exporter splits one guideline into 40-document sources, which produces
  one "version not found" warning per slice when a version filter is used;
* ``CustomSourceConnector`` truncates the candidate set to the pipeline's
  ``limit`` (``max(top_k * 5, 50)``) in file order, so a long guideline loses
  chunks before BM25 ever sees them;
* chunk titles carry ``[<chunk_id>]`` to survive title-based deduplication,
  which then shows up in citations;
* the reference list is chunked like body text and can outrank recommendations.

Here one connector serves **all chunks of one guideline** and returns the
whole set (bounded by ``max_documents``); the title stays the guideline title
while ``metadata["chunk_id"]`` gives each chunk its identity; reference-list
sections are dropped by default.  Cross-source DOI/PMID deduplication is not
applied to chunk records because their citable unit is the chunk, not the
article.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ..connectors import ConnectorRegistry, KnowledgeConnector
from ..models import QueryPlan, SearchResult, SourceDocument

SCHEMA = "guideline-chunks/v1"
DEFAULT_MAX_DOCUMENTS = 5000
_REFERENCE_HEADING = re.compile(
    r"^(references?|bibliography|literature cited|参\s*考\s*文\s*献)$", re.IGNORECASE
)
_PART_SUFFIX = re.compile(r"-\d{3}$")
_VERSION_PLACEHOLDERS = {"unknown-version", "unknown", "n/a", "none"}
#: Front matter / publication metadata that is not guideline evidence.
DEFAULT_EXCLUDED_SECTIONS = (
    "open access", "citation", "copyright", "license", "author contributions",
    "conflicts of interest", "conflict of interest", "funding", "acknowledgements",
    "acknowledgments", "keywords", "abbreviations",
)
_EXCLUDED_NORMALIZED = tuple(
    re.sub(r"\s+", "", item).casefold() for item in DEFAULT_EXCLUDED_SECTIONS
)
#: Non-chunk JSON that lives in the parser data directory.
_SKIP_JSON_NAMES = {"manifest.json", "guideline_pdfs.json"}


@dataclass(frozen=True)
class GuidelineChunk:
    """One normalized chunk; kept private to the loader."""
    chunk_id: str
    source_id: str
    title: str
    text: str
    version: str
    section_path: tuple[str, ...]
    locator: str
    page_start: int
    page_end: int
    url: str | None
    population: str
    element_type: str
    recommendation_grade: str
    evidence_level: str
    image_refs: tuple[str, ...]
    chunk_strategy: str
    table_strategy: str
    figure_strategy: str
    source_file: str


@dataclass
class GuidelineChunkLibrary:
    """Loaded chunk sources plus build-time warnings (returned with every search)."""
    sources: dict[str, tuple[SourceDocument, ...]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    dropped_references: int = 0
    dropped_non_evidence: int = 0
    max_documents: int = DEFAULT_MAX_DOCUMENTS

    def describe(self) -> list[dict[str, Any]]:
        rows = []
        for source_id in sorted(self.sources):
            documents = self.sources[source_id]
            rows.append({
                "source_id": source_id,
                "kinds": ["guideline"],
                "document_count": len(documents),
                "title": documents[0].title if documents else "",
                "version": documents[0].version_id if documents else None,
            })
        return rows

    def register_into(self, registry: ConnectorRegistry) -> list[str]:
        """Register one connector per guideline; returns the build warnings."""
        for source_id in sorted(self.sources):
            registry.register(GuidelineChunkConnector(
                source_id=source_id,
                documents=self.sources[source_id],
                max_documents=self.max_documents,
            ))
        return list(self.warnings)

    def build_registry(self) -> tuple[ConnectorRegistry, list[str]]:
        if not self.sources:
            raise ValueError(
                "no guideline chunks loaded; pass a JSONL file, a directory of JSONL files, "
                "or an exported {'sources': [...]} library"
            )
        registry = ConnectorRegistry(self.warnings)
        self.register_into(registry)
        return registry, list(self.warnings)


@dataclass
class GuidelineChunkConnector:
    """Serve every chunk of one guideline.

    The pipeline calls connectors with ``limit=max(top_k * 5, 50)``, a cap meant
    for remote APIs.  Applying it here would keep the first 50 chunks in file
    order and drop the rest before ranking, so this connector deliberately
    ignores ``limit`` and uses ``max_documents`` as an explicit safety bound.
    """
    source_id: str
    documents: tuple[SourceDocument, ...]
    kinds: tuple[str, ...] = ("guideline",)
    max_documents: int = DEFAULT_MAX_DOCUMENTS

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        allowed = set(plan.filters.get("source_kinds") or ())
        if allowed and "guideline" not in allowed:
            return SearchResult(self.source_id, ())
        selected = list(self.documents)
        warnings: list[str] = []
        requested_version = str(plan.filters.get("guideline_version") or "").strip()
        if requested_version:
            selected = [doc for doc in selected if doc.version_id == requested_version]
            if not selected:
                warnings.append(
                    f"指南分块 {self.source_id} 中未找到请求的指南版本：{requested_version}。"
                )
        if len(selected) > self.max_documents:
            warnings.append(
                f"指南分块 {self.source_id} 有 {len(selected)} 条，超过 max_documents="
                f"{self.max_documents}；已截断，未参与排序的条目不会出现在结果中。"
            )
            selected = selected[:self.max_documents]
        return SearchResult(self.source_id, tuple(selected), tuple(warnings))


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _section_path(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(">") if part.strip())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(_text(part) for part in value if _text(part))
    return ()


def _reference_section(section_path: Sequence[str]) -> bool:
    for part in section_path:
        normalized = re.sub(r"\s+", "", part)
        if _REFERENCE_HEADING.match(normalized):
            return True
        if "参考文献" in normalized:
            return True
    return False


def _excluded_section(section_path: Sequence[str], excluded: Sequence[str]) -> bool:
    if not excluded:
        return False
    markers = tuple(re.sub(r"\s+", "", item).casefold() for item in excluded)
    for part in section_path:
        normalized = re.sub(r"\s+", "", part).casefold()
        if any(normalized == marker or normalized.startswith(marker) for marker in markers):
            return True
    return False


def _locator(title: str, section_path: Sequence[str], page_start: int, page_end: int) -> str:
    parts = [part for part in section_path if part]
    # The parser section path starts with the document title (the top-level # heading);
    # repeating it in the locator adds noise without adding location information.
    if parts and parts[0] == title:
        parts = parts[1:]
    locator = " > ".join(parts)
    if page_start > 0:
        if page_end > page_start:
            suffix = f"（第 {page_start}–{page_end} 页）"
        else:
            suffix = f"（第 {page_start} 页）"
        locator = f"{locator}{suffix}" if locator else suffix.strip("（）")
    return locator


def _image_refs(value: Any) -> tuple[str, ...]:
    refs: list[str] = []
    for item in value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ():
        text = _text(item)
        if text:
            refs.append(re.sub(r"^data:image/([^;]+);base64,.*$", r"embedded-image/\1", text))
    return tuple(dict.fromkeys(refs))


def _source_file(value: Any) -> str:
    text = _text(value)
    # The parser may run on Windows; Path(...).name would not split backslashes on Linux.
    return re.split(r"[\\/]", text)[-1] if text else ""


def _chunk_from_record(record: Mapping[str, Any]) -> GuidelineChunk | None:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    chunk_id = _text(record.get("chunk_id") or record.get("document_id") or metadata.get("chunk_id"))
    text = _text(record.get("text"))
    if not chunk_id or not text:
        return None
    title = _text(
        record.get("source_title") or record.get("guide_title")
        or metadata.get("guide_title") or record.get("title")
    )
    if title.endswith(f"[{chunk_id}]"):
        title = title[: -len(f"[{chunk_id}]")].strip()
    source = record.get("source") if isinstance(record.get("source"), Mapping) else {}
    source_id = _text(
        record.get("source_id") or metadata.get("source_id") or source.get("source_id")
    )
    if not source_id:
        digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]
        source_id = f"guideline-{digest}"
    section_path = _section_path(record.get("section_path") or metadata.get("section_path"))
    page_start = _integer(record.get("page_start") or metadata.get("page_start"))
    page_end = _integer(record.get("page_end") or metadata.get("page_end"), page_start)
    if page_end < page_start:
        page_end = page_start
    version = _text(record.get("version") or record.get("version_id") or metadata.get("version"))
    provided_locator = _text(record.get("locator"))
    return GuidelineChunk(
        chunk_id=chunk_id,
        source_id=source_id,
        title=title or source_id,
        text=text,
        version=version,
        section_path=section_path,
        locator=(
            _locator(title or source_id, section_path, page_start, page_end)
            or provided_locator
        ),
        page_start=page_start,
        page_end=page_end,
        url=_text(record.get("url") or record.get("source_url") or metadata.get("source_url")) or None,
        population=_text(record.get("population") or record.get("specialty") or metadata.get("specialty")),
        element_type=_text(record.get("element_type") or metadata.get("element_type")) or "text",
        recommendation_grade=_text(record.get("recommendation_grade") or metadata.get("recommendation_grade")),
        evidence_level=_text(record.get("evidence_level") or metadata.get("evidence_level")),
        image_refs=_image_refs(record.get("image_refs") or metadata.get("image_refs")),
        chunk_strategy=_text(record.get("chunk_strategy") or metadata.get("chunk_strategy")) or "structure",
        table_strategy=_text(record.get("table_strategy") or metadata.get("table_strategy")) or "markdown",
        figure_strategy=_text(record.get("figure_strategy") or metadata.get("figure_strategy")) or "reference",
        source_file=_source_file(record.get("source_file") or metadata.get("source_file")),
    )


def _document(chunk: GuidelineChunk, applicability: Any = None) -> SourceDocument:
    metadata = {
        "schema": SCHEMA,
        "chunk_id": chunk.chunk_id,
        "guide_title": chunk.title,
        "source_id": chunk.source_id,
        "section_path": list(chunk.section_path),
        "element_type": chunk.element_type,
        "evidence_level": chunk.evidence_level,
        "recommendation_grade": chunk.recommendation_grade,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "image_refs": list(chunk.image_refs),
        "chunk_strategy": chunk.chunk_strategy,
        "table_strategy": chunk.table_strategy,
        "figure_strategy": chunk.figure_strategy,
        "content_mode": "caller_parsed_chunks",
    }
    if chunk.source_file:
        # The workbench writes an absolute local path; keep only the file name
        # so retrieval responses do not echo caller machine paths.
        metadata["source_file"] = chunk.source_file
    if applicability is not None:
        # Curated structured criteria live beside the chunk library (one file
        # per guideline) and are attached to every chunk of that guideline so
        # the existing applicability evaluator can check them against HIS facts.
        metadata["applicability_criteria"] = applicability
    return SourceDocument(
        source_id=chunk.source_id,
        document_id=chunk.chunk_id,
        kind="guideline",
        title=chunk.title,
        text=chunk.text,
        url=chunk.url,
        locator=chunk.locator or None,
        version_id=chunk.version or None,
        recommendation_id=f"rec-{chunk.chunk_id}" if chunk.recommendation_grade else None,
        population=chunk.population or None,
        metadata=metadata,
    )


def _exported_documents(payload: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """Flatten one ``{"sources": [...]}`` export into chunk-shaped records."""
    for source in payload.get("sources") or ():
        if not isinstance(source, Mapping):
            continue
        source_id = _PART_SUFFIX.sub("", _text(source.get("source_id")))
        for document in source.get("documents") or ():
            if not isinstance(document, Mapping):
                continue
            metadata = document.get("metadata") if isinstance(document.get("metadata"), Mapping) else {}
            yield {
                **document,
                "source_id": _text(metadata.get("source_id")) or source_id,
                "source_title": _text(metadata.get("guide_title")) or document.get("title"),
                "version": _text(source.get("version")) or document.get("version_id"),
            }


def _records(path: Path) -> Iterator[tuple[str, Mapping[str, Any]]]:
    if path.is_dir():
        files = sorted(
            candidate for candidate in path.rglob("*")
            if candidate.is_file() and candidate.suffix.lower() in {".jsonl", ".json"}
        )
        if not files:
            raise ValueError(f"目录中没有 .jsonl/.json 指南分块文件：{path}")
        for file in files:
            # The parser data dir also holds the input manifest and applicability
            # sidecars; neither is a chunk record.
            if file.suffix.lower() == ".json" and (
                file.name in _SKIP_JSON_NAMES or file.parent.name == "applicability"
            ):
                continue
            yield from _records(file)
        return
    if not path.is_file():
        raise FileNotFoundError(f"指南分块文件不存在：{path}")
    if path.suffix.lower() == ".jsonl":
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: JSON 解析失败：{exc.msg}") from exc
            if not isinstance(payload, Mapping):
                raise ValueError(f"{path}:{line_number}: 每行必须是一个 JSON 对象")
            yield f"{path}:{line_number}", payload
        return
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, Mapping) and isinstance(payload.get("sources"), list):
            for offset, item in enumerate(_exported_documents(payload), 1):
                yield f"{path}:sources[{offset}]", item
            return
        if isinstance(payload, Mapping) and isinstance(payload.get("chunks"), list):
            payload = payload["chunks"]
        if isinstance(payload, Mapping):
            yield str(path), payload
            return
        if isinstance(payload, list):
            for offset, item in enumerate(payload, 1):
                if not isinstance(item, Mapping):
                    raise ValueError(f"{path}: 第 {offset} 条不是 JSON 对象")
                yield f"{path}:{offset}", item
            return
        raise ValueError(f"{path}: 不支持的 JSON 结构")
    raise ValueError(f"不支持的指南分块文件类型：{path.suffix or '(无扩展名)'}")


def _load_applicability(
    directory: str | Path,
    source_ids: set[str],
    warnings: list[str],
) -> dict[str, Any]:
    """Load one structured-criteria sidecar per guideline.

    The file is ``<source_id>.json`` (or a filesystem-safe variant) and follows
    the same shape ``applicability.parse_applicability_criteria`` accepts:
    ``{"logic": "all_of"|"any_of", "criteria": [...]}`` or a bare criteria list.
    Missing files are explicit warnings, not silent ``not_assessed`` results.
    """
    base = Path(directory).expanduser()
    if not base.is_dir():
        raise ValueError(f"applicability_dir 不存在：{base}")
    loaded: dict[str, Any] = {}
    for source_id in sorted(source_ids):
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_id)
        candidates = [base / f"{source_id}.json", base / f"{safe_name}.json"]
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            warnings.append(
                f"指南分块 {source_id} 未提供 applicability 条件（{base}）；"
                "该指南不会参与结构化适用性核对。"
            )
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: applicability JSON 解析失败：{exc.msg}") from exc
        if not isinstance(payload, (Mapping, list)):
            raise ValueError(f"{path}: applicability 内容必须是对象或数组")
        loaded[source_id] = payload
    return loaded


def summarize_chunks(
    chunks: Sequence[Mapping[str, Any]],
    *,
    include_references: bool = False,
    exclude_sections: Sequence[str] = DEFAULT_EXCLUDED_SECTIONS,
) -> dict[str, int]:
    """Count raw vs retrieval-usable chunks with the same default filters."""
    references = non_evidence = 0
    for chunk in chunks:
        section = _section_path(chunk.get("section_path"))
        if not include_references and _reference_section(section):
            references += 1
            continue
        if _excluded_section(section, exclude_sections):
            non_evidence += 1
    total = len(chunks)
    return {
        "total": total,
        "retrievable": total - references - non_evidence,
        "dropped_references": references,
        "dropped_non_evidence": non_evidence,
    }


def load_guideline_chunk_library(
    path: str | Path,
    *,
    include_references: bool = False,
    exclude_sections: Sequence[str] = DEFAULT_EXCLUDED_SECTIONS,
    max_documents: int = DEFAULT_MAX_DOCUMENTS,
    applicability_dir: str | Path | None = None,
) -> GuidelineChunkLibrary:
    """Load parser-style chunks from JSONL, a directory, or an exported library.

    Args:
        path: ``*_chunks.jsonl``, a directory searched recursively for
            ``.jsonl``/``.json``, or ``medical_assistant_guidelines.json``.
        include_references: keep chunks whose section path is a reference list.
            Default ``False`` matches ``guideline_pdf.py``, which drops the
            reference list because citation rows score on author names.
        exclude_sections: front-matter/metadata sections to drop (copyright,
            citation, author contributions, conflicts, funding, keywords,
            abbreviations...). Defaults to ``DEFAULT_EXCLUDED_SECTIONS``; pass
            ``()`` to keep them for auditing.
        max_documents: per-guideline safety bound, not a ranking cut.
        applicability_dir: optional directory with ``<source_id>.json`` curated
            criteria; attached to every chunk of that guideline as
            ``metadata.applicability_criteria`` for the HIS comparison layer.
    """
    if max_documents <= 0:
        raise ValueError("max_documents must be positive")
    library = GuidelineChunkLibrary(max_documents=max_documents)
    grouped: dict[str, list[SourceDocument]] = {}
    chunks: list[GuidelineChunk] = []
    seen: dict[str, str] = {}
    missing_version: dict[str, int] = {}
    missing_page: dict[str, int] = {}
    missing_locator: dict[str, int] = {}

    for origin, record in _records(Path(path).expanduser()):
        chunk = _chunk_from_record(record)
        if chunk is None:
            library.warnings.append(f"指南分块 {origin} 缺少 chunk_id 或 text，已跳过。")
            continue
        if not include_references and _reference_section(chunk.section_path):
            library.dropped_references += 1
            continue
        if _excluded_section(chunk.section_path, exclude_sections):
            library.dropped_non_evidence += 1
            continue
        if chunk.chunk_id in seen:
            library.warnings.append(
                f"指南分块 {chunk.source_id}: 重复 chunk_id {chunk.chunk_id}（{origin}）已跳过；"
                f"首次出现于 {seen[chunk.chunk_id]}。"
            )
            continue
        seen[chunk.chunk_id] = origin
        if not chunk.version:
            missing_version[chunk.source_id] = missing_version.get(chunk.source_id, 0) + 1
        if chunk.page_start <= 0:
            missing_page[chunk.source_id] = missing_page.get(chunk.source_id, 0) + 1
        if not chunk.locator:
            missing_locator[chunk.source_id] = missing_locator.get(chunk.source_id, 0) + 1
        chunks.append(chunk)

    applicability: dict[str, Any] = {}
    if applicability_dir is not None:
        applicability = _load_applicability(
            applicability_dir, {chunk.source_id for chunk in chunks}, library.warnings
        )
    for chunk in chunks:
        grouped.setdefault(chunk.source_id, []).append(
            _document(chunk, applicability.get(chunk.source_id))
        )

    if library.dropped_references:
        library.warnings.append(
            f"指南分块：已跳过 {library.dropped_references} 条参考文献段落"
            "（include_references=False）；需要审计参考文献时显式开启。"
        )
    if library.dropped_non_evidence:
        library.warnings.append(
            f"指南分块：已跳过 {library.dropped_non_evidence} 条前置/元数据段落"
            "（exclude_sections 默认过滤版权、引用、作者贡献、利益冲突、致谢、关键词、缩略语）；"
            "需要审计时传 exclude_sections=()。"
        )
    for source_id in sorted(grouped):
        count = len(grouped[source_id])
        if missing_version.get(source_id):
            library.warnings.append(
                f"指南分块 {source_id} 有 {missing_version[source_id]}/{count} 条缺少 version；"
                "结果不标记指南版本。"
            )
        if missing_page.get(source_id):
            library.warnings.append(
                f"指南分块 {source_id} 有 {missing_page[source_id]}/{count} 条缺少页码；"
                "locator 只含章节，不能按页复核。"
            )
        if missing_locator.get(source_id):
            library.warnings.append(
                f"指南分块 {source_id} 有 {missing_locator[source_id]}/{count} 条缺少章节和页码；"
                "引用无法定位原文。"
            )
    library.sources = {source_id: tuple(documents) for source_id, documents in grouped.items()}
    if not library.sources:
        raise ValueError(f"没有可用的指南分块：{path}（全部记录为空、重复或被过滤）")
    return library


__all__ = [
    "DEFAULT_MAX_DOCUMENTS",
    "GUIDELINE_CHUNK_SCHEMA",
    "GuidelineChunkConnector",
    "GuidelineChunkLibrary",
    "load_guideline_chunk_library",
    "summarize_chunks",
]

GUIDELINE_CHUNK_SCHEMA = SCHEMA
