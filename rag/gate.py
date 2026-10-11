"""Fidelity gate: graded disposition of query-side routes before fusion (research, v0).

The gate consumes a :class:`~rag.fidelity.FidelityReport` and derives, per
conversion route, a :class:`GateDecision` under one of five policies:

===========  ==================================================================
policy       behaviour
===========  ==================================================================
``off``      no audit, no gate (frozen baseline semantics);
``audit``    audit runs and is traced; no signal changes, no warnings;
``warn``     audit + explicit warnings; no signal changes;
``downweight`` L1→warn, L2→down-weight the route's signal, L3→reject it;
``reject``   L1→warn, L2/L3→reject the route's signal entirely.
===========  ==================================================================

Policies ``downweight``/``reject`` generalise the frozen baseline's "all-zero
routes skip RRF" rule to "unqualified routes skip (or are discounted in) fusion".

Scoping and traceability (the auditability contract of this package):

- Policy is strictly **route-scoped**: only the route whose audit produced the
  findings is affected; the plan, its terms and every other route stay intact.
- Down-weighting multiplies signals by ``penalty`` **before** fusion; the
  original values remain visible in ``trace.query_fidelity``.
- Rejection removes a route's signal from fusion (neural score map emptied;
  lexical query variants dropped). It never silently edits the query plan
  terms themselves — the plan remains the audit record of what was asked.
- Semantics vs. the PRESS-style self-check: ``query.review_query_plan`` is a
  reporting checklist for humans; this gate is an acting mechanism for the
  system. They coexist and stay separate so the checklist never silently
  changes behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .fidelity import FidelityReport

GATE_POLICIES = ("off", "audit", "warn", "downweight", "reject")

ACTION_RECORD = "record"
ACTION_PASS = "pass"
ACTION_WARN = "warn"
ACTION_DOWNWEIGHT = "downweight"
ACTION_REJECT = "reject"

# Route -> method tokens whose *neural* scores belong to that route.
NEURAL_ROUTE_OF_METHOD = {"xlingual": "multilingual_encoding", "m3": "multilingual_encoding"}

# Route -> whether a down-weight/reject action has an installable signal.
# The reviewed mapping is the quality-upper-bound branch: dropping or damping
# the caller's audited terminology would never be a defensible v0 policy, so
# the gate demotes down-weight/reject on it to warn (recorded in the reason).
# The translate and multilingual routes are additional recall branches whose
# signals can be discounted or skipped; those actions are installable.
_SIGNAL_INSTALLABLE = {
    "reviewed_mapping": False,
    "translate": True,
    "multilingual_encoding": True,
}


@dataclass(frozen=True)
class GateDecision:
    route: str
    level: str
    action: str  # pass / record / warn / downweight / reject
    penalty: float  # 1.0 unless down-weighting
    reason: str

    @property
    def changes_signal(self) -> bool:
        return self.action in (ACTION_DOWNWEIGHT, ACTION_REJECT)

    def to_dict(self) -> dict:
        return {
            "route": self.route, "level": self.level, "action": self.action,
            "penalty": self.penalty, "changes_signal": self.changes_signal,
            "reason": self.reason,
        }


def _action_for_level(policy: str, level: str) -> str:
    if policy == "audit":
        return ACTION_RECORD if level != "L0" else ACTION_PASS
    if policy == "warn":
        return ACTION_WARN if level != "L0" else ACTION_PASS
    if policy == "downweight":
        return {"L0": ACTION_PASS, "L1": ACTION_WARN, "L2": ACTION_DOWNWEIGHT, "L3": ACTION_REJECT}[level]
    if policy == "reject":
        return {"L0": ACTION_PASS, "L1": ACTION_WARN, "L2": ACTION_REJECT, "L3": ACTION_REJECT}[level]
    return ACTION_PASS


@dataclass(frozen=True)
class FidelityGate:
    """Map audit levels to gate actions under a named policy.

    ``penalty`` is the multiplicative factor for down-weighted signals
    (v0 default 0.5). The value is an experiment object, not a clinical
    constant: see the paper's experiment protocol (threat #5).
    """

    policy: str = "downweight"
    penalty: float = 0.5

    def __post_init__(self) -> None:  # pragma: no cover - validation only
        if self.policy not in GATE_POLICIES:
            raise ValueError("unknown fidelity gate policy: " + str(self.policy))
        if not 0.0 <= float(self.penalty) <= 1.0:
            raise ValueError("fidelity penalty must be within [0, 1]")

    def decide(self, report: FidelityReport) -> tuple[GateDecision, ...]:
        decisions: list[GateDecision] = []
        for audit in report.routes:
            action = _action_for_level(self.policy, audit.level)
            penalty = float(self.penalty) if action == ACTION_DOWNWEIGHT else 1.0
            demoted_note = ""
            if action in (ACTION_DOWNWEIGHT, ACTION_REJECT) and not _SIGNAL_INSTALLABLE.get(audit.route, False):
                demoted_note = "（该路是质量上界分支，v0 门控只做警示、不改变其信号；降权/拒绝对它不可安装。）"
                action = ACTION_WARN
                penalty = 1.0
            if action == ACTION_PASS:
                reason = f"级别 {audit.level}：无需处置。"
            elif audit.notes:
                reason = f"{audit.notes[0]}（级别 {audit.level}）{demoted_note}"
            else:
                reason = {
                    ACTION_RECORD: f"级别 {audit.level}：仅记录，不改变该路信号（audit 策略）。",
                    ACTION_WARN: f"级别 {audit.level}：警示并保留该路信号（warn 策略）。{demoted_note}",
                    ACTION_DOWNWEIGHT: f"级别 {audit.level}：该路信号降权 {penalty} 后参与融合；原值保留在 trace。",
                    ACTION_REJECT: f"级别 {audit.level}：该路查询保真不合格，退出检索融合（不合格路线跳过，推广自零信号路线规则）。",
                }[action]
            decisions.append(GateDecision(route=audit.route, level=audit.level, action=action, penalty=penalty, reason=reason))
        return tuple(decisions)

    # -- signal application helpers ------------------------------------------

    @staticmethod
    def apply_to_neural_scores(
        neural_scores: Mapping[str, Mapping[str, float]],
        decisions: Sequence[GateDecision],
    ) -> tuple[dict[str, dict[str, float]], list[dict]]:
        """Apply decisions to ``{method: {doc_key: score}}`` before ranking.

        Returns ``(adjusted_scores, adjustments)``. ``adjusted_scores`` mirrors
        the input shape; rejected methods get an empty map (rank_documents then
        drops the signal, matching the zero-signal-route rule), down-weighted
        methods get ``score * penalty``. ``adjustments`` is the audit trail.
        """
        adjusted: dict[str, dict[str, float]] = {
            method: dict(scores) for method, scores in neural_scores.items()
        }
        adjustments: list[dict] = []
        by_route = {decision.route: decision for decision in decisions}
        for method, scores in neural_scores.items():
            route = NEURAL_ROUTE_OF_METHOD.get(method)
            decision = by_route.get(route) if route else None
            if decision is None or decision.action not in (ACTION_DOWNWEIGHT, ACTION_REJECT):
                continue
            if decision.action == ACTION_REJECT:
                adjusted[method] = {}
                adjustments.append({
                    "method": method, "route": route, "action": ACTION_REJECT,
                    "level": decision.level, "docs_affected": len(scores),
                    "reason": decision.reason,
                })
            else:
                adjusted[method] = {key: value * decision.penalty for key, value in scores.items()}
                adjustments.append({
                    "method": method, "route": route, "action": ACTION_DOWNWEIGHT,
                    "level": decision.level, "penalty": decision.penalty,
                    "docs_affected": len(scores), "reason": decision.reason,
                })
        return adjusted, adjustments

    @staticmethod
    def variant_weight(
        decisions: Sequence[GateDecision],
        route: str,
    ) -> float | None:
        """Weight to apply to ``route``-derived lexical query variants.

        Returns ``None`` when the route's variants must be dropped entirely
        (reject), ``penalty`` for down-weight, and ``1.0`` when unchanged.
        """
        decision = next((item for item in decisions if item.route == route), None)
        if decision is None:
            return 1.0
        if decision.action == ACTION_REJECT:
            return None
        if decision.action == ACTION_DOWNWEIGHT:
            return decision.penalty
        return 1.0
