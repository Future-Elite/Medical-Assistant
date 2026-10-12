"""Audit tests with a deterministic verifier; no MiniCheck model or network is used."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from rag.agent import EvidenceRAG
from rag.audit import GroundedAnswerAuditor, normalize_citation_groups, parse_citation_ids
from rag.tests.test_pipeline import _pipeline


class StubVerifier:
    name = "stub-minicheck"
    available = True

    def verify(self, pairs):
        return [(claim.startswith("指南支持"), 0.99 if claim.startswith("指南支持") else 0.01) for claim, _ in pairs]


class TestGroundedAnswerAudit(unittest.TestCase):
    def _tool(self) -> EvidenceRAG:
        return EvidenceRAG(_pipeline(), auditor=GroundedAnswerAuditor(StubVerifier()))

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
        audit = tool.audit_grounded_answer({"retrieval_id": result["retrieval_id"], "answer": "这是一条没有引文的主张。\n另一条使用了错误引文[other-retrieval:cit-1]。"})
        self.assertEqual(audit["claims"][0]["ragtruth_label"], "citation_missing")
        self.assertEqual(audit["claims"][1]["ragtruth_label"], "invalid_citation")

    def test_abbreviated_multi_citation_is_expanded_to_complete_ids(self) -> None:
        text = "主张[retrieval-test:cit-6, cit-2]"
        self.assertEqual(
            parse_citation_ids(text),
            ("retrieval-test:cit-6", "retrieval-test:cit-2"),
        )
        self.assertEqual(
            normalize_citation_groups(text),
            "主张[retrieval-test:cit-6][retrieval-test:cit-2]",
        )

    def test_structured_claim_keeps_semicolon_and_reuses_verification(self) -> None:
        tool = self._tool()
        result = tool.retrieve_evidence({"question": "高血压治疗指南推荐什么？", "as_of": "2025-01-01"})
        citation_id = result["citations"][0]["citation_id"]
        verifier = tool.auditor.verifier
        verifier.verify = Mock(wraps=verifier.verify)
        text = "指南支持该治疗; P < 0.001."
        request = {
            "retrieval_id": result["retrieval_id"],
            "answer": "中文草稿",
            "claim_records": [{
                "claim_zh": "中文原文",
                "claim_en": text,
                "citation_ids": [citation_id],
            }],
        }
        audit = tool.audit_grounded_answer(request)
        self.assertEqual(len(audit["claims"]), 1)
        self.assertEqual(audit["claims"][0]["verifier_claim"], text)
        self.assertEqual(audit["claims"][0]["ragtruth_label"], "supported")
        tool.audit_grounded_answer(request)
        verifier.verify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
