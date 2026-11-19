"""Iterative agentic guideline retrieval following recent medical agentic RAG work.

The pipeline follows the task decomposition of SEMA-RAG (Interpreter / Explorer /
Arbiter), the conflict-driven multi-round querying of MA-RAG, and the
verification/gap-analysis loop of MedTrust-RAG.  It is deliberately
deterministic: the existing knowledge graph and patient graph are the retrievers,
and no LLM is required to produce a plan, a query, or an evidence decision.

This is a *following-work* engineering adaptation, not a reimplementation of
the papers' trained models or benchmark results.  The paper-specific mapping is
documented in ``docs``/README and in the returned ``provenance`` object.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any, Callable, Mapping, Sequence

from ..core.his import PatientSnapshot
from ..knowledge.retrieval import classify_intent
from ..knowledge.service import KnowledgeService, get_default_service

AGENTIC_SCHEMA = "agentic-iterative-retrieval"
AGENTIC_VERSION = "2026-11-17"


@dataclass(frozen=True)
class AgenticBudget:
    max_rounds: int = 3
    max_queries_per_round: int = 5
    min_evidence_coverage: float = 0.8

    def __post_init__(self) -> None:
        if self.max_rounds < 1 or self.max_queries_per_round < 1:
            raise ValueError("agentic budget must be positive")
        if not 0 <= self.min_evidence_coverage <= 1:
            raise ValueError("min_evidence_coverage must be in [0, 1]")


@dataclass
class ClinicalSchema:
    intent: str
    intent_method: str
    entities: list[str] = field(default_factory=list)
    target_actions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    initial_ambiguous: list[str] = field(default_factory=list)
    initial_query: str = ""


@dataclass
class MedicalGap:
    missing_information: list[str] = field(default_factory=list)
    unresolved_terms: list[str] = field(default_factory=list)
    ambiguous_terms: list[str] = field(default_factory=list)
    conflicting_recommendations: list[str] = field(default_factory=list)
    critical_conflicts: list[str] = field(default_factory=list)
    excluded_targets: list[str] = field(default_factory=list)
    low_coverage_recommendations: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    next_queries: list[str] = field(default_factory=list)


@dataclass
class EvidenceLedger:
    recommendations: dict[str, dict[str, Any]] = field(default_factory=dict)
    excluded: dict[str, dict[str, Any]] = field(default_factory=dict)
    evidence_spans: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    patient_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    external_documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    external_source_counts: dict[str, int] = field(default_factory=dict)
    missing_information: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    rounds: list[dict[str, Any]] = field(default_factory=list)
    seen_queries: set[str] = field(default_factory=set)

    def absorb(self, context: Mapping[str, Any], *, round_index: int, queries: Sequence[str]) -> dict[str, Any]:
        before = set(self.recommendations)
        for row in context.get("recommendations") or ():
            self._merge_row(self.recommendations, row)
        for row in context.get("excluded_recommendations") or ():
            self._merge_excluded(row)
        for span_id, span in (context.get("evidence_spans") or {}).items():
            self.evidence_spans.setdefault(str(span_id), deepcopy(span))
        for source_id, source in (context.get("sources") or {}).items():
            self.sources.setdefault(str(source_id), deepcopy(source))
        for row in context.get("recommendations") or ():
            for evidence in row.get("patient_graph_evidence") or ():
                self.patient_evidence.setdefault(str(evidence.get("record_path")), deepcopy(evidence))
        for item in context.get("missing_information") or ():
            if item not in self.missing_information:
                self.missing_information.append(str(item))
        for conflict in context.get("conflicts") or ():
            if conflict not in self.conflicts:
                self.conflicts.append(deepcopy(conflict))
        for query in queries:
            self.seen_queries.add(_query_key(query))
        new_ids = sorted(set(self.recommendations) - before)
        self.rounds.append({
            "round": round_index,
            "queries": list(queries),
            "status": context.get("status"),
            "new_recommendations": new_ids,
            "recommendation_count": len(self.recommendations),
            "evidence_span_count": len(self.evidence_spans),
            "patient_evidence_count": len(self.patient_evidence),
            "missing_information": list(context.get("missing_information") or ()),
            "conflicts": len(context.get("conflicts") or ()),
            "fusion": (context.get("fusion") or {}).get("stats"),
        })
        return {
            "new_recommendations": new_ids,
            "new_evidence_spans": len(context.get("evidence_spans") or {}),
            "new_patient_evidence": len(context.get("patient_graph_evidence") or ()),
        }

    def absorb_external(
        self, external: Mapping[str, Any], *, round_index: int, queries: Sequence[str],
    ) -> dict[str, Any]:
        """Merge selected-source documents into the same round ledger."""
        documents = []
        for item in external.get("documents") or ():
            row = item.get("document") if isinstance(item, Mapping) and isinstance(item.get("document"), Mapping) else item
            if not isinstance(row, Mapping):
                continue
            source_id = str(row.get("source_id") or "")
            document_id = str(row.get("document_id") or "")
            if not source_id or not document_id:
                continue
            key = f"{source_id}:{document_id}"
            document = deepcopy(dict(row))
            documents.append(document)
            if key not in self.external_documents:
                self.external_documents[key] = document
                kind = str(document.get("kind") or source_id)
                self.external_source_counts[kind] = self.external_source_counts.get(kind, 0) + 1
        if self.rounds:
            self.rounds[-1]["external"] = {
                "documents": len(documents),
                "new_documents": len([
                    item for item in documents
                    if f"{item.get('source_id')}:{item.get('document_id')}" not in self.external_documents
                ]),
                "source_counts": dict(external.get("source_counts") or {}),
                "warnings": list(external.get("warnings") or ()),
            }
        return {
            "documents": len(documents),
            "new_documents": len(documents),
            "source_counts": dict(external.get("source_counts") or {}),
        }

    def _merge_row(self, target: dict[str, dict[str, Any]], row: Mapping[str, Any]) -> None:
        rec_id = str(row.get("recommendation_id") or "")
        if not rec_id:
            return
        if rec_id not in target:
            target[rec_id] = deepcopy(dict(row))
            return
        current = target[rec_id]
        for key in ("evidence_span_ids", "source_ids", "runtime_binding_ids", "monitoring_panels"):
            current[key] = list(dict.fromkeys([*(current.get(key) or ()), *(row.get(key) or ())]))
        evidence = {item.get("record_path"): item for item in current.get("patient_graph_evidence") or ()}
        for item in row.get("patient_graph_evidence") or ():
            evidence.setdefault(item.get("record_path"), item)
        current["patient_graph_evidence"] = list(evidence.values())
        current["missing_information"] = list(dict.fromkeys(
            [*(current.get("missing_information") or ()), *(row.get("missing_information") or ())]
        ))
        if row.get("fusion") and current.get("fusion"):
            current["fusion"] = {
                **current["fusion"],
                **row["fusion"],
                "matched_references": max(
                    int(current["fusion"].get("matched_references", 0)),
                    int(row["fusion"].get("matched_references", 0)),
                ),
            }

    def _merge_excluded(self, row: Mapping[str, Any]) -> None:
        rec_id = str(row.get("id") or "")
        if rec_id:
            self.excluded.setdefault(rec_id, deepcopy(dict(row)))


class AgenticIterativeRetriever:
    def __init__(
        self,
        service: KnowledgeService | None = None,
        *,
        budget: AgenticBudget | None = None,
    ) -> None:
        self.service = service or get_default_service()
        self.budget = budget or AgenticBudget()

    def run(
        self,
        queries: Sequence[str],
        snapshot: PatientSnapshot,
        *,
        intent: str | None = None,
        source_kinds: Sequence[str] = (),
        external_search: Callable[[Sequence[str], Sequence[str]], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        schema = self._interpret(queries, snapshot, intent)
        ledger = EvidenceLedger()
        current_queries = [query for query in queries if str(query).strip()]
        if not current_queries:
            current_queries = self._patient_queries(snapshot)[: self.budget.max_queries_per_round]
        current_queries = self._dedupe(current_queries)[: self.budget.max_queries_per_round]
        if not current_queries:
            current_queries = schema.entities[: self.budget.max_queries_per_round]
        gaps = MedicalGap()
        last_context: dict[str, Any] = {}
        stop_reason = "budget_exhausted"
        sufficient = False
        for round_index in range(self.budget.max_rounds):
            context = self.service.search(
                current_queries, snapshot,
                intent=schema.intent,
                fusion=True,
                fusion_mode="full",
            )
            last_context = context
            delta = ledger.absorb(context, round_index=round_index, queries=current_queries)
            external: dict[str, Any] = {}
            if external_search is not None and source_kinds:
                try:
                    external = dict(external_search(current_queries, list(source_kinds)) or {})
                except Exception as exc:  # external source failure must be explicit, not fatal
                    external = {
                        "documents": [], "source_counts": {},
                        "warnings": [f"{type(exc).__name__}: {str(exc)[:180]}"],
                    }
                ledger.absorb_external(external, round_index=round_index, queries=current_queries)
            gaps = self._gap_analysis(context, ledger, schema, current_queries)
            if source_kinds:
                gaps.missing_sources = sorted(
                    set(str(item) for item in source_kinds)
                    - {kind for kind, count in ledger.external_source_counts.items() if count}
                )
            sufficient = self._sufficient(context, gaps, ledger, schema)
            if external_search is not None and not ledger.recommendations and ledger.external_documents:
                stop_reason = "external_evidence"
                break
            if sufficient:
                stop_reason = "sufficient"
                break
            if schema.initial_ambiguous:
                stop_reason = "needs_disambiguation"
                break
            if gaps.excluded_targets and not [
                row for row in context.get("recommendations") or ()
                if str(row.get("action_id") or "") in set(schema.target_actions)
                and row.get("eligible_for_simulation")
            ]:
                stop_reason = "not_applicable"
                break
            if not gaps.next_queries:
                stop_reason = "needs_review" if gaps.critical_conflicts else (
                    "no_new_evidence" if delta["new_recommendations"] else "needs_information"
                )
                break
            if round_index + 1 >= self.budget.max_rounds:
                stop_reason = "budget_exhausted"
                break
            current_queries = gaps.next_queries[: self.budget.max_queries_per_round]
        return self._arbiter(
            schema=schema, ledger=ledger, gaps=gaps,
            last_context=last_context, sufficient=sufficient, stop_reason=stop_reason,
        )

    # ---------------------------------------------------------------- interpreter
    def _interpret(self, queries: Sequence[str], snapshot: PatientSnapshot, intent: str | None) -> ClinicalSchema:
        resolved_intent, method = classify_intent(queries, intent)
        entities: list[str] = []
        target_actions: list[str] = []
        constraints: list[str] = []
        initial_ambiguous: list[str] = []
        for query in queries:
            resolution = self.service.resolve_terms([str(query)])["terms"][0]
            if resolution.get("status") == "ambiguous":
                initial_ambiguous.append(str(query))
            for candidate in resolution.get("candidates") or ():
                label = str(candidate.get("label") or "")
                if label and label not in entities:
                    entities.append(label)
                if candidate.get("type") == "action":
                    action_id = str(candidate.get("id") or "")
                    if action_id and action_id not in target_actions:
                        target_actions.append(action_id)
        for record in snapshot.visible_records():
            fields = record.fields
            for key in ("name", "symptom", "symptoms", "mode"):
                raw = fields.get(key)
                values = raw if isinstance(raw, (list, tuple)) else (raw,)
                for value in values:
                    text = str(value or "").strip()
                    if text and text not in entities:
                        entities.append(text)
            for key in ("ga_text", "pna_days", "birth_weight_g", "care_setting"):
                if fields.get(key) not in (None, ""):
                    constraints.append(f"{key}:{fields[key]}")
        initial_query = "；".join(
            [*(str(q) for q in queries if str(q).strip()), *entities[:4], *constraints[:3]]
        )
        return ClinicalSchema(
            intent=resolved_intent, intent_method=method,
            entities=entities[:8], target_actions=target_actions[:8],
            constraints=constraints[:8], initial_ambiguous=initial_ambiguous[:5],
            initial_query=initial_query,
        )

    # ---------------------------------------------------------------- explorer
    def _gap_analysis(
        self,
        context: Mapping[str, Any],
        ledger: EvidenceLedger,
        schema: ClinicalSchema,
        queried: Sequence[str],
    ) -> MedicalGap:
        missing = [str(item) for item in context.get("missing_information") or ()]
        unresolved = [
            str(row["input"]) for row in context.get("term_resolutions") or ()
            if row.get("status") == "unresolved"
        ]
        ambiguous = [
            str(row["input"]) for row in context.get("term_resolutions") or ()
            if row.get("status") == "ambiguous"
        ]
        conflicts = [
            " ".join(str(item) for item in row.get("recommendations") or ())
            for row in context.get("conflicts") or ()
        ]
        critical_conflicts = self._critical_conflicts(context)
        target_actions = set(schema.target_actions)
        target_operation = _intent_operation(schema.intent)
        excluded_targets = [
            str(row.get("id")) for row in context.get("excluded_recommendations") or ()
            if str(row.get("action_id") or "") in target_actions
            and (not target_operation or target_operation in set(
                (self.service.graph.recommendations.get(str(row.get("id"))) or {}).get("supported_operations") or ()
            ))
        ] if target_actions else []
        low_coverage = [
            row["recommendation_id"] for row in context.get("recommendations") or ()
            if (row.get("fusion") or {}).get("coverage", 1.0) < self.budget.min_evidence_coverage
        ]
        candidates: list[str] = []
        # MA-RAG: conflicts become targeted evidence queries.
        for row in context.get("conflicts") or ():
            for rec_id in row.get("recommendations") or ():
                rec = self.service.graph.recommendations.get(str(rec_id)) or {}
                action = self.service.graph.actions.get(str(rec.get("action_id") or "")) or {}
                label = str(action.get("label") or "").strip()
                if label:
                    candidates.append(label)
                for alias in (action.get("aliases") or ())[:1]:
                    candidates.append(str(alias))
        # MedTrust / SEMA: missing dimensions -> condition summaries.
        for row in context.get("recommendations") or ():
            dims = row.get("applicability") or {}
            if not any(status in {"unknown", "conflict"} for status in dims.values()):
                continue
            rec = self.service.graph.recommendations.get(str(row.get("recommendation_id"))) or {}
            population = self.service.graph.populations.get(str(rec.get("population_id") or "")) or {}
            for text in (population.get("summary"),):
                if text:
                    candidates.append(str(text))
            for condition_id in (
                *list(rec.get("indications") or ()),
                *list(rec.get("exclusions") or ()),
                *list(rec.get("temporal_conditions") or ()),
            ):
                condition = self.service.graph.conditions.get(str(condition_id)) or {}
                if condition.get("summary"):
                    candidates.append(str(condition["summary"]))
        # Unresolved/ambiguous terms remain explicit; never force a candidate.
        candidates.extend(unresolved)
        # No recommendations: use patient-driven anchors.
        if not context.get("recommendations") and not context.get("excluded_recommendations"):
            candidates.extend(self._patient_queries_from_schema(schema))
        next_queries = [
            query for query in self._dedupe(candidates)
            if _query_key(query) not in ledger.seen_queries
            and _query_key(query) not in {_query_key(item) for item in queried}
        ][: self.budget.max_queries_per_round]
        return MedicalGap(
            missing_information=missing,
            unresolved_terms=unresolved,
            ambiguous_terms=ambiguous,
            conflicting_recommendations=conflicts,
            critical_conflicts=critical_conflicts,
            excluded_targets=excluded_targets,
            low_coverage_recommendations=low_coverage,
            next_queries=next_queries,
        )

    def _critical_conflicts(self, context: Mapping[str, Any]) -> list[str]:
        critical: list[str] = []
        for row in context.get("conflicts") or ():
            rec_ids = [str(item) for item in row.get("recommendations") or ()]
            directions = {
                str((self.service.graph.recommendations.get(rec_id) or {}).get("direction") or "")
                for rec_id in rec_ids
            }
            if len(directions) > 1:
                critical.append(" ".join(rec_ids))
        return critical

    def _sufficient(
        self, context: Mapping[str, Any], gaps: MedicalGap,
        ledger: EvidenceLedger, schema: ClinicalSchema,
    ) -> bool:
        recommendations = list(context.get("recommendations") or ())
        if not recommendations:
            return False
        if gaps.unresolved_terms or schema.initial_ambiguous or gaps.critical_conflicts:
            return False
        coverage = float(((context.get("fusion") or {}).get("stats") or {}).get("reference_coverage", 1.0))
        if coverage < self.budget.min_evidence_coverage:
            return False
        target_actions = set(schema.target_actions)
        target_rows = [
            row for row in recommendations
            if str(row.get("action_id") or "") in target_actions
        ] if target_actions else []
        if target_actions and not target_rows:
            return False
        focus = target_rows or recommendations
        eligible = [row for row in focus if row.get("eligible_for_simulation")]
        # An actionable recommendation for the requested action is sufficient even
        # when unrelated alternatives still list their own gaps.  Without an
        # eligible target recommendation, every missing item remains blocking.
        if eligible:
            return not any(row.get("missing_information") for row in eligible)
        if gaps.excluded_targets or target_rows:
            return False
        return not gaps.missing_information

    def _patient_queries(self, snapshot: PatientSnapshot) -> list[str]:
        return self._patient_queries_from_schema(ClinicalSchema(intent="assess", intent_method="default"))

    def _patient_queries_from_schema(self, schema: ClinicalSchema) -> list[str]:
        return self._dedupe([*schema.entities[:6], *schema.constraints[:4]])

    @staticmethod
    def _dedupe(values: Sequence[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            key = _query_key(text)
            if text and key not in seen:
                seen.add(key)
                result.append(text)
        return result

    # ---------------------------------------------------------------- arbiter
    def _arbiter(
        self,
        *,
        schema: ClinicalSchema,
        ledger: EvidenceLedger,
        gaps: MedicalGap,
        last_context: Mapping[str, Any],
        sufficient: bool,
        stop_reason: str,
    ) -> dict[str, Any]:
        recommendations = list(ledger.recommendations.values())
        citation_ready = sum(1 for row in recommendations if row.get("evidence_span_ids"))
        citation_coverage = round(citation_ready / len(recommendations), 4) if recommendations else 0.0
        status = (
            "sufficient" if sufficient
            else "not_applicable" if stop_reason == "not_applicable"
            else "needs_review" if gaps.critical_conflicts or stop_reason == "needs_review"
            else "needs_disambiguation" if stop_reason == "needs_disambiguation"
            else "external_evidence" if stop_reason == "external_evidence"
            else "no_results" if not recommendations
            else "needs_information" if stop_reason in {"needs_information", "no_new_evidence"}
            else "budget_exhausted"
        )
        return {
            "schema_version": AGENTIC_SCHEMA,
            "agentic_version": AGENTIC_VERSION,
            "knowledge_context_id": last_context.get("knowledge_context_id"),
            "retrieval_id": last_context.get("retrieval_id"),
            "knowledge_release_id": last_context.get("knowledge_release_id"),
            "release_sha256": last_context.get("release_sha256"),
            "snapshot_sha256": last_context.get("snapshot_sha256"),
            "clinical_schema": {
                "intent": schema.intent,
                "intent_method": schema.intent_method,
                "entities": list(schema.entities),
                "target_actions": list(schema.target_actions),
                "constraints": list(schema.constraints),
                "initial_ambiguous": list(schema.initial_ambiguous),
                "initial_query": schema.initial_query,
            },
            "rounds": ledger.rounds,
            "recommendations": recommendations,
            "excluded_recommendations": list(ledger.excluded.values()),
            "evidence_spans": ledger.evidence_spans,
            "sources": ledger.sources,
            "patient_graph_evidence": list(ledger.patient_evidence.values()),
            "external_documents": list(ledger.external_documents.values()),
            "external_source_counts": dict(ledger.external_source_counts),
            "missing_information": ledger.missing_information,
            "conflicts": ledger.conflicts,
            "gap_analysis": {
                "unresolved_terms": gaps.unresolved_terms,
                "ambiguous_terms": gaps.ambiguous_terms,
                "conflicting_recommendations": gaps.conflicting_recommendations,
                "critical_conflicts": gaps.critical_conflicts,
                "excluded_targets": gaps.excluded_targets,
                "low_coverage_recommendations": gaps.low_coverage_recommendations,
                "missing_sources": gaps.missing_sources,
                "next_queries": gaps.next_queries,
            },
            "stop_reason": stop_reason,
            "sufficient": sufficient,
            "status": status,
            "citation_coverage": citation_coverage,
            "patient_evidence_coverage": _average_patient_coverage(recommendations),
            "provenance": {
                "interpreter": "SEMA-RAG I-Agent structured clinical schema (deterministic adaptation)",
                "explorer": "SEMA-RAG E-Agent sufficiency loop + MedTrust Medical Gap Analysis + MA-RAG conflict-driven queries",
                "arbiter": "SEMA-RAG A-Agent evidence adjudication + MedTrust citation/verification gate",
                "retrievers": "existing patient graph + guideline knowledge graph fusion + selected external sources (PubMed / local guidelines / drug labels); no external LLM",
            },
            "boundary": (
                "Iterative retrieval/evidence packet only; not a clinical answer, diagnosis or treatment authorization. "
                "Synthetic demo release and contract metrics are not clinical effectiveness evidence."
            ),
        }


def _intent_operation(intent: str) -> str:
    return {
        "start": "start", "adjust": "adjust", "maintain": "continue",
        "stop": "stop", "observe": "wait", "review": "order_test",
    }.get(intent, "")


def _average_patient_coverage(recommendations: Sequence[Mapping[str, Any]]) -> float:
    coverages = [
        float((row.get("fusion") or {}).get("coverage", 0.0))
        for row in recommendations
    ]
    return round(sum(coverages) / len(coverages), 4) if coverages else 0.0


def _query_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())
