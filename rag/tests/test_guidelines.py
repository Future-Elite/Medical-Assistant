import unittest

from rag.guidelines import (
    GuidelineMetadataConnector,
    LicensedGuidelineSnapshotConnector,
    who_diabetes_glucose_metadata,
    who_guideline_metadata_registry,
    who_hypertension_metadata,
)
from rag.models import QueryPlan, SourceDocument


class GuidelineTests(unittest.TestCase):
    def setUp(self):
        self.plan = QueryPlan("guideline", ("guideline",), ("guideline",), ("who-guideline-live",), {"source_kinds": ["guideline"]}, ())

    def test_who_metadata_is_not_full_text(self):
        doc = who_hypertension_metadata()
        self.assertEqual(doc.metadata["content_mode"], "metadata_only")
        result = GuidelineMetadataConnector("who-guideline-live", (doc,)).search(self.plan, limit=5)
        self.assertEqual(len(result.documents), 1)
        self.assertIn("元数据", result.warnings[0])

    def test_who_diabetes_metadata_fields(self):
        doc = who_diabetes_glucose_metadata()
        self.assertEqual(doc.kind, "guideline")
        self.assertEqual(doc.metadata["stable_id"], "ISBN 978-92-4-155028-4")
        self.assertEqual(doc.published_at, "2018-04-21")
        self.assertEqual(doc.url, "https://www.who.int/publications/i/item/9789241550284")
        self.assertEqual(doc.metadata["content_mode"], "metadata_only")
        self.assertFalse(doc.metadata["full_text_allowed"])

    def test_metadata_registry_has_both_conditions(self):
        registry = who_guideline_metadata_registry()
        self.assertEqual(len(registry), 2)
        joined = " ".join(doc.title.lower() for doc in registry)
        self.assertIn("hypertension", joined)
        self.assertIn("diabetes", joined)
        self.assertTrue(all(doc.kind == "guideline" for doc in registry))

    def test_unlicensed_snapshot_not_indexed(self):
        doc = SourceDocument(source_id="g", document_id="g1", kind="guideline", title="G", text="restricted", metadata={"content_mode": "metadata_only", "full_text_allowed": False})
        result = LicensedGuidelineSnapshotConnector("g", (doc,)).search(self.plan, limit=5)
        self.assertEqual(result.documents, ())
        self.assertTrue(result.warnings)

    def test_licensed_snapshot_is_searchable(self):
        doc = SourceDocument(source_id="g", document_id="g1", kind="guideline", title="G", text="licensed text", version_id="1", metadata={"content_mode": "licensed_full_text", "full_text_allowed": True, "rights_basis": "fixture-license"})
        result = LicensedGuidelineSnapshotConnector("g", (doc,)).search(self.plan, limit=5)
        self.assertEqual(len(result.documents), 1)
