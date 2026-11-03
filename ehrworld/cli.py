from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .data import ProfileExtractor, prepare_dataset, split_dataset
from .demo import save_synthetic
from .io import ROOT, output_path, read_jsonl, write_json, write_jsonl
from .llm import APIConfig, ContextLimitError, JSONClient, ModelResponseError
from .metrics import evaluate, retention
from .mimic import MIMICAdapter
from .report import render_report
from .schema import Episode, PatientContext, Rollout
from .simulator import APIPredictor, PersistencePredictor, rollout
from .supervision import causal_samples


def load_episodes(path, limit=None):
    episodes = []
    for row in read_jsonl(path):
        if limit is not None and len(episodes) >= limit:
            break
        episodes.append(Episode.model_validate(row))
    if not episodes:
        raise ValueError("No episodes to simulate")
    return episodes


def make_predictor(backend):
    if backend == "offline":
        return PersistencePredictor(), None
    client = JSONClient(APIConfig.from_env())
    return APIPredictor(client), client


def run_experiment(episodes, predictor, client, output_dir, *, compare=False, mode="full", concurrency=1):
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    out = output_path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    modes = ("full", "next") if compare else (mode,)
    results, reports = {}, {}
    for current_mode in modes:
        print(f"Running {current_mode}: {len(episodes)} episode(s), backend={predictor.name}", flush=True)
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            predictions = list(pool.map(lambda e: rollout(e, predictor, current_mode), episodes))
        results[current_mode] = predictions
        write_jsonl(out / f"{current_mode}.predictions.jsonl", predictions)
        reports[current_mode] = evaluate(episodes, predictions)
        write_json(out / f"{current_mode}.metrics.json", reports[current_mode])
    seconds = time.perf_counter() - started
    stats = client.statistics() if client else {"requests": 0, "backend": predictor.name}
    cases = [r for values in results.values() for r in values]
    stats.update(wall_seconds=seconds, concurrency=concurrency, trajectory_count=len(cases),
                 mean_trajectory_seconds=sum(r.elapsed_seconds for r in cases) / len(cases),
                 aggregate_completion_tokens_per_second=stats.get("completion_tokens", 0) / seconds if seconds else None)
    write_json(out / "efficiency.json", stats)
    ret = retention(reports["full"], reports["next"]) if compare else None
    if ret:
        write_json(out / "retention.json", ret)
    primary = reports.get("full", reports.get("next"))
    report = render_report(out / "report.html", primary, reports.get("next") if compare else None,
                           episodes=episodes[:5], full_rollouts=results.get("full", ()), next_rollouts=results.get("next", ()),
                           retention_result=ret, efficiency=stats)
    write_json(out / "run_manifest.json", {"project_version": "0.1.0", "backend": predictor.name,
                                          "modes": list(modes), "subject_ids": sorted({e.subject_id for e in episodes}),
                                          "episode_ids": [e.episode_id for e in episodes], "concurrency": concurrency,
                                          "context": "retrospective episode diagnoses from discharge summaries, following the paper"})
    print(f"Report: {report}")
    return reports


