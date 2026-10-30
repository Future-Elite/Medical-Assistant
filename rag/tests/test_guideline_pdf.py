"""Caller-supplied guideline PDF connector (``guideline-pdf-local``).

Offline and deterministic: every test injects a ``text_extractor`` so no PDF is
opened, no ``pdftotext`` subprocess runs and no network is touched.  What is
pinned here:

* manifest validation (unique ids, required file/rights fields);
* page-located chunking with a *stable*, per-chunk document id (DOI must not
  collapse all chunks through the global dedup key);
* two-column reading order for ``pdftotext -bbox-layout`` output;
* topic routing (an RDS question must not receive the pneumonia guideline);
* license/rights fields are recorded but never gate retrieval.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rag.guideline_pdf import (
    LocalGuidelinePdfConnector,
    _detect_gutter,
    _join_words,
    _page_text,
    _Word,
    build_documents,
    clean_page,
    extract_pdf_text,
    load_manifest,
    page_chunks,
    split_pages,
)
from rag.models import QueryPlan

MANIFEST = {
    "profile": "test_pdf_v1",
    "guidelines": [
        {
            "document_id": "guideline:rds",
            "file": "rds.pdf",
            "title": "European RDS guideline 2025",
            "short_title": "RDS 指南",
            "doi": "10.1159/000551062",
            "pmid": "41802129",
            "version_id": "2025",
            "population": "preterm infants respiratory distress syndrome",
            "license": "CC BY-NC 4.0",
            "rights_basis": "publisher_open_access_cc_by_nc",
            "full_text_allowed": True,
            "language": "en",
            "topic_keys": ["respiratory distress syndrome", "RDS", "surfactant", "呼吸窘迫综合征"],
        },
        {
            "document_id": "guideline:pneumonia",
            "file": "pna.pdf",
            "title": "Neonatal infectious pneumonia guideline",
            "short_title": "肺炎指南",
            "doi": "10.7499/example",
            "version_id": "2025",
            "population": "新生儿感染性肺炎",
            "license": "journal copyright",
            "rights_basis": "caller_provided_pdf",
            "full_text_allowed": True,
            "language": "zh",
            "topic_keys": ["neonatal pneumonia", "感染性肺炎", "肺炎"],
        },
    ],
}

RDS_PAGES = [
    "European Consensus Guidelines on the Management of Respiratory Distress Syndrome: 2025\n\n"
    "Introduction\n\n"
    "Surfactant therapy improves survival in preterm infants with respiratory distress syndrome.\n\n"
    "Recommendations\n\n"
    "1. Surfactant should be given early in the course of RDS (A1).\n"
    "2. CPAP should be started from birth in all babies at risk (A1).\n",
    "Monitoring\n\n"
    "Oxygen saturation should be monitored continuously during stabilisation on CPAP.\n",
]
PNA_PAGES = [
    "新生儿感染性肺炎基层指南\n\n推荐意见11：新生儿感染性肺炎的诊断需结合高危因素、临床表现、影像学证据和实验室检查（GPS）。\n",
]


def _fake_extractor(pages: list[str]):
    text = "\f".join(pages)

    def extractor(path: Path) -> str:
        if path.name == "rds.pdf":
            return text if pages is RDS_PAGES else "\f".join(RDS_PAGES)
        return "\f".join(PNA_PAGES)

    return extractor


class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        (cls.dir / "guideline_pdfs.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (cls.dir / "rds.pdf").write_bytes(b"%PDF-1.4 fake")
        (cls.dir / "pna.pdf").write_bytes(b"%PDF-1.4 fake")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_manifest_loads_and_keeps_provenance(self):
        payload = load_manifest(self.dir)
        self.assertEqual(len(payload["guidelines"]), 2)
        first = payload["guidelines"][0]
        self.assertEqual(first["doi"], "10.1159/000551062")
        self.assertEqual(first["rights_basis"], "publisher_open_access_cc_by_nc")

    def test_manifest_rejects_missing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # rights_basis 不再是必填（全文策略不设许可闸门），只校验 id/文件与布尔字段
            for name, broken in (
                ("no-file", {"document_id": "x", "rights_basis": "r"}),
                ("bad-flag", {"document_id": "x", "file": "a.pdf", "full_text_allowed": "yes"}),
                ("dup", None),
            ):
                if broken is None:
                    entries = [
                        {"document_id": "x", "file": "a.pdf", "rights_basis": "r"},
                        {"document_id": "x", "file": "b.pdf", "rights_basis": "r"},
                    ]
                else:
                    entries = [broken]
                (base / "guideline_pdfs.json").write_text(json.dumps({"guidelines": entries}), encoding="utf-8")
                with self.assertRaises(ValueError, msg=name):
                    load_manifest(base)

    def test_full_text_allowed_defaults_to_true(self):
        """Policy: every registered guideline is searchable as full text."""
        entry = {"document_id": "x", "file": "a.pdf"}
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "guideline_pdfs.json").write_text(json.dumps({"guidelines": [entry]}), encoding="utf-8")
            (base / "a.pdf").write_bytes(b"%PDF")
            connector = LocalGuidelinePdfConnector(directory=base, text_extractor=lambda _p: "正文\n")
            self.assertTrue(connector.describe_entries()[0]["full_text_allowed"])


class ChunkingTests(unittest.TestCase):
    def test_clean_page_drops_headers_footers_and_page_numbers(self):
        page = (
            "第 27 卷 第 9 期\n中国当代儿科杂志\n"
            "正文第一段内容。\n·1028·\n"
            "正文第二段内容。\n"
            "Downloaded from karger.com by guest\n"
        )
        cleaned = clean_page(page)
        self.assertIn("正文第一段内容。", cleaned)
        self.assertIn("正文第二段内容。", cleaned)
        self.assertNotIn("Downloaded from", cleaned)
        self.assertNotIn("中国当代儿科杂志", cleaned)
        self.assertNotIn("1028", cleaned)

    def test_page_chunks_keep_heading_and_text(self):
        page = "Surfactant Therapy\n\nSurfactant reduces pneumothorax in RDS.\n\nRecommendations\n\n1. Give surfactant early (A1)."
        chunks = page_chunks(page)
        texts = " ".join(chunk for _, chunk in chunks)
        self.assertIn("reduces pneumothorax", texts)
        self.assertIn("Give surfactant early", texts)

    def test_split_pages_drops_trailing_empty_page(self):
        self.assertEqual(len(split_pages("a\fb\f")), 2)

    def test_build_documents_is_page_located_and_does_not_collapse_on_doi(self):
        entry = MANIFEST["guidelines"][0]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "rds.pdf").write_bytes(b"%PDF")
            documents = build_documents(
                entry, base, query_terms=("surfactant", "CPAP"),
                max_chunks=6, text_extractor=lambda _p: "\f".join(RDS_PAGES),
            )
        self.assertTrue(documents)
        ids = [document.document_id for document in documents]
        self.assertEqual(len(ids), len(set(ids)), "每个 chunk 必须有唯一 document_id")
        self.assertTrue(all(document.doi is None for document in documents), "DOI 不能挂在每个 chunk 上")
        self.assertTrue(all(document.metadata["doi"] == entry["doi"] for document in documents))
        self.assertTrue(all("p." in document.locator for document in documents))
        self.assertTrue(all(document.metadata["content_mode"] == "licensed_full_text" for document in documents))
        self.assertTrue(all(document.metadata["rights_basis"] == entry["rights_basis"] for document in documents))
        # 查询相关的 chunk 排在前面
        self.assertIn("surfactant", documents[0].text.lower())

    def test_empty_pdf_raises_instead_of_silent_empty_result(self):
        def boom(_path: Path) -> str:
            raise ValueError("broken")

        entry = MANIFEST["guidelines"][0]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "rds.pdf").write_bytes(b"%PDF")
            with self.assertRaises(ValueError):
                build_documents(entry, base, text_extractor=boom)


class ColumnOrderTests(unittest.TestCase):
    def _word(self, text: str, x0: float, y0: float, width: float = 40.0) -> _Word:
        return _Word(text=text, x0=x0, x1=x0 + width, y0=y0, y1=y0 + 10)

    def test_gutter_is_detected_on_two_column_page(self):
        words = [self._word("left", 40, y) for y in range(0, 500, 12)]
        words += [self._word("right", 320, y) for y in range(0, 500, 12)]
        self.assertIsNotNone(_detect_gutter(words, 595.0))

    def test_single_column_page_has_no_gutter(self):
        words = [self._word("wide", 40, y) for y in range(0, 500, 12)]
        self.assertIsNone(_detect_gutter(words, 595.0))

    def test_reading_order_keeps_left_column_before_right(self):
        words = []
        for index in range(20):
            y = 100 + index * 12
            words.append(self._word(f"L{index}", 40, y))
            words.append(self._word(f"R{index}", 320, y))
        text = _page_text(words, 595.0)
        self.assertLess(text.index("L0"), text.index("R0"))

    def test_join_words_inserts_spaces_for_latin_and_not_for_cjk(self):
        latin = [_Word("surfactant", 0, 40, 0, 10), _Word("therapy", 45, 85, 0, 10)]
        self.assertEqual(_join_words(latin), "surfactant therapy")
        cjk = [_Word("新生儿", 0, 30, 0, 10), _Word("肺炎", 30, 60, 0, 10)]
        self.assertEqual(_join_words(cjk), "新生儿肺炎")


class TopicRoutingTests(unittest.TestCase):
    def _connector(self, pages=RDS_PAGES):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        (base / "guideline_pdfs.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (base / "rds.pdf").write_bytes(b"%PDF")
        (base / "pna.pdf").write_bytes(b"%PDF")
        return LocalGuidelinePdfConnector(directory=base, text_extractor=_fake_extractor(pages))

    def _plan(self, question: str, terms=()) -> QueryPlan:
        return QueryPlan(question, (question,), tuple(terms), ("guideline-pdf-local",),
                         {"source_kinds": ["guideline"]}, (), english_terms=tuple(terms))

    def test_rds_question_does_not_receive_pneumonia_guideline(self):
        connector = self._connector()
        result = connector.search(self._plan("该患儿新生儿呼吸窘迫综合征的肺表面活性物质指征？",
                                            ("respiratory distress syndrome",)), limit=10)
        sources = {document.document_id.split(":")[1] for document in result.documents}
        self.assertEqual(sources, {"rds"})
        self.assertTrue(any("主题不符" in warning for warning in result.warnings))

    def test_pneumonia_question_does_not_receive_rds_guideline(self):
        connector = self._connector()
        result = connector.search(self._plan("该患儿新生儿感染性肺炎的诊断依据？", ("neonatal pneumonia",)), limit=10)
        sources = {document.document_id.split(":")[1] for document in result.documents}
        self.assertEqual(sources, {"pneumonia"})

    def test_unregistered_topic_returns_nothing_rather_than_unrelated_guideline(self):
        connector = self._connector()
        result = connector.search(self._plan("该患儿新生儿黄疸的光疗指征？", ("neonatal jaundice",)), limit=10)
        self.assertEqual(result.documents, ())
        self.assertTrue(any("没有与本问题主题相符" in warning for warning in result.warnings))

    def test_generic_question_keeps_every_guideline(self):
        connector = self._connector()
        result = connector.search(self._plan("新生儿指南推荐有哪些？"), limit=10)
        sources = {document.document_id.split(":")[1] for document in result.documents}
        self.assertEqual(sources, {"rds", "pneumonia"})
        self.assertEqual(result.warnings, ())

    def test_source_kinds_filter_honoured(self):
        connector = self._connector()
        plan = QueryPlan("新生儿感染性肺炎", ("q",), (), ("guideline-pdf-local",),
                         {"source_kinds": ["pubmed"]}, ())
        self.assertEqual(connector.search(plan, limit=5).documents, ())

    def test_guideline_version_filter_honoured(self):
        connector = self._connector()
        plan = QueryPlan("新生儿呼吸窘迫综合征", ("q",), (), ("guideline-pdf-local",),
                         {"source_kinds": ["guideline"], "guideline_version": "2019"}, (),
                         english_terms=("respiratory distress syndrome",))
        self.assertEqual(connector.search(plan, limit=5).documents, ())


class ExtractorErrorTests(unittest.TestCase):
    def test_missing_pdf_reports_exact_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception) as context:
                extract_pdf_text(Path(tmp) / "missing.pdf")
            self.assertIn("not found", str(context.exception))

    def test_oversized_pdf_is_refused(self):
        from rag.guideline_pdf import MAX_PDF_BYTES

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.pdf"
            path.write_bytes(b"0" * (MAX_PDF_BYTES + 1))
            with self.assertRaises(Exception) as context:
                extract_pdf_text(path)
            self.assertIn("larger than", str(context.exception))

    def test_entity_declaration_is_refused(self):
        from rag.guideline_pdf import _safe_xml_root

        payload = b"<!DOCTYPE x [<!ENTITY a 'b'>]><html/>"
        with self.assertRaises(Exception) as context:
            _safe_xml_root(payload)
        self.assertIn("entity", str(context.exception).lower())


if __name__ == "__main__":
    unittest.main()
