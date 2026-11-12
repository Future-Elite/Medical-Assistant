"""Interface-contract tests for the extra applicability workflow.

These tests use hand-built synthetic snapshots only.  They are not dataset
evaluation and must not be reported as model/data metrics.
"""
from __future__ import annotations

import unittest

from rag.eval.qrels.applicability_resolver import ApplicabilityResolver
from rag.quality.guideline_lifecycle_index import DEFAULT_CACHE_PATH

# 仓库级 data/ 不随发布分支分发；缺生命周期缓存时跳过依赖真实缓存的用例。
_LIFECYCLE_CACHE = DEFAULT_CACHE_PATH.is_file()


def _snapshot(*, ga=None, birth_weight=None, respiratory=None):
    birth = {}
    if ga is not None:
        birth.update({
            "ga_days": ga, "ga_days_range": [ga, ga], "ga_precision": "day",
            "ga_weeks": ga // 7, "ga_extra_days": ga % 7,
        })
    if birth_weight is not None:
        birth["birth_weight_g"] = birth_weight
        birth["birth_weight_status"] = "observed_positive"
    if birth:
        birth["data_status"] = "observed_positive"
    facts = []
    if birth:
        facts.append({"collection": "derived_birth", "fields": birth})
    if respiratory is not None:
        facts.append({
            "collection": "derived_respiratory_support",
            "fields": {
                "mode": respiratory,
                "data_status": "observed_negative" if respiratory == "无" else "observed_positive",
                "mapping_status": "mapped_negative" if respiratory == "无" else "mapped",
            },
        })
    return {"patient_ref": "p-resolver", "as_of": "2024-01-01", "facts": facts}


def _aap_snapshot(*, ga=None, bilirubin=220):
    facts = []
    if ga is not None:
        facts.append({
            "collection": "derived_birth",
            "fields": {"ga_days": ga, "data_status": "observed_positive"},
        })
    if bilirubin is not None:
        facts.append({
            "collection": "derived_labs",
            "fields": {"max_tbil_umol_l": bilirubin, "data_status": "observed_positive"},
        })
    return {"patient_ref": "p-resolver", "as_of": "2024-01-01", "facts": facts}


