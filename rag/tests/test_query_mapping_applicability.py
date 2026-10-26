"""Release-line tests for reviewed term mapping and structured applicability."""
from __future__ import annotations

import unittest

from rag.applicability import assess_applicability
from rag.connectors import ConnectorRegistry, InMemoryConnector
from rag.models import RetrievalRequest, SourceDocument, parse_term_mapping
from rag.pipeline import EvidenceRetrievalPipeline
from rag.pubmed import _safe_term
from rag.query import build_query_plan
from rag.tests.fixtures import SYNTHETIC_HIS_SNAPSHOTS

SOURCES = [
    {"source_id": "guideline-fixture", "kinds": ["guideline"]},
    {"source_id": "pubmed-fixture", "kinds": ["pubmed"]},
]


class TermMappingTests(unittest.TestCase):
    def test_mapping_parse_and_expansion(self):
        entries = parse_term_mapping([
            {"concept": "新生儿呼吸窘迫综合征", "aliases": ["NRDS"],
             "english": ["respiratory distress syndrome"],
             "mesh": ["Respiratory Distress Syndrome, Newborn"]},
            {"concept": "支气管肺发育不良", "english": ["bronchopulmonary dysplasia"]},
        ])
        self.assertEqual(entries[0].aliases, ("NRDS",))
        request = RetrievalRequest.from_dict({
            "question": "该NRDS患儿是否需要肺表面活性物质？",
            "term_mapping": [
                {"concept": "新生儿呼吸窘迫综合征", "aliases": ["NRDS"],
                 "english": ["respiratory distress syndrome"],
                 "mesh": ["Respiratory Distress Syndrome, Newborn"]},
                {"concept": "支气管肺发育不良", "english": ["bronchopulmonary dysplasia"]},
            ],
            "patient_terms": ["新生儿呼吸窘迫综合征"],
        })
        plan = build_query_plan(request, SOURCES)
        statuses = {entry["concept"]: entry["status"] for entry in plan.mapping_trace}
        self.assertEqual(statuses["新生儿呼吸窘迫综合征"], "expanded")
        self.assertEqual(statuses["支气管肺发育不良"], "not_matched")
        self.assertIn("respiratory distress syndrome", plan.english_terms)
        self.assertNotIn("bronchopulmonary dysplasia", plan.english_terms)
        self.assertIn("respiratory distress syndrome", plan.variants)
        self.assertIn("Respiratory Distress Syndrome, Newborn", plan.variants)

    def test_mapping_gating_changes_retrieval(self):
        rds = SourceDocument(
            "g", "rds-doc", "guideline", "Respiratory distress syndrome guideline",
            "Respiratory distress syndrome in preterm infants: surfactant and CPAP recommendations.",
            metadata={"mesh_terms": "Respiratory Distress Syndrome, Newborn"},
        )
        bpd = SourceDocument(
            "g", "bpd-doc", "guideline", "Bronchopulmonary dysplasia guideline",
            "Bronchopulmonary dysplasia definition and oxygen dependence in survivors.",
            metadata={"mesh_terms": "Bronchopulmonary Dysplasia"},
        )
        registry = ConnectorRegistry()
        registry.register(InMemoryConnector("g", ("guideline",), (rds, bpd)))
        pipeline = EvidenceRetrievalPipeline(registry)
        common = {
            "question": "该患儿新生儿呼吸窘迫综合征的肺表面活性物质指征是什么？",
            "patient_terms": ["新生儿呼吸窘迫综合征"],
            "source_kinds": ["guideline"], "top_k": 2, "source_mode": "custom",
        }
        mapping = pipeline.retrieve(RetrievalRequest.from_dict({
            **common,
            "term_mapping": [
                {"concept": "新生儿呼吸窘迫综合征", "english": ["respiratory distress syndrome"],
                 "mesh": ["Respiratory Distress Syndrome, Newborn"]},
                {"concept": "支气管肺发育不良", "english": ["bronchopulmonary dysplasia"],
                 "mesh": ["Bronchopulmonary Dysplasia"]},
            ],
        }))
        legacy = pipeline.retrieve(RetrievalRequest.from_dict({
            **common,
            "english_terms": ["respiratory distress syndrome", "bronchopulmonary dysplasia"],
            "mesh_terms": ["Respiratory Distress Syndrome, Newborn", "Bronchopulmonary Dysplasia"],
        }))
        self.assertEqual([item.document.document_id for item in mapping.ranked_documents], ["rds-doc"])
        self.assertIn("bpd-doc", [item.document.document_id for item in legacy.ranked_documents])

    def test_mapping_pubmed_query_uses_field_tags(self):
        request = RetrievalRequest.from_dict({
            "question": "新生儿呼吸窘迫综合征",
            "term_mapping": [{"concept": "新生儿呼吸窘迫综合征",
                              "english": ["respiratory distress syndrome"],
                              "mesh": ["Respiratory Distress Syndrome, Newborn"]}],
        })
        query = _safe_term(build_query_plan(request, SOURCES))
        self.assertIn('"respiratory distress syndrome"[tiab]', query)
        self.assertIn('"Respiratory Distress Syndrome, Newborn"[MeSH Terms]', query)


