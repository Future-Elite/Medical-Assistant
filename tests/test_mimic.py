from __future__ import annotations

import csv
import gzip
from pathlib import Path

from ehrworld.data import construct_episode
from ehrworld.mimic import MIMICAdapter


def csv_file(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for row in rows for k in row))
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, keys)
        writer.writeheader()
        writer.writerows(rows)


def fixture_tables(root):
    csv_file(root / "hosp/admissions.csv.gz", [dict(subject_id="1", hadm_id="10", admittime="2020-01-01 08:00:00", dischtime="2020-01-03 12:00:00")])
    csv_file(root / "hosp/patients.csv", [dict(subject_id="1", anchor_age="50", anchor_year="2020", gender="F")])
    csv_file(root / "note/discharge.csv.gz", [dict(subject_id="1", hadm_id="10", text="Artificial note")])
    csv_file(root / "hosp/d_labitems.csv", [dict(itemid="5", label="Glucose")])
    csv_file(root / "hosp/labevents.csv.gz", [
        dict(subject_id="1", hadm_id="10", labevent_id="1", itemid="5", charttime="2020-01-01 09:00:00", value="120", valuenum="120", valueuom="mg/dL", ref_range_lower="70", ref_range_upper="110"),
        dict(subject_id="1", hadm_id="10", labevent_id="2", itemid="5", charttime="2020-01-01 10:00:00", value="<10", valuenum="10", valueuom="mg/dL"),
        dict(subject_id="1", hadm_id="10", labevent_id="3", itemid="5", charttime="2020-02-01 09:00:00", value="120", valuenum="120", valueuom="mg/dL"),
    ])
    csv_file(root / "hosp/emar.csv", [
        dict(subject_id="1", hadm_id="10", emar_id="1-1", charttime="2020-01-01 10:00:00", medication="Synthetic medicine", event_txt="Administered"),
        dict(subject_id="1", hadm_id="10", emar_id="1-2", charttime="2020-01-01 10:00:00", medication="Synthetic medicine", event_txt="Not Given"),
    ])
    csv_file(root / "hosp/emar_detail.csv", [
        dict(subject_id="1", emar_id="1-1", parent_field_ordinal="", dose_given="2", dose_given_unit="mg", route="PO"),
        dict(subject_id="1", emar_id="1-1", parent_field_ordinal="1.1", dose_given="1", dose_given_unit="mg", route="PO"),
    ])
    csv_file(root / "hosp/microbiologyevents.csv", [
        dict(subject_id="1", hadm_id="10", microevent_id="1", charttime="2020-01-01 10:00:00", storetime="2020-01-02 10:00:00", test_itemid="8", test_name="Culture", spec_type_desc="Blood", comments="No growth"),
        dict(subject_id="1", hadm_id="10", microevent_id="2", chartdate="2020-01-02", test_name="Culture", comments="No growth"),
    ])
    csv_file(root / "icu/d_items.csv", [dict(itemid="50", label="Synthetic procedure")])
    csv_file(root / "icu/procedureevents.csv", [dict(subject_id="1", hadm_id="10", itemid="50", starttime="2020-01-01 11:00:00", endtime="2020-01-02 11:00:00", value="24", valueuom="hour", statusdescription="FinishedRunning")])


def test_mimic_gzip_mapping_completed_meds_dose_and_censored_labs(tmp_path, raw):
    fixture_tables(tmp_path)
    adapter = MIMICAdapter(tmp_path, staging_dir=tmp_path / "stage", include_icu=True)
    admissions = list(adapter)
    assert len(admissions) == 1
    result = admissions[0]
    result["context"] = raw["context"]
    episode, _ = construct_episode(result)
    events = [e for s in episode.steps for e in s.events]
    emar = [e for e in events if e.action.source == "emar"]
    assert len(emar) == 1
    assert len(emar[0].action.parameters["dose_details"]) == 2
    assert emar[0].value is None
    censored = next(e for e in events if e.action.action_id == "labevents:2")
    assert censored.value == "<10"
    assert censored.action.value_type == "label"
    procedure = [e for e in events if e.action.source == "procedureevents"]
    assert len(procedure) == 2
    assert "endtime" not in procedure[0].action.parameters
    assert "procedure_amount" not in procedure[0].action.parameters
    assert adapter.report["outside_stay"] == 1
    assert adapter.report["not_administered"] == 1


def test_result_availability_policy(tmp_path):
    fixture_tables(tmp_path)
    result = list(MIMICAdapter(tmp_path, staging_dir=tmp_path / "stage", timestamp_policy="available"))[0]
    culture = next(e for e in result["events"] if e["source"] == "microbiologyevents")
    assert culture["timestamp"] == "2020-01-02T10:00:00"
