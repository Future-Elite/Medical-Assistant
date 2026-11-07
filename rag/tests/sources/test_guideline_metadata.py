import unittest

from rag.guidelines import (
    GuidelineMetadataConnector,
    LicensedGuidelineSnapshotConnector,
    NEONATAL_GUIDELINE_SOURCE_ID,
    describe_registry,
    neonatal_guideline_metadata_registry,
    neonatal_pneumonia_metadata,
    neonatal_rds_chinese_interpretation_metadata,
    neonatal_rds_metadata,
)
from rag.models import QueryPlan, SourceDocument


class GuidelineTests(unittest.TestCase):
    def setUp(self):
        self.plan = QueryPlan("guideline", ("guideline",), ("guideline",), (NEONATAL_GUIDELINE_SOURCE_ID,), {"source_kinds": ["guideline"]}, ())

    def test_registry_is_neonatal_only(self):
        registry = neonatal_guideline_metadata_registry()
        self.assertEqual(len(registry), 3)
        joined = " ".join(doc.title.lower() for doc in registry)
        self.assertIn("肺炎", joined)
        self.assertIn("respiratory distress syndrome", joined)
        self.assertIn("解读", joined)
        # v6.6.3 的成人 WHO 条目不得出现
        for adult in ("hypertension", "blood glucose", "diabetes"):
            self.assertNotIn(adult, joined)
        self.assertTrue(all(doc.kind == "guideline" for doc in registry))
        self.assertTrue(all(doc.metadata["content_mode"] == "metadata_only" for doc in registry))
        # 现行策略：元数据记录本身不带正文，但必须指向全文来源（Europe PMC / 调用方 PDF）
        for doc in registry:
            self.assertTrue(doc.metadata["full_text_allowed"])
            self.assertTrue(doc.metadata["full_text_sources"])

    def test_pneumonia_metadata_fields(self):
        doc = neonatal_pneumonia_metadata()
        self.assertEqual(doc.doi, "10.7499/j.issn.1008-8830.2412164")
        self.assertEqual(doc.pmid, "40962511")
        self.assertEqual(doc.metadata["stable_id"], "PMCID PMC12447934")
        self.assertEqual(doc.version_id, "2025")
        self.assertEqual(doc.metadata["language"], "zh")

    def test_rds_version_label_is_separate_from_publication_date(self):
        doc = neonatal_rds_metadata()
        self.assertEqual(doc.version_id, "2025")
        self.assertEqual(doc.published_at, "2026-03-09")
        self.assertEqual(doc.metadata["version_label"], "2025 update (7th edition)")
        self.assertEqual(doc.pmid, "41802129")
        self.assertEqual(doc.doi, "10.1159/000551062")

    def test_chinese_interpretation_is_secondary_and_not_official(self):
        doc = neonatal_rds_chinese_interpretation_metadata()
        self.assertEqual(doc.metadata["source_role"], "secondary_interpretation")
        self.assertFalse(doc.metadata["official"])
        self.assertNotEqual(doc.doi, neonatal_rds_metadata().doi)

    def test_metadata_connector_only_returns_metadata(self):
        doc = neonatal_pneumonia_metadata()
        result = GuidelineMetadataConnector(NEONATAL_GUIDELINE_SOURCE_ID, (doc,)).search(self.plan, limit=5)
        self.assertEqual(len(result.documents), 1)
        self.assertIn("元数据", result.warnings[0])

    def test_metadata_registry_describe_is_audit_friendly(self):
        rows = describe_registry(neonatal_guideline_metadata_registry())
        self.assertEqual({row["content_mode"] for row in rows}, {"metadata_only"})
        self.assertIn("pmid", rows[0])

    def test_unlicensed_snapshot_not_indexed(self):
        doc = SourceDocument(source_id="g", document_id="g1", kind="guideline", title="G", text="restricted", metadata={"content_mode": "metadata_only", "full_text_allowed": False})
        result = LicensedGuidelineSnapshotConnector("g", (doc,)).search(self.plan, limit=5)
        self.assertEqual(result.documents, ())
        self.assertTrue(result.warnings)

    def test_licensed_snapshot_is_searchable(self):
        doc = SourceDocument(source_id="g", document_id="g1", kind="guideline", title="G", text="licensed text", version_id="1", metadata={"content_mode": "licensed_full_text", "full_text_allowed": True, "rights_basis": "fixture-license"})
        result = LicensedGuidelineSnapshotConnector("g", (doc,)).search(self.plan, limit=5)
        self.assertEqual(len(result.documents), 1)

    def test_metadata_connector_filters_unrelated_topic(self):
        connector = GuidelineMetadataConnector(
            NEONATAL_GUIDELINE_SOURCE_ID, neonatal_guideline_metadata_registry()
        )
        rds_plan = QueryPlan(
            "q", ("q",), (), (NEONATAL_GUIDELINE_SOURCE_ID,), {"source_kinds": ["guideline"]}, (),
            english_terms=("respiratory distress syndrome", "pulmonary surfactant"),
        )
        rds_docs = connector.search(rds_plan, limit=5).documents
        self.assertFalse(any("肺炎" in doc.title for doc in rds_docs))

        pneumonia_plan = QueryPlan(
            "q", ("q",), (), (NEONATAL_GUIDELINE_SOURCE_ID,), {"source_kinds": ["guideline"]}, (),
            english_terms=("neonatal pneumonia",),
        )
        pneumonia_docs = connector.search(pneumonia_plan, limit=5).documents
        self.assertTrue(any("肺炎" in doc.title for doc in pneumonia_docs))
