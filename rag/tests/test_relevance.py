"""Query-relevance gate: a source document must not be returned merely because
it exists in a registered source. Guards against cross-disease leakage such as
"diabetes question -> hypertension guideline".
"""
import unittest

from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED
from rag.guidelines import GuidelineMetadataConnector, who_hypertension_metadata
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline


def build_pipeline() -> EvidenceRetrievalPipeline:
    registry = ConnectorRegistry()
    registry.register(VersionedGuidelineConnector("guideline-fixture", SYNTHETIC_GUIDELINES))
    registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
    return EvidenceRetrievalPipeline(registry)


DIABETES_REQUEST = {
    "question": "该患者2型糖尿病的初始降糖治疗有哪些指南推荐和近期研究证据？",
    "as_of": "2025-12-31",
    "patient_terms": ["2型糖尿病"],
    "user_terms": {"2型糖尿病": ["type 2 diabetes", "metformin"]},
    "english_terms": ["type 2 diabetes", "metformin"],
    "mesh_terms": ["Diabetes Mellitus, Type 2"],
    "top_k": 6,
}


class RelevanceGateTests(unittest.TestCase):
    def test_diabetes_query_does_not_return_hypertension_guideline(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict(DIABETES_REQUEST))
        titles = [item.document.title for item in package.ranked_documents]
        self.assertTrue(any("diabetes" in title.lower() for title in titles))
        self.assertFalse(any("hypertension" in title.lower() for title in titles))
        self.assertFalse(any("hypertension" in citation.title.lower() for citation in package.citations))
        self.assertTrue(any("无检索相关度" in warning for warning in package.warnings))

    def test_diabetes_guideline_is_returned_with_version_and_recommendation(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict(DIABETES_REQUEST))
        guide = next(item for item in package.ranked_documents if item.document.kind == "guideline")
        self.assertEqual(guide.document.document_id, "guide-dm-2025")
        citation = next(citation for citation in package.citations if citation.kind == "guideline")
        self.assertEqual(citation.version_id, "2025")
        self.assertEqual(citation.recommendation_id, "3.2")
        self.assertTrue(citation.key_evidence_sentences)

    def test_metadata_only_guideline_does_not_backfill_unrelated_query(self):
        registry = ConnectorRegistry()
        registry.register(GuidelineMetadataConnector("who-guideline-live", (who_hypertension_metadata(),)))
        package = EvidenceRetrievalPipeline(registry).retrieve(RetrievalRequest.from_dict(DIABETES_REQUEST))
        self.assertEqual(package.ranked_documents, ())
        self.assertEqual(package.citations, ())
        self.assertTrue(any("无检索相关度" in warning for warning in package.warnings))

    def test_irrelevant_pubmed_candidates_are_excluded_too(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict(DIABETES_REQUEST))
        pmids = {citation.pmid for citation in package.citations if citation.kind == "pubmed"}
        self.assertIn("1003", pmids)
        self.assertNotIn("1001", pmids)
        self.assertNotIn("1002", pmids)

    def test_matching_query_still_returns_evidence(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({
            "question": "高血压指南推荐和最新证据？", "as_of": "2025-12-31",
            "user_terms": {"高血压": ["hypertension"]}, "english_terms": ["hypertension"],
            "mesh_terms": ["Hypertension"], "top_k": 6,
        }))
        kinds = {citation.kind for citation in package.citations}
        self.assertIn("guideline", kinds)
        self.assertIn("pubmed", kinds)


INFANT_JAUNDICE_REQUEST = {
    "question": "该患儿新生儿黄疸的胆红素阈值和光疗指征有哪些推荐和近期证据？",
    "as_of": "2025-12-31",
    "patient_terms": ["新生儿黄疸"],
    "user_terms": {"新生儿黄疸": ["neonatal jaundice", "hyperbilirubinemia", "phototherapy"]},
    "english_terms": ["neonatal jaundice", "hyperbilirubinemia", "phototherapy"],
    "mesh_terms": ["Jaundice, Neonatal"],
    "top_k": 6,
}

INFANT_FEVER_REQUEST = {
    "question": "2月龄婴儿发热的评估推荐和近期证据是什么？",
    "as_of": "2025-12-31",
    "patient_terms": ["婴儿发热"],
    "user_terms": {"婴儿发热": ["fever evaluation"]},
    "english_terms": ["fever evaluation"],
    "mesh_terms": ["Fever"],
    "top_k": 6,
}

VACCINE_REQUEST = {
    "question": "该婴儿的常规疫苗接种推荐和不良反应证据是什么？",
    "as_of": "2025-12-31",
    "patient_terms": ["疫苗"],
    "user_terms": {"疫苗": ["vaccination", "immunization"]},
    "english_terms": ["vaccination", "immunization"],
    "mesh_terms": ["Immunization Schedule"],
    "top_k": 6,
}

