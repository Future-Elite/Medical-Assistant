"""Knowledge service facade for RAG/Agent/EHR callers.

Python API:
    load_knowledge_graph() / get_default_service()
    service.resolve_terms(terms)
    service.search(queries, patient_snapshot, ...)
    service.validate_actions(candidate_plan, patient_snapshot, context_id)
    service.get_evidence(span_id)
    service.get_release()

The module is deterministic and does not call an LLM or the internet.  The
bundled fixture is explicitly ``synthetic_fixture`` and not for production.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import copy
import hashlib
import json
import threading
from pathlib import Path
import time
from typing import Any, Mapping, Sequence
from uuid import uuid4

from ..core.his import PatientSnapshot
from ..patient_graph import build_patient_graph
from .conditions import FactStore
from .model import KnowledgeError, KnowledgeGraph, KnowledgeRelease, load_knowledge_graph
from .patient_facts import patient_fact_store
from .planning import (
    build_maintenance_comparator,
    build_review_plan,
    episode_end_gate,
)
from .release import KnowledgeReleaseRegistry
from .fusion import retrieve_bidirectional_knowledge
from .retrieval import retrieve_knowledge
from .validation import validate_actions as _validate_actions


@dataclass
class _StoredContext:
    context: dict[str, Any]
    facts: FactStore


class KnowledgeService:
    def __init__(self, graph: KnowledgeGraph | None = None, *, fixture_path: str | Path | None = None) -> None:
        self.graph = graph or load_knowledge_graph(fixture_path)
        self.release_lifecycle = KnowledgeReleaseRegistry([self.graph])
        self._contexts: dict[str, _StoredContext] = {}
        self._audit_log: list[dict[str, Any]] = []
        self._cache: dict[tuple, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def stats(self) -> dict[str, Any]:
        return self.graph.stats()

    def graph_payload(self) -> dict[str, Any]:
        return self.graph.to_dict()

    def get_release(self, release_id: str | None = None) -> dict[str, Any]:
        if release_id and release_id != self.graph.release.release_id:
            raise KnowledgeError(f"unknown knowledge release: {release_id}")
        return self.graph.release.to_dict()

    def list_releases(self) -> dict[str, Any]:
        return self.release_lifecycle.list_releases()

    def publish_release(self, release_id: str, *, rationale: str = "") -> dict[str, Any]:
        return self.release_lifecycle.publish(release_id, rationale=rationale)

    def withdraw_release(self, release_id: str, *, reason: str = "") -> dict[str, Any]:
        return self.release_lifecycle.withdraw(release_id, reason=reason)

    def rollback_release(self, release_id: str, target_release_id: str, *, reason: str = "") -> dict[str, Any]:
        return self.release_lifecycle.rollback(release_id, target_release_id, reason=reason)

    def resolve_terms(self, terms: Sequence[str], *, release_id: str | None = None) -> dict[str, Any]:
        if release_id and release_id != self.graph.release.release_id:
            raise KnowledgeError(f"unknown knowledge release: {release_id}")
        resolutions = self.graph.resolve_terms(terms)
        return {
            "schema_version": "term-resolution",
            "knowledge_release_id": self.graph.release.release_id,
            "release_sha256": self.graph.release.content_sha256,
            "terms": resolutions,
        }

    def search(
        self,
        queries: Sequence[str],
        snapshot: PatientSnapshot,
        *,
        intent: str | None = None,
        release_id: str | None = None,
        limit: int = 18,
        max_hops: int = 3,
        fusion: bool = True,
        fusion_mode: str = "full",
    ) -> dict[str, Any]:
        if release_id and release_id != self.graph.release.release_id:
            raise KnowledgeError(f"unknown knowledge release: {release_id}")
        mode = str(fusion_mode or "full").strip().lower()
        if mode not in {"full", "forward", "backward"}:
            raise KnowledgeError(f"unknown fusion_mode: {fusion_mode}")
        started = time.perf_counter()
        snapshot_sha = build_patient_graph(snapshot).snapshot_sha256
        cache_key = (self.graph.release.content_sha256, snapshot_sha, tuple(str(item) for item in queries),
                     str(intent or ""), int(limit), int(max_hops), bool(fusion), mode)
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            context = copy.deepcopy(cached)
            context["retrieval_id"] = f"retrieval-{uuid4().hex}"
            context["knowledge_context_id"] = f"knowledge-{uuid4().hex}"
            context.setdefault("diagnostics", {})
            context["diagnostics"]["cache_hit"] = True
            context["diagnostics"]["cache_key"] = hashlib.sha256(
                repr(cache_key).encode("utf-8")).hexdigest()[:16]
        else:
            context = (
                retrieve_bidirectional_knowledge(
                    self.graph, snapshot, list(queries),
                    intent=intent, limit=limit, max_hops=max_hops,
                    forward=mode in {"full", "forward"},
                    backward=mode in {"full", "backward"},
                )
                if fusion else retrieve_knowledge(
                    self.graph, snapshot, list(queries),
                    intent=intent, limit=limit, max_hops=max_hops,
                )
            )
            context.setdefault("diagnostics", {})
            context["diagnostics"]["cache_hit"] = False
            context["diagnostics"]["cache_key"] = hashlib.sha256(
                repr(cache_key).encode("utf-8")).hexdigest()[:16]
            with self._lock:
                self._cache[cache_key] = copy.deepcopy(context)
        facts = patient_fact_store(snapshot)
        self._contexts[context["knowledge_context_id"]] = _StoredContext(context=context, facts=facts)
        self._audit_log.append({
            "retrieval_id": context.get("retrieval_id"),
            "knowledge_context_id": context.get("knowledge_context_id"),
            "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "snapshot_sha256": context.get("snapshot_sha256"),
            "knowledge_release_id": context.get("knowledge_release_id"),
            "release_sha256": context.get("release_sha256"),
            "queries": list(context.get("queries") or ()),
            "effective_queries": list(context.get("effective_queries") or ()),
            "intent": (context.get("diagnostics") or {}).get("intent"),
            "status": context.get("status"),
            "recommendation_ids": [row["recommendation_id"] for row in context.get("recommendations") or ()],
            "excluded_ids": [row["id"] for row in context.get("excluded_recommendations") or ()],
            "evidence_span_ids": sorted((context.get("evidence_spans") or {}).keys()),
            "missing_information": list(context.get("missing_information") or ()),
            "budget": (context.get("diagnostics") or {}).get("budget"),
            "fusion": (context.get("fusion") or {}).get("stats"),
            "cache_hit": (context.get("diagnostics") or {}).get("cache_hit"),
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        })
        return context

    def get_context(self, knowledge_context_id: str) -> dict[str, Any]:
        stored = self._contexts.get(str(knowledge_context_id))
        if stored is None:
            raise KnowledgeError(f"unknown knowledge_context_id: {knowledge_context_id}")
        return dict(stored.context)

    def replay_context(self, knowledge_context_id: str) -> dict[str, Any]:
        """Replay a pinned context without re-querying or changing the snapshot."""
        context = self.get_context(knowledge_context_id)
        return {
            "schema_version": "knowledge-context-replay-1.0",
            "knowledge_context_id": context.get("knowledge_context_id"),
            "retrieval_id": context.get("retrieval_id"),
            "knowledge_release_id": context.get("knowledge_release_id"),
            "release_sha256": context.get("release_sha256"),
            "snapshot_sha256": context.get("snapshot_sha256"),
            "queries": context.get("queries"),
            "status": context.get("status"),
            "recommendations": context.get("recommendations"),
            "excluded_recommendations": context.get("excluded_recommendations"),
            "evidence_spans": context.get("evidence_spans"),
            "boundary": "Historical replay of the stored context; it does not follow latest release.",
        }

    def audit_log(self, *, limit: int = 100) -> dict[str, Any]:
        """Return bounded, privacy-preserving retrieval audit events."""
        return {
            "schema_version": "knowledge-audit-log-1.0",
            "count": len(self._audit_log),
            "events": list(self._audit_log[-max(1, int(limit)):]),
        }

    def review_plan(
        self,
        knowledge_context_id: str,
        *,
        panel_ids: Sequence[str] = (),
        metric_keys: Sequence[str] = (),
    ) -> dict[str, Any]:
        stored = self._contexts.get(str(knowledge_context_id))
        if stored is None:
            raise KnowledgeError(f"unknown knowledge_context_id: {knowledge_context_id}")
        return build_review_plan(stored.context, panel_ids=panel_ids, metric_keys=metric_keys)

    def session(
        self,
        snapshot: PatientSnapshot,
        queries: Sequence[str] = (),
        **kwargs: Any,
    ) -> Any:
        from .session import KnowledgeSession

        return KnowledgeSession(self, snapshot, queries, **kwargs)

    def maintenance_comparator(
        self,
        primary_plan: Mapping[str, Any],
        *,
        target: str | None = None,
        proposal_order: int = 1,
    ) -> dict[str, Any]:
        return build_maintenance_comparator(
            primary_plan, target=target, proposal_order=proposal_order,
        )

    def episode_end_gate(
        self,
        knowledge_context_id: str,
        *,
        explicit_clinical_end: bool = False,
        end_reason: str = "",
    ) -> dict[str, Any]:
        stored = self._contexts.get(str(knowledge_context_id))
        if stored is None:
            raise KnowledgeError(f"unknown knowledge_context_id: {knowledge_context_id}")
        return episode_end_gate(
            stored.facts,
            explicit_clinical_end=explicit_clinical_end,
            end_reason=end_reason,
        )

    def validate_actions(
        self,
        candidate_plan: Mapping[str, Any],
        snapshot: PatientSnapshot,
        knowledge_context_id: str,
    ) -> dict[str, Any]:
        stored = self._contexts.get(str(knowledge_context_id))
        if stored is None:
            raise KnowledgeError(f"unknown knowledge_context_id: {knowledge_context_id}")
        current_hash = build_patient_graph(snapshot).snapshot_sha256
        if current_hash != stored.context.get("snapshot_sha256"):
            return {
                "schema_version": "action-validation",
                "knowledge_context_id": knowledge_context_id,
                "knowledge_release_id": self.graph.release.release_id,
                "snapshot_sha256": current_hash,
                "eligible_for_simulation": False,
                "status": "blocked",
                "actions": [],
                "combination_errors": [],
                "errors": [{
                    "code": "SNAPSHOT_STALE",
                    "message": "病例快照在检索后已变化，不能沿用旧知识上下文校验新动作。",
                }],
                "boundary": "stale snapshot; re-run knowledge search before validation",
            }
        return _validate_actions(
            self.graph, stored.context, candidate_plan, facts=stored.facts,
        )

    def subgraph(
        self,
        *,
        recommendation_id: str | None = None,
        term: str | None = None,
        release_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Return a bounded subgraph around one recommendation/term."""
        if release_id and release_id != self.graph.release.release_id:
            raise KnowledgeError(f"unknown knowledge release: {release_id}")
        seeds: list[str] = []
        term_resolution = None
        if recommendation_id:
            if recommendation_id not in self.graph.recommendations:
                raise KnowledgeError(f"unknown recommendation: {recommendation_id}")
            seeds.append(recommendation_id)
        if term:
            term_resolution = self.graph.resolve_term(term)
            if term_resolution["status"] != "resolved":
                return {
                    "schema_version": "knowledge-subgraph-1.0",
                    "release_id": self.graph.release.release_id,
                    "release_sha256": self.graph.release.content_sha256,
                    "term_resolution": term_resolution,
                    "nodes": [],
                    "edges": [],
                    "truncated": False,
                }
            seeds.append(term_resolution["candidates"][0]["id"])
        if not seeds:
            raise ValueError("subgraph requires recommendation_id or term")
        selected = set(seeds)
        frontier = set(seeds)
        for _depth in range(2):
            next_frontier = set()
            for node_id in frontier:
                for other, edge in self.graph.adjacency.get(node_id, ()):
                    if edge.get("relation") == "HAS_ALIAS":
                        continue
                    if self.graph.nodes.get(other, {}).get("type") == "alias":
                        continue
                    if other not in selected:
                        next_frontier.add(other)
            selected.update(next_frontier)
            frontier = next_frontier
            if len(selected) >= limit:
                break
        truncated = len(selected) > limit
        selected = set(sorted(selected)[:limit])
        nodes = [
            {"id": node["id"], "type": node["type"], "label": node["label"], **node["data"]}
            for node in self.graph.nodes.values() if node["id"] in selected
        ]
        edges = [
            edge for edge in self.graph.edges
            if edge.get("relation") != "HAS_ALIAS"
            and edge["source"] in selected and edge["target"] in selected
        ]
        return {
            "schema_version": "knowledge-subgraph-1.0",
            "release_id": self.graph.release.release_id,
            "release_sha256": self.graph.release.content_sha256,
            "term_resolution": term_resolution,
            "nodes": nodes,
            "edges": edges,
            "truncated": truncated,
            "limit": limit,
        }

    def get_evidence(self, evidence_span_id: str, release_id: str | None = None) -> dict[str, Any]:
        if release_id and release_id != self.graph.release.release_id:
            raise KnowledgeError(f"unknown knowledge release: {release_id}")
        return self.graph.get_evidence(evidence_span_id)

    def model_projection(
        self,
        context: Mapping[str, Any],
        *,
        max_tokens: int = 8000,
        max_bytes: int = 40000,
    ) -> dict[str, Any]:
        payload = {
            "version": context.get("knowledge_release_id"),
            "release_sha256": context.get("release_sha256"),
            "queries": context.get("queries"),
            "patient_facts": context.get("patient_facts"),
            "recommendations": [
                {
                    "id": row["recommendation_id"],
                    "summary": row["summary"],
                    "direction": row["direction"],
                    "action_id": row["action_id"],
                    "supported_operations": row["supported_operations"],
                    "applicability": row["applicability"],
                    "eligible_for_simulation": row["eligible_for_simulation"],
                    "missing_information": row["missing_information"],
                    "evidence_span_ids": row["evidence_span_ids"],
                    "runtime_binding_ids": row["runtime_binding_ids"],
                }
                for row in context.get("recommendations") or ()
            ],
            "excluded_recommendations": context.get("excluded_recommendations"),
            "available_monitoring_panels": context.get("available_monitoring_panels"),
            "boundary": context.get("boundary"),
        }
        serialized = json.dumps(payload, ensure_ascii=False)
        estimated_tokens = max(1, len(serialized) // 4)
        if estimated_tokens > max_tokens or len(serialized.encode("utf-8")) > max_bytes:
            kept = []
            for row in payload.get("recommendations") or ():
                candidate = {**payload, "recommendations": kept + [row]}
                size = len(json.dumps(candidate, ensure_ascii=False).encode("utf-8"))
                if size > max_bytes or max(1, size // 4) > max_tokens:
                    break
                kept.append(row)
            payload["recommendations"] = kept
            payload["truncated"] = True
            payload["omitted_counts"] = {"recommendations": len(context.get("recommendations") or ()) - len(kept)}
            payload["token_estimate"] = max(1, len(json.dumps(payload, ensure_ascii=False)) // 4)
            payload["tokenizer"] = "char_div_4_estimate"
            payload["budget"] = {"max_tokens": max_tokens, "max_bytes": max_bytes}
        else:
            payload["truncated"] = False
            payload["omitted_counts"] = {}
            payload["token_estimate"] = estimated_tokens
            payload["tokenizer"] = "char_div_4_estimate"
            payload["budget"] = {"max_tokens": max_tokens, "max_bytes": max_bytes}
        return payload


_DEFAULT: KnowledgeService | None = None


def get_default_service() -> KnowledgeService:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = KnowledgeService()
    return _DEFAULT


__all__ = [
    "KnowledgeError",
    "KnowledgeGraph",
    "KnowledgeRelease",
    "KnowledgeService",
    "get_default_service",
    "load_knowledge_graph",
]
