"""自定义来源接口契约：注册、校验、检索、清除、启动预载。

产品不内置任何本地语料（无 offline/simulated 模式）；调用方通过
``CustomSourceLibrary`` / ``POST /api/sources/custom`` 传入自己的数据库。
这些测试锁定该接口的 schema 校验、替换语义与检索行为（离线）。
"""
from __future__ import annotations

import json
import pathlib
import unittest

from rag.custom_sources import (
    CustomSource,
    CustomSourceLibrary,
    custom_source_from_dict,
    load_custom_library,
)
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline
from rag.tests.helpers import fixture_library, fixture_registry


def _documents(**overrides):
    document = {
        "document_id": "local-1", "kind": "guideline", "title": "本地指南（示例）",
        "text": "对高血压成人患者的示例推荐。", "version_id": "2025",
        "recommendation_id": "1.1", "locator": "第一章",
    }
    document.update(overrides)
    return [document]


class PayloadValidationTests(unittest.TestCase):
    def test_minimal_payload_registers_and_infers_kinds(self):
        source = custom_source_from_dict({"source_id": "lib-1", "documents": _documents()})
        self.assertEqual(source.kinds, ("guideline",))
        self.assertEqual(len(source.documents), 1)
        self.assertEqual(source.documents[0].source_id, "lib-1")

    def test_unknown_document_fields_go_to_metadata(self):
        source = custom_source_from_dict({
            "source_id": "lib-1",
            "documents": _documents(issuer="某协会", custom_flag=True),
        })
        self.assertEqual(source.documents[0].metadata["issuer"], "某协会")
        self.assertTrue(source.documents[0].metadata["custom_flag"])

    def test_missing_required_fields_are_explicit(self):
        with self.assertRaisesRegex(ValueError, "source_id"):
            custom_source_from_dict({"documents": _documents()})
        with self.assertRaisesRegex(ValueError, "documents"):
            custom_source_from_dict({"source_id": "lib-1"})
        with self.assertRaisesRegex(ValueError, "non-empty"):
            custom_source_from_dict({"source_id": "lib-1", "documents": []})
        with self.assertRaisesRegex(ValueError, "document_id"):
            custom_source_from_dict({"source_id": "lib-1", "documents": [{"kind": "guideline", "title": "t", "text": "x"}]})

    def test_declared_kinds_must_cover_documents(self):
        with self.assertRaisesRegex(ValueError, "declared kinds"):
            custom_source_from_dict({"source_id": "lib-1", "kinds": ["pubmed"], "documents": _documents()})

    def test_source_ownership_is_pinned(self):
        # 显式声明了不同归属 => 报错（不悄悄改写）；省略 source_id 则继承来源库 id。
        with self.assertRaisesRegex(ValueError, "omit source_id to inherit"):
            custom_source_from_dict({"source_id": "lib-1", "documents": _documents(source_id="other")})
        inherited = custom_source_from_dict({"source_id": "lib-1", "documents": _documents()})
        self.assertEqual(inherited.documents[0].source_id, "lib-1")


