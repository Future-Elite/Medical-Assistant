"""Local patient-fact retrieval. No network or external connector calls."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from .his import PatientFact, PatientSnapshot


@dataclass(frozen=True)
class PatientHit:
    record_path: str
    field_path: str
    collection: str
    event_type: str
    time: str | None
    start: str | None
    end: str | None
    matched_terms: tuple[str, ...]
    score: float
    evidence_text: str
    source_type: str = "patient_record"


def retrieve_patient_facts(snapshot: PatientSnapshot, queries: Sequence[str], *, top_k: int = 20) -> tuple[tuple[PatientHit, ...], dict[str, object]]:
    terms = set().union(*(_terms(query) for query in queries if query))
    candidates: list[PatientHit] = []
    for fact in snapshot.visible_facts():
        matched = tuple(sorted(terms & _terms(fact.searchable_text())))
        if not matched:
            continue
        score = len(matched) / max(1, len(terms))
        candidates.append(PatientHit(
            record_path=fact.record_path, field_path=fact.field_path,
            collection=fact.collection, event_type=fact.event_type,
            time=fact.time, start=fact.start, end=fact.end,
            matched_terms=matched, score=round(score, 6),
            evidence_text=_safe_fact_text(fact), source_type="patient_record",
        ))
    candidates.sort(key=lambda hit: (-hit.score, hit.record_path))
    selected = tuple(candidates[:max(1, top_k)])
    return selected, {
        "patient_context_id": snapshot.context_id,
        "visible_fact_count": len(snapshot.visible_facts()),
        "patient_hit_count": len(selected),
        "suppressed_future": snapshot.suppressed_future,
        "warnings": list(snapshot.warnings),
    }


def _safe_fact_text(fact: PatientFact) -> str:
    parts = [fact.collection, fact.event_type]
    for key, value in fact.fields.items():
        if key.lower() in {"text", "free_text", "narrative", "note", "history"}:
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def _terms(text: str) -> set[str]:
    ascii_terms = set(re.findall(r"[a-z0-9]+(?:[-./][a-z0-9]+)*", str(text).lower()))
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", str(text)))
    ascii_terms.update(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
    return ascii_terms
