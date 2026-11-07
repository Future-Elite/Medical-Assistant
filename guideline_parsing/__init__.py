"""Guideline parsing: PDF/Markdown to structure-aware chunks.

This package is adapted from the original external workbench's
``guideline_processing`` package (commit ``5581f95``).
The parsing/chunking logic is kept, the package is renamed to a neutral name,
and the import surface is limited to the pieces RAG and the workbench need.

Typical use::

    from guideline_parsing import process_guidelines
    process_guidelines(Path("guide.pdf"), Path("guide_chunks.jsonl"))

PDF conversion requires MinerU (``mineru-kit``); Markdown/TXT/JSON inputs do
not. The package never writes back to a chart or calls an LLM.
"""
from __future__ import annotations

from .chunker import GuidelineChunk, split_markdown, write_jsonl
from .page_markers import add_page_markers
from .pdf_to_markdown import convert_pdf
from .pipeline import process_guidelines

__all__ = [
    "GuidelineChunk",
    "add_page_markers",
    "convert_pdf",
    "process_guidelines",
    "split_markdown",
    "write_jsonl",
]