class ResolverTests(unittest.TestCase):
    def _a(self):
        return _snapshot(ga=224, birth_weight=1800, respiratory="CPAP")

    def _b(self):
        return _snapshot(ga=266, birth_weight=3200)

    def _c(self):
        return _snapshot(ga=224, birth_weight=1400)

    def test_a_resolver_switches_to_same_family_local_guideline(self):
        result = ApplicabilityResolver().resolve(
            question="VLBW feeding", snapshot=self._a(),
            target_guideline_id="pdf-neo-feeding-vlbw-2015",
        )
        self.assertEqual(result["outcome"], "resolved")
        self.assertEqual(result["target"]["status"], "mismatch")
        self.assertIn("pdf-neo-human-milk-fortification-2019", result["selected_guidelines"])
        self.assertEqual(result["rounds"][-1]["action"], "other_registered")
        self.assertEqual(result["target_detail"]["guideline_id"], "pdf-neo-feeding-vlbw-2015")
        self.assertTrue(result["selected_guideline_details"])
        self.assertEqual(result["next_action"]["type"], "review_selected")
        self.assertIn("parallel_retrieval", result)
        self.assertTrue(result["parallel_retrieval"]["status_families"])

    @unittest.skipUnless(_LIFECYCLE_CACHE, "guideline lifecycle cache is not shipped")
    def test_lifecycle_parallel_signal_and_industrial_graph_flag(self):
        result = ApplicabilityResolver().resolve(
            question="VLBW feeding", snapshot=_snapshot(birth_weight=1010),
            target_guideline_id="pdf-neo-feeding-vlbw-2015",
            include_industrial_graph=True,
        )
        parallel = result["parallel_retrieval"]
        self.assertEqual(parallel["lifecycle_status"], "current")
        self.assertEqual(parallel["lifecycle_source"], "pubmed_lifecycle_cache")
        self.assertEqual(
            parallel["lifecycle"]["pdf-neo-feeding-vlbw-2015"]["pmid"], "25580815"
        )
        graph = result["industrial_graph"]
        self.assertEqual(graph["guideline_id"], "pdf-neo-feeding-vlbw-2015")
        self.assertEqual(graph["scope"]["status"], "match")
        self.assertTrue(graph["not_clinical_advice"])
        self.assertTrue(graph["recommendations"])
        self.assertIn("claim_adjudication", graph)
        self.assertTrue(graph["claim_adjudication"]["claims"])

    def test_c_resolver_requests_fact_then_resumes(self):
        resolver = ApplicabilityResolver()
        first = resolver.resolve(
            question="POCUS", snapshot=self._c(),
            target_guideline_id="pdf-neo-pocus-espnic-2020",
        )
        self.assertEqual(first["outcome"], "needs_information")
        self.assertTrue(first["information_requests"])
        self.assertEqual(first["next_action"]["type"], "supply_fact")
        self.assertTrue(first["target_detail"]["criteria"])
        top = first["information_requests"][0]
        second = resolver.resume(first["resolution_id"], [{"field": top["field"], "value": "CPAP"}])
        self.assertTrue(second["resumed"])
        self.assertEqual(second["outcome"], "resolved")
        self.assertEqual(second["target"]["status"], "possible_match")
        self.assertEqual(second["stop_reason"], "target_or_same_family_coverage")

    def test_b_without_backend_is_scope_mismatch_not_fake_evidence(self):
        result = ApplicabilityResolver().resolve(
            question="parenteral nutrition", snapshot=self._b(),
            target_guideline_id="pdf-neo-parenteral-nutrition-2012",
        )
        self.assertEqual(result["outcome"], "scope_mismatch")
        self.assertTrue(result["failed_criteria"])
        self.assertEqual(result["evidence"], [])
        self.assertIn("not clinical inapplicability", result["reason"])
        self.assertTrue(result["target_detail"]["criteria"])
        self.assertIn("guideline_locators", result["provenance"])

    def test_b_with_backend_executes_and_screens_external_search(self):
        queries = []

        def fake_search(query):
            queries.append(query)
            return [
                {"title": "Preterm parenteral nutrition guideline", "abstract": "preterm infants parenteral nutrition", "pmid": "1"},
                {"title": "Unrelated feeding paper", "abstract": "term infants", "pmid": "2"},
            ]

        result = ApplicabilityResolver(search_fn=fake_search).resolve(
            question="parenteral nutrition", snapshot=self._b(),
            target_guideline_id="pdf-neo-parenteral-nutrition-2012",
        )
        self.assertEqual(result["outcome"], "scope_mismatch")
        self.assertEqual(len(queries), 1)
        self.assertEqual(len(result["evidence"]), 1)
        self.assertEqual(result["evidence"][0]["pmid"], "1")
        self.assertEqual(result["rounds"][-1]["action"], "external_search")

    def test_d_topic_gap_executes_search_or_reports_gap(self):
        def fake_search(query):
            return [{"title": "Neonatal hyperbilirubinemia guideline", "abstract": "jaundice in infants", "pmid": "35927462"}]

        found = ApplicabilityResolver(search_fn=fake_search).resolve(
            question="jaundice", snapshot=self._b(), target_topic="hyperbilirubinemia",
        )
        self.assertEqual(found["outcome"], "external_evidence")
        self.assertIsNone(found["target"]["guideline_id"])
        self.assertEqual(found["evidence"][0]["pmid"], "35927462")
        self.assertTrue(found["external_search"]["executed"])
        self.assertEqual(found["external_search"]["candidate_count"], 1)
        missing = ApplicabilityResolver().resolve(
            question="jaundice", snapshot=self._b(), target_topic="hyperbilirubinemia",
        )
        self.assertEqual(missing["outcome"], "evidence_gap")
        self.assertIn("search_backend_not_provided", missing["rounds"][-1]["reason"])

    def test_aap_table_lookup_stops_before_external_search(self):
        calls = []
        result = ApplicabilityResolver(search_fn=lambda query: calls.append(query) or []).resolve(
            question="bilirubin", snapshot=_aap_snapshot(ga=252),
            target_guideline_id="pdf-aap-hyperbilirubinemia-2022",
        )
        self.assertEqual(result["outcome"], "needs_table_lookup")
        self.assertEqual(result["lookup_fields"], ["derived_labs.max_tbil_umol_l"])
        self.assertTrue(result["lookup_locators"])
        self.assertTrue(result["lookup"]["required"])
        self.assertEqual(result["next_action"]["type"], "table_lookup")
        self.assertEqual(calls, [])

    def test_aap_missing_fact_resumes_to_table_lookup(self):
        resolver = ApplicabilityResolver()
        first = resolver.resolve(
            question="bilirubin", snapshot=_aap_snapshot(ga=None),
            target_guideline_id="pdf-aap-hyperbilirubinemia-2022",
        )
        self.assertEqual(first["outcome"], "needs_information")
        self.assertEqual(first["information_requests"][0]["field"], "derived_birth.ga_days")
        second = resolver.resume(
            first["resolution_id"], [{"field": "derived_birth.ga_days", "value": 252}],
        )
        self.assertEqual(second["outcome"], "needs_table_lookup")

    def test_unknown_target_guideline_is_rejected(self):
        with self.assertRaises(ValueError):
            ApplicabilityResolver().resolve(
                question="x", snapshot=self._a(),
                target_guideline_id="pdf-does-not-exist",
            )

    def test_unsupported_resume_field_is_rejected(self):
        resolver = ApplicabilityResolver()
        first = resolver.resolve(
            question="POCUS", snapshot=self._c(),
            target_guideline_id="pdf-neo-pocus-espnic-2020",
        )
        with self.assertRaises(ValueError):
            resolver.resume(first["resolution_id"], [{"field": "unknown.field", "value": 1}])

    def test_unknown_resolution_id_is_rejected(self):
        with self.assertRaises(ValueError):
            ApplicabilityResolver().resume("resolution-missing", [])


if __name__ == "__main__":
    unittest.main()
