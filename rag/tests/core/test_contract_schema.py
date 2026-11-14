"""Contract tests for the retrieval service."""

from __future__ import annotations

import unittest

from rag.config import get_config
from rag.models import RetrievalRequest, SourceDocument
from rag.pubmed import PubMedConnector


class TestContracts(unittest.TestCase):
    def test_request_rejects_empty_question(self) -> None:
        with self.assertRaises(ValueError):
            RetrievalRequest.from_dict({"question": " "})

    def test_request_id_is_generated_when_omitted(self) -> None:
        first = RetrievalRequest.from_dict({"question": "问题一"})
        second = RetrievalRequest.from_dict({"question": "问题二"})
        self.assertTrue(first.request_id.startswith("request-"))
        self.assertNotEqual(first.request_id, second.request_id)

        document = SourceDocument.from_dict({
            "document_id": "g-1", "kind": "guideline", "title": "指南", "text": "建议。", "issuer": "协会",
        }, source_id="guideline")
        self.assertEqual(document.metadata["issuer"], "协会")

    def test_upgraded_defaults(self) -> None:
        self.assertEqual(RetrievalRequest.from_dict({"question": "q"}).top_k, 50)
        self.assertEqual(get_config().entity_linking_mode, "active")
        self.assertEqual(get_config().guideline_lifecycle_mode, "active")
        self.assertEqual(get_config().minicheck_model, "flan-t5-large")
        self.assertTrue(get_config().entity_linking_semantic)
        self.assertEqual(get_config().pubmed_max_results, 200)
        self.assertEqual(PubMedConnector(transport=lambda *args: b"").max_results, 200)

    def test_doi_is_a_cross_source_deduplication_key(self) -> None:
        document = SourceDocument.from_dict({
            "document_id": "1", "kind": "pubmed", "title": "A", "text": "text", "doi": "10.1/ABC",
        }, source_id="pubmed")
        self.assertEqual(document.identity_key(), "doi:10.1/abc")
