import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from interface import ChatInterface
from medrax.answer_audit import AnswerAuditModule, QwenClaimTranslator


SUPPORTED = {
    "verifier": {"name": "stub-minicheck", "available": True},
    "claims": [{
        "claim": "有效主张[retrieval-test:cit-1]。",
        "valid_citation_ids": ["retrieval-test:cit-1"],
        "invalid_citation_ids": [],
        "ragtruth_label": "supported",
    }],
}


class AnswerAuditModuleTest(unittest.TestCase):
    def test_supported_draft_passes_and_renders_public_source_id(self):
        module = AnswerAuditModule(
            lambda retrieval_id, answer, **kwargs: SUPPORTED,
            lambda claims: claims,
        )

        result = module.audit("有效主张[retrieval-test:cit-1]。", "retrieval-test")
        rendered = module.render_public_citations(
            "有效主张[retrieval-test:cit-1]。",
            {"retrieval-test:cit-1": "PMID:12345678"},
        )

        self.assertTrue(result["passed"])
        self.assertEqual(rendered, "有效主张（PMID:12345678）。")
        self.assertEqual(module.max_revisions, 2)

    def test_unavailable_or_unsupported_audit_fails_closed(self):
        unavailable = AnswerAuditModule(
            lambda retrieval_id, answer, **kwargs: {
                "verifier": {"name": "minicheck", "available": False},
                "claims": SUPPORTED["claims"],
            },
            lambda claims: claims,
        )
        unsupported = AnswerAuditModule(
            lambda retrieval_id, answer, **kwargs: {
                "verifier": {"name": "stub-minicheck", "available": True},
                "claims": [{
                    **SUPPORTED["claims"][0],
                    "ragtruth_label": "evident_baseless_info",
                }],
            },
            lambda claims: claims,
        )

        self.assertFalse(unavailable.audit("draft", "retrieval-test")["passed"])
        self.assertFalse(unsupported.audit("draft", "retrieval-test")["passed"])

    def test_translates_claim_and_expands_abbreviated_multi_citation(self):
        captured = {}

        def audit(retrieval_id, answer, claim_records=None):
            captured["records"] = claim_records
            return {
                "verifier": {"name": "stub-minicheck", "available": True},
                "claims": [{
                    "claim": answer,
                    "valid_citation_ids": [
                        "retrieval-test:cit-6",
                        "retrieval-test:cit-2",
                    ],
                    "invalid_citation_ids": [],
                    "ragtruth_label": "supported",
                }],
            }

        module = AnswerAuditModule(audit, lambda claims: ["English claim"])
        result = module.audit(
            "中文主张[retrieval-test:cit-6, cit-2]。",
            "retrieval-test",
        )

        self.assertTrue(result["passed"])
        self.assertEqual(
            captured["records"][0]["citation_ids"],
            ["retrieval-test:cit-6", "retrieval-test:cit-2"],
        )
        self.assertEqual(result["claim_translations"][0]["claim_en"], "English claim")

    def test_english_skips_model_and_chinese_translation_is_cached(self):
        translator = QwenClaimTranslator()
        translator._translate_batch = Mock(return_value=["Blood glucose decreases."])
        english = "Blood glucose decreases; P < 0.001."
        self.assertEqual(translator([english]), [english])
        translator._translate_batch.assert_not_called()
        self.assertEqual(
            translator([english, "血糖下降。"]),
            [english, "Blood glucose decreases."],
        )
        translator(["血糖下降。"])
        translator._translate_batch.assert_called_once_with(["血糖下降。"])

    def test_missing_audit_rows_fail_closed(self):
        module = AnswerAuditModule(
            lambda *args, **kwargs: {"claims": []}, lambda claims: claims,
        )
        result = module.audit("Claim[retrieval-test:cit-1]", "retrieval-test")
        self.assertFalse(result["passed"])
        self.assertIn("count", result["reason"])

    def test_interface_revises_twice_then_blocks_failed_draft(self):
        class Tool:
            name = "retrieve_medical_evidence"

            @staticmethod
            def translate_claims(claims):
                return claims

            @staticmethod
            def audit_grounded_answer(retrieval_id, answer, claim_records=None):
                return {
                    "verifier": {"name": "stub-minicheck", "available": True},
                    "claims": [{
                        "claim": answer,
                        "valid_citation_ids": ["retrieval-test:cit-1"],
                        "invalid_citation_ids": [],
                        "ragtruth_label": "evident_baseless_info",
                    }],
                }

        class Workflow:
            calls = 0

            def stream(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    yield {"model": {"messages": [SimpleNamespace(
                        content="",
                        tool_calls=[{
                            "id": "call-1",
                            "name": "retrieve_medical_evidence",
                            "args": {"question": "test"},
                        }],
                    )]}}
                    yield {"tools": {"messages": [SimpleNamespace(
                        name="retrieve_medical_evidence",
                        tool_call_id="call-1",
                        content="evidence",
                        artifact={
                            "query": "test",
                            "retrieval_id": "retrieval-test",
                            "evidence": [{
                                "source_id": "PMID:12345678",
                                "source_type": "pubmed",
                                "metadata": {"citation_id": "retrieval-test:cit-1"},
                            }],
                            "trace": {},
                        },
                    )]}}
                yield {"model": {"messages": [SimpleNamespace(
                    content="unsupported[retrieval-test:cit-1]。",
                    tool_calls=[],
                )]}}

        tool = Tool()
        interface = ChatInterface(object(), {"MedicalEvidenceRAGTool": tool})
        workflow = Workflow()
        interface.workflow = workflow

        async def consume():
            last = None
            async for item in interface.process_message("test", None, []):
                last = item
            return last

        with tempfile.TemporaryDirectory() as directory:
            interface.audit_path = Path(directory) / "audit.jsonl"
            history, _, _ = asyncio.run(consume())

        self.assertEqual(workflow.calls, 3)
        self.assertIn("经过两次修改后仍未通过", history[-1].content)


if __name__ == "__main__":
    unittest.main()
