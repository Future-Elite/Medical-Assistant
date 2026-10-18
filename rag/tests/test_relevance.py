"""Query-relevance gate: a source document must not be returned merely because
it exists in a registered source. Guards against cross-disease leakage such as
"jaundice question -> pneumonia guideline" and adult-guideline contamination.
"""
import unittest

from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED
from rag.guidelines import GuidelineMetadataConnector, neonatal_pneumonia_metadata
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline


def build_pipeline() -> EvidenceRetrievalPipeline:
    registry = ConnectorRegistry()
    registry.register(VersionedGuidelineConnector("guideline-fixture", SYNTHETIC_GUIDELINES))
    registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
    return EvidenceRetrievalPipeline(registry)


PNEUMONIA_REQUEST = {
    "question": "该患儿新生儿感染性肺炎的影像检查有哪些指南推荐和近期研究证据？",
    "as_of": "2026-09-12",
    "patient_terms": ["新生儿感染性肺炎"],
    "user_terms": {"新生儿感染性肺炎": ["neonatal pneumonia"]},
    "english_terms": ["neonatal pneumonia", "chest radiograph"],
    "mesh_terms": ["Pneumonia, Infant, Newborn"],
    "top_k": 6,
}


class RelevanceGateTests(unittest.TestCase):
    def test_pneumonia_query_does_not_return_rds_guideline(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict(PNEUMONIA_REQUEST))
        titles = [item.document.title.lower() for item in package.ranked_documents]
        self.assertTrue(any("pneumonia" in title for title in titles))
        self.assertFalse(any("respiratory distress syndrome" in title for title in titles))
        self.assertTrue(any("无检索相关度" in warning for warning in package.warnings))

    def test_pneumonia_guideline_is_returned_with_version_and_recommendation(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict(PNEUMONIA_REQUEST))
        guide = next(item for item in package.ranked_documents if item.document.kind == "guideline")
        self.assertEqual(guide.document.document_id, "guide-pna-2025")
        citation = next(citation for citation in package.citations if citation.kind == "guideline")
        self.assertEqual(citation.version_id, "2025")
        self.assertEqual(citation.recommendation_id, "10")
        self.assertTrue(citation.key_evidence_sentences)

    def test_metadata_only_guideline_does_not_backfill_unrelated_query(self):
        registry = ConnectorRegistry()
        registry.register(GuidelineMetadataConnector("neonatal-guideline-live", (neonatal_pneumonia_metadata(),)))
        package = EvidenceRetrievalPipeline(registry).retrieve(RetrievalRequest.from_dict({
            "question": "surfactant indication for respiratory distress syndrome",
            "as_of": "2026-04-15",
            "english_terms": ["respiratory distress syndrome", "pulmonary surfactant"], "top_k": 4,
        }))
        self.assertEqual(package.ranked_documents, ())
        self.assertEqual(package.citations, ())
        self.assertTrue(any("无检索相关度" in warning for warning in package.warnings))

    def test_irrelevant_pubmed_candidates_are_excluded_too(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict(PNEUMONIA_REQUEST))
        pmids = {citation.pmid for citation in package.citations if citation.kind == "pubmed"}
        self.assertIn("2003", pmids)
        self.assertNotIn("2001", pmids)
        self.assertNotIn("2004", pmids)

    def test_matching_query_still_returns_evidence(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({
            "question": "新生儿呼吸窘迫综合征的指南推荐和最新证据？", "as_of": "2026-04-15",
            "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]},
            "english_terms": ["respiratory distress syndrome"],
            "mesh_terms": ["Respiratory Distress Syndrome, Newborn"], "top_k": 6,
        }))
        kinds = {citation.kind for citation in package.citations}
        self.assertIn("guideline", kinds)
        self.assertIn("pubmed", kinds)


