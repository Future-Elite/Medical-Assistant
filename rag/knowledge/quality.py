"""Knowledge-graph validation: structural, provenance, release-gate and CQ checks.

The validator turns published KG-evaluation practices into deterministic checks
over the local EHR-style guideline graph:

* structural constraints, in the spirit of SHACL shape validation
  (Pareti & Konstantinidis, arXiv:2112.01441; Figuera et al., arXiv:2101.07136);
* quality dimensions and provenance coverage
  (Huaman, arXiv:2208.07779; Li et al., arXiv:2212.00994);
* competency questions as executable acceptance tests
  (Wisniewski et al., arXiv:1811.09529; Keet & Khan, arXiv:2412.13688);
* release gating and evidence review, so a demo fixture cannot be published
  as clinical knowledge;
* GraphRAG retrieval/evidence checks with negative and unanswerable cases
  (Han et al., arXiv:2502.11371; Zeng et al., arXiv:2506.06331;
  Xiao et al., arXiv:2506.02404).

The output is evidence about the *contract*, not clinical correctness.  The
bundled fixture is synthetic and must never be reported as clinical validity.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from ..core.his import snapshot_from_dict
from ..patient_graph import build_patient_graph
from ..sources.demo import load_demo_cases
from .model import KnowledgeError, KnowledgeGraph, RELATION_TYPES, load_knowledge_graph
from .service import KnowledgeService, get_default_service

SCHEMA_VERSION = "knowledge-graph-validation-1.0"
DEFAULT_CQ_PATH = Path(__file__).resolve().parent / "fixtures" / "competency_questions.json"

# Fact names produced by patient_facts.py; condition expressions may only use
# these keys, otherwise an unknown fact silently becomes `unknown` forever.
ALLOWED_FACTS = {
    "ga_weeks", "ga_precision", "ga_text", "birth_weight_g", "age_hours",
    "gender", "gbs_status", "prom_hours", "maternal_fever_c", "diagnosis",
    "respiratory_support", "active_treatment", "pending_test", "clinically_well",
    "symptom", "ph", "paco2", "pao2", "spo2", "wbc", "platelet", "hemoglobin",
    "crp", "sodium", "total_bilirubin", "temperature_c", "heart_rate",
    "respiratory_rate", "creatinine", "urea", "glucose", "care_setting",
    "culture_status", "report_status",
}
CONDITION_OPS = {
    "all", "any", "not", "exists", "eq", "ne", "lt", "lte", "gt", "gte",
    "in", "contains", "between",
}
REVIEWED = {"reviewed", "approved"}
NON_CLINICAL_REVIEW = {"synthetic_fixture"}

LITERATURE = [
    {"id": "arXiv:2112.01441", "title": "A Review of SHACL: From Data Validation to Schema Reasoning for RDF Graphs",
     "authors": "Pareti & Konstantinidis", "year": 2021, "use": "SHACL constraint/validation framing"},
    {"id": "arXiv:2101.07136", "title": "Trav-SHACL: Efficiently Validating Networks of SHACL Constraints",
     "authors": "Figuera et al.", "year": 2021, "use": "constraint validation at graph scale"},
    {"id": "arXiv:2208.07779", "title": "Steps to Knowledge Graphs Quality Assessment",
     "authors": "Huaman", "year": 2022, "use": "KG quality dimensions and assessment steps"},
    {"id": "arXiv:2212.00994", "title": "Knowledge Graph Quality Evaluation under Incomplete Information",
     "authors": "Li, Zou & Cai", "year": 2022, "use": "ability-level quality under incomplete information"},
    {"id": "arXiv:1811.09529", "title": "Competency Questions and SPARQL-OWL Queries Dataset and Analysis",
     "authors": "Wisniewski et al.", "year": 2018, "use": "competency questions as executable queries"},
    {"id": "arXiv:2412.13688", "title": "Discerning and Characterising Types of Competency Questions for Ontologies",
     "authors": "Keet & Khan", "year": 2024, "use": "CQ types and formulation guidance"},
    {"id": "10.2196/46477", "title": "Structure of Health Information With Different Information Models: Evaluation Study With Competency Questions",
     "authors": "J Med Internet Res", "year": 2023, "use": "competency questions for health information models"},
    {"id": "10.1136/bmjdh-2026-000041", "title": "Development and evaluation of RAG and GraphRAG for guideline-based clinical decision support in chronic kidney disease",
     "authors": "BMJ Digital Health", "year": 2026, "use": "clinical GraphRAG evaluation protocol"},
    {"id": "arXiv:2502.11371", "title": "RAG vs. GraphRAG: A Systematic Evaluation and Key Insights",
     "authors": "Han et al.", "year": 2025, "use": "RAG vs GraphRAG systematic evaluation"},
    {"id": "arXiv:2506.06331", "title": "How Significant Are the Real Performance Gains? An Unbiased Evaluation Framework for GraphRAG",
     "authors": "Zeng et al.", "year": 2025, "use": "unbiased GraphRAG evaluation (unanswerable cases)"},
    {"id": "arXiv:2506.02404", "title": "GraphRAG-Bench: Challenging Domain-Specific Reasoning for Evaluating Graph Retrieval-Augmented Generation",
     "authors": "Xiao et al.", "year": 2025, "use": "domain-specific GraphRAG benchmark design"},
    {"id": "arXiv:2502.05239", "title": "Enhancing Knowledge Graph Construction: Evaluating with Emphasis on Hallucination, Omission, and Graph Similarity",
     "authors": "Ghanem & Cruz", "year": 2025, "use": "hallucination/omission and graph similarity metrics"},
    {"id": "arXiv:2508.21774", "title": "Improving Biomedical Knowledge Graph Quality: A Community Approach",
     "authors": "Cortes et al.", "year": 2025, "use": "biomedical KG quality and provenance criteria"},
    {"id": "10.1038/s41597-023-02814-8", "title": "PMC-Patients: A Large-scale Dataset of Patient Summaries and Relations for Retrieval and Summarization",
     "authors": "Zhao et al.", "year": 2023, "use": "patient-level retrieval evaluation (PMC-Patients)"},
]



def validate_knowledge_graph(
    graph: KnowledgeGraph | None = None,
    *,
    cqs_path: str | Path | None = None,
    service: KnowledgeService | None = None,
) -> dict[str, Any]:
    """Run all validation sections and return a JSON-serialisable report."""
    graph = graph or load_knowledge_graph()
    service = service or get_default_service()
    if service.graph is not graph:
        service = KnowledgeService(graph)
    issues: list[dict[str, Any]] = []

    structure = _validate_structure(graph, issues)
    provenance = _validate_provenance(graph, issues)
    patient_graph = _validate_patient_graphs(issues)
    quality = _quality_dimensions(graph, issues)
    release_gate = _validate_release_gate(graph, service, issues)
    cq_report = _run_competency_questions(graph, service, cqs_path, issues)

    errors = sum(1 for issue in issues if issue["severity"] == "error")
    warnings = sum(1 for issue in issues if issue["severity"] == "warning")
    goal = {
        "structure_errors": structure["errors"],
        "provenance_errors": provenance["errors"],
        "patient_graph_errors": patient_graph["errors"],
        "release_gate_passed": release_gate["passed"],
        "cq_pass_rate": cq_report["pass_rate"],
        "min_quality_score": 0.9,
        "targets": {
            "structure_errors": 0,
            "provenance_errors": 0,
            "patient_graph_errors": 0,
            "release_gate_passed": True,
            "cq_pass_rate": 1.0,
            "quality_score": ">= 0.9",
        },
    }
    goal["met"] = (
        structure["errors"] == 0
        and provenance["errors"] == 0
        and patient_graph["errors"] == 0
        and release_gate["passed"]
        and cq_report["pass_rate"] >= 1.0
        and quality["score"] >= 0.9
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "release": graph.release.to_dict(),
        "graph": {
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "recommendations": len(graph.recommendations),
            "sources": len(graph.sources),
            "evidence_spans": len(graph.spans),
            "runtime_bindings": len(graph.bindings),
        },
        "sections": {
            "structure": structure,
            "provenance": provenance,
            "patient_graph": patient_graph,
            "quality_dimensions": quality,
            "release_gate": release_gate,
            "competency_questions": cq_report,
        },
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "issues": issues,
        },
        "goal": goal,
        "literature": LITERATURE,
        "boundary": (
            "结构、来源与 CQ 检查只证明该 release 满足本地契约；synthetic_fixture 不是临床证据，"
            "CQ 通过不等于临床正确，不能写成临床有效性或医院可部署性。"
        ),
    }


def _issue(
    issues: list[dict[str, Any]],
    section: str,
    code: str,
    message: str,
    *,
    severity: str = "error",
    **extra: Any,
) -> None:
    issues.append({
        "section": section,
        "code": code,
        "severity": severity,
        "message": message,
        **{key: value for key, value in extra.items() if value not in (None, "")},
    })


def _validate_structure(graph: KnowledgeGraph, issues: list[dict[str, Any]]) -> dict[str, Any]:
    errors_before = sum(1 for issue in issues if issue["severity"] == "error")
    checks = 0
    for rec_id, rec in graph.recommendations.items():
        checks += 1
        if not rec.get("concepts"):
            _issue(issues, "structure", "REC_NO_CONCEPT", f"{rec_id} 没有 concept", recommendation_id=rec_id)
        checks += 1
        if not rec.get("evidence_span_ids"):
            _issue(issues, "structure", "REC_NO_EVIDENCE", f"{rec_id} 没有证据片段", recommendation_id=rec_id)
        checks += 1
        if not rec.get("runtime_binding_ids"):
            _issue(issues, "structure", "REC_NO_BINDING", f"{rec_id} 没有运行时绑定", recommendation_id=rec_id)
        checks += 1
        if not rec.get("supported_operations"):
            _issue(issues, "structure", "REC_NO_OPERATION", f"{rec_id} 没有 supported_operations", recommendation_id=rec_id)
        for binding_id in rec.get("runtime_binding_ids") or ():
            binding = graph.bindings.get(str(binding_id)) or {}
            checks += 1
            if not bool(binding.get("executable")):
                _issue(issues, "structure", "BINDING_NOT_EXECUTABLE",
                       f"{rec_id} 的绑定 {binding_id} 不可执行", recommendation_id=rec_id)
            missing = set(rec.get("supported_operations") or ()) - set(binding.get("operations") or ())
            checks += 1
            if missing:
                _issue(issues, "structure", "BINDING_OPERATION_GAP",
                       f"{rec_id} 的操作 {sorted(missing)} 不在绑定 {binding_id}", recommendation_id=rec_id)
        for condition_id in (
            list(rec.get("indications") or ())
            + list(rec.get("exclusions") or ())
            + list(rec.get("temporal_conditions") or ())
        ):
            condition = graph.conditions.get(str(condition_id)) or {}
            checks += 1
            _validate_condition_expr(condition.get("expr"), f"{rec_id}/{condition_id}", issues)
    for rec_id, rec in graph.recommendations.items():
        for other in rec.get("conflicts_with") or ():
            checks += 1
            reciprocal = graph.recommendations.get(str(other), {}).get("conflicts_with") or ()
            if rec_id not in {str(item) for item in reciprocal}:
                _issue(issues, "structure", "CONFLICT_NOT_SYMMETRIC",
                       f"{rec_id} 与 {other} 的冲突不是双向", recommendation_id=rec_id)
    required_panels = {
        "panel:vitals", "panel:blood_gas", "panel:cbc", "panel:infection_panel",
        "panel:culture", "panel:imaging", "panel:electrolytes", "panel:renal",
        "panel:glucose", "panel:bilirubin",
    }
    checks += 1
    missing_panels = sorted(required_panels - set(graph.panels))
    if missing_panels:
        _issue(issues, "structure", "PANEL_COVERAGE_GAP", f"缺少面板：{missing_panels}")
    for rec_id, rec in graph.recommendations.items():
        checks += 1
        for field in ("evidence_grade_original", "recommendation_strength_original",
                      "restriction_kind", "restriction_severity", "care_phase"):
            if not str(rec.get(field) or "").strip():
                _issue(issues, "structure", "REC_MISSING_P0_FIELD",
                       f"{rec_id} 缺少 {field}", recommendation_id=rec_id)
        checks += 1
        if not rec.get("review_records"):
            _issue(issues, "structure", "REC_NO_REVIEW_RECORD", f"{rec_id} 没有 review_records",
                   recommendation_id=rec_id)
        checks += 1
        if rec.get("setting_id") and str(rec["setting_id"]) not in graph.settings:
            _issue(issues, "structure", "REC_UNKNOWN_SETTING",
                   f"{rec_id} 的 setting_id 不存在", recommendation_id=rec_id)
        checks += 1
        if not isinstance(rec.get("unformalized_conditions"), list):
            _issue(issues, "structure", "REC_UNFORMALIZED_TYPE",
                   f"{rec_id} 的 unformalized_conditions 不是数组", recommendation_id=rec_id)
    for source_id, source in graph.sources.items():
        checks += 1
        if not str(source.get("screening_status") or "").strip():
            _issue(issues, "structure", "SOURCE_NO_SCREENING_STATUS", f"{source_id} 缺少 screening_status")
    checks += 1
    event_sources = {str(event.get("source_id") or "") for event in graph.source_events}
    missing_events = sorted(set(graph.sources) - event_sources)
    if missing_events:
        _issue(issues, "structure", "SOURCE_NO_EVENT_LOG", f"来源缺少事件日志：{missing_events}")
    for setting_id, setting in graph.settings.items():
        checks += 1
        if not str(setting.get("label") or "").strip():
            _issue(issues, "structure", "SETTING_MISSING_LABEL", f"{setting_id} 缺少 label")
    for analyte_id, analyte in graph.analytes.items():
        checks += 1
        for field in ("label", "specimen", "method", "unit"):
            if field not in analyte or str(analyte.get(field) or "").strip() == "" and field != "unit":
                _issue(issues, "structure", "ANALYTE_MISSING_FIELD",
                       f"{analyte_id} 缺少 {field}", analyte_id=analyte_id)
    for exam_id, exam in graph.examinations.items():
        checks += 1
        for field in ("label", "purpose"):
            if not str(exam.get(field) or "").strip():
                _issue(issues, "structure", "EXAM_MISSING_FIELD", f"{exam_id} 缺少 {field}")
    for ref_id, ref in graph.reference_definitions.items():
        checks += 1
        for field in ("analyte_key", "kind", "source_id"):
            if not str(ref.get(field) or "").strip():
                _issue(issues, "structure", "REFERENCE_MISSING_FIELD", f"{ref_id} 缺少 {field}")
        checks += 1
        if str(ref.get("source_id") or "") not in graph.sources:
            _issue(issues, "structure", "REFERENCE_UNKNOWN_SOURCE", f"{ref_id} 的 source 不存在")
    checks += 1
    for field in ("coverage", "dependencies", "change_summary", "known_gaps", "evaluation", "index_config"):
        value = getattr(graph.release, field, None)
        if not value:
            _issue(issues, "structure", "RELEASE_MISSING_P0_FIELD", f"release 缺少 {field}")
    for population_id, population in graph.populations.items():
        checks += 1
        _validate_condition_expr(population.get("expr"), f"population/{population_id}", issues)
    for condition_id, condition in graph.conditions.items():
        checks += 1
        _validate_condition_expr(condition.get("expr"), f"condition/{condition_id}", issues)
    for edge in graph.edges:
        checks += 1
        if edge.get("relation") not in RELATION_TYPES:
            _issue(issues, "structure", "EDGE_UNKNOWN_RELATION",
                   f"{edge.get('source')} -[{edge.get('relation')}]-> {edge.get('target')}")
    checks += 1
    _schema = str(graph.release.schema_version or "")
    if _schema != "knowledge-graph" and not _schema.startswith("knowledge-graph-"):
        _issue(issues, "structure", "SCHEMA_VERSION",
               f"不支持的 schema_version: {graph.release.schema_version}")
    for span_id, span in graph.spans.items():
        checks += 1
        for field in ("source_id", "locator", "quote"):
            if not str(span.get(field) or "").strip():
                _issue(issues, "structure", "SPAN_MISSING_FIELD",
                       f"{span_id} 缺少 {field}", span_id=span_id)
        checks += 1
        if not re.fullmatch(r"[0-9a-f]{64}", str(span.get("content_sha256") or "")):
            _issue(issues, "structure", "SPAN_HASH_MISSING", f"{span_id} 缺少 content_sha256", span_id=span_id)
        checks += 1
        if int(span.get("page_start") or 0) < 1:
            _issue(issues, "structure", "SPAN_PAGE_MISSING", f"{span_id} 缺少 page_start", span_id=span_id)
        checks += 1
        if str(span.get("source_id") or "") not in graph.sources:
            _issue(issues, "structure", "SPAN_UNKNOWN_SOURCE",
                   f"{span_id} 的 source 不存在", span_id=span_id)
    for source_id, source in graph.sources.items():
        checks += 1
        for field in ("revision_id", "title", "review_status", "document_status"):
            if not str(source.get(field) or "").strip():
                _issue(issues, "structure", "SOURCE_MISSING_FIELD",
                       f"{source_id} 缺少 {field}", source_id=source_id)
    for binding_id, binding in graph.bindings.items():
        checks += 1
        if str(binding.get("action_id") or "") not in graph.actions:
            _issue(issues, "structure", "BINDING_UNKNOWN_ACTION",
                   f"{binding_id} 的 action 不存在", binding_id=binding_id)
        checks += 1
        if not isinstance(binding.get("executable"), bool):
            _issue(issues, "structure", "BINDING_EXECUTABLE_TYPE",
                   f"{binding_id} 的 executable 不是 bool", binding_id=binding_id)
    for field in ("release_id", "knowledge_version", "schema_version", "created_at"):
        checks += 1
        if not str(getattr(graph.release, field, "") or "").strip():
            _issue(issues, "structure", "RELEASE_MISSING_FIELD", f"release 缺少 {field}")
    errors = sum(1 for issue in issues if issue["severity"] == "error") - errors_before
    return {"checks": checks, "errors": errors}


def _validate_condition_expr(expr: Any, where: str, issues: list[dict[str, Any]]) -> None:
    if not isinstance(expr, Mapping):
        _issue(issues, "structure", "CONDITION_MALFORMED", f"{where} 不是条件对象", where=where)
        return
    if len(expr) != 1:
        _issue(issues, "structure", "CONDITION_MALFORMED", f"{where} 必须只有一个操作符", where=where)
    op, arg = next(iter(expr.items()))
    if op not in CONDITION_OPS:
        _issue(issues, "structure", "CONDITION_UNKNOWN_OP", f"{where} 使用了未知操作符 {op}", where=where)
        return
    if op in {"all", "any"}:
        if not isinstance(arg, list) or not arg:
            _issue(issues, "structure", "CONDITION_MALFORMED", f"{where}.{op} 需要非空数组", where=where)
            return
        for index, child in enumerate(arg):
            _validate_condition_expr(child, f"{where}.{op}[{index}]", issues)
    elif op == "not":
        _validate_condition_expr(arg, f"{where}.not", issues)
    elif op == "exists":
        _validate_fact_ref(arg, f"{where}.exists", issues)
    elif op in {"eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "between"}:
        if not isinstance(arg, list) or len(arg) < 2:
            _issue(issues, "structure", "CONDITION_MALFORMED", f"{where}.{op} 需要 [fact, value]", where=where)
            return
        _validate_fact_ref(arg[0], f"{where}.{op}[0]", issues)
    else:
        _issue(issues, "structure", "CONDITION_UNKNOWN_OP", f"{where} 使用了未知操作符 {op}", where=where)


def _validate_fact_ref(ref: Any, where: str, issues: list[dict[str, Any]]) -> None:
    if not isinstance(ref, Mapping) or "fact" not in ref:
        _issue(issues, "structure", "CONDITION_MISSING_FACT", f"{where} 缺少 fact", where=where)
        return
    fact = str(ref.get("fact") or "")
    if fact not in ALLOWED_FACTS:
        _issue(issues, "structure", "CONDITION_UNKNOWN_FACT", f"{where} 使用了未知事实 {fact}", where=where)



def _validate_patient_graphs(issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Check the per-request patient graph on the shipped synthetic cases."""
    errors_before = sum(1 for issue in issues if issue["severity"] == "error")
    try:
        cases = load_demo_cases()["cases"]
    except Exception as exc:
        _issue(issues, "patient_graph", "CASES_UNAVAILABLE", f"{type(exc).__name__}: {exc}")
        return {"cases": 0, "checks": 0, "errors": 1}
    checks = 0
    records = 0
    for case in cases:
        case_id = str(case.get("case_id") or "")
        snapshot = snapshot_from_dict({"patient_snapshot": dict(case["snapshot"])})
        graph = build_patient_graph(snapshot)
        record_nodes = {node.node_id: node for node in graph.nodes if node.node_type == "record"}
        records += len(record_nodes)
        node_ids = {node.node_id for node in graph.nodes}
        checks += 1
        for edge in graph.edges:
            if edge.source not in node_ids or edge.target not in node_ids:
                _issue(issues, "patient_graph", "EDGE_DANGLING",
                       f"{case_id} 边端点不存在：{edge.path_step()}", case_id=case_id)
        for record_id, node in record_nodes.items():
            checks += 1
            subjects = [edge for edge in graph.edges
                        if edge.source == record_id and edge.relation == "HAS_SUBJECT"]
            if len(subjects) != 1:
                _issue(issues, "patient_graph", "RECORD_SUBJECT_CARDINALITY",
                       f"{case_id} {record_id} HAS_SUBJECT={len(subjects)}", case_id=case_id)
            checks += 1
            mentions = [edge for edge in graph.edges
                        if edge.source == record_id and edge.relation == "MENTIONS"]
            for edge in mentions:
                if graph.node_by_id[edge.target].node_type != "concept":
                    _issue(issues, "patient_graph", "MENTION_TARGET_TYPE",
                           f"{case_id} {record_id} MENTIONS 非 concept", case_id=case_id)
        derived = [
            node for node in record_nodes.values()
            if str(node.attributes.get("collection") or "").startswith("derived_")
            or str(node.attributes.get("source_type") or "") == "derived_from_ehr_input"
        ]
        for node in derived:
            checks += 1
            if not any(edge.source == node.node_id and edge.relation == "DERIVED_FROM"
                       for edge in graph.edges):
                _issue(issues, "patient_graph", "DERIVED_WITHOUT_SOURCE",
                       f"{case_id} {node.node_id} 没有 DERIVED_FROM", case_id=case_id)
        checks += 1
        if graph.snapshot_sha256 != build_patient_graph(snapshot).snapshot_sha256:
            _issue(issues, "patient_graph", "SNAPSHOT_HASH_UNSTABLE",
                   f"{case_id} 两次建图 hash 不一致", case_id=case_id)
        checks += 1
        if len(record_nodes) != len(snapshot.visible_records()):
            _issue(issues, "patient_graph", "RECORD_COUNT_MISMATCH",
                   f"{case_id} record={len(record_nodes)} visible={len(snapshot.visible_records())}",
                   case_id=case_id)
    errors = sum(1 for issue in issues if issue["severity"] == "error") - errors_before
    return {"cases": len(cases), "records": records, "checks": checks, "errors": errors}

