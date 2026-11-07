"""Structure-guided Markdown chunking for clinical guideline documents."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class GuidelineChunk:
    chunk_id: str
    text: str
    source_id: str
    source_title: str
    source_url: str
    specialty: str
    version: str
    section_path: list[str]
    recommendation_grade: str
    evidence_level: str
    keywords: list[str]
    source_file: str
    page_start: int
    page_end: int
    element_type: str
    image_refs: list[str]
    chunk_strategy: str
    table_strategy: str
    figure_strategy: str


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind("。", start, end), text.rfind("；", start, end), text.rfind("，", start, end))
            if boundary > start + size // 2:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _split_fixed(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = end - overlap
    return [chunk for chunk in chunks if chunk]


def _split_recursive(text: str, size: int, overlap: int) -> list[str]:
    """Split oversized prose by progressively finer natural boundaries."""
    if len(text) <= size:
        return [text]
    separators = ["\n\n", "\n", "。", "；", "，", " "]
    for separator in separators:
        parts = [part.strip() for part in text.split(separator) if part.strip()]
        if len(parts) < 2:
            continue
        output = []
        current = ""
        for part in parts:
            candidate = f"{current}{separator if current else ''}{part}"
            if len(candidate) <= size:
                current = candidate
                continue
            if current:
                output.extend(_split_recursive(current, size, overlap) if len(current) > size else [current])
            if len(part) > size:
                output.extend(_split_recursive(part, size, overlap))
                current = ""
            else:
                current = part
        if current:
            output.append(current)
        if all(len(item) <= size for item in output):
            if overlap and len(output) > 1:
                overlapped = []
                previous = ""
                for item in output:
                    remaining = item
                    while remaining:
                        prefix = previous[-overlap:] if previous else ""
                        room = max(size - len(prefix), 1)
                        chunk = prefix + remaining[:room]
                        overlapped.append(chunk)
                        previous = chunk
                        remaining = remaining[room:]
                output = overlapped
            return output
    return _split_fixed(text, size, overlap)


def _table_rows(table: list[str], section_path: list[str], mode: str) -> list[str]:
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in table]
    if len(rows) < 2:
        return ["\n".join(table)]
    header = rows[0]
    output = []
    context = " > ".join(section_path)
    for cells in rows[2:]:
        if not any(cells):
            continue
        pairs = [f"{header[index] if index < len(header) else f'列{index + 1}'}：{value}"
                 for index, value in enumerate(cells) if value]
        if not pairs:
            continue
        row_text = "；".join(pairs)
        if mode == "row_context" and context:
            row_text = f"章节：{context}。{row_text}"
        output.append(row_text)
    return output or ["\n".join(table)]


def _element_units(
    block: str,
    size: int,
    overlap: int,
    *,
    chunk_strategy: str = "structure",
    table_strategy: str = "markdown",
    figure_strategy: str = "reference",
    section_path: list[str] | None = None,
    figure_context: str = "",
) -> list[tuple[str, str, list[str]]]:
    """Keep paragraphs, Markdown tables and figures as separate unit types."""
    units: list[tuple[str, str, list[str]]] = []
    prose: list[str] = []

    def flush_prose() -> None:
        normalized = " ".join(line.strip() for line in prose if line.strip())
        splitter = {
            "structure": _split_text,
            "fixed": _split_fixed,
            "recursive": _split_recursive,
        }[chunk_strategy]
        units.extend(("text", text, []) for text in splitter(normalized, size, overlap) if text)
        prose.clear()

    lines = block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|"):
            flush_prose()
            table: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table.append(lines[i].strip())
                i += 1
            if table_strategy in {"row", "row_context"}:
                rows = _table_rows(table, section_path or [], table_strategy)
                units.extend(("table", row, []) for row in rows)
                continue
            if len(table) <= 2:
                units.append(("table", "\n".join(table), []))
                continue
            header = table[:2]
            current = header[:]
            for row in table[2:]:
                if len("\n".join(current + [row])) > size and len(current) > len(header):
                    units.append(("table", "\n".join(current), []))
                    current = header[:]
                current.append(row)
            units.append(("table", "\n".join(current), []))
            continue
        refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", line)
        if refs:
            nearby = " ".join(part for part in (figure_context, " ".join(
                item.strip() for item in prose if item.strip()
            )) if part)
            flush_prose()
            figure_text = re.sub(
                r"data:image/([^;]+);base64,[^)]+",
                r"embedded-image/\1",
                line,
            )
            refs = [
                re.sub(r"^data:image/([^;]+);base64,.*$", r"embedded-image/\1", ref)
                for ref in refs
            ]
            if figure_strategy == "caption_context" and nearby:
                figure_text = f"图示说明上下文：{nearby}。{figure_text}"
            units.append(("figure", figure_text, refs))
        elif line:
            prose.append(line)
        else:
            flush_prose()
        i += 1
    flush_prose()
    return units


def split_markdown(
    text: str,
    *,
    source_file: Path,
    source_id: str,
    source_title: str,
    specialty: str,
    version: str = "",
    source_url: str = "",
    chunk_size: int = 800,
    overlap: int = 120,
    chunk_strategy: str = "structure",
    table_strategy: str = "markdown",
    figure_strategy: str = "reference",
) -> list[GuidelineChunk]:
    if chunk_size <= overlap or overlap < 0:
        raise ValueError("chunk_size 必须大于 overlap，且 overlap 不能小于 0")
    if chunk_strategy not in {"structure", "fixed", "recursive"}:
        raise ValueError(f"未知 chunk_strategy: {chunk_strategy}")
    if table_strategy not in {"markdown", "row", "row_context"}:
        raise ValueError(f"未知 table_strategy: {table_strategy}")
    if figure_strategy not in {"reference", "caption_context"}:
        raise ValueError(f"未知 figure_strategy: {figure_strategy}")

    section_path: list[str] = []
    paragraph: list[str] = []
    block_page = 0
    page = 0
    recent_figure_context = ""
    pending_prose: list[str] = []
    chunks: list[GuidelineChunk] = []

    def emit_block(block: str) -> None:
        nonlocal recent_figure_context
        if not block:
            return
        recommendation = re.search(r"(?:推荐意见|推荐强度|推荐等级)[:：]?\s*([^。；\n]+)", block)
        evidence = re.search(r"(?:证据等级|证据级别)[:：]?\s*([^。；\n]+)", block)
        for number, (element_type, piece, image_refs) in enumerate(_element_units(
            block,
            chunk_size,
            overlap,
            chunk_strategy=chunk_strategy,
            table_strategy=table_strategy,
            figure_strategy=figure_strategy,
            section_path=section_path,
            figure_context=recent_figure_context,
        )):
            identity = f"{source_id}|{'/'.join(section_path)}|{element_type}|{number}|{piece}"
            chunks.append(GuidelineChunk(
                chunk_id=hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16],
                text=piece,
                source_id=source_id,
                source_title=source_title,
                source_url=source_url,
                specialty=specialty,
                version=version,
                section_path=section_path.copy(),
                recommendation_grade=recommendation.group(1).strip() if recommendation else "",
                evidence_level=evidence.group(1).strip() if evidence else "",
                keywords=[],
                source_file=str(source_file),
                page_start=block_page,
                page_end=page,
                element_type=element_type,
                image_refs=image_refs,
                chunk_strategy=chunk_strategy,
                table_strategy=table_strategy,
                figure_strategy=figure_strategy,
            ))
        context_lines = [
            line.strip() for line in block.splitlines()
            if line.strip() and not line.strip().startswith("|")
            and not re.search(r"!\[[^\]]*\]\([^)]+\)", line)
        ]
        if context_lines:
            recent_figure_context = " ".join(context_lines)

    def flush(*, force: bool = False) -> None:
        nonlocal paragraph, pending_prose
        if not paragraph:
            if force and pending_prose:
                splitter = _split_fixed if chunk_strategy == "fixed" else _split_recursive
                for piece in splitter(" ".join(pending_prose), chunk_size, overlap):
                    emit_block(piece)
                pending_prose = []
            return
        block = "\n".join(paragraph).strip()
        paragraph = []
        has_table_or_figure = any(
            line.strip().startswith("|") or re.search(r"!\[[^\]]*\]\([^)]+\)", line)
            for line in block.splitlines()
        )
        if chunk_strategy in {"fixed", "recursive"}:
            if not force and not has_table_or_figure:
                pending_prose.append(" ".join(line.strip() for line in block.splitlines() if line.strip()))
                return
            if pending_prose:
                if not has_table_or_figure:
                    block = " ".join(pending_prose + [block])
                    splitter = _split_fixed if chunk_strategy == "fixed" else _split_recursive
                    for piece in splitter(block, chunk_size, overlap):
                        emit_block(piece)
                    pending_prose = []
                    return
                splitter = _split_fixed if chunk_strategy == "fixed" else _split_recursive
                for piece in splitter(" ".join(pending_prose), chunk_size, overlap):
                    emit_block(piece)
                pending_prose = []
            if force and not has_table_or_figure:
                splitter = _split_fixed if chunk_strategy == "fixed" else _split_recursive
                for piece in splitter(block, chunk_size, overlap):
                    emit_block(piece)
                return
        emit_block(block)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("<!--") and re.search(r"\bpage\s*[:= ]\s*\d+", line, re.I):
            match = re.search(r"\bpage\s*[:= ]\s*(\d+)", line, re.I)
            if match:
                flush(force=True)
                page = int(match.group(1))
                block_page = page
            continue
        if line.startswith("<!--") and re.search(r"\b(page|页码)\s*[:= ]?\s*\d+", line, re.I):
            match = re.search(r"\b(page|页码)\s*[:= ]?\s*(\d+)", line, re.I)
            if match:
                flush(force=True)
                page = int(match.group(2))
                block_page = page
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush(force=True)
            recent_figure_context = ""
            level = len(heading.group(1))
            section_path = section_path[:level - 1] + [heading.group(2).strip()]
            continue
        if not line:
            flush()
        else:
            if not paragraph:
                block_page = page
            paragraph.append(raw_line)
    flush(force=True)
    return chunks


def write_jsonl(chunks: list[GuidelineChunk], output: Path) -> None:
    import json

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")
