"""Concept-identity schema for Chinese query to English evidence retrieval.

The unit is a **concept slot**, not a token: a Chinese clinical question is
decomposed into slots (disease / drug / population / numeric / negation / time)
and each slot carries a MeSH descriptor and an explicit status.  Conversion
products (reviewed mapping, machine translation, multilingual encoding, naive
lexical reuse) are audited slot by slot; the schema never coerces an unknown
status or label into a positive one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

CONCEPT_STATUSES = ("resolved", "ambiguous", "unresolved", "not_checked")
CONVERSION_ROUTES = (
    "reviewed_mapping",
    "machine_translation",
    "multilingual_encoding",
    "naive_lexical",
    "manual",
)
IDENTITY_LABELS = (
    "same_concept",
    "synonym_ok",
    "hypernym_drift",
    "hyponym_drift",
    "sense_ambiguity",
    "dropped",
    "fabricated",
    "numeric_mismatch",
    "negation_mismatch",
    "not_checked",
)
POSITIVE_IDENTITY_LABELS = frozenset({"same_concept", "synonym_ok"})
DRIFT_IDENTITY_LABELS = frozenset({
    "hypernym_drift", "hyponym_drift", "sense_ambiguity",
    "dropped", "fabricated", "numeric_mismatch", "negation_mismatch",
})


def _require_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must be a non-empty string")
    return text


@dataclass(frozen=True)
class ConceptState:
    """One query slot after conversion, with an explicit resolution status."""

    slot: str
    surface: str
    concept_id: str = ""
    confidence: float | None = None
    ambiguity_set: tuple[str, ...] = ()
    status: str = "not_checked"
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "slot", _require_text(self.slot, "slot"))
        object.__setattr__(self, "surface", _require_text(self.surface, "surface"))
        concept_id = str(self.concept_id or "").strip()
        object.__setattr__(self, "concept_id", concept_id)
        status = str(self.status or "not_checked").strip()
        if status not in CONCEPT_STATUSES:
            raise ValueError(
                f"unknown concept status: {status!r}（允许：{', '.join(CONCEPT_STATUSES)}）"
            )
        object.__setattr__(self, "status", status)
        if self.confidence is not None:
            confidence = float(self.confidence)
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("confidence must be within [0, 1]")
            object.__setattr__(self, "confidence", confidence)
        ambiguity = tuple(str(item).strip() for item in self.ambiguity_set if str(item).strip())
        object.__setattr__(self, "ambiguity_set", ambiguity)
        if status == "resolved" and not concept_id:
            raise ValueError("resolved concept state requires concept_id")
        if status == "ambiguous" and len(ambiguity) < 2:
            raise ValueError("ambiguous concept state requires at least two candidate IDs")
        if status in {"unresolved", "not_checked"} and concept_id:
            raise ValueError(f"{status} concept state must not carry a concept_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "surface": self.surface,
            "concept_id": self.concept_id,
            "confidence": self.confidence,
            "ambiguity_set": list(self.ambiguity_set),
            "status": self.status,
            "source": self.source,
        }


@dataclass(frozen=True)
class ConceptIdentity:
    """One slot-level comparison between source and converted concept state."""

    slot: str
    source_surface: str
    target_concept_id: str
    converted_concept_id: str = ""
    label: str = "not_checked"
    evidence: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "slot", _require_text(self.slot, "slot"))
        object.__setattr__(self, "source_surface", _require_text(self.source_surface, "source_surface"))
        target = str(self.target_concept_id or "").strip()
        converted = str(self.converted_concept_id or "").strip()
        object.__setattr__(self, "target_concept_id", target)
        object.__setattr__(self, "converted_concept_id", converted)
        label = str(self.label or "not_checked").strip()
        if label not in IDENTITY_LABELS:
            raise ValueError(
                f"unknown identity label: {label!r}（允许：{', '.join(IDENTITY_LABELS)}）"
            )
        object.__setattr__(self, "label", label)
        if label in POSITIVE_IDENTITY_LABELS and converted != target:
            raise ValueError(f"{label} requires converted_concept_id == target_concept_id")
        if label == "dropped" and converted:
            raise ValueError("dropped identity must not carry converted_concept_id")
        if label == "fabricated" and (not converted or converted == target):
            raise ValueError("fabricated identity requires a non-target converted_concept_id")
        if label == "not_checked" and converted:
            raise ValueError("not_checked identity must not carry converted_concept_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "source_surface": self.source_surface,
            "target_concept_id": self.target_concept_id,
            "converted_concept_id": self.converted_concept_id,
            "label": self.label,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class ConversionProduct:
    """A query conversion route and its audited slot-level concept states."""

    route: str
    query_zh: str
    query_converted: str
    concepts: tuple[ConceptState, ...]
    identities: tuple[ConceptIdentity, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        route = _require_text(self.route, "route")
        if route not in CONVERSION_ROUTES:
            raise ValueError(
                f"unknown conversion route: {route!r}（允许：{', '.join(CONVERSION_ROUTES)}）"
            )
        object.__setattr__(self, "route", route)
        object.__setattr__(self, "query_zh", _require_text(self.query_zh, "query_zh"))
        object.__setattr__(self, "query_converted", _require_text(self.query_converted, "query_converted"))
        concepts = tuple(self.concepts)
        if not concepts:
            raise ValueError("conversion product requires at least one concept state")
        object.__setattr__(self, "concepts", concepts)
        identities = tuple(self.identities)
        slots = {concept.slot for concept in concepts}
        for identity in identities:
            if identity.slot not in slots:
                raise ValueError(f"identity slot {identity.slot!r} has no matching concept state")
        object.__setattr__(self, "identities", identities)
        object.__setattr__(self, "warnings", tuple(str(item) for item in self.warnings if str(item).strip()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "query_zh": self.query_zh,
            "query_converted": self.query_converted,
            "concepts": [concept.to_dict() for concept in self.concepts],
            "identities": [identity.to_dict() for identity in self.identities],
            "warnings": list(self.warnings),
        }


def concept_state_from_dict(payload: Mapping[str, Any]) -> ConceptState:
    if not isinstance(payload, Mapping):
        raise ValueError("concept state must be an object")
    ambiguity = payload.get("ambiguity_set") or ()
    if isinstance(ambiguity, str):
        ambiguity = (ambiguity,)
    return ConceptState(
        slot=str(payload.get("slot") or ""),
        surface=str(payload.get("surface") or ""),
        concept_id=str(payload.get("concept_id") or ""),
        confidence=payload.get("confidence"),
        ambiguity_set=tuple(ambiguity),
        status=str(payload.get("status") or "not_checked"),
        source=str(payload.get("source") or ""),
    )


def concept_identity_from_dict(payload: Mapping[str, Any]) -> ConceptIdentity:
    if not isinstance(payload, Mapping):
        raise ValueError("concept identity must be an object")
    return ConceptIdentity(
        slot=str(payload.get("slot") or ""),
        source_surface=str(payload.get("source_surface") or ""),
        target_concept_id=str(payload.get("target_concept_id") or ""),
        converted_concept_id=str(payload.get("converted_concept_id") or ""),
        label=str(payload.get("label") or "not_checked"),
        evidence=str(payload.get("evidence") or ""),
    )


def conversion_product_from_dict(payload: Mapping[str, Any]) -> ConversionProduct:
    if not isinstance(payload, Mapping):
        raise ValueError("conversion product must be an object")
    concepts = payload.get("concepts")
    identities = payload.get("identities") or ()
    if not isinstance(concepts, list):
        raise ValueError("conversion product concepts must be an array")
    if not isinstance(identities, (list, tuple)):
        raise ValueError("conversion product identities must be an array")
    return ConversionProduct(
        route=str(payload.get("route") or ""),
        query_zh=str(payload.get("query_zh") or ""),
        query_converted=str(payload.get("query_converted") or ""),
        concepts=tuple(concept_state_from_dict(item) for item in concepts),
        identities=tuple(concept_identity_from_dict(item) for item in identities),
        warnings=tuple(str(item) for item in payload.get("warnings") or ()),
    )
