#!/usr/bin/env python3
"""Generate 30 neonatal demo cases with the HIS generator's selection rules.

Why this is a port, not a direct run
------------------------------------
``origin/his`` is a Spring Boot 3.3 / Java 21 project.  Its generator writes to
MySQL ``his_demo`` and its patient generator is adult-only (18--89 years).  This
machine has Java 11, no Maven and no MySQL listener, so the Java binary cannot
be executed here without installing a second toolchain and a neonatal ontology
database.

This script ports the parts that determine the demo data:

* a neonatal ontology fixture with ``dept_diagnosis``-style weights;
* weighted diagnosis selection, then diagnosis -> drug / lab / dose pools;
* a strictly increasing visit chain and downstream event times;
* GB 11643 identity and synthetic demographics.

It then reuses the v6.6.5 demo builder/validator in
``tools/generate_neonatal_his.py``.  The output is the same drop-in demo format:
``load_demo_cases(<output dir>)`` works directly.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_neonatal_his as base  # noqa: E402

DEFAULT_SEED = 20261018
DEFAULT_PATIENTS = 30
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "rag" / "demo" / "his_generated_30"

#: Neonatal ontology weights, the analogue of ``dept_diagnosis.weight``.
#: The retrospective RDS row keeps a small weight so a 30-case batch still
#: exercises the ``as_of`` future-evidence boundary.
ONTOLOGY_WEIGHTS: dict[str, int] = {
    "neo-his-rds": 18,
    "neo-his-rds-severe": 8,
    "neo-his-rds-retro": 3,
    "neo-his-pneumonia": 15,
    "neo-his-ttn": 12,
    "neo-his-jaundice": 12,
    "neo-his-pphn": 6,
    "neo-his-bpd": 5,
    "neo-his-hypoglycemia": 12,
    "neo-his-gbs": 7,
    "neo-his-apnea": 5,
}
SCENARIO_BY_ID = {scenario.case_id: scenario for scenario in base.SCENARIOS}


def _sample_scenarios(rng: random.Random, count: int) -> list[str]:
    """Stratified weighted sampling.

    Every ontology row appears at least once when ``count`` is at least the
    number of rows; the remainder is filled by weighted sampling and the whole
    list is shuffled.  This keeps a 30-case demo diverse instead of letting
    RDS dominate by chance.
    """
    ids = list(ONTOLOGY_WEIGHTS)
    weights = [ONTOLOGY_WEIGHTS[item] for item in ids]
    if count <= len(ids):
        chosen: list[str] = []
        while len(chosen) < count:
            candidate = rng.choices(ids, weights=weights, k=1)[0]
            if candidate not in chosen:
                chosen.append(candidate)
        return chosen
    sampled = list(ids)
    sampled.extend(rng.choices(ids, weights=weights, k=count - len(ids)))
    rng.shuffle(sampled)
    return sampled


def _base_date(scenario_id: str, scenario_base: str, rng: random.Random) -> str:
    if scenario_id == "neo-his-rds-retro":
        # Keep the retrospective case before the RDS 2025 publication date.
        return scenario_base
    return (date.fromisoformat(scenario_base) + timedelta(days=rng.randint(0, 30))).isoformat()


def _write_case_index(out_dir: Path, cases: list[dict[str, Any]]) -> Path:
    path = out_dir / "case_index.csv"
    rows = []
    for case in cases:
        diagnoses = [
            str(fact["fields"].get("name") or "")
            for fact in case["snapshot"]["facts"]
            if fact["collection"] == "diagnoses"
        ]
        theme = case["label"].split(" · ")[1] if " · " in case["label"] else ""
        rows.append([
            case["case_id"],
            case["snapshot"]["patient_ref"],
            case["snapshot"]["as_of"],
            theme,
            "；".join(diagnoses),
            case["preset"]["question"],
        ])
    base._write_csv(
        path,
        ["case_id", "patient_ref", "as_of", "theme", "primary_diagnosis", "question"],
        rows,
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patients", type=int, default=DEFAULT_PATIENTS, help="number of cases")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="deterministic seed")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="output directory")
    parser.add_argument("--no-validate", action="store_true", help="skip rag contract validation")
    args = parser.parse_args(argv)
    if args.patients < 1:
        parser.error("--patients must be >= 1")

    # Deterministic PRNG is intentional: this is synthetic demo data, not a
    # security-sensitive token. Replacing it with secrets would break
    # reproducibility, which is a stated property of this dataset.
    rng = random.Random(args.seed)
    sampled = _sample_scenarios(rng, args.patients)
    cases: list[dict[str, Any]] = []
    for index, scenario_id in enumerate(sampled, start=1):
        scenario = SCENARIO_BY_ID[scenario_id]
        slug = scenario_id.removeprefix("neo-his-")
        case_id = f"neo-his-gen-{index:03d}-{slug}"
        patient_ref = f"neo-his-gen-{index:03d}-001"
        cases.append(base._build_case(
            scenario,
            rng,
            1,
            case_id_override=case_id,
            base_date_override=_base_date(scenario_id, scenario.base_date, rng),
            patient_ref_override=patient_ref,
        ))

    out_dir: Path = args.output
    out_dir.mkdir(parents=True, exist_ok=True)
    cases_path = out_dir / "neonatal_cases.json"
    snapshots_path = out_dir / "snapshots.json"
    requests_path = out_dir / "requests.json"
    payload = base._build_payload(cases)
    payload["note"] = (
        "HIS 风格合成新生儿数据；由 tools/generate_neonatal_his30.py 确定性生成，"
        "仅用于界面、契约与流程测试，不是真实患者，也不构成诊疗建议。"
    )
    base._write_json(cases_path, payload)
    base._write_json(snapshots_path, base._build_snapshots(cases))
    base._write_json(requests_path, base._build_requests(cases))
    index_path = _write_case_index(out_dir, cases)

    tables = base._derive_tables(cases, args.seed)
    for name, (headers, rows) in tables.items():
        base._write_csv(out_dir / "tables" / name, headers, rows)

    if args.no_validate:
        validation: dict[str, Any] = {"status": "skipped", "reason": "--no-validate"}
    else:
        validation = base._validate(out_dir)
    base._validate_tables(out_dir)

    manifest = {
        "profile": "neonatal_his_synthetic_manifest_v1",
        "synthetic": True,
        "generation_mode": "his_style_weighted_ontology",
        "seed": args.seed,
        "case_count": len(cases),
        "patient_count": len({case["snapshot"]["patient_ref"] for case in cases}),
        "ontology_weights": dict(ONTOLOGY_WEIGHTS),
        "generator": "tools/generate_neonatal_his30.py",
        "source_branches": {
            "his": "origin/his@0e6a2d1 (HIS subtree)",
            "hisdata": "origin/hisdata@e85f469 (HIS data worktree)",
        },
        "why_not_java": (
            "origin/his 需要 JDK 21 + Maven + MySQL 本体库；本机为 JDK 11、无 Maven、"
            "3306 无监听，且 Java 生成器患者年龄固定 18-89 岁。本脚本移植其"
            "加权诊断选择、诊断-用药-检验-剂量池与时间链规则。"
        ),
        "files": {
            "cases": {"name": cases_path.name, "sha256": base._sha256(cases_path)},
            "snapshots": {"name": snapshots_path.name, "sha256": base._sha256(snapshots_path)},
            "requests": {"name": requests_path.name, "sha256": base._sha256(requests_path)},
            "case_index": {"name": index_path.name, "sha256": base._sha256(index_path)},
            "tables": sorted(name for name in tables),
        },
        "validation": validation,
        "boundaries": [
            "合成数据，不是真实患者，不构成诊断或治疗建议。",
            "不是 Synthea/FHIR 产物，也不是 origin/his Java 生成器的直接运行产物。",
            "ICD-10/LOINC/ATC 码值仅用于演示，未逐条核对国标/官方版本。",
            "数量/单价/金额留空而不是填 0；空值表示未建模。",
        ],
    }
    base._write_json(out_dir / "manifest.json", manifest)

    print(json.dumps({
        "output": str(out_dir),
        "cases": len(cases),
        "patients": manifest["patient_count"],
        "tables": len(tables),
        "validation": validation,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
