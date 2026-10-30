"""Audit tests: structural audit by default, optional injected verifier."""

from __future__ import annotations

import unittest

from rag.agent import EvidenceRAG
from rag.audit import GroundedAnswerAuditor
from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED
from rag.pipeline import EvidenceRetrievalPipeline


def _pipeline() -> EvidenceRetrievalPipeline:
    registry = ConnectorRegistry()
    registry.register(VersionedGuidelineConnector("guideline-fixture", SYNTHETIC_GUIDELINES))
    registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
    return EvidenceRetrievalPipeline(registry)


class StubVerifier:
    name = "stub-verifier"
    available = True

    def verify(self, pairs):
        return [(claim.startswith("指南支持"), 0.99 if claim.startswith("指南支持") else 0.01) for claim, _ in pairs]


class TestGroundedAnswerAudit(unittest.TestCase):
    def _tool(self) -> EvidenceRAG:
        return EvidenceRAG(_pipeline(), auditor=GroundedAnswerAuditor(StubVerifier()))

    def test_alce_like_metrics_and_supported_claim(self) -> None:
        tool = self._tool()
        result = tool.retrieve_evidence({
            "question": "新生儿呼吸窘迫综合征治疗指南推荐什么？", "as_of": "2026-04-15",
            "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]},
        })
        citation_id = result["citations"][0]["citation_id"]
        audit = tool.audit_grounded_answer({"retrieval_id": result["retrieval_id"], "answer": f"指南支持该治疗[{citation_id}]。"})
        self.assertEqual(audit["claims"][0]["ragtruth_label"], "supported")
        self.assertEqual(audit["alce_like"]["citation_precision"], 1.0)

    def test_missing_and_cross_retrieval_citations_are_visible(self) -> None:
        tool = self._tool()
        result = tool.retrieve_evidence({
            "question": "新生儿呼吸窘迫综合征治疗指南推荐什么？", "as_of": "2026-04-15",
            "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]},
        })
        audit = tool.audit_grounded_answer({"retrieval_id": result["retrieval_id"], "answer": "这是一条没有引文的主张。\n另一条使用了错误引文[other-retrieval:cit-1]。"})
        self.assertEqual(audit["claims"][0]["ragtruth_label"], "citation_missing")
        self.assertEqual(audit["claims"][1]["ragtruth_label"], "invalid_citation")

    def test_default_auditor_is_structural_only(self) -> None:
        """No verifier is created by default; semantic support stays not_checked."""
        tool = EvidenceRAG(_pipeline())
        result = tool.retrieve_evidence({
            "question": "新生儿呼吸窘迫综合征治疗指南推荐什么？", "as_of": "2026-04-15",
            "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]},
        })
        citation_id = result["citations"][0]["citation_id"]
        audit = tool.audit_grounded_answer({"retrieval_id": result["retrieval_id"], "answer": f"可考虑该治疗[{citation_id}]。"})
        self.assertEqual(audit["verifier"]["name"], "none")
        self.assertFalse(audit["verifier"]["available"])
        self.assertEqual(audit["verifier"]["mode"], "structural_only")
        self.assertEqual(audit["claims"][0]["ragtruth_label"], "not_checked")
        self.assertIsNone(audit["alce_like"]["citation_precision"])
        self.assertEqual(audit["alce_like"]["citation_completeness"], 1.0)


if __name__ == "__main__":
    unittest.main()
