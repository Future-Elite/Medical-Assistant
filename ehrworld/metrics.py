"""Paper Appendix C metrics with explicit missing-value and aggregation policies."""
from __future__ import annotations

import math
import re
from collections import Counter
from statistics import mean
from typing import Iterable

from .schema import Episode, Event, Rollout

EPS = 1e-10
MISSING = "__missing__"


def relative_error(truth: float, prediction: float) -> float:
    return abs(truth - prediction) / max(abs(truth), EPS)


def smape(truth: float, prediction: float) -> float:
    return 2 * abs(truth - prediction) / (abs(truth) + abs(prediction) + EPS)


def numeric_metrics(pairs: list[tuple[float, float | None]]) -> dict:
    if not pairs:
        return {"count": 0, "predicted_count": 0, "coverage": None,
                "S@10": None, "S@15": None, "S@25": None, "SMAPE": None, "SMAPE_percent": None}
    errors = [relative_error(y, p) if p is not None else math.inf for y, p in pairs]
    err = mean(smape(y, p) if p is not None else 2.0 for y, p in pairs)
    return {"count": len(pairs), "predicted_count": sum(p is not None for _, p in pairs),
            "coverage": sum(p is not None for _, p in pairs) / len(pairs),
            **{f"S@{k}": sum(e <= k / 100 + 1e-12 for e in errors) / len(errors) for k in (10, 15, 25)},
            "SMAPE": err, "SMAPE_percent": err * 100}


def normalize_label(value: str, aliases: dict[str, str] | None = None) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().casefold())
    return (aliases or {}).get(normalized, normalized)


def classification_metrics(truth: list[str], predicted: list[str]) -> dict:
    if not truth:
        return {"count": 0, "coverage": None, "precision": None, "recall": None, "f1": None,
                "micro_f1": None, "weighted_f1": None, "accuracy": None, "per_class": {}}
    labels = sorted((set(truth) | set(predicted)) - {MISSING})
    per_class = {}
    totals = Counter()
    true_counts = Counter(truth)
    pred_counts = Counter(predicted)
    correct = Counter(y for y, p in zip(truth, predicted) if y == p)
    for label in labels:
        tp = correct[label]
        fp = pred_counts[label] - tp
        fn = true_counts[label] - tp
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
        per_class[label] = dict(precision=precision, recall=recall, f1=f1, support=tp + fn)
        totals.update(tp=tp, fp=fp, fn=fn)
    micro_p = totals["tp"] / (totals["tp"] + totals["fp"]) if totals["tp"] + totals["fp"] else 0.0
    micro_r = totals["tp"] / (totals["tp"] + totals["fn"]) if totals["tp"] + totals["fn"] else 0.0
    return {"count": len(truth), "coverage": sum(p != MISSING for p in predicted) / len(truth),
            "precision": mean(v["precision"] for v in per_class.values()),
            "recall": mean(v["recall"] for v in per_class.values()),
            "f1": mean(v["f1"] for v in per_class.values()),
            "micro_precision": micro_p, "micro_recall": micro_r,
            "micro_f1": 2 * micro_p * micro_r / (micro_p + micro_r) if micro_p + micro_r else 0.0,
            "weighted_f1": sum(v["f1"] * v["support"] for v in per_class.values()) / len(truth),
            "accuracy": sum(y == p for y, p in zip(truth, predicted)) / len(truth),
            "per_class": per_class}


def clinical_status(value: float, event: Event) -> str:
    abnormal = ((event.reference_low is not None and value < event.reference_low)
                or (event.reference_high is not None and value > event.reference_high))
    return "abnormal" if abnormal else "normal"


