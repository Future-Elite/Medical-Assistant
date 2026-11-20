"""Frozen query-set loading: duplicate IDs and unknown concepts fail loudly."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from rag.concept_fidelity.mesh import load_concept_index
from rag.concept_fidelity.queries import load_query_set

RAG_ROOT = Path(__file__).resolve().parents[2]
LEXICON = RAG_ROOT / "demo" / "mesh_lexicon_neonatal_2026.json"
QUERIES = RAG_ROOT / "concept_fidelity" / "data" / "pilot_queries_2026-11-20.jsonl"


class QuerySetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = load_concept_index(str(LEXICON))

    def test_pilot_query_set_loads(self):
        queries = load_query_set(QUERIES, self.index)
        self.assertEqual(len(queries), 14)
        self.assertTrue(all(query.pilot for query in queries))
        self.assertTrue(all(query.not_for_clinical_use for query in queries))
        self.assertTrue(all(query.target_concepts for query in queries))

    def test_duplicate_query_id_rejected(self):
        row = json.loads(QUERIES.read_text(encoding="utf-8").splitlines()[0])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queries.jsonl"
            path.write_text(
                json.dumps(row, ensure_ascii=False) + "\n" + json.dumps(row, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_query_set(path, self.index)

    def test_unknown_concept_rejected(self):
        row = json.loads(QUERIES.read_text(encoding="utf-8").splitlines()[0])
        row["target_concepts"][0]["concept_id"] = "D999999"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queries.jsonl"
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_query_set(path, self.index)

    def test_overlapping_target_and_confusable_rejected(self):
        row = json.loads(QUERIES.read_text(encoding="utf-8").splitlines()[0])
        row["confusable_concepts"] = [row["target_concepts"][0]["concept_id"]]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queries.jsonl"
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_query_set(path, self.index)


if __name__ == "__main__":
    unittest.main()
