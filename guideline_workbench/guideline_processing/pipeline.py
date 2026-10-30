"""Run MinerU PDF conversion and structure-guided guideline chunking."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .chunker import split_markdown, write_jsonl
from .pdf_to_markdown import convert_pdf


def _read_input(path: Path, markdown_dir: Path, tier: str) -> tuple[str, Path, dict]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        markdown_path = markdown_dir / f"{path.stem}.md"
        converted = convert_pdf(path, markdown_path, tier)
        return converted.read_text(encoding="utf-8"), converted, {}
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return str(payload.get("text", payload.get("content", ""))), path, payload
        return "\n".join(str(item) for item in payload), path, {}
    return path.read_text(encoding="utf-8"), path, {}


def process_guidelines(
    input_path: Path,
    output_path: Path,
    *,
    specialty: str = "肺癌",
    chunk_size: int = 800,
    overlap: int = 120,
    tier: str = "advanced",
    version: str = "",
    source_url: str = "",
    chunk_strategy: str = "structure",
    table_strategy: str = "markdown",
    figure_strategy: str = "reference",
) -> int:
    input_path = input_path.resolve()
    markdown_dir = output_path.parent / "parsed_markdown"
    if input_path.is_file():
        files = [input_path]
    elif input_path.is_dir():
        files = sorted(
            path for path in input_path.rglob("*")
            if path.suffix.lower() in {".pdf", ".md", ".markdown", ".txt", ".json"}
        )
    else:
        raise FileNotFoundError(f"输入文件或目录不存在：{input_path}")
    if not files:
        raise ValueError(f"输入目录中没有可处理的 PDF、Markdown、TXT 或 JSON：{input_path}")
    chunks = []
    for path in files:
        text, parsed_path, supplied = _read_input(path, markdown_dir, tier)
        chunks.extend(split_markdown(
            text,
            source_file=path,
            source_id=str(supplied.get("source_id") or path.stem),
            source_title=str(supplied.get("source_title") or path.stem),
            source_url=str(supplied.get("source_url") or source_url),
            specialty=str(supplied.get("specialty") or specialty),
            version=str(supplied.get("version") or version),
            chunk_size=chunk_size,
            overlap=overlap,
            chunk_strategy=chunk_strategy,
            table_strategy=table_strategy,
            figure_strategy=figure_strategy,
        ))
    write_jsonl(chunks, output_path)
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse clinical guideline PDFs with MinerU and create retrieval chunks.")
    parser.add_argument("--input", type=Path, required=True, help="PDF/Markdown/TXT/JSON file or a directory")
    parser.add_argument("--output", type=Path, default=Path("data/guideline_chunks.jsonl"))
    parser.add_argument("--specialty", default="肺癌")
    parser.add_argument("--chunk-size", type=int, default=800)
    parser.add_argument("--overlap", type=int, default=120)
    parser.add_argument("--chunk-strategy", choices=("structure", "fixed", "recursive"), default="structure")
    parser.add_argument("--table-strategy", choices=("markdown", "row", "row_context"), default="markdown")
    parser.add_argument("--figure-strategy", choices=("reference", "caption_context"), default="reference")
    parser.add_argument("--tier", choices=("standard", "advanced"), default="advanced")
    parser.add_argument("--version", default="", help="Guideline version, e.g. 2025")
    parser.add_argument("--source-url", default="", help="Official guideline page or DOI URL")
    parser.add_argument("--medical-assistant-output", type=Path, help="Also export Medical-Assistant custom-source JSON")
    parser.add_argument("--medical-assistant-source-id", default="medllm-guidelines")
    args = parser.parse_args()
    count = process_guidelines(args.input, args.output, specialty=args.specialty,
                               chunk_size=args.chunk_size, overlap=args.overlap, tier=args.tier,
                               version=args.version, source_url=args.source_url,
                               chunk_strategy=args.chunk_strategy, table_strategy=args.table_strategy,
                               figure_strategy=args.figure_strategy)
    print(f"Created {count} chunks: {args.output}")
    if args.medical_assistant_output:
        from .medical_assistant_export import export_custom_source
        exported = export_custom_source(args.output, args.medical_assistant_output, args.medical_assistant_source_id)
        print(f"Exported {exported} documents for Medical-Assistant: {args.medical_assistant_output}")


if __name__ == "__main__":
    main()