class ApplicabilityTests(unittest.TestCase):
    def _document(self, criteria):
        return SourceDocument("test", "guide", "guideline", "Guide", "Text",
                              metadata={"applicability_criteria": criteria})

    def test_structured_match_mismatch_unknown(self):
        snapshot = SYNTHETIC_HIS_SNAPSHOTS["syn-neo-001"]
        document = self._document([
            {"criterion_id": "dx", "field": "diagnosis", "operator": "contains_any",
             "value": ["呼吸窘迫综合征"], "review_status": "reviewed",
             "source": {"locator": "标题", "quote": "RDS"}},
            {"criterion_id": "ga", "field": "ga_days", "operator": "lt", "value": 259,
             "unit": "days", "review_status": "reviewed",
             "source": {"locator": "适用人群", "quote": "preterm"}},
            {"criterion_id": "culture", "field": "culture_status", "operator": "in",
             "value": ["negative"], "review_status": "reviewed",
             "source": {"locator": "推荐", "quote": "negative"}},
            {"criterion_id": "ga_fail", "field": "ga_days", "operator": "lt", "value": 200,
             "review_status": "reviewed", "source": {"locator": "测试", "quote": "x"}},
        ])
        assessment = assess_applicability(document, snapshot, ())
        self.assertEqual(assessment.status, "mismatch")
        statuses = {item.criterion_id: item.status for item in assessment.criteria}
        self.assertEqual(statuses["dx"], "match")
        self.assertEqual(statuses["ga"], "match")
        self.assertEqual(statuses["culture"], "unknown")
        self.assertEqual(statuses["ga_fail"], "mismatch")
        self.assertIn("birth[0]", assessment.criteria[1].patient_record_paths)

    def test_subject_role_and_review_gate(self):
        snapshot = SYNTHETIC_HIS_SNAPSHOTS["syn-neo-002"]
        document = self._document([
            {"criterion_id": "gbs", "field": "maternal.gbs_status", "operator": "in",
             "value": ["positive"], "subject_role": "mother", "review_status": "reviewed",
             "source": {"locator": "适用人群", "quote": "GBS"}},
            {"criterion_id": "draft", "field": "ga_days", "operator": "lt", "value": 259,
             "review_status": "scope_only", "source": {"locator": "测试", "quote": "x"}},
        ])
        assessment = assess_applicability(document, snapshot, ())
        self.assertEqual(assessment.criteria[0].status, "match")
        self.assertEqual(assessment.criteria[1].status, "unknown")
        self.assertIn("条件未审核", assessment.criteria[1].reason)


if __name__ == "__main__":
    unittest.main()
