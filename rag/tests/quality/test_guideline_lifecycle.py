"""v6.7.1 guideline lifecycle: real PubMed XML parsing, date gating, cache, modes."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from rag.connectors import ConnectorRegistry, InMemoryConnector
from rag.sources.guideline_lifecycle import (
    GuidelineLifecycleResolver,
    fetch_pubmed_lifecycle,
    parse_pubmed_xml,
)
from rag.models import RetrievalRequest, SourceDocument
from rag.pipeline import EvidenceRetrievalPipeline
from rag.tests import lifecycle_fixtures as fixtures


def _transport(url: str, *, timeout: float = 60.0) -> bytes:
    ids = parse_qs(urlparse(url).query).get("id", [""])[0].split(",")
    if "41110921" in ids:
        return fixtures.RETRACTED_GUIDELINE_XML.encode("utf-8")
    if "41422828" in ids:
        return fixtures.RETRACTION_NOTICE_XML.encode("utf-8")
    if "41802129" in ids:
        return fixtures.CURRENT_GUIDELINE_XML.encode("utf-8")
    return b"<?xml version='1.0'?><PubmedArticleSet></PubmedArticleSet>"


def _guideline_document(pmid: str = "41110921") -> SourceDocument:
    return SourceDocument(
        source_id="guideline-test",
        document_id="guide-test",
        kind="guideline",
        title="测试指南：新生儿呼吸窘迫综合征",
        text="新生儿呼吸窘迫综合征 治疗 肺表面活性物质 respiratory distress syndrome surfactant",
        published_at="2025-10-01",
        pmid=pmid,
        version_id="2025",
        status="current",
    )


class ParserTests(unittest.TestCase):
    def test_retraction_and_correction_dates_are_parsed_from_real_xml(self) -> None:
        parsed = parse_pubmed_xml(fixtures.RETRACTED_GUIDELINE_XML)
        record = parsed["41110921"]
        self.assertIn("Retracted Publication", record["publication_types"])
        events = {event.kind: event for event in record["events"]}
        self.assertIn("retraction", events)
        self.assertEqual(events["retraction"].ref_type, "RetractionIn")
        self.assertEqual(events["retraction"].pmid, "41422828")
        self.assertIn("correction", events)

        records = fetch_pubmed_lifecycle(["41110921"], transport=_transport)
        retraction = records["41110921"].retraction_events[0]
        self.assertEqual(retraction.date, "2025-12-23")
        self.assertEqual(retraction.date_precision, "day")
        self.assertIn("WITHDRAWN", retraction.title or "")

    def test_retraction_notice_requested_as_target_still_supplies_event_date(self) -> None:
        records = fetch_pubmed_lifecycle(["41110921", "41422828"], transport=_transport)
        retraction = records["41110921"].retraction_events[0]
        self.assertEqual(retraction.date, "2025-12-23")

    def test_retracted_publication_without_dated_notice_is_current_state_retracted(self) -> None:
        parsed = parse_pubmed_xml(fixtures.RETRACTED_WITHOUT_DATED_NOTICE_XML)
        # Build the record directly from the real XML excerpt: the RetractionIn
        # entry has no notice PMID, so there is no dated related record to fetch.
        from rag.sources.guideline_lifecycle import GuidelineLifecycleRecord
        row = parsed["33775419"]
        record = GuidelineLifecycleRecord(
            pmid="33775419", published_at=row["published_at"], title=row["title"],
            publication_types=tuple(row["publication_types"]), events=tuple(row["events"]),
            checked_at="2026-11-01T00:00:00+00:00",
        )
        current = record.status_at(None)
        self.assertEqual(current["status"], "retracted")
        self.assertTrue(any("日期未知" in note for note in current["notes"]))
        historical = record.status_at("2021-06-01")
        self.assertEqual(historical["status"], "current")
        self.assertTrue(historical["unknown_date_events"])

    def test_current_guideline_has_no_lifecycle_events(self) -> None:
        records = fetch_pubmed_lifecycle(["41802129"], transport=_transport)
        record = records["41802129"]
        self.assertFalse(record.events)
        self.assertEqual(record.status_at("2026-11-01")["status"], "current")


class ResolverTests(unittest.TestCase):
    def test_temporal_gating_before_and_after_retraction(self) -> None:
        records = fetch_pubmed_lifecycle(["41110921"], transport=_transport)
        record = records["41110921"]
        before = record.status_at("2025-12-22")
        self.assertEqual(before["status"], "current")
        self.assertTrue(before["future_events"])
        after = record.status_at("2025-12-23")
        self.assertEqual(after["status"], "retracted")
        self.assertTrue(after["applied_events"])

    def test_shadow_leaves_documents_untouched_and_active_marks_withdrawn(self) -> None:
        document = _guideline_document()
        shadow = GuidelineLifecycleResolver(mode="shadow", transport=_transport)
        shadow_docs, shadow_trace = shadow.annotate([document], as_of="2026-11-01")
        self.assertEqual(shadow_docs[0].status, "current")
        self.assertEqual(shadow_trace["statuses"]["41110921"]["status"], "retracted")
        self.assertFalse(shadow_trace["documents"][0]["applied"])

        active = GuidelineLifecycleResolver(mode="active", transport=_transport)
        active_docs, active_trace = active.annotate([document], as_of="2026-11-01")
        self.assertEqual(active_docs[0].status, "withdrawn")
        self.assertTrue(active_trace["documents"][0]["applied"])
        self.assertIn("lifecycle", active_docs[0].metadata)

    def test_synthetic_pmids_are_not_sent_to_pubmed(self) -> None:
        calls = {"count": 0}

        def counting_transport(url: str, *, timeout: float = 60.0) -> bytes:
            calls["count"] += 1
            return b"<?xml version='1.0'?><PubmedArticleSet></PubmedArticleSet>"

        document = SourceDocument(
            source_id="guideline-demo",
            document_id="syn-demo",
            kind="guideline",
            title="合成演示指南",
            text="合成演示",
            pmid="90002001",
            metadata={"synthetic": True},
        )
        resolver = GuidelineLifecycleResolver(mode="active", transport=counting_transport)
        docs, trace = resolver.annotate([document], as_of="2026-11-01")
        self.assertEqual(calls["count"], 0)
        self.assertEqual(docs[0].pmid, "90002001")
        self.assertEqual(trace["synthetic_excluded"], ["90002001"])

    def test_cache_avoids_second_network_call(self) -> None:
        calls = {"count": 0}

        def counting_transport(url: str, *, timeout: float = 60.0) -> bytes:
            calls["count"] += 1
            return _transport(url, timeout=timeout)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "lifecycle.json"
            resolver = GuidelineLifecycleResolver(mode="shadow", cache_path=str(cache_path), transport=counting_transport)
            first = resolver.resolve(["41110921"], as_of="2026-11-01")
            second = resolver.resolve(["41110921"], as_of="2026-11-01")
            self.assertEqual(first["statuses"]["41110921"]["status"], "retracted")
            self.assertEqual(second["statuses"]["41110921"]["status"], "retracted")
            self.assertEqual(calls["count"], 2)  # target + related notice; cached call adds none
            self.assertTrue(cache_path.exists())


class PipelineLifecycleTests(unittest.TestCase):
    def _pipeline(self, resolver, mode: str) -> EvidenceRetrievalPipeline:
        registry = ConnectorRegistry()
        registry.register(InMemoryConnector("guideline-test", ("guideline",), (_guideline_document(),)))
        return EvidenceRetrievalPipeline(registry, guideline_lifecycle=resolver, guideline_lifecycle_mode=mode)

    def test_active_removes_retracted_guideline_from_evidence(self) -> None:
        resolver = GuidelineLifecycleResolver(mode="active", transport=_transport)
        package = self._pipeline(resolver, "active").retrieve(
            RetrievalRequest(request_id="active", question="新生儿呼吸窘迫综合征治疗", as_of="2026-11-01")
        )
        self.assertFalse(package.ranked_documents)
        self.assertIn("guideline_lifecycle", package.trace)
        self.assertTrue(any("撤回" in warning for warning in package.warnings))

    def test_historical_as_of_before_retraction_keeps_guideline(self) -> None:
        resolver = GuidelineLifecycleResolver(mode="active", transport=_transport)
        package = self._pipeline(resolver, "active").retrieve(
            RetrievalRequest(request_id="history", question="新生儿呼吸窘迫综合征治疗", as_of="2025-12-22")
        )
        self.assertTrue(package.ranked_documents)
        self.assertFalse(package.ranked_documents[0].evidence_status.value == "withdrawn")

    def test_off_mode_has_no_lifecycle_trace(self) -> None:
        package = self._pipeline(None, "off").retrieve(
            RetrievalRequest(request_id="off", question="新生儿呼吸窘迫综合征治疗", as_of="2026-11-01")
        )
        self.assertNotIn("guideline_lifecycle", package.trace)


if __name__ == "__main__":
    unittest.main()
