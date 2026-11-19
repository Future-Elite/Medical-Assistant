"""Build structural guideline graphs from parsed guideline chunks.

The graph is a *navigation and audit* structure, not clinical advice.  Each
recommendation candidate is linked to the chunk/page it came from, plus any
conditions, exceptions and timeframes found verbatim in that chunk.  Nodes that
cannot be mapped to a canonical patient field stay ``unmapped`` with
``review_status=needs_mapping``; they are never silently dropped or invented.

The output follows the two-layer design in the guideline-graph requirements:
one small graph per guideline, plus a library-level relation layer for topics
and shared subject matter.  This module does not call an LLM and does not
change any parsed text.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

GRAPH_SCHEMA = "guideline-graph"
LIBRARY_SCHEMA = "guideline-graph-library"
GRAPH_VERSION = "2026-11-16"
DEFAULT_LIBRARY_PATH = Path(__file__).resolve().parents[2] / "data" / "generated" / "validation" / "guideline_graph_library.json"

_CUES = (
    "recommend", "suggest", "should", "should be", "is recommended",
    "推荐", "建议", "应给予", "应考虑", "可考虑", "不推荐", "不宜",
)
_HARD_EXCEPTION_CUES = ("禁忌", "禁用", "contraindicated", "must not", "do not use")
_RELATED_EXCEPTION_CUES = ("慎用", "注意", "警惕", "related", "caution", "consider")
_NEGATIVE_EXCEPTION_CUES = ("不推荐", "不宜", "not recommended", "except", "unless", "除外", "除非")
_RECOMMENDATION_SECTIONS = (
    "推荐", "建议", "recommendation", "statement", "consensus", "guideline",
)
_REFERENCE_SECTIONS = ("参考文献", "references", "bibliography", "acknowledgement", "致谢")

_FIELD_TERMS = (
    ("birth_weight_g", ("出生体重", "birth weight", "birthweight", "体重")),
    ("ga_days", ("胎龄", "孕周", "gestational age", "gestation")),
    ("pna_days", ("日龄", "生后", "出生后", "age in days", "postnatal age")),
    ("temp_c", ("体温", "temperature")),
    ("spo2_percent", ("spo2", "血氧饱和度", "oxygen saturation", "血氧")),
    ("pao2", ("pao2", "动脉氧分压")),
    ("paco2", ("paco2", "二氧化碳分压")),
    ("ph", ("ph", "酸碱度")),
    ("crp", ("crp", "c-reactive protein", "c反应蛋白")),
    ("wbc", ("wbc", "白细胞", "white blood cell")),
    ("platelet", ("血小板", "platelet")),
    ("total_bilirubin_umol_l", ("总胆红素", "胆红素", "bilirubin")),
    ("glucose", ("血糖", "glucose")),
    ("fio2", ("fio2", "吸入氧浓度", "fraction of inspired oxygen")),
    ("pressure_cm_h2o", ("peep", "气道压力", "气道压")),
)
_FIELD_PREFIX = {
    "birth_weight_g": "derived_birth", "ga_days": "derived_birth",
    "pna_days": "patient", "temp_c": "vitals", "spo2_percent": "labs",
    "pao2": "labs", "paco2": "labs", "ph": "labs", "crp": "labs",
    "wbc": "labs", "platelet": "labs", "total_bilirubin_umol_l": "labs",
    "glucose": "labs", "fio2": "respiratory_support",
    "pressure_cm_h2o": "respiratory_support",
}
_ACTION_TERMS = (
    ("cpap", ("cpap", "ncpap", "持续气道正压")),
    ("nippv", ("nippv", "无创间歇正压", "non-invasive positive pressure")),
    ("invasive_ventilation", ("机械通气", "invasive ventilation", "intubation", "气管插管")),
    ("surfactant", ("surfactant", "肺表面活性物质", "ps治疗")),
    ("antimicrobial", ("antibiotic", "antimicrobial", "抗菌", "抗感染")),
    ("oxygen", ("oxygen", "氧疗", "吸氧")),
    ("caffeine", ("caffeine", "咖啡因")),
    ("phototherapy", ("phototherapy", "光疗")),
    ("feeding_support", ("feeding", "喂养", "肠内营养")),
    ("transfusion", ("transfusion", "输血")),
    ("fluid_electrolyte", ("fluid", "electrolyte", "补液", "电解质")),
    ("thermal_nutrition", ("temperature control", "保暖", "体温管理")),
)
_OPERATIONS = {
    "start": ("start", "initiate", "开始", "给予", "使用"),
    "stop": ("stop", "discontinue", "停止", "停用"),
    "adjust": ("adjust", "titrate", "调整", "调节"),
    "continue": ("continue", "维持"),
    "wait": ("observe", "wait", "观察"),
    "order_test": ("culture", "imaging", "检查", "培养", "影像"),
}
_OPERATOR_MAP = {
    "≥": "gte", "<=": "lte", "≤": "lte", ">": "gt", "<": "lt",
    "大于等于": "gte", "至少": "gte", "不超过": "lte", "小于等于": "lte",
    "大于": "gt", "超过": "gt", "高于": "gt", "小于": "lt", "低于": "lt",
}
_UNIT_MAP = {
    "克": "g", "g": "g", "千克": "kg", "kg": "kg", "毫克": "mg", "mg": "mg",
    "毫升": "mL", "ml": "mL", "mL": "mL", "天": "day", "d": "day",
    "日": "day", "周": "week", "星期": "week", "小时": "hour", "h": "hour",
    "分钟": "min", "min": "min", "%": "%", "mmhg": "mmHg",
    "次/分": "per_min", "bpm": "per_min",
}
_NUMBER_UNIT = re.compile(
    r"(≥|≤|>|<|>=|<=|大于等于|小于等于|至少|不超过|大于|超过|高于|小于|低于)?"
    r"\s*(\d+(?:\.\d+)?)\s*(%|mmHg|mmhg|mg|g|kg|mL|ml|毫升|毫克|千克|克|"
    r"小时|天|周|分钟|次/分|h|d|min|bpm)?"
)
_TIME_CUE = re.compile(
    r"(生后|出生后|入院后|开始后|within|after|by)\s*(\d+(?:\.\d+)?)\s*"
    r"(小时|h|hours?|天|day|days?|周|week|weeks?)",
    re.IGNORECASE,
)
_GRADE = re.compile(
    r"(?:grade|level|证据等级|推荐强度)\s*[:：]?\s*([A-DⅠⅡⅢⅣ1-4][+\-−]?)",
    re.IGNORECASE,
)


def build_guideline_graph(
    chunks: Sequence[Mapping[str, Any]],
    guideline: Mapping[str, Any],
    *,
    max_recommendations: int = 80,
) -> dict[str, Any]:
    """Build one guideline graph from parsed chunks.

    ``chunks`` must come from ``guideline_parsing`` and contain ``chunk_id`` and
    ``text``.  ``guideline`` is a manifest entry or a mapping with at least
    ``document_id`` and ``title``.
    """
    guideline_id = str(guideline.get("document_id") or guideline.get("guideline_id") or "").strip()
    if not guideline_id:
        raise ValueError("guideline requires document_id")
    title = str(guideline.get("title") or guideline_id)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    warnings: list[str] = []

    def add_node(node: dict[str, Any]) -> str:
        node_id = str(node["id"])
        if node_id in nodes:
            raise ValueError(f"duplicate node id: {node_id}")
        nodes[node_id] = node
        return node_id

    def add_edge(source: str, target: str, relation: str, **attributes: Any) -> None:
        edges.append({"source": source, "target": target, "type": relation, **attributes})

    guideline_node = add_node({
        "id": f"guideline:{guideline_id}", "type": "guideline",
        "title": title, "version": str(guideline.get("version_id") or guideline.get("version") or ""),
        "published_at": str(guideline.get("published_at") or ""),
        "publisher": str(guideline.get("publisher") or ""),
        "population": str(guideline.get("population") or ""),
        "license": str(guideline.get("license") or ""),
        "document_id": guideline_id,
        "review_status": "extracted",
        "publishable": False,
    })

    recommendation_count = 0
    seen_statements: set[str] = set()
    for chunk in chunks:
        if recommendation_count >= max_recommendations:
            break
        chunk_id = str(chunk.get("chunk_id") or "").strip()
        text = str(chunk.get("text") or "").strip()
        if not chunk_id or not text:
            continue
        section_path = [str(item) for item in (chunk.get("section_path") or ())]
        section_text = " ".join(section_path).lower()
        if any(marker in section_text for marker in _REFERENCE_SECTIONS):
            continue
        if not _is_recommendation(text, section_text):
            continue
        statement = _statement(text)
        fingerprint = re.sub(r"\W+", "", statement.lower())[:180]
        if not fingerprint or fingerprint in seen_statements:
            continue
        seen_statements.add(fingerprint)
        recommendation_count += 1
        rec_id = f"rec:{guideline_id}:{chunk_id}"
        actions = _actions(text)
        operations = _operations(text)
        rec_node = add_node({
            "id": rec_id, "type": "recommendation",
            "title": statement[:120],
            "statement": statement,
            "evidence_level": _evidence_level(chunk, text),
            "recommendation_grade": str(chunk.get("recommendation_grade") or ""),
            "actions": actions,
            "supported_operations": operations,
            "review_status": "extracted",
            "publishable": False,
            "source": _locator(chunk),
        })
        add_edge(guideline_node, rec_id, "has_recommendation")

        chunk_node_id = f"chunk:{guideline_id}:{chunk_id}"
        if chunk_node_id not in nodes:
            add_node({
                "id": chunk_node_id, "type": "chunk",
                "chunk_id": chunk_id,
                "text": text[:1200],
                "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "section_path": section_path,
                **_locator(chunk),
            })
            add_edge(chunk_node_id, guideline_node, "part_of_source")
        add_edge(rec_id, chunk_node_id, "supported_by")

        for index, condition in enumerate(_conditions(text), 1):
            cond_id = f"condition:{rec_id}:{index}"
            add_node({
                "id": cond_id, "type": "condition",
                "raw_text": condition["raw_text"],
                "field": condition["field"],
                "operator": condition["operator"],
                "value": condition["value"],
                "unit": condition["unit"],
                "review_status": condition["review_status"],
                "mapping_status": condition["mapping_status"],
                "source": _locator(chunk),
            })
            add_edge(rec_id, cond_id, "requires")

        for index, exception in enumerate(_exceptions(text), 1):
            exc_id = f"exception:{rec_id}:{index}"
            add_node({
                "id": exc_id, "type": "exception",
                "kind": exception["kind"],
                "terms": exception["terms"],
                "raw_text": exception["raw_text"],
                "review_status": "extracted",
                "source": _locator(chunk),
            })
            add_edge(rec_id, exc_id, "excludes")

        for index, timeframe in enumerate(_timeframes(text), 1):
            tf_id = f"timeframe:{rec_id}:{index}"
            add_node({
                "id": tf_id, "type": "timeframe",
                "anchor": timeframe["anchor"],
                "amount": timeframe["amount"],
                "unit": timeframe["unit"],
                "raw_text": timeframe["raw_text"],
                "review_status": "extracted",
                "source": _locator(chunk),
            })
            add_edge(rec_id, tf_id, "within_time")

    graph = {
        "schema_version": GRAPH_SCHEMA,
        "graph_version": GRAPH_VERSION,
        "guideline_id": guideline_id,
        "title": title,
        "version": str(guideline.get("version_id") or guideline.get("version") or ""),
        "source": {
            "document_id": guideline_id,
            "file": str(guideline.get("file") or ""),
            "doi": str(guideline.get("doi") or ""),
            "pmid": str(guideline.get("pmid") or ""),
            "pmcid": str(guideline.get("pmcid") or ""),
            "license": str(guideline.get("license") or ""),
            "topic_keys": list(guideline.get("topic_keys") or ()),
        },
        "nodes": list(nodes.values()),
        "edges": edges,
        "review_status": "extracted",
        "publishable": False,
        "warnings": warnings,
        "stats": {
            "recommendations": sum(1 for node in nodes.values() if node["type"] == "recommendation"),
            "conditions": sum(1 for node in nodes.values() if node["type"] == "condition"),
            "exceptions": sum(1 for node in nodes.values() if node["type"] == "exception"),
            "timeframes": sum(1 for node in nodes.values() if node["type"] == "timeframe"),
            "chunks": sum(1 for node in nodes.values() if node["type"] == "chunk"),
        },
    }
    issues = _validate_graph(graph)
    graph["validation"] = {
        "errors": [item[6:].strip() for item in issues if item.startswith("ERROR:")],
        "warnings": [item[8:].strip() for item in issues if item.startswith("WARNING:")],
    }
    graph["warnings"] = graph["validation"]["warnings"]
    return graph


def build_guideline_library(
    graphs: Sequence[Mapping[str, Any]],
    *,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine per-guideline graphs with a topic relation layer."""
    nodes: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    topics: dict[str, set[str]] = {}
    for graph in graphs:
        guideline_id = str(graph.get("guideline_id") or "")
        for topic in graph.get("source", {}).get("topic_keys") or ():
            key = _topic_key(topic)
            if key:
                topics.setdefault(key, set()).add(guideline_id)
                topic_id = f"topic:{key}"
                if not any(node["id"] == topic_id for node in nodes):
                    nodes.append({"id": topic_id, "type": "topic", "title": str(topic)})
                relations.append({"source": f"guideline:{guideline_id}", "target": topic_id, "type": "covers_topic"})
    guideline_ids = [str(graph.get("guideline_id")) for graph in graphs]
    for key, members in sorted(topics.items()):
        if len(members) < 2:
            continue
        ordered = [item for item in guideline_ids if item in members]
        for left, right in zip(ordered, ordered[1:]):
            relations.append({
                "source": f"guideline:{left}", "target": f"guideline:{right}",
                "type": "shares_topic", "topic": key,
            })
    library = {
        "schema_version": LIBRARY_SCHEMA,
        "graph_version": GRAPH_VERSION,
        "generated_at": date.today().isoformat(),
        "manifest": str(manifest.get("profile") or "") if manifest else "",
        "guidelines": list(graphs),
        "topic_nodes": nodes,
        "relations": relations,
        "stats": {
            "guidelines": len(graphs),
            "recommendations": sum(int(graph.get("stats", {}).get("recommendations", 0)) for graph in graphs),
            "conditions": sum(int(graph.get("stats", {}).get("conditions", 0)) for graph in graphs),
            "exceptions": sum(int(graph.get("stats", {}).get("exceptions", 0)) for graph in graphs),
            "timeframes": sum(int(graph.get("stats", {}).get("timeframes", 0)) for graph in graphs),
            "chunks": sum(int(graph.get("stats", {}).get("chunks", 0)) for graph in graphs),
            "relations": len(relations),
        },
        "review_status": "extracted",
        "publishable": False,
    }
    errors = validate_library(library)
    library["validation"] = {"errors": errors, "warnings": _library_warnings(library)}
    return library


