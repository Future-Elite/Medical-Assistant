"""Bounded Agent evidence loop over the knowledge graph.

The loop is deliberately deterministic: a caller supplies a proposal callback;
when it returns ``needs_evidence`` the listed queries are executed against the
same snapshot and release.  The budget is finite and every executed query is
recorded.  No proposal can mutate the patient state.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from ..core.his import PatientSnapshot
from .model import KnowledgeError


class KnowledgeBudgetError(KnowledgeError):
    """Raised when the bounded evidence loop would exceed its budget."""


class KnowledgeSession:
    def __init__(
        self,
        service: Any,
        snapshot: PatientSnapshot,
        queries: Sequence[str] = (),
        *,
        max_evidence_rounds: int = 2,
        intent: str | None = None,
        limit: int = 18,
        max_hops: int = 3,
    ) -> None:
        if max_evidence_rounds < 0:
            raise ValueError("max_evidence_rounds must be non-negative")
        self.service = service
        self.snapshot = snapshot
        self.queries = [str(item) for item in queries if str(item).strip()]
        self.max_evidence_rounds = int(max_evidence_rounds)
        self.intent = intent
        self.limit = limit
        self.max_hops = max_hops
        self.context = service.search(
            self.queries, snapshot, intent=intent, limit=limit, max_hops=max_hops,
        )
        self.evidence_rounds = 0
        self.trace: list[dict[str, Any]] = []
        self._seen_queries: set[str] = set(self.queries)
        self._seen_recommendations: set[str] = {
            row["recommendation_id"] for row in self.context.get("recommendations") or ()
        }
        self._seen_spans: set[str] = {
            row.get("span_id") or row.get("id")
            for row in (self.context.get("evidence_spans") or {}).values()
            if isinstance(row, Mapping)
        }

    def request_more(self, queries: Sequence[str]) -> dict[str, Any]:
        normalized = list(dict.fromkeys(str(item).strip() for item in queries if str(item).strip()))
        if not normalized:
            raise ValueError("needs_evidence requires at least one non-empty query")
        if self.evidence_rounds >= self.max_evidence_rounds:
            raise KnowledgeBudgetError(
                f"图谱补充查询预算已用完（{self.max_evidence_rounds} 轮）；未伪造证据。"
            )
        repeated = all(item in self._seen_queries for item in normalized)
        self.evidence_rounds += 1
        self.queries.extend(item for item in normalized if item not in self.queries)
        self._seen_queries.update(normalized)
        self.context = self.service.search(
            normalized, self.snapshot, intent=self.intent,
            limit=self.limit, max_hops=self.max_hops,
        )
        current_recommendations = {
            row["recommendation_id"] for row in self.context.get("recommendations") or ()
        }
        current_spans = {
            row.get("span_id") or row.get("id")
            for row in (self.context.get("evidence_spans") or {}).values()
            if isinstance(row, Mapping)
        }
        new_recommendations = current_recommendations - self._seen_recommendations
        new_spans = current_spans - self._seen_spans
        self._seen_recommendations.update(current_recommendations)
        self._seen_spans.update(current_spans)
        no_new = repeated and not new_recommendations and not new_spans
        self.context.setdefault("diagnostics", {})
        self.context["diagnostics"]["no_new_evidence"] = no_new
        self.context["diagnostics"]["new_recommendation_ids"] = sorted(new_recommendations)
        self.context["diagnostics"]["new_evidence_span_ids"] = sorted(new_spans)
        self.context["diagnostics"]["budget"] = {
            "round": self.evidence_rounds,
            "max_evidence_rounds": self.max_evidence_rounds,
        }
        if no_new:
            self.context["status"] = "no_new_evidence"
        normalized_terms = [
            candidate["id"]
            for row in self.context.get("term_resolutions") or ()
            for candidate in row.get("candidates") or ()
        ]
        entry = {
            "stage": "graph_query",
            "round": self.evidence_rounds,
            "queries": normalized,
            "knowledge_context_id": self.context.get("knowledge_context_id"),
            "normalized_terms": list(dict.fromkeys(normalized_terms)),
            "recommendation_ids": [
                row["recommendation_id"] for row in self.context.get("recommendations") or ()
            ],
            "graph": self.context.get("graph") or {"nodes": [], "edges": []},
            "snapshot_sha256": self.context.get("snapshot_sha256"),
            "cache_hit": False,
            "new_recommendation_ids": sorted(new_recommendations),
            "new_evidence_span_ids": sorted(new_spans),
            "status": self.context.get("status"),
        }
        self.trace.append(entry)
        return self.context

    def run_loop(self, propose: Callable[[Mapping[str, Any]], Mapping[str, Any]]) -> dict[str, Any]:
        """Run proposal callbacks until ready or the evidence budget is exhausted."""
        while True:
            proposal = propose(self.context)
            if not isinstance(proposal, Mapping):
                raise ValueError("proposal callback must return an object")
            if proposal.get("status") != "needs_evidence":
                return {
                    "status": proposal.get("status") or "ready",
                    "proposal": dict(proposal),
                    "context": self.context,
                    "evidence_rounds": self.evidence_rounds,
                    "trace": list(self.trace),
                }
            queries = proposal.get("knowledge_queries") or proposal.get("queries") or ()
            self.request_more(queries)
            if self.context.get("status") == "no_new_evidence":
                return {
                    "status": "no_new_evidence",
                    "proposal": dict(proposal),
                    "context": self.context,
                    "evidence_rounds": self.evidence_rounds,
                    "trace": list(self.trace),
                }
