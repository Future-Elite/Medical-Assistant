"""Live open-access guideline full-text connector tests (no real network)."""
from __future__ import annotations

import json
import unittest

from rag.connectors import ConnectorRegistry
from rag.guideline_fulltext import (
    GuidelineFullTextError,
    LiveGuidelineFullTextConnector,
    _chunk_text,
    _parse_jats,
)
from rag.models import QueryPlan, RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline

SEARCH_PAYLOAD = {
    "resultList": {
        "result": [
            {
                "id": "41802129",
                "pmid": "41802129",
                "pmcid": "PMC13038240",
                "doi": "10.1159/000551062",
                "title": "European Consensus Guidelines on the Management of Respiratory Distress Syndrome: 2025",
                "journalTitle": "Neonatology",
                "pubYear": "2026",
                "firstPublicationDate": "2026-03-09",
                "isOpenAccess": "Y",
                "abstractText": "Abstract: CPAP and surfactant are discussed.",
                "pubType": "practice guideline",
            },
            {
                "id": "41802130",
                "pmid": "41802130",
                "pmcid": "PMC99999998",
                "doi": "10.1000/non-oa-rds",
                "title": "Respiratory Distress Syndrome in Preterm Infants: A Non-Open-Access Guideline",
                "journalTitle": "Test Journal",
                "pubYear": "2025",
                "firstPublicationDate": "2025-09-01",
                "isOpenAccess": "N",
                "abstractText": "Abstract: respiratory distress syndrome and surfactant.",
                "pubType": "practice guideline",
            },
        ]
    }
}

FULLTEXT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<article xml:lang="en">
  <front>
    <article-meta>
      <title-group><article-title>European Consensus Guidelines on the Management of Respiratory Distress Syndrome: 2025</article-title></title-group>
      <abstract><p>Abstract: CPAP and surfactant are discussed.</p></abstract>
      <permissions><license>CC BY 4.0</license></permissions>
    </article-meta>
  </front>
  <body>
    <sec>
      <title>Recommendations on CPAP</title>
      <p>Use CPAP for preterm infants with RDS.</p>
      <p>Surfactant should be considered early.</p>
    </sec>
    <sec>
      <title>Monitoring</title>
      <p>Monitor SpO2 and blood gases.</p>
    </sec>
  </body>
