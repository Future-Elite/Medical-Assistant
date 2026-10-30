import unittest
from pathlib import Path

from guideline_processing.evaluation import _ndcg, _reciprocal_rank, _resolve_gold, load_queries


class RetrievalMetricTests(unittest.TestCase):
    def test_reusable_pilot_dataset_has_complete_schema(self):
        dataset = Path(__file__).resolve().parents[2] / "data" / "guideline_eval_pilot.jsonl"
        rows = load_queries(dataset)
        self.assertGreaterEqual(len(rows), 10)
        self.assertEqual(rows[0]["benchmark"]["version"], "2.1.0")
        self.assertTrue(all(row.get("gold_evidence") for row in rows))

    def test_reciprocal_rank_uses_first_relevant_result(self):
        self.assertEqual(_reciprocal_rank({"b", "d"}, ["a", "b", "d"]), 0.5)
        self.assertEqual(_reciprocal_rank({"b"}, ["a", "c"]), 0.0)

    def test_binary_ndcg_normalizes_against_ideal_order(self):
        self.assertEqual(_ndcg({"a", "b"}, ["a", "b"], 2), 1.0)
        self.assertLess(_ndcg({"a", "b"}, ["x", "a", "b"], 3), 1.0)
        self.assertEqual(_ndcg(set(), ["a"], 1), 0.0)

    def test_anchor_gold_resolves_against_changed_chunk_ids(self):
        row = {
            "gold_evidence": [{
                "guide_title": "新生儿肺炎指南",
                "section_contains": "抗感染",
                "anchor_terms": ["经验性抗生素", "药敏"],
                "element_type": "text",
            }]
        }
        documents = [{
            "document_id": "completely-new-chunk-id",
            "title": "新生儿肺炎指南 [completely-new-chunk-id]",
            "text": "依据经验性抗生素方案治疗，并根据药敏结果调整。",
            "metadata": {"guide_title": "新生儿肺炎指南", "section_path": ["抗感染治疗"]},
        }]
        relevant, diagnostics = _resolve_gold(row, "new", documents)
        self.assertEqual(relevant, {"completely-new-chunk-id"})
        self.assertTrue(diagnostics[0]["matched"])

    def test_anchor_gold_rejects_wrong_section_and_reports_unmatched(self):
        row = {
            "gold_evidence": [{
                "guide_title": "新生儿肺炎指南",
                "section_contains": "抗感染",
                "anchor_terms": ["经验性抗生素", "药敏"],
            }]
        }
        documents = [{
            "document_id": "wrong-section",
            "title": "新生儿肺炎指南 [wrong-section]",
            "text": "经验性抗生素方案结合药敏调整。",
            "metadata": {"guide_title": "新生儿肺炎指南", "section_path": ["呼吸支持"]},
        }]
        relevant, diagnostics = _resolve_gold(row, "new", documents)
        self.assertEqual(relevant, set())
        self.assertFalse(diagnostics[0]["matched"])


if __name__ == "__main__":
    unittest.main()
