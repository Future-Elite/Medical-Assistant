"""Fidelity-weighted metric tests (eval harness skeleton, offline)."""
import unittest

from rag.eval.run_fidelity_experiments import (
    ARMS,
    PILOT_ARMS,
    convert_arm_to_request,
    entity_correct_recall,
    gate_cost,
    mrr_at,
    recall_at,
    wrong_entity_contamination,
)


class MetricTests(unittest.TestCase):
    def test_recall_and_mrr(self):
        relevance = {"d1", "d2", "d3"}
        ranked = ["d9", "d1", "d2", "d8", "d3"]
        self.assertAlmostEqual(recall_at(relevance, ranked, 3), 2 / 3)
        self.assertAlmostEqual(recall_at(relevance, ranked, 5), 1.0)
        self.assertAlmostEqual(mrr_at(relevance, ranked, 5), 1 / 2)
        self.assertAlmostEqual(recall_at(set(), ranked, 3), 0.0)

    def test_entity_correct_recall_unlabelled_is_none(self):
        self.assertIsNone(entity_correct_recall({"d1"}, ["d1"], 5, route_entities_correct=None))
        self.assertEqual(entity_correct_recall({"d1"}, ["d1"], 5, route_entities_correct=True), 1.0)
        self.assertEqual(entity_correct_recall({"d1"}, ["d1"], 5, route_entities_correct=False), 0.0)

    def test_wrong_entity_contamination(self):
        doc_topics = {"d1": {"neonatal pneumonia"}, "d2": {"neonatal jaundice"}, "d3": {"neonatal pneumonia"}}
        self.assertAlmostEqual(wrong_entity_contamination("neonatal pneumonia", doc_topics, ["d1", "d2"], 2), 0.5)
        self.assertAlmostEqual(wrong_entity_contamination("neonatal pneumonia", doc_topics, [], 2), 0.0)

    def test_gate_cost(self):
        decisions = [
            {"route": "translate", "action": "reject"},
            {"route": "translate", "action": "pass"},
            {"route": "translate", "action": "warn"},
        ]
        cost = gate_cost(decisions, route="translate")
        self.assertEqual(cost["total"], 3)
        self.assertEqual(cost["gated"], 1)
        self.assertAlmostEqual(cost["gated_share"], 1 / 3)

    def test_arms_map_to_requests(self):
        query = {"query_id": "q1", "zh_question": "问题", "english_terms": ["x"], "mesh_terms": ["Y"]}
        payload = convert_arm_to_request("A1", query)
        self.assertEqual(payload["fidelity_gate"], "off")
        self.assertEqual(payload["english_terms"], ["x"])
        payload = convert_arm_to_request("B3", query)
        self.assertEqual(payload["retrieval_method"], "translate")
        self.assertEqual(payload["fidelity_gate"], "downweight")
        payload = convert_arm_to_request("C2", query)
        self.assertEqual(payload["retrieval_method"], "bm25,xlingual")
        self.assertEqual(payload["fidelity_gate"], "reject")

    def test_arm_matrix_is_complete(self):
        from rag.eval.run_fidelity_experiments import ABLATION_VARIANTS, ORTHOGONALITY_ARMS
        self.assertTrue(set(PILOT_ARMS) <= set(ARMS))
        routes = {spec["route"] for spec in ARMS.values()}
        self.assertEqual(routes, {"reviewed_mapping", "translate", "multilingual_encoding"})
        # v2 臂矩阵：≥15 臂（主臂 + 正交对照），含阈值敏感性与双编码器
        self.assertEqual(len(ARMS), 15)
        self.assertIn("B5", ARMS) and self.assertIn("B6", ARMS)
        self.assertIn("C3", ARMS) and self.assertIn("C4", ARMS)
        self.assertIn("D2", ARMS) and self.assertIn("D3", ARMS)
        self.assertEqual(set(ORTHOGONALITY_ARMS), {"E1", "E2"})
        self.assertEqual(len(ABLATION_VARIANTS), 3)


if __name__ == "__main__":
    unittest.main()
