"""GUI/server contracts; these tests never send a live PubMed or LLM request."""

from __future__ import annotations

import unittest
from pathlib import Path

from v5.agent import V5RAG
from v5.server import V5RequestHandler, create_live_tool


class TestGuiContracts(unittest.TestCase):
    def test_live_tool_registers_only_the_live_pubmed_connector(self) -> None:
        tool = create_live_tool()
        self.assertEqual(
            tool.pipeline.registry.describe(),
            [{"source_id": "pubmed-live", "kinds": ["pubmed"]}],
        )

    def test_handler_exposes_expected_paths(self) -> None:
        self.assertTrue(hasattr(V5RequestHandler, "do_GET"))
        self.assertTrue(hasattr(V5RequestHandler, "do_POST"))
        self.assertIn("audit_grounded_answer", {item["name"] for item in V5RAG.tool_specifications()})

    def test_audit_controls_are_present_in_the_gui(self) -> None:
        page = (Path(__file__).resolve().parents[1] / "demo.html").read_text(encoding="utf-8")
        self.assertIn('id="auditPanel"', page)
        self.assertIn('id="auditAnswer"', page)
        self.assertIn("'/api/audit'", page)
        self.assertIn("exception_type", page)
        self.assertIn("error_id", page)
        self.assertIn("真实错误详情", page)
        self.assertIn("key_evidence_sentences", page)
        self.assertIn("state.selectedIndex !== null", page)

if __name__ == "__main__":
    unittest.main()
