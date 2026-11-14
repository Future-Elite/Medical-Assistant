"""Executable guideline-applicability resolver.

This is the workflow layer above the comparison.  It does not just display
coverage/snippets/query; it chooses and executes actions:

1. assess the target guideline;
2. switch to another local guideline whose criteria cover the patient;
3. return ``needs_table_lookup`` when the guideline requires a table/curve;
4. ask for a missing fact and ``resume`` after the caller supplies it;
5. execute an external-evidence search through an injected callback;
6. stop with ``scope_mismatch`` when the structured scope criteria contradict
   the record, or ``evidence_gap`` when nothing is found / no search backend
   exists.  ``scope_mismatch`` is not clinical inapplicability.

The external search callback is injected so offline tests never call PubMed.
Production can pass the existing PubMed connector as the callback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import secrets
from typing import Any, Callable, Mapping, Sequence

from ...quality.applicability import assess_applicability
from ...quality.guideline_lifecycle_index import load_lifecycle_index
from ...his import snapshot_from_dict
from .gold_rules import load_criteria_fixture
from .v2_fixture import load_v2_fixture
from .decision_triage import apply_value_to_snapshot, derived_from_snapshot, triage_snapshot
from .families import family_index, family_of
from .followup import build_followup
from .rubric import build_documents

SearchFn = Callable[[str], Sequence[Mapping[str, Any] | str]]

def _target_terms(
    guideline: Mapping[str, Any] | None, topic: str | None = None,
) -> tuple[str, ...]:
    """Derive external-search terms from guideline metadata.

    ``search_terms`` is declared per guideline in the fixture/catalog; an
    explicit ``target_topic`` always wins.  No guideline IDs live in code.
    """
    if topic:
        return (str(topic).replace("_", " ").strip().lower(),)
    if not guideline:
        return ()
    return tuple(
        str(term) for term in (guideline.get("search_terms") or ()) if str(term).strip()
    )


_FIELD_LABELS = {
    "derived_birth.ga_days": "胎龄",
    "derived_birth.birth_weight_g": "出生体重",
    "derived_respiratory_support.mode": "呼吸支持方式",
    "derived_labs.max_tbil_umol_l": "胆红素",
}
_SUPPORTED_FACT_FIELDS = frozenset(_FIELD_LABELS)
_POPULATION_TERMS = (
    "preterm", "gestational age", "37 weeks", "35 weeks", "very low birth weight",
    "vlbw", "birth weight", "respiratory support", "cpap", "mechanical ventilation",
    "neonatal", "newborn", "infant",
)


@dataclass(frozen=True)
class ResolverPolicy:
    max_external_results: int = 5


@dataclass
class ApplicabilityResolver:
    # The 4-guideline evaluation corpus is the default.  AAP is a separate
    # capability fixture and is only added when the caller explicitly targets it.
    fixture: Mapping[str, Any] = field(default_factory=load_criteria_fixture)
    capability_fixture: Mapping[str, Any] = field(default_factory=load_v2_fixture)
    search_fn: SearchFn | None = None
    policy: ResolverPolicy = field(default_factory=ResolverPolicy)
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    documents: dict[str, Any] = field(init=False)
    capability_documents: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self.documents = build_documents(self.fixture)
        self.capability_documents = build_documents(self.capability_fixture)
        self.guidelines = tuple(self.fixture.get("guidelines") or ())
        self.families = family_index(self.guidelines)

    def _guideline_record(self, guideline_id: str | None) -> Mapping[str, Any] | None:
        for item in self.guidelines:
            if item.get("document_id") == guideline_id:
                return item
        return None

    def resolve(
        self,
        *,
        question: str,
        snapshot: Mapping[str, Any],
        target_guideline_id: str | None = None,
        target_topic: str | None = None,
    ) -> dict[str, Any]:
        resolution_id = "resolution-" + secrets.token_hex(8)
        result = self._resolve(
            question=question, snapshot=snapshot,
            target_guideline_id=target_guideline_id, target_topic=target_topic,
        )
        result["resolution_id"] = resolution_id
        result["parallel_retrieval"] = self._parallel_retrieval(
            snapshot=snapshot, target_guideline_id=target_guideline_id,
            target_topic=target_topic, assessments=result.get("assessments", {}),
        )
        self.sessions[resolution_id] = {
            "question": question,
            "snapshot": snapshot,
            "target_guideline_id": target_guideline_id,
            "target_topic": target_topic,
        }
        return result

    def resume(
        self, resolution_id: str, supplied_facts: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        try:
            session = self.sessions[resolution_id]
        except KeyError:
            raise ValueError("unknown resolution_id") from None
        snapshot = session["snapshot"]
        for item in supplied_facts:
            field = str(item.get("field") or "")
            if not field or "value" not in item:
                raise ValueError("supplied_facts require field and value")
            if field not in _SUPPORTED_FACT_FIELDS:
                raise ValueError(f"unsupported supplied fact field: {field}")
            snapshot = apply_value_to_snapshot(snapshot, field, item["value"])
        session["snapshot"] = snapshot
        result = self._resolve(
            question=session["question"], snapshot=snapshot,
            target_guideline_id=session["target_guideline_id"],
            target_topic=session["target_topic"],
        )
        result["resolution_id"] = resolution_id
        result["resumed"] = True
        result["parallel_retrieval"] = self._parallel_retrieval(
            snapshot=session["snapshot"], target_guideline_id=session["target_guideline_id"],
            target_topic=session["target_topic"], assessments=result.get("assessments", {}),
        )
        return result

    def _parallel_retrieval(
        self, *, snapshot: Mapping[str, Any], target_guideline_id: str | None,
        target_topic: str | None, assessments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Parallel display signals: coverage, qualifier snippets, family status."""
        gold = {
            "guidelines": [
                {
                    "guideline_id": document_id,
                    "status": assessment.get("status"),
                    "criteria": assessment.get("criteria", []),
                }
                for document_id, assessment in assessments.items()
            ]
        }
        try:
            result = build_followup(
                snapshot=snapshot,
                derived=derived_from_snapshot(snapshot),
                target_guideline_id=target_guideline_id,
                target_topic=target_topic,
                gold=gold,
                fixture=self.fixture,
            )
            result.update(self._lifecycle_signal(assessments, snapshot))
            return result
        except Exception as exc:  # display layer must not break the resolution
            return {
                "coverage_guidelines": [],
                "population_qualifiers": [],
                "status_families": [],
                "external_query": None,
                "lifecycle_status": "not_checked",
                "error": f"{type(exc).__name__}: {str(exc)[:180]}",
            }

    def _lifecycle_signal(
        self, assessments: Mapping[str, Any], snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        documents = dict(self.documents)
        documents.update(self.capability_documents)
        selected = [
            documents[document_id] for document_id in assessments
            if document_id in documents
        ]
        as_of = str(snapshot.get("as_of") or "").strip() or None
        signal = load_lifecycle_index().statuses(selected, as_of=as_of)
        return {
            "lifecycle_status": signal["overall"],
            "lifecycle": signal["guidelines"],
            "lifecycle_checked_at": signal["checked_at"],
            "lifecycle_source": signal["source"],
        }

    def _resolve(
        self, *, question: str, snapshot: Mapping[str, Any],
        target_guideline_id: str | None, target_topic: str | None,
    ) -> dict[str, Any]:
        parsed = snapshot_from_dict(snapshot)
        documents = dict(self.documents)
        if target_guideline_id and target_guideline_id in self.capability_documents:
            documents[target_guideline_id] = self.capability_documents[target_guideline_id]
        assessments = {
            document_id: assess_applicability(document, parsed, [])
            for document_id, document in documents.items()
        }
        titles = {document_id: document.title for document_id, document in documents.items()}
        if target_guideline_id and target_guideline_id not in assessments:
            raise ValueError(f"unknown target_guideline_id: {target_guideline_id}")
        target_id = target_guideline_id or (None if target_topic else _choose_target(assessments))
        target = assessments.get(target_id) if target_id else None
        target_terms = _target_terms(self._guideline_record(target_id), target_topic)
        rounds: list[dict[str, Any]] = [{
            "round": 0,
            "action": "assess",
            "target_guideline_id": target_id,
            "target_topic": target_topic,
            "target_status": target.status if target else None,
        }]
        if target is not None and target.status == "possible_match":
            return _result("resolved", target_id, assessments, rounds, titles=titles,
                           selected=[target_id], reason="target guideline criteria match")
        if target is not None and _table_lookup_ready(target):
            return _result(
                "needs_table_lookup", target_id, assessments, rounds, titles=titles,
                selected=[],
                lookup_fields=[
                    criterion.field for criterion in target.criteria
                    if criterion.operator == "table_lookup" and criterion.status == "unknown"
                ],
                lookup_locators=sorted({
                    criterion.source_locator for criterion in target.criteria
                    if criterion.operator == "table_lookup" and criterion.source_locator
                }),
                reason="guideline requires table/curve lookup; no automatic threshold comparison",
            )
        family = family_of(target_id, self.families)
        all_coverage = [] if target_topic else [
            document_id for document_id, assessment in assessments.items()
            if document_id != target_id and assessment.status == "possible_match"
        ]
        coverage = [
            document_id for document_id in all_coverage
            if family is None or family_of(document_id, self.families) == family
        ]
        cross_family = [document_id for document_id in all_coverage if document_id not in coverage]
        triage = _triage(snapshot, target_id, target_topic, target)
        requests = [
            {
                "field": item["field"],
                "label": item["label"],
                "source": item["source"],
                "why": item["why"],
            }
            for item in triage["target_priorities"]
        ]
        if requests and target_topic is None:
            rounds.append({
                "round": 1,
                "action": "request_information",
                "requests": requests,
                "stop_after": "needs_information",
            })
            return _result("needs_information", target_id, assessments, rounds, titles=titles,
                           selected=coverage, other_local_guidelines=cross_family,
                           information_requests=requests,
                           reason="missing facts could change the target guideline")
        if coverage:
            rounds.append({
                "round": 1,
                "action": "other_registered",
                "new_candidates": coverage,
                "stop_after": "resolved_by_other_local_guideline",
            })
            return _result("resolved", target_id, assessments, rounds, titles=titles,
                           selected=coverage, other_local_guidelines=cross_family,
                           reason="another local guideline in the same family covers the patient")
        query = _external_query(target_terms, target_topic)
        target_out_of_scope = target is not None and target.status == "mismatch"
        if target_out_of_scope and self.search_fn is None:
            rounds.append({
                "round": 1,
                "action": "external_search",
                "query": query,
                "new_candidates": [],
                "stop_after": "scope_mismatch",
                "reason": "search_backend_not_provided",
            })
            return _result("scope_mismatch", target_id, assessments, rounds, titles=titles,
                           selected=coverage, other_local_guidelines=cross_family,
                           query=query, failed_criteria=_failed_criteria(target),
                           reason=_scope_reason(cross_family, searched=False))
        if self.search_fn is None:
            rounds.append({
                "round": 1,
                "action": "external_search",
                "query": query,
                "new_candidates": [],
                "stop_after": "evidence_gap",
                "reason": "search_backend_not_provided",
            })
            return _result("evidence_gap", target_id, assessments, rounds, titles=titles,
                           selected=coverage, other_local_guidelines=cross_family,
                           query=query,
                           reason="no local coverage; external search backend not provided")
        raw = list(self.search_fn(query))
        screened = _screen(raw, target_terms, target_topic)[: self.policy.max_external_results]
        rounds.append({
            "round": 1,
            "action": "external_search",
            "query": query,
            "new_candidates": screened,
            "stop_after": "scope_mismatch" if target_out_of_scope else ("external_evidence" if screened else "evidence_gap"),
        })
        if target_out_of_scope:
            return _result("scope_mismatch", target_id, assessments, rounds, titles=titles,
                           selected=coverage, other_local_guidelines=cross_family,
                           query=query, evidence=screened,
                           failed_criteria=_failed_criteria(target),
                           reason=_scope_reason(cross_family, searched=True))
        if screened:
            return _result("external_evidence", target_id, assessments, rounds, titles=titles,
                           selected=coverage, other_local_guidelines=cross_family,
                           query=query, evidence=screened,
                           reason="external candidates retrieved; not adjudicated as guideline applicability")
        return _result("evidence_gap", target_id, assessments, rounds, titles=titles,
                       selected=coverage, other_local_guidelines=cross_family,
                       query=query,
                       reason="no local or external candidate found")


def _result(
    outcome: str, target_id: str | None, assessments: Mapping[str, Any],
    rounds: Sequence[Mapping[str, Any]], *, selected: Sequence[str],
    titles: Mapping[str, str] | None = None,
    other_local_guidelines: Sequence[str] = (),
    information_requests: Sequence[Mapping[str, Any]] = (),
    evidence: Sequence[Mapping[str, Any]] = (),
    query: str | None = None,
    lookup_fields: Sequence[str] = (),
    lookup_locators: Sequence[str] = (),
    failed_criteria: Sequence[Mapping[str, Any]] = (),
    reason: str = "",
) -> dict[str, Any]:
    titles = titles or {}
    target_detail = _assessment_detail(target_id, assessments, titles)
    selected_details = [
        _assessment_detail(item, assessments, titles) for item in selected
    ]
    other_details = [
        {**_assessment_detail(item, assessments, titles),
         "note": "不同问题；不替代目标指南"}
        for item in other_local_guidelines
    ]
    external_executed = any(
        item.get("action") == "external_search" and not item.get("reason")
        for item in rounds
    )
    external_search = {
        "query": query,
        "executed": external_executed,
        "candidate_count": len(evidence),
        "candidates": list(evidence),
    }
    lookup = {
        "required": bool(lookup_fields),
        "fields": list(lookup_fields),
        "locators": list(lookup_locators),
    }
    next_action = _next_action(
        outcome, information_requests, lookup_fields, selected, evidence
    )
    provenance = {
        "guideline_locators": sorted({
            criterion.get("locator") for criterion in (target_detail or {}).get("criteria", [])
            if criterion.get("locator")
        } | {
            criterion.get("locator") for detail in selected_details
            for criterion in detail.get("criteria", []) if criterion.get("locator")
        }),
        "external_ids": [
            {"pmid": item.get("pmid"), "doi": item.get("doi")}
            for item in evidence if item.get("pmid") or item.get("doi")
        ],
    }
    return {
        "outcome": outcome,
        "selected_guidelines": list(selected),
        "selected_guideline_details": [item for item in selected_details if item],
        "other_local_guidelines": list(other_local_guidelines),
        "other_local_guideline_details": [item for item in other_details if item.get("guideline_id")],
        "target": {
            "guideline_id": target_id,
            "title": (target_detail or {}).get("title"),
            "status": assessments[target_id].status if target_id in assessments else None,
        },
        "target_detail": target_detail,
        "assessments": {
            document_id: {
                "guideline_id": document_id,
                "title": titles.get(document_id, document_id),
                "status": assessment.status,
                "matched_fields": list(assessment.matched_patient_fields),
                "missing_fields": list(assessment.missing_patient_fields),
                "criteria": [
                    {
                        "criterion_id": criterion.criterion_id,
                        "field": criterion.field,
                        "status": criterion.status,
                        "locator": criterion.source_locator,
                    }
                    for criterion in assessment.criteria
                ],
            }
            for document_id, assessment in assessments.items()
        },
        "information_requests": list(information_requests),
        "evidence": list(evidence),
        "external_query": query,
        "external_search": external_search,
        "lookup": lookup,
        "next_action": next_action,
        "stop_reason": _stop_reason(outcome),
        "provenance": provenance,
        "lookup_fields": list(lookup_fields),
        "lookup_locators": list(lookup_locators),
        "failed_criteria": list(failed_criteria),
        "rounds": list(rounds),
        "reason": reason,
    }


def _assessment_detail(
    guideline_id: str | None, assessments: Mapping[str, Any], titles: Mapping[str, str],
) -> dict[str, Any] | None:
    if not guideline_id or guideline_id not in assessments:
        return None
    assessment = assessments[guideline_id]
    return {
        "guideline_id": guideline_id,
        "title": titles.get(guideline_id, guideline_id),
        "status": assessment.status,
        "reason": assessment.reason,
        "matched_fields": list(assessment.matched_patient_fields),
        "missing_fields": list(assessment.missing_patient_fields),
        "criteria": [
            {
                "criterion_id": criterion.criterion_id,
                "field": criterion.field,
                "label": criterion.label or _FIELD_LABELS.get(criterion.field, criterion.field),
                "status": criterion.status,
                "observed_value": criterion.observed_value,
                "reason": criterion.reason,
                "locator": criterion.source_locator,
                "record_paths": list(criterion.patient_record_paths),
            }
            for criterion in assessment.criteria
        ],
    }


def _next_action(
    outcome: str, requests: Sequence[Mapping[str, Any]],
    lookup_fields: Sequence[str], selected: Sequence[str],
    evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if outcome == "needs_information":
        return {
            "type": "supply_fact",
            "fields": [item.get("field") for item in requests],
            "text": "补关键事实后 resume；只问会改变目标指南结论的字段。",
        }
    if outcome == "needs_table_lookup":
        return {
            "type": "table_lookup",
            "fields": list(lookup_fields),
            "text": "按指南原文的表/曲线核对，不自动拿单一阈值比较。",
        }
    if outcome == "external_evidence":
        return {
            "type": "review_external",
            "count": len(evidence),
            "text": "复核外部候选；候选不等于指南适用性结论。",
        }
    if outcome == "evidence_gap":
        return {
            "type": "report_gap",
            "text": "本地/外部均无候选；明确报告证据缺口，不写成阴性。",
        }
    if outcome == "scope_mismatch":
        return {
            "type": "scope_mismatch",
            "text": "结构化条件与记录矛盾；不是临床不适用，需人工核对。",
        }
    return {
        "type": "review_selected",
        "selected": list(selected),
        "text": "按选中指南的条件复核；跨族覆盖只作另一个问题。",
    }


def _stop_reason(outcome: str) -> str:
    return {
        "resolved": "target_or_same_family_coverage",
        "needs_information": "missing_fact_can_change_target",
        "needs_table_lookup": "table_or_curve_required",
        "external_evidence": "external_candidates_found",
        "evidence_gap": "no_candidate_or_backend",
        "scope_mismatch": "structured_scope_contradiction",
    }.get(outcome, outcome)


def _triage(
    snapshot: Mapping[str, Any], target_id: str | None, target_topic: str | None,
    target: Any,
) -> dict[str, Any]:
    try:
        triage = triage_snapshot(
            snapshot, target_guideline_id=target_id, target_topic=target_topic,
        )
        if target is not None:
            # The triage fixture is stricter than the assessment (it may lack
            # ga_days_range/ga_precision).  Keep only fields the assessment
            # itself still considers unknown and unobserved.
            unknown = {
                criterion.field for criterion in target.criteria
                if criterion.status == "unknown" and not criterion.observed_value
            }
            triage["target_priorities"] = [
                item for item in triage["target_priorities"] if item["field"] in unknown
            ]
        return triage
    except ValueError:
        # The triage fixture is the four-guideline v1 set; an AAP target can be
        # absent.  Fall back to the assessment's own unobserved criteria.
        priorities = []
        if target is not None:
            for criterion in target.criteria:
                if criterion.status != "unknown" or criterion.observed_value:
                    continue
                priorities.append({
                    "field": criterion.field,
                    "label": criterion.label or _FIELD_LABELS.get(criterion.field, criterion.field),
                    "source": "病历/检验记录",
                    "why": "补该字段后才能判断目标指南；当前没有可用记录。",
                })
        return {"target_priorities": priorities}


def _scope_reason(cross_family: Sequence[str], *, searched: bool) -> str:
    reason = (
        "structured scope criteria contradict the visible record; "
        "not clinical inapplicability"
    )
    if cross_family:
        reason += (
            "; other local guidelines cover the patient but address a different "
            "question, so they were not substituted"
        )
    if searched:
        reason += "; alternative candidates retrieved but not adjudicated"
    else:
        reason += "; external search backend not provided"
    return reason


def _choose_target(assessments: Mapping[str, Any]) -> str | None:
    for document_id, assessment in assessments.items():
        if assessment.status != "possible_match":
            return document_id
    return next(iter(assessments), None)


def _table_lookup_ready(assessment: Any) -> bool:
    """True only when every non-table criterion matches and the table value exists."""
    table = [
        criterion for criterion in assessment.criteria
        if criterion.operator == "table_lookup"
    ]
    if not table or not all(criterion.observed_value for criterion in table):
        return False
    non_table = [
        criterion for criterion in assessment.criteria
        if criterion.operator != "table_lookup" and criterion.required
    ]
    return bool(non_table) and all(criterion.status == "match" for criterion in non_table)


def _failed_criteria(assessment: Any) -> list[dict[str, Any]]:
    return [
        {
            "criterion_id": criterion.criterion_id,
            "field": criterion.field,
            "observed_value": criterion.observed_value,
            "reason": criterion.reason,
            "locator": criterion.source_locator,
        }
        for criterion in assessment.criteria
        if criterion.status == "mismatch"
    ]


def _external_query(terms: Sequence[str], target_topic: str | None) -> str:
    if target_topic:
        topic = str(target_topic).replace("_", " ").strip()
        text = f'"{topic}"[tiab]' if topic else "neonatal[tiab]"
    else:
        text = " OR ".join(f'"{term}"[tiab]' for term in terms) or "neonatal[tiab]"
    return (
        '("Infant, Newborn"[MeSH] OR neonatal[tiab] OR preterm[tiab]) AND '
        f"({text}) AND (practice guideline[pt] OR guideline[pt])"
    )


def _screen(
    raw: Sequence[Mapping[str, Any] | str], terms: Sequence[str], target_topic: str | None,
) -> list[dict[str, Any]]:
    target_terms = terms
    if target_topic:
        target_terms = (str(target_topic).replace("_", " ").lower(),)
    scored = []
    for item in raw:
        if isinstance(item, str):
            payload = {"title": item, "abstract": ""}
        else:
            payload = dict(item)
        text = " ".join(str(payload.get(key) or "") for key in ("title", "abstract", "summary")).lower()
        target_hits = [term for term in target_terms if term.lower() in text]
        population_hits = [term for term in _POPULATION_TERMS if term in text]
        if not target_hits:
            continue
        scored.append({
            "title": payload.get("title") or "",
            "pmid": payload.get("pmid"),
            "doi": payload.get("doi"),
            "source": payload.get("source") or "external_candidate",
            "matched_target_terms": target_hits,
            "population_signal": population_hits,
            "score": len(target_hits) + 0.5 * len(population_hits),
            "note": "external candidate; not adjudicated as guideline applicability",
        })
    scored.sort(key=lambda row: (-row["score"], str(row.get("pmid") or "")))
    return scored
