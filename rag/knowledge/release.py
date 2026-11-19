"""Release lifecycle contracts for knowledge graphs.

The default demo graph is intentionally not publishable.  This registry is the
mechanism a future guideline graph will use: only a reviewed, publishable
release can be published; withdrawal and rollback are recorded explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .model import KnowledgeError, KnowledgeGraph


class ReleaseLifecycleError(KnowledgeError):
    pass


@dataclass
class _ReleaseState:
    graph: KnowledgeGraph
    status: str = "draft"
    published_at: str | None = None
    withdrawn_at: str | None = None
    supersedes: str | None = None
    rollback_to: str | None = None
    reason: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)


class KnowledgeReleaseRegistry:
    def __init__(self, graphs: Iterable[KnowledgeGraph] = ()) -> None:
        self._states: dict[str, _ReleaseState] = {}
        self._active: str | None = None
        for graph in graphs:
            self.register(graph)

    def register(self, graph: KnowledgeGraph) -> dict[str, Any]:
        release_id = graph.release.release_id
        if release_id in self._states:
            raise ReleaseLifecycleError(f"release already registered: {release_id}")
        state = _ReleaseState(graph=graph)
        state.history.append({"action": "registered", "at": _now()})
        self._states[release_id] = state
        return self._payload(state)

    def publish(self, release_id: str, *, rationale: str = "") -> dict[str, Any]:
        state = self._require(release_id)
        if state.status == "published":
            raise ReleaseLifecycleError(f"release already published: {release_id}")
        release = state.graph.release
        if not release.publishable:
            raise ReleaseLifecycleError("release is not publishable")
        if not release.coverage:
            raise ReleaseLifecycleError("release has no coverage declaration")
        if not release.dependencies:
            raise ReleaseLifecycleError("release has no dependency declaration")
        if release.review_status not in {"reviewed", "approved"}:
            raise ReleaseLifecycleError("release has not passed review")
        unreviewed = [
            span_id for span_id, span in state.graph.spans.items()
            if str(span.get("review_status") or "") not in {"reviewed", "approved"}
        ]
        if unreviewed:
            raise ReleaseLifecycleError(f"evidence spans not reviewed: {','.join(unreviewed[:3])}")
        unlocatable = [
            span_id for span_id, span in state.graph.spans.items()
            if not str(span.get("locator") or "").strip() or not str(span.get("quote") or "").strip()
        ]
        if unlocatable:
            raise ReleaseLifecycleError(f"evidence spans lack locator/quote: {','.join(unlocatable[:3])}")
        unreviewed_sources = [
            source_id for source_id, source in state.graph.sources.items()
            if str(source.get("review_status") or "") not in {"reviewed", "approved"}
        ]
        if unreviewed_sources:
            raise ReleaseLifecycleError(f"knowledge sources not reviewed: {','.join(unreviewed_sources[:3])}")
        invalid_sources = [
            source_id for source_id, source in state.graph.sources.items()
            if str(source.get("document_status") or "") not in {"valid", "current", "active"}
        ]
        if invalid_sources:
            raise ReleaseLifecycleError(f"knowledge sources are not valid: {','.join(invalid_sources[:3])}")
        unlicensed_sources = [
            source_id for source_id, source in state.graph.sources.items()
            if not str(source.get("license_note") or "").strip()
            and not str(source.get("storage_policy") or "").strip()
        ]
        if unlicensed_sources:
            raise ReleaseLifecycleError(f"knowledge sources lack license/storage policy: {','.join(unlicensed_sources[:3])}")
        if self._active:
            previous = self._states[self._active]
            previous.status = "superseded"
            previous.history.append({"action": "superseded", "at": _now(), "by": release_id})
        state.status = "published"
        state.published_at = _now()
        state.reason = rationale
        state.history.append({"action": "published", "at": state.published_at, "rationale": rationale})
        self._active = release_id
        return self._payload(state)

    def withdraw(self, release_id: str, *, reason: str = "") -> dict[str, Any]:
        state = self._require(release_id)
        if state.status != "published":
            raise ReleaseLifecycleError(f"release is not published: {release_id}")
        state.status = "withdrawn"
        state.withdrawn_at = _now()
        state.reason = reason
        state.history.append({"action": "withdrawn", "at": state.withdrawn_at, "reason": reason})
        if self._active == release_id:
            self._active = None
        return self._payload(state)

    def rollback(self, release_id: str, target_release_id: str, *, reason: str = "") -> dict[str, Any]:
        current = self._require(release_id)
        target = self._require(target_release_id)
        if current.status != "published":
            raise ReleaseLifecycleError(f"release is not published: {release_id}")
        if target.status not in {"published", "superseded"}:
            raise ReleaseLifecycleError(f"rollback target is not a published release: {target_release_id}")
        current.status = "superseded"
        current.rollback_to = target_release_id
        current.reason = reason
        current.history.append({"action": "rolled_back", "at": _now(), "to": target_release_id, "reason": reason})
        target.status = "published"
        target.history.append({"action": "reactivated", "at": _now(), "from": release_id, "reason": reason})
        self._active = target_release_id
        return self._payload(target)

    def active(self) -> KnowledgeGraph | None:
        return self._states[self._active].graph if self._active else None

    def list_releases(self) -> dict[str, Any]:
        return {
            "active_release_id": self._active,
            "releases": [self._payload(state) for state in self._states.values()],
        }

    def _require(self, release_id: str) -> _ReleaseState:
        state = self._states.get(str(release_id))
        if state is None:
            raise ReleaseLifecycleError(f"unknown release: {release_id}")
        return state

    def _payload(self, state: _ReleaseState) -> dict[str, Any]:
        release = state.graph.release
        return {
            **release.to_dict(),
            "status": state.status,
            "published_at": state.published_at,
            "withdrawn_at": state.withdrawn_at,
            "supersedes": state.supersedes,
            "rollback_to": state.rollback_to,
            "reason": state.reason,
            "history": list(state.history),
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
