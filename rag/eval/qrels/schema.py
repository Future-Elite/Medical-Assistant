"""Schemas and validators for the v6.7.2 rule-derived dataset.

The dataset is *not* clinical ground truth.  Every row carries
``label_source="rule"`` and the manifest records the rule/fixture versions.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "v672-dataset-v1"
PATIENT_SCHEMA = "v672-patient-v1"
QREL_SCHEMA = "v672-qrel-v1"
TEST_SCHEMA = "v672-test-case-v1"
PAIR_SCHEMA = "v672-counterfactual-v1"
QUESTION_SCHEMA = "v672-synthetic-question-v1"
LABEL_SOURCE = "rule"
CASE_TYPES = ("A", "B", "C", "D", "GA", "H")
FACT_TYPES = (
    "sex", "pna_days", "ga", "admission_diagnosis", "discharge_diagnosis",
    "outcome", "los_days", "max_tbil", "phototherapy", "antibiotic",
    "respiratory_support_level", "hearing_screen",
)
FACT_STATUSES = (
    "observed_positive", "observed_negative", "not_recorded", "uncertain", "pending",
)
CRITERION_STATUSES = ("match", "mismatch", "unknown")
GUIDELINE_STATUSES = ("possible_match", "mismatch", "unknown")


def validate_patient_record(record: Mapping[str, Any]) -> None:
    required = ("schema_version", "patient_ref", "as_of", "snapshot", "derived", "gold", "flags")
    _require(record, required, "patient record")
    if record["schema_version"] != PATIENT_SCHEMA:
        raise ValueError("unexpected patient schema version")
    if not str(record["patient_ref"]).startswith("hmac-sha256:"):
        raise ValueError("patient_ref must be a salted HMAC reference")
    if "住院号" in record or "admission_no" in record:
        raise ValueError("patient record must not carry the raw admission number")
    snapshot = record["snapshot"]
    if snapshot.get("patient_ref") != record["patient_ref"]:
        raise ValueError("snapshot/patient_ref mismatch")
    if not record["gold"].get("guidelines"):
        raise ValueError("patient record requires guideline gold")


def validate_test_case(case: Mapping[str, Any]) -> None:
    _require(case, ("schema_version", "case_id", "case_type", "patient_ref", "snapshot", "gold", "question"),
             "test case")
    if case["schema_version"] != TEST_SCHEMA:
        raise ValueError("unexpected test-case schema version")
    if case["case_type"] not in CASE_TYPES:
        raise ValueError(f"unknown case_type: {case['case_type']}")
    gold = case["gold"]
    _require(gold, ("expected_behavior", "guideline_status", "failed_criteria", "missing_fields", "coverage"),
             "test-case gold")
    if "住院号" in case:
        raise ValueError("test case must not carry the raw admission number")


def validate_counterfactual_pair(pair: Mapping[str, Any]) -> None:
    _require(pair, ("schema_version", "pair_id", "patient_ref", "guideline_id", "changed_field",
                    "before", "after", "label_before", "label_after"), "counterfactual pair")
    if pair["schema_version"] != PAIR_SCHEMA:
        raise ValueError("unexpected pair schema version")
    if pair["label_before"] == pair["label_after"]:
        raise ValueError("counterfactual pair must flip the label")


def validate_synthetic_question(item: Mapping[str, Any]) -> None:
    _require(item, ("schema_version", "question_id", "question", "guideline_id", "expected_status"),
             "synthetic question")
    if item["schema_version"] != QUESTION_SCHEMA:
        raise ValueError("unexpected question schema version")
    if "patient_ref" in item:
        raise ValueError("synthetic question must not carry a patient reference")


def _require(mapping: Mapping[str, Any], keys: Sequence[str], label: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ValueError(f"{label} missing keys: " + ", ".join(missing))
