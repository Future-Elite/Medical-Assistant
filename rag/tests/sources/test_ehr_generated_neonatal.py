"""EHR → HIS → RAG demo data contract (``data/`` at the repository root).

The stored snapshots must match the HIS API fixtures under the documented
Agent-side mapping (the RAG package itself never imports the adapter), and the
HIS relational tables must keep their foreign keys.  Guideline applicability is
checked against the same snapshots with the four ``synthetic_fixture`` sidecars.
"""
from __future__ import annotations

import csv
import hashlib
import json
import unittest
from collections import Counter
from pathlib import Path

from rag.quality.applicability import assess_applicability
from rag.demo import load_demo_cases
from rag.his import filter_snapshot, snapshot_from_dict
from rag.models import SourceDocument

DATA = Path(__file__).resolve().parents[3] / "data"
IDENTIFIERS = {"name", "idCard", "phone", "address", "patientId"}
RANGE_FIELDS = {"ORDER": ("startTime", "stopTime"), "ADMISSION": ("admissionTime", "dischargeTime")}
DERIVED_COLLECTIONS = {"derived_birth", "derived_respiratory_support"}


def _read_csv(name: str) -> list[dict[str, str]]:
    with (DATA / "his" / "tables" / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _api(case: dict) -> tuple[dict, dict, dict]:
    patient_id = case["snapshot"]["patient_ref"]
    admission = next(fact for fact in case["snapshot"]["facts"]
                     if fact["collection"] == "admission")
    admission_id = str(admission["fields"]["id"])
    return (
        json.loads((DATA / "his" / "api" / "patients" / f"{patient_id}.json").read_text(encoding="utf-8")),
        json.loads((DATA / "his" / "api" / "patient_timelines" / f"{patient_id}.json").read_text(encoding="utf-8")),
        json.loads((DATA / "his" / "api" / "admission_timelines" / f"{admission_id}.json").read_text(encoding="utf-8")),
    )


def _expected_facts(patient: dict, timeline: dict, admission: dict) -> list[dict]:
    """The documented Agent-side mapping, kept local so RAG never imports it."""
    record = patient["data"]
    facts = [{
        "collection": "patient", "event_type": "PATIENT", "record_path": "his:patient",
        "fields": {"gender": record.get("gender"), "birthDate": record.get("birthDate")},
        "time": record.get("createdAt"), "start": None, "end": None,
    }]
    events = timeline["data"]["events"] + admission["data"]["events"]
    events = sorted(events, key=lambda event: (event.get("time") is None, str(event.get("time") or "")))
    counters: dict[str, int] = {}
    for event in events:
        event_type = event["type"]
        counters[event_type] = counters.get(event_type, 0) + 1
        data = event.get("data") or {}
        fields = {key: value for key, value in data.items() if key not in IDENTIFIERS}
        start = end = None
        if event_type in RANGE_FIELDS:
            start_field, end_field = RANGE_FIELDS[event_type]
            start = data.get(start_field) or event.get("time")
            end = data.get(end_field)
        facts.append({
            "collection": event_type.lower(), "event_type": event_type,
            "record_path": f"his:timeline:{event_type}:{counters[event_type]}",
            "fields": fields, "time": event.get("time"), "start": start, "end": end,
        })
    return facts


@unittest.skipUnless(DATA.is_dir(), "仓库级 data/ 不随发布分支分发")
class EhrHisDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = load_demo_cases(DATA)
        cls.cases = cls.payload["cases"]

    def test_count_and_composition(self) -> None:
        self.assertEqual(len(self.cases), 50)
        counts = Counter(case["case_id"].rsplit("-", 1)[-1] for case in self.cases)
        self.assertEqual(counts, {"pneumonia": 25, "rds": 25})
        self.assertEqual(len({case["snapshot"]["patient_ref"] for case in self.cases}), 50)

    def test_snapshots_match_his_api_fixtures(self) -> None:
        for case in self.cases:
            patient, timeline, admission = _api(case)
            expected = _expected_facts(patient, timeline, admission)
            stored = case["snapshot"]
            his_actual = [fact for fact in stored["facts"]
                          if fact.get("source_type") != "derived_from_ehr_input"]
            derived = [fact for fact in stored["facts"]
                       if fact.get("source_type") == "derived_from_ehr_input"]
            self.assertEqual(stored["patient_ref"], str(patient["data"]["id"]), case["case_id"])
            self.assertEqual(len(expected), len(his_actual), case["case_id"])
            for expected_fact, actual in zip(expected, his_actual):
                self.assertEqual(expected_fact["collection"], actual["collection"], case["case_id"])
                self.assertEqual(expected_fact["record_path"], actual["record_path"], case["case_id"])
                self.assertEqual(expected_fact["time"], actual["time"], case["case_id"])
                self.assertEqual(expected_fact["start"], actual["start"], case["case_id"])
                self.assertEqual(expected_fact["end"], actual["end"], case["case_id"])
                self.assertEqual(set(expected_fact["fields"]), set(actual["fields"]), case["case_id"])
                for key, value in expected_fact["fields"].items():
                    self.assertEqual(actual["fields"][key], value, f"{case['case_id']} {key}")
            self.assertEqual({fact["collection"] for fact in derived}, DERIVED_COLLECTIONS, case["case_id"])

    def test_future_facts_and_pending_culture(self) -> None:
        for case in self.cases:
            snapshot = snapshot_from_dict({"patient_snapshot": case["snapshot"]})
            filtered = filter_snapshot(snapshot, snapshot.as_of)
            self.assertGreaterEqual(filtered.suppressed_future, 1, case["case_id"])
            self.assertEqual(filtered.warnings, (), case["case_id"])
            self.assertNotIn("discharge", {fact.collection for fact in filtered.facts}, case["case_id"])
        pending = [
            fact for case in self.cases for fact in case["snapshot"]["facts"]
            if fact["collection"] == "lab_test" and fact["fields"].get("resultValue") == "pending"
        ]
        self.assertTrue(pending)
        self.assertTrue(all(fact["fields"]["resultValue"] == "pending" for fact in pending))
        self.assertTrue(all("data_status" not in fact["fields"] for fact in pending))

    def test_his_tables_and_foreign_keys(self) -> None:
        patients = {row["id"] for row in _read_csv("patient.csv")}
        visits = {row["id"]: row for row in _read_csv("visit.csv")}
        admissions = {row["id"]: row for row in _read_csv("admission.csv")}
        records = {row["id"]: row for row in _read_csv("neonatal_record.csv")}
        source_rows = {row["id"] for row in _read_csv("neonatal_source_row.csv")}
        self.assertEqual(len(patients), 50)
        self.assertEqual(len(visits), 50)
        self.assertEqual(len(admissions), 50)
        self.assertEqual(len(records), 50)
        self.assertEqual(len(source_rows), 50)
        for row in _read_csv("diagnosis.csv"):
            self.assertIn(row["patient_id"], patients)
            self.assertIn(row["visit_id"], visits)
        for row in _read_csv("lab_test.csv"):
            self.assertIn(row["patient_id"], patients)
            self.assertIn(row["admission_id"], admissions)
        for row in _read_csv("medical_order.csv"):
            self.assertIn(row["patient_id"], patients)
            self.assertIn(row["admission_id"], admissions)
        for row in _read_csv("vital_sign.csv"):
            self.assertIn(row["patient_id"], patients)
            self.assertIn(row["admission_id"], admissions)
        for row in records.values():
            self.assertIn(row["source_row_id"], source_rows)
            self.assertEqual(row["batch_id"], "1")
        for name in ("neonatal_summary.csv", "neonatal_diagnosis.csv"):
            for row in _read_csv(name):
                self.assertIn(row["record_id"], records, name)
        self.assertEqual(_read_csv("neonatal_quality_issue.csv"), [])

    def test_manifest_hashes_and_source_trajectories(self) -> None:
        manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["case_count"], 50)
        self.assertEqual(manifest["scenario_counts"], {"rds": 25, "pneumonia": 25})
        for key, relative in (("neonatal_cases.json", "neonatal_cases.json"),
                              ("source/ehr_cases_raw.json", "source/ehr_cases_raw.json")):
            digest = hashlib.sha256((DATA / relative).read_bytes()).hexdigest()
            self.assertEqual(manifest["sha256"][key], digest, key)
        lines = (DATA / "source" / "ehr_trajectories.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 50)

    def test_repo_local_guideline_parsing_layout(self) -> None:
        root = DATA / "guideline_parsing"
        self.assertTrue((root / "guidelines" / "manifest.json").is_file())
        base_pdfs = {
            "pocus-espnic-2020", "feeding-vlbw-2015",
            "human-milk-fortification-2019", "parenteral-nutrition-2012",
        }
        base_chunks = {
            "pdf-neo-pocus-espnic-2020", "pdf-neo-feeding-vlbw-2015",
            "pdf-neo-human-milk-fortification-2019", "pdf-neo-parenteral-nutrition-2012",
        }
        pdfs = {path.stem for path in (root / "guidelines").glob("*.pdf")}
        chunks = {path.name.removesuffix("_chunks.jsonl") for path in root.glob("*_chunks.jsonl")}
        self.assertTrue(base_pdfs <= pdfs)
        self.assertTrue(base_chunks <= chunks)
        self.assertTrue((root / "parsed_markdown").is_dir())

    def test_runtime_applicability_copies_match_canonical(self) -> None:
        canonical = DATA / "guideline_applicability"
        runtime = DATA / "guideline_parsing" / "applicability"
        self.assertTrue(runtime.is_dir())
        for path in canonical.glob("*.json"):
            self.assertEqual(path.read_bytes(), (runtime / path.name).read_bytes(), path.name)

    def test_applicability_criteria_run_on_derived_facts(self) -> None:
        sidecars = sorted((DATA / "guideline_applicability").glob("*.json"))
        self.assertEqual(len(sidecars), 4)
        for path in sidecars:
            criteria = json.loads(path.read_text(encoding="utf-8"))
            document = SourceDocument(
                source_id="demo", document_id=path.stem, kind="guideline",
                title=path.stem, text="fixture", metadata={"applicability_criteria": criteria},
            )
            statuses = Counter()
            for case in self.cases:
                snapshot = snapshot_from_dict({"patient_snapshot": case["snapshot"]})
                assessment = assess_applicability(document, snapshot, ())
                statuses[assessment.status] += 1
            # synthetic_fixture conditions report possible_match, not a clinical match.
            self.assertTrue(statuses["match"] or statuses["possible_match"], path.name)
            self.assertEqual(statuses["unknown"], 0, path.name)
            self.assertTrue(set(statuses) <= {"match", "possible_match", "mismatch", "unknown"}, path.name)


if __name__ == "__main__":
    unittest.main()
