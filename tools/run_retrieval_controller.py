"""Run the trial retrieval controller over the 39 synthetic demo cases.

Offline only: uses the demo custom-source library (guideline metadata +
synthetic guideline/literature). No network, no PDF directory dependency.

Usage (from the repository root)::

    PYTHONPATH=. python3 tools/run_retrieval_controller.py
    PYTHONPATH=. python3 tools/run_retrieval_controller.py --output data/controller.json
"""
from __future__ import annotations

import argparse
import collections
import json
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from rag.retrieval.controller import ControllerPolicy, RetrievalController, replay_controller_result
from rag.custom_sources import CustomSourceLibrary
from rag.demo import load_demo_cases, load_demo_guidelines
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline


def build_pipeline() -> EvidenceRetrievalPipeline:
    library = CustomSourceLibrary()
    for source in load_demo_guidelines()["sources"]:
        library.register_payload(source)
    registry, _warnings = library.build_registry()
    return EvidenceRetrievalPipeline(registry)


def run(output: Path, policy: ControllerPolicy, run_date: str) -> dict:
    pipeline = build_pipeline()
    controller = RetrievalController(pipeline, policy=policy)
    rows = []
    started = time.time()
    for case in load_demo_cases()["cases"]:
        request = RetrievalRequest.from_dict({**case["preset"], "patient_snapshot": dict(case["snapshot"])})
        result = controller.run(request)
        rows.append({
            "case_id": case["case_id"],
            "label": case.get("label", ""),
            "clinical_intent": result["query_plan"]["clinical_intent"],
            "english_coverage": result["query_plan"]["english_coverage"],
            "stop_reason": result["stop"]["reason"],
            "rounds": result["final"]["rounds"],
            "roles": result["final"]["source_roles"],
            "evidence_count": result["final"]["evidence_count"],
            "coverage": result["final"]["coverage"],
            "budget_spent": result["final"]["budget_spent"],
            "patient_hits": result["final"]["patient_hits"],
            "warnings": result["final"]["warnings"],
            "round_trace": result["rounds"],
            "replay": result["replay"],
            "replay_view": replay_controller_result(result),
        })
    total = round(time.time() - started, 2)
    summary = {
        "n_cases": len(rows),
        "stop_reasons": dict(collections.Counter(row["stop_reason"] for row in rows)),
        "clinical_intents": dict(collections.Counter(row["clinical_intent"] for row in rows)),
        "roles_queried": dict(collections.Counter(role for row in rows for role in row["roles"])),
        "avg_rounds": round(sum(row["rounds"] for row in rows) / len(rows), 2) if rows else 0,
        "avg_evidence": round(sum(row["evidence_count"] for row in rows) / len(rows), 2) if rows else 0,
        "avg_coverage_ratio": round(sum(row["coverage"]["ratio"] for row in rows) / len(rows), 4) if rows else 0,
        "cases_with_zero_evidence": [row["case_id"] for row in rows if row["evidence_count"] == 0],
        "cases_stopped_without_sufficiency": [
            row["case_id"] for row in rows
            if row["stop_reason"] in {"sources_exhausted", "no_eligible_source", "no_positive_expected_gain", "max_rounds"}
            and (row["coverage"]["ratio"] < policy.min_coverage or row["evidence_count"] < policy.min_evidence)
        ],
        "total_elapsed_s": total,
    }
    artifact = {
        "experiment": "检索控制器 controller-v1-trial 试运行",
        "date": run_date,
        "corpus": "50 例 EHR 生成、HIS 对齐演示病例（25 肺炎 + 25 RDS）× 演示来源库（指南元数据/合成指南/合成文献）",
        "network": "未联网；离线检索",
        "controller_version": "controller-v1-trial",
        "policy": policy.to_dict(),
        "summary": summary,
        "rows": rows,
        "note": (
            "这是一次可运行性、决策可回看与停止条件实验，不是检索质量或临床充分性评估。"
            "coverage 是词面出现率（concept/英文词/MeSH 是否出现在标题或正文），不是语义覆盖或临床金标准。"
            "演示库只有指南注册表与 PubMed 两个逻辑来源，Europe PMC/授权 PDF 在 live 环境才可用；"
            "stop_reason=sources_exhausted 表示可用来源已查完仍未达到启发式充分条件，不是“没有证据”。"
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "rag/validation/检索控制器试运行_2026-10-28.json")
    parser.add_argument("--max-rounds", type=int, default=4)
    parser.add_argument("--max-cost", type=float, default=3.0)
    parser.add_argument("--min-evidence", type=int, default=3)
    parser.add_argument("--min-coverage", type=float, default=0.5)
    parser.add_argument("--date", default=date.today().isoformat(), help="实验日期（写入 JSON）")
    args = parser.parse_args()
    policy = ControllerPolicy(
        max_rounds=args.max_rounds, max_cost=args.max_cost,
        min_evidence=args.min_evidence, min_coverage=args.min_coverage,
    )
    artifact = run(args.output, policy, args.date)
    print(json.dumps({"summary": artifact["summary"], "output": str(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