def _validate_provenance(graph: KnowledgeGraph, issues: list[dict[str, Any]]) -> dict[str, Any]:
    errors_before = sum(1 for issue in issues if issue["severity"] == "error")
    checks = 0
    for rec_id, rec in graph.recommendations.items():
        spans = [graph.spans.get(str(item)) for item in rec.get("evidence_span_ids") or ()]
        checks += 1
        if not any(spans):
            _issue(issues, "provenance", "REC_NO_EVIDENCE_CHAIN", f"{rec_id} 没有可解析的证据链", recommendation_id=rec_id)
            continue
        for span in spans:
            if span is None:
                _issue(issues, "provenance", "REC_SPAN_MISSING", f"{rec_id} 证据片段不存在", recommendation_id=rec_id)
                continue
            checks += 1
            source = graph.sources.get(str(span.get("source_id") or ""))
            if source is None:
                _issue(issues, "provenance", "SPAN_SOURCE_MISSING",
                       f"{rec_id} 的证据片段没有 source", recommendation_id=rec_id)
                continue
            checks += 1
            if str(span.get("review_status") or "") in NON_CLINICAL_REVIEW:
                _issue(issues, "provenance", "EVIDENCE_NOT_CLINICALLY_REVIEWED",
                       f"{rec_id} 的证据 {span.get('span_id')} 是 synthetic_fixture",
                       severity="warning", recommendation_id=rec_id)
            elif str(span.get("review_status") or "") not in REVIEWED:
                _issue(issues, "provenance", "EVIDENCE_REVIEW_STATUS",
                       f"{rec_id} 的证据 {span.get('span_id')} 未审核", recommendation_id=rec_id)
            checks += 1
            if str(source.get("review_status") or "") in NON_CLINICAL_REVIEW:
                _issue(issues, "provenance", "SOURCE_NOT_CLINICALLY_REVIEWED",
                       f"{rec_id} 的来源 {source.get('source_id')} 是 synthetic_fixture",
                       severity="warning", recommendation_id=rec_id)
            elif str(source.get("review_status") or "") not in REVIEWED:
                _issue(issues, "provenance", "SOURCE_REVIEW_STATUS",
                       f"{rec_id} 的来源 {source.get('source_id')} 未审核", recommendation_id=rec_id)
    for source_id, source in graph.sources.items():
        checks += 1
        if not str(source.get("license_note") or "").strip() and not str(source.get("storage_policy") or "").strip():
            _issue(issues, "provenance", "SOURCE_NO_LICENSE", f"{source_id} 没有许可/存储说明",
                   severity="warning", source_id=source_id)
        checks += 1
        if not str(source.get("publication_date") or "").strip() and not str(source.get("retrieved_at") or "").strip():
            _issue(issues, "provenance", "SOURCE_NO_DATE", f"{source_id} 没有发布日期/获取日期",
                   severity="warning", source_id=source_id)
    checks += 1
    if not re.fullmatch(r"[0-9a-f]{64}", graph.release.content_sha256):
        _issue(issues, "provenance", "RELEASE_HASH_INVALID", "release content hash 不是 sha256")
    errors = sum(1 for issue in issues if issue["severity"] == "error") - errors_before
    return {"checks": checks, "errors": errors}