class LibraryTests(unittest.TestCase):
    def test_register_replace_and_clear_with_events(self):
        library = CustomSourceLibrary()
        first = library.register(custom_source_from_dict({"source_id": "lib-1", "documents": _documents()}))
        self.assertEqual(first["action"], "register")
        second = library.register(custom_source_from_dict({
            "source_id": "lib-1", "documents": _documents(document_id="local-2", title="第二版"),
        }))
        self.assertEqual(second["action"], "replace")
        self.assertEqual(second["previous_document_count"], 1)
        self.assertEqual(len(library), 1)
        self.assertEqual(library.describe()[0]["document_count"], 1)
        removed = library.clear()
        self.assertEqual(removed, 1)
        self.assertEqual(len(library), 0)

    def test_empty_library_raises_instead_of_searching_nothing(self):
        with self.assertRaisesRegex(ValueError, "no custom sources registered"):
            CustomSourceLibrary().build_registry()

    def test_build_registry_serves_documents_via_connector(self):
        registry = fixture_library().build_registry()[0]
        pipeline = EvidenceRetrievalPipeline(registry)
        package = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": "该患者高血压的指南推荐是什么？", "as_of": "2025-12-31", "top_k": 4,
            "english_terms": ["hypertension"], "source_mode": "custom",
        }))
        self.assertTrue(package.citations)
        source_ids = {citation.source_id for citation in package.citations}
        self.assertIn("test-guideline-library", source_ids)
        # 库内文档归属经 register 固定为该来源 id（连接器只返回自己拥有的文档）。
        source = fixture_library().get("test-guideline-library")
        self.assertTrue(all(document.source_id == "test-guideline-library" for document in source.documents))

    def test_guideline_version_filter_and_audit_warning(self):
        library = CustomSourceLibrary()
        library.register(custom_source_from_dict({
            "source_id": "lib-1",
            "documents": _documents() + [{
                "document_id": "no-version", "kind": "guideline", "title": "无版本指南",
                "text": "缺少 version_id/locator 的条目。",
            }],
        }))
        registry, warnings = library.build_registry()
        self.assertTrue(any("version" in warning for warning in warnings))
        pipeline = EvidenceRetrievalPipeline(registry)
        package = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": "高血压成人患者的推荐", "as_of": "2025-12-31", "top_k": 4,
            "filters": {"guideline_version": "2025"}, "source_mode": "custom",
        }))
        kept = {item.document.document_id for item in package.ranked_documents}
        self.assertIn("local-1", kept)
        self.assertNotIn("no-version", kept)
        # 注册表构建期警告（缺 version 快照标签）必须随结果返回，而不是只进进程日志。
        self.assertTrue(any("version 快照标签" in warning for warning in package.warnings), package.warnings)

    def test_unknown_version_warns_explicitly(self):
        registry, _ = fixture_library().build_registry()
        pipeline = EvidenceRetrievalPipeline(registry)
        package = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": "高血压指南推荐", "as_of": "2025-12-31", "top_k": 4,
            "filters": {"guideline_version": "1999"}, "source_mode": "custom",
        }))
        self.assertTrue(any("未找到请求的指南版本" in warning for warning in package.warnings))


class LibraryFileTests(unittest.TestCase):
    def test_load_from_file_wraps_index_errors(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "lib.json"
            path.write_text(json.dumps({"sources": [
                {"source_id": "ok", "documents": _documents()},
                {"documents": _documents()},
            ]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"sources\[1\]"):
                load_custom_library(path)

    def test_load_example_register_payload(self):
        example = pathlib.Path(__file__).resolve().parents[1] / "contracts" / "examples" / "agent_custom_source_register.json"
        source = custom_source_from_dict(json.loads(example.read_text(encoding="utf-8")))
        self.assertEqual(source.kinds, ("guideline", "pubmed"))
        self.assertEqual(len(source.documents), 2)


class SourceModeTests(unittest.TestCase):
    def test_only_two_modes_accepted(self):
        from rag.models import RetrievalRequest
        request = RetrievalRequest.from_dict({"question": "x", "source_mode": "custom"})
        self.assertEqual(request.source_mode, "custom")
        request = RetrievalRequest.from_dict({"question": "x", "source_mode": "live"})
        self.assertEqual(request.source_mode, "live")
        for legacy in ("simulated", "live_pubmed", "offline"):
            with self.assertRaisesRegex(ValueError, "source_mode"):
                RetrievalRequest.from_dict({"question": "x", "source_mode": legacy})

    def test_server_only_creates_live_and_custom(self):
        from rag.server import SOURCE_MODES, create_tool
        self.assertEqual(tuple(SOURCE_MODES), ("live", "custom"))
        with self.assertRaisesRegex(ValueError, "unknown source_mode"):
            create_tool("simulated")


if __name__ == "__main__":
    unittest.main()
