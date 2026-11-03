from __future__ import annotations

import copy
import json

import pytest

from ehrworld.data import construct_episode, prepare_dataset, split_dataset
from ehrworld.demo import synthetic_admissions
from ehrworld.io import ROOT, output_path, read_jsonl, write_jsonl
from ehrworld.metrics import classification_metrics, evaluate, numeric_metrics, retention
from ehrworld.schema import Episode, Rollout, Step


def test_cleaning_concurrency_and_executed_filter(raw):
    altered = copy.deepcopy(raw)
    altered["events"] += [dict(raw["events"][0], event_id="duplicate"),
                           dict(raw["events"][0], event_id="not-done", executed=False),
                           dict(raw["events"][0], event_id="bad", value=None),
                           dict(raw["events"][0], event_id="late", timestamp="2030-01-01")]
    episode, removed = construct_episode(altered)
    assert dict(removed) == {"duplicate": 1, "not_executed": 1, "incomplete_or_invalid": 1, "outside_stay": 1}
    assert len(episode.steps) == 4
    assert len(episode.steps[0].events) == 2


def test_patient_split_is_disjoint_stratified_and_reproducible(tmp_path):
    prepared = tmp_path / "prepared"
    report = prepare_dataset(synthetic_admissions(24), prepared, statistical_filter=True)
    assert report["retained"] == 24
    left = split_dataset(prepared / "episodes.jsonl", tmp_path / "left", .25, seed=4)
    right = split_dataset(prepared / "episodes.jsonl", tmp_path / "right", .25, seed=4)
    assert left == right
    train = list(read_jsonl(tmp_path / "left/train.jsonl"))
    test = list(read_jsonl(tmp_path / "left/test.jsonl"))
    assert not {r["subject_id"] for r in train} & {r["subject_id"] for r in test}
    assert sum(left["primary_category_test"].values()) == len(test)
    assert len(left["primary_category_test"]) == 3


def test_quantile_filter_and_zero_medication_are_not_optional(tmp_path):
    rows = list(synthetic_admissions(20))
    for row in rows:
        row["events"] = [e for e in row["events"] if e["kind"] != "intervention"]
    report = prepare_dataset(rows, tmp_path / "no-meds", statistical_filter=False)
    assert report["retained"] == 0
    assert all("no_emar" in item["reason"] for item in report["rejected"])
    rows = list(synthetic_admissions(20))
    rows[-1]["discharged_at"] = "2050-01-01"
    report = prepare_dataset(rows, tmp_path / "outlier", statistical_filter=True)
    assert report["retained"] == 19
    assert "above_p90:los_days" in report["rejected"][0]["reason"]


def test_metric_boundaries_zero_and_missing_are_finite():
    result = numeric_metrics([(100, 110), (0, 0), (100, None), (100, 125)])
    assert result["S@10"] == .5
    assert result["S@25"] == .75
    assert result["coverage"] == .75
    assert 0 <= result["SMAPE"] <= 2
    result = classification_metrics(["a", "a", "b"], ["a", "b", "b"])
    assert result["precision"] == .75
    assert result["recall"] == .75
    assert result["f1"] == pytest.approx(2 / 3)


def test_perfect_score_status_and_sensitivity(episode):
    prediction = Rollout(subject_id=episode.subject_id, episode_id=episode.episode_id,
                         mode="full", backend="oracle-test-only", steps=episode.steps)
    report = evaluate([episode], [prediction])
    assert report["average_score"] == 1
    assert report["numerical"]["SMAPE"] == 0
    # Potassium 3.1->4.8 exceeds 50%; the glucose decline does not.
    assert report["high_sensitivity"]["count"] == 1
    assert retention(report, report)["overall_percent"] == 100
    missing = evaluate([episode], [])
    assert missing["numerical"]["S@25"] == 0
    assert missing["numerical"]["SMAPE"] == 2
    assert missing["missing_inquiry_count"] == 8


def test_unknown_predictions_rejected(episode):
    extra = episode.steps[0].events[0].model_copy(update={"action": episode.steps[0].events[0].action.model_copy(update={"action_id": "unknown"})})
    prediction = Rollout(subject_id=episode.subject_id, episode_id=episode.episode_id, mode="full", backend="test",
                         steps=(Step(timestamp=episode.steps[0].timestamp, events=(extra,)),))
    with pytest.raises(ValueError, match="unknown"):
        evaluate([episode], [prediction])


def test_output_cannot_escape_workspace():
    with pytest.raises(ValueError):
        output_path(ROOT.parent / "not-allowed.txt")
    with pytest.raises(ValueError):
        output_path("../not-allowed.txt")
