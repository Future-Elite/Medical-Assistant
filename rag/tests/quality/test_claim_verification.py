"""v6.7.1 claim verification adapters and audit integration (no model download)."""

from __future__ import annotations

import unittest

from rag.agent import EvidenceRAG
from rag.audit import GroundedAnswerAuditor
from rag.quality.claim_verification import (
    CrossLingualMiniCheckVerifier,
    MiniCheckVerifier,
    build_claim_verifier,
)
from rag.tests.helpers import fixture_pipeline


class FakeScorer:
    name = "fake-scorer"
    model_name = "fake-model"
    available = True

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    def verify(self, pairs):
        self.seen = list(pairs)
        return [("supported" in claim.lower(), 0.9 if "supported" in claim.lower() else 0.1) for claim, _ in pairs]


class FakeTranslator:
    name = "fake-zh-en"
    available = True
    last_translations = {}

    def translate(self, texts):
        return [f"translated({text})" for text in texts]

    def describe(self):
        return {"name": self.name, "loaded": True}


class UnavailableTranslator(FakeTranslator):
    available = False
    unavailable_reason = "translator not configured"


class AdapterTests(unittest.TestCase):
    def test_model_alias_is_canonicalized(self) -> None:
        verifier = MiniCheckVerifier(model_name="lytang/MiniCheck-Flan-T5-Large")
        self.assertEqual(verifier.model_name, "flan-t5-large")
        self.assertEqual(verifier.name, "minicheck")

    def test_empty_config_builds_no_verifier(self) -> None:
        self.assertIsNone(build_claim_verifier(model_name=""))

    def test_cross_lingual_verifier_translates_only_chinese_claims(self) -> None:
        scorer = FakeScorer()
        verifier = CrossLingualMiniCheckVerifier(scorer=scorer, translator=FakeTranslator())
        results = verifier.verify([
            ("supported 中文主张", "evidence"),
            ("supported english claim", "evidence"),
        ])
        self.assertEqual([result[0] for result in results], [True, True])
        self.assertIn("supported 中文主张", verifier.last_translations)
        self.assertEqual(
            dict(scorer.seen)["translated(supported 中文主张)"],
            "evidence",
        )
        self.assertEqual(scorer.seen[1][0], "supported english claim")

    def test_chinese_claim_without_translator_raises_not_silently_checks(self) -> None:
        verifier = CrossLingualMiniCheckVerifier(scorer=FakeScorer(), translator=None)
        with self.assertRaises(RuntimeError):
            verifier.verify([("中文主张", "evidence")])

    def test_unavailable_translator_raises(self) -> None:
        verifier = CrossLingualMiniCheckVerifier(scorer=FakeScorer(), translator=UnavailableTranslator())
        with self.assertRaises(RuntimeError):
            verifier.verify([("中文主张", "evidence")])


class TranslatingStubVerifier:
    name = "stub-translating"
    model_name = "stub-model"
    translator_name = "stub-zh-en"
    available = True

    def __init__(self) -> None:
        self.last_translations = {}

    def verify(self, pairs):
        self.last_translations = {
            claim: f"EN:{claim}" for claim, _ in pairs if any("\u4e00" <= char <= "\u9fff" for char in claim)
        }
        return [(True, 0.88) for _ in pairs]


class UnavailableVerifier:
    name = "unavailable"
    available = False
    unavailable_reason = "model weights missing"

    def verify(self, pairs):
        raise AssertionError("must not be called")


class AuditTranslationTests(unittest.TestCase):
    def _retrieval(self, tool: EvidenceRAG) -> dict:
        return tool.retrieve_evidence({
            "question": "新生儿呼吸窘迫综合征治疗指南推荐什么？",
            "as_of": "2026-04-15",
            "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]},
        })

    def test_translated_claim_and_original_are_both_visible(self) -> None:
        verifier = TranslatingStubVerifier()
        tool = EvidenceRAG(fixture_pipeline(), auditor=GroundedAnswerAuditor(verifier))
        result = self._retrieval(tool)
        citation_id = result["citations"][0]["citation_id"]
        audit = tool.audit_grounded_answer({
            "retrieval_id": result["retrieval_id"],
            "answer": f"早产儿可使用肺表面活性物质[{citation_id}]。",
        })
        claim = audit["claims"][0]
        self.assertTrue(claim["translated_for_verification"])
        self.assertEqual(claim["verifier_claim_en"], "EN:早产儿可使用肺表面活性物质。")
        self.assertEqual(audit["verifier"]["translation"]["used"], True)
        self.assertEqual(claim["ragtruth_label"], "supported")
        self.assertTrue(any("机器翻译" in warning for warning in audit["warnings"]))
        self.assertTrue(any("机器翻译" in item for item in audit["limitations"]))

        repeated = tool.audit_grounded_answer({
            "retrieval_id": result["retrieval_id"],
            "answer": f"早产儿可使用肺表面活性物质[{citation_id}]。",
        })
        self.assertTrue(repeated["claims"][0]["translated_for_verification"])
        self.assertEqual(repeated["claims"][0]["verifier_claim_en"], "EN:早产儿可使用肺表面活性物质。")

    def test_unavailable_verifier_stays_not_checked(self) -> None:
        tool = EvidenceRAG(fixture_pipeline(), auditor=GroundedAnswerAuditor(UnavailableVerifier()))
        result = self._retrieval(tool)
        citation_id = result["citations"][0]["citation_id"]
        audit = tool.audit_grounded_answer({
            "retrieval_id": result["retrieval_id"],
            "answer": f"主张[{citation_id}]。",
        })
        self.assertEqual(audit["claims"][0]["ragtruth_label"], "not_checked")
        self.assertIsNone(audit["alce_like"]["citation_precision"])
        self.assertIn("model weights missing", " ".join(audit["warnings"]))


if __name__ == "__main__":
    unittest.main()
