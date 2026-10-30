"""Iterative retrieval controller trial (controller-v1-trial).

The controller turns source routing from a single-pass rule set into an
explicit, replayable round loop:

- round 0 is always the patient snapshot (local, no external call);
- each later round selects at most one logical source role, runs only that
  role's connectors, records the evidence delta, then decides continue/stop;
- every considered source, selected source, skipped reason, budget spend and
  stop reason is written to the result;
- ``replay`` contains a request fingerprint and the full decision sequence, so
  a reviewer can reconstruct *why* each source was queried and why the loop
  stopped.

This is a deterministic rule/utility policy, not a learned router. It is the
R1 bridge between ``routing.py`` (single-pass v0) and future learned policies
(RAGRoute / ORDER / Retriever Portfolios). Default production retrieval is
unchanged; callers opt in by using :class:`RetrievalController` explicitly.

Important boundaries:

- "sufficiency" is a **retrieval-level heuristic** (evidence count, concept
  coverage, required role), not a clinical sufficiency or diagnosis claim;
- patient facts are always local and never sent to external connectors;
- patient facts are not routed: they are acquired before external rounds;
- the controller returns per-round evidence summaries, not a merged
  ``EvidencePackage`` with cross-round citation IDs. Citation IDs remain scoped
  to the round's ``retrieval_id``; the trial is for decision audit, not for
  pretending that cross-round citations are valid.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .his import PatientSnapshot
from .models import QueryPlan, RetrievalRequest
from .patient_retrieval import retrieve_patient_facts
from .pipeline import EvidenceRetrievalPipeline
from .query import build_query_plan

CONTROLLER_VERSION = "controller-v1-trial"
SOURCE_ROLES = ("patient_facts", "guideline_registry", "europe_pmc", "authorized_pdf", "pubmed", "other")
SOURCE_COSTS = {
    "guideline_registry": 0.1,
    "authorized_pdf": 0.4,
    "europe_pmc": 0.6,
    "pubmed": 0.8,
    "other": 1.0,
}
ROLE_LABELS = {
    "patient_facts": "患者事实（本地）",
    "guideline_registry": "指南注册表",
    "europe_pmc": "Europe PMC 全文",
    "authorized_pdf": "授权 PDF",
    "pubmed": "PubMed",
    "other": "其它来源",
}
_INTENT_PRIORS = {
    "guideline_recommendation": {"guideline_registry": 0.55, "authorized_pdf": 0.45, "europe_pmc": 0.35, "pubmed": 0.25},
    "evidence_update": {"pubmed": 0.60, "europe_pmc": 0.45, "guideline_registry": 0.20, "authorized_pdf": 0.20},
    "drug_safety": {"pubmed": 0.60, "europe_pmc": 0.45, "guideline_registry": 0.20, "authorized_pdf": 0.20},
    "clinical_question": {"guideline_registry": 0.45, "pubmed": 0.40, "europe_pmc": 0.35, "authorized_pdf": 0.30},
}


@dataclass(frozen=True)
class ControllerPolicy:
    """Deterministic trial policy; all thresholds are retrieval heuristics."""

    max_rounds: int = 4
    max_cost: float = 3.0
    min_evidence: int = 3
    min_coverage: float = 0.5
    max_zero_yield_rounds: int = 2
    min_action_score: float = 0.1
    cost_weight: float = 0.15

    def __post_init__(self) -> None:
        if self.max_rounds < 0:
            raise ValueError("max_rounds must be >= 0")
        if self.max_cost < 0:
            raise ValueError("max_cost must be >= 0")
        if self.min_evidence < 0:
            raise ValueError("min_evidence must be >= 0")
        if not 0 <= self.min_coverage <= 1:
            raise ValueError("min_coverage must be in [0, 1]")
        if self.max_zero_yield_rounds < 1:
            raise ValueError("max_zero_yield_rounds must be >= 1")
        if self.cost_weight < 0:
            raise ValueError("cost_weight must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_rounds": self.max_rounds,
            "max_cost": self.max_cost,
            "min_evidence": self.min_evidence,
            "min_coverage": self.min_coverage,
            "max_zero_yield_rounds": self.max_zero_yield_rounds,
            "min_action_score": self.min_action_score,
            "cost_weight": self.cost_weight,
        }


@dataclass(frozen=True)
class SourceProfile:
    role: str
    source_ids: tuple[str, ...]
    cost: float
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "label": ROLE_LABELS.get(self.role, self.role),
            "source_ids": list(self.source_ids),
            "cost": self.cost,
            "available": self.available,
        }


@dataclass
class ControllerState:
    round: int = 0
    queried_roles: tuple[str, ...] = ()
    queried_source_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    source_evidence_count: dict[str, int] = field(default_factory=dict)
    patient_hit_count: int = 0
    visible_fact_count: int = 0
    coverage: dict[str, bool] = field(default_factory=dict)
    budget_spent: float = 0.0
    consecutive_zero_yield: int = 0
    warnings: list[str] = field(default_factory=list)

    def snapshot(self, coverage_terms: Sequence[str]) -> dict[str, Any]:
        total = len(coverage_terms)
        covered = sum(1 for term in coverage_terms if self.coverage.get(term))
        return {
            "round": self.round,
            "queried_roles": list(self.queried_roles),
            "queried_source_ids": list(self.queried_source_ids),
            "evidence_count": len(self.evidence_ids),
            "source_evidence_count": dict(self.source_evidence_count),
            "patient_hit_count": self.patient_hit_count,
            "visible_fact_count": self.visible_fact_count,
            "coverage": {"covered": covered, "total": total, "ratio": round(covered / total, 4) if total else 1.0},
            "budget_spent": round(self.budget_spent, 4),
            "consecutive_zero_yield": self.consecutive_zero_yield,
            "warnings": list(dict.fromkeys(self.warnings)),
        }


@dataclass(frozen=True)
class _Decision:
    role: str | None
    score: float
    considered: tuple[dict[str, Any], ...]
    reason: str


def role_for_source(source_id: str, kinds: Sequence[str]) -> str:
    """Map a concrete connector to one of the five logical sources.

    The mapping is deliberately explicit and auditable. ``guideline-pdf-local``
    is checked before the generic guideline branch; ``guideline-fulltext-live``
    is Europe PMC; a connector whose kinds include ``pubmed`` is PubMed even if
    its id is a caller-chosen custom name.
    """
    kind_set = {str(kind) for kind in kinds}
    sid = str(source_id).lower()
    if "pubmed" in kind_set:
        return "pubmed"
    if "pdf" in sid:
        return "authorized_pdf"
    if "fulltext" in sid or "europe" in sid or "pmc" in sid:
        return "europe_pmc"
    if "guideline" in kind_set:
        return "guideline_registry"
    return "other"


def build_source_profiles(known_sources: Sequence[Mapping[str, Any]]) -> tuple[SourceProfile, ...]:
    """Group connectors by logical role; preserve registry order for replication."""
    grouped: dict[str, list[str]] = {}
    for source in known_sources:
        source_id = str(source.get("source_id") or "").strip()
        if not source_id:
            continue
        role = role_for_source(source_id, source.get("kinds") or ())
        grouped.setdefault(role, []).append(source_id)
    profiles = []
    for role, source_ids in grouped.items():
        profiles.append(SourceProfile(
            role=role,
            source_ids=tuple(source_ids),
            cost=float(SOURCE_COSTS.get(role, SOURCE_COSTS["other"])),
            available=bool(source_ids),
        ))
    return tuple(profiles)


def _english_coverage(plan: QueryPlan) -> bool:
    return bool(plan.english_terms or plan.question_english_terms or plan.machine_translations)


def _coverage_terms(plan: QueryPlan) -> tuple[str, ...]:
    """Terms used for the trial's lexical coverage heuristic.

    This is intentionally a *heuristic*: a term is considered covered when its
    string (case-insensitive) appears in a retrieved document title/text. It is
    not semantic entailment, and the trace labels it as coverage, not clinical
    sufficiency.
    """
    terms: list[str] = []
    for term in (*plan.concepts, *plan.english_terms, *plan.mesh_terms):
        text = str(term).strip()
        if text and text not in terms:
            terms.append(text)
    return tuple(terms[:20])


def _coverage_map(terms: Sequence[str], documents: Sequence[Any]) -> dict[str, bool]:
    haystacks = []
    for item in documents:
        document = getattr(item, "document", item)
        haystacks.append(f"{getattr(document, 'title', '')}\n{getattr(document, 'text', '')}".casefold())
    return {
        term: any(term.casefold() in haystack for haystack in haystacks)
        for term in terms
    }


def _required_roles(intent: str, profiles: Sequence[SourceProfile]) -> tuple[str, ...]:
    """At least one role from the returned set must be queried before stop.

    Only roles that are actually available can block stopping. This prevents a
    missing PDF/Europe PMC connector from forcing an infinite loop.
    """
    available = {profile.role for profile in profiles if profile.available}
    if intent == "guideline_recommendation":
        wanted = ("guideline_registry",)
    elif intent in {"evidence_update", "drug_safety"}:
        wanted = ("pubmed",)
    else:
        wanted = ("guideline_registry", "pubmed")
    return tuple(role for role in wanted if role in available)


def _sufficiency(state: ControllerState, plan: QueryPlan, profiles: Sequence[SourceProfile],
                 coverage_terms: Sequence[str], policy: ControllerPolicy) -> dict[str, Any]:
    total = len(coverage_terms)
    covered = sum(1 for term in coverage_terms if state.coverage.get(term))
    ratio = covered / total if total else 1.0
    required = _required_roles(plan.clinical_intent, profiles)
    required_satisfied = not required or bool(set(state.queried_roles) & set(required))
    enough = (
        len(state.evidence_ids) >= policy.min_evidence
        and ratio >= policy.min_coverage
        and required_satisfied
    )
    return {
        "sufficient": enough,
        "evidence_count": len(state.evidence_ids),
        "coverage": {"covered": covered, "total": total, "ratio": round(ratio, 4)},
        "required_roles": list(required),
        "required_satisfied": required_satisfied,
        "queried_roles": list(state.queried_roles),
        "min_evidence": policy.min_evidence,
        "min_coverage": policy.min_coverage,
    }


class RetrievalController:
    """Deterministic sequential source controller for trial runs."""

    def __init__(self, pipeline: EvidenceRetrievalPipeline, *, policy: ControllerPolicy | None = None) -> None:
        self.pipeline = pipeline
        self.policy = policy or ControllerPolicy()

    def run(self, request: RetrievalRequest) -> dict[str, Any]:
        known_sources = self.pipeline.registry.describe()
        plan = build_query_plan(request, known_sources, library=getattr(self.pipeline, "term_mapping_library", None))
        # Respect an explicit caller restriction (source kinds or source ids):
        # the controller may only choose connectors that survived planning.
        allowed_source_ids = set(plan.source_order)
        profiles = tuple(
            SourceProfile(
                role=profile.role,
                source_ids=tuple(sid for sid in profile.source_ids if sid in allowed_source_ids),
                cost=profile.cost,
                available=bool(profile.source_ids and any(sid in allowed_source_ids for sid in profile.source_ids)),
            )
            for profile in build_source_profiles(known_sources)
        )
        coverage_terms = _coverage_terms(plan)
        state = ControllerState()
        rounds: list[dict[str, Any]] = []

        # Round 0: patient facts, always local. Never routed to external sources.
        snapshot = request.patient_snapshot if isinstance(request.patient_snapshot, PatientSnapshot) else None
        if snapshot is None:
            rounds.append(self._round_record(
                state, None, [], "患者事实：未提供 patient_snapshot，跳过本地检索。",
                [], {"visible_facts": 0, "patient_hits": 0}, None, coverage_terms,
            ))
        else:
            patient_hits, patient_trace = retrieve_patient_facts(
                snapshot,
                (request.question, *request.patient_terms, *request.english_terms, *request.mesh_terms),
                top_k=max(request.top_k * 2, 20),
            )
            state.patient_hit_count = len(patient_hits)
            state.visible_fact_count = int(patient_trace.get("visible_fact_count") or 0)
            state.warnings.extend(patient_trace.get("warnings") or [])
            rounds.append(self._round_record(
                state, "patient_facts", [], "患者事实始终本地检索；不发送给外部连接器。",
                [], {
                    "visible_facts": state.visible_fact_count,
                    "patient_hits": state.patient_hit_count,
                    "suppressed_future": patient_trace.get("suppressed_future", 0),
                }, None, coverage_terms,
            ))

        stop_reason: str | None = None
        while state.round < self.policy.max_rounds:
            decision = self._choose(plan, profiles, state)
            if decision.role is None:
                stop_reason = decision.reason
                break
            profile = next(item for item in profiles if item.role == decision.role)
            state.round += 1
            package = self.pipeline.retrieve(request, only_source_ids=profile.source_ids)
            documents = list(package.ranked_documents)
            evidence_keys = [item.document.identity_key() for item in documents]
            previous = set(state.evidence_ids)
            new_keys = [key for key in evidence_keys if key not in previous]
            state.evidence_ids = tuple(sorted(previous | set(evidence_keys)))
            state.queried_roles = (*state.queried_roles, decision.role)
            state.queried_source_ids = tuple(dict.fromkeys((*state.queried_source_ids, *profile.source_ids)))
            state.source_evidence_count[decision.role] = len(documents)
            state.budget_spent = round(state.budget_spent + profile.cost, 4)
            state.consecutive_zero_yield = state.consecutive_zero_yield + 1 if not new_keys else 0
            new_coverage = _coverage_map(coverage_terms, documents)
            state.coverage = {
                term: bool(state.coverage.get(term) or new_coverage.get(term))
                for term in coverage_terms
            }
            state.warnings.extend(package.warnings)

            stop_after = self._stop_after(state, plan, profiles, coverage_terms)
            rounds.append(self._round_record(
                state, decision.role, list(profile.source_ids), decision.reason,
                list(decision.considered), {
                    "retrieval_id": package.retrieval_id,
                    "documents": len(documents),
                    "new_documents": len(new_keys),
                    "new_document_ids": [item.document.document_id for item in documents if item.document.identity_key() in set(new_keys)],
                    "warnings": list(package.warnings),
                }, stop_after, coverage_terms,
            ))
            if stop_after is not None and stop_after.get("decision") == "stop":
                stop_reason = stop_after["reason"]
                break

        if stop_reason is None:
            stop_reason = "max_rounds"
        stop_state = state.snapshot(coverage_terms)
        final = {
            "evidence_count": len(state.evidence_ids),
            "evidence_ids": list(state.evidence_ids),
            "source_roles": list(state.queried_roles),
            "source_ids": list(state.queried_source_ids),
            "source_evidence_count": dict(state.source_evidence_count),
            "patient_hits": state.patient_hit_count,
            "visible_facts": state.visible_fact_count,
            "coverage": stop_state["coverage"],
            "budget_spent": state.budget_spent,
            "rounds": state.round,
            "warnings": list(dict.fromkeys(state.warnings)),
        }
        return {
            "controller_version": CONTROLLER_VERSION,
            "request_id": request.request_id,
            "policy": self.policy.to_dict(),
            "source_profiles": [profile.to_dict() for profile in profiles],
            "query_plan": {
                "clinical_intent": plan.clinical_intent,
                "concepts": list(plan.concepts),
                "english_terms": list(plan.english_terms),
                "question_english_terms": list(plan.question_english_terms),
                "english_coverage": _english_coverage(plan),
                "coverage_terms": list(coverage_terms),
            },
            "rounds": rounds,
            "stop": {"decision": "stop", "reason": stop_reason, "after_round": state.round, "state": stop_state},
            "final": final,
            "replay": self._replay_log(request, profiles, rounds, stop_reason),
        }

    def _choose(self, plan: QueryPlan, profiles: Sequence[SourceProfile], state: ControllerState) -> _Decision:
        considered: list[dict[str, Any]] = []
        candidates: list[tuple[float, SourceProfile]] = []
        english = _english_coverage(plan)
        for profile in profiles:
            if not profile.available:
                considered.append({"role": profile.role, "score": 0.0, "decision": "skip", "reason": "来源不可用"})
                continue
            if profile.role in state.queried_roles:
                considered.append({"role": profile.role, "score": 0.0, "decision": "skip", "reason": "本轮之前已查询"})
                continue
            if profile.role in {"pubmed", "europe_pmc"} and not english:
                considered.append({"role": profile.role, "score": 0.0, "decision": "skip", "reason": "无英文覆盖：英文来源只会零命中"})
                continue
            if state.budget_spent + profile.cost > self.policy.max_cost:
                considered.append({"role": profile.role, "score": 0.0, "decision": "skip", "reason": "预算不足"})
                continue
            prior = _INTENT_PRIORS.get(plan.clinical_intent, _INTENT_PRIORS["clinical_question"]).get(profile.role, 0.2)
            score = round(prior - profile.cost * self.policy.cost_weight, 4)
            considered.append({
                "role": profile.role, "score": score, "decision": "candidate",
                "reason": f"意图先验 {prior:.2f} - 成本惩罚 {profile.cost * self.policy.cost_weight:.2f}",
            })
            candidates.append((score, profile))
        if not candidates:
            if any(item["reason"] == "预算不足" for item in considered):
                return _Decision(None, 0.0, tuple(considered), "budget_exhausted")
            remaining = [profile for profile in profiles if profile.available and profile.role not in state.queried_roles]
            if not remaining:
                return _Decision(None, 0.0, tuple(considered), "sources_exhausted")
            return _Decision(None, 0.0, tuple(considered), "no_eligible_source")
        candidates.sort(key=lambda item: (-item[0], item[1].role))
        best_score, best = candidates[0]
        if best_score < self.policy.min_action_score:
            return _Decision(None, best_score, tuple(considered), "no_positive_expected_gain")
        considered = [
            {**item, "decision": "selected"} if item["role"] == best.role and item["decision"] == "candidate" else item
            for item in considered
        ]
        return _Decision(best.role, best_score, tuple(considered),
                         f"选择 {ROLE_LABELS.get(best.role, best.role)}：得分 {best_score:.2f}（意图 {plan.clinical_intent}）")

    def _stop_after(self, state: ControllerState, plan: QueryPlan, profiles: Sequence[SourceProfile],
                    coverage_terms: Sequence[str]) -> dict[str, Any] | None:
        sufficiency = _sufficiency(state, plan, profiles, coverage_terms, self.policy)
        if sufficiency["sufficient"]:
            return {"decision": "stop", "reason": "sufficient_evidence", "sufficiency": sufficiency}
        if state.budget_spent >= self.policy.max_cost:
            return {"decision": "stop", "reason": "budget_exhausted", "sufficiency": sufficiency}
        if state.consecutive_zero_yield >= self.policy.max_zero_yield_rounds:
            return {"decision": "stop", "reason": "no_new_evidence", "sufficiency": sufficiency}
        if state.round >= self.policy.max_rounds:
            return {"decision": "stop", "reason": "max_rounds", "sufficiency": sufficiency}
        return {"decision": "continue", "reason": "continue", "sufficiency": sufficiency}

    def _round_record(self, state: ControllerState, role: str | None, source_ids: list[str], reason: str,
                      considered: list[dict[str, Any]], result: Mapping[str, Any],
                      stop_after: Mapping[str, Any] | None, coverage_terms: Sequence[str]) -> dict[str, Any]:
        return {
            "round": state.round,
            "action": "query" if role else "skip",
            "role": role,
            "source_ids": list(source_ids),
            "reason": reason,
            "considered": considered,
            "result": dict(result),
            "state_after": state.snapshot(coverage_terms),
            "stop_after": dict(stop_after) if stop_after else None,
        }

    def _replay_log(self, request: RetrievalRequest, profiles: Sequence[SourceProfile],
                    rounds: Sequence[Mapping[str, Any]], stop_reason: str) -> dict[str, Any]:
        return {
            "request_fingerprint": request_fingerprint(request),
            "policy": self.policy.to_dict(),
            "source_profiles": [profile.to_dict() for profile in profiles],
            "decision_sequence": [
                {
                    "round": row["round"],
                    "action": row["action"],
                    "role": row["role"],
                    "source_ids": row["source_ids"],
                    "stop_after": (row.get("stop_after") or {}).get("reason"),
                }
                for row in rounds
            ],
            "stop_reason": stop_reason,
            "deterministic": True,
            "note": "回放只重放决策序列；检索结果需同一冻结来源与同一查询计划才能复现。",
        }


def request_fingerprint(request: RetrievalRequest) -> str:
    """Stable fingerprint without raw fact text (no free-text PHI in the log)."""
    snapshot = request.patient_snapshot if isinstance(request.patient_snapshot, PatientSnapshot) else None
    payload = {
        "question": request.question,
        "task": request.task,
        "as_of": request.as_of,
        "patient_terms": list(request.patient_terms),
        "english_terms": list(request.english_terms),
        "mesh_terms": list(request.mesh_terms),
        "user_terms": {key: list(value) for key, value in request.user_terms.items()},
        "term_mapping": [asdict(entry) for entry in request.term_mapping],
        "facets": {key: list(value) for key, value in request.facets.items()},
        "source_kinds": list(request.source_kinds),
        "top_k": request.top_k,
        "patient_context": None if snapshot is None else {
            "patient_ref": snapshot.patient_ref,
            "as_of": snapshot.as_of,
            "fact_count": len(snapshot.facts),
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def replay_controller_result(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read-only replay view: one row per recorded round plus the stop row."""
    rows = []
    for row in result.get("rounds") or []:
        rows.append({
            "round": row.get("round"),
            "action": row.get("action"),
            "role": row.get("role"),
            "source_ids": list(row.get("source_ids") or []),
            "new_documents": (row.get("result") or {}).get("new_documents"),
            "stop_after": (row.get("stop_after") or {}).get("reason"),
        })
    rows.append({
        "round": (result.get("stop") or {}).get("after_round"),
        "action": "stop",
        "role": None,
        "source_ids": [],
        "new_documents": None,
        "stop_after": (result.get("stop") or {}).get("reason"),
    })
    return rows
