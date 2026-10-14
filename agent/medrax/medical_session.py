"""Per-turn tool context; copied by LangChain when tools run in worker threads."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4
from threading import Lock


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class MedicalSession:
    patient_ref: str = ""
    as_of: str = ""
    generation: str = field(default_factory=lambda: uuid4().hex)
    snapshot: dict | None = None
    retrievals: dict[str, dict] = field(default_factory=dict)

    @property
    def citation_map(self):
        return {
            record["metadata"]["citation_id"]: record["source_id"]
            for package in self.retrievals.values()
            for record in package.get("evidence", [])
        }


@dataclass
class TurnContext:
    session: MedicalSession
    question: str
    emit: Callable[[dict], None]
    turn_id: str = field(default_factory=lambda: uuid4().hex)
    counts: dict[str, int] = field(default_factory=dict)
    retrieval_errors: list[str] = field(default_factory=list)
    limits: dict[str, int] = field(default_factory=lambda: {
        "retrieve_medical_evidence": 3,
        "get_patient_snapshot": 1,
        "open_medical_citation": 4,
        "model": 12,
    })
    _budget_lock: Any = field(default_factory=Lock, repr=False)

    def consume(self, name: str) -> None:
        with self._budget_lock:
            count = self.counts.get(name, 0)
            if count >= self.limits[name]:
                raise BudgetExceeded(f"{name} exhausted its per-turn budget ({count})")
            self.counts[name] = count + 1


ACTIVE_TURN: ContextVar[TurnContext | None] = ContextVar("medical_agent_turn", default=None)


def require_turn() -> TurnContext:
    turn = ACTIVE_TURN.get()
    if turn is None:
        raise RuntimeError("This tool requires a MedicalAgentOrchestrator turn")
    return turn
