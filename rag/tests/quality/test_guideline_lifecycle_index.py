"""Tests for the cached guideline lifecycle parallel signal."""
from __future__ import annotations

import unittest

from rag.models import SourceDocument
from rag.quality.guideline_lifecycle_index import LifecycleIndex
from rag.sources.guideline_lifecycle import GuidelineLifecycleRecord, LifecycleEvent


def _document(document_id: str, pmid: str | None) -> SourceDocument:
    return SourceDocument(
        source_id="test", document_id=document_id, kind="guideline",
        title=document_id, text="", pmid=pmid,
    )


def _record(pmid: str, *, events=(), published_at="2020-01-01") -> GuidelineLifecycleRecord:
    return GuidelineLifecycleRecord(
        pmid=pmid, published_at=published_at, title=pmid,
        publication_types=("Practice Guideline",), events=tuple(events),
        checked_at="2026-11-10T00:00:00+00:00",
    )


class LifecycleIndexTests(unittest.TestCase):
    def test_current_retracted_superseded_and_missing(self):
        index = LifecycleIndex(records={
            "1": _record("1"),
            "2": _record("2", events=(
                LifecycleEvent(kind="retraction", ref_type="RetractionIn", source="pubmed", date="2023-01-01"),
            )),
            "3": _record("3", events=(
                LifecycleEvent(kind="update", ref_type="UpdateIn", source="pubmed", date="2023-06-01"),
            )),
        })
        result = index.statuses(
            [_document("g1", "1"), _document("g2", "2"), _document("g3", "3"),
             _document("g4", "4"), _document("g5", None)],
            as_of="2024-01-01",
        )
        self.assertEqual(result["guidelines"]["g1"]["status"], "current")
        self.assertEqual(result["guidelines"]["g2"]["status"], "retracted")
        self.assertEqual(result["guidelines"]["g3"]["status"], "superseded")
        self.assertEqual(result["guidelines"]["g4"]["status"], "not_checked")
        self.assertEqual(result["guidelines"]["g5"]["status"], "not_checked")
        self.assertEqual(result["overall"], "retracted")

    def test_historical_as_of_does_not_apply_future_retraction(self):
        index = LifecycleIndex(records={
            "2": _record("2", events=(
                LifecycleEvent(kind="retraction", ref_type="RetractionIn", source="pubmed", date="2023-01-01"),
            )),
        })
        result = index.statuses([_document("g2", "2")], as_of="2022-01-01")
        self.assertEqual(result["guidelines"]["g2"]["status"], "current")
        self.assertEqual(result["guidelines"]["g2"]["future_events"][0]["kind"], "retraction")

    def test_before_publication_is_not_yet_published(self):
        index = LifecycleIndex(records={"1": _record("1", published_at="2020-01-01")})
        result = index.statuses([_document("g1", "1")], as_of="2019-01-01")
        self.assertEqual(result["guidelines"]["g1"]["status"], "not_yet_published")
        self.assertEqual(result["overall"], "not_yet_published")

    def test_current_and_missing_is_mixed(self):
        index = LifecycleIndex(records={"1": _record("1")})
        result = index.statuses([_document("g1", "1"), _document("g2", "2")], as_of="2024-01-01")
        self.assertEqual(result["overall"], "mixed")


if __name__ == "__main__":
    unittest.main()
