"""Guideline parsing source adapter: direct parser call, layout, applicability.

The parser package is represented by a minimal fake ``guideline_parsing``
so the adapter contract (import path, manifest, Markdown reuse, chunk output
layout, applicability sidecars) is tested offline. The real parsing package is
exercised by the integration run, not by the unit suite.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from rag.his import snapshot_from_dict
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline
from rag.sources.guideline_source import GuidelineParsingError, GuidelineParsingSource

FAKE_CHUNKER = '''
import json

def split_markdown(text, *, source_file, source_id, source_title, specialty, version,
                   source_url, chunk_size, overlap, chunk_strategy, table_strategy,
                   figure_strategy):
    return [{
        "chunk_id": "c1",
        "text": " ".join(text.split())[:120],
        "source_id": source_id,
        "source_title": source_title,
        "source_url": source_url,
        "specialty": specialty,
        "version": version,
        "section_path": [source_title, "1 Introduction"],
        "recommendation_grade": "",
        "evidence_level": "",
        "keywords": [],
        "source_file": str(source_file),
        "page_start": 2,
        "page_end": 2,
        "element_type": "text",
        "image_refs": [],
        "chunk_strategy": chunk_strategy,
        "table_strategy": table_strategy,
        "figure_strategy": figure_strategy,
    }]

def write_jsonl(chunks, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "\\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks),
        encoding="utf-8",
    )
'''

FAKE_PDF = '''
def convert_pdf(pdf_path, markdown_path, tier="advanced"):
    raise AssertionError("MinerU converter must not run when parsed Markdown is reused")
'''


@contextmanager
def _isolated_parser_modules():
    """Restore sys.path/sys.modules so fake parser imports do not leak."""
    saved_path = list(sys.path)
    saved_modules = {key: value for key, value in sys.modules.items() if key.startswith("guideline_parsing")}
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for key in [key for key in sys.modules if key.startswith("guideline_parsing")]:
            del sys.modules[key]
        sys.modules.update(saved_modules)


def _write_parser_package(directory: Path) -> Path:
    package = directory / "guideline_parsing"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('"""fake parser package"""\n', encoding="utf-8")
    (package / "chunker.py").write_text(textwrap.dedent(FAKE_CHUNKER), encoding="utf-8")
    (package / "pdf_to_markdown.py").write_text(textwrap.dedent(FAKE_PDF), encoding="utf-8")
    (package / "page_markers.py").write_text(
        "def add_page_markers(markdown, pdf_path, pages=None):\n    return markdown\n",
        encoding="utf-8",
    )
    return directory


def _snapshot(weight: int | None):
    fields = {"data_status": "observed_positive"}
    if weight is not None:
        fields["birth_weight_g"] = weight
    return snapshot_from_dict({
        "patient_ref": "p1",
        "as_of": "2026-03-10",
        "facts": [{
            "collection": "birth",
            "event_type": "birth",
            "record_path": "birth[0]",
            "fields": fields,
            "time": "2026-03-10",
        }],
    })


class GuidelineParsingSourceTests(unittest.TestCase):
    def _source(self, root: Path, *, input_name: str = "guide.md") -> GuidelineParsingSource:
        parser_root = _write_parser_package(root / "workbench")
        data = root / "data"
        (data / "guidelines").mkdir(parents=True)
        (data / "parsed_markdown").mkdir(parents=True)
        (data / "applicability").mkdir(parents=True)
        (data / "guidelines" / "manifest.json").write_text(json.dumps({
            "guidelines": [{
                "document_id": "doc-1",
                "file": input_name,
                "title": "指南A",
                "version_id": "2025",
                "doi": "10.1/example",
                "population": "新生儿",
            }],
        }, ensure_ascii=False), encoding="utf-8")
        if input_name.endswith(".md"):
            (data / "guidelines" / input_name).write_text("指南A 正文：极低出生体重儿喂养。", encoding="utf-8")
        else:
            (data / "guidelines" / input_name).write_text("", encoding="utf-8")
            (data / "parsed_markdown" / "guide.md").write_text(
                "指南A 正文：极低出生体重儿喂养。", encoding="utf-8"
            )
        (data / "applicability" / "doc-1.json").write_text(json.dumps({
            "logic": "all_of",
            "criteria": [{
                "criterion_id": "vlbw",
                "field": "birth.birth_weight_g",
                "operator": "lt",
                "value": 1500,
                "unit": "g",
                "review_status": "synthetic_fixture",
                "source": {"locator": "1 Introduction（第 2 页）"},
            }],
        }, ensure_ascii=False), encoding="utf-8")
        return GuidelineParsingSource(parser_root=parser_root, data_dir=data)

    def test_parse_writes_flat_chunk_layout_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory, _isolated_parser_modules():
            source = self._source(Path(directory))
            results = source.parse()

            self.assertEqual(results[0]["document_id"], "doc-1")
            self.assertTrue((Path(directory) / "data" / "doc-1_chunks.jsonl").is_file())
            library = source.load_library()
            document = library.sources["doc-1"][0]
            self.assertEqual(document.title, "指南A")
            self.assertEqual(document.version_id, "2025")
            self.assertEqual(document.url, "https://doi.org/10.1/example")
            self.assertEqual(document.population, "新生儿")
            self.assertEqual(document.metadata["applicability_criteria"]["criteria"][0]["criterion_id"], "vlbw")

    def test_list_guidelines_and_chunk_preview(self):
        with tempfile.TemporaryDirectory() as directory, _isolated_parser_modules():
            source = self._source(Path(directory))
            source.parse()
            rows = source.list_guidelines()
            self.assertEqual(rows[0]["document_id"], "doc-1")
            self.assertEqual(rows[0]["chunk_count"], 1)
            chunks = source.chunks("doc-1")
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0]["chunk_id"], "c1")
            with self.assertRaisesRegex(GuidelineParsingError, "尚未解析"):
                source.chunks("missing")

    def test_parse_can_select_document_ids_and_reject_unknown(self):
        with tempfile.TemporaryDirectory() as directory, _isolated_parser_modules():
            source = self._source(Path(directory))
            self.assertEqual(len(source.parse(document_ids=["doc-1"])), 1)
            with self.assertRaisesRegex(GuidelineParsingError, "未知的指南"):
                source.parse(document_ids=["missing"])

    def test_real_parser_package_importable(self):
        from guideline_parsing.chunker import split_markdown

        chunks = split_markdown(
            "# Recommendations\n\nRecommendation text for preterm infants.",
            source_file=Path("guide.md"),
            source_id="guide",
            source_title="Test guideline",
            specialty="neonatal",
            chunk_size=100,
            overlap=10,
            chunk_strategy="recursive",
        )
        self.assertTrue(chunks)
        self.assertEqual(chunks[0].section_path, ["Recommendations"])
        self.assertEqual(chunks[0].element_type, "text")

    def test_save_live_guideline_writes_manifest_and_parses(self):
        from rag.server import _save_live_guideline

        with tempfile.TemporaryDirectory() as directory:
            source = GuidelineParsingSource(data_dir=directory)
            result = _save_live_guideline(source, {
                "title": "Neonatal pneumonia guideline",
                "pmid": "40962511",
                "doi": "10.7499/j.issn.1008-8830.2412164",
                "published_at": "2025-09-01",
                "version_id": "2025",
                "license": "unknown",
                "abstract": "Recommendation text for neonatal pneumonia.",
                "evidence_type": "clinical_practice_guideline",
            })
            self.assertEqual(result["content_mode"], "abstract")
            self.assertTrue((Path(directory) / "guidelines" / f"{result['document_id']}.md").is_file())
            self.assertTrue(source.chunks(result["document_id"]))
            manifest = json.loads((Path(directory) / "guidelines" / "manifest.json").read_text(encoding="utf-8"))
            entry = next(row for row in manifest["guidelines"] if row["document_id"] == result["document_id"])
            self.assertEqual(entry["pmid"], "40962511")
            self.assertEqual(entry["content_mode"], "abstract")
            self.assertEqual(entry["license"], "unknown")

    def test_parser_modules_resolve_to_repo_guideline_parsing(self):
        source = GuidelineParsingSource(data_dir=Path(tempfile.mkdtemp()))
        chunker, pdf_to_markdown = source._parser_modules()
        package = Path(__file__).resolve().parents[3] / "guideline_parsing"
        for module in (chunker, pdf_to_markdown):
            self.assertTrue(Path(module.__file__).resolve().is_relative_to(package))

    def test_pdf_uses_preparsed_markdown_without_mineru(self):
        with tempfile.TemporaryDirectory() as directory, _isolated_parser_modules():
            source = self._source(Path(directory), input_name="guide.pdf")
            results = source.parse()
            self.assertEqual(results[0]["chunks"], 1)
            self.assertTrue(results[0]["markdown"].endswith("parsed_markdown/guide.md"))

    def test_missing_parser_package_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory, _isolated_parser_modules():
            data = Path(directory) / "data"
            (data / "guidelines").mkdir(parents=True)
            (data / "guidelines" / "manifest.json").write_text(json.dumps({
                "guidelines": [{"document_id": "doc-1", "file": "guide.md", "title": "指南A"}],
            }, ensure_ascii=False), encoding="utf-8")
            (data / "guidelines" / "guide.md").write_text("正文", encoding="utf-8")
            source = GuidelineParsingSource(
                parser_root=Path(directory) / "missing", data_dir=data
            )
            with self.assertRaisesRegex(GuidelineParsingError, "guideline_parsing"):
                source.parse()

    def test_applicability_match_mismatch_and_unknown(self):
        with tempfile.TemporaryDirectory() as directory, _isolated_parser_modules():
            source = self._source(Path(directory))
            source.parse()
            registry, _ = source.build_registry()
            pipeline = EvidenceRetrievalPipeline(registry)

            def status(weight):
                package = pipeline.retrieve(RetrievalRequest.from_dict({
                    "question": "极低出生体重儿如何喂养？",
                    "source_kinds": ["guideline"],
                    "top_k": 1,
                    "patient_snapshot": _snapshot(weight),
                }))
                return package.applicability[0].status

            self.assertEqual(status(1200), "possible_match")
            self.assertEqual(status(1800), "mismatch")
            self.assertEqual(status(None), "unknown")

    def test_create_tool_prefers_guideline_source(self):
        from rag.server import create_tool

        with tempfile.TemporaryDirectory() as directory, _isolated_parser_modules():
            source = self._source(Path(directory))
            source.parse()
            with mock.patch.dict(os.environ, {
                "RAG_GUIDELINE_DATA": str(Path(directory) / "data"),
                "RAG_GUIDELINE_AUTO_PARSE": "0",
            }):
                rag = create_tool("custom")
            result = rag.retrieve_evidence({
                "question": "极低出生体重儿如何喂养？",
                "source_mode": "custom",
                "source_kinds": ["guideline"],
                "top_k": 1,
            })
        self.assertEqual(result["ranked_documents"][0]["document"]["source_id"], "doc-1")


if __name__ == "__main__":
    unittest.main()
