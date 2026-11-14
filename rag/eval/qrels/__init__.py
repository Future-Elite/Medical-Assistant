"""v6.7.2 rule-derived dataset and patient-fact qrels.

Public entry points:

* :mod:`rag.eval.qrels.xlsx_reader` — standard-library xlsx reader
* :mod:`rag.eval.qrels.hospital_adapter` — row -> HIS-shaped snapshot
* :mod:`rag.eval.qrels.fact_rules` — deterministic fact extraction
* :mod:`rag.eval.qrels.gold_rules` — independent criterion/guideline gold
* :mod:`rag.eval.qrels.build_dataset` — splits, counterfactuals, questions
* :mod:`rag.eval.qrels.rubric` — R1-R8 structural checks

All patient text stays in the project's ``datasets/`` folder
(``CHINALLM_PRIVATE_DATA_DIR`` must point inside it).  Git-repository artifacts
are aggregate counts and schemas only.
"""
from .build_dataset import (
    BuildConfig,
    assign_splits,
    build_counterfactual_pairs,
    build_manifest,
    build_outputs,
    build_patient_records,
    build_synthetic_questions,
)
from .gold_rules import (
    criterion_gold,
    guideline_gold,
    load_criteria_fixture,
    patient_gold,
)
from .hospital_adapter import (
    DischargeRow,
    dedupe,
    patient_ref,
    row_to_snapshot,
    rows_from_sheet,
    select_patient_rows,
)
from .xlsx_reader import XlsxSheet, read_xlsx

__all__ = [
    "BuildConfig",
    "DischargeRow",
    "XlsxSheet",
    "assign_splits",
    "build_counterfactual_pairs",
    "build_manifest",
    "build_outputs",
    "build_patient_records",
    "build_synthetic_questions",
    "criterion_gold",
    "dedupe",
    "guideline_gold",
    "load_criteria_fixture",
    "patient_gold",
    "patient_ref",
    "read_xlsx",
    "row_to_snapshot",
    "rows_from_sheet",
    "select_patient_rows",
]