def parser():
    p = argparse.ArgumentParser(description="EHRWorld data, simulation and evaluation reproduction (external API; no fine-tuning)")
    sub = p.add_subparsers(dest="command", required=True)
    smoke = sub.add_parser("api-smoke", help="Extract a static profile from an artificial note using the real API")
    smoke.add_argument("--output", default="outputs/api_smoke.json")
    extract = sub.add_parser("extract-profile", help="Extract the paper A.1 profile from a note")
    extract.add_argument("--note", required=True)
    extract.add_argument("--output", required=True)
    demo = sub.add_parser("demo", help="Generate synthetic data, prepare, split, and compare full vs next")
    demo.add_argument("--backend", choices=["api", "offline"], default="api")
    demo.add_argument("--output-dir", default="outputs/demo")
    demo.add_argument("--episodes", type=int, default=1)
    prepare = sub.add_parser("prepare", help="Build episodes from normalized raw admission JSONL")
    prepare.add_argument("--input", required=True)
    prepare.add_argument("--output-dir", default="data/prepared")
    prepare.add_argument("--offline", action="store_true", help="Require supplied static context; make no API calls")
    prepare.add_argument("--no-statistical-filter", action="store_true")
    mimic = sub.add_parser("prepare-mimic", help="Stream native MIMIC-IV tables via local SQLite")
    mimic.add_argument("--mimic-root", required=True)
    mimic.add_argument("--notes-path")
    mimic.add_argument("--output-dir", default="data/mimic")
    mimic.add_argument("--include-icu", action="store_true")
    mimic.add_argument("--allow-date-only", action="store_true")
    mimic.add_argument("--timestamp-policy", choices=["chart", "available"], default="chart")
    mimic.add_argument("--max-admissions", type=int)
    mimic.add_argument("--no-statistical-filter", action="store_true")
    split = sub.add_parser("split", help="Diagnosis-stratified patient split")
    split.add_argument("--input", required=True)
    split.add_argument("--output-dir", default="data/split")
    split.add_argument("--test-fraction", type=float, default=.00524)
    split.add_argument("--test-subjects", type=int)
    split.add_argument("--seed", type=int, default=42)
    for name in ("simulate", "benchmark"):
        command = sub.add_parser(name, help="Run full/next simulation and evaluation")
        command.add_argument("--input", required=True)
        command.add_argument("--output-dir", default=f"outputs/{name}")
        command.add_argument("--backend", choices=["api", "offline"], default="api")
        command.add_argument("--mode", choices=["full", "next"], default="full")
        command.add_argument("--compare", action="store_true")
        command.add_argument("--limit", type=int)
        command.add_argument("--concurrency", type=int, default=1)
    score = sub.add_parser("evaluate", help="Evaluate saved prediction JSONL without making API calls")
    score.add_argument("--input", required=True)
    score.add_argument("--predictions", required=True)
    score.add_argument("--output", default="outputs/evaluation.json")
    score.add_argument("--label-aliases", help="JSON mapping of normalized label synonyms")
    export = sub.add_parser("export-supervision", help="Export causal inquiry-only examples; does not train a model")
    export.add_argument("--input", required=True)
    export.add_argument("--output", default="data/causal_samples.jsonl")
    analyze = sub.add_parser("analyze-data", help="Create input-cohort distribution statistics and Figure-5-style histograms")
    analyze.add_argument("--input", required=True)
    analyze.add_argument("--output-dir", default="outputs/data_analysis")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    client = None
    try:
        if args.command in ("api-smoke", "extract-profile"):
            client = JSONClient(APIConfig.from_env())
            note = ("Artificial record, not a real patient. A 50-year-old female was admitted for hyperglycemia. "
                    "Diabetes was addressed this stay. No documented allergies or family history.") if args.command == "api-smoke" else Path(args.note).read_text(encoding="utf-8")
            profile = ProfileExtractor(client).extract(note)
            if args.command == "api-smoke":
                write_json(args.output, {"success": True, "profile": profile.model_dump(mode="json", by_alias=True),
                                         "api": client.statistics(), "synthetic": True})
            else:
                write_json(args.output, profile)
            print(f"Saved: {output_path(args.output)}")
        elif args.command == "prepare":
            if not args.offline:
                client = JSONClient(APIConfig.from_env())
            report = prepare_dataset(read_jsonl(args.input), args.output_dir,
                                     ProfileExtractor(client) if client else None, not args.no_statistical_filter)
            print(f"Retained {report['retained']} / {report['valid_before_filter']} valid episodes")
        elif args.command == "prepare-mimic":
            if args.max_admissions is not None and args.max_admissions < 1:
                raise ValueError("max-admissions must be positive")
            client = JSONClient(APIConfig.from_env())
            adapter = MIMICAdapter(args.mimic_root, notes_path=args.notes_path,
                                   staging_dir=Path(args.output_dir) / "staging", include_icu=args.include_icu,
                                   allow_date_only=args.allow_date_only, timestamp_policy=args.timestamp_policy,
                                   max_admissions=args.max_admissions)
            report = prepare_dataset(adapter, args.output_dir, ProfileExtractor(client), not args.no_statistical_filter)
            write_json(Path(args.output_dir) / "import_report.json", dict(adapter.report))
            print(f"Retained {report['retained']} episodes; staging: {adapter.db_path}")
        elif args.command == "split":
            result = split_dataset(args.input, args.output_dir, args.test_fraction, args.seed, args.test_subjects)
            print(f"Train: {result['train_episodes']}; test: {result['test_episodes']}; subject overlap: 0")
        elif args.command == "demo":
            if args.episodes < 1:
                raise ValueError("episodes must be positive")
            out = output_path(args.output_dir)
            raw = save_synthetic(out / "synthetic_raw.jsonl")
            prepared = out / "prepared"
            prepare_dataset(read_jsonl(raw), prepared, statistical_filter=False)
            split_dataset(prepared / "episodes.jsonl", out / "split", test_fraction=.2)
            episodes = load_episodes(out / "split" / "test.jsonl", args.episodes)
            write_jsonl(out / "evaluation_episodes.jsonl", episodes)
            predictor, client = make_predictor(args.backend)
            run_experiment(episodes, predictor, client, out, compare=True)
        elif args.command in ("simulate", "benchmark"):
            if args.limit is not None and args.limit < 1:
                raise ValueError("limit must be positive")
            episodes = load_episodes(args.input, args.limit)
            predictor, client = make_predictor(args.backend)
            run_experiment(episodes, predictor, client, args.output_dir, compare=args.compare,
                           mode=args.mode, concurrency=args.concurrency)
        elif args.command == "evaluate":
            aliases = json.loads(Path(args.label_aliases).read_text(encoding="utf-8")) if args.label_aliases else None
            report = evaluate((Episode.model_validate(r) for r in read_jsonl(args.input)),
                              (Rollout.model_validate(r) for r in read_jsonl(args.predictions)), aliases)
            write_json(args.output, report)
            print(f"Saved: {output_path(args.output)}")
        elif args.command == "export-supervision":
            write_jsonl(args.output, causal_samples(Episode.model_validate(row) for row in read_jsonl(args.input)))
            print(f"Saved: {output_path(args.output)}")
        elif args.command == "analyze-data":
            from .analysis import analyze_dataset
            print(f"Report: {analyze_dataset(args.input, args.output_dir)}")
        return 0
    except (ValueError, OSError, ModelResponseError, ContextLimitError) as exc:
        # Pydantic errors may include patient input; only emit the error class for them.
        from pydantic import ValidationError
        message = "Invalid patient data schema; inspect the local input" if isinstance(exc, ValidationError) else str(exc)
        if args.command == "api-smoke":
            write_json(args.output, {"success": False, "error": message,
                                     "api": client.statistics() if client else {}, "synthetic": True})
        print(f"Error: {message}", file=sys.stderr)
        return 1
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
