# Guideline Parsing

Structure-aware guideline parsing: PDF/Markdown/TXT/JSON → section/page-aware chunks.

This package is adapted from the original external workbench's
`guideline_processing` package (commit `5581f95`). Parsing
and chunking logic is preserved; the package name and imports are neutral so
RAG and the browser workbench can call it directly.

## Layout

```text
guideline_parsing/
  __init__.py          # public API: process_guidelines / split_markdown / convert_pdf / add_page_markers
  chunker.py           # section/table/figure-aware Markdown chunking
  pdf_to_markdown.py   # MinerU adapter (mineru-kit) + page markers
  page_markers.py      # pdftotext page alignment → <!-- page: N -->
  pipeline.py          # PDF/Markdown/TXT/JSON → JSONL chunks
  export.py            # optional legacy custom-source export
  requirements.txt     # mineru>=4.0,<5 (only for PDF input)
  tests/test_chunker.py
  tests/test_page_markers.py
```

MinerU installation (recommended: separate environment outside the repository):

```bash
uv venv <mineru-venv> --python 3.10
uv pip install --python <mineru-venv>/bin/python mineru==4.0.12
export RAG_MINERU_BIN=<mineru-venv>/bin/mineru-kit
```

Models are downloaded on first `parse` (the tool's default cache); subsequent
calls reuse them. `--tier advanced` gives the cleanest layout but is slower. Without
`RAG_MINERU_BIN`, the adapter also checks `$RAG_MINERU_VENV/bin/mineru-kit`.

## Use

```python
from pathlib import Path
from guideline_parsing import process_guidelines, split_markdown

count = process_guidelines(
    Path("guide.pdf"),
    Path("guide_chunks.jsonl"),
    specialty="新生儿",
    version="2025",
    chunk_size=800,
    overlap=120,
    chunk_strategy="recursive",   # structure / recursive / fixed
    table_strategy="markdown",    # markdown / row / row_context
    figure_strategy="reference",  # reference / caption_context
)
```

Markdown/TXT/JSON inputs do not need MinerU. PDF input calls
`mineru-kit parse`; if it is not installed the module raises an explicit
`RuntimeError` with installation instructions.

CLI:

```bash
python3 -m guideline_parsing.pipeline \
  --input guide.pdf --output guide_chunks.jsonl \
  --specialty 新生儿 --version 2025 --chunk-strategy recursive
```

## Chunk fields

Each JSONL line contains `chunk_id`, `text`, `source_id`, `source_title`,
`source_url`, `specialty`, `version`, `section_path`, `recommendation_grade`,
`evidence_level`, `keywords`, `source_file`, `page_start`, `page_end`,
`element_type`, `image_refs`, `chunk_strategy`, `table_strategy`,
`figure_strategy`. RAG's `rag.guideline_source` adds manifest metadata and
converts these into `SourceDocument` records.

## Boundaries

- The parser does not judge guideline quality, recommendation strength or
  clinical applicability; it only preserves structure and text.
- `convert_pdf` calls `page_markers.add_page_markers()` after MinerU: it aligns
  Markdown blocks with `pdftotext -layout` page text and inserts
  `<!-- page: N -->`. This is a block-level heuristic; tables/reference rows
  may stay on the page where the block starts. Without the marker
  `page_start=0` and the locator only contains the section path.
- `chunk_id` is a content/section hash and can collide on repeated text; RAG's
  loader deduplicates and warns.
- Reference-list chunks are kept by the parser; RAG drops them by default.
