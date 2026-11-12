"""新生儿演示档案（rag/demo/）的 schema 与内容契约。

只验证"演示输入与真实 RAG 输入一致"和"合成标记不缺失"，不评价医学正确性。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from rag.demo import (
    DEMO_PROFILE,
    PRESET_FIELDS,
    case_summaries,
    guideline_summary,
    load_demo_cases,
    load_demo_guidelines,
    register_demo_guidelines,
)
from rag.custom_sources import CustomSourceLibrary
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline


class DemoCaseTests(unittest.TestCase):
    def test_profile_is_synthetic_and_non_empty(self):
        payload = load_demo_cases()
        self.assertEqual(payload["profile"], DEMO_PROFILE)
        self.assertIs(payload["synthetic"], True)
        self.assertGreaterEqual(len(payload["cases"]), 8)

    def test_generated_his_cases_are_merged_into_default_demo(self):
        payload = load_demo_cases()
        generated = [
            case for case in payload["cases"]
            if case["case_id"].startswith("neo-his-gen-")
        ]
        self.assertEqual(len(generated), 30)
        self.assertEqual(payload["case_sets"]["generated_his"], 30)
        self.assertEqual(len(payload["cases"]), payload["case_sets"]["curated"] + 30)
        # 显式传入目录时只读该目录，不合并。
        only_generated = load_demo_cases(
            Path(__file__).resolve().parents[1] / "demo" / "his_generated_30"
        )
        self.assertEqual(len(only_generated["cases"]), 30)
        self.assertNotIn("case_sets", only_generated)

    def test_every_preset_parses_as_a_real_retrieval_request(self):
        for case in load_demo_cases()["cases"]:
            request = RetrievalRequest.from_dict({**case["preset"], "patient_snapshot": case["snapshot"]})
            self.assertTrue(request.question)
            self.assertEqual(request.source_mode, "live")
            self.assertIn(request.top_k, range(1, 51))
            self.assertTrue(request.english_terms, case["case_id"])
            self.assertTrue(request.term_mapping, case["case_id"])
            self.assertTrue(
                any(entry.english or entry.mesh for entry in request.term_mapping),
                case["case_id"],
            )
            snapshot = request.patient_snapshot
            self.assertTrue(snapshot.patient_ref)
            self.assertTrue(snapshot.facts)

    def test_preset_fields_are_a_subset_of_retrieval_request(self):
        from dataclasses import fields

        known = {item.name for item in fields(RetrievalRequest)}
        self.assertTrue(PRESET_FIELDS <= known)
        for case in load_demo_cases()["cases"]:
            self.assertEqual(sorted(set(case["preset"]) - PRESET_FIELDS), [], case["case_id"])

    def test_all_cases_are_neonatal(self):
        for case in load_demo_cases()["cases"]:
            snapshot = case["snapshot"]
            self.assertIn("neo-", snapshot["patient_ref"])
            # 每个病例都必须有出生或母体事实，避免成人病例混入
            collections = {fact["collection"] for fact in snapshot["facts"]}
            self.assertTrue({"birth", "maternal"} & collections, case["case_id"])
            self.assertEqual(case["preset"]["query_language"], "zh")

    def test_retrospective_case_as_of_precedes_rds_publication(self):
        cases = {case["case_id"]: case for case in load_demo_cases()["cases"]}
        current = cases["neo-rds"]
        retro = cases["neo-rds-retro"]
        self.assertLess(retro["snapshot"]["as_of"], "2026-03-09")
        self.assertGreaterEqual(current["snapshot"]["as_of"], "2026-03-09")

    def test_age_rules_are_documented(self):
        rules = load_demo_cases()["age_rules"]
        for key in ("gestational_age", "postnatal_age", "postmenstrual_age", "corrected_age"):
            self.assertIn(key, rules)

    def test_data_status_vocabulary_is_explicit(self):
        payload = load_demo_cases()
        self.assertEqual(
            payload["data_status_vocabulary"],
            ["observed_positive", "observed_negative", "not_recorded", "pending", "unavailable", "hypothesis"],
        )

    def test_case_summaries_keep_synthetic_flag(self):
        for summary in case_summaries():
            self.assertTrue(summary["synthetic"])
            self.assertIn("snapshot", summary)
            self.assertIn("preset", summary)


class DemoGuidelineTests(unittest.TestCase):
    def test_two_libraries_with_clear_synthetic_flags(self):
        rows = {row["source_id"]: row for row in guideline_summary(load_demo_guidelines())}
        self.assertEqual(set(rows), {"neonatal-guideline-metadata", "neonatal-guideline-synthetic", "neonatal-literature-synthetic"})
        self.assertFalse(rows["neonatal-guideline-metadata"]["synthetic"])
        self.assertTrue(rows["neonatal-guideline-synthetic"]["synthetic"])
        self.assertTrue(rows["neonatal-literature-synthetic"]["synthetic"])

    def test_metadata_records_are_metadata_only(self):
        library = load_demo_guidelines()
        metadata = next(source for source in library["sources"] if source["source_id"] == "neonatal-guideline-metadata")
        for document in metadata["documents"]:
            self.assertEqual(document["metadata"]["content_mode"], "metadata_only")
            self.assertFalse(document["metadata"]["full_text_allowed"])
            self.assertIn("doi", document)
            self.assertIn("pmid", document)

    def test_rds_update_keeps_version_and_publication_date_separate(self):
        library = load_demo_guidelines()
        metadata = next(source for source in library["sources"] if source["source_id"] == "neonatal-guideline-metadata")
        rds = next(document for document in metadata["documents"] if document["document_id"] == "guide-neo-rds-2025-meta")
        self.assertEqual(rds["version_id"], "2025")
        self.assertEqual(rds["published_at"], "2026-03-09")
        self.assertEqual(rds["pmid"], "41802129")

    def test_register_into_library_and_retrieve(self):
        library = CustomSourceLibrary()
        events = register_demo_guidelines(library)
        self.assertEqual(len(events), 3)
        registry, warnings = library.build_registry()
        self.assertFalse([warning for warning in warnings if "version" in warning])
        pipeline = EvidenceRetrievalPipeline(registry)
        case = next(case for case in load_demo_cases()["cases"] if case["case_id"] == "neo-rds")
        package = pipeline.retrieve(RetrievalRequest.from_dict(case["preset"]))
        # 合成 RDS 块应命中；元数据条目通常仅在关键词匹配时进入结果。
        self.assertTrue(package.citations)
        self.assertTrue(any(item.document.kind == "guideline" for item in package.ranked_documents))
        self.assertTrue(any("synthetic" in str(item.document.metadata.get("synthetic", "")).lower() or
                            item.document.metadata.get("content_mode") == "metadata_only"
                            for item in package.ranked_documents))

    def test_retrospective_as_of_excludes_rds_2025_metadata(self):
        """as_of 早于 2026-03-09 时，RDS 2025 更新版元数据必须被未来状态排除。"""
        library = CustomSourceLibrary()
        register_demo_guidelines(library)
        pipeline = EvidenceRetrievalPipeline(library.build_registry()[0])
        cases = {case["case_id"]: case for case in load_demo_cases()["cases"]}
        current = pipeline.retrieve(RetrievalRequest.from_dict(cases["neo-rds"]["preset"]))
        retro = pipeline.retrieve(RetrievalRequest.from_dict(cases["neo-rds-retro"]["preset"]))
        current_ids = {item.document.document_id for item in current.ranked_documents}
        retro_ids = {item.document.document_id for item in retro.ranked_documents}
        self.assertIn("guide-neo-rds-2025-meta", current_ids)
        self.assertNotIn("guide-neo-rds-2025-meta", retro_ids)
        self.assertTrue(any("未来" in warning for warning in retro.warnings))

    def test_no_adult_guideline_in_demo_library(self):
        """演示库不得包含成人指南条目；正文可以提到“母亲糖尿病”等新生儿危险因素。"""
        for source in load_demo_guidelines()["sources"]:
            for document in source["documents"]:
                title = document["title"].lower()
                for adult in ("type 2 diabetes", "amlodipine", "高血压", "2型糖尿病", "氨氯地平"):
                    self.assertNotIn(adult, title, document["document_id"])
                # 允许新生儿 PPHN 的 "pulmonary hypertension"，但不得是成人高血压指南
                if "hypertension" in title:
                    self.assertIn("pulmonary hypertension", title, document["document_id"])
                    self.assertIn("persistent", title, document["document_id"])


if __name__ == "__main__":
    unittest.main()
