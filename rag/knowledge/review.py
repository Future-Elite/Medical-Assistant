"""Knowledge review/publication state machine.

The requirements separate discovery, screening, extraction, terminology review,
clinical review, approval and publication.  This module keeps the transition
rules explicit so a caller cannot skip medical review or publish a fixture.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

REVIEW_STATES = (
    "discovered", "screened", "extracted", "terminology_reviewed",
    "clinically_reviewed", "approved", "published",
)
TERMINAL_STATES = ("rejected", "needs_revision", "superseded", "withdrawn")
ALL_STATES = REVIEW_STATES + TERMINAL_STATES

_TRANSITIONS = {
    "discovered": {"screened", "rejected"},
    "screened": {"extracted", "rejected"},
    "extracted": {"terminology_reviewed", "needs_revision", "rejected"},
    "terminology_reviewed": {"clinically_reviewed", "needs_revision", "rejected"},
    "clinically_reviewed": {"approved", "needs_revision", "rejected"},
    "approved": {"published", "withdrawn"},
    "published": {"superseded", "withdrawn"},
    "needs_revision": {"extracted", "rejected"},
    "rejected": set(),
    "superseded": set(),
    "withdrawn": set(),
}


class ReviewStateError(ValueError):
    """Raised when a review transition would skip a required stage."""


@dataclass(frozen=True)
class ReviewRecord:
    state: str
    at: str
    actor_role: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"state": self.state, "at": self.at, "actor_role": self.actor_role, "note": self.note}


def validate_review_history(records: Sequence[Mapping[str, Any]]) -> list[ReviewRecord]:
    """Validate a linear review history, returning normalized records."""
    if not records:
        raise ReviewStateError("review history is empty")
    normalized: list[ReviewRecord] = []
    current = ""
    for index, row in enumerate(records):
        state = str(row.get("state") or row.get("status") or "").strip()
        at = str(row.get("at") or "").strip()
        if state not in ALL_STATES:
            raise ReviewStateError(f"review history[{index}] has unknown state: {state}")
        if not at:
            raise ReviewStateError(f"review history[{index}] is missing at")
        if index == 0:
            if state not in {"discovered", "screened"}:
                raise ReviewStateError("review history must start at discovered/screened")
        else:
            if state not in _TRANSITIONS.get(current, set()):
                raise ReviewStateError(f"illegal review transition: {current} -> {state}")
        normalized.append(ReviewRecord(state, at, str(row.get("actor_role") or ""), str(row.get("note") or "")))
        current = state
    return normalized


def can_transition(current: str, target: str) -> bool:
    return target in _TRANSITIONS.get(str(current), set())


def publication_ready(records: Sequence[Mapping[str, Any]]) -> bool:
    """Publication requires an explicit approved state, not just review fields."""
    states = [str(row.get("state") or row.get("status") or "") for row in records]
    return "approved" in states and states[-1] in {"approved", "published"}


__all__ = [
    "ALL_STATES", "REVIEW_STATES", "TERMINAL_STATES", "ReviewRecord",
    "ReviewStateError", "can_transition", "publication_ready", "validate_review_history",
]
