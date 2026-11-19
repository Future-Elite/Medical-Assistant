"""Four-state condition evaluation over patient facts.

The condition language is a deliberately small, data-only AST.  It never
executes Python or SQL from a knowledge source.  Every leaf records the fact
references behind its result so the caller can audit why a dimension is
``met``, ``not_met``, ``unknown`` or ``conflict``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

STATUSES = ("met", "not_met", "unknown", "conflict")


@dataclass(frozen=True)
class FactValue:
    value: Any
    status: str = "observed_positive"
    path: str = ""
    time: str | None = None
    unit: str | None = None
    multi: bool = False
    qualifier: str = ""
    scope: str = "current"


@dataclass(frozen=True)
class FactSnapshot:
    status: str
    value: Any = None
    path: str = ""
    time: str | None = None
    unit: str | None = None
    values: tuple[Any, ...] = ()
    qualifier: str = ""
    scope: str = "current"


@dataclass(frozen=True)
class ConditionEvaluation:
    status: str
    fact_refs: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "fact_refs": list(self.fact_refs), "detail": self.detail}


class FactStore:
    def __init__(self, *, as_of: str | None = None) -> None:
        self._facts: dict[str, list[FactValue]] = {}
        self.as_of = as_of

    def add(
        self,
        name: str,
        value: Any,
        *,
        status: str = "observed_positive",
        path: str = "",
        time: str | None = None,
        unit: str | None = None,
        multi: bool = False,
        qualifier: str = "",
        scope: str = "current",
    ) -> None:
        normalized = str(status or "observed_positive")
        if normalized not in {"observed_positive", "observed_negative", "uncertain", "not_recorded", "pending"}:
            normalized = "observed_positive"
        self._facts.setdefault(str(name), []).append(FactValue(
            value=value, status=normalized, path=str(path), time=_time_text(time),
            unit=unit, multi=multi, qualifier=qualifier, scope=str(scope or "current"),
        ))

    def has(self, name: str) -> bool:
        return bool(self._facts.get(str(name)))

    def rows(self, name: str) -> tuple[FactValue, ...]:
        return tuple(self._facts.get(str(name), ()))

    def latest(self, name: str, *, scope: str | None = None) -> FactSnapshot:
        rows = list(self._facts.get(str(name), ()))
        if scope is not None:
            rows = [row for row in rows if row.scope == scope]
        if not rows:
            return FactSnapshot("unknown")
        positive = [row for row in rows if row.status in {"observed_positive", "uncertain", "pending"}]
        negative = [row for row in rows if row.status == "observed_negative"]
        if not positive:
            if negative:
                return FactSnapshot("not_met", value=False, path=negative[-1].path, time=negative[-1].time)
            return FactSnapshot("unknown")
        latest_time = max((row.time or "" for row in positive), default="")
        latest = [row for row in positive if (row.time or "") == latest_time]
        values = {_comparable(row.value) for row in latest}
        if len(values) > 1:
            return FactSnapshot(
                "conflict",
                value=None,
                path=latest[-1].path,
                time=latest[-1].time,
                values=tuple(row.value for row in latest),
            )
        row = latest[-1]
        status = "met" if row.status == "observed_positive" else "unknown"
        return FactSnapshot(status, value=row.value, path=row.path, time=row.time, unit=row.unit,
                            values=(row.value,), qualifier=row.qualifier, scope=row.scope)

    def list_values(self, name: str) -> tuple[Any, ...]:
        rows = self._facts.get(str(name), ())
        values = [row.value for row in rows if row.status in {"observed_positive", "uncertain"}]
        if not values and any(row.status == "observed_negative" for row in rows):
            return ()
        return tuple(values)

    def fact_refs(self, name: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(row.path for row in self._facts.get(str(name), ()) if row.path))

    def compact(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in sorted(self._facts):
            snap = self.latest(name)
            values = self.list_values(name)
            result[name] = {
                "status": snap.status,
                "value": snap.value,
                "values": list(values) if len(values) > 1 else [],
                "path": snap.path,
                "time": snap.time,
                "unit": snap.unit,
                "qualifier": snap.qualifier,
                "scope": snap.scope,
            }
            if snap.status == "conflict":
                result[name]["conflicting_values"] = list(snap.values)
        return result


def evaluate(expr: Mapping[str, Any], facts: FactStore) -> ConditionEvaluation:
    if not isinstance(expr, Mapping):
        return ConditionEvaluation("unknown", detail="condition is not an object")
    if "all" in expr:
        return _combine([evaluate(item, facts) for item in expr["all"]], mode="all")
    if "any" in expr:
        return _combine([evaluate(item, facts) for item in expr["any"]], mode="any")
    if "not" in expr:
        child = evaluate(expr["not"], facts)
        inverse = {"met": "not_met", "not_met": "met", "unknown": "unknown", "conflict": "conflict"}
        return ConditionEvaluation(inverse[child.status], child.fact_refs, f"not({child.detail})")
    if "exists" in expr:
        return _exists(expr["exists"], facts)
    for operator in ("eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "between"):
        if operator in expr:
            return _leaf(operator, expr[operator], facts)
    return ConditionEvaluation("unknown", detail="unsupported condition operator")


def evaluate_condition_ids(
    condition_ids: Iterable[str],
    conditions: Mapping[str, Mapping[str, Any]],
    facts: FactStore,
    *,
    mode: str = "all",
) -> ConditionEvaluation:
    evaluations: list[ConditionEvaluation] = []
    for condition_id in condition_ids:
        condition = conditions.get(str(condition_id))
        if condition is None:
            evaluations.append(ConditionEvaluation("unknown", detail=f"missing condition {condition_id}"))
            continue
        child = evaluate(condition.get("expr") or {}, facts)
        evaluations.append(ConditionEvaluation(child.status, child.fact_refs, f"{condition_id}: {child.detail}"))
    return _combine(evaluations, mode=mode)


def _combine(evaluations: Sequence[ConditionEvaluation], *, mode: str) -> ConditionEvaluation:
    refs = tuple(dict.fromkeys(ref for item in evaluations for ref in item.fact_refs))
    statuses = [item.status for item in evaluations]
    detail = "; ".join(item.detail for item in evaluations if item.detail)
    if mode == "all":
        if "not_met" in statuses:
            return ConditionEvaluation("not_met", refs, detail)
        if "conflict" in statuses:
            return ConditionEvaluation("conflict", refs, detail)
        if "unknown" in statuses:
            return ConditionEvaluation("unknown", refs, detail)
        return ConditionEvaluation("met", refs, detail)
    if "met" in statuses:
        return ConditionEvaluation("met", refs, detail)
    if "conflict" in statuses:
        return ConditionEvaluation("conflict", refs, detail)
    if not statuses or all(status == "not_met" for status in statuses):
        return ConditionEvaluation("not_met", refs, detail)
    return ConditionEvaluation("unknown", refs, detail)


def _exists(spec: Any, facts: FactStore) -> ConditionEvaluation:
    if not isinstance(spec, Mapping):
        return ConditionEvaluation("unknown", detail="exists operand is malformed")
    name = str(spec.get("fact") or "")
    if not name:
        return ConditionEvaluation("unknown", detail="exists fact is empty")
    values = facts.list_values(name)
    if values:
        return ConditionEvaluation("met", facts.fact_refs(name), f"{name} present")
    rows = facts.rows(name)
    if rows and all(row.status == "observed_negative" for row in rows):
        return ConditionEvaluation("not_met", facts.fact_refs(name), f"{name} explicitly negative")
    return ConditionEvaluation("unknown", facts.fact_refs(name), f"{name} not recorded")


def _leaf(operator: str, operands: Any, facts: FactStore) -> ConditionEvaluation:
    if not isinstance(operands, (list, tuple)) or not operands:
        return ConditionEvaluation("unknown", detail=f"{operator} operands malformed")
    left = operands[0]
    if not isinstance(left, Mapping):
        return ConditionEvaluation("unknown", detail=f"{operator} fact missing")
    name = str(left.get("fact") or "")
    if not name:
        return ConditionEvaluation("unknown", detail=f"{operator} fact missing")
    if operator == "contains":
        expected = str(operands[1]) if len(operands) > 1 else ""
        values = facts.list_values(name)
        if not values:
            snap = facts.latest(name)
            if snap.status == "not_met":
                return ConditionEvaluation("not_met", facts.fact_refs(name), f"{name} empty")
            return ConditionEvaluation("unknown", facts.fact_refs(name), f"{name} missing")
        wanted = _normalize_text(expected)
        found = any(wanted in _normalize_text(value) for value in values)
        return ConditionEvaluation("met" if found else "not_met", facts.fact_refs(name), f"{name} contains {expected}")
    snapshot = facts.latest(name, scope=str(left.get("scope")) if left.get("scope") else None)
    if snapshot.status == "conflict":
        return ConditionEvaluation("conflict", facts.fact_refs(name), f"{name} conflicting values")
    if snapshot.status != "met":
        return ConditionEvaluation("unknown", facts.fact_refs(name), f"{name} {snapshot.status}")
    value = snapshot.value
    expected = operands[1] if len(operands) > 1 else None
    if operator == "between":
        if not isinstance(operands[1], (list, tuple)) or len(operands[1]) != 2:
            return ConditionEvaluation("unknown", facts.fact_refs(name), "between bounds malformed")
        low, high = operands[1]
        ok = _number(value) is not None and _number(low) is not None and _number(high) is not None
        if not ok:
            return ConditionEvaluation("unknown", facts.fact_refs(name), f"{name} not numeric")
        return ConditionEvaluation(
            "met" if float(low) <= float(value) <= float(high) else "not_met",
            facts.fact_refs(name), f"{name}={value} between {low}..{high}",
        )
    if operator == "in":
        choices = expected if isinstance(expected, (list, tuple, set)) else (expected,)
        ok = any(_equal(value, item) for item in choices)
        return ConditionEvaluation("met" if ok else "not_met", facts.fact_refs(name), f"{name}={value} in {choices}")
    # Optional freshness and unit checks are data-driven; no hard-coded clinical TTL.
    if left.get("max_age_hours") is not None and facts.as_of and snapshot.time:
        age = _age_hours(snapshot.time, facts.as_of)
        if age is not None and age > float(left["max_age_hours"]):
            return ConditionEvaluation(
                "unknown", facts.fact_refs(name),
                f"{name} 记录时间 {snapshot.time} 距 {facts.as_of} 已 {age:.1f}h，超过条件时限",
            )
    wanted_unit = str(left.get("unit") or "").strip()
    if wanted_unit and snapshot.unit and not units_compatible(snapshot.unit, wanted_unit):
        return ConditionEvaluation(
            "unknown", facts.fact_refs(name),
            f"{name} 单位 {snapshot.unit} 与条件单位 {wanted_unit} 不可比较",
        )
    if operator in {"lt", "lte", "gt", "gte"}:
        left_number, right_number = _number(value), _number(expected)
        if left_number is None or right_number is None:
            return ConditionEvaluation("unknown", facts.fact_refs(name), f"{name} not numeric")
        if wanted_unit and snapshot.unit:
            converted = convert_unit(right_number, wanted_unit, snapshot.unit)
            if converted is None:
                return ConditionEvaluation("unknown", facts.fact_refs(name), f"{name} 单位换算失败")
            right_number = converted
        # GA weeks-only is an interval [weeks, weeks+1), not a precise day.
        if name == "ga_weeks":
            precision = facts.latest("ga_precision")
            if precision.status == "met" and str(precision.value) == "weeks_only":
                status, detail = _interval_compare(operator, left_number, left_number + 1.0,
                                                   False, False, right_number)
                return ConditionEvaluation(status, facts.fact_refs(name), f"{name}~[{left_number},{left_number+1}) {operator} {right_number}: {detail}")
        if snapshot.qualifier:
            low, high, low_closed, high_closed = _qualifier_interval(left_number, snapshot.qualifier)
            status, detail = _interval_compare(operator, low, high, low_closed, high_closed, right_number)
            return ConditionEvaluation(status, facts.fact_refs(name), f"{name}{snapshot.qualifier}{left_number} {operator} {right_number}: {detail}")
        comparisons = {
            "lt": left_number < right_number,
            "lte": left_number <= right_number,
            "gt": left_number > right_number,
            "gte": left_number >= right_number,
        }
        return ConditionEvaluation(
            "met" if comparisons[operator] else "not_met",
            facts.fact_refs(name), f"{name}={value} {operator} {expected}",
        )
    if operator in {"eq", "ne"}:
        if snapshot.qualifier:
            low, high, low_closed, high_closed = _qualifier_interval(_number(value) or 0.0, snapshot.qualifier)
            target = _number(expected)
            if target is None:
                return ConditionEvaluation("unknown", facts.fact_refs(name), f"{name} qualifier vs non-numeric")
            eq_status, _ = _interval_compare("eq", low, high, low_closed, high_closed, target)
            if operator == "ne":
                eq_status = {"met": "not_met", "not_met": "met", "unknown": "unknown"}[eq_status]
            return ConditionEvaluation(eq_status, facts.fact_refs(name), f"{name}{snapshot.qualifier}{value} {operator} {expected}")
        ok = _equal(value, expected)
        if operator == "ne":
            ok = not ok
        return ConditionEvaluation("met" if ok else "not_met", facts.fact_refs(name), f"{name}={value} {operator} {expected}")
    return ConditionEvaluation("unknown", detail=f"unsupported operator {operator}")


_UNIT_FACTORS = {
    ("mg/dl", "umol/l"): 17.1,
    ("umol/l", "mg/dl"): 1.0 / 17.1,
    ("mg/dl", "mmol/l"): 0.0555,
    ("mmol/l", "mg/dl"): 1.0 / 0.0555,
    ("mg/dl", "umol/l_creatinine"): 88.4,
}


def units_compatible(left: str, right: str) -> bool:
    a, b = str(left or "").strip().casefold(), str(right or "").strip().casefold()
    if not a or not b or a == b:
        return True
    return (a, b) in _UNIT_FACTORS or (b, a) in _UNIT_FACTORS


def convert_unit(value: float, from_unit: str, to_unit: str) -> float | None:
    a, b = str(from_unit or "").strip().casefold(), str(to_unit or "").strip().casefold()
    if not a or not b or a == b:
        return float(value)
    factor = _UNIT_FACTORS.get((a, b))
    if factor is not None:
        return float(value) * factor
    reverse = _UNIT_FACTORS.get((b, a))
    if reverse is not None:
        return float(value) / reverse
    return None


def _age_hours(recorded: str, as_of: str) -> float | None:
    try:
        left = datetime.fromisoformat(str(recorded).replace("Z", "+00:00"))
        right = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        return (right - left).total_seconds() / 3600.0
    except ValueError:
        return None


def _qualifier_interval(value: float, qualifier: str) -> tuple[float, float, bool, bool]:
    """Return (low, high, low_closed, high_closed) for a bounded observation."""
    if qualifier == "<":
        return float("-inf"), value, False, False
    if qualifier == "<=":
        return float("-inf"), value, False, True
    if qualifier == ">":
        return value, float("inf"), False, False
    if qualifier == ">=":
        return value, float("inf"), True, False
    return value, value, True, True


def _interval_compare(operator: str, low: float, high: float, low_closed: bool, high_closed: bool, target: float) -> tuple[str, str]:
    """Compare an interval with a target, returning met/not_met/unknown."""
    if operator == "lt":
        if high <= target and not (high == target and high_closed):
            return "met", "all values below"
        if low >= target:
            return "not_met", "all values at/above"
        return "unknown", "interval crosses target"
    if operator == "lte":
        if high <= target:
            return "met", "all values below/equal"
        if low > target or (low == target and not low_closed):
            return "not_met", "all values above"
        return "unknown", "interval crosses target"
    if operator == "gt":
        if low > target or (low == target and not low_closed):
            return "met", "all values above"
        if high < target or (high == target and not high_closed):
            return "not_met", "all values at/below"
        return "unknown", "interval crosses target"
    if operator == "gte":
        if low >= target:
            return "met", "all values above/equal"
        if high < target or (high == target and not high_closed):
            return "not_met", "all values below"
        return "unknown", "interval crosses target"
    if operator == "eq":
        if high == low and low_closed and high_closed and low == target:
            return "met", "single point"
        if target < low or target > high or (target == low and not low_closed) or (target == high and not high_closed):
            return "not_met", "target outside interval"
        return "unknown", "interval contains target"
    return "unknown", "unsupported interval comparison"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    left_number, right_number = _number(left), _number(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return _normalize_text(left) == _normalize_text(right)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().casefold()


def _comparable(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.8f}"
    return str(value)


def _time_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text
