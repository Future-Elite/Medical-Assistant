from __future__ import annotations

import unittest

from rag.evidence import extract_key_evidence_sentences, split_sentence_spans


class TestEvidenceSentences(unittest.TestCase):
    def test_spans_round_trip_each_selected_sentence(self) -> None:
        text = "第一句有新生儿肺炎。Surfactant reduces risk. 背景句。"
        spans = split_sentence_spans(text)
        self.assertEqual([item[2] for item in spans], ["第一句有新生儿肺炎。", "Surfactant reduces risk.", "背景句。"])
        for start, end, sentence in spans:
            self.assertEqual(text[start:end], sentence)

    def test_selection_is_query_matched_and_deterministic(self) -> None:
        text = "背景句。新生儿肺炎诊断需要综合评估。Neonatal pneumonia diagnosis needs assessment."
        first = extract_key_evidence_sentences("doc", text, ("新生儿肺炎诊断", "neonatal pneumonia diagnosis"), budget=2)
        second = extract_key_evidence_sentences("doc", text, ("新生儿肺炎诊断", "neonatal pneumonia diagnosis"), budget=2)
        self.assertEqual(first, second)
        self.assertEqual([item.sentence_index for item in first], [1, 2])
        self.assertTrue(all(text[item.start:item.end] == item.text for item in first))

    def test_no_overlap_returns_empty(self) -> None:
        self.assertEqual(extract_key_evidence_sentences("doc", "完全无关的背景。", ("neonatal pneumonia",)), ())


if __name__ == "__main__":
    unittest.main()
