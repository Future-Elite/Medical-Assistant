from __future__ import annotations

import unittest

from rag.evidence import extract_key_evidence_sentences, split_sentence_spans


class TestEvidenceSentences(unittest.TestCase):
    def test_spans_round_trip_each_selected_sentence(self) -> None:
        text = "第一句有高血压。The treatment reduces risk. 背景句。"
        spans = split_sentence_spans(text)
        self.assertEqual([item[2] for item in spans], ["第一句有高血压。", "The treatment reduces risk.", "背景句。"])
        for start, end, sentence in spans:
            self.assertEqual(text[start:end], sentence)

    def test_selection_is_query_matched_and_deterministic(self) -> None:
        text = "背景句。高血压治疗需要评估风险。Hypertension treatment may reduce risk."
        first = extract_key_evidence_sentences("doc", text, ("高血压治疗", "hypertension treatment"), budget=2)
        second = extract_key_evidence_sentences("doc", text, ("高血压治疗", "hypertension treatment"), budget=2)
        self.assertEqual(first, second)
        self.assertEqual([item.sentence_index for item in first], [1, 2])
        self.assertTrue(all(text[item.start:item.end] == item.text for item in first))

    def test_no_overlap_returns_empty(self) -> None:
        self.assertEqual(extract_key_evidence_sentences("doc", "完全无关的背景。", ("asthma",)), ())


if __name__ == "__main__":
    unittest.main()
