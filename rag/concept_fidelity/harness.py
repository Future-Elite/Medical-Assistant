"""Zero-shot retrieval harness for concept-fidelity diagnostics.

The harness is deliberately offline and model-agnostic:

* ``BM25Retriever`` is a deterministic lexical baseline over a frozen corpus;
* ``DenseRetriever`` accepts any caller-provided encoder (``encode`` callable),
  so real BGE-M3 / e5 / MedCPT runs stay outside the unit-test environment;
* metrics are concept-bound and return ``None`` for empty denominators.

It never downloads a model, never calls a network, and never claims clinical
validity.  The pilot corpus is synthetic by construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from ..retrieval.bm25 import tokenize
from .mesh import ConceptIndex, load_concept_index
from .metrics import aggregate_metrics, concept_identity_summary, ranking_metrics
from .probes import build_sibling_probes
from .queries import ConceptQuery, load_query_set
from .schema import ConceptIdentity


@dataclass(frozen=True)
class CorpusDocument:
    doc_id: str
    text: str
    concept_ids: tuple[str, ...]
    title: str = ""
    synthetic: bool = True

    def __post_init__(self) -> None:
        if not str(self.doc_id or "").strip():
            raise ValueError("doc_id must be non-empty")
        if not str(self.text or "").strip():
            raise ValueError(f"{self.doc_id}: text must be non-empty")
        if not self.concept_ids:
            raise ValueError(f"{self.doc_id}: concept_ids must be non-empty")
        object.__setattr__(self, "concept_ids", tuple(str(item) for item in self.concept_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "text": self.text,
            "concept_ids": list(self.concept_ids),
            "synthetic": self.synthetic,
        }


class FrozenCorpus:
    def __init__(self, documents: Sequence[CorpusDocument]) -> None:
        self.documents = tuple(documents)
        if not self.documents:
            raise ValueError("corpus must contain at least one document")
        self.by_id = {document.doc_id: document for document in self.documents}
        if len(self.by_id) != len(self.documents):
            raise ValueError("corpus contains duplicate doc_id values")
        self.concepts_by_doc = {
            document.doc_id: frozenset(document.concept_ids) for document in self.documents
        }

    @classmethod
    def load_jsonl(cls, path: str | Path, *, index: ConceptIndex | None = None) -> "FrozenCorpus":
        documents: list[CorpusDocument] = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, Mapping):
                raise ValueError("corpus row must be a JSON object")
            raw_concepts = payload.get("concept_ids") or ()
            if not isinstance(raw_concepts, (list, tuple)):
                raise ValueError("concept_ids must be an array")
            concepts = tuple(str(item).strip() for item in raw_concepts if str(item).strip())
            if index is not None:
                unknown = [item for item in concepts if item not in index.by_ui]
                if unknown:
                    raise ValueError("unknown corpus MeSH descriptor(s): " + ", ".join(unknown))
            documents.append(CorpusDocument(
                doc_id=str(payload.get("doc_id") or "").strip(),
                title=str(payload.get("title") or "").strip(),
                text=str(payload.get("text") or "").strip(),
                concept_ids=concepts,
                synthetic=bool(payload.get("synthetic", True)),
            ))
        return cls(documents)

    def target_doc_count(self, target_ids: Sequence[str]) -> int:
        wanted = frozenset(target_ids)
        return sum(1 for document in self.documents if wanted & set(document.concept_ids))


@dataclass(frozen=True)
class RetrievalHit:
    doc_id: str
    score: float


class Retriever(Protocol):
    def search(self, query: str, k: int) -> tuple[RetrievalHit, ...]:
        """Return at most ``k`` hits, best first; zero-score hits are omitted."""


class BM25Retriever:
    """Deterministic field-free BM25 over the frozen corpus."""

    def __init__(self, corpus: FrozenCorpus, *, k1: float = 1.2, b: float = 0.75) -> None:
        self.corpus = corpus
        self.k1 = k1
        self.b = b
        self._tokens = {
            document.doc_id: tokenize(" ".join((document.title, document.text)))
            for document in corpus.documents
        }
        lengths = [len(tokens) for tokens in self._tokens.values()]
        self._avg_length = sum(lengths) / max(1, len(lengths))
        self._df: dict[str, int] = {}
        for tokens in self._tokens.values():
            for token in set(tokens):
                self._df[token] = self._df.get(token, 0) + 1
        total = max(1, len(self._tokens))
        self._idf = {
            token: math.log(1.0 + (total - count + 0.5) / (count + 0.5))
            for token, count in self._df.items()
        }

    def search(self, query: str, k: int) -> tuple[RetrievalHit, ...]:
        query_tokens = tuple(dict.fromkeys(tokenize(query)))
        if not query_tokens or k <= 0:
            return ()
        scored: list[RetrievalHit] = []
        for doc_id, tokens in self._tokens.items():
            length = len(tokens) or 1
            counts: dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            score = 0.0
            for token in query_tokens:
                tf = counts.get(token, 0)
                if not tf:
                    continue
                saturation = (tf * (self.k1 + 1.0)) / (
                    tf + self.k1 * (1.0 - self.b + self.b * length / max(self._avg_length, 1.0))
                )
                score += self._idf.get(token, 0.0) * saturation
            if score > 0.0:
                scored.append(RetrievalHit(doc_id, score))
        scored.sort(key=lambda hit: (-hit.score, hit.doc_id))
        return tuple(scored[:k])


class DenseRetriever:
    """Cosine top-k over caller-encoded texts.

    ``encoder`` must expose ``encode(texts: Sequence[str]) -> Sequence[Sequence[float]]``.
    Vectors are L2-normalized; dimension mismatches fail loudly.
    """

    def __init__(self, corpus: FrozenCorpus, encoder: Any) -> None:
        self.corpus = corpus
        self.encoder = encoder
        texts = [" ".join((document.title, document.text)) for document in corpus.documents]
        vectors = self._normalize(self.encoder.encode(texts))
        if len(vectors) != len(corpus.documents):
            raise ValueError("encoder returned a different number of document vectors")
        self._doc_vectors = {
            document.doc_id: vector
            for document, vector in zip(corpus.documents, vectors)
        }

    @staticmethod
    def _normalize(vectors: Sequence[Sequence[float]]) -> list[list[float]]:
        rows: list[list[float]] = []
        for vector in vectors:
            values = [float(item) for item in vector]
            norm = math.sqrt(sum(item * item for item in values)) or 1.0
            rows.append([item / norm for item in values])
        return rows

    def search(self, query: str, k: int) -> tuple[RetrievalHit, ...]:
        if k <= 0:
            return ()
        vector = self._normalize(self.encoder.encode([query]))[0]
        scored: list[RetrievalHit] = []
        for doc_id, doc_vector in self._doc_vectors.items():
            if len(doc_vector) != len(vector):
                raise ValueError(
                    f"query/document vector dimension mismatch: {len(vector)} vs {len(doc_vector)}"
                )
            score = sum(left * right for left, right in zip(vector, doc_vector))
            scored.append(RetrievalHit(doc_id, score))
        scored.sort(key=lambda hit: (-hit.score, hit.doc_id))
        return tuple(scored[:k])


def _query_text(query: ConceptQuery, route: str) -> str:
    conversion = query.conversion_for_route(route)
    if conversion is not None:
        return conversion.query_converted
    if route == "reviewed_mapping":
        return query.reviewed_english
    if route == "zh_direct":
        return query.query_zh
    raise ValueError(f"unknown evaluation route: {route}")


def evaluate_query_set(
    corpus: FrozenCorpus,
    queries: Sequence[ConceptQuery],
    retriever: Retriever,
    *,
    route: str,
    k: int = 10,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    identities: list[ConceptIdentity] = []
    warnings: list[str] = []
    for query in queries:
        text = _query_text(query, route)
        if not text:
            warnings.append(f"{query.query_id}: route {route} has no query text; skipped")
            continue
        hits = retriever.search(text, k)
        ranked_ids = [hit.doc_id for hit in hits]
        confusable = query.confusable_concepts
        if query.gold_doc_ids:
            missing = [doc_id for doc_id in query.gold_doc_ids if doc_id not in corpus.by_id]
            if missing:
                raise ValueError(f"{query.query_id}: gold_doc_ids not in corpus: {', '.join(missing)}")
            total_target = len(query.gold_doc_ids)
        else:
            total_target = corpus.target_doc_count(query.target_ids)
        metrics = ranking_metrics(
            ranked_ids,
            corpus.concepts_by_doc,
            target_ids=query.target_ids,
            confusable_ids=confusable,
            total_target_docs=total_target,
            k=k,
        )
        if not hits:
            warnings.append(f"{query.query_id}: route {route} returned no positive-score hits")
        conversion = query.conversion_for_route(route)
        if conversion is not None and conversion.identities:
            identities.extend(conversion.identities)
        else:
            for concept in query.target_concepts:
                if route == "reviewed_mapping":
                    identities.append(ConceptIdentity(
                        slot=concept.slot,
                        source_surface=concept.surface,
                        target_concept_id=concept.concept_id,
                        converted_concept_id=concept.concept_id,
                        label="same_concept",
                        evidence="pilot reviewed mapping (upper bound)",
                    ))
                else:
                    identities.append(ConceptIdentity(
                        slot=concept.slot,
                        source_surface=concept.surface,
                        target_concept_id=concept.concept_id,
                        label="not_checked",
                        evidence=f"no explicit conversion product for route {route}",
                    ))
        rows.append({
            "query_id": query.query_id,
            "query_text": text,
            "target_ids": list(query.target_ids),
            "confusable_ids": list(confusable),
            "gold_doc_count": total_target,
            "metrics": metrics,
        })
    return {
        "route": route,
        "k": k,
        "query_count": len(rows),
        "metrics": aggregate_metrics([row["metrics"] for row in rows]),
        "concept_identity": concept_identity_summary(identities),
        "queries": rows,
        "warnings": warnings,
    }


def run_phase0(
    *,
    query_path: str | Path,
    corpus_path: str | Path,
    lexicon_path: str | Path,
    retriever: Retriever | None = None,
    k: int = 10,
    probe_limit: int = 20,
) -> dict[str, Any]:
    index = load_concept_index(str(lexicon_path))
    queries = load_query_set(query_path, index)
    corpus = FrozenCorpus.load_jsonl(corpus_path, index=index)
    active = retriever or BM25Retriever(corpus)
    probes = build_sibling_probes(index, max_probes=probe_limit)
    routes: dict[str, Any] = {}
    route_names = {"reviewed_mapping", "zh_direct"}
    for query in queries:
        route_names.update(conversion.route for conversion in query.conversions)
    for route in sorted(route_names):
        if any(_query_text(query, route) for query in queries):
            routes[route] = evaluate_query_set(corpus, queries, active, route=route, k=k)
    warnings: list[str] = []
    if not any(query.reviewed_english for query in queries):
        warnings.append("no reviewed English conversion; only direct-query diagnostics available")
    if not any(probe.query_zh for probe in probes):
        warnings.append("no sibling probe has a reviewed Chinese surface; probes are English-only")
    report = {
        "benchmark": "concept-fidelity-pilot",
        "benchmark_version": "v0.1",
        "pilot": True,
        "not_clinical": True,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mesh": index.describe(),
        "query_count": len(queries),
        "corpus_document_count": len(corpus.documents),
        "corpus_synthetic_documents": sum(1 for document in corpus.documents if document.synthetic),
        "probes": {
            "count": len(probes),
            "chinese_probe_count": sum(1 for probe in probes if probe.query_zh),
            "examples": [probe.to_dict() for probe in probes[:5]],
        },
        "routes": routes,
        "warnings": warnings,
    }
    return report
