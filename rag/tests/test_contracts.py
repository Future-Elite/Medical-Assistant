"""Contract tests to run after integration; not executed during the v5 handoff."""

from __future__ import annotations

import unittest

from v5.models import RetrievalRequest, SourceDocument


class TestContracts(unittest.TestCase):
    def test_request_rejects_empty_question(self) -> None:
        with self.assertRaises(ValueError):
            RetrievalRequest.from_dict({"question": " "})

    def test_unknown_document_fields_are_preserved_as_metadata(self) -> None:
        document = SourceDocument.from_dict({
            "document_id": "g-1", "kind": "guideline", "title": "指南", "text": "建议。", "issuer": "协会",
        }, source_id="guideline")
        self.assertEqual(document.metadata["issuer"], "协会")

    def test_doi_is_a_cross_source_deduplication_key(self) -> None:
        document = SourceDocument.from_dict({
            "document_id": "1", "kind": "pubmed", "title": "A", "text": "text", "doi": "10.1/ABC",
        }, source_id="pubmed")
        self.assertEqual(document.identity_key(), "doi:10.1/abc")
