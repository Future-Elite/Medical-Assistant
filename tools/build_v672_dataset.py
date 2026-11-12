#!/usr/bin/env python3
"""Build the v6.7.2 rule-derived dataset from the hospital discharge export.

Real patient text is written only under the project's ``private_data/`` folder
(inside the project directory, outside every git repository).  The repository
receives a single aggregate JSON (counts, hashes, distributions) and never
receives patient text, patient references or evidence spans.

Example::

    export CHINALLM_PRIVATE_DATA_DIR="<repository root>/private_data"
    export CHINALLM_PATIENT_SALT="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
    cd <repository root>
    PYTHONPATH=. python3 tools/build_v672_dataset.py \
        --discharge <hospital discharge export>.xlsx
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys

from rag.eval.qrels.build_dataset import (
    BuildConfig,
    assign_splits,
    build_counterfactual_pairs,
    build_manifest,
    build_outputs,
    build_patient_records,
    build_synthetic_questions,
)
from rag.eval.qrels.gold_rules import load_criteria_fixture
from rag.eval.qrels.hospital_adapter import (
    dedupe,
    rows_from_sheet,
    select_patient_rows,
)
from rag.eval.qrels.xlsx_reader import read_xlsx

MODULE_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_DIR = MODULE_ROOT / "rag" / "validation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discharge", required=True, help="path to the discharge-summary xlsx")
    parser.add_argument("--subdir", default=None, help="output subdirectory under the private root")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    private_root = _private_root()
    salt = _salt()
    source = Path(args.discharge).expanduser().resolve()
    if not source.is_file():
        print(f"discharge export not found: {source}", file=sys.stderr)
        return 2
    output_dir = private_root / (args.subdir or f"v6.7.2_dataset_{date.today().isoformat()}")
    if output_dir.exists():
        print(f"refusing to overwrite existing private dataset directory: {output_dir}", file=sys.stderr)
        return 2
    _assert_inside_private_data(output_dir)

    sheets = read_xlsx(source)
    if "Sheet1" not in sheets:
        print(f"worksheet Sheet1 not found; got {sorted(sheets)}", file=sys.stderr)
        return 2
    raw_rows = rows_from_sheet(sheets["Sheet1"])
    deduped = dedupe(raw_rows)
    patient_rows = select_patient_rows(deduped)
    merged = len(deduped) - len(patient_rows)
    fixture = load_criteria_fixture()
    print(f"source rows={len(raw_rows)} deduped={len(deduped)} admissions={len(patient_rows)} merged={merged}")

    records = build_patient_records(patient_rows, salt=salt, fixture=fixture)
    config = BuildConfig(seed=args.seed)
    train, test_cases = assign_splits(records, config)
    pairs = build_counterfactual_pairs(train, fixture, seed=config.seed, count=config.pair_n)
    questions = build_synthetic_questions(
        train, fixture, seed=config.seed, count=config.question_n,
    )
    manifest = build_manifest(
        source_path=source,
        rows_total=len(raw_rows),
        unique_admissions=len({row.admission_no for row in deduped}),
        duplicate_rows=len(raw_rows) - len(deduped),
        merged_admissions=merged,
        config=config,
        criteria_fixture_path=MODULE_ROOT / "rag" / "eval" / "criteria_fixture" / "registry_scope_criteria.json",
    )
    templates = json.loads(
        (MODULE_ROOT / "rag" / "eval" / "criteria_fixture" / "fact_question_templates.json")
        .read_text(encoding="utf-8")
    )
    aggregates = build_outputs(
        records, test_cases, pairs, questions,
        output_dir=output_dir, manifest=manifest, templates=templates,
    )
    repo_payload = {
        "schema_version": aggregates["schema_version"],
        "id_level": aggregates["id_level"],
        "generated_at": manifest["generated_at"],
        "source": manifest["source"],
        "build": manifest["build"],
        "split_counts": aggregates["split_counts"],
        "case_type_counts": aggregates["case_type_counts"],
        "guideline_status_counts": aggregates["guideline_status_counts"],
        "criterion_reason_counts": aggregates["criterion_reason_counts"],
        "missing_field_counts": aggregates["missing_field_counts"],
        "coverage_size_counts": aggregates["coverage_size_counts"],
        "fact_status_counts": aggregates["fact_status_counts"],
        "counts": {
            "test_cases": aggregates["test_case_count"],
            "counterfactual_pairs": aggregates["counterfactual_pair_count"],
            "synthetic_questions": aggregates["synthetic_question_count"],
            "fact_qrels": aggregates["fact_qrel_count"],
        },
        "overlap": aggregates["overlap"],
        "flags": aggregates["flags"],
        "caveats": manifest["caveats"],
        "private_output_dir_name": output_dir.name,
    }
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = _unique_path(VALIDATION_DIR / f"v672_dataset_build_{date.today().isoformat()}.json")
    repo_path.write_text(json.dumps(repo_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(repo_payload, ensure_ascii=False, indent=2))
    print(f"private dataset: {output_dir}")
    print(f"repository aggregate: {repo_path}")
    return 0


def _private_root() -> Path:
    default = MODULE_ROOT / "private_data"  # <project>/private_data
    value = os.environ.get("CHINALLM_PRIVATE_DATA_DIR", "").strip()
    root = Path(value).expanduser().resolve() if value else default.resolve()
    allowed = (MODULE_ROOT / "private_data").resolve()
    if not str(root).startswith(str(allowed) + os.sep) and root != allowed:
        raise SystemExit(
            f"CHINALLM_PRIVATE_DATA_DIR must stay under {allowed} "
            "(the project's private_data folder; do not write outside the project)"
        )
    return root


def _salt() -> bytes:
    value = os.environ.get("CHINALLM_PATIENT_SALT", "")
    if not value:
        raise SystemExit("CHINALLM_PATIENT_SALT is required (do not commit it)")
    return value.encode("utf-8")


def _assert_inside_private_data(path: Path) -> None:
    resolved = path.resolve()
    allowed = (MODULE_ROOT / "private_data").resolve()
    if not (resolved == allowed or str(resolved).startswith(str(allowed) + os.sep)):
        raise SystemExit(f"private dataset directory must stay under {allowed}: {resolved}")


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(2, 100):
        candidate = path.with_name(f"{stem}_run{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise SystemExit(f"cannot find a free output name for {path}")


if __name__ == "__main__":
    raise SystemExit(main())