def validate_library(library: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for graph in library.get("guidelines") or ():
        for issue in _validate_graph(graph):
            if issue.startswith("ERROR:"):
                errors.append(f"{graph.get('guideline_id')}: {issue[6:].strip()}")
    allowed_topics = {str(node["id"]) for node in library.get("topic_nodes") or ()}
    guideline_ids = {f"guideline:{graph.get('guideline_id')}" for graph in library.get("guidelines") or ()}
    for relation in library.get("relations") or ():
        source = str(relation.get("source") or "")
        target = str(relation.get("target") or "")
        if source.startswith("guideline:") and source not in guideline_ids:
            errors.append(f"relation source not found: {source}")
        if target.startswith("guideline:") and target not in guideline_ids:
            errors.append(f"relation target not found: {target}")
        if target.startswith("topic:") and target not in allowed_topics:
            errors.append(f"relation topic not found: {target}")
    return errors


def load_manifest(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_chunks(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _is_recommendation(text: str, section_text: str) -> bool:
    lowered = text.lower()
    if _is_meta_chunk(lowered):
        return False
    if any(marker in section_text for marker in _RECOMMENDATION_SECTIONS):
        return _best_sentence(text) is not None
    return _best_sentence(text) is not None


def _best_sentence(text: str) -> str | None:
    strong = (
        "we recommend", "we suggest", "it is recommended", "is recommended",
        "should be", "should receive", "should not", "推荐意见", "建议", "推荐",
        "可考虑", "应给予", "不应",
    )
    meta = (
        "recommendations were developed", "formed .* recommendations",
        "推荐意见进行分级", "形成.*条推荐意见", "指南制订方法", "指南制定方法",
    )
    for sentence in _sentences(text):
        lowered = sentence.lower()
        if len(sentence) < 20 or len(sentence) > 600:
            continue
        if any(marker in lowered for marker in meta):
            continue
        if _looks_like_heading(sentence):
            continue
        if re.search(r"推荐意见\s*\d+\s*[:：]", sentence):
            return sentence
        if any(cue in lowered for cue in strong) and not _looks_like_heading(sentence):
            return sentence
    return None


def _looks_like_heading(sentence: str) -> bool:
    stripped = sentence.strip()
    if re.fullmatch(r"[\d.\s、]+", stripped):
        return True
    if re.search(r"(临床问题|推荐意见及推荐说明|指南制订方法|指南制定方法)\s*[\d.、\s]*$", stripped):
        return True
    return bool(re.fullmatch(r"[^：:]{0,40}(推荐意见|建议)\s*[\d.、\s]*", stripped))


def _is_meta_chunk(lowered: str) -> bool:
    markers = (
        "author information", "authorinformation", "email:", "e-mail", "abstract",
        "keywords:", "references", "bibliography", "doi:", "https://doi",
        "copyright", "license", "this guideline represents", "we believe",
        "recommendations were developed", "recommendation was developed",
        "www.", "指南制订方法", "指南制定方法", "证据分级", "推荐意见进行分级",
        "文献筛选", "德尔菲", "作者信息", "通信作者", "版权",
    )
    return any(marker in lowered for marker in markers)


def _statement(text: str) -> str:
    sentence = _best_sentence(text)
    return re.sub(r"\s+", " ", sentence or text).strip()[:600]


def _evidence_level(chunk: Mapping[str, Any], text: str) -> str:
    for key in ("evidence_level", "recommendation_grade"):
        value = str(chunk.get(key) or "").strip()
        if value:
            return value
    match = _GRADE.search(text)
    return match.group(1) if match else "not_reported"


def _actions(text: str) -> list[str]:
    lowered = text.lower()
    found = [name for name, terms in _ACTION_TERMS if any(term in lowered for term in terms)]
    return [f"action:{name}" for name in dict.fromkeys(found)]


def _operations(text: str) -> list[str]:
    lowered = text.lower()
    found = [name for name, terms in _OPERATIONS.items() if any(term in lowered for term in terms)]
    return list(dict.fromkeys(found))


def _conditions(text: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for match in _NUMBER_UNIT.finditer(text):
        operator = (match.group(1) or "").strip()
        value = match.group(2)
        unit = (match.group(3) or "").strip()
        if not unit and not operator:
            continue
        context = text[max(0, match.start() - 40):match.end() + 20]
        lowered = context.lower()
        if any(marker in lowered for marker in ("page ", "doi", "pmid", "vol.", "figure ", "table ")):
            continue
        if re.search(r"\b(19|20)\d{2}\b", context):
            continue
        field = _field_for(context)
        normalized_unit = _UNIT_MAP.get(unit.lower(), unit)
        if not field and normalized_unit in {"%", "day", "hour", "week", "min"}:
            if normalized_unit == "%" and not any(
                    marker in lowered for marker in ("saturation", "spo2", "fio2", "oxygen", "血氧", "氧浓度")):
                continue
            if normalized_unit in {"day", "hour", "week", "min"} and not any(
                    marker in lowered for marker in ("生后", "出生", "入院", "after birth", "postnatal", "within")):
                continue
        results.append({
            "raw_text": context.strip(),
            "field": field or "unmapped",
            "mapping_status": "mapped" if field else "unmapped",
            "operator": _OPERATOR_MAP.get(operator, "eq" if operator == "=" else "unknown"),
            "value": _number(value),
            "unit": normalized_unit,
            "review_status": "extracted",
        })
        if len(results) >= 8:
            break
    return results


def _field_for(context: str) -> str:
    lowered = context.lower()
    for field, terms in _FIELD_TERMS:
        if any(term.lower() in lowered for term in terms):
            return f"{_FIELD_PREFIX[field]}.{field}"
    return ""


def _exceptions(text: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for sentence in _sentences(text):
        lowered = sentence.lower()
        kind = ""
        terms: list[str] = []
        for term in _HARD_EXCEPTION_CUES:
            if term in lowered:
                kind = "hard"
                terms.append(term)
                break
        if not kind:
            for term in _NEGATIVE_EXCEPTION_CUES:
                if term in lowered:
                    kind = "negative"
                    terms.append(term)
                    break
        if not kind:
            for term in _RELATED_EXCEPTION_CUES:
                if term in lowered and any(marker in lowered for marker in ("慎用", "警惕", "caution", "related")):
                    kind = "related"
                    terms.append(term)
                    break
        if kind and terms:
            results.append({"kind": kind, "terms": terms, "raw_text": sentence[:400]})
        if len(results) >= 4:
            break
    return results


def _timeframes(text: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for match in _TIME_CUE.finditer(text):
        results.append({
            "anchor": match.group(1),
            "amount": _number(match.group(2)),
            "unit": _UNIT_MAP.get(match.group(3).lower(), match.group(3).lower()),
            "raw_text": match.group(0),
        })
        if len(results) >= 4:
            break
    return results


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？；])\s*|(?<=[.!?;])\s+|\n+", text)
    return [re.sub(r"\s+", " ", part).strip() for part in parts if part.strip()]


def _number(value: str) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _locator(chunk: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": str(chunk.get("chunk_id") or ""),
        "page_start": chunk.get("page_start"),
        "page_end": chunk.get("page_end"),
        "section_path": list(chunk.get("section_path") or ()),
        "source_file": Path(str(chunk.get("source_file") or "")).name,
    }


def _validate_graph(graph: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    node_ids = {str(node.get("id")) for node in graph.get("nodes") or ()}
    if len(node_ids) != len(graph.get("nodes") or ()):
        issues.append("ERROR: duplicate node ids")
    for edge in graph.get("edges") or ():
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in node_ids or target not in node_ids:
            issues.append(f"ERROR: dangling edge {source} -> {target}")
    for node in graph.get("nodes") or ():
        if node.get("type") == "recommendation":
            if not any(edge.get("source") == node["id"] and edge.get("type") == "supported_by"
                       for edge in graph.get("edges") or ()):
                issues.append(f"ERROR: recommendation without chunk: {node['id']}")
            source = node.get("source") or {}
            if not source.get("chunk_id") or (not source.get("page_start") and not source.get("section_path")):
                issues.append(f"WARNING: recommendation has no page/section locator: {node['id']}")
        if node.get("type") == "condition" and node.get("field") == "unmapped":
            issues.append(f"WARNING: condition field unmapped: {node['id']}")
    return issues


def _library_warnings(library: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    for graph in library.get("guidelines") or ():
        for issue in _validate_graph(graph):
            if issue.startswith("WARNING:"):
                warnings.append(f"{graph.get('guideline_id')}: {issue[8:].strip()}")
    return warnings


def _topic_key(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return text


def write_json(path: str | Path, payload: Mapping[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def summarize(graphs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter()
    for graph in graphs:
        for key, value in (graph.get("stats") or {}).items():
            counts[key] += int(value)
    return {"guidelines": len(graphs), **dict(counts)}


def load_guideline_graph_library(path: str | Path | None = None) -> dict[str, Any]:
    """Load the latest generated real-guideline graph library."""
    target = Path(path) if path else DEFAULT_LIBRARY_PATH
    if not target.is_file():
        raise FileNotFoundError(f"guideline graph library not found: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if payload.get("schema_version") != LIBRARY_SCHEMA:
        raise ValueError(f"unsupported guideline graph library schema: {payload.get('schema_version')!r}")
    return payload


def guideline_graph_summary(library: Mapping[str, Any]) -> dict[str, Any]:
    """Small, safe summary for an HTTP/UI caller (no node payload)."""
    return {
        "schema_version": library.get("schema_version"),
        "graph_version": library.get("graph_version"),
        "generated_at": library.get("generated_at"),
        "review_status": library.get("review_status"),
        "publishable": library.get("publishable"),
        "stats": dict(library.get("stats") or {}),
        "guidelines": [
            {
                "guideline_id": graph.get("guideline_id"),
                "title": graph.get("title"),
                "version": graph.get("version"),
                "stats": dict(graph.get("stats") or {}),
                "warnings": len(graph.get("warnings") or ()),
            }
            for graph in library.get("guidelines") or ()
        ],
    }
