import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from interface import ChatInterface
from medrax.answer_audit import AnswerAuditModule, QwenClaimTranslator, TranslationValidationError


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
    def test_numeric_validation_accepts_reordered_date_and_rejects_changed_dose(self):
        translator = QwenClaimTranslator()
        source = "截至2021年3月，纳入24项研究，随访48周；2025-03-03血压158/96 mmHg，剂量30 mg，p<0.05。"
        equivalent = "On March 3, 2025 the dose was 30 mg and BP was 158/96 mmHg; 24 studies with 48 weeks of follow-up were included through March 2021 (p<0.05)."
        self.assertEqual(translator._numeric_facts(source), translator._numeric_facts(equivalent))
        changed = equivalent.replace("30 mg", "20 mg")
        self.assertNotEqual(translator._numeric_facts(source), translator._numeric_facts(changed))

    def test_numeric_validation_normalizes_hyphenated_time_units_and_treatment_plus(self):
        translator = QwenClaimTranslator()
        source = "一项6周RCT纳入74例1级高血压患者，DASH+8小时TRE组收缩压降低8.459 ± 4.260 mmHg。"
        equivalent = (
            "A 6-week RCT enrolled 74 patients with stage 1 hypertension; the "
            "DASH + 8-hour TRE group reduced systolic blood pressure by 8.459 ± 4.260 mmHg."
        )
        expected = {
            "measurement": ["4.260mmhg", "6week", "8hour"],
            "stage": ["1"],
            "count": ["74"],
            "number": ["8.459"],
        }
        self.assertEqual(translator._numeric_facts(source), expected)
        self.assertEqual(translator._numeric_facts(equivalent), expected)

    def test_numeric_validation_normalizes_number_words_and_comma_counts(self):
        translator = QwenClaimTranslator()
        source = "在411,202例患者中，35.2%在1年内至少检测1次血清钾。"
        equivalent = (
            "Among 411,202 patients, 35.2% had at least one serum potassium test within 1 year."
        )
        facts = translator._numeric_facts(source)
        self.assertEqual(facts, translator._numeric_facts(equivalent))
        self.assertEqual(facts["count"], ["1", "411202"])

    def test_numeric_translation_validation_is_temporarily_bypassed(self):
        translator = QwenClaimTranslator()
        count_model = Mock()
        count_model.invoke.return_value = SimpleNamespace(
            content='{"translations":[{"id":0,"text":"The study enrolled 75 patients."}]}'
        )
        translator._get_model = Mock(return_value=count_model)
        module = AnswerAuditModule(
            lambda retrieval_id, answer, **kwargs: SUPPORTED,
            translator,
        )
        result = module.audit("该研究纳入74例患者[retrieval-test:cit-1]。", "retrieval-test")
        self.assertTrue(result["passed"])
        self.assertNotIn("translation_validation_warnings", result)

        dose_model = Mock()
        dose_model.invoke.return_value = SimpleNamespace(
            content='{"translations":[{"id":0,"text":"Dose 20 mg"}]}'
        )
        dose_translator = QwenClaimTranslator()
        dose_translator._get_model = Mock(return_value=dose_model)
        self.assertEqual(dose_translator(["剂量30 mg"]), ["Dose 20 mg"])
        self.assertEqual(dose_model.invoke.call_count, 1)
        with self.assertRaises(TranslationValidationError):
            dose_translator._validate_numeric_translations(["剂量30 mg"], ["Dose 20 mg"])

    def test_numeric_translation_failure_is_revisable_and_logged(self):
        mismatch = [{
            "claim_index": 0, "claim_zh": "剂量30 mg", "claim_en": "Dose 20 mg",
            "source_numeric_facts": {"measurement": ["30mg"]},
            "target_numeric_facts": {"measurement": ["20mg"]},
        }]
        module = AnswerAuditModule(
            Mock(), Mock(side_effect=TranslationValidationError(mismatch)),
        )
        result = module.audit("剂量30 mg[retrieval-test:cit-1]", "retrieval-test")
        self.assertEqual(result["status"], "failed")
        self.assertNotEqual(result["status"], "error")
        self.assertEqual(result["translation_validation"], mismatch)
        self.assertIn("30mg", module.revision_prompt(result, 1))

    def test_bypassed_numeric_mismatch_does_not_retry_translation(self):
        translator = QwenClaimTranslator()
        model = Mock()
        model.invoke.side_effect = [
            SimpleNamespace(content='{"translations":[{"id":0,"text":"Dose 20 mg"}]}'),
            SimpleNamespace(content='{"translations":[{"id":0,"text":"Dose 30 mg"}]}'),
        ]
        translator._get_model = Mock(return_value=model)
        self.assertEqual(translator(["剂量30 mg"]), ["Dose 20 mg"])
        self.assertEqual(model.invoke.call_count, 1)

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

    def test_citation_only_line_is_attached_to_preceding_claim_before_audit(self):
        captured = {}

        def audit(retrieval_id, answer, claim_records=None):
            captured["answer"] = answer
            captured["records"] = claim_records
            return {
                "verifier": {"name": "stub-minicheck", "available": True},
                "claims": [{
                    "claim": row["claim_en"],
                    "valid_citation_ids": row["citation_ids"],
                    "invalid_citation_ids": [],
                    "ragtruth_label": "supported",
                } for row in claim_records],
            }

        module = AnswerAuditModule(audit, lambda claims: claims)
        result = module.audit(
            "一项有证据支持的主张。\n\n[retrieval-test:cit-1]",
            "retrieval-test",
        )

        self.assertTrue(result["passed"])
        self.assertEqual(captured["answer"].splitlines()[0], "一项有证据支持的主张[retrieval-test:cit-1]。")
        self.assertEqual(captured["records"][0]["claim_en"], "一项有证据支持的主张。")
        self.assertEqual(
            captured["records"][0]["citation_ids"],
            ["retrieval-test:cit-1"],
        )

    def test_compound_cited_claim_is_audited_as_atomic_clauses(self):
        captured = {}

        def audit(retrieval_id, answer, claim_records=None):
            captured["records"] = claim_records
            return {
                "verifier": {"name": "stub-minicheck", "available": True},
                "claims": [
                    {
                        "claim": row["claim_en"],
                        "valid_citation_ids": row["citation_ids"],
                        "invalid_citation_ids": [],
                        "ragtruth_label": "supported",
                    }
                    for row in claim_records
                ],
            }

        module = AnswerAuditModule(audit, lambda claims: claims)
        result = module.audit(
            "结论一；结论二[retrieval-test:cit-1]。",
            "retrieval-test",
        )

        self.assertTrue(result["passed"])
        self.assertEqual(len(captured["records"]), 2)
        self.assertEqual(
            [row["citation_ids"] for row in captured["records"]],
            [["retrieval-test:cit-1"], ["retrieval-test:cit-1"]],
        )

    def test_revision_prompt_preserves_passed_text_and_deletes_failed_claim_only(self):
        module = AnswerAuditModule(Mock(), Mock())
        prompt = module.revision_prompt({
            "failure_reasons": ["unsupported"],
            "claims": [{
                "claim_zh": "证据不支持的扩展结论",
                "valid_citation_ids": ["retrieval-test:cit-1"],
                "invalid_citation_ids": [],
                "ragtruth_label": "evident_baseless_info",
            }],
        }, 1)

        self.assertIn("Preserve every other sentence", prompt)
        self.assertIn("delete only that claim", prompt)
        self.assertIn("do not replace it with a new inference", prompt)
        self.assertIn("证据不支持的扩展结论", prompt)

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
