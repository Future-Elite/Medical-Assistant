"""规则路由 v0 实验性运行（稳定复现工具）。

50 例 EHR 生成、HIS 对齐演示病例 × {off, shadow, active} × {无映射库, 有映射库}，另加 5 个
规则覆盖探针。只读合成演示数据，不联网；输出 JSON 工件到 ``rag/validation/``。

用法（在仓库根目录下）::

    PYTHONPATH=. python3 tools/run_routing_shadow_experiment.py
    PYTHONPATH=. python3 tools/run_routing_shadow_experiment.py --output data/route.json
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.custom_sources import CustomSourceLibrary
from rag.demo import load_demo_cases, load_demo_guidelines
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline
from rag.quality.term_mapping_library import TermMappingLibrary

MAPPING_PATH = ROOT / "rag/contracts/examples/term_mapping_library_neonatal.json"
DEFAULT_OUTPUT = ROOT / "rag/validation" / f"规则路由shadow实验_{date.today().isoformat()}.json"


def build_registry():
    library = CustomSourceLibrary()
    for source in load_demo_guidelines()["sources"]:
        library.register_payload(source)
    registry, warnings = library.build_registry()
    return registry, warnings


def request_for(case):
    return RetrievalRequest.from_dict({**case["preset"], "patient_snapshot": dict(case["snapshot"])})


def ids(package):
    return [item.document.document_id for item in package.ranked_documents]


def run_variant(registry, cases, mapping_library):
    mapping = {"term_mapping_library": mapping_library} if mapping_library else {}
    off = EvidenceRetrievalPipeline(registry, routing_mode="off", **mapping)
    shadow = EvidenceRetrievalPipeline(registry, routing_mode="shadow", **mapping)
    active = EvidenceRetrievalPipeline(registry, routing_mode="active", **mapping)
    rows = []
    for case in cases:
        request = request_for(case)
        started = time.time()
        p_off = off.retrieve(request)
        p_shadow = shadow.retrieve(request)
        p_active = active.retrieve(request)
        elapsed = round(time.time() - started, 3)
        shadow_trace = p_shadow.trace["route_decision"]
        active_trace = p_active.trace["route_decision"]
        off_ids, shadow_ids, active_ids = ids(p_off), ids(p_shadow), ids(p_active)
        rows.append({
            "case_id": case["case_id"],
            "label": case.get("label", ""),
            "clinical_intent": shadow_trace["clinical_intent"],
            "english_coverage": shadow_trace["english_coverage"],
            "rule": shadow_trace["rule"],
            "selected_sources": shadow_trace["selected_sources"],
            "notes": shadow_trace["notes"],
            "off_ids": off_ids,
            "shadow_ids": shadow_ids,
            "active_ids": active_ids,
            "shadow_same_as_off": shadow_ids == off_ids,
            "active_same_as_off": active_ids == off_ids,
            "active_source_order": active_trace.get("source_order_after"),
            "elapsed_3_modes_s": elapsed,
        })
    summary = {
        "n_cases": len(rows),
        "rules": dict(collections.Counter(row["rule"] for row in rows)),
        "intents": dict(collections.Counter(row["clinical_intent"] for row in rows)),
        "english_coverage": dict(collections.Counter(str(row["english_coverage"]) for row in rows)),
        "pubmed_dropped_cases": sum(1 for row in rows if "no_english_drop_pubmed" in row["rule"]),
        "shadow_same_as_off": sum(1 for row in rows if row["shadow_same_as_off"]),
        "active_same_as_off": sum(1 for row in rows if row["active_same_as_off"]),
        "active_changed_case_ids": [row["case_id"] for row in rows if not row["active_same_as_off"]],
        "total_elapsed_s": round(sum(row["elapsed_3_modes_s"] for row in rows), 2),
    }
    return summary, rows


def decision_signature(rows):
    return {
        row["case_id"]: (row["rule"], tuple(row["selected_sources"]), row["clinical_intent"], row["english_coverage"])
        for row in rows
    }


PROBES = [
    ("no_english_no_library", "新生儿呼吸窘迫综合征如何处理？", None),
    ("no_english_with_library", "新生儿呼吸窘迫综合征如何处理？", "library"),
    ("evidence_update", "新生儿呼吸窘迫综合征的最新研究证据有哪些？", "library"),
    ("drug_safety_with_english", "新生儿使用 ibuprofen 的安全性和不良反应有哪些？", "library"),
    ("drug_safety_no_english", "新生儿使用布洛芬的安全性和不良反应有哪些？", "library"),
]


def run_probes(registry, mapping):
    """规则覆盖探针：少量人为构造的合成请求，专门触发各条规则（不代表真实分布）。"""
    rows = []
    for probe_id, question, library_mode in PROBES:
        library = mapping if library_mode == "library" else None
        kwargs = {"term_mapping_library": library} if library else {}
        off = EvidenceRetrievalPipeline(registry, routing_mode="off", **kwargs)
        shadow = EvidenceRetrievalPipeline(registry, routing_mode="shadow", **kwargs)
        active = EvidenceRetrievalPipeline(registry, routing_mode="active", **kwargs)
        request = RetrievalRequest.from_dict({"question": question, "top_k": 3})
        p_off, p_shadow, p_active = off.retrieve(request), shadow.retrieve(request), active.retrieve(request)
        trace = p_shadow.trace["route_decision"]
        active_trace = p_active.trace["route_decision"]
        rows.append({
            "probe_id": probe_id,
            "question": question,
            "mapping_library": bool(library),
            "clinical_intent": trace["clinical_intent"],
            "english_coverage": trace["english_coverage"],
            "rule": trace["rule"],
            "selected_sources": trace["selected_sources"],
            "notes": trace["notes"],
            "source_order_after": active_trace.get("source_order_after"),
            "off_ids": ids(p_off),
            "active_ids": ids(p_active),
            "active_same_as_off": ids(p_active) == ids(p_off),
            "shadow_same_as_off": ids(p_shadow) == ids(p_off),
            "off_kinds": [item.document.kind for item in p_off.ranked_documents],
            "active_kinds": [item.document.kind for item in p_active.ranked_documents],
            "off_connectors": len(p_off.trace.get("connector_trace") or ()),
            "active_connectors": len(p_active.trace.get("connector_trace") or ()),
            "off_candidates": p_off.trace.get("candidate_count"),
            "active_candidates": p_active.trace.get("candidate_count"),
            "off_warnings": len(p_off.warnings),
            "active_warnings": len(p_active.warnings),
        })
    return rows


def run(output: Path) -> dict:
    registry, warnings = build_registry()
    cases = load_demo_cases()["cases"]
    mapping = TermMappingLibrary.from_json_file(MAPPING_PATH)
    probes = run_probes(registry, mapping)

    started = time.time()
    base_summary, base_rows = run_variant(registry, cases, None)
    lib_summary, lib_rows = run_variant(registry, cases, mapping)
    total = round(time.time() - started, 2)

    b_sig, l_sig = decision_signature(base_rows), decision_signature(lib_rows)
    changed = [case_id for case_id in b_sig if b_sig[case_id] != l_sig[case_id]]
    changes = [
        {"case_id": case_id, "without_library": list(b_sig[case_id]), "with_library": list(l_sig[case_id])}
        for case_id in changed
    ]

    artifact = {
        "experiment": "规则路由 v0 shadow/active 实验性运行",
        "date": date.today().isoformat(),
        "tree": "v6.7.2",
        "corpus": "50 例 EHR 生成、HIS 对齐演示病例（25 肺炎 + 25 RDS）× 演示来源库（合成指南/文献 + 指南元数据）",
        "network": "未联网；离线检索",
        "variants": {
            "without_mapping_library": {"summary": base_summary, "rows": base_rows},
            "with_mapping_library": {"summary": lib_summary, "rows": lib_rows},
        },
        "rule_coverage_probes": probes,
        "mapping_library_effect": {
            "changed_decision_count": len(changed),
            "changes": changes,
        },
        "registry_warnings": list(warnings),
        "total_elapsed_s": total,
        "note": (
            "shadow 模式只写 trace.route_decision，不改变返回；active 模式按决策收窄/重排来源。"
            "本实验只验证路由的可运行性、确定性与覆盖面，不评估检索质量提升——"
            "质量结论需 S8 人工标注查询集，本实验不构成证据。"
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="输出 JSON 路径")
    args = parser.parse_args()
    artifact = run(args.output)
    base = artifact["variants"]["without_mapping_library"]["summary"]
    lib = artifact["variants"]["with_mapping_library"]["summary"]
    print(json.dumps({
        "without_mapping_library": base,
        "with_mapping_library": lib,
        "mapping_library_changed_decisions": artifact["mapping_library_effect"]["changed_decision_count"],
        "probes": [
            {key: row[key] for key in (
                "probe_id", "clinical_intent", "english_coverage", "rule", "selected_sources",
                "off_connectors", "active_connectors", "off_candidates", "active_candidates",
                "off_warnings", "active_warnings",
            )}
            for row in artifact["rule_coverage_probes"]
        ],
        "total_elapsed_s": artifact["total_elapsed_s"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
