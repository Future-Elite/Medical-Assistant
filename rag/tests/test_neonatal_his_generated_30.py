"""30-case HIS-style neonatal demo batch (``rag/demo/his_generated_30/``)."""
from __future__ import annotations

import csv
import hashlib
import json
import unittest
from pathlib import Path

from rag.demo import PRESET_FIELDS, load_demo_cases
from rag.his import snapshot_from_dict
from rag.models import RetrievalRequest

GENERATED = Path(__file__).resolve().parents[1] / "demo" / "his_generated_30"


class GeneratedNeonatalHIS30Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = load_demo_cases(GENERATED)
        cls.cases = cls.payload["cases"]

    def test_count_and_unique_ids(self) -> None:
        self.assertEqual(len(self.cases), 30)
        self.assertEqual(len({case["case_id"] for case in self.cases}), 30)
        self.assertEqual(len({case["snapshot"]["patient_ref"] for case in self.cases}), 30)
        for case in self.cases:
            self.assertIs(case["synthetic"], True, case["case_id"])
            self.assertIn("neo-", case["snapshot"]["patient_ref"], case["case_id"])

    def test_every_case_parses_as_a_request(self) -> None:
        for case in self.cases:
            case_id = case["case_id"]
            self.assertEqual(sorted(set(case["preset"]) - PRESET_FIELDS), [], case_id)
            self.assertNotIn("request_id", case["preset"], case_id)
            self.assertNotIn("patient_snapshot", case["preset"], case_id)
            request = RetrievalRequest.from_dict({
                **case["preset"],
                "patient_snapshot": case["snapshot"],
            })
            self.assertTrue(request.patient_snapshot.facts, case_id)

    def test_future_records_and_pending_cultures(self) -> None:
        for case in self.cases:
            raw = case["snapshot"]
            snapshot = snapshot_from_dict({"patient_snapshot": raw})
            future = sum(
                1
                for fact in raw["facts"]
                if (fact.get("start") or fact.get("time"))
                and (fact.get("start") or fact.get("time")) > raw["as_of"]
            )
            self.assertEqual(snapshot.suppressed_future, future, case["case_id"])
        cultures = [
            fact
            for case in self.cases
            for fact in case["snapshot"]["facts"]
            if fact["collection"] == "labs" and fact["event_type"] == "culture"
        ]
        self.assertTrue(cultures)
        for fact in cultures:
            self.assertEqual(fact["fields"]["data_status"], "pending")

    def test_case_index_and_manifest_hashes(self) -> None:
        manifest = json.loads((GENERATED / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["case_count"], 30)
        self.assertEqual(manifest["generation_mode"], "his_style_weighted_ontology")
        for key in ("cases", "snapshots", "requests", "case_index"):
            info = manifest["files"][key]
            digest = hashlib.sha256((GENERATED / info["name"]).read_bytes()).hexdigest()
            self.assertEqual(info["sha256"], digest, key)
        with (GENERATED / "case_index.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 30)

    def test_csv_foreign_keys(self) -> None:
        def read(name: str) -> list[dict[str, str]]:
            with (GENERATED / "tables" / name).open(encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle))

        patients = {row["patient_id"] for row in read("患者.csv")}
        visits = {row["就诊ID"]: row for row in read("就诊.csv")}
        self.assertEqual(len(patients), 30)
        self.assertEqual(len(visits), 30)
        for row in read("母亲.csv"):
            self.assertIn(row["patient_id"], patients)
        for name in ("诊断.csv", "医嘱_药品.csv", "检验.csv", "体征.csv", "呼吸支持.csv", "手术操作.csv"):
            for row in read(name):
                self.assertIn(row["就诊ID"], visits, name)


if __name__ == "__main__":
    unittest.main()
