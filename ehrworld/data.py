from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Iterable

from pydantic import ValidationError

from .io import output_path, read_jsonl, write_json, write_jsonl
from .llm import JSONClient
from .prompts import EXTRACT_SYSTEM
from .schema import Action, Episode, Event, PatientContext, Step, naive_utc


class ProfileExtractor:
    def __init__(self, client: JSONClient, cache_dir: str | Path = "data/profile_cache"):
        self.client = client
        self.cache_dir = output_path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def extract(self, note: str) -> PatientContext:
        if not note.strip():
            raise ValueError("A discharge note or an explicitly supplied profile is required")
        key = hashlib.sha256((self.client.config.base_url + self.client.config.model + EXTRACT_SYSTEM + note).encode()).hexdigest()
        target = self.cache_dir / f"{key}.json"
        if target.exists():
            return PatientContext.model_validate_json(target.read_text(encoding="utf-8"))
        profile = self.client.request(EXTRACT_SYSTEM, {"discharge_record": note}, PatientContext, operation="extract-profile")
        write_json(target, profile)
        return profile


def parse_time(value: str | datetime) -> datetime:
    return naive_utc(value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00")))


def construct_episode(raw: dict, extractor: ProfileExtractor | None = None) -> tuple[Episode, Counter]:
    """Normalize, remove nonexecuted/incomplete/duplicate events, group concurrent sets."""
    counts = Counter()
    admitted, discharged = parse_time(raw["admitted_at"]), parse_time(raw["discharged_at"])
    context = PatientContext.model_validate(raw["context"]) if raw.get("context") else None
    if context is None:
        if extractor is None:
            raise ValueError("Profile extraction requires the API; --offline requires pre-extracted context")
        context = extractor.extract(raw.get("note", ""))
    if raw.get("demographics"):
        context = PatientContext.model_validate({**context.model_dump(), "demographics": {
            **context.demographics, **{k: v for k, v in raw["demographics"].items() if v is not None}}})
    if context.diagnoses.primary.content.strip().casefold() in {"unknown", "n/a", "未提供", "未知"}:
        raise ValueError("Primary diagnosis is missing")
    groups, seen = defaultdict(list), set()
    for row in raw.get("events", []):
        if row.get("executed", True) is not True:
            counts["not_executed"] += 1
            continue
        try:
            timestamp = parse_time(row["timestamp"])
            if not admitted <= timestamp <= discharged:
                counts["outside_stay"] += 1
                continue
            action = Action(action_id=str(row["event_id"]), kind=row["kind"], name=row["name"],
                            concept=row.get("concept", row["name"]), source=row["source"],
                            value_type=row.get("value_type"), unit=row.get("unit"), parameters=row.get("parameters", {}))
            event = Event(action=action, value=row.get("value"), reference_low=row.get("reference_low"),
                          reference_high=row.get("reference_high"))
        except (KeyError, TypeError, ValueError, ValidationError):
            counts["incomplete_or_invalid"] += 1
            continue
        signature = json.dumps({"time": timestamp.isoformat(), "action": action.model_dump(exclude={"action_id"}),
                                "value": event.value}, sort_keys=True, ensure_ascii=False)
        if signature in seen:
            counts["duplicate"] += 1
            continue
        seen.add(signature)
        groups[timestamp].append(event)
    steps = tuple(Step(timestamp=t, events=tuple(sorted(events, key=lambda e: e.action.action_id)))
                  for t, events in sorted(groups.items()))
    return Episode(subject_id=str(raw["subject_id"]), episode_id=str(raw["episode_id"]),
                   admitted_at=admitted, discharged_at=discharged, context=context, steps=steps), counts


def episode_statistics(episode: Episode) -> dict[str, float]:
    events = [e for s in episode.steps for e in s.events]
    counts = Counter(e.action.source for e in events)
    los = (episode.discharged_at - episode.admitted_at).total_seconds() / 86400
    return {"los_days": los, "total_events": len(events), "lab_events": counts["labevents"],
            "microbiology_events": counts["microbiologyevents"], "medication_events": counts["emar"],
            "procedure_events": counts["procedureevents"], "omr_events": counts["omr"],
            "event_intensity": len(events) / max(los, 1 / 86400)}


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("No population for percentile computation")
    position = (len(ordered) - 1) * fraction
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def prepare_dataset(raw_rows: Iterable[dict], output_dir: str | Path, extractor: ProfileExtractor | None = None,
                    statistical_filter: bool = True) -> dict:
    """Two disk passes; event objects for only one admission are retained at a time."""
    out = output_path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stage = out / "episodes.unfiltered.jsonl"
    stats, identifiers, rejected, cleaning = [], set(), [], Counter()
    with stage.open("w", encoding="utf-8") as handle:
        for raw in raw_rows:
            try:
                episode, removed = construct_episode(raw, extractor)
                cleaning.update(removed)
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                # Omit validation bodies, which can contain complete patient records.
                rejected.append({"episode_id": str(raw.get("episode_id", "unknown")), "reason": type(exc).__name__})
                continue
            if episode.episode_id in identifiers:
                raise ValueError("Duplicate episode_id in input")
            identifiers.add(episode.episode_id)
            stats.append(episode_statistics(episode))
            handle.write(episode.model_dump_json() + "\n")
    if not stats:
        write_json(out / "preparation_report.json", {"rejected": rejected, "cleaning": dict(cleaning)})
        raise ValueError("No valid episodes; inspect preparation_report.json")
    upper_keys = ("los_days", "lab_events", "microbiology_events", "medication_events")
    upper = {k: percentile([s[k] for s in stats], .9) for k in upper_keys}
    lower_total = percentile([s["total_events"] for s in stats], .1)
    upper_total = percentile([s["total_events"] for s in stats], .9)
    retained_stats = []

    def retained():
        for row, stat in zip(read_jsonl(stage), stats):
            reasons = []
            if stat["medication_events"] == 0:
                reasons.append("no_emar")
            if statistical_filter:
                reasons += [f"above_p90:{k}" for k in upper_keys if stat[k] > upper[k]]
                if not lower_total <= stat["total_events"] <= upper_total:
                    reasons.append("total_events_outside_p10_p90")
            if reasons:
                rejected.append({"episode_id": row["episode_id"], "reason": reasons})
            else:
                retained_stats.append(stat)
                yield row

    write_jsonl(out / "episodes.jsonl", retained())
    report = {"valid_before_filter": len(stats), "retained": len(retained_stats), "rejected": rejected,
              "cleaning": dict(cleaning), "statistical_filter": statistical_filter,
              "thresholds": {"upper_p90": upper, "total_events_p10": lower_total, "total_events_p90": upper_total},
              "distribution": {k: {"min": min(s[k] for s in retained_stats), "max": max(s[k] for s in retained_stats),
                                   "mean": mean(s[k] for s in retained_stats), "median": median(s[k] for s in retained_stats)}
                               for k in stats[0] if retained_stats},
              "warning": "These are this input cohort's statistics, not the published EHRWorld-110K dataset."}
    write_json(out / "preparation_report.json", report)
    return report


def split_dataset(path: str | Path, output_dir: str | Path, test_fraction: float = .00524, seed: int = 42,
                  test_subjects: int | None = None) -> dict:
    """Greedy diagnosis-stratified subject allocation; never split a patient's admissions."""
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be between zero and one")
    subjects, cohort = defaultdict(Counter), Counter()
    seen_episodes = set()
    for row in read_jsonl(path):
        ep = Episode.model_validate(row)
        if ep.episode_id in seen_episodes:
            raise ValueError("Duplicate episode_id")
        seen_episodes.add(ep.episode_id)
        d = ep.context.diagnoses.primary
        category = d.code or d.content.strip().casefold()
        subjects[ep.subject_id][category] += 1
        cohort[category] += 1
    if len(subjects) < 2:
        raise ValueError("At least two distinct subjects are needed for splitting")
    target_count = test_subjects if test_subjects is not None else max(1, round(len(subjects) * test_fraction))
    if not 1 <= target_count < len(subjects):
        raise ValueError("test_subjects must leave at least one subject in each partition")
    rng = random.Random(seed)
    candidates = sorted(subjects)
    rng.shuffle(candidates)
    targets = {k: v * target_count / len(subjects) for k, v in cohort.items()}
    chosen, selected = set(), Counter()
    # Inverted index limits candidate evaluation for large cohorts.
    by_category = defaultdict(list)
    for subject in candidates:
        for category in subjects[subject]:
            by_category[category].append(subject)
    for _ in range(target_count):
        deficits = sorted(targets, key=lambda c: (targets[c] - selected[c]) / max(targets[c], 1), reverse=True)
        pool = []
        for category in deficits:
            pool = [s for s in by_category[category] if s not in chosen][:256]
            if pool:
                break
        def cost(subject):
            return sum((abs(selected[c] + n - targets[c]) - abs(selected[c] - targets[c])) / max(targets[c], 1)
                       for c, n in subjects[subject].items())
        subject = min(pool, key=cost)
        chosen.add(subject)
        selected.update(subjects[subject])
    out = output_path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    train_count = test_count = 0
    with (out / "train.jsonl").open("w", encoding="utf-8") as train, (out / "test.jsonl").open("w", encoding="utf-8") as test:
        for row in read_jsonl(path):
            is_test = row["subject_id"] in chosen
            (test if is_test else train).write(json.dumps(row, ensure_ascii=False) + "\n")
            train_count += not is_test
            test_count += is_test
    result = {"seed": seed, "train_subjects": len(subjects) - len(chosen), "test_subjects": len(chosen),
              "train_episodes": train_count, "test_episodes": test_count,
              "test_subject_ids": sorted(chosen), "primary_category_total": dict(cohort),
              "primary_category_test": dict(selected),
              "method": "greedy diagnosis-stratified subject split; exact 579-episode test set is not assumed"}
    write_json(out / "split_manifest.json", result)
    return result
