"""Contract tests for LLM-assisted qrels annotation (no API calls)."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from rag.eval.qrels.llm_annotation import (
    AnnotationError,
    AnnotationUnavailable,
    LLMAnnotator,
)


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Response(self.content)


class _Client:
    def __init__(self, content):
        self.chat = mock.Mock()
        self.chat.completions = _Completions(content)


def _annotator(content: str, model: str = "fake-llm") -> tuple[LLMAnnotator, _Completions]:
    client = _Client(content)
    return LLMAnnotator(client=client, model=model), client.chat.completions


class LLMAnnotationTests(unittest.TestCase):
    def test_external_valid_annotation(self):
        annotator, _ = _annotator(
            '{"label":3,"evidence_span":"preterm infants","reason":"direct","confidence":"high","abstain":false}'
        )
        result = annotator.annotate(
            track="external_evidence",
            question="preterm feeding guideline",
            candidate={"id": "d1", "text": "A guideline for preterm infants with feeding support."},
        )
        self.assertEqual(result["label"], 3)
        self.assertTrue(result["validation"]["span_exists"])
        self.assertFalse(result["needs_review"])
        self.assertEqual(result["label_source"], "llm")

    def test_span_mismatch_needs_review(self):
        annotator, _ = _annotator(
            '{"label":3,"evidence_span":"not in text","reason":"x","confidence":"high","abstain":false}'
        )
        result = annotator.annotate(
            track="external_evidence",
            question="q",
            candidate={"id": "d1", "text": "preterm infants feeding"},
        )
        self.assertFalse(result["validation"]["span_exists"])
        self.assertTrue(result["needs_review"])

    def test_patient_fact_unknown_is_allowed(self):
        annotator, _ = _annotator(
            '{"answerability":"unknown","answer_value":null,"evidence_span":"","reason":"not recorded","confidence":"high","abstain":false}'
        )
        result = annotator.annotate(
            track="patient_fact",
            question="hearing screen?",
            candidate={"id": "f1", "text": "no hearing screen mentioned"},
        )
        self.assertEqual(result["answerability"], "unknown")
        self.assertFalse(result["needs_review"])

    def test_numeric_mismatch_needs_review(self):
        annotator, _ = _annotator(
            '{"answerability":"answerable","answer_value":"7.2","evidence_span":"bilirubin 5.1","reason":"x","confidence":"high","abstain":false}'
        )
        result = annotator.annotate(
            track="patient_fact",
            question="bilirubin?",
            candidate={"id": "f2", "text": "bilirubin 5.1 mg/dL"},
        )
        self.assertFalse(result["validation"]["numeric_ok"])
        self.assertTrue(result["needs_review"])

    def test_missing_configuration_is_explicit(self):
        env = {
            "CHINALLM_LLM_MODEL": "",
            "CHINALLM_LLM_API_KEY": "",
            "OPENAI_MODEL": "",
            "OPENAI_API_KEY": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(AnnotationUnavailable):
                LLMAnnotator()

    def test_malformed_response_is_rejected(self):
        annotator, _ = _annotator("not json")
        with self.assertRaises(AnnotationError):
            annotator.annotate(
                track="applicability",
                question="q",
                candidate={"id": "c1", "text": "clinical text"},
            )

    def test_repeated_call_uses_cache(self):
        annotator, completions = _annotator(
            '{"label":0,"evidence_span":"","reason":"irrelevant","confidence":"high","abstain":false}'
        )
        first = annotator.annotate(
            track="external_evidence", question="q",
            candidate={"id": "d1", "text": "unrelated text"},
        )
        second = annotator.annotate(
            track="external_evidence", question="q",
            candidate={"id": "d1", "text": "unrelated text"},
        )
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(completions.calls), 1)


if __name__ == "__main__":
    unittest.main()
