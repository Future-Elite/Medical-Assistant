"""Dataset distributions corresponding to paper Figure 5, computed from input."""
from __future__ import annotations

import html
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

from .data import episode_statistics
from .io import output_path, read_jsonl, write_json
from .schema import Episode


def analyze_dataset(path: str | Path, output_dir: str | Path):
    out = output_path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    values = defaultdict(list)
    for row in read_jsonl(path):
        episode = Episode.model_validate(row)
        for key, value in episode_statistics(episode).items():
            values[key].append(value)
        age = episode.context.demographics.get("age")
        if age is None:
            match = re.search(r"(?:age\s*[:=]?\s*)(\d+(?:\.\d+)?)|\b(\d+)\s*[- ]year[- ]old", episode.context.basic_information, re.I)
            age = float(next(g for g in match.groups() if g)) if match else None
        if isinstance(age, (int, float)) and not isinstance(age, bool):
            values["age"].append(age)
    if not values:
        raise ValueError("No episodes to analyze")
    stats = {key: {"count": len(series), "min": min(series), "max": max(series), "mean": mean(series), "median": median(series)}
             for key, series in values.items() if series}
    write_json(out / "dataset_statistics.json", stats)
    # Matplotlib can otherwise create a font cache outside the authorized directory.
    os.environ["MPLCONFIGDIR"] = str(output_path("tmp/matplotlib"))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        raise ValueError("Dataset plotting requires matplotlib; install the analysis extra in the project .venv") from None
    panels = [("age", "Age (years)"), ("los_days", "Length of stay (days)"), ("total_events", "Total events"),
              ("lab_events", "Lab events"), ("microbiology_events", "Microbiology events"),
              ("event_intensity", "Events/day"), ("procedure_events", "Procedure events"),
              ("medication_events", "Medication (EMAR)"), ("omr_events", "OMR events")]
    fig, axes = plt.subplots(3, 3, figsize=(14, 10), layout="constrained")
    for ax, (key, label) in zip(axes.flat, panels):
        series = values.get(key, [])
        ax.set_title(label)
        if not series:
            ax.text(.5, .5, "Unavailable", transform=ax.transAxes, ha="center")
            continue
        bins = min(30, max(1, len(set(series))))
        ax.hist(series, bins=bins, color="#2563eb", alpha=.8, edgecolor="white")
        if key != "age":
            ax.set_yscale("log")
        summary = stats[key]
        ax.text(.97, .96, f"n={summary['count']}\nMax={summary['max']:.2f}\nMean={summary['mean']:.2f}\nMedian={summary['median']:.2f}",
                transform=ax.transAxes, ha="right", va="top", fontsize=9,
                bbox={"facecolor": "white", "alpha": .8, "edgecolor": "none"})
        ax.set_ylabel("Episodes")
    fig.suptitle("Input-cohort distributions (not the published EHRWorld-110K cohort)", fontsize=14)
    fig.savefig(out / "dataset_distribution.png", dpi=160)
    plt.close(fig)
    (out / "dataset_report.html").write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Dataset distribution</title>'
        '<style>body{font:16px system-ui;max-width:1200px;margin:30px auto;padding:20px}img{max-width:100%}pre{white-space:pre-wrap}</style>'
        '<h1>输入队列分布</h1><p>这些统计来自实际输入；合成数据不代表 EHRWorld-110K。</p>'
        '<img src="dataset_distribution.png" alt="Nine dataset distribution histograms">'
        f'<pre>{html.escape(json.dumps(stats, ensure_ascii=False, indent=2))}</pre></html>', encoding="utf-8")
    return out / "dataset_report.html"
