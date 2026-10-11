"""Offline evaluation harness skeleton for the  fidelity experiments.

This module provides the *metric layer* and *arm definitions* of the paper's
experiment plan. It deliberately does not run any
experiment in this phase (user instruction: stop before experiment execution).
What it does provide, fully offline and unit-tested:

- **fidelity-weighted retrieval metrics** (research contribution G):
  ``entity_correct_recall`` counts a relevant document only when the query's
  entities (per the fidelity audit) are correctly represented in the
  conversion product for that route; ``wrong_entity_contamination`` measures
  the share of returned documents whose topic is disjoint from the query
  topic (document-level proxy, same caveat as the frozen-snapshot round).
- **arm definitions** for the 3×4 design plus the orthogonal controls, so the
  full run is a thin driver once annotation data exists.

Run (after experiments are authorised):

    python3 -m rag.eval.run_fidelity_experiments --plan   # 在仓库根目录运行

The ``--plan`` mode prints the arm matrix and data requirements without
executing retrieval — this is the only mode intended for the pre-experiment
phase.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Arm definitions (matching the paper's experiment protocol §3)
# ---------------------------------------------------------------------------

ROUTES = ("reviewed_mapping", "translate", "multilingual_encoding")
GATE_POLICIES = ("off", "warn", "downweight", "reject")

# arm_id -> (route, gate policy, purpose)
# v2 臂矩阵（15 臂；与论文实验协议 v2 §3 同步，2026-10-10 复核轮修订）。
ARMS: dict[str, dict[str, str]] = {
    # 主臂：路线 × 门控
    "A1": {"route": "reviewed_mapping", "gate": "off", "purpose": "上界基线（人工审核映射，无门控）"},
    "A2": {"route": "reviewed_mapping", "gate": "reject", "purpose": "门控在好输入上的代价（误拒率）——R2 修订：系统强制把 reviewed_mapping 的 reject 降级为 warn，故需 allow_gate_quality_upper_bound 开关才能真测代价；否则作为'质量上界分支门控无损'的系统属性报告"},
    "B1": {"route": "translate", "gate": "off", "purpose": "错误最密集的基线"},
    "B2": {"route": "translate", "gate": "warn", "purpose": "对照基线现状（只警示）"},
    "B3": {"route": "translate", "gate": "downweight", "purpose": "H3 主检验（降权，penalty=0.5）"},
    "B4": {"route": "translate", "gate": "reject", "purpose": "H3 主检验（拒入融合）"},
    "B5": {"route": "translate", "gate": "downweight", "penalty": "0.3", "purpose": "阈值敏感性（penalty=0.3）"},
    "B6": {"route": "translate", "gate": "downweight", "penalty": "0.7", "purpose": "阈值敏感性（penalty=0.7）"},
    "C1": {"route": "multilingual_encoding", "gate": "off", "purpose": "复现 E3（多语言编码错配，e5）"},
    "C2": {"route": "multilingual_encoding", "gate": "reject", "purpose": "该路线可门控性检验（e5）"},
    "C3": {"route": "multilingual_encoding", "gate": "off", "purpose": "编码器依赖检查（BGE-M3）"},
    "C4": {"route": "multilingual_encoding", "gate": "reject", "purpose": "编码器 × 门控交互（BGE-M3，探索性）"},
    # 正交与对照臂
    "D1": {"route": "translate", "gate": "off", "purpose": "反方对照：通用 MT-QE 过滤 vs 实体保真门控（同 MT 输出、同阈值网格；QE 代理不可用时降级声明）"},
    "D2": {"route": "multilingual_encoding", "gate": "off", "purpose": "“跳过转换”对照：无显式转换产物时门控无对象可审（no_explicit_conversion）"},
    "D3": {"route": "translate", "gate": "downweight", "purpose": "组合路线：映射 + MT 双启用，门控只处置 MT 分支（路线级隔离检验）"},
}

# H4 正交性对照（与文档侧验证组合；确定性占位实现 EAV 风格）。
ORTHOGONALITY_ARMS = {
    "E1": {"route": "translate", "gate": "off", "verifier": "doc_side", "purpose": "H4：文档侧验证（EAV 风格占位）单独"},
    "E2": {"route": "translate", "gate": "downweight", "verifier": "query+doc", "purpose": "H4：查询侧门控 + 文档侧验证组合"},
}

PILOT_ARMS = ("A1", "B1", "B2", "B3", "B4")

# 组件消融（在第 6 节以分析形式执行，不单独占臂；此处登记为分析变体）。
ABLATION_VARIANTS = {
    "no_numeric_check": "B3 配置去掉数值检查",
    "no_negation_check": "B3 配置去掉否定检查",
    "no_fabrication_check": "B3 配置去掉词表串换检查",
}

DATA_REQUIREMENTS = {
    "queries": "queries_v1.json（冻结；正式 80 条、先导 20 条子集；含 english_terms/mesh_terms/人工实体槽位）",
    "conversions": "conversions_<route>_<model>.json（逐条译文/映射产物；MT 与多语言编码各 2 个实现）",
    "labels": "fidelity_labels_v1.jsonl（双人标注 + 仲裁；κ 报告；约 700–1,200 条槽位判定）",
    "corpus": "v1：pubmed_snapshot_2026-10-09.json（495 摘要，先导）；v2：pubmed_snapshot_v2.json（目标 ≥1500 摘要，正式；抓后冻结）",
    "lexicon": "调用方提供的 zh<->en 医学词表 JSON（FidelityLexicon.from_json_file；许可由调用方负责）",
    "power": "power_simulation.py（功效论证：n=80–100，MDE dz≈0.35–0.40，BH 校正）",
}


# ---------------------------------------------------------------------------
# Metric layer (offline, deterministic)
# ---------------------------------------------------------------------------


def recall_at(relevance: set[str], ranked_ids: Sequence[str], k: int) -> float:
    if not relevance:
        return 0.0
    return len(set(ranked_ids[:k]) & relevance) / len(relevance)


def precision_at(relevance: set[str], ranked_ids: Sequence[str], k: int) -> float:
    top = list(ranked_ids[:k])
    if not top:
        return 0.0
    return len(set(top) & relevance) / len(top)


def mrr_at(relevance: set[str], ranked_ids: Sequence[str], k: int) -> float:
    for index, item in enumerate(ranked_ids[:k], 1):
        if item in relevance:
            return 1.0 / index
    return 0.0


def entity_correct_recall(
    relevance: set[str],
    ranked_ids: Sequence[str],
    k: int,
    *,
    route_entities_correct: bool | None,
) -> float | None:
    """Recall@k counting only documents whose query entities were preserved.

    ``route_entities_correct`` comes from the human annotation of the
    conversion product for the arm's route (None = unverifiable / unlabelled).
    When the route's entities are labelled correct, this equals the standard
    recall; when labelled wrong, returned documents never count as
    entity-correct even if topically relevant — the failure mode this metric
    exists to expose. Returns None for unlabelled routes (never 0).
    """
    if route_entities_correct is None:
        return None
    if not route_entities_correct:
        return 0.0
    return recall_at(relevance, ranked_ids, k)


def wrong_entity_contamination(
    topic: str,
    doc_topics: Mapping[str, set[str]],
    ranked_ids: Sequence[str],
    k: int,
) -> float:
    """Share of top-k documents whose topics are disjoint from the query topic.

    Document-level proxy (same caveat as the frozen-snapshot round: the true entity-level
    mismatch rate requires human annotation; see the paper's annotation manual).
    """
    top = list(ranked_ids[:k])
    if not top:
        return 0.0
    wrong = sum(1 for doc_id in top if not (doc_topics.get(doc_id, set()) & {topic}))
    return wrong / len(top)


def gate_cost(
    decisions: Sequence[Mapping[str, Any]],
    *,
    route: str,
) -> Mapping[str, Any]:
    """Aggregate gate cost for reporting: how often did a route get gated."""
    relevant = [item for item in decisions if item.get("route") == route]
    total = len(relevant)
    gated = sum(1 for item in relevant if item.get("action") in ("downweight", "reject"))
    warned = sum(1 for item in relevant if item.get("action") == "warn")
    return {
        "route": route,
        "total": total,
        "gated": gated,
        "gated_share": (gated / total) if total else None,
        "warned": warned,
        "warned_share": (warned / total) if total else None,
    }


# ---------------------------------------------------------------------------
# Plan printer (pre-experiment mode only)
# ---------------------------------------------------------------------------


def print_plan() -> None:
    print("=" * 78)
    print("保真实验：臂矩阵与数据需求（未运行；实验前模式）")
    print("=" * 78)
    print("\n主臂（3 路线 × 门控 + 阈值敏感性 + 编码器检查）：")
    for arm_id, spec in ARMS.items():
        pilot = " [pilot]" if arm_id in PILOT_ARMS else ""
        penalty = f" penalty={spec['penalty']}" if spec.get("penalty") else ""
        print(f"  {arm_id:3s} route={spec['route']:22s} gate={spec['gate']:11s}{penalty} {spec['purpose']}{pilot}")
    print("\nH4 正交性对照（文档侧验证组合）：")
    for arm_id, spec in ORTHOGONALITY_ARMS.items():
        print(f"  {arm_id:3s} route={spec['route']:22s} gate={spec['gate']:11s} verifier={spec['verifier']:9s} {spec['purpose']}")
    print("\n组件消融（分析变体，不单独占臂）：")
    for key, value in ABLATION_VARIANTS.items():
        print(f"  - {key}: {value}")
    print("\n数据需求：")
    for key, value in DATA_REQUIREMENTS.items():
        print(f"  - {key}: {value}")
    print("\n主指标（fidelity 层）：per-class P/R/F1 vs 人工标注；κ 门槛 0.6（类别不平衡显著时并报 Gwet's AC1）")
    print("主指标（检索层）：R@5/R@10/MRR@10/nDCG@10 + entity_correct_recall + wrong_entity_contamination + gate-cost")
    print("统计：Wilcoxon signed-rank（配对）+ BH 分族校正 + bootstrap 95% CI + MDE/等效性规则")
    print("功效（tools/power_simulation.py 实测）：n=80–100 对 dz≥0.40–0.45 达 >0.9 功效（BH 后，重尾假设）")
    print("先导 20 查询（止损检查），正式 80 查询（可升级 100–120）；v1/v2 双语料稳健性")
    print("止损规则（预注册）：H2 关联不成立（dz≈0 且 CI 覆盖 0）或 κ<0.6 两轮 → 终止/降级（见论文实验协议 §7）")


def convert_arm_to_request(arm_id: str, query: Mapping[str, Any], *, top_k: int = 10) -> dict[str, Any]:
    """Map an experiment arm to a RetrievalRequest payload (for the driver)."""
    spec = ARMS.get(arm_id) or ORTHOGONALITY_ARMS[arm_id]
    route = spec["route"]
    payload: dict[str, Any] = {
        "question": query["zh_question"],
        "top_k": top_k,
        "fidelity_gate": spec["gate"] if spec["gate"] != "off" else "off",
        "request_id": f"{arm_id}:{query.get('query_id')}",
    }
    if route == "reviewed_mapping":
        payload["english_terms"] = list(query.get("english_terms") or ())
        payload["mesh_terms"] = list(query.get("mesh_terms") or ())
        payload["retrieval_method"] = "bm25"
    elif route == "translate":
        payload["retrieval_method"] = "translate"
    elif route == "multilingual_encoding":
        payload["retrieval_method"] = "bm25,xlingual"
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="fidelity experiment harness (skeleton)")
    parser.add_argument("--plan", action="store_true", help="print the arm matrix and data requirements")
    parser.add_argument("--queries", type=Path, help="path to frozen queries_v1.json (driver mode, not implemented pre-experiment)")
    args = parser.parse_args()
    if args.plan or not args.queries:
        print_plan()
        if not args.plan and not args.queries:
            print("\n（未提供 --queries：仅打印计划。实验运行在人工标注完成后启动。）")
        return
    raise SystemExit(
        "driver mode 尚未启用：正式实验需要 (1) 冻结 queries_v1.json、(2) 双人标注 fidelity_labels_v1.jsonl、"
        "(3) 调用方词表；见论文实验协议 §7 启动条件。"
    )


if __name__ == "__main__":
    main()
