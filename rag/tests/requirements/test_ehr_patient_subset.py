"""Patient-side subset of the EHR knowledge-graph requirements.

These tests cite the reference tests in ``code/ehr/tests/test_graph_rag.py`` but
only assert what v6.7.3 actually implements: snapshot isolation, patient graph
structure, record paths/status, determinism and concept granularity.  The
knowledge-side P0 items (recommendation graph, four-state conditions, action
validation, release packages, knowledge API) are reported by
``tools/validate/audit_ehr_requirements.py``, not asserted as passing here.
"""
from __future__ import annotations

import unittest

from rag.core.his import snapshot_from_dict
from rag.patient_graph import (
    build_patient_graph,
    default_lexicon,
    retrieve_patient_facts,
    retrieve_patient_graph,
)


def _snapshot():
    return snapshot_from_dict({
        "patient_ref": "req-audit",
        "as_of": "2026-04-15",
        "records": [
            {"collection": "patient", "event_type": "PATIENT", "record_path": "r:patient",
             "field_path": "r:patient", "time": "2026-04-10",
             "fields": {"gender": "女", "pna_days": 5, "data_status": "observed_positive"}},
            {"collection": "derived_birth", "event_type": "DERIVED_BIRTH", "record_path": "r:birth",
             "field_path": "r:birth", "time": "2026-04-10",
             "fields": {"ga_days": 245, "ga_text": "35周", "data_status": "observed_positive",
                        "source_paths": ["r:patient"]}},
            {"collection": "derived_treatment", "event_type": "DERIVED_TREATMENT",
             "record_path": "r:treatment", "field_path": "r:treatment", "time": "2026-04-10",
             "fields": {"phototherapy": True, "phototherapy_status": "observed_positive",
                        "antibiotics": None, "antibiotics_status": "not_recorded",
                        "data_status": "observed_positive", "source_paths": ["r:patient"]}},
            {"collection": "reports", "event_type": "REPORT", "record_path": "r:future",
             "field_path": "r:future.impression", "time": "2026-05-05",
             "fields": {"impression": "未来记录", "data_status": "observed_positive"}},
        ],
    })


class EHROneRequirementSubsetTests(unittest.TestCase):
    def test_visible_snapshot_isolation(self):
        """Mirrors EHR AT-18/AT-19: future/unpublished reports are not visible."""
        graph = build_patient_graph(_snapshot())
        self.assertNotIn("record:r:future", graph.node_by_id)

    def test_patient_graph_has_attribution_relations(self):
        """Mirrors EHR test_graph_has_canonical_actions_real_relations_and_provenance (line 26)."""
        graph = build_patient_graph(_snapshot())
        relations = {edge.relation for edge in graph.edges}
        self.assertTrue({"HAS_RECORD", "HAS_SUBJECT", "MENTIONS", "DERIVED_FROM"} <= relations)

    def test_patient_retrieval_returns_path_and_status(self):
        """Mirrors the path/provenance requirement, not EHR recommendation matching."""
        graph = build_patient_graph(_snapshot())
        hits, trace = retrieve_patient_graph(graph, ["该患儿出生胎龄是多少周？"], top_k=5)
        self.assertTrue(hits)
        self.assertTrue(hits[0].graph_path)
        self.assertTrue(hits[0].status)
        self.assertIn("budget", trace)

    def test_missing_field_is_not_normal(self):
        """Mirrors AT-09/AT-13: not_recorded is explicit, not a normal value."""
        hits, _ = retrieve_patient_facts(_snapshot(), ["是否使用抗生素？"])
        self.assertTrue(hits)
        self.assertEqual(hits[0].status, "not_recorded")

    def test_same_snapshot_retrieval_is_reproducible(self):
        first, _ = retrieve_patient_graph(build_patient_graph(_snapshot()), ["该患儿出生胎龄是多少周？"], top_k=5)
        second, _ = retrieve_patient_graph(build_patient_graph(_snapshot()), ["该患儿出生胎龄是多少周？"], top_k=5)
        self.assertEqual([hit.record_path for hit in first], [hit.record_path for hit in second])

    def test_concept_granularity_is_preserved(self):
        """Mirrors KG-TERM-03: total/direct bilirubin and specimen-specific cultures stay separate."""
        lex = default_lexicon()
        total = set(lex.match_concept_ids("总胆红素"))
        direct = set(lex.match_concept_ids("直接胆红素"))
        self.assertIn("concept:bilirubin_total", total)
        self.assertNotIn("concept:bilirubin_direct", total)
        self.assertIn("concept:bilirubin_direct", direct)
        self.assertIn("concept:blood_culture", set(lex.match_concept_ids("血培养")))
        self.assertIn("concept:urine_culture", set(lex.match_concept_ids("尿培养")))


if __name__ == "__main__":
    unittest.main()
