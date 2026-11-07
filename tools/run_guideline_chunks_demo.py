"""Run v6.7.1 retrieval over local guideline parsing chunks.

Two entry modes:

* ``--guideline-data``: call the local ``guideline_parsing`` parser (optional
  ``--parse``) and load its data layout (``guidelines/``,
  ``parsed_markdown/``, ``<id>_chunks.jsonl``, ``applicability/``); this is the
  "guideline source is the local parser" path.
* ``--chunks``: load already-produced chunk JSONL / export library directly.

Usage (from the repository root)::

    python3 tools/run_guideline_chunks_demo.py \
      --guideline-data /path/to/guideline_data \
      --parse \
      --question "极低出生体重儿如何开始肠内喂养？"

Output is JSON on stdout: loader summary, warnings, and per-question top-k
documents, citations, and applicability assessments.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

VERSION_ROOT = Path(__file__).resolve().parents[1]
if str(VERSION_ROOT) not in sys.path:
    sys.path.insert(0, str(VERSION_ROOT))

from rag.sources.guideline_chunks import load_guideline_chunk_library
from rag.server import create_tool
from rag.sources.guideline_source import GuidelineParsingSource


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--chunks", type=Path, help="chunk JSONL, directory, or exported library JSON")
    source.add_argument("--guideline-data", type=Path, help="guideline parsing data directory")
    parser.add_argument("--parser-root", type=Path, help="directory containing guideline_parsing/ (default: repository root)")
    parser.add_argument("--parse", action="store_true", help="call guideline_parsing before loading")
    parser.add_argument("--applicability-dir", type=Path, help="criteria sidecars for --chunks mode")
    parser.add_argument("--question", action="append", required=True, help="repeatable question")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--version", default="", help="optional guideline_version filter")
    args = parser.parse_args()

    if args.guideline_data:
        parsing = GuidelineParsingSource(
            parser_root=args.parser_root,
            data_dir=args.guideline_data,
        )
        if args.parse:
            parsing.parse()
        library = parsing.load_library()
        os.environ["RAG_GUIDELINE_DATA"] = str(args.guideline_data.resolve())
        os.environ["RAG_GUIDELINE_AUTO_PARSE"] = "0"
    else:
        library = load_guideline_chunk_library(
            args.chunks, applicability_dir=args.applicability_dir
        )
        os.environ["RAG_GUIDELINE_CHUNKS"] = str(args.chunks.resolve())
        os.environ.pop("RAG_GUIDELINE_DATA", None)

    rag = create_tool("custom")
    output = {
        "source_mode": "guideline_parsing" if args.guideline_data else "chunks",
        "sources": library.describe(),
        "loader_warnings": library.warnings,
        "results": [],
    }
    for index, question in enumerate(args.question, 1):
        request = {
            "request_id": f"guideline-chunks-demo-{index}",
            "question": question,
            "source_mode": "custom",
            "source_kinds": ["guideline"],
            "top_k": args.top_k,
        }
        if args.version:
            request["filters"] = {"guideline_version": args.version}
        result = rag.retrieve_evidence(request)
        rows = []
        for ranked in result.get("ranked_documents", []):
            document = ranked["document"]
            rows.append({
                "score": ranked.get("score"),
                "document_id": document.get("document_id"),
                "title": document.get("title"),
                "locator": document.get("locator"),
                "version_id": document.get("version_id"),
                "metadata": {
                    key: document.get("metadata", {}).get(key)
                    for key in ("chunk_id", "section_path", "element_type", "page_start", "page_end")
                },
                "text": document.get("text", "")[:500],
            })
        output["results"].append({
            "question": question,
            "warnings": result.get("warnings", []),
            "ranked_documents": rows,
            "citations": result.get("citations", []),
            "applicability": result.get("applicability", []),
        })
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
