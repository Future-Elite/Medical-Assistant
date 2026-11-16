"""Build the six-guide retrieval corpus with Recursive, Row, and Native representations."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from medical_assistant_export import MAX_DOCUMENTS_PER_SOURCE, export_custom_source

WORKBENCH = Path(__file__).resolve().parents[1]
WORKSPACE = Path(__file__).resolve().parents[3]
BUNDLE = WORKBENCH / "data" / "retrieval_test_bundle" / "recursive_row_native"
MARKDOWN = WORKSPACE / "data" / "parsed_markdown"
NATIVE_CHUNKS = WORKSPACE / "data" / "experiments" / "20261111" / "chunks" / "figure" / "native.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    chunks_path = BUNDLE / "chunks.jsonl"
    chunks = read_jsonl(chunks_path)
    replacements: dict[str, dict] = {}

    for md_path in MARKDOWN.glob("*.md"):
        title = md_path.stem
        if "转运" not in title and "败血症" not in title:
            continue
        title_rows = [row for row in chunks if row.get("source_title") == title]
        if not title_rows:
            continue
        content = md_path.read_text(encoding="utf-8")
        image_matches = list(re.finditer(r"!\[[^]]*\]\(data:image/([^;]+);base64,([^)]*)\)", content))
        for diagram in re.finditer(r"```mermaid\s*\n(.*?)\n```", content, re.S):
            preceding_images = [image for image in image_matches if image.start() < diagram.start()]
            if not preceding_images:
                continue
            image = preceding_images[-1]
            template = next((row for row in title_rows if row.get("element_type") == "image"), None)
            if template is None:
                continue
            caption_match = re.search(r"(图\s*\d+[^\n]*)", content[image.end():diagram.start()])
            caption = caption_match.group(1).strip() if caption_match else "流程图"
            graph = diagram.group(1).strip()
            native = dict(template)
            image_ref = template.get("image_refs", [""])[0]
            native["chunk_id"] = hashlib.sha256(f"{title}\0{image_ref}\0{graph}".encode()).hexdigest()[:16]
            native["text"] = f"{caption}\n```mermaid\n{graph}\n```"
            native["element_type"] = "flowchart"
            native["figure_strategy"] = "native"
            native["image_refs"] = [image_ref]
            native["representation_origin"] = "mineru_mermaid_reviewed_against_source_image"
            # No page/element location is inferred where structured source data is unavailable.
            replacements[template["chunk_id"]] = native

    deduped: dict[tuple[str, str], dict] = {}
    for row in chunks:
        if row.get("element_type") in {"flowchart", "chart"}:
            row["figure_strategy"] = "native"
        if row["chunk_id"] in replacements:
            row = replacements[row["chunk_id"]]
        if row.get("element_type") == "image":
            row["element_type"] = "image_unresolved"
            row["figure_strategy"] = "native"
            row["text"] = "QR code image; payload not decoded and not treated as clinical evidence."
        key = (row["chunk_id"], "|".join(row.get("image_refs", [])))
        deduped[key] = row

    chunks = list(deduped.values())
    # Restore all three tested resuscitation Native figures from the experiment output.
    native_figures = [row for row in read_jsonl(NATIVE_CHUNKS) if row.get("element_type") == "flowchart" and "复苏" in row.get("source_title", "")]
    if native_figures:
        chunks = [row for row in chunks if not (row.get("element_type") == "flowchart" and "复苏" in row.get("source_title", ""))]
        for row in native_figures:
            row["figure_strategy"] = "native"
            row["image_refs"] = ["embedded-image/jpeg#sha256=" + hashlib.sha256(Path(ref).read_bytes()).hexdigest() for ref in row.get("image_refs", []) if Path(ref).is_file()]
            for element in row.get("source_elements", []):
                element.pop("asset_paths", None)
            row["representation_origin"] = "november_experiment_native"
            chunks.append(row)

    for row in chunks:
        row["source_file"] = Path(row.get("source_file", "")).name
        for element in row.get("source_elements", []):
            element.pop("asset_paths", None)
    chunks_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in chunks), encoding="utf-8")
    batch_count = export_custom_source(chunks_path, BUNDLE / "custom_sources.json", "neonatal-guidelines", replace_all=True)
    counts = Counter((row.get("source_title"), row.get("element_type")) for row in chunks)
    manifest = {
        "configuration": {"prose": "recursive", "table": "row", "figure": "native", "chunk_size_chars": 800, "overlap_chars": 120},
        "total_chunks": len(chunks),
        "custom_source_batches": (batch_count + MAX_DOCUMENTS_PER_SOURCE - 1) // MAX_DOCUMENTS_PER_SOURCE,
        "manual_native_diagrams": sum(row.get("representation_origin") == "mineru_mermaid_reviewed_against_source_image" for row in chunks),
        "unresolved_qr_images": sum(row.get("element_type") == "image_unresolved" for row in chunks),
        "guides": [
            {"title": title, **{kind: count for (guide, kind), count in counts.items() if guide == title}}
            for title in dict.fromkeys(row["source_title"] for row in chunks)
        ],
    }
    (BUNDLE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
