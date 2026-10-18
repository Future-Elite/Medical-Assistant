import unittest

from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_HIS_SNAPSHOTS, SYNTHETIC_PUBMED
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline


class JointRetrievalTests(unittest.TestCase):
    def setUp(self):
        registry = ConnectorRegistry()
        registry.register(VersionedGuidelineConnector("guideline-fixture", SYNTHETIC_GUIDELINES))
        registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
        self.pipeline = EvidenceRetrievalPipeline(registry)

    def test_patient_hits_groups_and_trace(self):
        package = self.pipeline.retrieve(RetrievalRequest.from_dict({
            "question": "新生儿呼吸窘迫综合征指南推荐和最新证据？", "as_of": "2026-04-15",
            "patient_snapshot": {
                "patient_ref": "syn-neo-001", "as_of": "2026-04-15", "source": "synthetic_neonatal_chart",
                "reviewed_external_terms": ["respiratory distress syndrome"], "facts": [
                    {"collection": "diagnoses", "event_type": "diagnosis", "record_path": "diagnoses[0]", "field_path": "diagnoses[0].name", "time": "2026-04-10", "fields": {"subject_role": "patient", "name": "新生儿呼吸窘迫综合征"}},
                    {"collection": "reports", "event_type": "report", "record_path": "reports[0]", "field_path": "reports[0].impression", "time": "2026-05-05", "fields": {"subject_role": "patient", "impression": "future secret"}},
                ],
            },
            "english_terms": ["respiratory distress syndrome"],
            "mesh_terms": ["Respiratory Distress Syndrome, Newborn"], "top_k": 4,
        }))
        self.assertTrue(package.patient_hits)
        self.assertIn("pubmed", package.evidence_groups)
        self.assertIn("guideline", package.evidence_groups)
        self.assertEqual(package.trace["patient_context"]["received"], True)
        self.assertEqual(package.trace["patient_context"]["suppressed_future"], 1)
        self.assertEqual(package.source_separation["status"], "grouped")
        self.assertTrue(package.applicability)

    def test_explicit_pubmed_source_does_not_add_guideline(self):
        package = self.pipeline.retrieve(RetrievalRequest.from_dict({
            "question": "指南推荐", "source_kinds": ["pubmed"], "top_k": 4,
        }))
        self.assertTrue(all(item.document.kind == "pubmed" for item in package.ranked_documents))
        self.assertNotIn("guideline", package.evidence_groups)

    def test_raw_fact_text_not_in_external_terms_trace(self):
        package = self.pipeline.retrieve(RetrievalRequest.from_dict({
            "question": "新生儿肺炎", "patient_snapshot": {
                "patient_ref": "syn", "as_of": "2026-01-01",
                "reviewed_external_terms": ["neonatal pneumonia"],
                "facts": [{"collection": "notes", "event_type": "note", "record_path": "notes[0]", "field_path": "notes[0].text", "fields": {"text": "secret raw narrative"}}],
            }, "top_k": 2,
        }))
        self.assertNotIn("secret", " ".join(package.trace["external_terms"]))

    def test_maternal_fact_is_local_only(self):
        """母体记录只参与本地患者事实检索，绝不进入外发术语。"""
        package = self.pipeline.retrieve(RetrievalRequest.from_dict({
            "question": "maternal group B streptococcus colonisation: does this newborn need early-onset sepsis evaluation?", "as_of": "2026-09-12",
            "patient_snapshot": {
                "patient_ref": "syn-neo-002", "as_of": "2026-09-12",
                "reviewed_external_terms": ["early-onset sepsis"],
                "facts": [{"collection": "maternal", "event_type": "pregnancy", "record_path": "maternal[0]",
                           "field_path": "maternal[0].gbs_status", "time": "2026-09-10",
                           "fields": {"subject_role": "mother", "gbs_status": "positive"}}],
            },
            "english_terms": ["early-onset sepsis"], "top_k": 2,
        }))
        joined = " ".join(package.trace["external_terms"])
        self.assertNotIn("maternal[0]", joined)
        self.assertNotIn("mother", joined)
        self.assertTrue(any(hit.record_path == "maternal[0]" for hit in package.patient_hits))

    def test_snapshot_fixture_uses_neonatal_ids(self):
        self.assertEqual(set(SYNTHETIC_HIS_SNAPSHOTS), {f"syn-neo-00{i}" for i in range(1, 7)})
