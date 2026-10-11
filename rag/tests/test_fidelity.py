"""Query-side fidelity audit tests (query fidelity research layer).

Offline and deterministic: no models, no network. They pin the labelled
contract of ``rag/fidelity.py`` — slot extraction, per-route audits, level
mapping — and deliberately do NOT assert on medical correctness beyond the
lexicon/suffix rules the baseline implements (quality vs human annotation is
an experiment; see the paper's experiment protocol).
"""
import unittest

from rag.fidelity import (
    FidelityLexicon,
    QueryFidelityAuditor,
    TermEntry,
    audit_reviewed_mapping,
    audit_translation,
    extract_slots,
)


def _lexicon() -> FidelityLexicon:
    return FidelityLexicon([
        TermEntry("高血压", ("hypertension",), "disease"),
        TermEntry("二甲双胍", ("metformin",), "drug"),
        TermEntry("新生儿黄疸", ("neonatal jaundice",), "disease"),
        TermEntry("哮喘", ("asthma",), "disease"),
    ])


class _Plan:
    """Minimal stand-in for a QueryPlan (only fields the auditor reads)."""

    def __init__(self, question, *, english_terms=(), mesh_terms=(), expansion_trace=()):
        self.original_question = question
        self.english_terms = tuple(english_terms)
        self.mesh_terms = tuple(mesh_terms)
        self.expansion_trace = tuple(expansion_trace)


class LexiconTests(unittest.TestCase):
    def test_from_dict_validates_entries(self):
        lex = FidelityLexicon.from_dict({"entries": [{"zh": "高血压", "en": ["hypertension"], "kind": "disease"}]})
        self.assertEqual(lex.entries[0].zh, "高血压")
        with self.assertRaises(ValueError):
            FidelityLexicon.from_dict({"entries": [{"zh": "", "en": ["x"]}]})
        with self.assertRaises(ValueError):
            FidelityLexicon.from_dict({"entries": [{"zh": "高血压", "en": []}]})
        with self.assertRaises(ValueError):
            FidelityLexicon.from_dict({})

    def test_match_zh_is_longest_first_and_non_overlapping(self):
        lex = _lexicon()
        matches = lex.match_zh("新生儿黄疸与高血压")
        surfaces = [entry.zh for entry, _, _ in matches]
        self.assertIn("新生儿黄疸", surfaces)
        self.assertIn("高血压", surfaces)
        self.assertNotIn("黄疸", surfaces)  # covered by the longer match

    def test_match_english_uses_word_boundaries(self):
        lex = _lexicon()
        hits = [entry.zh for entry, _ in lex.match_english("treated with metformin for asthma")]
        self.assertEqual(sorted(hits), sorted(["哮喘", "二甲双胍"]))
        self.assertEqual(lex.match_english("metforminx"), [])


class SlotExtractionTests(unittest.TestCase):
    def test_extracts_lexicon_reviewed_and_rule_slots(self):
        slots = extract_slots("该患者高血压使用二甲双胍，2月龄，未记录过敏", lexicon=_lexicon())
        by_type = {slot.slot_type: slot for slot in slots}
        self.assertEqual(by_type["disease"].surface, "高血压")
        self.assertEqual(by_type["disease"].expected_en, ("hypertension",))
        self.assertEqual(by_type["drug"].surface, "二甲双胍")
        self.assertEqual(by_type["numeric"].surface, "2月龄")
        self.assertEqual(by_type["negation"].surface, "未记录")
        # spans point back into the question
        for slot in slots:
            self.assertEqual("该患者高血压使用二甲双胍，2月龄，未记录过敏"[slot.start:slot.end][: len(slot.surface)], slot.surface)

    def test_suffix_rule_avoids_non_entity_endings(self):
        slots = extract_slots("新生儿黄疸的光疗指征是什么", lexicon=_lexicon())
        surfaces = [slot.surface for slot in slots]
        self.assertIn("新生儿黄疸", surfaces)
        self.assertNotIn("光疗指征", surfaces)

    def test_reviewed_mapping_key_provides_expected_english(self):
        slots = extract_slots(
            "该患者高血压的用药如何调整？",
            reviewed={"高血压": ["high blood pressure"]},
        )
        disease = next(slot for slot in slots if slot.surface == "高血压")
        self.assertEqual(disease.expected_en, ("high blood pressure",))

    def test_span_determinism(self):
        first = extract_slots("既往有哮喘，最近发作", lexicon=_lexicon())
        second = extract_slots("既往有哮喘，最近发作", lexicon=_lexicon())
        self.assertEqual(first, second)


