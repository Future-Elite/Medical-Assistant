"""Build the v6.7.2 rule-derived dataset from hospital discharge rows.

The builder is deliberately deterministic (``random.Random(seed)``) and
admission-level (住院号): a reference appears in exactly one split.  The export
has no person-level master index, so this is not a claim of patient-level
isolation.  Real patient
text is written only to ``output_dir`` (must be outside the repository); the
returned aggregate dictionary contains counts only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import copy
import hashlib
import json
import random
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .gold_rules import (
    criterion_gold,
    derived_values_from_row,
    guideline_gold,
    load_criteria_fixture,
    patient_gold,
)
from .hospital_adapter import DischargeRow, row_to_snapshot
from .schema import (
    LABEL_SOURCE,
    PAIR_SCHEMA,
    PATIENT_SCHEMA,
    QUESTION_SCHEMA,
    SCHEMA_VERSION,
    TEST_SCHEMA,
    validate_counterfactual_pair,
    validate_patient_record,
    validate_synthetic_question,
    validate_test_case,
)
from .fact_qrels import build_fact_qrels
from .families import family_of


@dataclass(frozen=True)
class BuildConfig:
    seed: int = 42
    train_n: int = 1000
    dev_n: int = 200
    test_a: int = 100
    test_b: int = 100
    test_c: int = 100
    test_d: int = 100
    test_ga: int = 100
    pair_n: int = 500
    question_n: int = 500


TRAIN_QUOTAS = (
    ("vlbw_match", 150),
    ("preterm_match_nonvlbw", 250),
    ("pocus_match_only", 250),
    ("all_mismatch", 150),
    ("unknown", 200),
)
DEV_QUOTAS = (
    ("vlbw_match", 50),
    ("preterm_match_nonvlbw", 50),
    ("pocus_match_only", 50),
    ("unknown", 50),
)


def build_patient_records(
    rows: Sequence[DischargeRow], *, salt: bytes, fixture: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    fixture = fixture or load_criteria_fixture()
    records: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for row in rows:
        snapshot = row_to_snapshot(row, salt=salt)
        ref = snapshot["patient_ref"]
        if ref in seen_refs:
            raise ValueError("patient reference collision after HMAC")
        seen_refs.add(ref)
        derived = derived_values_from_row(row)
        gold = patient_gold(fixture, derived)
        record_paths = {
            "derived_birth.birth_weight_g": f"{snapshot['extra']['row_ref']}:derived_birth",
            "derived_birth.ga_days": f"{snapshot['extra']['row_ref']}:derived_birth",
            "derived_respiratory_support.mode": f"{snapshot['extra']['row_ref']}:derived_respiratory_support",
        }
        for guideline in gold["guidelines"]:
            for criterion in guideline["criteria"]:
                criterion.setdefault("record_path", record_paths.get(criterion["field"]))
        flags = {
            "ga_column_missing": bool(derived.get("ga_column_missing")),
            "ga_text_present": bool(derived.get("ga_text_present")),
            "ga_text_conflict": bool(derived.get("ga_text_conflict")),
            "birth_weight_missing": derived.get("birth_weight_g") is None,
            "jaundice": bool(derived.get("jaundice_mentioned")),
            "uncertain_diagnosis": bool((derived.get("uncertain_diagnosis") or {}).get("uncertain")),
            "respiratory_unclassified": derived.get("respiratory_mapping_status") == "unclassified_support",
            "time_unparseable": snapshot["as_of"] == "1970-01-01",
        }
        pools = _classify_pools(gold, flags, derived)
        record = {
            "schema_version": PATIENT_SCHEMA,
            "dataset_version": SCHEMA_VERSION,
            "label_source": LABEL_SOURCE,
            "patient_ref": ref,
            "row_hash": row.row_hash,
            "row_index": row.row_index,
            "as_of": snapshot["as_of"],
            "snapshot": snapshot,
            "derived": derived,
            "gold": gold,
            "flags": flags,
            "pools": pools,
            "source_text": {
                "sex": row.sex,
                "age_text": row.age_text,
                "admission_diagnosis": row.admission_diagnosis,
                "discharge_diagnosis": row.discharge_diagnosis,
                "presenting_symptoms": row.presenting_symptoms,
                "laboratory_findings": row.laboratory_findings,
                "special_examinations": row.special_examinations,
                "treatment_course": row.treatment_course,
                "complications": row.complications,
                "discharge_condition": row.discharge_condition,
                "discharge_orders": row.discharge_orders,
                "discharge_guidance": row.discharge_guidance,
                "outcome": row.outcome,
                "gestational_age_raw": row.gestational_age_raw,
            },
            "split": "unused",
            "case_type": None,
        }
        validate_patient_record(record)
        records.append(record)
    return records


def assign_splits(
    records: Sequence[dict[str, Any]], config: BuildConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Assign train/dev/test and build test cases with patient-level isolation."""
    rng = random.Random(config.seed)
    by_ref = {record["patient_ref"]: record for record in records}
    used: set[str] = set()
    test_cases: list[dict[str, Any]] = []

    ga_pool = [record for record in records if record["pools"]["ga_candidate"]]
    ga_cases = _pick(ga_pool, config.test_ga, rng, used,
                     key=lambda record: record["derived"].get("ga_weeks"))
    for record in ga_cases:
        test_cases.append(_make_test_case(record, "GA", rng, fixture=None))
        record["split"] = "test"
        record["case_type"] = "GA"

    d_pool = [record for record in records if record["pools"]["d_candidate"]]
    d_empty = [record for record in d_pool if record["gold"]["coverage_empty"]]
    d_nonempty = [record for record in d_pool if not record["gold"]["coverage_empty"]]
    d_cases: list[dict[str, Any]] = []
    d_cases.extend(_pick(d_empty, config.test_d // 2, rng, used, key=lambda record: record["derived"].get("ga_weeks")))
    d_cases.extend(_pick(d_nonempty, config.test_d - len(d_cases), rng, used,
                         key=lambda record: tuple(record["gold"]["applicable"])))
    if len(d_cases) < config.test_d:
        d_cases.extend(_pick(d_pool, config.test_d - len(d_cases), rng, used,
                             key=lambda record: record["derived"].get("ga_weeks")))
    for record in d_cases:
        test_cases.append(_make_test_case(record, "D", rng, fixture=None))
        record["split"] = "test"
        record["case_type"] = "D"

    a_pool = [record for record in records if record["pools"]["a_candidate"]]
    a_cases = _pick(a_pool, config.test_a, rng, used, key=lambda record: _target_for_a(record, rng))
    for record in a_cases:
        test_cases.append(_make_test_case(record, "A", rng, fixture=None))
        record["split"] = "test"
        record["case_type"] = "A"

    b_pool = [record for record in records if record["pools"]["b_candidate"]]
    b_cases = _pick(b_pool, config.test_b, rng, used, key=lambda record: _target_for_b(record, rng))
    for record in b_cases:
        test_cases.append(_make_test_case(record, "B", rng, fixture=None))
        record["split"] = "test"
        record["case_type"] = "B"

    c_pool = [record for record in records if record["pools"]["c_candidate"]]
    c_cases = _pick(c_pool, config.test_c, rng, used, key=lambda record: _target_for_c(record, rng))
    for record in c_cases:
        test_cases.append(_make_test_case(record, "C", rng, fixture=None))
        record["split"] = "test"
        record["case_type"] = "C"

    dev = _pick_quotas(records, DEV_QUOTAS, config.dev_n, rng, used)
    for record in dev:
        record["split"] = "dev"
    train = _pick_quotas(records, TRAIN_QUOTAS, config.train_n, rng, used)
    for record in train:
        record["split"] = "train"
    return train, test_cases


def build_counterfactual_pairs(
    train_records: Sequence[dict[str, Any]], fixture: Mapping[str, Any],
    *, seed: int, count: int = 500,
) -> list[dict[str, Any]]:
    rng = random.Random(seed + 1)
    guidelines = {item["document_id"]: item for item in fixture["guidelines"]}
    candidates: dict[str, list[tuple[dict[str, Any], Mapping[str, Any], dict[str, Any], str, Any, Any]]] = {
        "vlbw": [], "preterm": [], "pocus": [],
    }
    for record in train_records:
        derived = record["derived"]
        bw = derived.get("birth_weight_g")
        if bw is not None:
            for target in (1400, 1600):
                after = copy.deepcopy(derived)
                after["birth_weight_g"] = target
                before_label = _guideline_status(guidelines["pdf-neo-feeding-vlbw-2015"], derived)
                after_label = _guideline_status(guidelines["pdf-neo-feeding-vlbw-2015"], after)
                if before_label != after_label:
                    candidates["vlbw"].append((
                        record, guidelines["pdf-neo-feeding-vlbw-2015"], after,
                        "birth_weight_g", bw, target,
                    ))
        ga_days = derived.get("ga_days")
        if ga_days is not None:
            for target in (238, 266):
                after = copy.deepcopy(derived)
                after["ga_days"] = target
                after["ga_days_range"] = [target, target]
                after["ga_precision"] = "day"
                for document_id, key in (
                    ("pdf-neo-human-milk-fortification-2019", "preterm"),
                    ("pdf-neo-parenteral-nutrition-2012", "preterm"),
                ):
                    before_label = _guideline_status(guidelines[document_id], derived)
                    after_label = _guideline_status(guidelines[document_id], after)
                    if before_label != after_label:
                        candidates[key].append((
                            record, guidelines[document_id], after, "ga_days", ga_days, target,
                        ))
        resp_status = derived.get("respiratory_status")
        if resp_status in ("observed_positive", "observed_negative"):
            for target in ("氧疗", "无"):
                after = copy.deepcopy(derived)
                after["respiratory_mode"] = target
                after["respiratory_status"] = "observed_negative" if target == "无" else "observed_positive"
                after["respiratory_mapping_status"] = "mapped_negative" if target == "无" else "mapped"
                before_label = _guideline_status(guidelines["pdf-neo-pocus-espnic-2020"], derived)
                after_label = _guideline_status(guidelines["pdf-neo-pocus-espnic-2020"], after)
                if before_label != after_label:
                    candidates["pocus"].append((
                        record, guidelines["pdf-neo-pocus-espnic-2020"], after,
                        "respiratory_mode", derived.get("respiratory_mode"), target,
                    ))

    quotas = {"vlbw": count // 4, "preterm": count // 2, "pocus": count // 4}
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for bucket, quota in quotas.items():
        pool = candidates[bucket]
        rng.shuffle(pool)
        picked = 0
        for record, guideline, after, field_name, before_value, after_value in pool:
            key = (record["patient_ref"], guideline["document_id"], field_name)
            if key in seen:
                continue
            seen.add(key)
            before_label = _guideline_status(guideline, record["derived"])
            after_label = _guideline_status(guideline, after)
            if before_label == after_label:
                continue
            pair = {
                "schema_version": PAIR_SCHEMA,
                "pair_id": f"cf-{len(pairs) + 1:04d}",
                "patient_ref": record["patient_ref"],
                "split": "train",
                "guideline_id": guideline["document_id"],
                "changed_field": field_name,
                "before": {field_name: before_value},
                "after": {field_name: after_value},
                "label_before": before_label,
                "label_after": after_label,
                "criterion_before": _criterion_snapshot(guideline, record["derived"]),
                "criterion_after": _criterion_snapshot(guideline, after),
                "note": "rule-derived one-field counterfactual; no clinical review",
            }
            validate_counterfactual_pair(pair)
            pairs.append(pair)
            picked += 1
            if picked >= quota:
                break
    if len(pairs) < count:
        # Fill from any remaining candidate.
        remaining = [item for bucket in candidates.values() for item in bucket]
        rng.shuffle(remaining)
        for record, guideline, after, field_name, before_value, after_value in remaining:
            if len(pairs) >= count:
                break
            key = (record["patient_ref"], guideline["document_id"], field_name)
            if key in seen:
                continue
            seen.add(key)
            before_label = _guideline_status(guideline, record["derived"])
            after_label = _guideline_status(guideline, after)
            if before_label == after_label:
                continue
            pair = {
                "schema_version": PAIR_SCHEMA,
                "pair_id": f"cf-{len(pairs) + 1:04d}",
                "patient_ref": record["patient_ref"],
                "split": "train",
                "guideline_id": guideline["document_id"],
                "changed_field": field_name,
                "before": {field_name: before_value},
                "after": {field_name: after_value},
                "label_before": before_label,
                "label_after": after_label,
                "criterion_before": _criterion_snapshot(guideline, record["derived"]),
                "criterion_after": _criterion_snapshot(guideline, after),
                "note": "rule-derived one-field counterfactual; no clinical review",
            }
            validate_counterfactual_pair(pair)
            pairs.append(pair)
    return pairs[:count]


def build_synthetic_questions(
    train_records: Sequence[dict[str, Any]], fixture: Mapping[str, Any],
    *, seed: int, count: int = 500,
) -> list[dict[str, Any]]:
    rng = random.Random(seed + 2)
    guidelines = {item["document_id"]: item for item in fixture["guidelines"]}
    buckets = {
        "vlbw": ("pdf-neo-feeding-vlbw-2015", _vlbw_question),
        "preterm": ("pdf-neo-human-milk-fortification-2019", _preterm_question),
        "pn": ("pdf-neo-parenteral-nutrition-2012", _preterm_question),
        "pocus": ("pdf-neo-pocus-espnic-2020", _pocus_question),
    }
    per_bucket = count // len(buckets)
    questions: list[dict[str, Any]] = []
    for bucket, (document_id, builder) in buckets.items():
        pool = list(train_records)
        rng.shuffle(pool)
        for record in pool[:per_bucket]:
            guideline = guidelines[document_id]
            question, facts_used = builder(record)
            status = _guideline_status(guideline, record["derived"])
            criterion = _criterion_snapshot(guideline, record["derived"])
            item = {
                "schema_version": QUESTION_SCHEMA,
                "question_id": f"q-{len(questions) + 1:04d}",
                "question": question,
                "guideline_id": document_id,
                "expected_status": status,
                "expected_reason_code": criterion.get("reason_code"),
                "facts_used": facts_used,
                "label_source": LABEL_SOURCE,
                "note": "generated from a real training record; label is rule-derived",
            }
            validate_synthetic_question(item)
            questions.append(item)
            if len(questions) >= count:
                return questions
    return questions[:count]


def build_outputs(
    records: Sequence[dict[str, Any]],
    test_cases: Sequence[dict[str, Any]],
    pairs: Sequence[dict[str, Any]],
    questions: Sequence[dict[str, Any]],
    *,
    output_dir: Path,
    manifest: Mapping[str, Any],
    templates: Mapping[str, Any],
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    train = [record for record in records if record["split"] == "train"]
    dev = [record for record in records if record["split"] == "dev"]
    sampled = [record for record in records if record["split"] in ("train", "dev", "test")]
    fact_qrels = [row for record in sampled for row in build_fact_qrels(record, templates)]

    _write_jsonl(output_dir / "patients_all.jsonl", records)
    _write_jsonl(output_dir / "rule_labels_train.jsonl", train)
    _write_jsonl(output_dir / "dev.jsonl", dev)
    _write_jsonl(output_dir / "test_cases.jsonl", test_cases)
    _write_jsonl(output_dir / "counterfactual_pairs.jsonl", pairs)
    _write_jsonl(output_dir / "synthetic_questions.jsonl", questions)
    _write_jsonl(output_dir / "patient_fact_qrels.jsonl", fact_qrels)
    _write_jsonl(output_dir / "split_ids.jsonl", [
        {"patient_ref": record["patient_ref"], "split": record["split"],
         "case_type": record["case_type"]}
        for record in records if record["split"] != "unused"
    ])
    aggregates = aggregate_stats(records, test_cases, pairs, questions, fact_qrels)
    manifest = dict(manifest)
    manifest["split_counts"] = aggregates["split_counts"]
    manifest["file_sha256"] = {
        path.name: _sha256_file(path)
        for path in sorted(output_dir.glob("*.jsonl"))
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "build_aggregates.json").write_text(
        json.dumps(aggregates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return aggregates


def aggregate_stats(
    records: Sequence[dict[str, Any]],
    test_cases: Sequence[dict[str, Any]],
    pairs: Sequence[dict[str, Any]],
    questions: Sequence[dict[str, Any]],
    fact_qrels: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    split_counts: dict[str, int] = {}
    case_type_counts: dict[str, int] = {}
    status_counts: dict[str, dict[str, int]] = {}
    reason_counts: dict[str, int] = {}
    missing_field_counts: dict[str, int] = {}
    coverage_sizes: dict[str, int] = {}
    fact_status_counts: dict[str, dict[str, int]] = {}
    for record in records:
        split_counts[record["split"]] = split_counts.get(record["split"], 0) + 1
        if record["case_type"]:
            case_type_counts[record["case_type"]] = case_type_counts.get(record["case_type"], 0) + 1
        for guideline in record["gold"]["guidelines"]:
            per = status_counts.setdefault(guideline["guideline_id"], {})
            per[guideline["status"]] = per.get(guideline["status"], 0) + 1
            for criterion in guideline["criteria"]:
                reason = criterion.get("reason_code") or "none"
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            for field_name in guideline["missing_fields"]:
                missing_field_counts[field_name] = missing_field_counts.get(field_name, 0) + 1
        size = str(len(record["gold"]["applicable"]))
        coverage_sizes[size] = coverage_sizes.get(size, 0) + 1
    for row in fact_qrels:
        per = fact_status_counts.setdefault(row["fact_type"], {})
        per[row["fact_status"]] = per.get(row["fact_status"], 0) + 1
    test_patients = {case["patient_ref"] for case in test_cases}
    train_patients = {record["patient_ref"] for record in records if record["split"] == "train"}
    dev_patients = {record["patient_ref"] for record in records if record["split"] == "dev"}
    return {
        "schema_version": SCHEMA_VERSION,
        "label_source": LABEL_SOURCE,
        "id_level": "admission_number",
        "split_counts": split_counts,
        "case_type_counts": case_type_counts,
        "guideline_status_counts": status_counts,
        "criterion_reason_counts": reason_counts,
        "missing_field_counts": missing_field_counts,
        "coverage_size_counts": coverage_sizes,
        "fact_status_counts": fact_status_counts,
        "test_case_count": len(test_cases),
        "counterfactual_pair_count": len(pairs),
        "synthetic_question_count": len(questions),
        "fact_qrel_count": len(fact_qrels),
        "overlap": {
            "train_test": len(train_patients & test_patients),
            "dev_test": len(dev_patients & test_patients),
            "train_dev": len(train_patients & dev_patients),
        },
        "flags": {
            "ga_column_missing_text_present": sum(
                1 for record in records if record["flags"]["ga_column_missing"] and record["flags"]["ga_text_present"]
            ),
            "ga_column_text_conflict": sum(1 for record in records if record["flags"]["ga_text_conflict"]),
            "jaundice_mentioned": sum(1 for record in records if record["flags"]["jaundice"]),
            "uncertain_diagnosis": sum(1 for record in records if record["flags"]["uncertain_diagnosis"]),
            "respiratory_unclassified": sum(1 for record in records if record["flags"]["respiratory_unclassified"]),
            "time_unparseable": sum(1 for record in records if record["flags"].get("time_unparseable")),
        },
    }


def _classify_pools(
    gold: Mapping[str, Any], flags: Mapping[str, Any], derived: Mapping[str, Any],
) -> dict[str, bool]:
    if flags.get("time_unparseable"):
        return {"a_candidate": False, "b_candidate": False, "c_candidate": False,
                "d_candidate": False, "ga_candidate": False, "has_coverage": False}
    mismatched = [item for item in gold["guidelines"] if item["status"] == "mismatch"]
    unknown = [item for item in gold["guidelines"] if item["status"] == "unknown"]
    has_coverage = bool(gold["applicable"])
    a_candidate = any(gold["coverage_by_mismatched"].get(item["guideline_id"]) for item in mismatched)
    b_candidate = bool(mismatched) and not has_coverage
    c_candidate = any(
        any(criterion.get("reason_code") != "unclassified_support" for criterion in item["criteria"])
        for item in unknown
    )
    ga_candidate = (
        flags.get("ga_column_missing") and flags.get("ga_text_present")
        and derived.get("ga_days") is not None
    )
    return {
        "a_candidate": a_candidate,
        "b_candidate": b_candidate,
        "c_candidate": c_candidate,
        "d_candidate": bool(flags.get("jaundice")),
        "ga_candidate": bool(ga_candidate),
        "has_coverage": has_coverage,
    }


def _pick(
    pool: Sequence[dict[str, Any]], n: int, rng: random.Random, used: set[str],
    *, key: Any,
) -> list[dict[str, Any]]:
    available = [record for record in pool if record["patient_ref"] not in used]
    if n <= 0 or not available:
        return []
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in available:
        group_key = str(key(record))
        groups.setdefault(group_key, []).append(record)
    for group in groups.values():
        rng.shuffle(group)
    ordered_keys = sorted(groups)
    rng.shuffle(ordered_keys)
    picked: list[dict[str, Any]] = []
    index = 0
    while len(picked) < n:
        progressed = False
        for group_key in ordered_keys:
            group = groups[group_key]
            if index < len(group):
                record = group[index]
                if record["patient_ref"] not in used:
                    used.add(record["patient_ref"])
                    picked.append(record)
                    progressed = True
                if len(picked) >= n:
                    break
        if not progressed:
            break
        index += 1
    return picked


def _pick_quotas(
    records: Sequence[dict[str, Any]], quotas: Sequence[tuple[str, int]], total: int,
    rng: random.Random, used: set[str],
) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    for name, quota in quotas:
        selector = _quota_selector(name)
        pool = [record for record in records if selector(record) and record["patient_ref"] not in used]
        remaining_quota = min(quota, total - len(picked))
        picked.extend(_pick(pool, remaining_quota, rng, used, key=lambda record: _diversity_key(record)))
    if len(picked) < total:
        remaining = [record for record in records if record["patient_ref"] not in used]
        picked.extend(_pick(remaining, total - len(picked), rng, used,
                            key=lambda record: _diversity_key(record)))
    return picked[:total]


def _quota_selector(name: str) -> Any:
    if name == "vlbw_match":
        return lambda record: (
            record["derived"].get("birth_weight_g") is not None
            and record["derived"]["birth_weight_g"] < 1500
        )
    if name == "preterm_match_nonvlbw":
        return lambda record: (
            record["derived"].get("ga_days") is not None
            and record["derived"]["ga_days"] < 259
            and (record["derived"].get("birth_weight_g") is None or record["derived"]["birth_weight_g"] >= 1500)
        )
    if name == "pocus_match_only":
        return lambda record: (
            record["derived"].get("respiratory_status") == "observed_positive"
            and record["derived"].get("ga_days") is not None
            and record["derived"]["ga_days"] >= 259
            and record["derived"].get("birth_weight_g") is not None
            and record["derived"]["birth_weight_g"] >= 1500
        )
    if name == "all_mismatch":
        return lambda record: record["pools"]["b_candidate"]
    if name == "unknown":
        return lambda record: record["pools"]["c_candidate"]
    return lambda record: True


def _diversity_key(record: Mapping[str, Any]) -> str:
    derived = record["derived"]
    return "|".join((
        str(derived.get("ga_weeks")),
        str(derived.get("ga_column_missing")),
        str(record["flags"].get("uncertain_diagnosis")),
        str(record["flags"].get("jaundice")),
    ))


def _target_for_a(record: Mapping[str, Any], rng: random.Random) -> str:
    eligible = [
        item["guideline_id"] for item in record["gold"]["guidelines"]
        if item["status"] == "mismatch"
        and record["gold"]["coverage_by_mismatched"].get(item["guideline_id"])
    ]
    return rng.choice(sorted(eligible))


def _target_for_b(record: Mapping[str, Any], rng: random.Random) -> str:
    eligible = [
        item["guideline_id"] for item in record["gold"]["guidelines"]
        if item["status"] == "mismatch"
    ]
    return rng.choice(sorted(eligible))


def _target_for_c(record: Mapping[str, Any], rng: random.Random) -> str:
    eligible = [
        item["guideline_id"] for item in record["gold"]["guidelines"]
        if item["status"] == "unknown"
        and any(criterion.get("reason_code") != "unclassified_support" for criterion in item["criteria"])
    ]
    return rng.choice(sorted(eligible))


def _make_test_case(
    record: dict[str, Any], case_type: str, rng: random.Random,
    *, fixture: Mapping[str, Any] | None,
) -> dict[str, Any]:
    gold = record["gold"]
    target = None
    target_guideline = None
    failed_criteria: list[str] = []
    missing_fields: list[str] = []
    guideline_status = None
    coverage = list(gold["applicable"])
    coverage_scope = "not_applicable"
    same_family: list[str] = []
    evidence_locator = None
    if case_type in ("A", "B", "C"):
        target = _target_for_a(record, rng) if case_type == "A" else (
            _target_for_b(record, rng) if case_type == "B" else _target_for_c(record, rng)
        )
        target_guideline = next(item for item in gold["guidelines"] if item["guideline_id"] == target)
        guideline_status = target_guideline["status"]
        failed_criteria = list(target_guideline["failed_criteria"])
        missing_fields = list(target_guideline["missing_fields"])
        coverage = list(gold["applicable"]) if case_type == "C" else list(
            gold["coverage_by_mismatched"].get(target, [])
        )
        same_family = [item for item in coverage if family_of(item) == family_of(target)]
        coverage_scope = "same_family" if same_family else ("cross_family_only" if coverage else "none")
        locators = [criterion.get("locator") for criterion in target_guideline["criteria"] if criterion.get("locator")]
        evidence_locator = locators[0] if locators else None
    expected_behavior = {
        "A": "find_coverage",
        "B": "report_no_coverage",
        "C": "request_missing_fact",
        "D": "report_topic_not_in_corpus",
        "GA": "extract_ga_from_text",
    }[case_type]
    snapshot = record["snapshot"]
    if case_type == "GA":
        snapshot = _mask_ga(snapshot)
    question = _case_question(case_type, record)
    case = {
        "schema_version": TEST_SCHEMA,
        "case_id": f"test-{case_type}-{record['patient_ref'].split(':')[-1]}",
        "case_type": case_type,
        "patient_ref": record["patient_ref"],
        "as_of": record["as_of"],
        "question": question,
        "snapshot": snapshot,
        "gold": {
            "expected_behavior": expected_behavior,
            "guideline_status": guideline_status,
            "guideline_id": target,
            "failed_criteria": failed_criteria,
            "missing_fields": missing_fields,
            "coverage": coverage,
            "coverage_scope": coverage_scope,
            "coverage_same_family": same_family,
            "all_guidelines": [
                {
                    "guideline_id": item["guideline_id"],
                    "status": item["status"],
                    "failed_criteria": item["failed_criteria"],
                    "missing_fields": item["missing_fields"],
                    "criteria": [
                        {
                            "criterion_id": criterion["criterion_id"],
                            "field": criterion["field"],
                            "status": criterion["status"],
                            "reason_code": criterion.get("reason_code"),
                        }
                        for criterion in item["criteria"]
                    ],
                }
                for item in gold["guidelines"]
            ],
            "topic": "hyperbilirubinemia" if case_type == "D" else None,
            "evidence_locator": evidence_locator,
            "ga_text_days": record["derived"].get("ga_days") if case_type == "GA" else None,
            "label_source": LABEL_SOURCE,
        },
        "flags": record["flags"],
    }
    validate_test_case(case)
    return case


def _mask_ga(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    masked = copy.deepcopy(snapshot)
    for fact in masked.get("facts", []):
        if fact.get("collection") == "derived_birth":
            fact["fields"]["ga_days"] = None
            fact["fields"]["ga_days_range"] = None
            fact["fields"]["ga_precision"] = None
            fact["fields"]["ga_text"] = None
            fact["fields"]["data_status"] = "not_recorded"
    return masked


def _case_question(case_type: str, record: Mapping[str, Any]) -> str:
    if case_type == "A":
        return "该患儿与一份本地指南的条件对不上；请判断是否有另一份本地指南的条件能覆盖该患儿。"
    if case_type == "B":
        return "该患儿与本地指南的条件对不上；请判断本地语料中是否有能覆盖该患儿的指南。"
    if case_type == "C":
        return "该患儿有一部分事实未记录，无法判断指南适用性；请指出缺哪些事实。"
    if case_type == "D":
        return "该患儿有黄疸或高胆红素记录；请判断本地指南库是否有对应的指南。"
    return "该患儿的孕周结构化字段未记录，但病历文本中可能写了胎龄；请从文本中抽取胎龄。"


def _guideline_status(guideline: Mapping[str, Any], derived: Mapping[str, Any]) -> str:
    return guideline_gold(guideline, derived)["status"]


def _criterion_snapshot(guideline: Mapping[str, Any], derived: Mapping[str, Any]) -> dict[str, Any]:
    return criterion_gold(guideline["criteria"][0], derived)


def _vlbw_question(record: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    value = record["derived"].get("birth_weight_g")
    if value is None:
        return "该患儿出生体重未记录，是否符合极低出生体重喂养指南（<1500 g）？", {"birth_weight_g": None}
    return (
        f"该患儿出生体重 {value} g，是否符合极低出生体重喂养指南（<1500 g）？",
        {"birth_weight_g": value},
    )


def _preterm_question(record: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    ga = record["derived"].get("ga_weeks")
    if ga is None:
        return "该患儿胎龄未记录，是否符合早产（<37 周）相关指南的适用条件？", {"ga_weeks": None}
    return (
        f"该患儿胎龄 {ga} 周，是否符合早产（<37 周）相关指南的适用条件？",
        {"ga_weeks": ga},
    )


def _pocus_question(record: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    mode = record["derived"].get("respiratory_mode")
    status = record["derived"].get("respiratory_status")
    if status == "observed_positive":
        return (
            f"该患儿接受过{mode}，是否符合 POCUS 指南的呼吸支持适用条件？",
            {"respiratory_mode": mode},
        )
    if status == "observed_negative":
        return "该患儿未接受呼吸支持，是否符合 POCUS 指南的呼吸支持适用条件？", {"respiratory_mode": "无"}
    return "该患儿呼吸支持情况未记录，是否符合 POCUS 指南的呼吸支持适用条件？", {"respiratory_mode": None}


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    *, source_path: Path, rows_total: int, unique_admissions: int,
    duplicate_rows: int, merged_admissions: int, config: BuildConfig,
    criteria_fixture_path: Path, salt_version: str = "v1",
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "id_level": "admission_number",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "file_name": source_path.name,
            "sha256": _sha256_file(source_path),
            "sheet": "Sheet1",
            "rows": rows_total,
            "unique_admissions": unique_admissions,
            "exact_duplicate_rows_removed": duplicate_rows,
            "merged_multi_row_admissions": merged_admissions,
        },
        "build": {
            "seed": config.seed,
            "salt_version": salt_version,
            "criteria_fixture": str(criteria_fixture_path.name),
            "criteria_fixture_sha256": _sha256_file(criteria_fixture_path),
            "code_sha256": _code_hash(),
            "label_source": LABEL_SOURCE,
            "clinical_review": False,
        },
        "config": {
            "train_n": config.train_n,
            "dev_n": config.dev_n,
            "test": {"A": config.test_a, "B": config.test_b, "C": config.test_c,
                     "D": config.test_d, "GA": config.test_ga},
            "counterfactual_pairs": config.pair_n,
            "synthetic_questions": config.question_n,
        },
        "caveats": [
            "labels are rule-derived silver, not clinical ground truth",
            "criteria fixture is synthetic_fixture copied from local guideline scope proxies",
            "real patient text is written only to this private directory",
            "test subsets are patient-level disjoint from train/dev",
        ],
    }


def _code_hash() -> str:
    digest = hashlib.sha256()
    package = Path(__file__).resolve().parent
    for path in sorted(package.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