def _quality_dimensions(graph: KnowledgeGraph, issues: list[dict[str, Any]]) -> dict[str, Any]:
    def ratio(rows: Sequence[bool]) -> float:
        return round(sum(1 for row in rows if row) / len(rows), 4) if rows else 0.0

    completeness = ratio([
        bool(rec.get("summary") and rec.get("direction") and rec.get("supported_operations"))
        and bool(rec.get("evidence_span_ids")) and bool(rec.get("runtime_binding_ids"))
        for rec in graph.recommendations.values()
    ])
    provenance = ratio([
        bool(rec.get("evidence_span_ids"))
        and all(
            str(graph.spans.get(str(span_id), {}).get("locator") or "").strip()
            and str(graph.spans.get(str(span_id), {}).get("quote") or "").strip()
            for span_id in rec.get("evidence_span_ids") or ()
        )
        for rec in graph.recommendations.values()
    ])
    consistency_errors = sum(
        1 for issue in issues
        if issue["section"] == "structure" and issue["severity"] == "error"
    )
    consistency = 1.0 if consistency_errors == 0 else 0.0
    versioning = ratio([
        bool(source.get("revision_id") and (source.get("publication_date") or source.get("retrieved_at")))
        for source in graph.sources.values()
    ])
    understandability = ratio([
        bool(node.get("label") and (node.get("aliases") or node["type"] == "recommendation"))
        for node in graph.nodes.values()
        if node["type"] in {"concept", "action", "panel"}
    ])
    licensing = ratio([
        bool(source.get("license_note") or source.get("storage_policy"))
        for source in graph.sources.values()
    ])
    score = round((completeness + provenance + consistency + versioning + understandability + licensing) / 6.0, 4)
    for dimension, value in (
        ("completeness", completeness), ("provenance", provenance),
        ("consistency", consistency), ("versioning", versioning),
        ("understandability", understandability), ("licensing", licensing),
    ):
        if value < 1.0:
            _issue(issues, "quality", "QUALITY_GAP", f"{dimension} = {value}", severity="warning")
    return {
        "completeness": completeness,
        "provenance": provenance,
        "consistency": consistency,
        "versioning": versioning,
        "understandability": understandability,
        "licensing": licensing,
        "score": score,
    }