class ReviewedMappingAuditTests(unittest.TestCase):
    def test_drop_of_primary_entity_is_l3(self):
        slots = extract_slots("高血压使用二甲双胍治疗", lexicon=_lexicon())
        audit = audit_reviewed_mapping(slots, ("hypertension",))
        self.assertEqual(audit.level, "L3")
        drops = [item for item in audit.findings if item.verdict == "suspected_drop"]
        self.assertEqual(drops[0].surface, "二甲双胍")
        self.assertEqual(drops[0].level, "L3")

    def test_numeric_loss_is_l2(self):
        slots = extract_slots("2月龄婴儿发热如何处理", lexicon=_lexicon())
        audit = audit_reviewed_mapping(slots, ("fever",))
        numeric = [item for item in audit.findings if item.verdict == "numeric_loss"]
        self.assertTrue(numeric)
        self.assertEqual(numeric[0].level, "L2")

    def test_unverifiable_reported_without_escalation(self):
        slots = extract_slots("该患者某种未知症状如何处理", lexicon=None)
        audit = audit_reviewed_mapping(slots, ("hypertension",))
        # no expected English for the extracted concept -> unverifiable, L0
        self.assertTrue(all(item.level == "L0" for item in audit.findings))
        self.assertEqual(audit.status, "ok")

    def test_absent_product_is_not_an_error(self):
        audit = audit_reviewed_mapping((), ())
        self.assertEqual(audit.status, "absent")
        self.assertEqual(audit.level, "L0")


class TranslationAuditTests(unittest.TestCase):
    def test_corrupted_entity_becomes_unexpected_entity(self):
        # metformin -> not present; hypertension preserved; "dichotomy" not in lexicon
        slots = extract_slots("高血压使用二甲双胍", lexicon=_lexicon())
        audit = audit_translation(slots, ("The patient takes dichotomy for hypertension.",), _lexicon())
        self.assertEqual(audit.level, "L3")
        verdicts = {item.verdict for item in audit.findings if item.level != "L0"}
        self.assertIn("suspected_drop", verdicts)

    def test_fabricated_lexicon_entity_is_detected(self):
        # translation mentions asthma although the question never did
        slots = extract_slots("高血压使用二甲双胍", lexicon=_lexicon())
        audit = audit_translation(slots, ("Hypertension and asthma management with metformin.",), _lexicon())
        fabricated = [item for item in audit.findings if item.verdict == "unexpected_entity"]
        self.assertTrue(fabricated)
        self.assertEqual(fabricated[0].surface, "哮喘")
        self.assertEqual(fabricated[0].level, "L3")  # disease entity

    def test_negation_loss_is_l3(self):
        slots = extract_slots("未记录药物过敏史", lexicon=_lexicon())
        audit = audit_translation(slots, ("The patient has a history of drug allergy.",), _lexicon())
        negation = [item for item in audit.findings if item.verdict == "suspected_negation_loss"]
        self.assertTrue(negation)
        self.assertEqual(negation[0].level, "L3")

    def test_numeric_loss_and_added(self):
        slots = extract_slots("2月龄婴儿发热", lexicon=_lexicon())
        audit = audit_translation(slots, ("Infant with fever at 6 months of age.",), _lexicon())
        verdicts = {item.verdict for item in audit.findings if item.level != "L0"}
        self.assertIn("numeric_loss", verdicts)
        self.assertIn("numeric_added", verdicts)

    def test_preserved_translation_is_clean(self):
        slots = extract_slots("新生儿黄疸光疗", lexicon=_lexicon())
        audit = audit_translation(slots, ("Phototherapy for neonatal jaundice.",), _lexicon())
        self.assertEqual(audit.level, "L0")
        self.assertEqual(audit.status, "ok")


