import unittest

from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_HIS_SNAPSHOTS, SYNTHETIC_PUBMED
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
            "question": "高血压指南推荐和最新证据？", "as_of": "2025-12-31",
            "patient_snapshot": {
                "patient_ref": "syn-patient-001", "as_of": "2025-12-31", "source": "synthetic_chart",
                "reviewed_external_terms": ["hypertension"], "facts": [
                    {"collection": "diagnoses", "event_type": "diagnosis", "record_path": "diagnoses[0]", "field_path": "diagnoses[0].name", "time": "2024-02-01", "fields": {"name": "高血压"}},
                    {"collection": "reports", "event_type": "report", "record_path": "reports[0]", "field_path": "reports[0].impression", "time": "2026-01-05", "fields": {"impression": "future secret"}},
                ],
            },
            "english_terms": ["hypertension"], "mesh_terms": ["Hypertension"], "top_k": 4,
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
            "question": "高血压", "patient_snapshot": {
                "patient_ref": "syn", "as_of": "2025-01-01", "reviewed_external_terms": ["hypertension"],
                "facts": [{"collection": "notes", "event_type": "note", "record_path": "notes[0]", "field_path": "notes[0].text", "fields": {"text": "secret raw narrative"}}],
            }, "top_k": 2,
        }))
        self.assertNotIn("secret", " ".join(package.trace["external_terms"]))