def evaluate(episodes: Iterable[Episode], rollouts: Iterable[Rollout], aliases: dict[str, str] | None = None) -> dict:
    predictions = {}
    backends, modes = set(), set()
    for result in rollouts:
        key = result.subject_id, result.episode_id
        if key in predictions:
            raise ValueError("Duplicate prediction episode; evaluate each mode/backend separately")
        predictions[key] = result
        backends.add(result.backend)
        modes.add(result.mode)
    if len(backends) > 1 or len(modes) > 1:
        raise ValueError("Do not pool different backends or simulation modes")
    numeric, sensitive = [], []
    label_true, label_pred, status_true, status_pred = [], [], [], []
    round_pairs: dict[int, list] = {}
    concept_pairs: dict[str, list] = {}
    episode_keys = set()
    missing_count = unexpected = interventions = total_inquiries = 0
    for episode in episodes:
        key = episode.subject_id, episode.episode_id
        if key in episode_keys:
            raise ValueError("Duplicate ground-truth episode")
        episode_keys.add(key)
        result = predictions.get(key)
        pred_events = {(s.timestamp, e.action.action_id): e for s in result.steps for e in s.events} if result else {}
        expected_keys = {(s.timestamp, e.action.action_id) for s in episode.steps for e in s.events}
        unexpected += len(set(pred_events) - expected_keys)
        previous = {}
        for round_index, step in enumerate(episode.steps, 1):
            updates = {}
            for event in step.events:
                action = event.action
                if action.kind == "intervention":
                    interventions += 1
                    continue
                total_inquiries += 1
                predicted_event = pred_events.get((step.timestamp, action.action_id))
                if predicted_event and predicted_event.action != action:
                    raise ValueError("Prediction action definition differs from ground truth")
                value = predicted_event.value if predicted_event else None
                missing_count += value is None
                if action.value_type == "number":
                    pair = float(event.value), float(value) if value is not None else None
                    numeric.append(pair)
                    round_pairs.setdefault(round_index, []).append(pair)
                    concept_pairs.setdefault(f"{action.concept} [{action.unit or 'unitless'}]", []).append(pair)
                    prior = previous.get(action.measurement_key)
                    if prior is not None and relative_error(prior, pair[0]) > 0.5:
                        sensitive.append(pair)
                    updates[action.measurement_key] = pair[0]
                    if event.reference_low is not None or event.reference_high is not None:
                        status_true.append(clinical_status(pair[0], event))
                        status_pred.append(clinical_status(pair[1], event) if pair[1] is not None else MISSING)
                else:
                    # Prefix with concept to avoid scoring unrelated categorical tasks as one class.
                    prefix = action.concept + "::"
                    label_true.append(prefix + normalize_label(str(event.value), aliases))
                    label_pred.append(prefix + normalize_label(str(value), aliases) if value is not None else MISSING)
            previous.update(updates)
    if set(predictions) - episode_keys:
        raise ValueError("Predictions contain unknown episodes")
    if unexpected:
        raise ValueError(f"Predictions contain {unexpected} unknown timestamp/action pairs")
    num = numeric_metrics(numeric)
    labels = classification_metrics(label_true, label_pred)
    statuses = classification_metrics(status_true, status_pred)
    # Binary abnormal-positive F1; all-normal + perfect predictions has no abnormal support => 0.
    stat_f1 = statuses["per_class"].get("abnormal", {}).get("f1", 0.0) if status_true else None
    scores = [num["S@25"], stat_f1, labels["f1"]]
    return {"episode_count": len(episode_keys), "inquiry_count": total_inquiries,
            "intervention_count": interventions, "missing_inquiry_count": missing_count,
            "backend": next(iter(backends), None), "mode": next(iter(modes), None),
            "numerical": num, "high_sensitivity": numeric_metrics(sensitive),
            "clinical_status": {**statuses, "abnormal_f1": stat_f1,
                                "reference_range_coverage": len(status_true) / len(numeric) if numeric else None},
            "label": labels, "average_score": mean(scores) if all(s is not None for s in scores) else None,
            "by_round": {str(k): numeric_metrics(v) for k, v in sorted(round_pairs.items())},
            "by_measurement": {k: numeric_metrics(v) for k, v in sorted(concept_pairs.items())},
            "policies": {"label_aggregation": "macro over concept-prefixed normalized labels",
                         "clinical_status": "binary abnormal-positive F1; per-event reference ranges",
                         "SMAPE_scale": "fraction in [0,2]; percent also reported",
                         "missing_numeric": "failure for S@k; SMAPE penalty 2",
                         "sensitivity": "abs change >50% from previous ground truth of same concept AND unit",
                         "zero_denominator": "max(abs(truth), 1e-10)"}}


def retention(full: dict, next_step: dict) -> dict:
    def ratio(a, b):
        return a / b * 100 if a is not None and b is not None and b != 0 else None
    return {"S@25_percent": ratio(full["numerical"]["S@25"], next_step["numerical"]["S@25"]),
            "status_f1_percent": ratio(full["clinical_status"]["abnormal_f1"], next_step["clinical_status"]["abnormal_f1"]),
            "label_f1_percent": ratio(full["label"]["f1"], next_step["label"]["f1"]),
            "overall_percent": ratio(full["average_score"], next_step["average_score"])}
