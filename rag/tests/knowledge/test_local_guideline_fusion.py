from __future__ import annotations

import unittest

from rag.core.his import snapshot_from_dict
from rag.knowledge.local_guideline_fusion import (
    fuse_local_guidelines,
    select_local_guidelines,
)


class FakeGuidelineSource:
    def _rows(self):
        return {
            "g-rds": {
                "title": "RDS 指南",
                "chunks": [
                    {
                        "chunk_id": "c-rds-1",
                        "text": "推荐使用 CPAP 作为早产儿 RDS 呼吸窘迫的初始呼吸支持；胎龄小于 32 周应评估。",
                        "section_path": ["推荐"],
                        "page_start": 2,
                    },
                    {
                        "chunk_id": "c-rds-2",
                        "text": "推荐肺表面活性物质用于 RDS 治疗。",
                        "section_path": ["推荐"],
                        "page_start": 3,
                    },
                ],
            },
            "g-pocus": {
                "title": "POCUS 指南",
                "chunks": [
                    {
                        "chunk_id": "c-pocus-1",
                        "text": "床旁超声用于评估新生儿肺部病变。",
                        "section_path": ["推荐"],
                        "page_start": 5,
                    }
                ],
            },
        }

    def list_guidelines(self):
        return [
            {"document_id": key, "title": value["title"]}
            for key, value in self._rows().items()
        ]

    def chunks(self, document_id):
        return list(self._rows()[document_id]["chunks"])


def _library():
    return {
        "schema_version": "guideline-graph-library",
        "graph_version": "test",
        "generated_at": "2026-11-17",
        "guidelines": [
            {
                "guideline_id": "g-rds",
                "title": "RDS 指南",
                "nodes": [
                    {"id": "guideline:g-rds", "type": "guideline", "title": "RDS 指南"},
                    {
                        "id": "rec:g-rds:1",
                        "type": "recommendation",
                        "statement": "早产儿 RDS 伴呼吸窘迫可使用 CPAP。",
                        "source": {"chunk_id": "c-rds-1"},
                    },
                    {
                        "id": "cond:g-rds:1",
                        "type": "condition",
                        "field": "ga_days",
                        "raw_text": "胎龄小于 32 周",
                    },
                    {"id": "chunk:c-rds-1", "type": "chunk", "text": "CPAP 是早产儿 RDS 呼吸窘迫的初始呼吸支持。"},
                ],
                "edges": [
                    {"source": "rec:g-rds:1", "target": "cond:g-rds:1", "type": "requires"},
                    {"source": "rec:g-rds:1", "target": "chunk:c-rds-1", "type": "supported_by"},
                ],
            },
            {
                "guideline_id": "g-pocus",
                "title": "POCUS 指南",
                "nodes": [
                    {
                        "id": "rec:g-pocus:1",
                        "type": "recommendation",
                        "statement": "床旁超声用于评估肺部病变。",
                        "source": {"chunk_id": "c-pocus-1"},
                    },
                    {"id": "chunk:c-pocus-1", "type": "chunk", "text": "床旁超声用于评估新生儿肺部病变。"},
                ],
                "edges": [],
            },
        ],
    }


def _snapshot():
    return snapshot_from_dict({
        "patient_ref": "synthetic-local-fusion",
        "as_of": "2026-01-08",
        "records": [{
            "record_id": "derived:ehr:birth:1",
            "record_type": "derived_birth",
            "event_type": "DERIVED_BIRTH",
            "fields": {
                "ga_days": 210,
                "birth_weight_g": 1200,
                "data_status": "observed_positive",
            },
            "provenance": {"record_path": "derived:ehr:birth:1", "field_path": "derived:ehr:birth:1"},
        }, {
            "record_id": "derived:ehr:respiratory_support:1",
            "record_type": "derived_respiratory_support",
            "event_type": "DERIVED_RESPIRATORY_SUPPORT",
            "fields": {
                "mode": "经鼻 CPAP",
                "fio2": 0.25,
                "data_status": "observed_positive",
            },
            "provenance": {
                "record_path": "derived:ehr:respiratory_support:1",
                "field_path": "derived:ehr:respiratory_support:1",
            },
        }],
    })


class LocalGuidelineFusionTests(unittest.TestCase):
    def test_selection_returns_top_chunks_and_guidelines(self):
        selection = select_local_guidelines(FakeGuidelineSource(), ["CPAP", "超声"], top_guidelines=2)
        self.assertEqual(selection["stats"]["guidelines_selected"], 2)
        self.assertEqual(
            {row["guideline_id"] for row in selection["guidelines"]},
            {"g-rds", "g-pocus"},
        )
        rds = next(row for row in selection["guidelines"] if row["guideline_id"] == "g-rds")
        self.assertEqual(rds["chunks"][0]["chunk_id"], "c-rds-1")

    def test_fusion_returns_chain_compatible_recommendations(self):
        result = fuse_local_guidelines(
            FakeGuidelineSource(), _snapshot(), ["CPAP", "超声", "早产儿", "呼吸窘迫"],
            library=_library(), top_guidelines=2, limit=4,
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["guidelines"]), 2)
        self.assertTrue(result["recommendations"])
        row = result["recommendations"][0]
        self.assertEqual(row["source_ids"], ["g-rds"])
        self.assertEqual(row["review_status"], "extracted")
        self.assertEqual(row["applicability"]["population"], "unknown")
        self.assertTrue(row["patient_graph_evidence"])
        self.assertTrue(any(
            item["record_path"] == "derived:ehr:birth:1"
            for item in row["patient_graph_evidence"]
        ))
        self.assertGreater(result["fusion"]["stats"]["forward_hits"], 0)
        self.assertGreater(result["fusion"]["stats"]["recommendations_with_patient_evidence"], 0)

    def test_target_guideline_is_kept_even_when_query_scores_another(self):
        result = fuse_local_guidelines(
            FakeGuidelineSource(), _snapshot(), ["超声", "POCUS"],
            library=_library(), top_guidelines=2, limit=4,
            target_guideline_id="g-rds",
        )
        ids = [row["guideline_id"] for row in result["guidelines"]]
        self.assertIn("g-rds", ids)
        self.assertIn("g-pocus", ids)

    def test_missing_selected_graph_is_built_from_chunks(self):
        library = _library()
        library["guidelines"] = [
            graph for graph in library["guidelines"] if graph["guideline_id"] != "g-rds"
        ]
        result = fuse_local_guidelines(
            FakeGuidelineSource(), _snapshot(), ["CPAP", "早产儿"],
            library=library, top_guidelines=2, limit=4,
            target_guideline_id="g-rds",
        )
        self.assertEqual(result["status"], "partial")
        self.assertIn("g-rds", result["diagnostics"]["built_on_demand"])
        self.assertTrue(any(row["guideline_id"] == "g-rds" for row in result["recommendations"]))

    def test_no_match_is_explicit_no_results(self):
        result = fuse_local_guidelines(
            FakeGuidelineSource(), _snapshot(), ["完全无关的词"],
            library=_library(), top_guidelines=2, limit=4,
        )
        self.assertEqual(result["status"], "no_results")
        self.assertEqual(result["recommendations"], [])
        self.assertIn("不是阴性", result["fusion"]["boundary"])


if __name__ == "__main__":
    unittest.main()