class RuleCoverageTests(unittest.TestCase):
    """Rule hardening recorded in the paper's method doc (revision R6).

    The gaps below were named in ``深度分析与论文构建_2026-10-11.md`` §2.5:
    unit-localised numbers, English negation equivalents and uneven temporal
    coverage used to slip through the audit.
    """

    def test_unit_localisation_is_tolerated(self):
        slots = extract_slots("2月龄婴儿发热如何处理", lexicon=_lexicon())
        audit = audit_translation(slots, ("Fever in a 2-month-old infant: management.",), _lexicon())
        self.assertNotIn("numeric_loss", {item.verdict for item in audit.findings})
        audit = audit_translation(slots, ("Fever in an infant aged 2 months.",), _lexicon())
        self.assertNotIn("numeric_loss", {item.verdict for item in audit.findings})

    def test_unit_family_conflict_is_flagged(self):
        slots = extract_slots("2月龄婴儿发热如何处理", lexicon=_lexicon())
        audit = audit_translation(slots, ("Fever in an infant given 2 mg of paracetamol.",), _lexicon())
        numeric = [item for item in audit.findings if item.verdict == "numeric_loss"]
        self.assertTrue(numeric)
        self.assertIn("单位族", numeric[0].evidence)

    def test_temperature_unit_localisation_is_tolerated(self):
        slots = extract_slots("体温38.9℃如何处理", lexicon=_lexicon())
        for translation in ("Temperature 38.9°C in an infant.", "Temperature 38.9 degrees Celsius."):
            audit = audit_translation(slots, (translation,), _lexicon())
            self.assertNotIn("numeric_loss", {item.verdict for item in audit.findings}, translation)

    def test_temperature_conflict_is_flagged(self):
        slots = extract_slots("体温38.9℃如何处理", lexicon=_lexicon())
        audit = audit_translation(slots, ("Temperature 38.9 mg in an infant.",), _lexicon())
        self.assertIn("numeric_loss", {item.verdict for item in audit.findings})

    def test_extended_negation_equivalents_are_recognised(self):
        slots = extract_slots("未记录药物过敏史", lexicon=_lexicon())
        for translation in ("No documentation of drug allergy.", "Drug allergy not documented.", "No evidence of drug allergy."):
            audit = audit_translation(slots, (translation,), _lexicon())
            self.assertNotIn("suspected_negation_loss", {item.verdict for item in audit.findings}, translation)
        audit = audit_translation(slots, ("The patient has a documented drug allergy.",), _lexicon())
        self.assertIn("suspected_negation_loss", {item.verdict for item in audit.findings})

    def test_temporal_terms_are_matched_on_word_boundaries(self):
        slots = extract_slots("既往高血压病史如何处理", lexicon=_lexicon())
        # "known" contains "now": substring matching used to mask the loss
        audit = audit_translation(slots, ("The patient is known to have hypertension.",), _lexicon())
        self.assertIn("suspected_temporal_loss", {item.verdict for item in audit.findings})
        audit = audit_translation(slots, ("Past history of hypertension: management.",), _lexicon())
        self.assertNotIn("suspected_temporal_loss", {item.verdict for item in audit.findings})

    def test_negation_markers_stay_aligned_with_query_review(self):
        """审计器的否定标记必须覆盖 query.py 检索式自检所用的全部标记。"""
        from rag import query
        from rag import fidelity

        chinese = [marker for marker in query._NEGATION_MARKERS if not marker.isascii()]
        for marker in chinese:
            self.assertIn(marker, fidelity._NEGATION_MARKERS, marker)
        english = [marker for marker in query._NEGATION_MARKERS if marker.isascii()]
        for marker in english:
            self.assertTrue(fidelity._EN_NEGATION_RE.search(marker), marker)


class AuditorRouteStatusTests(unittest.TestCase):
    def test_route_status_matrix(self):
        auditor = QueryFidelityAuditor(lexicon=_lexicon())
        plan = _Plan("高血压治疗", english_terms=("hypertension",))
        # translate not requested
        report = auditor.audit(plan, translate_requested=False)
        self.assertEqual(report.route("translate").status, "absent")
        self.assertEqual(report.route("multilingual_encoding").status, "absent")
        # requested but unconfigured
        report = auditor.audit(plan, translate_requested=True, translate_configured=False)
        self.assertEqual(report.route("translate").status, "not_configured")
        # requested, configured, no product
        report = auditor.audit(plan, translate_requested=True, translations=())
        self.assertEqual(report.route("translate").status, "no_product")
        # multilingual without explicit terms -> documented no_explicit_conversion
        report = auditor.audit(
            _Plan("高血压治疗", english_terms=(), mesh_terms=()),
            multilingual_methods=("xlingual",),
        )
        self.assertEqual(report.route("multilingual_encoding").status, "no_explicit_conversion")
        # multilingual *with* explicit terms -> audited against question slots
        report = auditor.audit(
            _Plan("高血压治疗", english_terms=("hypertension",)),
            multilingual_methods=("xlingual",),
        )
        self.assertEqual(report.route("multilingual_encoding").status, "ok")

    def test_expansion_trace_reviewed_terms_become_expected_english(self):
        auditor = QueryFidelityAuditor(lexicon=None)
        plan = _Plan(
            "该患者高血压用药如何调整？",
            expansion_trace=({"concept": "高血压", "terms": ["hypertension"], "source": "reviewed_user_terms"},),
        )
        report = auditor.audit(plan, translate_requested=False)
        disease = next(slot for slot in report.slots if slot.surface == "高血压")
        self.assertEqual(disease.expected_en, ("hypertension",))
        reviewed_route = report.route("reviewed_mapping")
        self.assertEqual(reviewed_route.level, "L0")

    def test_report_is_deterministic(self):
        auditor = QueryFidelityAuditor(lexicon=_lexicon())
        plan = _Plan("高血压使用二甲双胍，2月龄", english_terms=("hypertension",))
        first = auditor.audit(plan, translations=("hypertension with metformin",), translate_requested=True).to_dict()
        second = auditor.audit(plan, translations=("hypertension with metformin",), translate_requested=True).to_dict()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
