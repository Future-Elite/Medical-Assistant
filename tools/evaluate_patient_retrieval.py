#!/usr/bin/env python3
"""Compare the current patient-fact retrieval with a structured-field prototype.

The default engine (``rag/patient_retrieval.py``) tokenizes a fact's
``searchable_text()``, which contains the collection, event type and field
*values* -- not the field *keys* and not Chinese aliases.  This script measures
what that costs on the 39-case EHR/HIS demo batch, and compares it with a
prototype that indexes:

* field keys (``gestationalWeeks``, ``birthWeightG``, ``admissionDiagnosis``, ...);
* collection aliases (``neonatal_record`` -> 出院病案, ``vital_sign`` -> 生命体征);
* Chinese aliases for common keys (胎龄, CRP, 血培养, 血糖, 呼吸支持, ...);
* value aliases (pending -> 待回报, not_recorded -> 未记录, positive -> 阳性).

The prototype lives only in this script.  It does not change the engine, the
API, or the default retrieval behaviour; the numbers are a research signal,
not a validated clinical retrieval result.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from rag.demo import load_demo_cases  # noqa: E402
from rag.his import PatientFact, PatientSnapshot, snapshot_from_dict  # noqa: E402
from rag.retrieval.patient_retrieval import _terms, retrieve_patient_facts  # noqa: E402

DEFAULT_CASES = REPO_ROOT / "data"
DEFAULT_OUTPUT = REPO_ROOT / "rag" / "validation" / "EHR_HIS新生儿RAG利用研究_2026-11-05.json"

#: Expected collections by demo theme.  These are broad "what a clinician would
#: want in the patient panel" sets, not a claim that every fact is relevant to
#: every question.  The current demo has only the two EHR scenarios.
EXPECTED_COLLECTIONS: dict[str, set[str]] = {
    "RDS": {"patient", "neonatal_record", "neonatal_summary", "admission", "diagnosis",
            "neonatal_diagnosis", "vital_sign", "lab_test", "order",
            "derived_birth", "derived_respiratory_support"},
    "肺炎": {"patient", "neonatal_record", "neonatal_summary", "admission", "diagnosis",
             "neonatal_diagnosis", "vital_sign", "lab_test", "order",
             "derived_birth", "derived_respiratory_support"},
}

COLLECTION_ALIASES: dict[str, str] = {
    "patient": "患者 性别 出生日期",
    "admission": "入院 住院 入院诊断",
    "diagnosis": "诊断 ICD",
    "neonatal_record": "出院病案 胎龄 孕周 出生体重 住院号 治疗结果",
    "neonatal_summary": "出院小结 症状 体征 检验 检查 诊疗过程 出院医嘱",
    "neonatal_diagnosis": "入院诊断 出院诊断",
    "vital_sign": "生命体征 体温 心率 呼吸 血氧",
    "lab_test": "检验 化验 CRP PCT WBC 血气 pH 血培养",
    "order": "医嘱 检查 治疗 呼吸支持 CPAP 抗菌",
    "derived_birth": "出生 胎龄 出生体重 分娩方式",
    "derived_respiratory_support": "呼吸支持 通气 CPAP FiO2 血氧",
}
FIELD_ALIASES: dict[str, str] = {
    "gender": "性别", "birthDate": "出生日期",
    "hospitalNo": "住院号", "gestationalWeeks": "孕周 胎龄",
    "gestationalDays": "胎龄 天", "ga_days": "胎龄 孕周",
    "ga_text": "胎龄 孕周", "birth_weight_g": "出生体重",
    "delivery_mode": "分娩方式", "multiple_birth": "多胎",
    "reportedAge": "年龄 日龄 生后", "mode": "通气 呼吸支持",
    "fio2": "FiO2 氧浓度", "cpap_cm_h2o": "CPAP", "spo2_percent": "血氧 氧合 SpO2",
    "admissionDiagnosis": "入院诊断", "dischargeDiagnosis": "出院诊断",
    "admissionTime": "入院时间", "temperature": "体温", "pulse": "心率",
    "respiration": "呼吸频率", "spo2Percent": "血氧 氧合 SpO2",
    "fio2": "FiO2 氧浓度", "itemName": "项目 检查 检验 医嘱",
    "resultValue": "结果 检验值", "diagnosisName": "诊断",
    "diagnosisText": "诊断", "respiratorySupport": "呼吸支持 通气 CPAP",
}
VALUE_ALIASES: dict[str, str] = {
    "positive": "阳性", "negative": "阴性", "pending": "待回报 未回报",
    "not_recorded": "未记录 未记载", "observed_positive": "已观察 阳性",
    "observed_negative": "已观察 阴性", "room air": "空气 未吸氧",
    "mother": "母亲 母体", "patient": "患儿",
}

#: Key-fact checks.  A check is counted only for cases that contain a matching
#: fact; the denominator therefore includes cases where the fact may not be
#: question-relevant.  The metric is coverage, not precision.
KEY_FACTS: dict[str, Any] = {
    "胎龄/出生": lambda fact: fact.collection == "birth",
    "诊断": lambda fact: fact.collection == "diagnoses",
    "母体事实": lambda fact: fact.collection == "maternal",
    "呼吸支持": lambda fact: fact.collection == "respiratory_support",
    "血气": lambda fact: fact.collection == "blood_gas",
    "影像": lambda fact: fact.collection == "imaging",
    "CRP": lambda fact: fact.collection == "labs" and "crp_mg_l" in fact.fields,
    "血培养": lambda fact: fact.collection == "labs" and "culture_status" in fact.fields,
    "胆红素": lambda fact: fact.collection == "labs" and "total_bilirubin_umol_l" in fact.fields,
    "血糖": lambda fact: fact.collection == "labs" and "glucose_mmol_l" in fact.fields,
    "光疗": lambda fact: fact.collection == "therapies",
    "喂养": lambda fact: fact.collection == "feeding",
    "危险因素": lambda fact: fact.collection == "risk_factors",
}


def structured_text(fact: PatientFact) -> str:
    parts = [fact.collection, fact.event_type, COLLECTION_ALIASES.get(fact.collection, "")]
    for key, value in fact.fields.items():
        parts.extend((
            key,
            FIELD_ALIASES.get(key, ""),
            str(value),
            VALUE_ALIASES.get(str(value).lower(), ""),
        ))
    return " ".join(part for part in parts if part)


def retrieve_structured(
    snapshot: PatientSnapshot,
    queries: Iterable[str],
    *,
    top_k: int = 20,
) -> list[tuple[PatientFact, float, tuple[str, ...]]]:
    terms = set().union(*(_terms(query) for query in queries if query))
    candidates: list[tuple[PatientFact, float, tuple[str, ...]]] = []
    for fact in snapshot.visible_facts():
        matched = tuple(sorted(terms & _terms(structured_text(fact))))
        if not matched:
            continue
        candidates.append((fact, len(matched) / max(1, len(terms)), matched))
    candidates.sort(key=lambda item: (-item[1], item[0].record_path))
    return candidates[:max(1, top_k)]


def _case_theme(case: Mapping[str, Any]) -> str:
    label = str(case.get("label") or "")
    return label.split(" · ")[1] if " · " in label else ""


def evaluate(cases: list[Mapping[str, Any]]) -> dict[str, Any]:
    aggregate = {
        "baseline": {"collection_recall": 0.0, "fact_recall": 0.0, "cases": 0},
        "structured": {"collection_recall": 0.0, "fact_recall": 0.0, "cases": 0},
    }
    per_theme: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    key_facts: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "baseline": 0, "structured": 0})

    for case in cases:
        theme = _case_theme(case)
        snapshot = snapshot_from_dict({"patient_snapshot": dict(case["snapshot"])})
        queries = (
            case["preset"]["question"],
            *case["preset"]["patient_terms"],
            *case["preset"]["english_terms"],
            *case["preset"]["mesh_terms"],
        )
        expected_collections = EXPECTED_COLLECTIONS.get(theme)
        if expected_collections is None:
            expected_collections = {fact.collection for fact in snapshot.visible_facts()}
        expected_paths = {
            fact.record_path
            for fact in snapshot.visible_facts()
            if fact.collection in expected_collections
        }

        baseline_hits, _ = retrieve_patient_facts(snapshot, queries, top_k=20)
        structured_hits = retrieve_structured(snapshot, queries, top_k=20)
        baseline_paths = {hit.record_path for hit in baseline_hits}
        structured_paths = {fact.record_path for fact, _, _ in structured_hits}
        baseline_collections = {hit.collection for hit in baseline_hits}
        structured_collections = {fact.collection for fact, _, _ in structured_hits}

        for name, collections, paths in (
            ("baseline", baseline_collections, baseline_paths),
            ("structured", structured_collections, structured_paths),
        ):
            collection_recall = len(expected_collections & collections) / max(1, len(expected_collections))
            fact_recall = len(expected_paths & paths) / max(1, len(expected_paths))
            aggregate[name]["collection_recall"] += collection_recall
            aggregate[name]["fact_recall"] += fact_recall
            aggregate[name]["cases"] += 1
            per_theme[theme][name].append(collection_recall)
            per_theme[theme][f"{name}_fact"].append(fact_recall)

        for name, predicate in KEY_FACTS.items():
            expected = {
                fact.record_path
                for fact in snapshot.visible_facts()
                if predicate(fact)
            }
            if not expected:
                continue
            key_facts[name]["n"] += 1
            key_facts[name]["baseline"] += int(bool(expected & baseline_paths))
            key_facts[name]["structured"] += int(bool(expected & structured_paths))

    summary = {
        "case_count": len(cases),
        "baseline": {
            "collection_recall": round(aggregate["baseline"]["collection_recall"] / max(1, aggregate["baseline"]["cases"]), 4),
            "fact_recall": round(aggregate["baseline"]["fact_recall"] / max(1, aggregate["baseline"]["cases"]), 4),
        },
        "structured": {
            "collection_recall": round(aggregate["structured"]["collection_recall"] / max(1, aggregate["structured"]["cases"]), 4),
            "fact_recall": round(aggregate["structured"]["fact_recall"] / max(1, aggregate["structured"]["cases"]), 4),
        },
        "key_facts": {
            name: {
                "cases": data["n"],
                "baseline_rate": round(data["baseline"] / max(1, data["n"]), 4),
                "structured_rate": round(data["structured"] / max(1, data["n"]), 4),
            }
            for name, data in key_facts.items()
        },
        "per_theme": {
            theme: {
                "cases": len(values.get("baseline", [])),
                "baseline_collection_recall": round(sum(values.get("baseline", [])) / max(1, len(values.get("baseline", []))), 4),
                "structured_collection_recall": round(sum(values.get("structured", [])) / max(1, len(values.get("structured", []))), 4),
                "baseline_fact_recall": round(sum(values.get("baseline_fact", [])) / max(1, len(values.get("baseline_fact", []))), 4),
                "structured_fact_recall": round(sum(values.get("structured_fact", [])) / max(1, len(values.get("structured_fact", []))), 4),
            }
            for theme, values in per_theme.items()
        },
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="demo directory with neonatal_cases.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON result path")
    args = parser.parse_args(argv)

    payload = load_demo_cases(args.cases)
    summary = evaluate(payload["cases"])
    summary["dataset"] = str(args.cases)
    summary["note"] = (
        "Prototype comparison only: the structured alias layer lives in this script, "
        "not in rag/patient_retrieval.py.  Collection/fact recall uses broad expected "
        "sets; key-fact rates are coverage, not clinical relevance or precision."
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"dataset: {args.cases} ({summary['case_count']} cases)")
    print(f"{'variant':<12} {'collection_recall':>18} {'fact_recall':>12}")
    for name in ("baseline", "structured"):
        row = summary[name]
        print(f"{name:<12} {row['collection_recall']:>18.3f} {row['fact_recall']:>12.3f}")
    print(f"{'key fact':<12} {'n':>2} {'baseline':>9} {'structured':>11}")
    for name, row in summary["key_facts"].items():
        print(f"{name:<12} {row['cases']:>2} {row['baseline_rate']:>9.2f} {row['structured_rate']:>11.2f}")
    print(f"result: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
