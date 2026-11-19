"""HIS snapshot contract tests (visibility, minimization, subject roles)."""
import unittest

from rag.his import InMemoryHISAdapter, filter_snapshot, minimize_patient_snapshot, snapshot_from_dict
from rag.tests.fixtures import SYNTHETIC_HIS_SNAPSHOTS


class HISTests(unittest.TestCase):
    def test_as_of_suppresses_future_and_keeps_stable_paths(self):
        snapshot = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS).get_snapshot("syn-neo-001", "2026-04-15")
        self.assertEqual(snapshot.suppressed_future, 1)
        self.assertTrue(all(fact.record_id != "reports[0]" for fact in snapshot.records))
        self.assertIn("diagnoses[0]", {fact.record_id for fact in snapshot.records})
        self.assertIn("respiratory_support[0]", {fact.record_id for fact in snapshot.records})

    def test_minimize_only_reviewed_terms(self):
        snapshot = snapshot_from_dict({
            "patient_ref": "syn", "as_of": "2026-01-01",
            "reviewed_external_terms": ["respiratory distress syndrome"],
            "records": [{"record_type": "notes", "event_type": "note", "record_id": "notes[0]", "fields": {"text": "secret patient narrative"}}],
        })
        self.assertEqual(minimize_patient_snapshot(snapshot), ("respiratory distress syndrome",))

    def test_maternal_and_infant_facts_are_separable_by_subject_role(self):
        """母婴主体不能混属：同一快照里用 subject_role 显式区分。"""
        snapshot = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS).get_snapshot("syn-neo-002", "2026-09-12")
        roles = {
            fact.fields.get("subject_role")
            for fact in snapshot.records if fact.record_type == "maternal"
        }
        self.assertEqual(roles, {"mother"})
        patient_roles = {
            fact.fields.get("subject_role")
            for fact in snapshot.records if fact.record_type != "maternal"
        }
        self.assertEqual(patient_roles, {"patient"})

    def test_missing_gestational_age_is_kept_as_not_recorded(self):
        """胎龄缺失必须显式保留为 not_recorded，不能从体重反推。"""
        snapshot = snapshot_from_dict({
            "patient_ref": "syn-neo-hypo", "as_of": "2026-04-05",
            "reviewed_external_terms": ["neonatal hypoglycemia"],
            "records": [{
                "record_type": "birth", "event_type": "birth", "record_id": "birth[0]",
                "event_time": "2026-04-05",
                "fields": {"subject_role": "patient", "birth_weight_g": 2100, "data_status": "not_recorded"},
            }],
        })
        birth = next(fact for fact in snapshot.records if fact.record_type == "birth")
        self.assertNotIn("ga_days", birth.fields)
        self.assertEqual(birth.fields["data_status"], "not_recorded")

    def test_filter_snapshot_keeps_open_interval_but_not_future_start(self):
        snapshot = snapshot_from_dict({
            "patient_ref": "syn-neo-open", "as_of": "2026-04-15",
            "records": [
                {"record_type": "respiratory_support", "event_type": "support", "record_id": "rs[0]", "start": "2026-04-10", "end": None, "fields": {"mode": "CPAP"}},
                {"record_type": "respiratory_support", "event_type": "support", "record_id": "rs[1]", "start": "2026-04-20", "end": None, "fields": {"mode": "IMV"}},
            ],
        })
        self.assertEqual([fact.record_id for fact in filter_snapshot(snapshot, "2026-04-15").records], ["rs[0]"])

    def test_reported_time_is_a_visibility_gate(self):
        """A result reported after ``as_of`` is not visible yet even if collected earlier."""
        snapshot = snapshot_from_dict({
            "patient_ref": "syn-neo-reported", "as_of": "2026-04-15T12:00:00",
            "records": [
                {"record_type": "labs", "event_type": "measurement", "record_id": "lab[0]",
                 "event_time": "2026-04-15T08:00:00", "reported_time": "2026-04-15T14:00:00",
                 "fields": {"crp": 42}, "status": "final"},
                {"record_type": "labs", "event_type": "measurement", "record_id": "lab[1]",
                 "event_time": "2026-04-15T08:00:00", "reported_time": "2026-04-15T10:00:00",
                 "fields": {"crp": 8}, "status": "final"},
                {"record_type": "labs", "event_type": "measurement", "record_id": "lab[2]",
                 "event_time": None, "reported_time": "2026-04-15T13:00:00",
                 "fields": {"wbc": 12}, "status": "final"},
            ],
        })
        self.assertEqual({record.record_id for record in snapshot.records}, {"lab[1]"})
        self.assertEqual(snapshot.suppressed_future, 2)

    def test_record_status_is_preserved(self):
        snapshot = snapshot_from_dict({
            "patient_ref": "syn-neo-status", "as_of": "2026-04-15",
            "records": [{"record_type": "labs", "event_type": "culture", "record_id": "culture[0]",
                         "event_time": "2026-04-15", "status": "pending",
                         "fields": {"culture_status": "pending"}}],
        })
        record = snapshot.records[0]
        self.assertEqual(record.status, "pending")
        self.assertEqual(record.record_id, "culture[0]")
        self.assertEqual(record.record_type, "labs")

    def test_legacy_facts_key_is_rejected(self):
        with self.assertRaises(ValueError):
            snapshot_from_dict({"patient_ref": "syn", "as_of": "2026-04-15", "facts": []})

    def test_his_style_import_keys_are_normalized(self):
        """The import layer accepts HIS rows, but stores only the contract fields."""
        snapshot = snapshot_from_dict({
            "patient_ref": "syn", "as_of": "2026-04-15",
            "records": [{"collection": "birth", "event_type": "birth",
                         "record_path": "birth[0]", "field_path": "birth[0].ga_days",
                         "time": "2026-04-10", "source_type": "his",
                         "fields": {"ga_days": 210}}],
        })
        record = snapshot.records[0]
        self.assertEqual((record.record_type, record.record_id, record.event_time, record.source_system),
                         ("birth", "birth[0]", "2026-04-10", "his"))

    def test_date_only_as_of_uses_local_record_date(self):
        """A +08:00 record just after midnight is next-day local time, not UTC."""
        snapshot = snapshot_from_dict({
            "patient_ref": "syn-neo-tz", "as_of": "2026-04-15",
            "records": [
                {"record_type": "labs", "event_type": "measurement", "record_id": "lab[0]",
                 "event_time": "2026-04-15T23:00:00+08:00", "fields": {"crp": 8}},
                {"record_type": "labs", "event_type": "measurement", "record_id": "lab[1]",
                 "event_time": "2026-04-16T01:00:00+08:00", "fields": {"crp": 42}},
            ],
        })
        self.assertEqual([record.record_id for record in snapshot.records], ["lab[0]"])
        self.assertEqual(snapshot.suppressed_future, 1)
