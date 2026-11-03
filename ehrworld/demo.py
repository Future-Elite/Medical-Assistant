"""Artificial fixtures for integration checks; no real patient information."""
from __future__ import annotations

from datetime import datetime, timedelta

from .io import write_jsonl


def synthetic_admissions(count: int = 12):
    for index in range(count):
        start = datetime(2020, 1, 1) + timedelta(days=index * 4)
        profile = {
            "Basic Information": "Synthetic adult, age 50, female. Allergy history unknown. Chief complaint: fatigue.",
            "History Information": "Artificial example; no real medical record. History of diabetes.",
            "Diagnosis Results": {
                "Primary Diagnosis": {"Content": "Synthetic hyperglycemia", "Reason": "Admission for metabolic monitoring", "code": f"SYN-{index % 3}"},
                "Secondary Diagnoses": [{"Content": "Synthetic electrolyte disturbance", "Reason": "Monitored during this admission"}],
            },
            "demographics": {"age": 50, "gender": "F"},
        }
        events = []
        for round_index, (hours, glucose, potassium) in enumerate([(0, 240 + index, 3.1), (8, 130 + index, 4.8), (24, 105 + index, 4.1)]):
            timestamp = (start + timedelta(hours=hours)).isoformat()
            for concept, name, value, unit, low, high in [
                ("lab:glucose", "Glucose", glucose, "mg/dL", 70, 110),
                ("lab:potassium", "Potassium", potassium, "mEq/L", 3.5, 5.1),
            ]:
                events.append(dict(event_id=f"{index}:{round_index}:{concept}", timestamp=timestamp, kind="inquiry",
                                   name=name, concept=concept, source="labevents", value_type="number", value=value,
                                   unit=unit, reference_low=low, reference_high=high))
            if round_index > 0:
                events.append(dict(event_id=f"{index}:{round_index}:culture", timestamp=timestamp, kind="inquiry",
                                   name="Synthetic culture result", concept="micro:culture", source="microbiologyevents",
                                   value_type="label", value="negative", unit=None))
        events.append(dict(event_id=f"{index}:medication", timestamp=(start + timedelta(hours=1)).isoformat(),
                           kind="intervention", name="Synthetic glucose-lowering treatment", concept="med:synthetic",
                           source="emar", value_type=None, value=None, unit=None,
                           parameters={"dose_given": "synthetic demonstration only", "route": "synthetic"}))
        yield {"subject_id": f"synthetic-{index // 2}", "episode_id": f"synthetic-admission-{index}",
               "admitted_at": start.isoformat(), "discharged_at": (start + timedelta(days=2)).isoformat(),
               "context": profile,
               "note": "Artificial record. A 50-year-old woman was admitted for hyperglycemia and electrolyte disturbance. No known family history. Allergy history unknown.",
               "events": events}


def save_synthetic(path="examples/synthetic_raw.jsonl", count=12):
    return write_jsonl(path, synthetic_admissions(count))
