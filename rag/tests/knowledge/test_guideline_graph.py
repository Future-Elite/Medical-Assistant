"""Guideline graph builder tests (structure extraction, not clinical review)."""
from __future__ import annotations

import unittest

from rag.knowledge.guideline_graph import (
    build_guideline_graph,
    build_guideline_library,
    validate_library,
)


def _chunk(chunk_id: str, text: str, *, section=(), page=1) -> dict:
    return {
        "chunk_id": chunk_id, "text": text, "section_path": list(section),
        "page_start": page, "page_end": page, "source_file": "guide.pdf",
        "source_title": "Guide", "element_type": "text",
    }


class GuidelineGraphTests(unittest.TestCase):
    def test_extracts_recommendation_condition_exception_and_timeframe(self):
        graph = build_guideline_graph([
            _chunk("c1", "推荐意见 1：对出生体重 <1500 g 的早产儿，应在生后 24 h 内开始喂养。禁忌：肠梗阻。"),
            _chunk("c2", "Author information: someone@example.com"),
            _chunk("c3", "参考文献 [1] [2]", section=("References",)),
        ], {"document_id": "g1", "title": "Guide 1", "version_id": "2025"})
        self.assertEqual(graph["stats"]["recommendations"], 1)
        self.assertEqual(graph["stats"]["chunks"], 1)
        rec = next(node for node in graph["nodes"] if node["type"] == "recommendation")
        self.assertIn("出生体重", rec["statement"])
        condition = next(node for node in graph["nodes"] if node["type"] == "condition")
        self.assertEqual(condition["field"], "derived_birth.birth_weight_g")
        self.assertEqual(condition["operator"], "lt")
        self.assertEqual(condition["value"], 1500)
        self.assertEqual(condition["unit"], "g")
        exception = next(node for node in graph["nodes"] if node["type"] == "exception")
        self.assertEqual(exception["kind"], "hard")
        timeframe = next(node for node in graph["nodes"] if node["type"] == "timeframe")
        self.assertEqual(timeframe["unit"], "hour")
        self.assertEqual(graph["validation"]["errors"], [])
        self.assertFalse(graph["publishable"])
        self.assertEqual(graph["review_status"], "extracted")

    def test_meta_chunk_is_not_a_recommendation(self):
        graph = build_guideline_graph([
            _chunk("c1", "Abstract Background: this guideline represents recommendations."),
            _chunk("c2", "作者信息：通信作者 Email: x@example.com"),
        ], {"document_id": "g2", "title": "Guide 2"})
        self.assertEqual(graph["stats"]["recommendations"], 0)

    def test_library_relations_only_use_existing_nodes(self):
        graph_a = build_guideline_graph([
            _chunk("a1", "推荐意见 1：应使用 CPAP。"),
        ], {"document_id": "a", "title": "A", "topic_keys": ["RDS", "CPAP"]})
        graph_b = build_guideline_graph([
            _chunk("b1", "推荐意见 1：应使用 CPAP。"),
        ], {"document_id": "b", "title": "B", "topic_keys": ["RDS", "CPAP"]})
        library = build_guideline_library([graph_a, graph_b])
        self.assertEqual(validate_library(library), [])
        self.assertTrue(any(row["type"] == "shares_topic" for row in library["relations"]))
        self.assertEqual(library["stats"]["guidelines"], 2)

    def test_dangling_edge_is_reported(self):
        graph = build_guideline_graph([
            _chunk("c1", "推荐意见 1：应使用 CPAP。"),
        ], {"document_id": "g3", "title": "Guide 3"})
        graph["edges"].append({"source": "missing", "target": "guideline:g3", "type": "x"})
        library = build_guideline_library([graph])
        self.assertTrue(any("dangling edge" in item for item in library["validation"]["errors"]))


if __name__ == "__main__":
    unittest.main()
