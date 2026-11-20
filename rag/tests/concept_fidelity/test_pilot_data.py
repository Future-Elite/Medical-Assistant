"""Pilot data contract: every concept, gold doc and corpus tag is resolvable."""
from __future__ import annotations

from pathlib import Path
import unittest

from rag.concept_fidelity.harness import FrozenCorpus
from rag.concept_fidelity.mesh import load_concept_index
from rag.concept_fidelity.queries import load_query_set

RAG_ROOT = Path(__file__).resolve().parents[2]
LEXICON = RAG_ROOT / "demo" / "mesh_lexicon_neonatal_2026.json"
QUERIES = RAG_ROOT / "concept_fidelity" / "data" / "pilot_queries_2026-11-20.jsonl"
CORPUS = RAG_ROOT / "concept_fidelity" / "data" / "pilot_corpus_2026-11-20.jsonl"


class PilotDataContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = load_concept_index(str(LEXICON))
        cls.queries = load_query_set(QUERIES, cls.index)
        cls.corpus = FrozenCorpus.load_jsonl(CORPUS, index=cls.index)

    def test_all_target_and_confusable_concepts_exist(self):
        known = set(self.index.by_ui)
        for query in self.queries:
            self.assertTrue(set(query.target_ids) <= known, query.query_id)
            self.assertTrue(set(query.confusable_concepts) <= known, query.query_id)

    def test_gold_docs_exist_and_are_tagged_with_target(self):
        for query in self.queries:
            for doc_id in query.gold_doc_ids:
                self.assertIn(doc_id, self.corpus.by_id, query.query_id)
                document = self.corpus.by_id[doc_id]
                self.assertTrue(set(document.concept_ids) & set(query.target_ids), query.query_id)

    def test_confusable_docs_exist_in_corpus(self):
        corpus_concepts = {concept for document in self.corpus.documents for concept in document.concept_ids}
        for query in self.queries:
            self.assertTrue(set(query.confusable_concepts) <= corpus_concepts, query.query_id)

    def test_pilot_corpus_is_explicitly_synthetic(self):
        self.assertTrue(all(document.synthetic for document in self.corpus.documents))
        self.assertTrue(all(query.pilot for query in self.queries))
        self.assertTrue(all(query.not_for_clinical_use for query in self.queries))


if __name__ == "__main__":
    unittest.main()
