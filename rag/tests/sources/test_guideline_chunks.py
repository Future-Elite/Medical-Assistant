"""外部指南工作台分块（guideline chunks）的加载与检索契约。

``guideline_parsing`` 输出结构感知 chunk；这些测试锁定 v6.7.1 的
适配层：标题保持指南名、chunk 身份不被标题去重折叠、参考文献默认过滤、
单指南全量候选不被连接器 limit 截断，以及版本过滤与显式警告。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rag.sources.guideline_chunks import load_guideline_chunk_library
from rag.models import QueryPlan, RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline


def _record(
    chunk_id: str = "c1",
    text: str = "推荐意见：对新生儿感染性肺炎患儿经验性使用抗生素。",
    *,
    source_id: str = "guide-a",
    title: str = "指南A",
    section: list[str] | None = None,
    page: int = 2,
    **overrides,
):
    payload = {
        "chunk_id": chunk_id,
        "text": text,
        "source_id": source_id,
        "source_title": title,
        "source_url": "https://example.org/guide-a",
        "specialty": "新生儿",
        "version": "2025",
        "section_path": section if section is not None else [title, "1 推荐"],
        "recommendation_grade": "",
        "evidence_level": "",
        "keywords": [],
        "source_file": "/home/caller/private/guide-a.pdf",
        "page_start": page,
        "page_end": page,
        "element_type": "text",
        "image_refs": [],
        "chunk_strategy": "structure",
        "table_strategy": "markdown",
        "figure_strategy": "reference",
    }
    payload.update(overrides)
    return payload


def _write_jsonl(directory: str, records: list[dict], name: str = "guide_chunks.jsonl") -> Path:
    path = Path(directory) / name
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )
    return path


def _plan(**filters) -> QueryPlan:
    return QueryPlan(
        original_question="新生儿肺炎如何治疗？",
        variants=("新生儿肺炎如何治疗？",),
        concepts=(),
        source_order=(),
        filters=filters,
        rationale=(),
    )


class LoaderTests(unittest.TestCase):
    def test_titles_stay_clean_and_chunks_keep_their_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_jsonl(directory, [
                _record("c1"),
                _record("c2", text="推荐意见：根据药敏调整抗生素。", page=3,
                        source_file=r"C:\Users\caller\private\guide-a.pdf"),
            ])
            library = load_guideline_chunk_library(path)

        self.assertEqual(list(library.sources), ["guide-a"])
        first, second = library.sources["guide-a"]
        self.assertEqual(first.title, "指南A")
        self.assertEqual(first.metadata["chunk_id"], "c1")
        self.assertEqual(first.locator, "1 推荐（第 2 页）")
        self.assertEqual(first.version_id, "2025")
        self.assertEqual(first.population, "新生儿")
        self.assertEqual(first.metadata["source_file"], "guide-a.pdf")
        self.assertEqual(second.metadata["source_file"], "guide-a.pdf")
        self.assertEqual(first.metadata["content_mode"], "caller_parsed_chunks")
        self.assertNotEqual(first.identity_key(), second.identity_key())
        self.assertTrue(first.identity_key().startswith("chunk:"))

    def test_reference_sections_are_dropped_by_default(self):
        records = [
            _record("body", section=["指南A", "1 推荐"]),
            _record("refs-en", text="1. Author A. Title. Journal. 2020.", section=["指南A", "References"], page=9),
            _record("refs-zh", text="[1] 作者. 标题. 期刊. 2020.", section=["指南A", "参考文献"], page=10),
            _record("front", text="OPEN ACCESS Citation: Author et al.", section=["指南A", "OPEN ACCESS", "Citation"], page=1),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = _write_jsonl(directory, records)
            default = load_guideline_chunk_library(path)
            kept_refs = load_guideline_chunk_library(path, include_references=True)
            kept_all = load_guideline_chunk_library(path, include_references=True, exclude_sections=())

        self.assertEqual(len(default.sources["guide-a"]), 1)
        self.assertEqual(default.dropped_references, 2)
        self.assertEqual(default.dropped_non_evidence, 1)
        self.assertTrue(any("参考文献段落" in warning for warning in default.warnings))
        self.assertTrue(any("前置/元数据段落" in warning for warning in default.warnings))
        self.assertEqual(len(kept_refs.sources["guide-a"]), 3)
        self.assertEqual(kept_refs.dropped_references, 0)
        self.assertEqual(len(kept_all.sources["guide-a"]), 4)
        self.assertEqual(kept_all.dropped_non_evidence, 0)

    def test_missing_version_and_page_produce_explicit_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_jsonl(directory, [_record("c1", version="", page=0, section_path=[], source_file="")])
            library = load_guideline_chunk_library(path)

        warnings = "\n".join(library.warnings)
        self.assertIn("缺少 version", warnings)
        self.assertIn("缺少页码", warnings)
        self.assertIn("缺少章节和页码", warnings)

    def test_duplicate_chunk_id_is_skipped_with_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_jsonl(directory, [
                _record("c1", text="第一段。"),
                _record("c1", text="重复段。"),
            ])
            library = load_guideline_chunk_library(path)

        self.assertEqual(len(library.sources["guide-a"]), 1)
        self.assertTrue(any("重复 chunk_id" in warning for warning in library.warnings))

    def test_invalid_jsonl_line_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text('{"chunk_id": "c1", "text": "ok"}\nnot-json\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON 解析失败"):
                load_guideline_chunk_library(path)

    def test_empty_after_filtering_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_jsonl(directory, [
                _record("refs", section=["指南A", "References"]),
            ])
            with self.assertRaisesRegex(ValueError, "没有可用的指南分块"):
                load_guideline_chunk_library(path)

    def test_exported_custom_source_library_is_accepted(self):
        export = {
            "sources": [{
                "source_id": "guide-a-001",
                "kinds": ["guideline"],
                "description": "Guideline chunks generated by MedLLM guideline_parsing",
                "version": "2025",
                "documents": [{
                    "document_id": "c1",
                    "kind": "guideline",
                    "title": "指南A [c1]",
                    "text": "推荐意见：经验性使用抗生素。",
                    "locator": "1 推荐（第 2 页）",
                    "version_id": "2025",
                    "metadata": {
                        "chunk_id": "c1",
                        "guide_title": "指南A",
                        "section_path": ["指南A", "1 推荐"],
                        "element_type": "text",
                        "page_start": 2,
                        "page_end": 2,
                    },
                }],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "medical_assistant_guidelines.json"
            path.write_text(json.dumps(export, ensure_ascii=False), encoding="utf-8")
            library = load_guideline_chunk_library(path)

        document = library.sources["guide-a"][0]
        self.assertEqual(document.title, "指南A")
        self.assertEqual(document.document_id, "c1")
        self.assertEqual(document.version_id, "2025")

    def test_applicability_sidecar_is_attached_and_missing_file_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_jsonl(directory, [_record("c1")])
            applicability = Path(directory) / "applicability"
            applicability.mkdir()
            (applicability / "guide-a.json").write_text(json.dumps({
                "logic": "all_of",
                "criteria": [{
                    "criterion_id": "c1", "field": "birth.birth_weight_g", "operator": "lt",
                    "value": 1500, "review_status": "synthetic_fixture",
                    "source": {"locator": "1 推荐（第 2 页）"},
                }],
            }, ensure_ascii=False), encoding="utf-8")
            loaded = load_guideline_chunk_library(path, applicability_dir=applicability)
            (applicability / "guide-a.json").unlink()
            missing = load_guideline_chunk_library(path, applicability_dir=applicability)

        self.assertEqual(
            loaded.sources["guide-a"][0].metadata["applicability_criteria"]["criteria"][0]["criterion_id"],
            "c1",
        )
        self.assertTrue(any("未提供 applicability 条件" in warning for warning in missing.warnings))


class ConnectorTests(unittest.TestCase):
    def test_full_corpus_connector_does_not_truncate_to_pipeline_limit(self):
        # 第 60 条才含查询词；若连接器沿用 limit=50 的文件序截断，它永远进不了排序。
        records = [_record(f"c{index:02d}", text="无关的指南正文。") for index in range(1, 60)]
        records.append(_record("c60", text="独特目标词：推荐使用肺表面活性物质。"))
        with tempfile.TemporaryDirectory() as directory:
            path = _write_jsonl(directory, records)
            library = load_guideline_chunk_library(path)

        registry, _ = library.build_registry()
        result = EvidenceRetrievalPipeline(registry).retrieve(RetrievalRequest.from_dict({
            "question": "独特目标词",
            "source_kinds": ["guideline"],
            "top_k": 5,
        }))
        ranked_ids = [item.document.document_id for item in result.ranked_documents]
        self.assertIn("c60", ranked_ids)

    def test_version_filter_and_explicit_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_jsonl(directory, [
                _record("c1", source_id="guide-2025", title="指南2025", version="2025"),
                _record("c2", source_id="guide-2024", title="指南2024", version="2024"),
            ])
            library = load_guideline_chunk_library(path)

        registry, _ = library.build_registry()
        connector = registry._connectors["guide-2025"]  # noqa: SLF001 - test inspects the registered connector
        selected = connector.search(_plan(guideline_version="2025", source_kinds=["guideline"]), limit=50)
        self.assertEqual([doc.document_id for doc in selected.documents], ["c1"])

        missing = connector.search(_plan(guideline_version="2019", source_kinds=["guideline"]), limit=50)
        self.assertEqual(missing.documents, ())
        self.assertTrue(any("未找到请求的指南版本" in warning for warning in missing.warnings))

    def test_source_kinds_filter_is_respected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_jsonl(directory, [_record("c1")])
            library = load_guideline_chunk_library(path)
        registry, _ = library.build_registry()
        connector = next(iter(registry._connectors.values()))  # noqa: SLF001
        result = connector.search(_plan(source_kinds=["pubmed"]), limit=50)
        self.assertEqual(result.documents, ())

    def test_create_tool_loads_chunks_without_custom_library(self):
        from rag.server import create_tool

        with tempfile.TemporaryDirectory() as directory:
            path = _write_jsonl(directory, [
                _record("c1", text="推荐意见：新生儿肺炎经验性使用抗生素。"),
            ])
            with mock.patch.dict(os.environ, {"RAG_GUIDELINE_CHUNKS": str(path)}):
                rag = create_tool("custom")

        result = rag.retrieve_evidence({
            "question": "新生儿肺炎如何治疗？",
            "source_mode": "custom",
            "source_kinds": ["guideline"],
            "top_k": 3,
        })
        self.assertEqual(
            [row["document"]["document_id"] for row in result["ranked_documents"]],
            ["c1"],
        )


if __name__ == "__main__":
    unittest.main()
