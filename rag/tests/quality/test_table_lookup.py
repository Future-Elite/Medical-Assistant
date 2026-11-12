"""Guideline-capability tests for the table/curve operator.

AAP 2022 is used only because it is the encoded guideline that requires a
table lookup; this is not a disease-specific test subset.
"""
from __future__ import annotations

import unittest

from rag.quality.applicability import assess_applicability
from rag.eval.qrels.gold_rules import criterion_gold
from rag.eval.qrels.rubric import build_documents
from rag.eval.qrels.v2_fixture import load_v2_fixture
from rag.his import snapshot_from_dict


class TableLookupTests(unittest.TestCase):
    def test_table_lookup_gold_is_unknown(self):
        criterion = {
            "criterion_id": "aap-bili-table",
            "field": "derived_labs.max_tbil_umol_l",
            "operator": "table_lookup",
            "value": "AAP 2022 hour-specific threshold",
            "review_status": "synthetic_fixture",
            "source": {"locator": "AAP 2022 Figures 1-2"},
        }
        result = criterion_gold(criterion, {"labs": {"max_tbil_umol_l": 200}})
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason_code"], "table_lookup_required")

    def test_engine_table_lookup_is_unknown(self):
        snapshot = snapshot_from_dict({
            "patient_ref": "h1", "as_of": "2026-01-08", "facts": [
                {"collection": "derived_birth", "fields": {
                    "ga_days": 250, "ga_days_range": [245, 251], "ga_precision": "week",
                    "birth_weight_g": 3000, "data_status": "observed_positive",
                }},
                {"collection": "derived_labs", "fields": {
                    "max_tbil_umol_l": 200, "data_status": "observed_positive",
                }},
            ],
        }, default_source="test")
        documents = build_documents(load_v2_fixture())
        result = assess_applicability(documents["pdf-aap-hyperbilirubinemia-2022"], snapshot, [])
        self.assertEqual(result.status, "unknown")
        self.assertIn("查表", result.reason)
