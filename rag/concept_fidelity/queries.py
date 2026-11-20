"""Frozen concept-fidelity query sets (JSONL) with strict validation.

A query set is a research asset, not clinical guidance.  Every row must carry
``not_for_clinical_use: true``; the pilot rows also carry ``pilot: true`` and
``review_status: draft``.  Concept IDs are MeSH descriptors from the local
snapshot, so a typo fails at load time instead of becoming an unresolved probe.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from .mesh import ConceptIndex
from .schema import (
    ConversionProduct,
    ConceptState,
    concept_state_from_dict,
    conversion_product_from_dict,
)


@dataclass(frozen=True)
class ConceptQuery:
    query_id: str
    query_zh: str
    target_concepts: tuple[ConceptState, ...]
    reviewed_english: str = ""
    confusable_concepts: tuple[str, ...] = ()
    gold_doc_ids: tuple[str, ...] = ()
    conversions: tuple[ConversionProduct, ...] = ()
    source: str = ""
    review_status: str = "draft"
    pilot: bool = True
    not_for_clinical_use: bool = True

    def __post_init__(self) -> None:
        if not str(self.query_id or "").strip():
            raise ValueError("query_id must be non-empty")
        if not str(self.query_zh or "").strip():
            raise ValueError(f"{self.query_id}: query_zh must be non-empty")
        if not self.target_concepts:
            raise ValueError(f"{self.query_id}: target_concepts must be non-empty")
        if not self.not_for_clinical_use:
            raise ValueError(f"{self.query_id}: not_for_clinical_use must be true")
        for concept in self.target_concepts:
            if concept.status != "resolved":
                raise ValueError(
                    f"{self.query_id}: target concept {concept.slot!r} must be resolved"
                )
        confusables = tuple(str(item).strip() for item in self.confusable_concepts if str(item).strip())
        if set(confusables) & {concept.concept_id for concept in self.target_concepts}:
            raise ValueError(f"{self.query_id}: confusable concept overlaps a target concept")
        object.__setattr__(self, "confusable_concepts", confusables)
        routes = [conversion.route for conversion in self.conversions]
        if len(routes) != len(set(routes)):
            raise ValueError(f"{self.query_id}: duplicate conversion route")

    @property
    def target_ids(self) -> tuple[str, ...]:
        return tuple(concept.concept_id for concept in self.target_concepts)

    def conversion_for_route(self, route: str) -> ConversionProduct | None:
        for conversion in self.conversions:
            if conversion.route == route:
                return conversion
        return None

    def reviewed_concepts(self) -> tuple[ConceptState, ...]:
        return tuple(
            ConceptState(
                slot=concept.slot,
                surface=concept.surface,
                concept_id=concept.concept_id,
                confidence=1.0,
                status="resolved",
                source="reviewed_mapping",
            )
            for concept in self.target_concepts
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "query_zh": self.query_zh,
            "reviewed_english": self.reviewed_english,
            "target_concepts": [concept.to_dict() for concept in self.target_concepts],
            "confusable_concepts": list(self.confusable_concepts),
            "gold_doc_ids": list(self.gold_doc_ids),
            "conversions": [conversion.to_dict() for conversion in self.conversions],
            "source": self.source,
            "review_status": self.review_status,
            "pilot": self.pilot,
            "not_for_clinical_use": self.not_for_clinical_use,
        }


def load_query_set(path: str | Path, index: ConceptIndex) -> tuple[ConceptQuery, ...]:
    rows = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError(f"query set is empty: {path}")
    queries: list[ConceptQuery] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("query row must be a JSON object")
        query = _query_from_dict(row, index)
        if query.query_id in seen:
            raise ValueError(f"duplicate query_id: {query.query_id}")
        seen.add(query.query_id)
        queries.append(query)
    return tuple(queries)


def _query_from_dict(row: Mapping[str, Any], index: ConceptIndex) -> ConceptQuery:
    raw_concepts = row.get("target_concepts")
    if not isinstance(raw_concepts, list):
        raise ValueError("target_concepts must be an array")
    concepts = tuple(concept_state_from_dict(item) for item in raw_concepts)
    for concept in concepts:
        if concept.concept_id not in index.by_ui:
            raise ValueError(f"unknown target MeSH descriptor: {concept.concept_id}")
    confusables = row.get("confusable_concepts") or ()
    if not isinstance(confusables, (list, tuple)):
        raise ValueError("confusable_concepts must be an array")
    unknown = [str(item) for item in confusables if str(item) not in index.by_ui]
    if unknown:
        raise ValueError("unknown confusable MeSH descriptor(s): " + ", ".join(unknown))
    gold = row.get("gold_doc_ids") or ()
    if not isinstance(gold, (list, tuple)):
        raise ValueError("gold_doc_ids must be an array")
    raw_conversions = row.get("conversions") or ()
    if not isinstance(raw_conversions, (list, tuple)):
        raise ValueError("conversions must be an array")
    return ConceptQuery(
        query_id=str(row.get("query_id") or "").strip(),
        query_zh=str(row.get("query_zh") or "").strip(),
        target_concepts=concepts,
        reviewed_english=str(row.get("reviewed_english") or "").strip(),
        confusable_concepts=tuple(str(item) for item in confusables),
        gold_doc_ids=tuple(str(item) for item in gold),
        conversions=tuple(conversion_product_from_dict(item) for item in raw_conversions),
        source=str(row.get("source") or ""),
        review_status=str(row.get("review_status") or "draft"),
        pilot=bool(row.get("pilot", True)),
        not_for_clinical_use=bool(row.get("not_for_clinical_use", True)),
    )
