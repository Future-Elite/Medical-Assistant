"""Rule-based source routing v0 (research feature; default ``off``).

Deterministic source-selection policy for the retrieval pipeline. This is the
"M0" step of the adaptive source-routing line: rules only, no learning, no
extra network calls. Modes:

- ``off``    legacy behaviour — every selected connector runs (frozen baseline);
- ``shadow`` compute and record the decision in ``trace.route_decision`` but
  keep the legacy source order (experimental run without behaviour change);
- ``active`` apply the decision by narrowing/reordering ``source_kinds`` before
  query planning.

Rules v0 (evaluated in order, first match wins):

1. ``no_english_drop_pubmed`` — no English coverage at all: skip the PubMed
   branch (it would be zero-hit) and note the degradation; if PubMed is the only
   registered source, fall back to all sources instead of returning nothing.
2. ``guideline_recommendation_first`` — recommendation/guideline intent:
   guideline first, PubMed as supporting source.
3. ``evidence_update_pubmed_first`` / ``drug_safety_pubmed_first`` — PubMed
   first, guideline as supporting source.
4. ``default_all_sources`` — no specific marker: all registered sources in
   their legacy order (explicit fallback, recorded as such).

Patient-side facts are always local and never routed here: routing only decides
which *external* source kinds run. This module does not decide whether retrieval
is needed at all (that remains the Agent's call).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

ROUTING_MODES = ("off", "shadow", "active")


@dataclass(frozen=True)
class RouteDecision:
    rule: str
    selected_sources: tuple[str, ...] = ()
    rationale: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    fallback_all: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "selected_sources": list(self.selected_sources),
            "rationale": list(self.rationale),
            "notes": list(self.notes),
            "fallback_all": self.fallback_all,
        }


def decide_route(
    *,
    clinical_intent: str,
    english_coverage: bool,
    known_kinds: Sequence[str],
    requested_kinds: Sequence[str] = (),
) -> RouteDecision:
    """Deterministic routing v0. Pure function — no I/O, fully unit-testable."""
    kinds: list[str] = []
    for kind in (requested_kinds or known_kinds):
        if kind in known_kinds and kind not in kinds:
            kinds.append(kind)
    if not kinds:
        return RouteDecision(
            rule="no_registered_source",
            selected_sources=(),
            rationale=("没有已注册的来源；无法路由。",),
            fallback_all=True,
        )

    selected = list(kinds)
    notes: list[str] = []
    restored_all = False
    if "pubmed" in selected and not english_coverage:
        selected.remove("pubmed")
        notes.append("无英文覆盖：PubMed 分支跳过（否则零命中空转）。")
        if not selected:
            selected = list(kinds)
            restored_all = True
            notes.append("PubMed 是唯一来源：回退全来源，避免空检索。")
    rule_prefix = "no_english_drop_pubmed+" if notes and not restored_all else ""

    if clinical_intent == "guideline_recommendation":
        selected = _ordered(["guideline"], selected)
        rule = rule_prefix + "guideline_recommendation_first"
        rationale = ("问题含推荐/指南线索：指南来源优先，PubMed 作为补充。",)
    elif clinical_intent == "evidence_update":
        selected = _ordered(["pubmed"], selected)
        rule = rule_prefix + "evidence_update_pubmed_first"
        rationale = ("问题含最新/更新线索：PubMed 优先，指南用于版本对照。",)
    elif clinical_intent == "drug_safety":
        selected = _ordered(["pubmed"], selected)
        rule = rule_prefix + "drug_safety_pubmed_first"
        rationale = ("药物安全类问题：PubMed 优先，指南作为补充。",)
    else:
        rule = rule_prefix + "default_all_sources"
        rationale = ("无特定来源线索：全部可用来源按 legacy 顺序参与。",)

    return RouteDecision(
        rule=rule,
        selected_sources=tuple(selected),
        rationale=rationale,
        notes=tuple(notes),
        fallback_all=(rule == "default_all_sources") or restored_all,
    )


def _ordered(priority: Iterable[str], kinds: Sequence[str]) -> list[str]:
    ordered = [kind for kind in priority if kind in kinds]
    ordered.extend(kind for kind in kinds if kind not in ordered)
    return ordered