def _validate_release_gate(
    graph: KnowledgeGraph, service: KnowledgeService, issues: list[dict[str, Any]]
) -> dict[str, Any]:
    before = sum(1 for issue in issues if issue["severity"] == "error")
    passed = True
    release = graph.release
    if release.publishable:
        if release.review_status not in REVIEWED:
            _issue(issues, "release_gate", "PUBLISHABLE_NOT_REVIEWED", "publishable release 未审核")
            passed = False
        for span_id, span in graph.spans.items():
            if str(span.get("review_status") or "") not in REVIEWED:
                _issue(issues, "release_gate", "PUBLISHABLE_UNREVIEWED_SPAN",
                       f"publishable release 的 {span_id} 未审核")
                passed = False
        for source_id, source in graph.sources.items():
            if str(source.get("review_status") or "") not in REVIEWED:
                _issue(issues, "release_gate", "PUBLISHABLE_UNREVIEWED_SOURCE",
                       f"publishable release 的 {source_id} 未审核")
                passed = False
    else:
        try:
            service.publish_release(release.release_id, rationale="validation probe")
        except KnowledgeError:
            pass
        else:
            _issue(issues, "release_gate", "SYNTHETIC_RELEASE_PUBLISHED",
                   "synthetic_fixture release 不应通过 publish")
            passed = False
    reloaded = KnowledgeGraph(graph.raw)
    if reloaded.release.content_sha256 != release.content_sha256:
        _issue(issues, "release_gate", "RELEASE_HASH_UNSTABLE", "同一内容两次加载的 hash 不一致")
        passed = False
    errors = sum(1 for issue in issues if issue["severity"] == "error") - before
    return {"passed": passed, "errors": errors, "publishable": release.publishable}