DRUG_SAFETY_REQUEST = {
    "question": "该患者服用氨氯地平后出现的周围性水肿，有哪些相关研究证据？",
    "as_of": "2025-12-31",
    "patient_terms": ["氨氯地平"],
    "user_terms": {"氨氯地平": ["amlodipine", "peripheral edema"]},
    "english_terms": ["amlodipine", "peripheral edema"],
    "mesh_terms": ["Amlodipine"],
    "top_k": 6,
}


class InfantAndDrugSafetyPresetTests(unittest.TestCase):
    def test_jaundice_query_returns_jaundice_evidence_and_patient_lab(self):
        from rag.tests.fixtures import SYNTHETIC_HIS_SNAPSHOTS
        from rag.his import InMemoryHISAdapter

        snapshot = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS).get_snapshot("syn-patient-003", "2025-12-31")
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**INFANT_JAUNDICE_REQUEST, "patient_snapshot": snapshot}))
        doc_ids = {item.document.document_id for item in package.ranked_documents}
        self.assertEqual(doc_ids, {"guide-nnj-2025", "pmid-1004"})
        self.assertTrue(any(hit.record_path == "labs[0]" for hit in package.patient_hits))
        self.assertEqual(package.patient_context["suppressed_future"], 1)

    def test_infant_fever_query_does_not_pull_vaccine_or_jaundice(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict(INFANT_FEVER_REQUEST))
        doc_ids = {item.document.document_id for item in package.ranked_documents}
        self.assertEqual(doc_ids, {"guide-fever-2024", "pmid-1005"})

    def test_vaccine_query_routes_to_immunization_evidence(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict(VACCINE_REQUEST))
        doc_ids = {item.document.document_id for item in package.ranked_documents}
        self.assertEqual(doc_ids, {"guide-vax-2025", "pmid-1006"})
        guide = next(citation for citation in package.citations if citation.kind == "guideline")
        self.assertEqual(guide.version_id, "2025")
        self.assertEqual(guide.recommendation_id, "1.2")

    def test_drug_safety_query_returns_amlodipine_evidence_only(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict(DRUG_SAFETY_REQUEST))
        doc_ids = {item.document.document_id for item in package.ranked_documents}
        self.assertEqual(doc_ids, {"pmid-1007"})
        self.assertFalse(any(citation.kind == "guideline" for citation in package.citations))


CN_ASTHMA_REQUEST = {
    "question": "该患儿支气管哮喘的长期管理建议和依据是什么？",
    "as_of": "2025-12-31",
    "patient_terms": ["哮喘"],
    "top_k": 6,
}


class ChineseQuestionParticipationTests(unittest.TestCase):
    """中文问题直接参与检索：CJK 二元组匹配中文来源，无需英文术语。"""

    def test_chinese_question_matches_chinese_guideline_and_patient_fact(self):
        from rag.tests.fixtures import SYNTHETIC_HIS_SNAPSHOTS
        from rag.his import InMemoryHISAdapter

        snapshot = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS).get_snapshot("syn-patient-007", "2025-12-31")
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**CN_ASTHMA_REQUEST, "patient_snapshot": snapshot}))
        doc_ids = {item.document.document_id for item in package.ranked_documents}
        self.assertEqual(doc_ids, {"guide-cn-asthma-2025"})
        self.assertTrue(any(hit.record_path == "diagnoses[0]" for hit in package.patient_hits))
        citation = package.citations[0]
        self.assertEqual(citation.version_id, "2025")
        self.assertEqual(citation.recommendation_id, "1.1")
        self.assertTrue(citation.key_evidence_sentences)
        self.assertEqual(package.query_plan.question_english_terms, ())

    def test_chinese_question_does_not_pull_english_guidelines(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict(CN_ASTHMA_REQUEST))
        for item in package.ranked_documents:
            self.assertEqual(item.document.document_id, "guide-cn-asthma-2025")

    def test_english_guideline_queries_do_not_pull_chinese_guideline(self):
        # 反向：英文/其他病种的问题不应串到中文文档
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({
            "question": "该患者高血压的指南推荐和最新证据是什么？", "as_of": "2025-12-31",
            "english_terms": ["hypertension"], "mesh_terms": ["Hypertension"], "top_k": 8,
        }))
        doc_ids = {item.document.document_id for item in package.ranked_documents}
        self.assertNotIn("guide-cn-asthma-2025", doc_ids)


if __name__ == "__main__":
    unittest.main()
