"""End-to-end retrieval tests with only synthetic, non-clinical fixtures."""

from __future__ import annotations

import unittest

from v5.connectors import ConnectorRegistry, InMemoryConnector
from v5.models import RetrievalRequest, SourceDocument
from v5.pipeline import EvidenceRetrievalPipeline


def _pipeline() -> EvidenceRetrievalPipeline:
    registry = ConnectorRegistry()
    registry.register(InMemoryConnector("guideline", ("guideline",), (
        SourceDocument.from_dict({"document_id": "guide-1", "kind": "guideline", "title": "高血压指南", "text": "高血压药物治疗建议。", "updated_at": "2024-01-01", "locator": "第3章", "metadata": {"official": True}}, source_id="guideline"),
    )))
    registry.register(InMemoryConnector("pubmed", ("pubmed",), (
        SourceDocument.from_dict({"document_id": "pmid-1", "kind": "pubmed", "title": "Hypertension", "text": "Hypertension treatment study.", "published_at": "2023-01-01", "pmid": "1"}, source_id="pubmed"),
        SourceDocument.from_dict({"document_id": "future", "kind": "pubmed", "title": "Future", "text": "future evidence", "published_at": "2027-01-01", "pmid": "2"}, source_id="pubmed"),
    )))
    return EvidenceRetrievalPipeline(registry)


class TestPipeline(unittest.TestCase):
    def test_key_evidence_sentences_are_locatable(self) -> None:
        package = _pipeline().retrieve(RetrievalRequest.from_dict({"request_id": "t", "question": "高血压治疗指南推荐什么？", "as_of": "2025-01-01"}))
        citation = package.citations[0]
        self.assertTrue(citation.key_evidence_sentences)
        sentence = citation.key_evidence_sentences[0]
        source = package.ranked_documents[0].document.text
        self.assertEqual(source[sentence["start"]:sentence["end"]], sentence["text"])
        self.assertIn("sentence 1", sentence["locator"])

    def test_guideline_is_first_for_recommendation_question(self) -> None:
        package = _pipeline().retrieve(RetrievalRequest.from_dict({"request_id": "t", "question": "高血压治疗指南推荐什么？", "as_of": "2025-01-01"}))
        self.assertEqual(package.ranked_documents[0].document.kind, "guideline")
        self.assertEqual(package.citations[0].locator, "第3章")

    def test_future_evidence_is_not_returned(self) -> None:
        package = _pipeline().retrieve(RetrievalRequest.from_dict({"request_id": "t", "question": "future evidence", "as_of": "2025-01-01"}))
        self.assertNotIn("future", [item.document.document_id for item in package.ranked_documents])
        self.assertTrue(any("排除" in warning for warning in package.warnings))
