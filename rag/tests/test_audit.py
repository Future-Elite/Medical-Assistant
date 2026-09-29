"""Audit tests with a deterministic verifier; no MiniCheck model or network is used."""

from __future__ import annotations

import unittest

from v5.agent import V5RAG
from v5.audit import GroundedAnswerAuditor
from v5.tests.test_pipeline import _pipeline


class StubVerifier:
    name = "stub-minicheck"
    available = True

    def verify(self, pairs):
        return [(claim.startswith("指南支持"), 0.99 if claim.startswith("指南支持") else 0.01) for claim, _ in pairs]


class TestGroundedAnswerAudit(unittest.TestCase):
    def _tool(self) -> V5RAG:
        return V5RAG(_pipeline(), auditor=GroundedAnswerAuditor(StubVerifier()))

    def test_alce_like_metrics_and_supported_claim(self) -> None:
        tool = self._tool()
        result = tool.retrieve_evidence({"question": "高血压治疗指南推荐什么？", "as_of": "2025-01-01"})
        citation_id = result["citations"][0]["citation_id"]
        audit = tool.audit_grounded_answer({"retrieval_id": result["retrieval_id"], "answer": f"指南支持该治疗[{citation_id}]。"})
        self.assertEqual(audit["claims"][0]["ragtruth_label"], "supported")
        self.assertEqual(audit["alce_like"]["citation_precision"], 1.0)

    def test_missing_and_cross_retrieval_citations_are_visible(self) -> None:
        tool = self._tool()
        result = tool.retrieve_evidence({"question": "高血压治疗指南推荐什么？", "as_of": "2025-01-01"})
        audit = tool.audit_grounded_answer({"retrieval_id": result["retrieval_id"], "answer": "这是一条没有引文的主张。\n另一条使用了错误引文[v5-other:cit-1]。"})
        self.assertEqual(audit["claims"][0]["ragtruth_label"], "citation_missing")
        self.assertEqual(audit["claims"][1]["ragtruth_label"], "invalid_citation")


if __name__ == "__main__":
    unittest.main()
