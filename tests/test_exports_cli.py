from __future__ import annotations

import json

from ehrworld.cli import main
from ehrworld.io import read_jsonl
from ehrworld.supervision import causal_samples


def test_supervision_has_no_current_results_or_intervention_targets(episode):
    samples = list(causal_samples([episode]))
    assert len(samples) == 3
    first = json.loads(samples[0]["messages"][1]["content"])
    assert first["history"] == []
    assert "240" not in samples[0]["messages"][1]["content"]
    second = json.loads(samples[1]["messages"][1]["content"])
    assert len(second["history"]) == 2
    assert second["history"][1]["events"][0]["value"] is None
    for sample in samples:
        assert "reference_low" not in sample["messages"][1]["content"]
        targets = json.loads(sample["messages"][2]["content"])["outcomes"]
        assert all(target["value"] is not None for target in targets)


def test_offline_cli_and_rescore_and_export(tmp_path):
    out = tmp_path / "demo"
    assert main(["demo", "--backend", "offline", "--output-dir", str(out)]) == 0
    assert (out / "report.html").is_file()
    assert (out / "retention.json").is_file()
    assert main(["evaluate", "--input", str(out / "evaluation_episodes.jsonl"),
                 "--predictions", str(out / "full.predictions.jsonl"), "--output", str(out / "rescored.json")]) == 0
    assert json.loads((out / "rescored.json").read_text(encoding="utf-8")) == json.loads((out / "full.metrics.json").read_text(encoding="utf-8"))
    assert main(["export-supervision", "--input", str(out / "evaluation_episodes.jsonl"), "--output", str(out / "samples.jsonl")]) == 0
    assert len(list(read_jsonl(out / "samples.jsonl"))) == 3


def test_cli_refuses_external_write_and_invalid_count(tmp_path):
    assert main(["demo", "--backend", "offline", "--episodes", "0"]) == 1
    assert main(["demo", "--backend", "offline", "--output-dir", "../escape"]) == 1
