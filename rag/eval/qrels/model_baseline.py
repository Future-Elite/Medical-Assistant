"""Minimal S4 baseline: TF-IDF + logistic regression for guideline status.

This is deliberately the smallest model that can answer the project question
"does a weak-supervision model add anything over the deterministic rules?".
It trains on the 1,000 rule-labelled admissions, tunes one hyper-parameter on
the 200 dev admissions, and evaluates once on the 500 test cases (GA excluded
because that subset is for text extraction, not status classification).

The module keeps data preparation in pure Python so unit tests can run without
scikit-learn; the model itself imports scikit-learn lazily.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

GUIDELINE_IDS = (
    "pdf-neo-feeding-vlbw-2015",
    "pdf-neo-human-milk-fortification-2019",
    "pdf-neo-parenteral-nutrition-2012",
    "pdf-neo-pocus-espnic-2020",
)
STATUSES = ("possible_match", "mismatch", "unknown")

STRUCTURED_FEATURES = (
    "ga_days", "ga_missing", "ga_week_precision", "ga_day_precision",
    "birth_weight_g", "birth_weight_missing",
    "pna_days", "pna_missing",
    "resp_positive", "resp_negative", "resp_unclassified", "resp_not_recorded",
    "jaundice", "uncertain_diagnosis",
    "max_tbil_umol", "tbil_missing",
    "phototherapy_positive", "phototherapy_negative", "phototherapy_not_recorded",
    "antibiotics_positive", "antibiotics_negative", "antibiotics_not_recorded",
    "hearing_positive", "hearing_negative", "hearing_not_recorded",
    "blood_culture_pending", "blood_culture_positive", "blood_culture_negative",
)


@dataclass(frozen=True)
class Sample:
    patient_ref: str
    guideline_id: str
    label: str
    text: str
    structured: tuple[float, ...]
    case_type: str | None = None


def build_samples_from_records(records: Iterable[Mapping[str, Any]]) -> list[Sample]:
    samples: list[Sample] = []
    for record in records:
        text = _record_text(record)
        structured = _record_structured(record)
        for guideline in record["gold"]["guidelines"]:
            samples.append(Sample(
                patient_ref=record["patient_ref"],
                guideline_id=guideline["guideline_id"],
                label=guideline["status"],
                text=text,
                structured=structured,
                case_type=None,
            ))
    return samples


def build_samples_from_cases(cases: Iterable[Mapping[str, Any]]) -> list[Sample]:
    samples: list[Sample] = []
    for case in cases:
        if case["case_type"] == "GA":
            continue
        text = _snapshot_text(case["snapshot"])
        structured = _snapshot_structured(case["snapshot"], case.get("flags") or {})
        for guideline in case["gold"]["all_guidelines"]:
            samples.append(Sample(
                patient_ref=case["patient_ref"],
                guideline_id=guideline["guideline_id"],
                label=guideline["status"],
                text=text,
                structured=structured,
                case_type=case["case_type"],
            ))
    return samples


def train_and_evaluate(
    train_samples: Sequence[Sample],
    dev_samples: Sequence[Sample],
    test_samples: Sequence[Sample],
    *,
    seed: int = 42,
    bootstrap: int = 1000,
) -> dict[str, Any]:
    import numpy as np
    from scipy.sparse import hstack, vstack
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support
    from sklearn.preprocessing import LabelEncoder, OneHotEncoder, StandardScaler

    vectorizer = TfidfVectorizer(
        analyzer="char", ngram_range=(1, 3), min_df=2, max_features=50000,
        sublinear_tf=True,
    )
    vectorizer.fit(list(dict.fromkeys(sample.text for sample in train_samples)))
    scaler = StandardScaler()
    scaler.fit([list(sample.structured) for sample in train_samples])
    encoder = OneHotEncoder(categories=[list(GUIDELINE_IDS)], sparse_output=False, handle_unknown="ignore")
    encoder.fit([[sample.guideline_id] for sample in train_samples])
    label_encoder = LabelEncoder()
    label_encoder.fit(list(STATUSES))

    def matrix(samples: Sequence[Sample]):
        text = vectorizer.transform([sample.text for sample in samples])
        structured = scaler.transform([list(sample.structured) for sample in samples])
        guideline = encoder.transform([[sample.guideline_id] for sample in samples])
        return hstack([text, np.asarray(structured), np.asarray(guideline)]).tocsr()

    def labels(samples: Sequence[Sample]):
        return label_encoder.transform([sample.label for sample in samples])

    x_train, y_train = matrix(train_samples), labels(train_samples)
    x_dev, y_dev = matrix(dev_samples), labels(dev_samples)
    x_test, y_test = matrix(test_samples), labels(test_samples)

    tuning: dict[str, float] = {}
    best_c = 1.0
    best_score = -1.0
    for c_value in (0.1, 1.0, 10.0):
        model = LogisticRegression(
            C=c_value, max_iter=2000, class_weight="balanced", random_state=seed,
        )
        model.fit(x_train, y_train)
        score = float(f1_score(y_dev, model.predict(x_dev), average="macro"))
        tuning[str(c_value)] = score
        if score > best_score:
            best_score, best_c = score, c_value

    model = LogisticRegression(
        C=best_c, max_iter=2000, class_weight="balanced", random_state=seed,
    )
    model.fit(vstack([x_train, x_dev]).tocsr(), np.concatenate([y_train, y_dev]))
    prediction = model.predict(x_test)
    probabilities = model.predict_proba(x_test)

    metrics = _metrics(y_test, prediction, label_encoder, precision_recall_fscore_support,
                       accuracy_score, f1_score, confusion_matrix)
    metrics["bootstrap_95ci"] = _bootstrap_ci(
        test_samples, y_test, prediction, seed=seed, iterations=bootstrap,
        accuracy_score=accuracy_score, f1_score=f1_score,
    )
    per_guideline = {}
    for guideline_id in GUIDELINE_IDS:
        mask = np.array([sample.guideline_id == guideline_id for sample in test_samples])
        if mask.any():
            per_guideline[guideline_id] = _metrics(
                y_test[mask], prediction[mask], label_encoder,
                precision_recall_fscore_support, accuracy_score, f1_score, confusion_matrix,
            )
    per_case_type = {}
    for case_type in ("A", "B", "C", "D"):
        mask = np.array([sample.case_type == case_type for sample in test_samples])
        if mask.any():
            per_case_type[case_type] = _metrics(
                y_test[mask], prediction[mask], label_encoder,
                precision_recall_fscore_support, accuracy_score, f1_score, confusion_matrix,
            )

    return {
        "model": "tfidf_char_1_3 + logistic_regression",
        "seed": seed,
        "sizes": {
            "train_samples": len(train_samples),
            "dev_samples": len(dev_samples),
            "test_samples": len(test_samples),
            "test_admissions": len({sample.patient_ref for sample in test_samples}),
        },
        "class_distribution": {
            split: _label_counts(samples) for split, samples in (
                ("train", train_samples), ("dev", dev_samples), ("test", test_samples),
            )
        },
        "chosen_C": best_c,
        "dev_macro_f1_by_C": tuning,
        "test": metrics,
        "per_guideline": per_guideline,
        "per_case_type": per_case_type,
        "predictions": [
            {
                "patient_ref": sample.patient_ref,
                "case_type": sample.case_type,
                "guideline_id": sample.guideline_id,
                "gold": sample.label,
                "predicted": str(label_encoder.inverse_transform([int(index)])[0]),
                "probabilities": {
                    str(label): round(float(probability), 6)
                    for label, probability in zip(label_encoder.classes_, probabilities[row])
                },
            }
            for row, (sample, index) in enumerate(zip(test_samples, prediction))
        ],
    }


def rule_baseline_metrics(
    test_cases: Sequence[Mapping[str, Any]], test_samples: Sequence[Sample],
) -> dict[str, Any]:
    """Run the deterministic engine on the same test cases for comparison."""
    from ...quality.applicability import assess_applicability
    from ...his import snapshot_from_dict
    from .gold_rules import load_criteria_fixture
    from .rubric import build_documents

    documents = build_documents(load_criteria_fixture())
    predicted: list[str] = []
    comparable: list[bool] = []
    for case in test_cases:
        if case["case_type"] == "GA":
            continue
        snapshot = snapshot_from_dict(case["snapshot"], default_source="hospital_export")
        for guideline in case["gold"]["all_guidelines"]:
            unclassified = any(
                criterion.get("reason_code") == "unclassified_support"
                for criterion in guideline.get("criteria", [])
            )
            assessment = assess_applicability(documents[guideline["guideline_id"]], snapshot, [])
            predicted.append(assessment.status)
            comparable.append(not unclassified)
    gold = [sample.label for sample in test_samples]
    if len(predicted) != len(gold):
        raise ValueError("rule prediction/test sample length mismatch")
    return {
        "all": _simple_metrics(gold, predicted),
        "comparable": _simple_metrics(
            [label for label, keep in zip(gold, comparable) if keep],
            [label for label, keep in zip(predicted, comparable) if keep],
        ),
    }


def _record_text(record: Mapping[str, Any]) -> str:
    source = record.get("source_text") or {}
    keys = (
        "admission_diagnosis", "discharge_diagnosis", "presenting_symptoms",
        "laboratory_findings", "special_examinations", "treatment_course",
        "complications", "discharge_condition", "discharge_orders", "discharge_guidance",
        "gestational_age_raw",
    )
    return " ".join(str(source.get(key) or "") for key in keys)


def _snapshot_text(snapshot: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for fact in snapshot.get("facts", []):
        fields = fact.get("fields") or {}
        for key in (
            "admissionDiagnosis", "dischargeDiagnosis", "presentingSymptoms",
            "laboratoryFindings", "specialExaminations", "treatmentCourse",
            "complications", "dischargeCondition", "dischargeOrders",
            "dischargeGuidance", "gestationalAgeRaw",
        ):
            value = fields.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
    return " ".join(parts)


def _record_structured(record: Mapping[str, Any]) -> tuple[float, ...]:
    derived = record.get("derived") or {}
    labs = derived.get("labs") or {}
    treatments = derived.get("treatments") or {}
    return _structured_vector(
        ga_days=derived.get("ga_days"),
        ga_precision=derived.get("ga_precision"),
        birth_weight_g=derived.get("birth_weight_g"),
        pna_days=derived.get("pna_days"),
        respiratory_status=derived.get("respiratory_status"),
        respiratory_mapping=derived.get("respiratory_mapping_status"),
        jaundice=bool(derived.get("jaundice_mentioned")),
        uncertain=bool((derived.get("uncertain_diagnosis") or {}).get("uncertain")),
        max_tbil_umol=labs.get("max_tbil_umol_l"),
        blood_culture=labs.get("blood_culture_status"),
        treatments=treatments,
    )


def _snapshot_structured(snapshot: Mapping[str, Any], flags: Mapping[str, Any]) -> tuple[float, ...]:
    facts = {fact.get("collection"): fact.get("fields") or {} for fact in snapshot.get("facts", [])}
    birth = facts.get("derived_birth") or {}
    resp = facts.get("derived_respiratory_support") or {}
    labs = facts.get("derived_labs") or {}
    treatment = facts.get("derived_treatment") or {}
    treatments = {
        "phototherapy": {"status": treatment.get("phototherapy_status")},
        "antibiotics": {"status": treatment.get("antibiotics_status")},
        "hearing_screen": {"status": treatment.get("hearing_screen_status")},
    }
    return _structured_vector(
        ga_days=birth.get("ga_days"),
        ga_precision=birth.get("ga_precision"),
        birth_weight_g=birth.get("birth_weight_g"),
        pna_days=birth.get("pna_days"),
        respiratory_status=resp.get("data_status"),
        respiratory_mapping=resp.get("mapping_status"),
        jaundice=bool(labs.get("jaundice_mentioned")),
        uncertain=bool(flags.get("uncertain_diagnosis")),
        max_tbil_umol=labs.get("max_tbil_umol_l"),
        blood_culture=labs.get("blood_culture_status"),
        treatments=treatments,
    )


def _structured_vector(
    *, ga_days: Any, ga_precision: Any, birth_weight_g: Any, pna_days: Any,
    respiratory_status: Any, respiratory_mapping: Any, jaundice: bool, uncertain: bool,
    max_tbil_umol: Any, blood_culture: Any, treatments: Mapping[str, Any],
) -> tuple[float, ...]:
    values = {
        "ga_days": _number(ga_days),
        "ga_missing": 0.0 if ga_days is not None else 1.0,
        "ga_week_precision": 1.0 if ga_precision == "week" else 0.0,
        "ga_day_precision": 1.0 if ga_precision == "day" else 0.0,
        "birth_weight_g": _number(birth_weight_g),
        "birth_weight_missing": 0.0 if birth_weight_g is not None else 1.0,
        "pna_days": _number(pna_days),
        "pna_missing": 0.0 if pna_days is not None else 1.0,
        "resp_positive": 1.0 if respiratory_status == "observed_positive" else 0.0,
        "resp_negative": 1.0 if respiratory_status == "observed_negative" else 0.0,
        "resp_unclassified": 1.0 if respiratory_mapping == "unclassified_support" else 0.0,
        "resp_not_recorded": 1.0 if respiratory_status in (None, "not_recorded") else 0.0,
        "jaundice": 1.0 if jaundice else 0.0,
        "uncertain_diagnosis": 1.0 if uncertain else 0.0,
        "max_tbil_umol": _number(max_tbil_umol),
        "tbil_missing": 0.0 if max_tbil_umol is not None else 1.0,
        "phototherapy_positive": _status(treatments, "phototherapy", "observed_positive"),
        "phototherapy_negative": _status(treatments, "phototherapy", "observed_negative"),
        "phototherapy_not_recorded": _status(treatments, "phototherapy", "not_recorded"),
        "antibiotics_positive": _status(treatments, "antibiotics", "observed_positive"),
        "antibiotics_negative": _status(treatments, "antibiotics", "observed_negative"),
        "antibiotics_not_recorded": _status(treatments, "antibiotics", "not_recorded"),
        "hearing_positive": _status(treatments, "hearing_screen", "observed_positive"),
        "hearing_negative": _status(treatments, "hearing_screen", "observed_negative"),
        "hearing_not_recorded": _status(treatments, "hearing_screen", "not_recorded"),
        "blood_culture_pending": 1.0 if blood_culture == "pending" else 0.0,
        "blood_culture_positive": 1.0 if blood_culture == "positive" else 0.0,
        "blood_culture_negative": 1.0 if blood_culture == "negative" else 0.0,
    }
    return tuple(values[name] for name in STRUCTURED_FEATURES)


def _number(value: Any) -> float:
    try:
        return float(value) if value is not None else -1.0
    except (TypeError, ValueError):
        return -1.0


def _status(treatments: Mapping[str, Any], name: str, expected: str) -> float:
    item = treatments.get(name) or {}
    return 1.0 if item.get("status") == expected else 0.0


def _label_counts(samples: Sequence[Sample]) -> dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for sample in samples:
        counts[sample.label] = counts.get(sample.label, 0) + 1
    return counts


def _metrics(y_true, y_pred, label_encoder, precision_recall_fscore_support,
             accuracy_score, f1_score, confusion_matrix) -> dict[str, Any]:
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=range(len(label_encoder.classes_)), zero_division=0,
    )
    return {
        "n": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro")),
        "per_class": {
            str(label): {
                "precision": float(precision[index]),
                "recall": float(recall[index]),
                "f1": float(f1[index]),
                "support": int(support[index]),
            }
            for index, label in enumerate(label_encoder.classes_)
        },
        "confusion_matrix": confusion_matrix(
            y_true, y_pred, labels=range(len(label_encoder.classes_)),
        ).tolist(),
    }


def _simple_metrics(gold: Sequence[str], predicted: Sequence[str]) -> dict[str, Any]:
    labels = list(STATUSES)
    correct = sum(1 for a, b in zip(gold, predicted) if a == b)
    per_class = {}
    for label in labels:
        tp = sum(1 for a, b in zip(gold, predicted) if a == label and b == label)
        fp = sum(1 for a, b in zip(gold, predicted) if a != label and b == label)
        fn = sum(1 for a, b in zip(gold, predicted) if a == label and b != label)
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": (2 * precision * recall / (precision + recall)) if precision and recall else None,
            "support": sum(1 for value in gold if value == label),
        }
    return {
        "n": len(gold),
        "correct": correct,
        "accuracy": correct / len(gold) if gold else None,
        "per_class": per_class,
    }


def _bootstrap_ci(test_samples, y_true, y_pred, *, seed, iterations, accuracy_score, f1_score):
    import numpy as np

    refs = [sample.patient_ref for sample in test_samples]
    groups: dict[str, list[int]] = {}
    for index, ref in enumerate(refs):
        groups.setdefault(ref, []).append(index)
    unique_refs = sorted(groups)
    rng = np.random.default_rng(seed)
    accuracy_values = []
    f1_values = []
    for _ in range(iterations):
        sampled = rng.choice(unique_refs, size=len(unique_refs), replace=True)
        indices = [index for ref in sampled for index in groups[ref]]
        accuracy_values.append(float(accuracy_score(y_true[indices], y_pred[indices])))
        f1_values.append(float(f1_score(y_true[indices], y_pred[indices], average="macro")))
    return {
        "iterations": iterations,
        "cluster": "admission_ref",
        "accuracy": {
            "lower": float(np.percentile(accuracy_values, 2.5)),
            "upper": float(np.percentile(accuracy_values, 97.5)),
        },
        "macro_f1": {
            "lower": float(np.percentile(f1_values, 2.5)),
            "upper": float(np.percentile(f1_values, 97.5)),
        },
    }
