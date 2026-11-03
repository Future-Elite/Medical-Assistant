"""Streaming MIMIC-IV CSV/CSV.GZ adapter with project-local SQLite staging.

Input tables are read-only. Prescription/order/billing tables are deliberately
not interpreted as completed interventions. See docs/REPRODUCTION.md for policies.
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import sqlite3
import uuid
from collections import Counter
from pathlib import Path

from .data import parse_time
from .io import output_path


def finite_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def table_path(root: Path, module: str, table: str) -> Path | None:
    for folder in (root / module, root):
        for suffix in (".csv.gz", ".csv"):
            candidate = folder / (table + suffix)
            if candidate.is_file():
                return candidate
    return None


def rows(path: Path | None):
    if path is None:
        return
    opener = gzip.open if path.name.endswith(".gz") else open
    # Discharge summaries can exceed csv's platform default field limit.
    csv.field_size_limit(16 * 1024 * 1024)
    with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


class MIMICAdapter:
    def __init__(self, root: str | Path, *, notes_path: str | Path | None = None,
                 staging_dir: str | Path = "data/staging", include_icu: bool = False,
                 allow_date_only: bool = False, timestamp_policy: str = "chart",
                 max_admissions: int | None = None):
        self.root = Path(root)
        self.notes_path = Path(notes_path) if notes_path else table_path(self.root, "note", "discharge")
        self.include_icu = include_icu
        self.allow_date_only = allow_date_only
        if timestamp_policy not in ("chart", "available"):
            raise ValueError("timestamp_policy must be chart or available")
        self.timestamp_policy = timestamp_policy
        self.max_admissions = max_admissions
        self.db_path = output_path(Path(staging_dir) / f"mimic-{uuid.uuid4().hex}.sqlite")
        self.report = Counter()

    def __iter__(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript('''
                CREATE TABLE admissions (hadm TEXT PRIMARY KEY, subject TEXT, admitted TEXT,
                  discharged TEXT, note TEXT DEFAULT '', demographics TEXT DEFAULT '{}');
                CREATE TABLE details (emar_id TEXT, payload TEXT);
                CREATE INDEX details_emar ON details(emar_id);
                CREATE TABLE events (hadm TEXT, payload TEXT);
                CREATE INDEX events_hadm ON events(hadm);
            ''')
            admissions_file = table_path(self.root, "hosp", "admissions")
            if admissions_file is None:
                raise ValueError("Missing admissions.csv[.gz] in MIMIC root or hosp/")
            for row in rows(admissions_file):
                if self.max_admissions is not None and self.report["admissions"] >= self.max_admissions:
                    break
                try:
                    admitted, discharged = parse_time(row["admittime"]), parse_time(row["dischtime"])
                    if discharged < admitted:
                        raise ValueError("negative stay")
                    db.execute("INSERT INTO admissions(hadm,subject,admitted,discharged) VALUES (?,?,?,?)",
                               (row["hadm_id"], row["subject_id"], admitted.isoformat(), discharged.isoformat()))
                    self.report["admissions"] += 1
                except (KeyError, ValueError):
                    self.report["invalid_admission"] += 1
            admitted_rows = db.execute("SELECT hadm,subject,admitted,discharged FROM admissions").fetchall()
            stays = {h: (s, a, d) for h, s, a, d in admitted_rows}
            by_subject = {}
            for h, s, a, d in admitted_rows:
                by_subject.setdefault(s, []).append((h, a, d))
            for row in rows(table_path(self.root, "hosp", "patients")):
                if row["subject_id"] not in by_subject:
                    continue
                for hadm, admitted, _ in by_subject[row["subject_id"]]:
                    anchor_age = finite_number(row.get("anchor_age"))
                    anchor_year = finite_number(row.get("anchor_year"))
                    age = anchor_age + parse_time(admitted).year - anchor_year if anchor_age is not None and anchor_year is not None else None
                    db.execute("UPDATE admissions SET demographics=? WHERE hadm=?",
                               (json.dumps({"age": age, "gender": row.get("gender", "Unknown")}), hadm))
            if self.notes_path is None or not self.notes_path.is_file():
                raise ValueError("Discharge notes not found; supply --notes-path to discharge.csv[.gz]")
            for row in rows(self.notes_path):
                hadm = row.get("hadm_id")
                if hadm in stays and row.get("text", "").strip():
                    # The last nonempty record in source order wins; audit this for duplicate notes.
                    db.execute("UPDATE admissions SET note=? WHERE hadm=?", (row["text"], hadm))
                    self.report["notes_loaded"] += 1
            for row in rows(table_path(self.root, "hosp", "emar_detail")):
                emar_id = row.get("emar_id", "")
                if emar_id and row.get("subject_id") in by_subject:
                    fields = ("parent_field_ordinal", "dose_given", "dose_given_unit", "route", "infusion_rate",
                              "infusion_rate_unit", "product_description", "complete_dose_not_given")
                    details = {k: row[k] for k in fields if row.get(k)}
                    db.execute("INSERT INTO details VALUES (?,?)", (emar_id, json.dumps(details)))
            lab_dict = {r["itemid"]: r for r in rows(table_path(self.root, "hosp", "d_labitems"))}
            icu_dict = {r["itemid"]: r for r in rows(table_path(self.root, "icu", "d_items"))} if self.include_icu else {}
            batch = []

            def add(row, event):
                hadm = row.get("hadm_id", "")
                if hadm not in stays:
                    self.report["unmatched_admission_event"] += 1
                    return
                if row.get("subject_id") and row["subject_id"] != stays[hadm][0]:
                    self.report["subject_mismatch"] += 1
                    return
                if event is None:
                    return
                try:
                    timestamp = parse_time(event["timestamp"]).isoformat()
                except (ValueError, TypeError):
                    self.report["invalid_timestamp"] += 1
                    return
                if not stays[hadm][1] <= timestamp <= stays[hadm][2]:
                    self.report["outside_stay"] += 1
                    return
                event["timestamp"] = timestamp
                batch.append((hadm, json.dumps(event)))
                self.report[f"loaded:{event['source']}"] += 1
                if len(batch) >= 2000:
                    db.executemany("INSERT INTO events VALUES (?,?)", batch)
                    batch.clear()
                    db.commit()

            for index, row in enumerate(rows(table_path(self.root, "hosp", "labevents"))):
                item = lab_dict.get(row.get("itemid"), {})
                label = item.get("label") or row.get("itemid", "Unknown lab")
                add(row, self.observation(row, "labevents", row.get("labevent_id", str(index)),
                                          label, "lab:" + row.get("itemid", "")))
            for index, row in enumerate(rows(table_path(self.root, "hosp", "microbiologyevents"))):
                timestamp, precision = self.timestamp(row)
                if not timestamp:
                    continue
                test = row.get("test_name") or row.get("spec_type_desc") or "Microbiology"
                if row.get("ab_name"):
                    label = row.get("interpretation", "").strip()
                    name = f"{test} / {row.get('org_name', '')} / {row['ab_name']} susceptibility"
                    concept = f"micro:{row.get('spec_itemid')}:{row.get('test_itemid')}:{row.get('org_itemid')}:{row.get('ab_itemid')}"
                else:
                    label = row.get("org_name") or row.get("comments", "")
                    name = f"{row.get('spec_type_desc', '')} / {test}"
                    concept = f"micro:{row.get('spec_itemid')}:{row.get('test_itemid')}"
                if not label.strip() or any(k in label.casefold() for k in ("cancelled", "canceled", "not performed")):
                    self.report["incomplete_microbiology"] += 1
                    continue
                add(row, dict(event_id="micro:" + row.get("microevent_id", str(index)), timestamp=timestamp,
                              kind="inquiry", name=name, concept=concept, source="microbiologyevents",
                              value_type="label", value=label, unit=None, parameters={"timestamp_precision": precision}))
            for index, row in enumerate(rows(table_path(self.root, "hosp", "emar"))):
                if row.get("event_txt", "").strip().casefold() not in {"administered", "applied", "confirmed"}:
                    self.report["not_administered"] += 1
                    continue
                if not row.get("charttime") or not row.get("medication"):
                    self.report["incomplete_emar"] += 1
                    continue
                details = [json.loads(r[0]) for r in db.execute("SELECT payload FROM details WHERE emar_id=?", (row.get("emar_id"),))]
                some_dose_given = any(d.get("dose_given") and (finite_number(d["dose_given"]) is None
                                       or finite_number(d["dose_given"]) > 0) for d in details)
                if details and not some_dose_given and all(d.get("complete_dose_not_given", "").casefold() in {"yes", "y", "1"} for d in details):
                    self.report["dose_not_given"] += 1
                    continue
                add(row, dict(event_id="emar:" + row.get("emar_id", str(index)), timestamp=row["charttime"],
                              kind="intervention", source="emar", concept="med:" + row["medication"].casefold(),
                              name=row["medication"], value_type=None, value=None, unit=None,
                              parameters={"administration_status": row["event_txt"], "dose_details": details}))
            if self.include_icu:
                for index, row in enumerate(rows(table_path(self.root, "icu", "chartevents"))):
                    label = icu_dict.get(row.get("itemid"), {}).get("label", row.get("itemid", "Unknown"))
                    add(row, self.observation(row, "chartevents", str(index), label, "chart:" + row.get("itemid", "")))
                for index, row in enumerate(rows(table_path(self.root, "icu", "procedureevents"))):
                    if row.get("statusdescription", "").casefold() in {"rewritten", "cancelled", "canceled"}:
                        self.report["nonexecuted_procedure"] += 1
                        continue
                    if not row.get("starttime"):
                        continue
                    label = icu_dict.get(row.get("itemid"), {}).get("label", row.get("itemid", "Unknown"))
                    # Actual end time/duration are future facts at starttime. Record them
                    # as a separate stop event, never leak them into the start action.
                    params = {"phase": "start", **{k: row[k] for k in ("location",) if row.get(k)}}
                    add(row, dict(event_id=f"procedure:{index}", timestamp=row["starttime"], kind="intervention",
                                  name=label, concept="procedure:" + row.get("itemid", ""), source="procedureevents",
                                  value_type=None, value=None, unit=None, parameters=params))
                    if row.get("endtime") and parse_time(row["endtime"]) > parse_time(row["starttime"]):
                        add(row, dict(event_id=f"procedure:{index}:stop", timestamp=row["endtime"], kind="intervention",
                                      name=label + " (stop)", concept="procedure:" + row.get("itemid", ""), source="procedureevents",
                                      value_type=None, value=None, unit=None, parameters={"phase": "stop"}))
            for index, row in enumerate(rows(table_path(self.root, "hosp", "omr"))):
                if not self.allow_date_only:
                    self.report["date_only_omr_skipped"] += 1
                    continue
                timestamp = row.get("chartdate")
                if not timestamp:
                    continue
                # A date without a time is usable only when its entire day maps to one stay.
                matches = [(h, a, d) for h, a, d in by_subject.get(row.get("subject_id"), [])
                           if a[:10] <= timestamp[:10] <= d[:10]]
                if len(matches) != 1:
                    self.report["ambiguous_omr"] += 1
                    continue
                hadm, admitted, _ = matches[0]
                timestamp = max(parse_time(timestamp), parse_time(admitted)).isoformat()
                name, value = row.get("result_name", ""), row.get("result_value", "")
                measurements = [(name, value, None)]
                if name.casefold() == "blood pressure" and "/" in value:
                    systolic, diastolic = value.split("/", 1)
                    measurements = [("Systolic blood pressure", systolic, "mmHg"), ("Diastolic blood pressure", diastolic, "mmHg")]
                for subindex, (label, result, unit) in enumerate(measurements):
                    number = finite_number(result)
                    if number is None:
                        continue
                    fake_row = {**row, "hadm_id": hadm}
                    add(fake_row, dict(event_id=f"omr:{index}:{subindex}", timestamp=timestamp, kind="inquiry",
                                       name=label, concept="omr:" + label.casefold(), source="omr", value_type="number",
                                       value=number, unit=unit, parameters={"timestamp_precision": "date"}))
            if batch:
                db.executemany("INSERT INTO events VALUES (?,?)", batch)
            db.commit()
            for hadm, subject, admitted, discharged, note, demographics in db.execute("SELECT * FROM admissions ORDER BY hadm"):
                if not note:
                    self.report["no_discharge_note"] += 1
                    continue
                yield {"subject_id": subject, "episode_id": hadm, "admitted_at": admitted,
                       "discharged_at": discharged, "note": note, "demographics": json.loads(demographics),
                       "events": [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE hadm=?", (hadm,))]}

    def timestamp(self, row):
        value = row.get("charttime")
        precision = "second"
        if self.timestamp_policy == "available":
            value = row.get("storetime") or value
        if not value and self.allow_date_only:
            value = row.get("storedate") if self.timestamp_policy == "available" else None
            value = value or row.get("chartdate")
            precision = "date"
        if not value:
            self.report["no_precise_timestamp"] += 1
        return value, precision

    def observation(self, row, source, event_id, label, concept):
        timestamp, precision = self.timestamp(row)
        if not timestamp:
            return None
        text = (row.get("value") or "").strip()
        # Inequality/censored values must not be silently converted to exact numbers.
        number = finite_number(row.get("valuenum")) if not text.startswith(("<", ">")) else None
        if number is None and not text:
            self.report["missing_observation"] += 1
            return None
        return dict(event_id=f"{source}:{event_id}", timestamp=timestamp, kind="inquiry", name=label,
                    concept=concept, source=source, value_type="number" if number is not None else "label",
                    value=number if number is not None else text, unit=row.get("valueuom") or None,
                    reference_low=finite_number(row.get("ref_range_lower")),
                    reference_high=finite_number(row.get("ref_range_upper")),
                    parameters={"timestamp_precision": precision})
