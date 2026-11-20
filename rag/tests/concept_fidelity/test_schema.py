"""ConceptState / ConceptIdentity validation: no silent coercion."""
from __future__ import annotations

import unittest

from rag.concept_fidelity.schema import (
    ConceptIdentity,
    ConceptState,
    ConversionProduct,
)


class ConceptStateTests(unittest.TestCase):
    def test_resolved_requires_concept_id(self):
        with self.assertRaises(ValueError):
            ConceptState(slot="disease", surface="新生儿黄疸", status="resolved")

    def test_ambiguous_requires_two_candidates(self):
        with self.assertRaises(ValueError):
            ConceptState(
                slot="disease", surface="黄疸", status="ambiguous",
                ambiguity_set=("D051556",),
            )

    def test_unresolved_must_not_carry_concept_id(self):
        with self.assertRaises(ValueError):
            ConceptState(
                slot="disease", surface="黄疸", status="unresolved",
                concept_id="D051556",
            )

    def test_confidence_range(self):
        with self.assertRaises(ValueError):
            ConceptState(slot="disease", surface="黄疸", concept_id="D051556",
                         status="resolved", confidence=1.2)

    def test_to_dict_round_trip_shape(self):
        state = ConceptState(
            slot="disease", surface="新生儿呼吸窘迫综合征", concept_id="D012127",
            confidence=0.9, status="resolved", source="reviewed_mapping",
        )
        self.assertEqual(state.to_dict()["concept_id"], "D012127")
        self.assertEqual(state.to_dict()["status"], "resolved")


class ConceptIdentityTests(unittest.TestCase):
    def test_same_concept_requires_equal_ids(self):
        with self.assertRaises(ValueError):
            ConceptIdentity(
                slot="disease", source_surface="黄疸",
                target_concept_id="D051556", converted_concept_id="D007567",
                label="same_concept",
            )

    def test_not_checked_must_not_carry_converted_id(self):
        with self.assertRaises(ValueError):
            ConceptIdentity(
                slot="disease", source_surface="黄疸",
                target_concept_id="D051556", converted_concept_id="D051556",
                label="not_checked",
            )

    def test_drift_label_accepts_different_id(self):
        identity = ConceptIdentity(
            slot="disease", source_surface="新生儿高胆红素血症",
            target_concept_id="D051556", converted_concept_id="D007567",
            label="hyponym_drift",
        )
        self.assertEqual(identity.label, "hyponym_drift")

    def test_dropped_must_not_carry_converted_id(self):
        with self.assertRaises(ValueError):
            ConceptIdentity(
                slot="disease", source_surface="黄疸",
                target_concept_id="D051556", converted_concept_id="D007567",
                label="dropped",
            )

    def test_fabricated_requires_non_target_id(self):
        with self.assertRaises(ValueError):
            ConceptIdentity(
                slot="disease", source_surface="黄疸",
                target_concept_id="D051556", label="fabricated",
            )


class ConversionProductTests(unittest.TestCase):
    def test_unknown_route_rejected(self):
        state = ConceptState(slot="disease", surface="黄疸", concept_id="D051556",
                             status="resolved")
        with self.assertRaises(ValueError):
            ConversionProduct(
                route="unknown", query_zh="新生儿黄疸", query_converted="neonatal jaundice",
                concepts=(state,),
            )

    def test_identity_slot_must_exist(self):
        state = ConceptState(slot="disease", surface="黄疸", concept_id="D051556",
                             status="resolved")
        identity = ConceptIdentity(slot="drug", source_surface="x",
                                   target_concept_id="D051556",
                                   converted_concept_id="D051556",
                                   label="same_concept")
        with self.assertRaises(ValueError):
            ConversionProduct(
                route="reviewed_mapping", query_zh="新生儿黄疸",
                query_converted="neonatal jaundice", concepts=(state,),
                identities=(identity,),
            )


if __name__ == "__main__":
    unittest.main()