RDS_REQUEST = {
    "question": "该患儿新生儿呼吸窘迫综合征的肺表面活性物质和呼吸支持推荐是什么？",
    "as_of": "2026-04-15",
    "patient_terms": ["新生儿呼吸窘迫综合征", "肺表面活性物质"],
    "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]},
    "english_terms": ["respiratory distress syndrome", "pulmonary surfactant"],
    "mesh_terms": ["Respiratory Distress Syndrome, Newborn", "Pulmonary Surfactants"],
    "top_k": 6,
}

JAUNDICE_REQUEST = {
    "question": "该患儿生后72小时总胆红素285 μmol/L，新生儿黄疸的光疗指征是什么？",
    "as_of": "2026-07-18",
    "patient_terms": ["高胆红素血症", "光疗"],
    "user_terms": {"新生儿黄疸": ["neonatal jaundice"], "光疗": ["phototherapy"]},
    "english_terms": ["neonatal jaundice", "hyperbilirubinemia", "phototherapy"],
    "mesh_terms": ["Jaundice, Neonatal"],
    "top_k": 6,
}

TTN_REQUEST = {
    "question": "该患儿暂时性呼吸增快的鉴别和处理证据是什么？",
    "as_of": "2026-08-20",
    "patient_terms": ["暂时性呼吸增快"],
    "user_terms": {"暂时性呼吸增快": ["transient tachypnea of the newborn"]},
    "english_terms": ["transient tachypnea of the newborn", "TTN"],
    "mesh_terms": ["Transient Tachypnea of the Newborn"],
    "top_k": 6,
}


class NeonatalPresetTests(unittest.TestCase):
    def test_rds_query_returns_rds_evidence_and_support_fact(self):
        from rag.tests.fixtures import SYNTHETIC_HIS_SNAPSHOTS
        from rag.his import InMemoryHISAdapter

        snapshot = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS).get_snapshot("syn-neo-001", "2026-04-15")
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**RDS_REQUEST, "patient_snapshot": snapshot}))
        doc_ids = {item.document.document_id for item in package.ranked_documents}
        self.assertIn("guide-rds-2025", doc_ids)
        self.assertIn("pmid-2001", doc_ids)
        self.assertTrue(any(hit.record_path == "respiratory_support[0]" for hit in package.patient_hits))
        self.assertEqual(package.patient_context["suppressed_future"], 1)

    def test_jaundice_query_does_not_pull_pneumonia(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict(JAUNDICE_REQUEST))
        doc_ids = {item.document.document_id for item in package.ranked_documents}
        self.assertIn("guide-jnd-2025", doc_ids)
        self.assertNotIn("guide-pna-2025", doc_ids)

    def test_ttn_query_routes_to_ttn_evidence(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict(TTN_REQUEST))
        doc_ids = {item.document.document_id for item in package.ranked_documents}
        self.assertIn("guide-ttn-2025", doc_ids)
        guide = next(citation for citation in package.citations if citation.kind == "guideline")
        self.assertEqual(guide.version_id, "2025")
        self.assertEqual(guide.recommendation_id, "1.1")

    def test_cjk_query_matches_chinese_guideline(self):
        """中文问题通过 CJK 二元组直接命中中文来源（无英文术语）。"""
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({
            "question": "该患儿黄疸的光疗阈值和胆红素监测建议是什么？",
            "as_of": "2026-07-18", "patient_terms": ["黄疸"], "top_k": 6,
        }))
        doc_ids = {item.document.document_id for item in package.ranked_documents}
        self.assertIn("guide-jnd-2025", doc_ids)
        self.assertEqual(package.query_plan.question_english_terms, ())

    def test_english_query_does_not_pull_chinese_guideline(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({
            "question": "neonatal respiratory distress syndrome surfactant recommendation",
            "as_of": "2026-04-15", "english_terms": ["respiratory distress syndrome"],
            "top_k": 8,
        }))
        doc_ids = {item.document.document_id for item in package.ranked_documents}
        self.assertNotIn("guide-jnd-2025", doc_ids)


if __name__ == "__main__":
    unittest.main()
