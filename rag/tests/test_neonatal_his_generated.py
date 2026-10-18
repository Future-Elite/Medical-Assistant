"""Generated neonatal HIS data (``rag/demo/his_generated/``) contract tests.

The generated directory is a *separate* demo profile, so the curated
``neonatal_cases.json`` stays untouched.  These tests check the properties the
generator promises: deterministic synthetic cases, real ``PatientSnapshot`` /
``RetrievalRequest`` parsing, ``as_of`` suppression, pending-not-negative
semantics, and relational CSV foreign keys.
"""
from __future__ import annotations

import csv
import hashlib
import json
import unittest
from pathlib import Path

from rag.demo import PRESET_FIELDS, load_demo_cases
from rag.his import snapshot_from_dict
from rag.models import RetrievalRequest

GENERATED = Path(__file__).resolve().parents[1] / "demo" / "his_generated"


def _read_table(name: str) -> list[dict[str, str]]:
    with (GENERATED / "tables" / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class GeneratedNeonatalHISCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = load_demo_cases(GENERATED)
        cls.cases = cls.payload["cases"]

    def test_profile_is_synthetic_and_case_ids_are_unique(self) -> None:
        self.assertEqual(self.payload["profile"], "neonatal_demo_v1")
        self.assertIs(self.payload["synthetic"], True)
        self.assertGreaterEqual(len(self.cases), 10)
        self.assertEqual(len(self.cases), len({case["case_id"] for case in self.cases}))

    def test_every_case_is_neonatal_and_parses_as_a_request(self) -> None:
        for case in self.cases:
            case_id = case["case_id"]
            self.assertIs(case["synthetic"], True, case_id)
            self.assertIn("neo-", case["snapshot"]["patient_ref"], case_id)
            collections = {fact["collection"] for fact in case["snapshot"]["facts"]}
            self.assertTrue({"birth", "maternal"} & collections, case_id)
            self.assertEqual(case["preset"]["query_language"], "zh", case_id)
            self.assertTrue(case["preset"]["english_terms"], case_id)
            self.assertEqual(sorted(set(case["preset"]) - PRESET_FIELDS), [], case_id)
            self.assertNotIn("request_id", case["preset"], case_id)
            self.assertNotIn("patient_snapshot", case["preset"], case_id)
            request = RetrievalRequest.from_dict({
                **case["preset"],
                "patient_snapshot": case["snapshot"],
            })
            self.assertTrue(request.patient_snapshot.facts, case_id)

    def test_future_records_are_suppressed_at_as_of(self) -> None:
        for case in self.cases:
            snapshot_raw = case["snapshot"]
            snapshot = snapshot_from_dict({"patient_snapshot": snapshot_raw})
            future = sum(
                1
                for fact in snapshot_raw["facts"]
                if (fact.get("start") or fact.get("time"))
                and (fact.get("start") or fact.get("time")) > snapshot_raw["as_of"]
            )
            self.assertEqual(snapshot.suppressed_future, future, case["case_id"])
            self.assertGreaterEqual(future, 1, case["case_id"])

    def test_pending_culture_is_never_written_as_negative(self) -> None:
        cultures = [
            fact
            for case in self.cases
            for fact in case["snapshot"]["facts"]
            if fact["collection"] == "labs" and fact["event_type"] == "culture"
        ]
        self.assertTrue(cultures)
        for fact in cultures:
            self.assertEqual(fact["fields"]["data_status"], "pending")
            self.assertEqual(fact["fields"]["culture_status"], "pending")

    def test_ga_text_matches_ga_days(self) -> None:
        for case in self.cases:
            birth = next(fact for fact in case["snapshot"]["facts"] if fact["collection"] == "birth")
            ga_days = birth["fields"].get("ga_days")
            ga_text = birth["fields"].get("ga_text")
            if ga_days is None:
                self.assertIsNone(ga_text, case["case_id"])
            else:
                self.assertEqual(ga_text, f"{ga_days // 7}周+{ga_days % 7}天", case["case_id"])

    def test_data_status_values_are_in_the_documented_vocabulary(self) -> None:
        vocabulary = self.payload["data_status_vocabulary"]
        for case in self.cases:
            for fact in case["snapshot"]["facts"]:
                self.assertIn(fact["fields"]["data_status"], vocabulary, case["case_id"])

    def test_question_numbers_match_the_snapshot(self) -> None:
        """Templates must quote generated values, not hard-coded ones."""
        for case in self.cases:
            question = case["preset"]["question"]
            facts = case["snapshot"]["facts"]
            for fact in facts:
                fields = fact["fields"]
                for key in ("total_bilirubin_umol_l", "glucose_mmol_l"):
                    if key in fields and fields[key] is not None:
                        # Only assert when the question actually quotes the value.
                        value = fields[key]
                        if isinstance(value, float):
                            rendered = f"{value:.1f}".rstrip("0").rstrip(".")
                        else:
                            rendered = str(value)
                        if "胆红素" in question and key == "total_bilirubin_umol_l":
                            self.assertIn(rendered, question, case["case_id"])
                        if "血糖" in question and key == "glucose_mmol_l":
                            self.assertIn(rendered, question, case["case_id"])


class GeneratedNeonatalHISFileTests(unittest.TestCase):
    def test_requests_json_parses_as_real_retrieval_requests(self) -> None:
        payload = json.loads((GENERATED / "requests.json").read_text(encoding="utf-8"))
        self.assertIs(payload["synthetic"], True)
        self.assertTrue(payload["requests"])
        for item in payload["requests"]:
            request = RetrievalRequest.from_dict(item["request"])
            self.assertTrue(request.question)
            self.assertIsNotNone(request.patient_snapshot)

    def test_snapshots_json_parses_as_patient_snapshots(self) -> None:
        payload = json.loads((GENERATED / "snapshots.json").read_text(encoding="utf-8"))
        self.assertIs(payload["synthetic"], True)
        self.assertTrue(payload["patients"])
        for raw in payload["patients"]:
            snapshot = snapshot_from_dict({"patient_snapshot": raw})
            self.assertTrue(snapshot.facts)

    def test_csv_foreign_keys_and_synthetic_markers(self) -> None:
        patients = {row["patient_id"] for row in _read_table("患者.csv")}
        visits = {row["就诊ID"]: row for row in _read_table("就诊.csv")}
        institutions = {row["机构ID"] for row in _read_table("机构.csv")}
        doctors = {row["医生ID"] for row in _read_table("医生.csv")}
        self.assertTrue(patients)
        self.assertTrue(visits)
        for row in _read_table("母亲.csv"):
            self.assertIn(row["patient_id"], patients)
        for row in visits.values():
            self.assertIn(row["patient_id"], patients)
            self.assertIn(row["机构ID"], institutions)
            self.assertIn(row["医生ID"], doctors)
        for name in ("诊断.csv", "医嘱_药品.csv", "检验.csv", "体征.csv", "呼吸支持.csv", "手术操作.csv"):
            for row in _read_table(name):
                self.assertIn(row["就诊ID"], visits, name)
                self.assertEqual(row["synthetic"], "true", name)

    def test_manifest_hashes_match_files(self) -> None:
        manifest = json.loads((GENERATED / "manifest.json").read_text(encoding="utf-8"))
        self.assertIs(manifest["synthetic"], True)
        self.assertEqual(manifest["case_count"], len(load_demo_cases(GENERATED)["cases"]))
        for key in ("cases", "snapshots", "requests"):
            info = manifest["files"][key]
            digest = hashlib.sha256((GENERATED / info["name"]).read_bytes()).hexdigest()
            self.assertEqual(info["sha256"], digest, key)


if __name__ == "__main__":
    unittest.main()
