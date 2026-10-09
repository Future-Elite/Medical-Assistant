import unittest

from rag.fixtures import SYNTHETIC_HIS_SNAPSHOTS
from rag.his import InMemoryHISAdapter, PatientSnapshot, filter_snapshot, minimize_patient_snapshot, snapshot_from_dict
from rag.patient_retrieval import retrieve_patient_facts


class HISTests(unittest.TestCase):
    def test_as_of_suppresses_future_and_keeps_stable_paths(self):
        snapshot = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS).get_snapshot("syn-patient-001", "2025-12-31")
        self.assertEqual(snapshot.suppressed_future, 1)
        self.assertTrue(all(fact.record_path != "reports[0]" for fact in snapshot.facts))
        self.assertIn("diagnoses[0]", {fact.record_path for fact in snapshot.facts})

    def test_minimize_only_reviewed_terms(self):
        snapshot = snapshot_from_dict({
            "patient_ref": "syn", "as_of": "2025-01-01",
            "reviewed_external_terms": ["hypertension"],
            "facts": [{"collection": "notes", "event_type": "note", "record_path": "notes[0]", "field_path": "notes[0].text", "fields": {"text": "secret patient narrative"}}],
        })
        self.assertEqual(minimize_patient_snapshot(snapshot), ("hypertension",))

    def test_patient_hit_preserves_record_path(self):
        snapshot = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS).get_snapshot("syn-patient-001", "2025-12-31")
        hits, trace = retrieve_patient_facts(snapshot, ("高血压", "hypertension"))
        self.assertTrue(hits)
        self.assertEqual(hits[0].source_type, "patient_record")
        self.assertIn("record_path", hits[0].__dict__)
        self.assertEqual(trace["suppressed_future"], 1)
