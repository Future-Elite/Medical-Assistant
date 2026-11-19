from __future__ import annotations

import copy
import unittest

from rag.knowledge.applicability_bridge import attach_fusion_to_applicability


def _result() -> dict:
    return {
        "outcome": "needs_information",
        "stop_reason": "missing_fact_can_change_target",
        "next_action": {"type": "supply_fact", "text": "补充胎龄后继续"},
        "target_detail": {
            "guideline_id": "pdf-neo-feeding-vlbw-2015",
            "title": "极低出生体重儿喂养指南（2015）",
            "status": "unknown",
            "criteria": [
                {
                    "criterion_id": "birth-weight",
                    "field": "derived_birth.birth_weight_g",
                    "label": "出生体重",
                    "status": "unknown",
                    "record_paths": [],
                },
                {
                    "criterion_id": "resp-support",
                    "field": "derived_respiratory_support.mode",
                    "label": "呼吸支持",
                    "status": "match",
                    "record_paths": ["derived:ehr:respiratory_support:2001"],
                },
            ],
        },
        "selected_guideline_details": [],
        "information_requests": [{"field": "birth_weight_g", "label": "出生体重"}],
    }


def _fusion() -> dict:
    return {
        "retrieval_id": "retrieval-test",
        "knowledge_context_id": "knowledge-test",
        "knowledge_release_id": "DEMO-RELEASE-NOT-FOR-PRODUCTION",
        "release_sha256": "a" * 64,
        "fusion": {
            "forward": {"hits": 2, "seeds": ["concept:ga"]},
            "backward": {"hits": 3, "queries": ["出生体重", "胎龄"]},
            "stats": {
                "forward_hits": 2,
                "backward_hits": 3,
                "recommendations_with_patient_evidence": 1,
                "reference_coverage": 1.0,
            },
        },
        "recommendations": [
            {
                "recommendation_id": "recommendation:DEMO-FEED",
                "summary": "DEMO：极低出生体重儿喂养",
                "direction": "support",
                "review_status": "synthetic_fixture",
                "source_ids": ["pdf-neo-feeding-vlbw-2015"],
                "applicability": {
                    "population": "met",
                    "indication": "unknown",
                    "exclusion": "unknown",
                },
                "missing_information": ["临床指征仍有未知项"],
                "fusion": {"coverage": 1.0},
                "patient_graph_evidence": [
                    {
                        "record_path": "derived:ehr:birth:2001",
                        "record_type": "derived_birth",
                        "event_type": "DERIVED_BIRTH",
                        "status": "observed_positive",
                        "origin": "backward",
                        "concept_ids": ["concept:birth_weight", "concept:ga"],
                        "graph_path": ["patient:2001 -[HAS_RECORD]-> record:derived:ehr:birth:2001"],
                    },
                    {
                        "record_path": "derived:ehr:respiratory_support:2001",
                        "record_type": "derived_respiratory_support",
                        "event_type": "DERIVED_RESPIRATORY_SUPPORT",
                        "status": "observed_positive",
                        "origin": "forward",
                        "concept_ids": ["concept:respiratory_support"],
                        "graph_path": ["patient:2001 -[HAS_RECORD]-> record:derived:ehr:respiratory_support:2001"],
                    }
                ],
            }
        ],
    }


class ApplicabilityBridgeTests(unittest.TestCase):
    def test_no_fusion_is_a_noop(self):
        result = _result()
        self.assertEqual(attach_fusion_to_applicability(result, None), result)
        self.assertEqual(attach_fusion_to_applicability(result, {}), result)

    def test_graph_evidence_attaches_without_changing_status_or_outcome(self):
        result = _result()
        enriched = attach_fusion_to_applicability(result, _fusion())
        self.assertEqual(enriched["outcome"], result["outcome"])
        self.assertEqual(enriched["stop_reason"], result["stop_reason"])
        criteria = enriched["target_detail"]["criteria"]
        self.assertEqual(criteria[0]["status"], "unknown")
        self.assertEqual(criteria[0]["graph_support"], "same_record_type")
        self.assertEqual(criteria[0]["graph_record_paths"], ["derived:ehr:birth:2001"])
        self.assertEqual(criteria[0]["graph_origin"], "backward")
        self.assertEqual(criteria[1]["graph_support"], "same_record")
        self.assertEqual(enriched["knowledge_fusion"]["criteria_with_graph_support"], 2)
        self.assertEqual(
            enriched["knowledge_fusion"]["graph_unknown_dimensions"],
            ["exclusion", "indication"],
        )

    def test_candidates_and_next_action_are_marked_for_review(self):
        enriched = attach_fusion_to_applicability(_result(), _fusion())
        self.assertEqual(len(enriched["graph_candidates"]), 1)
        candidate = enriched["graph_candidates"][0]
        self.assertEqual(candidate["recommendation_id"], "recommendation:DEMO-FEED")
        self.assertEqual(candidate["patient_record_paths"], ["derived:ehr:birth:2001", "derived:ehr:respiratory_support:2001"])
        action = enriched["next_action"]
        self.assertEqual(action["type"], "supply_fact")
        self.assertEqual(action["graph_candidate_count"], 1)
        self.assertEqual(action["graph_candidates_with_patient_evidence"], 1)
        self.assertEqual(action["graph_action"], "review_graph_candidates")
        self.assertIn("不是已判定适用", action["graph_text"])
        self.assertIn("不改变条件状态", enriched["knowledge_fusion"]["boundary"])

    def test_input_is_not_mutated(self):
        result = _result()
        before = copy.deepcopy(result)
        attach_fusion_to_applicability(result, _fusion())
        self.assertEqual(result, before)


if __name__ == "__main__":
    unittest.main()