</article>
"""


class FakeTransport:
    def __init__(self, search_payload=None, fulltext_xml=FULLTEXT_XML, fail_fulltext=False):
        self.search_payload = search_payload or SEARCH_PAYLOAD
        self.fulltext_xml = fulltext_xml
        self.fail_fulltext = fail_fulltext
        self.urls: list[str] = []

    def __call__(self, url, params, timeout):
        self.urls.append(url)
        if "search?" in url:
            return json.dumps(self.search_payload, ensure_ascii=False).encode("utf-8")
        if "fullTextXML" in url:
            if self.fail_fulltext:
                raise OSError("simulated full-text failure")
            return self.fulltext_xml
        raise AssertionError(f"unexpected URL: {url}")


def _plan(question="该患儿新生儿呼吸窘迫综合征的CPAP和肺表面活性物质推荐是什么？"):
    return QueryPlan(
        question,
        (question,),
        ("respiratory distress syndrome", "CPAP", "surfactant"),
        ("guideline-fulltext-live",),
        {"source_kinds": ["guideline"]},
        (),
        english_terms=("respiratory distress syndrome", "pulmonary surfactant", "CPAP"),
        mesh_terms=("Respiratory Distress Syndrome, Newborn",),
    )


class LiveGuidelineFullTextTests(unittest.TestCase):
    def test_open_access_fulltext_and_non_open_access_metadata(self):
        transport = FakeTransport()
        connector = LiveGuidelineFullTextConnector(transport=transport)
        result = connector.search(_plan(), limit=20)

        self.assertIn("guideline", connector.last_query or "")
        self.assertIn("neonatal", connector.last_query or "")
        self.assertTrue(any("resultType=core" in url for url in transport.urls))

        metadata_docs = [doc for doc in result.documents if doc.document_id.endswith("-metadata")]
        fulltext_docs = [doc for doc in result.documents if doc.metadata.get("content_mode") == "full_text"]
        # OA records with a parsed full text are emitted as chunks only (no
        # duplicate metadata row); the non-OA record keeps its metadata row.
        self.assertEqual(len(metadata_docs), 1)
        self.assertTrue(fulltext_docs)

        for doc in fulltext_docs:
            self.assertIsNone(doc.doi)
            self.assertIsNone(doc.pmid)
            self.assertEqual(doc.metadata["rights_basis"], "open_access")
            self.assertTrue(doc.metadata["full_text_allowed"])
            self.assertEqual(doc.metadata["evidence_type"], "clinical_practice_guideline")
            self.assertEqual(doc.metadata["doi"], "10.1159/000551062")
            self.assertIn(doc.metadata["section"], {"Recommendations on CPAP", "Monitoring"})

        non_open_access = next(doc for doc in metadata_docs if doc.pmid == "41802130")
        self.assertEqual(non_open_access.metadata["rights_basis"], "not_open_access")
        self.assertFalse(non_open_access.metadata["full_text_allowed"])
        self.assertTrue(any("不是开放获取全文" in warning for warning in result.warnings))

    def test_peer_reviewed_fallback_is_labelled(self):
        """When no OA guideline full text exists, real reviews are labelled as such."""
        search_payload = {
            "resultList": {
                "result": [
                    {
                        "id": "40962511",
                        "pmid": "40962511",
                        "pmcid": "PMC12447934",
                        "doi": "10.7499/j.issn.1008-8830.2412164",
                        "title": "Guideline for neonatal infectious pneumonia (2025)",
                        "journalTitle": "Zhongguo Dang Dai Er Ke Za Zhi",
                        "pubYear": "2025",
                        "firstPublicationDate": "2025-09-01",
                        "isOpenAccess": "N",
                        "abstractText": "Abstract: neonatal pneumonia diagnosis.",
                        "pubType": "practice guideline",
                    }
                ]
            }
        }
        review_payload = {
            "resultList": {
                "result": [
                    {
                        "id": "99999999",
                        "pmid": "99999999",
                        "pmcid": "PMC99999999",
                        "doi": "10.1000/review",
                        "title": "Lung ultrasound for neonatal pneumonia: a systematic review",
                        "journalTitle": "Review Journal",
                        "pubYear": "2026",
                        "firstPublicationDate": "2026-01-01",
                        "isOpenAccess": "Y",
                        "abstractText": "Abstract: review.",
                        "pubType": "systematic review",
                    }
                ]
            }
        }

        class FallbackTransport(FakeTransport):
            def __call__(self, url, params, timeout):
                self.urls.append(url)
                if "search?" in url:
                    return json.dumps(
                        review_payload if "systematic+review" in url or "systematic%20review" in url else search_payload,
                        ensure_ascii=False,
                    ).encode("utf-8")
                if "fullTextXML" in url:
                    return FULLTEXT_XML
                raise AssertionError(f"unexpected URL: {url}")

        connector = LiveGuidelineFullTextConnector(transport=FallbackTransport())
        result = connector.search(_plan(), limit=20)
        review_chunks = [
            doc for doc in result.documents
            if doc.metadata.get("evidence_type") == "systematic_review"
        ]
        self.assertTrue(review_chunks)
        self.assertTrue(all(doc.metadata.get("fallback_for_missing_guideline") for doc in review_chunks))
        self.assertTrue(any("同行评议全文" in warning for warning in result.warnings))

    def test_fulltext_failure_falls_back_to_metadata(self):
        connector = LiveGuidelineFullTextConnector(transport=FakeTransport(fail_fulltext=True))
        result = connector.search(_plan(), limit=20)
        self.assertTrue(any("全文获取失败" in warning for warning in result.warnings))
        self.assertTrue(any(doc.pmid == "41802129" for doc in result.documents))

    def test_doi_question_targets_the_record(self):
        connector = LiveGuidelineFullTextConnector(transport=FakeTransport())
        plan = _plan("请给我 10.1159/000551062 的指南原文")
        self.assertEqual(connector._build_query(plan), 'DOI:"10.1159/000551062"')

    def test_chunking_respects_max_chars(self):
        chunks = _chunk_text("a" * 3000, 1200)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(chunk) <= 1200 for chunk in chunks))

    def test_parse_rejects_entity_declarations(self):
        raw = b'<?xml version="1.0"?><!DOCTYPE article [<!ENTITY x "y">]><article><body><sec><p>&x;</p></sec></body></article>'
        with self.assertRaises(GuidelineFullTextError):
            _parse_jats(raw, {}, ("CPAP",), 5, 500)

    def test_parse_accepts_plain_external_doctype(self):
        raw = b'<?xml version="1.0"?><!DOCTYPE article PUBLIC "-//NLM//DTD JATS//EN" "https://example.invalid/jats.dtd">' + FULLTEXT_XML.split(b"?>", 1)[1]
        documents, _ = _parse_jats(raw, {}, ("CPAP",), 5, 500)
        self.assertTrue(documents)

    def test_pipeline_consumes_fulltext_chunks(self):
        registry = ConnectorRegistry()
        registry.register(LiveGuidelineFullTextConnector(transport=FakeTransport()))
        pipeline = EvidenceRetrievalPipeline(registry)
        request = RetrievalRequest.from_dict({
            "question": "respiratory distress syndrome CPAP surfactant recommendation",
            "source_kinds": ["guideline"],
            "english_terms": ["respiratory distress syndrome", "CPAP", "pulmonary surfactant"],
            "mesh_terms": ["Respiratory Distress Syndrome, Newborn"],
            "source_mode": "live",
            "top_k": 6,
        })
        package = pipeline.retrieve(request)
        self.assertTrue(package.citations)
        self.assertTrue(any(
            item.document.metadata.get("content_mode") == "full_text"
            for item in package.ranked_documents
        ))
        self.assertTrue(any("不是开放获取全文" in warning for warning in package.warnings))


class TopicSelectionTests(unittest.TestCase):
    def test_topic_phrase_prefers_disease_over_population(self):
        from rag.guideline_fulltext import _topic_phrase
        plan = QueryPlan(
            "q", ("q",), (), ("guideline-fulltext-live",),
            {"source_kinds": ["guideline"]}, (),
            english_terms=("neonatal hypoglycemia", "blood glucose", "low birth weight infant"),
            mesh_terms=("Hypoglycemia", "Infant, Newborn"),
        )
        self.assertEqual(_topic_phrase(plan), "neonatal hypoglycemia")

    def test_default_chunk_cap_limits_single_article_dominance(self):
        self.assertEqual(LiveGuidelineFullTextConnector().max_chunks_per_article, 3)


if __name__ == "__main__":
    unittest.main()
