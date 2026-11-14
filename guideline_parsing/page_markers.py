"""Add ``<!-- page: N -->`` markers to MinerU Markdown.

MinerU's Markdown output is clean but does not carry page boundaries. The RAG
chunker uses ``<!-- page: N -->`` to fill ``page_start``/``page_end`` and the
workbench uses the same value to jump to a PDF page. This module aligns
Markdown blocks with the PDF's per-page text using ``pdftotext -layout``.

The alignment is a deterministic heuristic, not a byte-exact layout mapping:
a block gets the first page (from the current page onward) whose normalized
text contains the block's leading text. Unmatched blocks keep the previous
page. Reference-list rows may therefore stay on the page where the list
starts; RAG drops reference-list chunks by default.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

_MARKER = re.compile(r"<!--\s*page\s*[:=]\s*\d+\s*-->", re.IGNORECASE)
_WS = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def _normalize(text: str) -> str:
    text = re.sub(r"[#*`|>\-–—]", "", text)
    return _NON_WORD.sub("", _WS.sub("", text)).lower()


def page_texts(pdf_path: Path) -> list[str]:
    """Return per-page text via ``pdftotext -layout`` (empty list if unavailable)."""
    if not shutil.which("pdftotext"):
        return []
    completed = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf_path), "-"],
        capture_output=True, text=True, timeout=90,
    )
    if completed.returncode:
        return []
    return completed.stdout.split("\f")


def add_page_markers(markdown: str, pdf_path: Path, *, pages: Sequence[str] | None = None) -> str:
    """Return Markdown with page markers inserted before page-changing blocks."""
    if _MARKER.search(markdown):
        return markdown
    page_list = list(pages) if pages is not None else page_texts(pdf_path)
    if not page_list:
        return markdown
    normalized_pages = [_normalize(page) for page in page_list]
    blocks = [block.strip() for block in re.split(r"\n\s*\n", markdown) if block.strip()]
    if not blocks:
        return markdown
    current = 1
    output: list[str] = []
    last_page = 0
    for block in blocks:
        key = _normalize(block)[:60]
        page = current
        if key:
            for index in range(max(current - 1, 0), len(normalized_pages)):
                if key[:30] and key[:30] in normalized_pages[index]:
                    page = index + 1
                    break
        current = page
        if page != last_page:
            output.append(f"<!-- page: {page} -->")
            last_page = page
        output.append(block)
    return "\n\n".join(output) + "\n"


__all__ = ["add_page_markers", "page_texts"]
