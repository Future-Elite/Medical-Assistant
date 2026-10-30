"""Build and evaluate a small factorial chunking/structure experiment."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
from pathlib import Path
from typing import Any

from .chunker import split_markdown, write_jsonl
from .evaluation import evaluate_systems, load_queries, save_report
from .medical_assistant_export import export_custom_source

ROOT = Path(__file__).resolve().parents[1]
GUIDES = (
    {
        "file": "基层医疗卫生机构常见新生儿疾病诊疗指南：新生儿感染性肺炎（2025年）.md",
        "source_file": "基层医疗卫生机构常见新生儿疾病诊疗指南：新生儿感染性肺炎（2025年）.pdf",
        "specialty": "新生儿",
        "url": "https://www.zgddek.com/CN/10.7499/j.issn.1008-8830.2412164",
    },
    {
        "file": "《欧洲新生儿呼吸窘迫综合征管理共识指南2025版》解读.md",
        "source_file": "《欧洲新生儿呼吸窘迫综合征管理共识指南2025版》解读.pdf",
        "specialty": "新生儿呼吸窘迫综合征",
        "url": "https://www.zgddek.com/CN/10.7499/j.issn.1008-8830.2603060",
    },
    {
        "file": "中国新生儿复苏指南（2021年修订）.md",
        "source_file": "中国新生儿复苏指南（2021年修订）.pdf",
        "specialty": "新生儿复苏",
        "url": "https://doi.org/10.3760/cma.j.cn113903-20211122-00967",
    },
)


def _variants() -> list[dict[str, str]]:
    variants = [
        {
            "name": f"{chunk}-{table}",
            "chunk_strategy": chunk,
            "table_strategy": table,
            "figure_strategy": "reference",
        }
        for chunk, table in itertools.product(
            ("fixed", "recursive", "structure"),
            ("markdown", "row", "row_context"),
        )
    ]
    variants.extend([
        {"name": "figure-reference", "chunk_strategy": "structure", "table_strategy": "markdown", "figure_strategy": "reference"},
        {"name": "figure-caption-context", "chunk_strategy": "structure", "table_strategy": "markdown", "figure_strategy": "caption_context"},
    ])
    return variants


def run_experiments(
    markdown_dir: Path,
    benchmark_path: Path,
    output_dir: Path,
    *,
    chunk_size: int = 800,
    overlap: int = 120,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir = output_dir / "chunks"
    libraries_dir = output_dir / "libraries"
    chunks_dir.mkdir(exist_ok=True)
    libraries_dir.mkdir(exist_ok=True)
    variants = _variants()
    systems = []
    chunk_stats = {}
    figure_reference_count = 0
    for guide in GUIDES:
        markdown_path = markdown_dir / guide["file"]
        if markdown_path.is_file():
            figure_reference_count += sum(
                line.count("](") for line in markdown_path.read_text(encoding="utf-8").splitlines()
                if line.lstrip().startswith("!")
            )

    for variant in variants:
        variant_chunks = []
        library_path = libraries_dir / f"{variant['name']}.json"
        if library_path.exists():
            library_path.unlink()
        for guide in GUIDES:
            markdown_path = markdown_dir / guide["file"]
            if not markdown_path.is_file():
                raise FileNotFoundError(f"MinerU Markdown not found: {markdown_path}")
            chunks = split_markdown(
                markdown_path.read_text(encoding="utf-8"),
                source_file=ROOT / "data" / "guidelines" / guide["source_file"],
                source_id=markdown_path.stem,
                source_title=markdown_path.stem,
                source_url=guide["url"],
                specialty=guide["specialty"],
                version="2021" if "2021" in guide["source_file"] else "2025",
                chunk_size=chunk_size,
                overlap=overlap,
                chunk_strategy=variant["chunk_strategy"],
                table_strategy=variant["table_strategy"],
                figure_strategy=variant["figure_strategy"],
            )
            chunk_path = chunks_dir / f"{variant['name']}-{markdown_path.stem}.jsonl"
            write_jsonl(chunks, chunk_path)
            export_custom_source(
                chunk_path,
                library_path,
                f"experiment-{variant['name']}-{markdown_path.stem}",
            )
            variant_chunks.extend(chunks)
        chunk_stats[variant["name"]] = {
            **variant,
            "chunk_count": len(variant_chunks),
            "mean_chunk_chars": round(
                sum(len(chunk.text) for chunk in variant_chunks) / max(len(variant_chunks), 1), 2
            ),
            "element_counts": {
                kind: sum(chunk.element_type == kind for chunk in variant_chunks)
                for kind in ("text", "table", "figure")
            },
        }
        systems.append({"name": variant["name"], "library": str(library_path)})

    queries = load_queries(benchmark_path)
    report = evaluate_systems(
        queries,
        systems,
        benchmark=queries[0].get("benchmark"),
    )
    report["experiment"] = {
        "design": "3 chunk strategies x 3 table representations, plus 2 figure handling variants",
        "chunk_size_chars": chunk_size,
        "overlap_chars": overlap,
        "parser": "existing MinerU Markdown; PDF parsing held constant",
        "retriever": "Medical-Assistant default BM25",
        "variant_stats": chunk_stats,
        "figure_reference_count": figure_reference_count,
        "figure_comparison_interpretable": figure_reference_count > 0,
        "limitations": [
            f"The benchmark is a {len(queries)}-query pilot and needs clinical review.",
            f"The selected Markdown files contain {figure_reference_count} image references; figure strategy effects cannot be inferred when this is zero.",
            "Fixed/recursive/structure variants are compared with the same character-size and overlap settings, not token counts.",
            "Evidence-anchor coverage measures whether annotated phrases co-occur in a chunk, not clinical completeness by expert judgment.",
        ],
    }
    save_report(report, output_dir / "latest.json")
    _write_summary_csv(report, output_dir / "summary.csv")
    return report


def _write_summary_csv(report: dict[str, Any], path: Path) -> None:
    fields = [
        "variant", "chunk_strategy", "table_strategy", "figure_strategy", "chunk_count",
        "mean_chunk_chars", "query_coverage", "gold_anchor_coverage", "recall@3", "hit@3",
        "mrr@3", "ndcg@3", "element_hit@3", "context_chars@3", "latency_ms",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        stats = report["experiment"]["variant_stats"]
        for system in report["systems"]:
            metric = system["summary"].get("3", {})
            config = stats[system["system"]]
            writer.writerow({
                "variant": system["system"],
                **{key: config[key] for key in (
                    "chunk_strategy", "table_strategy", "figure_strategy", "chunk_count", "mean_chunk_chars"
                )},
                "query_coverage": system["query_coverage"],
                "gold_anchor_coverage": system["gold_anchor_coverage"],
                "recall@3": metric.get("recall"),
                "hit@3": metric.get("hit"),
                "mrr@3": metric.get("mrr"),
                "ndcg@3": metric.get("ndcg"),
                "element_hit@3": metric.get("element_hit"),
                "context_chars@3": metric.get("context_chars"),
                "latency_ms": system["average_latency_ms"],
            })


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare chunking and table/figure representations on the guideline pilot.")
    parser.add_argument("--markdown-dir", type=Path, default=ROOT / "data" / "parsed_markdown")
    parser.add_argument("--benchmark", type=Path, default=ROOT / "data" / "guideline_eval_pilot.jsonl")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "chunking_experiments")
    parser.add_argument("--chunk-size", type=int, default=800)
    parser.add_argument("--overlap", type=int, default=120)
    args = parser.parse_args()
    report = run_experiments(args.markdown_dir, args.benchmark, args.output_dir,
                             chunk_size=args.chunk_size, overlap=args.overlap)
    print(f"Built {len(report['systems'])} variants; summary: {args.output_dir / 'summary.csv'}")


if __name__ == "__main__":
    main()
