from __future__ import annotations

import json
import unittest

from rag.core.models import QueryPlan
from rag.sources.drug_labels import DrugLabelConnector


def _plan(**overrides):
    values = {
        "original_question": "surfactant dosing",
        "variants": ("surfactant",),
        "concepts": ("surfactant",),
        "source_order": ("drug",),
        "filters": {},
        "rationale": (),
        "english_terms": ("surfactant",),
        "mesh_terms": (),
        "question_english_terms": (),
    }
    values.update(overrides)
    return QueryPlan(**values)


class DrugLabelConnectorTests(unittest.TestCase):
    def test_maps_openfda_label_to_source_document(self):
        payload = {
            "meta": {"results": {"skip": 0, "limit": 5, "total": 1}},
            "results": [{
                "id": "abc-123",
                "effective_time": "20240131",
                "openfda": {
                    "generic_name": ["SURFACTANT"],
                    "brand_name": ["TEST BRAND"],
                    "manufacturer_name": ["TEST MANUFACTURER"],
                    "route": ["RESPIRATORY (INHALATION)"],
                    "set_id": ["set-123"],
                },
                "indications_and_usage": ["For neonatal respiratory distress syndrome."],
                "dosage_and_administration": ["Use as directed by a clinician."],
                "warnings": ["Not a prescription."],
                "description": ["Test label only."],
            }],
        }
        seen = {}
        def transport(url, timeout):
            seen["url"] = url
            return json.dumps(payload).encode("utf-8")
        connector = DrugLabelConnector(transport=transport)
        result = connector.search(_plan(), limit=3)
        self.assertEqual(result.source_id, "drug-openfda")
        self.assertEqual(len(result.documents), 1)
        document = result.documents[0]
        self.assertEqual(document.kind, "drug")
        self.assertEqual(document.document_id, "openfda:abc-123")
        self.assertIn("SURFACTANT", document.title)
        self.assertIn("indications_and_usage", document.text)
        self.assertEqual(document.published_at, "2024-01-31")
        self.assertEqual(document.authority, "FDA")
        self.assertEqual(document.metadata["country"], "US")
        self.assertIn("surfactant", seen["url"])

    def test_chinese_only_query_is_not_sent(self):
        connector = DrugLabelConnector(transport=lambda url, timeout: b"{}")
        result = connector.search(_plan(english_terms=(), question_english_terms=()), limit=3)
        self.assertEqual(result.documents, ())
        self.assertTrue(any("未向 openFDA" in item for item in result.warnings))

    def test_network_error_is_explicit(self):
        def transport(url, timeout):
            raise TimeoutError("timed out")
        connector = DrugLabelConnector(transport=transport)
        result = connector.search(_plan(), limit=3)
        self.assertEqual(result.documents, ())
        self.assertTrue(any("openFDA 请求失败" in item for item in result.warnings))

    def test_empty_results_are_not_negative_evidence(self):
        connector = DrugLabelConnector(
            transport=lambda url, timeout: json.dumps({"results": []}).encode("utf-8")
        )
        result = connector.search(_plan(), limit=3)
        self.assertEqual(result.documents, ())
        self.assertTrue(any("不能据此判断药品不存在" in item for item in result.warnings))


if __name__ == "__main__":
    unittest.main()
