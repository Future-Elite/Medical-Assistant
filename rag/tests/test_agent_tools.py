"""Agent JSON boundary and citation ownership tests; not executed during handoff."""

from __future__ import annotations

import unittest

from v5.agent import V5RAG
from v5.tests.test_pipeline import _pipeline


class TestAgentTools(unittest.TestCase):
    def test_retrieve_then_open_its_own_citation(self) -> None:
        tool = V5RAG(_pipeline())
        result = tool.retrieve_evidence({"question": "高血压治疗指南推荐什么？", "as_of": "2025-01-01"})
        opened = tool.open_citation({"retrieval_id": result["retrieval_id"], "citation_id": result["citations"][0]["citation_id"]})
        self.assertIn("查看原文证据", opened["citation"]["actions"][0]["label"])
        self.assertTrue(opened["citation"]["key_evidence_sentences"])
        self.assertIn("start", opened["citation"]["key_evidence_sentences"][0])

    def test_citation_cannot_cross_retrieval_boundaries(self) -> None:
        tool = V5RAG(_pipeline())
        first = tool.retrieve_evidence({"question": "高血压指南", "as_of": "2025-01-01"})
        second = tool.retrieve_evidence({"question": "Hypertension", "as_of": "2025-01-01"})
        with self.assertRaises(ValueError):
            tool.open_citation({"retrieval_id": second["retrieval_id"], "citation_id": first["citations"][0]["citation_id"]})

    def test_tool_schema_has_both_tools(self) -> None:
        self.assertEqual(
            {item["name"] for item in V5RAG.tool_specifications()},
            {"retrieve_evidence", "open_citation", "audit_grounded_answer"},
        )
