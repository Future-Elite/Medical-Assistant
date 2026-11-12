"""新生儿演示档案（``data/``）的 schema、HIS 对齐与内容契约。

只验证“演示输入与真实 RAG 输入一致”“HIS 事实结构”“合成标记不缺失”和
``as_of`` 语义，不评价医学正确性。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from rag.custom_sources import CustomSourceLibrary
from rag.demo import (
    DEMO_PROFILE,
    PRESET_FIELDS,
    case_summaries,
    guideline_summary,
    load_demo_cases,
    load_demo_guidelines,
    register_demo_guidelines,
)
from rag.his import filter_snapshot, snapshot_from_dict
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
HIS_COLLECTIONS = {
    "patient", "visit", "admission", "diagnosis", "neonatal_record",
    "neonatal_summary", "neonatal_diagnosis", "vital_sign", "lab_test", "order",
}
IDENTIFIERS = {"name", "idCard", "phone", "address", "patientId"}


def _rds_cases() -> list[dict]:
    return [case for case in load_demo_cases()["cases"] if case["case_id"].endswith("-rds")]


class DemoCaseTests(unittest.TestCase):
    def test_profile_is_synthetic_and_has_50_his_aligned_cases(self):
        payload = load_demo_cases()
        self.assertEqual(payload["profile"], DEMO_PROFILE)
        self.assertIs(payload["synthetic"], True)
        self.assertEqual(len(payload["cases"]), 50)
        self.assertEqual(payload["case_sets"], {"ehr_his_generated": 50})
        self.assertEqual(len({case["case_id"] for case in payload["cases"]}), 50)

    def test_every_case_has_the_his_fact_set(self):
        for case in load_demo_cases()["cases"]:
            collections = {fact["collection"] for fact in case["snapshot"]["facts"]}
            missing = HIS_COLLECTIONS - collections
            self.assertFalse(missing, f"{case['case_id']} missing {sorted(missing)}")
            self.assertIn("discharge", collections, case["case_id"])
            derived = {fact["collection"] for fact in case["snapshot"]["facts"]
                       if fact.get("source_type") == "derived_from_ehr_input"}
            self.assertEqual(derived, {"derived_birth", "derived_respiratory_support"}, case["case_id"])
            self.assertTrue(case["snapshot"]["patient_ref"].isdigit(), case["case_id"])
            self.assertEqual(case["snapshot"]["source"], "his_demo")

    def test_patient_fact_drops_direct_identifiers(self):
        for case in load_demo_cases()["cases"]:
            patient = next(fact for fact in case["snapshot"]["facts"]
                           if fact["collection"] == "patient")
            self.assertEqual(set(patient["fields"]), {"gender", "birthDate"})
            self.assertFalse(IDENTIFIERS & set(patient["fields"]), case["case_id"])

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
            self.assertTrue(request.patient_snapshot.patient_ref)
            self.assertTrue(request.patient_snapshot.facts)

    def test_preset_fields_are_a_subset_of_retrieval_request(self):
        from dataclasses import fields

        known = {item.name for item in fields(RetrievalRequest)}
        self.assertTrue(PRESET_FIELDS <= known)
        for case in load_demo_cases()["cases"]:
            self.assertEqual(sorted(set(case["preset"]) - PRESET_FIELDS), [], case["case_id"])

    def test_his_facts_are_unmodified_and_derived_facts_are_separate(self):
        his_fields = {
            "id", "batchId", "hospitalNo", "gender", "reportedAge", "reportedAgeDays",
            "gestationalAgeRaw", "gestationalWeeks", "gestationalDays", "admissionTime",
            "dischargeTime", "admissionTimeRaw", "dischargeTimeRaw", "lengthOfStay",
            "treatmentResult", "outcomeGroup", "issueCount", "rowNumber", "sourceFile",
            "admissionDiagnosis", "dischargeDiagnosis",
        }
        for case in load_demo_cases()["cases"]:
            record = next(fact for fact in case["snapshot"]["facts"]
                          if fact["collection"] == "neonatal_record")
            self.assertEqual(set(record["fields"]), his_fields, case["case_id"])
            self.assertNotIn("source_type", record, case["case_id"])
            derived = {fact["collection"]: fact for fact in case["snapshot"]["facts"]
                       if fact.get("source_type") == "derived_from_ehr_input"}
            self.assertEqual(set(derived), {"derived_birth", "derived_respiratory_support"})
            self.assertTrue(
                {"ga_days", "ga_text", "birth_weight_g", "data_status"} <= set(derived["derived_birth"]["fields"]),
                case["case_id"],
            )
            self.assertTrue(
                {"mode", "fio2", "spo2_percent", "data_status"} <= set(derived["derived_respiratory_support"]["fields"]),
                case["case_id"],
            )

    def test_future_facts_are_suppressed_and_pending_is_not_negative(self):
        for case in load_demo_cases()["cases"]:
            snapshot = snapshot_from_dict({"patient_snapshot": case["snapshot"]})
            filtered = filter_snapshot(snapshot, snapshot.as_of)
            self.assertGreaterEqual(filtered.suppressed_future, 1, case["case_id"])
            self.assertEqual(filtered.warnings, (), case["case_id"])
        cultures = [
            fact for case in load_demo_cases()["cases"] for fact in case["snapshot"]["facts"]
            if fact["collection"] == "lab_test" and fact["fields"].get("resultValue") == "pending"
        ]
        self.assertTrue(cultures)
        for fact in cultures:
            self.assertEqual(fact["fields"]["resultValue"], "pending")
            self.assertNotIn("data_status", fact["fields"])

    def test_publication_boundary_has_cases_on_both_sides(self):
        cases = _rds_cases()
        self.assertTrue(any(case["snapshot"]["as_of"] < "2026-03-09" for case in cases))
        self.assertTrue(any(case["snapshot"]["as_of"] >= "2026-03-09" for case in cases))

    def test_age_rules_and_status_vocabulary_are_documented(self):
        payload = load_demo_cases()
        for key in ("gestational_age", "postnatal_age", "postmenstrual_age", "corrected_age"):
            self.assertIn(key, payload["age_rules"])
        self.assertEqual(
            payload["data_status_vocabulary"],
            ["observed_positive", "observed_negative", "not_recorded", "pending", "unavailable", "hypothesis"],
        )

    def test_case_summaries_keep_synthetic_flag(self):
        for summary in case_summaries():
            self.assertTrue(summary["synthetic"])
            self.assertIn("snapshot", summary)
            self.assertIn("preset", summary)

    def test_explicit_directory_reads_only_that_directory(self):
        payload = load_demo_cases(DATA_DIR)
        self.assertEqual(len(payload["cases"]), 50)


class DemoGuidelineTests(unittest.TestCase):
    def test_three_libraries_with_clear_synthetic_flags(self):
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
        case = _rds_cases()[0]
        package = pipeline.retrieve(RetrievalRequest.from_dict(case["preset"]))
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
        current = next(case for case in _rds_cases() if case["snapshot"]["as_of"] >= "2026-03-09")
        retro = next(case for case in _rds_cases() if case["snapshot"]["as_of"] < "2026-03-09")
        current_package = pipeline.retrieve(RetrievalRequest.from_dict(current["preset"]))
        retro_package = pipeline.retrieve(RetrievalRequest.from_dict(retro["preset"]))
        current_ids = {item.document.document_id for item in current_package.ranked_documents}
        retro_ids = {item.document.document_id for item in retro_package.ranked_documents}
        self.assertIn("guide-neo-rds-2025-meta", current_ids)
        self.assertNotIn("guide-neo-rds-2025-meta", retro_ids)
        self.assertTrue(any("未来" in warning for warning in retro_package.warnings))

    def test_no_adult_guideline_in_demo_library(self):
        """演示库不得包含成人指南条目；正文可以提到“母亲糖尿病”等新生儿危险因素。"""
        for source in load_demo_guidelines()["sources"]:
            for document in source["documents"]:
                title = document["title"].lower()
                for adult in ("type 2 diabetes", "amlodipine", "高血压", "2型糖尿病", "氨氯地平"):
                    self.assertNotIn(adult, title, document["document_id"])
                if "hypertension" in title:
                    self.assertIn("pulmonary hypertension", title, document["document_id"])
                    self.assertIn("persistent", title, document["document_id"])


if __name__ == "__main__":
    unittest.main()