def _run_competency_questions(
    graph: KnowledgeGraph,
    service: KnowledgeService,
    cqs_path: str | Path | None,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    path = Path(cqs_path) if cqs_path else DEFAULT_CQ_PATH
    if not path.is_file():
        _issue(issues, "competency_questions", "CQ_FILE_MISSING", f"{path} 不存在")
        return {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0, "questions": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    snapshots = payload.get("snapshots") or {}
    questions = payload.get("questions") or []
    results = []
    for question in questions:
        result = _run_one_cq(graph, service, question, snapshots)
        results.append(result)
        if not result["passed"]:
            _issue(
                issues, "competency_questions", "CQ_FAILED",
                f"{result['id']} 未通过：{'；'.join(result['failures'])}",
                question_id=result["id"],
            )
    passed = sum(1 for result in results if result["passed"])
    total = len(results)
    return {
        "path": str(path),
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "questions": results,
    }


def _snapshot(payload: Mapping[str, Any]):
    return snapshot_from_dict({
        "patient_ref": payload.get("patient_ref", "cq"),
        "as_of": payload.get("as_of", "2026-04-15"),
        "records": payload.get("records") or [],
    })


def _run_one_cq(
    graph: KnowledgeGraph,
    service: KnowledgeService,
    question: Mapping[str, Any],
    snapshots: Mapping[str, Any],
) -> dict[str, Any]:
    cq_id = str(question.get("id") or "")
    kind = str(question.get("type") or "retrieval")
    expect = dict(question.get("expect") or {})
    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            failures.append(f"{name}: {detail}" if detail else name)

    try:
        snapshot_payload = snapshots.get(str(question.get("snapshot") or ""))
        if kind == "release_gate":
            try:
                service.publish_release(graph.release.release_id, rationale="CQ probe")
            except KnowledgeError as exc:
                message = str(exc)
                record("publish_blocked", "not publishable" in message or "not reviewed" in message, message)
            else:
                record("publish_blocked", False, "synthetic release was published")
            record("hash_stable", KnowledgeGraph(graph.raw).release.content_sha256 == graph.release.content_sha256)
        elif kind == "term_resolution":
            snapshot = _snapshot(snapshot_payload or {})
            resolution = service.resolve_terms(list(question.get("queries") or []))
            rows = {row["input"]: row for row in resolution["terms"]}
            for text, expected_status in (expect.get("term_status") or {}).items():
                record(f"term:{text}", rows.get(text, {}).get("status") == expected_status,
                       f"actual={rows.get(text, {}).get('status')}")
            record("no_recommendations", not service.search(list(question.get("queries") or []), snapshot).get("recommendations"))
        elif kind == "evidence":
            evidence = service.get_evidence(str(question.get("span_id") or ""))
            span = evidence["span"]; source = evidence["source"]
            record("source_id", source.get("source_id") == expect.get("source_id"),
                   f"actual={source.get('source_id')}")
            record("locator", bool(str(span.get("locator") or "").strip()))
            record("quote", bool(str(span.get("quote") or "").strip()))
            record("review_status", bool(str(span.get("review_status") or "").strip()))
        elif kind == "action_validation":
            snapshot = _snapshot(snapshot_payload or {})
            context = service.search(list(question.get("queries") or []), snapshot)
            result = service.validate_actions(dict(question.get("plan") or {}), snapshot, context["knowledge_context_id"])
            expected = dict(expect.get("action") or {})
            if "eligible_for_simulation" in expected:
                record("eligible_for_simulation", result["eligible_for_simulation"] == expected["eligible_for_simulation"],
                       f"actual={result['eligible_for_simulation']}")
            if "status" in expected:
                record("action_status", result["status"] == expected["status"], f"actual={result['status']}")
            codes = {error["code"] for error in result.get("errors") or ()}
            for code in expected.get("error_codes") or ():
                record(f"error:{code}", code in codes, f"actual={sorted(codes)}")
            for code in expected.get("absent_error_codes") or ():
                record(f"absent_error:{code}", code not in codes, f"actual={sorted(codes)}")
        elif kind == "stale_snapshot":
            snapshot = _snapshot(snapshot_payload or {})
            context = service.search(list(question.get("queries") or []), snapshot)
            changed = _snapshot(snapshots.get(str(question.get("stale_snapshot") or "")) or {})
            result = service.validate_actions(dict(question.get("plan") or {}), changed, context["knowledge_context_id"])
            codes = {error["code"] for error in result.get("errors") or ()}
            record("stale_blocked", "SNAPSHOT_STALE" in codes, f"actual={sorted(codes)}")
            record("not_eligible", not result["eligible_for_simulation"])
        else:
            snapshot = _snapshot(snapshot_payload or {})
            context = service.search(list(question.get("queries") or []), snapshot)
            if "status" in expect:
                record("status", context.get("status") == expect["status"], f"actual={context.get('status')}")
            recs = {row["recommendation_id"]: row for row in context.get("recommendations") or ()}
            excluded = {row["id"]: row for row in context.get("excluded_recommendations") or ()}
            for rec_id in expect.get("recommendation_ids") or ():
                record(f"recommendation:{rec_id}", rec_id in recs, f"actual={sorted(recs)}")
            for rec_id in expect.get("absent_recommendation_ids") or ():
                record(f"absent_recommendation:{rec_id}", rec_id not in recs, f"actual={sorted(recs)}")
            for rec_id, value in (expect.get("eligible") or {}).items():
                record(f"eligible:{rec_id}", recs.get(rec_id, {}).get("eligible_for_simulation") is value,
                       f"actual={recs.get(rec_id, {}).get('eligible_for_simulation')}")
            for rec_id in expect.get("not_eligible") or ():
                record(f"not_eligible:{rec_id}", recs.get(rec_id, {}).get("eligible_for_simulation") is False,
                       f"actual={recs.get(rec_id, {}).get('eligible_for_simulation')}")
            for rec_id in expect.get("excluded_ids") or ():
                record(f"excluded:{rec_id}", rec_id in excluded, f"actual={sorted(excluded)}")
            for rec_id, spans in (expect.get("evidence_span_ids") or {}).items():
                actual = set(recs.get(rec_id, {}).get("evidence_span_ids") or ())
                record(f"evidence:{rec_id}", set(spans) <= actual, f"actual={sorted(actual)}")
            for rec_id, direction in (expect.get("direction") or {}).items():
                record(f"direction:{rec_id}", recs.get(rec_id, {}).get("direction") == direction,
                       f"actual={recs.get(rec_id, {}).get('direction')}")
            for rec_id, dims in (expect.get("applicability") or {}).items():
                actual = recs.get(rec_id, {}).get("applicability") or {}
                for dim, status in dims.items():
                    record(f"applicability:{rec_id}:{dim}", actual.get(dim) == status,
                           f"actual={actual.get(dim)}")
            for rec_id in expect.get("evidence_paths_nonempty") or ():
                record(f"evidence_paths:{rec_id}", bool(recs.get(rec_id, {}).get("evidence_paths")))
            missing_text = " ".join(
                list(context.get("missing_information") or ())
                + [item for row in recs.values() for item in (row.get("missing_information") or ())]
            )
            for needle in expect.get("missing_information_contains") or ():
                record(f"missing:{needle}", needle in missing_text, f"actual={missing_text}")
            for text, expected_status in (expect.get("term_status") or {}).items():
                rows = {row["input"]: row for row in context.get("term_resolutions") or ()}
                record(f"term:{text}", rows.get(text, {}).get("status") == expected_status,
                       f"actual={rows.get(text, {}).get('status')}")
            # No fabricated graph path/evidence: every returned path step must be
            # an edge in the release, and every cited span/source must exist.
            edge_set = {(edge["source"], edge["relation"], edge["target"]) for edge in graph.edges}
            bad_paths: list[str] = []
            for rec in recs.values():
                for path in rec.get("evidence_paths") or ():
                    for step in path:
                        match = re.fullmatch(r"(.+?) -\[(.+?)\]-> (.+)", str(step))
                        if not match or (match.group(1), match.group(2), match.group(3)) not in edge_set:
                            bad_paths.append(str(step))
                for span_id in rec.get("evidence_span_ids") or ():
                    if span_id not in graph.spans:
                        bad_paths.append(f"missing span {span_id}")
                for source_id in rec.get("source_ids") or ():
                    if source_id not in graph.sources:
                        bad_paths.append(f"missing source {source_id}")
            record("graph_path_and_evidence_exist", not bad_paths, f"bad={bad_paths[:5]}")
    except Exception as exc:  # a CQ must surface, not crash the report
        record("exception", False, f"{type(exc).__name__}: {exc}")
    return {
        "id": cq_id,
        "type": kind,
        "question": question.get("question", ""),
        "passed": not failures,
        "checks": checks,
        "failures": failures,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    """Compact human-readable report for the CLI/log."""
    lines = [
        f"# 知识图谱验证 {report.get('release', {}).get('release_id', '')}",
        "",
        f"- schema: `{report.get('schema_version')}`",
        f"- release: `{report.get('release', {}).get('release_id')}` / `{report.get('release', {}).get('content_sha256')}`",
        f"- goal met: **{report.get('goal', {}).get('met')}**",
        "",
        "## 汇总",
        "",
        "| 项 | 值 |",
        "|---|---|",
    ]
    sections = report.get("sections") or {}
    for key in ("structure", "provenance", "release_gate", "competency_questions"):
        section = sections.get(key) or {}
        lines.append(f"| {key} | {json.dumps({k: v for k, v in section.items() if k != 'questions'}, ensure_ascii=False)} |")
    lines.append(f"| quality_score | {sections.get('quality_dimensions', {}).get('score')} |")
    lines += ["", "## CQ", "", "| ID | type | passed | failures |", "|---|---|---|---|"]
    for row in (sections.get("competency_questions") or {}).get("questions") or ():
        lines.append(f"| {row['id']} | {row['type']} | {row['passed']} | {'；'.join(row['failures'])[:180]} |")
    errors = [issue for issue in (report.get("summary") or {}).get("issues") or () if issue.get("severity") == "error"]
    if errors:
        lines += ["", "## Errors", ""]
        for issue in errors[:40]:
            lines.append(f"- `{issue['section']}/{issue['code']}` {issue['message']}")
    lines += ["", "## 边界", "", str(report.get("boundary") or "")]
    return "\n".join(lines) + "\n"


__all__ = ["validate_knowledge_graph", "render_markdown", "DEFAULT_CQ_PATH", "LITERATURE"]
