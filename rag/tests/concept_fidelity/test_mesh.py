"""MeSH tree index: descendants, siblings and deterministic confusion sets."""
from __future__ import annotations

import unittest

from rag.concept_fidelity.mesh import ConceptIndex
from rag.quality.mesh_linking import MeshLexicon

DESCRIPTORS = [
    {"ui": "D012127", "label": "Respiratory Distress Syndrome, Newborn",
     "terms": [{"ui": "T1", "text": "Respiratory Distress Syndrome, Newborn"}],
     "tree_numbers": ["C16.614.521.563"]},
    {"ui": "D001997", "label": "Bronchopulmonary Dysplasia",
     "terms": [{"ui": "T2", "text": "Bronchopulmonary Dysplasia"}],
     "tree_numbers": ["C16.614.521.125"]},
    {"ui": "D059245", "label": "Transient Tachypnea of the Newborn",
     "terms": [{"ui": "T3", "text": "Transient Tachypnea of the Newborn"}],
     "tree_numbers": ["C16.614.521.563.737"]},
    {"ui": "D051556", "label": "Hyperbilirubinemia, Neonatal",
     "terms": [{"ui": "T4", "text": "Hyperbilirubinemia, Neonatal"}],
     "tree_numbers": ["C16.614.451"]},
    {"ui": "D007567", "label": "Jaundice, Neonatal",
     "terms": [{"ui": "T5", "text": "Jaundice, Neonatal"}],
     "tree_numbers": ["C16.614.451.500"]},
]


def _index() -> ConceptIndex:
    lexicon = MeshLexicon.from_dict({
        "descriptors": DESCRIPTORS,
        "reviewed_aliases": [
            {"surface": "新生儿呼吸窘迫综合征", "descriptor_ui": "D012127"},
            {"surface": "新生儿高胆红素血症", "descriptor_ui": "D051556"},
        ],
    })
    return ConceptIndex(lexicon)


class ConceptIndexTests(unittest.TestCase):
    def test_descendants_find_hyponym(self):
        index = _index()
        self.assertIn("D059245", index.descendants("D012127"))
        self.assertIn("D007567", index.descendants("D051556"))

    def test_siblings_do_not_include_descendants(self):
        index = _index()
        siblings = index.siblings("D012127")
        self.assertIn("D001997", siblings)
        self.assertNotIn("D059245", siblings)

    def test_confusion_set_prefers_descendants(self):
        index = _index()
        self.assertEqual(index.confusion_set("D012127", size=2)[0], "D059245")
        self.assertEqual(index.confusion_set("D051556", size=2)[0], "D007567")

    def test_chinese_surfaces_come_from_reviewed_aliases(self):
        index = _index()
        self.assertIn("新生儿呼吸窘迫综合征", index.chinese_surfaces("D012127"))

    def test_unknown_descriptor_fails_loudly(self):
        index = _index()
        with self.assertRaises(KeyError):
            index.descendants("D999999")


if __name__ == "__main__":
    unittest.main()
