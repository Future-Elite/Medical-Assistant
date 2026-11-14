"""v6.7.1 MeSH entity linking: real lexicon, normalization, ambiguity, shadow/active."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from rag.quality.mesh_linking import (
    MeshDescriptor,
    MeshLexicon,
    MeshLinker,
    MeshTerm,
    apply_entity_linking,
    extract_abbreviations,
    normalize_text,
    normalized_variants,
)
from rag.models import RetrievalRequest
from rag.retrieval.query import build_query_plan
from rag.tests.helpers import fixture_pipeline

LEXICON = Path(__file__).resolve().parents[2] / "demo" / "mesh_lexicon_neonatal_2026.json"
KNOWN_SOURCES = [
    {"source_id": "guideline-fixture", "kinds": ["guideline"]},
    {"source_id": "pubmed-fixture", "kinds": ["pubmed"]},
]


class NormalizationTests(unittest.TestCase):
    def test_unicode_case_british_and_greek(self) -> None:
        self.assertEqual(
            normalize_text("Paediatric Oedema & α-Haemolysis, 38.9°C"),
            "pediatric edema and alpha haemolysis 38 9 c",
        )

    def test_plural_variants_are_conservative(self) -> None:
        variants = normalized_variants("newborn infants")
        self.assertIn("newborn infant", variants)
        self.assertNotIn("newborn infant", normalized_variants("newborn infantis"))

    def test_schwartz_hearst_abbreviation_both_directions(self) -> None:
        found = extract_abbreviations(
            "Neonatal respiratory distress syndrome (NRDS) is common. "
            "RDS (respiratory distress syndrome) is treated with surfactant."
        )
        self.assertEqual(found["NRDS"], "Neonatal respiratory distress syndrome")
        self.assertEqual(found["RDS"], "respiratory distress syndrome")


class RealLexiconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lexicon = MeshLexicon.load(LEXICON)
        cls.linker = MeshLinker(cls.lexicon)

    def test_lexicon_provenance_and_size(self) -> None:
        self.assertGreaterEqual(len(self.lexicon.descriptors), 700)
        self.assertGreaterEqual(len(self.lexicon.reviewed_aliases), 50)
        self.assertIn("NLM MeSH", self.lexicon.source)
        self.assertEqual(self.lexicon.mesh_year, "2026")

    def test_exact_label_and_entry_term(self) -> None:
        dysplasia = self.linker.link("bronchopulmonary dysplasia")
        self.assertEqual(dysplasia.status, "linked")
        self.assertEqual(dysplasia.best.ui, "D001997")
        self.assertEqual(dysplasia.best.method, "exact_label")

        jaundice = self.linker.link("neonatal jaundice")
        self.assertEqual(jaundice.status, "linked")
        self.assertEqual(jaundice.best.ui, "D007567")
        self.assertEqual(jaundice.best.method, "exact_entry_term")

    def test_reviewed_alias_and_abbreviation_context(self) -> None:
        alias = self.linker.link("RDS")
        self.assertEqual(alias.status, "linked")
        self.assertEqual(alias.best.ui, "D012127")
        self.assertEqual(alias.best.method, "reviewed_alias")

        contextual = self.linker.link("NRDS", context="neonatal respiratory distress syndrome (NRDS) is common")
        self.assertEqual(contextual.status, "linked")
        self.assertEqual(contextual.best.ui, "D012127")
        self.assertEqual(contextual.expanded_from, "neonatal respiratory distress syndrome")

    def test_chinese_without_english_product_is_skipped_not_translated(self) -> None:
        result = self.linker.link("新生儿呼吸窘迫综合征")
        self.assertEqual(result.status, "skipped_no_english_surface")
        self.assertIn("不自动翻译", result.reason or "")

    def test_verify_mapping_verified_conflict_and_skipped(self) -> None:
        verified = self.linker.verify_mapping(
            "新生儿呼吸窘迫综合征",
            ["Neonatal Respiratory Distress Syndrome"],
            ["Respiratory Distress Syndrome, Newborn"],
        )
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["linked_uis"], ["D012127"])

        conflict = self.linker.verify_mapping(
            "混合概念", ["Phototherapy"], ["Respiratory Distress Syndrome, Newborn"],
        )
        self.assertEqual(conflict["status"], "conflict")

        skipped = self.linker.verify_mapping("只有中文", [], [])
        self.assertEqual(skipped["status"], "skipped_no_english_surface")

    def test_unlinked_garbage_does_not_claim_a_descriptor(self) -> None:
        result = self.linker.link("zzzz not a mesh concept")
        self.assertEqual(result.status, "unlinked")
        self.assertIsNone(result.best)


class AmbiguityTests(unittest.TestCase):
    def test_equal_candidates_are_ambiguous_not_silently_chosen(self) -> None:
        lexicon = MeshLexicon([
            MeshDescriptor("D000001", "Shared Term", (MeshTerm("T1", "shared term"),)),
            MeshDescriptor("D000002", "Shared Term Two", (MeshTerm("T2", "shared term"),)),
        ], source="unit-test")
        linker = MeshLinker(lexicon)
        result = linker.link("shared term")
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual({candidate.ui for candidate in result.candidates}, {"D000001", "D000002"})


class QueryPlanIntegrationTests(unittest.TestCase):
    def _plan(self):
        return build_query_plan(
            RetrievalRequest(request_id="test", question="NRDS 早产儿使用肺表面活性物质吗？"),
            KNOWN_SOURCES,
        )

    @classmethod
    def setUpClass(cls) -> None:
        cls.linker = MeshLinker(MeshLexicon.load(LEXICON))

    def test_shadow_records_links_without_changing_plan(self) -> None:
        plan = self._plan()
        linked, trace = apply_entity_linking(plan, self.linker, mode="shadow", context=plan.original_question)
        self.assertEqual(linked, plan)
        self.assertEqual(trace["mode"], "shadow")
        self.assertTrue(trace["links"])
        self.assertTrue(any(row["status"] == "linked" for row in trace["links"]))

    def test_active_adds_official_mesh_label_and_audits_origin(self) -> None:
        plan = self._plan()
        linked, trace = apply_entity_linking(plan, self.linker, mode="active", context=plan.original_question)
        self.assertIn("Respiratory Distress Syndrome, Newborn", linked.mesh_terms)
        self.assertTrue(any(row.get("origin") == "entity_linker" for row in linked.mapping_trace))
        self.assertTrue(trace["added_terms"])

    def test_active_does_not_expand_a_conflicting_mapping_row(self) -> None:
        request = RetrievalRequest.from_dict({
            "request_id": "conflict",
            "question": "新生儿高胆红素血症如何处理？",
            "term_mapping": [{
                "concept": "新生儿高胆红素血症",
                "aliases": ["新生儿黄疸"],
                "english": ["neonatal jaundice"],
                "mesh": ["Hyperbilirubinemia, Neonatal"],
            }],
        })
        plan = build_query_plan(request, KNOWN_SOURCES)
        linked, trace = apply_entity_linking(plan, self.linker, mode="active", context=plan.original_question)
        verification = trace["mapping_verifications"][0]
        self.assertEqual(verification["status"], "conflict")
        self.assertNotIn("Jaundice, Neonatal", linked.mesh_terms)
        added = {
            term for group in trace["added_terms"] for term in group.get("terms", ())
        }
        self.assertNotIn("Jaundice, Neonatal", added)
        self.assertNotIn("Hyperbilirubinemia, Neonatal", added)

    def test_off_is_byte_for_byte_legacy(self) -> None:
        plan = self._plan()
        linked, trace = apply_entity_linking(plan, self.linker, mode="off")
        self.assertIs(linked, plan)
        self.assertEqual(trace["links"], [])

    def test_pipeline_without_linker_has_no_entity_trace(self) -> None:
        package = fixture_pipeline().retrieve(RetrievalRequest(request_id="legacy", question="新生儿呼吸窘迫综合征治疗"))
        self.assertNotIn("entity_linking", package.trace)

    def test_pipeline_active_exposes_trace(self) -> None:
        pipeline = fixture_pipeline(entity_linker=self.linker, entity_linking_mode="active")
        package = pipeline.retrieve(RetrievalRequest(request_id="active", question="NRDS 早产儿使用肺表面活性物质吗？"))
        self.assertIn("entity_linking", package.trace)
        self.assertEqual(package.trace["entity_linking"]["mode"], "active")


if __name__ == "__main__":
    unittest.main()
