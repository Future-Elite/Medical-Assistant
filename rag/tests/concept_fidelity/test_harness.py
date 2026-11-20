"""Offline harness: BM25, injected dense encoder and pilot report flags."""
from __future__ import annotations

from pathlib import Path
import unittest

from rag.concept_fidelity.harness import (
    BM25Retriever,
    CorpusDocument,
    DenseRetriever,
    FrozenCorpus,
    evaluate_query_set,
    run_phase0,
)
from rag.concept_fidelity.mesh import load_concept_index
from rag.concept_fidelity.queries import ConceptQuery
from rag.concept_fidelity.schema import (
    ConceptIdentity,
    ConceptState,
    ConversionProduct,
)

RAG_ROOT = Path(__file__).resolve().parents[2]
LEXICON = RAG_ROOT / "demo" / "mesh_lexicon_neonatal_2026.json"
QUERIES = RAG_ROOT / "concept_fidelity" / "data" / "pilot_queries_2026-11-20.jsonl"
CORPUS = RAG_ROOT / "concept_fidelity" / "data" / "pilot_corpus_2026-11-20.jsonl"


def _query() -> ConceptQuery:
    return ConceptQuery(
        query_id="q-test",
        query_zh="目标疾病怎么处理？",
        reviewed_english="target alpha management",
        target_concepts=(ConceptState(
            slot="disease", surface="目标疾病", concept_id="D1",
            confidence=1.0, status="resolved",
        ),),
        confusable_concepts=("D2",),
        gold_doc_ids=("doc-target",),
    )


def _corpus() -> FrozenCorpus:
    return FrozenCorpus([
        CorpusDocument("doc-target", "target alpha management", ("D1",)),
        CorpusDocument("doc-confusable", "confusable beta feature", ("D2",)),
        CorpusDocument("doc-other", "unrelated gamma text", ("D3",)),
    ])


class StubEncoder:
    def encode(self, texts):
        rows = []
        for text in texts:
            lowered = str(text).lower()
            if "target" in lowered:
                rows.append([1.0, 0.0])
            elif "confusable" in lowered:
                rows.append([0.0, 1.0])
            else:
                rows.append([0.5, 0.5])
        return rows


class HarnessTests(unittest.TestCase):
    def test_bm25_reviewed_route_finds_target(self):
        result = evaluate_query_set(
            _corpus(), [_query()], BM25Retriever(_corpus()),
            route="reviewed_mapping", k=3,
        )
        self.assertEqual(result["queries"][0]["metrics"]["ranked"][0], "doc-target")
        self.assertEqual(result["metrics"]["sibling_discrimination"], 1.0)

    def test_bm25_chinese_route_returns_no_lexical_hits(self):
        result = evaluate_query_set(
            _corpus(), [_query()], BM25Retriever(_corpus()),
            route="zh_direct", k=3,
        )
        self.assertEqual(result["queries"][0]["metrics"]["ranked"], [])
        self.assertIsNone(result["metrics"]["sibling_discrimination"])

    def test_dense_retriever_accepts_injected_encoder(self):
        retriever = DenseRetriever(_corpus(), StubEncoder())
        hits = retriever.search("target alpha", 2)
        self.assertEqual(hits[0].doc_id, "doc-target")
        self.assertGreater(hits[0].score, hits[1].score)

    def test_conversion_product_route_uses_declared_identities(self):
        query = ConceptQuery(
            query_id="q-conversion",
            query_zh="目标疾病怎么处理？",
            target_concepts=(ConceptState(
                slot="disease", surface="目标疾病", concept_id="D1",
                confidence=1.0, status="resolved",
            ),),
            confusable_concepts=("D2",),
            conversions=(ConversionProduct(
                route="machine_translation",
                query_zh="目标疾病怎么处理？",
                query_converted="confusable beta",
                concepts=(ConceptState(
                    slot="disease", surface="目标疾病", concept_id="D2",
                    confidence=0.9, status="resolved",
                ),),
                identities=(ConceptIdentity(
                    slot="disease", source_surface="目标疾病",
                    target_concept_id="D1", converted_concept_id="D2",
                    label="hyponym_drift",
                ),),
            ),),
        )
        result = evaluate_query_set(
            _corpus(), [query], BM25Retriever(_corpus()),
            route="machine_translation", k=3,
        )
        self.assertEqual(result["queries"][0]["metrics"]["ranked"][0], "doc-confusable")
        self.assertEqual(result["concept_identity"]["drift_rate"], 1.0)
        self.assertEqual(result["concept_identity"]["concept_identity_accuracy"], 0.0)

    def test_phase0_report_is_pilot_and_not_clinical(self):
        report = run_phase0(
            query_path=QUERIES,
            corpus_path=CORPUS,
            lexicon_path=LEXICON,
            k=5,
        )
        self.assertTrue(report["pilot"])
        self.assertTrue(report["not_clinical"])
        self.assertIn("reviewed_mapping", report["routes"])
        self.assertIn("zh_direct", report["routes"])
        self.assertGreater(
            report["routes"]["reviewed_mapping"]["metrics"]["entity_correct_recall"],
            report["routes"]["zh_direct"]["metrics"]["entity_correct_recall"],
        )


if __name__ == "__main__":
    unittest.main()
