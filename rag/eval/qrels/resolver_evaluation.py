"""Offline evaluation of the executable applicability resolver on frozen cases.

The resolver receives the question, patient snapshot and target/topic identity,
never the gold status, missing fields or coverage.  This is a local rule-based
workflow test, not a complete online RAG evaluation or a clinical result.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from .applicability_resolver import ApplicabilityResolver
from .families import family_of


def evaluate_resolver(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    resolver = ApplicabilityResolver(search_fn=None)
    by_type: dict[str, Counter[str]] = {}
    outcomes: dict[str, Counter[str]] = {}
    target_status = Counter()
    for case in cases:
        gold = case["gold"]
        result = resolver.resolve(
            question=case["question"],
            snapshot=case["snapshot"],
            target_guideline_id=gold.get("guideline_id"),
            target_topic=gold.get("topic"),
        )
        case_type = case["case_type"]
        by_type.setdefault(case_type, Counter())
        outcomes.setdefault(case_type, Counter())
        outcomes[case_type][result["outcome"]] += 1
        if gold.get("guideline_status") is not None:
            target_status["n"] += 1
            target_status["correct"] += result["target"]["status"] == gold["guideline_status"]
        correct = _expected_behavior(case, result)
        by_type[case_type]["n"] += 1
        by_type[case_type]["correct"] += correct

    per_type = {
        case_type: {
            "n": counts["n"],
            "correct": counts["correct"],
            "rate": counts["correct"] / counts["n"],
            "outcomes": dict(sorted(outcomes[case_type].items())),
        }
        for case_type, counts in by_type.items()
    }
    local_n = sum(per_type.get(kind, {}).get("n", 0) for kind in ("A", "B", "C", "D"))
    local_correct = sum(per_type.get(kind, {}).get("correct", 0) for kind in ("A", "B", "C", "D"))
    return {
        "case_count": len(cases),
        "local_abcd": {
            "n": local_n,
            "correct": local_correct,
            "rate": local_correct / local_n if local_n else None,
        },
        "target_status": {
            "n": target_status["n"],
            "correct": target_status["correct"],
            "accuracy": target_status["correct"] / target_status["n"] if target_status["n"] else None,
        },
        "per_case_type": per_type,
        "ga": {
            "n": per_type.get("GA", {}).get("n", 0),
            "handled": 0,
            "reason": "current resolver has no free-text GA extraction action",
        },
    }


def _expected_behavior(case: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    gold = case["gold"]
    case_type = case["case_type"]
    if case_type == "A":
        same_family = {
            item for item in gold.get("coverage", [])
            if family_of(item) == family_of(gold.get("guideline_id"))
        }
        return result["outcome"] == "resolved" and set(result["selected_guidelines"]) == same_family
    if case_type == "B":
        return (
            result["outcome"] == "scope_mismatch"
            and not any(
                row["status"] == "possible_match" for row in result["assessments"].values()
            )
            and not result["selected_guidelines"]
        )
    if case_type == "C":
        return result["outcome"] == "needs_information" and {
            row["field"] for row in result["information_requests"]
        } == set(gold["missing_fields"])
    if case_type == "D":
        return (
            result["outcome"] == "evidence_gap"
            and result["target"]["guideline_id"] is None
            and not result["selected_guidelines"]
            and not result["external_search"]["executed"]
        )
    return False
