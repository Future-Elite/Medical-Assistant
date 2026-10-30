import unittest

from rag.tests.fixtures import SYNTHETIC_HIS_SNAPSHOTS
from rag.his import InMemoryHISAdapter, filter_snapshot, minimize_patient_snapshot, snapshot_from_dict
from rag.patient_retrieval import retrieve_patient_facts


class HISTests(unittest.TestCase):
    def test_as_of_suppresses_future_and_keeps_stable_paths(self):
        snapshot = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS).get_snapshot("syn-neo-001", "2026-04-15")
        self.assertEqual(snapshot.suppressed_future, 1)
        self.assertTrue(all(fact.record_path != "reports[0]" for fact in snapshot.facts))
        self.assertIn("diagnoses[0]", {fact.record_path for fact in snapshot.facts})
        self.assertIn("respiratory_support[0]", {fact.record_path for fact in snapshot.facts})

    def test_minimize_only_reviewed_terms(self):
        snapshot = snapshot_from_dict({
            "patient_ref": "syn", "as_of": "2026-01-01",
            "reviewed_external_terms": ["respiratory distress syndrome"],
            "facts": [{"collection": "notes", "event_type": "note", "record_path": "notes[0]", "field_path": "notes[0].text", "fields": {"text": "secret patient narrative"}}],
        })
        self.assertEqual(minimize_patient_snapshot(snapshot), ("respiratory distress syndrome",))

    def test_patient_hit_preserves_record_path(self):
        snapshot = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS).get_snapshot("syn-neo-001", "2026-04-15")
        hits, trace = retrieve_patient_facts(snapshot, ("新生儿呼吸窘迫综合征", "respiratory distress syndrome"))
        self.assertTrue(hits)
        self.assertEqual(hits[0].source_type, "patient_record")
        self.assertIn("record_path", hits[0].__dict__)
        self.assertEqual(trace["suppressed_future"], 1)

    def test_maternal_and_infant_facts_are_separable_by_subject_role(self):
        """母婴主体不能混属：同一快照里用 subject_role 显式区分。"""
        snapshot = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS).get_snapshot("syn-neo-002", "2026-09-12")
        roles = {
            fact.fields.get("subject_role")
            for fact in snapshot.facts if fact.collection == "maternal"
        }
        self.assertEqual(roles, {"mother"})
        patient_roles = {
            fact.fields.get("subject_role")
            for fact in snapshot.facts if fact.collection != "maternal"
        }
        self.assertEqual(patient_roles, {"patient"})

    def test_missing_gestational_age_is_kept_as_not_recorded(self):
        """胎龄缺失必须显式保留为 not_recorded，不能从体重反推。"""
        snapshot = snapshot_from_dict({
            "patient_ref": "syn-neo-hypo", "as_of": "2026-04-05",
            "reviewed_external_terms": ["neonatal hypoglycemia"],
            "facts": [{
                "collection": "birth", "event_type": "birth", "record_path": "birth[0]",
                "field_path": "birth[0].ga_days", "time": "2026-04-05",
                "fields": {"subject_role": "patient", "birth_weight_g": 2100, "data_status": "not_recorded"},
            }],
        })
        birth = next(fact for fact in snapshot.facts if fact.collection == "birth")
        self.assertNotIn("ga_days", birth.fields)
        self.assertEqual(birth.fields["data_status"], "not_recorded")

    def test_filter_snapshot_keeps_open_interval_but_not_future_start(self):
        snapshot = snapshot_from_dict({
            "patient_ref": "syn-neo-open", "as_of": "2026-04-15",
            "facts": [
                {"collection": "respiratory_support", "event_type": "support", "record_path": "rs[0]", "field_path": "rs[0].mode", "start": "2026-04-10", "end": None, "fields": {"mode": "CPAP"}},
                {"collection": "respiratory_support", "event_type": "support", "record_path": "rs[1]", "field_path": "rs[1].mode", "start": "2026-04-20", "end": None, "fields": {"mode": "IMV"}},
            ],
        })
        self.assertEqual([fact.record_path for fact in filter_snapshot(snapshot, "2026-04-15").facts], ["rs[0]"])
