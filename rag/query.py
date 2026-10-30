"""Transparent, controlled bilingual query planning for the RAG package."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .his import PatientSnapshot, minimize_patient_snapshot
from .models import QueryPlan, RetrievalRequest, TermMappingEntry
from .term_mapping_library import MAPPING_ORIGIN_LIBRARY, MAPPING_ORIGIN_REQUEST

DEFAULT_SOURCE_ORDER = ("guideline", "pubmed", "drug", "clinical_trial", "local_knowledge")
_RECOMMENDATION_MARKERS = ("指南", "推荐", "建议", "应当", "适应证", "禁忌", "剂量", "治疗", "guideline", "recommend")
_UPDATE_MARKERS = ("更新", "最新", "update", "recent", "new evidence")
# 问题内英文词会先过滤常见功能词（what/is/the/...）与不含字母的纯数值；
# 调用方显式提供的 english_terms 不做任何过滤（那是已审核术语）。
_QUESTION_EN_STOPWORDS = frozenset(
    """a an and are as at be been being both but by can could did do does doing down during each few for from
    further had has have having he her here hers him his how i if in into is it its just may me might more most
    my no nor not of off on once only or other our out over own same she should so some such than that the
    their them then there these they this those through to too under until up very was we were what when where
    which while who whom why will with would you your""".split()
)


@dataclass(frozen=True)
class _MappingExpansion:
    entries: tuple[TermMappingEntry, ...] = ()
    concepts: tuple[str, ...] = ()
    english: tuple[str, ...] = ()
    mesh: tuple[str, ...] = ()
    trace: tuple[Mapping[str, Any], ...] = ()
    unmatched: tuple[str, ...] = ()


def build_query_plan(request: RetrievalRequest, known_sources: list[dict[str, object]], library=None) -> QueryPlan:
    """Build the query plan.

    ``library`` (optional :class:`~rag.term_mapping_library.TermMappingLibrary`)
    adds reusable mapping rows: request ``term_mapping`` rows win on concept
    collision, library rows cover concepts the request did not supply. Library
    expansions are traced with ``origin=library`` and never masquerade as
    caller-reviewed rows. With no library the plan is identical to the
    request-only path.
    """
    reviewed_external = minimize_patient_snapshot(request.patient_snapshot) if isinstance(request.patient_snapshot, PatientSnapshot) else ()
    segments = _input_segments(request, reviewed_external)
    extracted = (*_extract_terms(request.question), *request.patient_terms, *reviewed_external, *_facet_values(request.facets))
    request_text = " ".join(text for _, text in segments).lower()
    request_concepts = {entry.concept.casefold() for entry in request.term_mapping}
    library_rows = tuple(getattr(library, "entries", ()) or ())
    added_library_rows = tuple(entry for entry in library_rows if entry.concept.casefold() not in request_concepts)
    mapping = _expand_term_mapping(request.term_mapping, segments, trace_source="reviewed_term_mapping", origin=MAPPING_ORIGIN_REQUEST)
    library_mapping = _expand_term_mapping(
        added_library_rows, segments, trace_source="term_mapping_library", origin=MAPPING_ORIGIN_LIBRARY,
    ) if added_library_rows else _MappingExpansion()
    reviewed_concepts = tuple(
        key for key in request.user_terms
        if str(key).strip() and str(key).lower() in request_text
    )
    concepts = _unique((*extracted, *mapping.concepts, *library_mapping.concepts, *reviewed_concepts))
    english: list[str] = [*mapping.english, *library_mapping.english, *request.english_terms]
    mesh: list[str] = [*mapping.mesh, *library_mapping.mesh, *request.mesh_terms]
    question_english = _question_english_terms(request.question)
    expansion_trace: list[Mapping[str, Any]] = [*mapping.trace, *library_mapping.trace]
    if question_english:
        expansion_trace.append({"concept": "question", "terms": list(question_english), "source": "question_ascii"})
    mapped_concepts = {entry.concept.casefold() for entry in (*mapping.entries, *library_mapping.entries) if entry.english}
    for concept in concepts:
        if concept.casefold() in mapped_concepts:
            continue
        mapped = tuple(_strings(request.user_terms.get(concept, ())))
        if mapped:
            english.extend(mapped)
            expansion_trace.append({"concept": concept, "terms": list(mapped), "source": "reviewed_user_terms"})
    english = list(_unique(english))
    mesh = list(_unique(mesh))
    effective_mapping_entries = (*mapping.entries, *library_mapping.entries)
    if effective_mapping_entries:
        variants, variant_trace = _mapping_query_variants(
            request.question, concepts, english, mesh, effective_mapping_entries,
        )
    else:
        variants = _unique((request.question, " ".join(concepts), " ".join((*concepts, *english)), " ".join((*english, *mesh))))
        variant_trace = ()

    requested = request.source_kinds or DEFAULT_SOURCE_ORDER
    source_order: list[str] = []
    for kind in requested:
        for source in known_sources:
            source_id = str(source["source_id"])
            kinds = {str(value) for value in source.get("kinds", [])}
            if (kind in kinds or kind == source_id) and source_id not in source_order:
                source_order.append(source_id)
    if not source_order:
        source_order = [str(item["source_id"]) for item in known_sources]

    question_lower = request.question.lower()
    recommendation = any(marker in question_lower for marker in _RECOMMENDATION_MARKERS)
    update = any(marker in question_lower for marker in _UPDATE_MARKERS)
    guideline_sources = [
        str(item["source_id"]) for item in known_sources
        if "guideline" in {str(value) for value in item.get("kinds", [])}
    ]
    if recommendation and guideline_sources and not request.source_kinds:
        source_order = guideline_sources + [item for item in source_order if item not in guideline_sources]
    rationale = ["保留原始问题作为精确查询。", "仅扩展调用方提供的审核术语、英文词和 MeSH 词。"]
    if question_english:
        rationale.append("问题中的英文词已直接加入英文检索词（已过滤常见功能词与纯数值）。")
    rationale.append("问题中的中文片段用于中文来源匹配；中文概念展开为英文仅使用已审核的术语映射，不自动翻译。")
    if mapping.entries:
        rationale.append(
            f"审核术语映射表命中 {len(mapping.entries)} 个概念；英文与 MeSH 由同一映射条目派生，未命中概念不展开。"
        )
        rationale.append("查询变体按概念分组生成（原始问题/中文概念/概念英文/概念 MeSH/概念组合/全局组合），保留概念—术语绑定。")
    elif request.term_mapping:
        rationale.append("提供了 term_mapping，但没有任何概念出现在问题/术语文本中；未展开映射术语。")
    if library_mapping.entries:
        rationale.append(
            f"术语映射库命中 {len(library_mapping.entries)} 个概念（来源 {getattr(library, 'source', '') or 'library'}）；"
            "库行与请求行同构展开、请求优先，provenance 见 mapping_trace.origin。"
        )
    if recommendation:
        rationale.append("问题含推荐/指南线索，优先路由指南来源；这不是绝对正确性排序。")
    if update:
        rationale.append("问题含更新线索，保留时间窗口并同时检索指南版本与 PubMed 新证据。")
    if request.patient_terms:
        rationale.append("患者相关词仅作为最小化术语，不发送原始病历。")
    if request.facets:
        rationale.append("临床问题槽位以结构化 facets 保留，避免把人群、结局和时间压成不可审计长句。")
    filters = dict(request.filters)
    filters["source_kinds"] = list(request.source_kinds)
    filters["as_of"] = request.as_of
    return QueryPlan(
        original_question=request.question,
        variants=tuple(item for item in variants if item.strip()),
        concepts=concepts,
        source_order=tuple(source_order),
        filters=filters,
        rationale=tuple(rationale),
        query_language=request.query_language,
        clinical_intent=request.clinical_intent or _infer_intent(request.question),
        facets={key: tuple(value) for key, value in request.facets.items()},
        english_terms=tuple(english),
        mesh_terms=tuple(mesh),
        expansion_trace=tuple(expansion_trace),
        term_mapping=(*request.term_mapping, *added_library_rows),
        mapping_trace=(*mapping.trace, *library_mapping.trace),
        variant_trace=variant_trace,
        question_english_terms=question_english,
    )


def _input_segments(request: RetrievalRequest, reviewed_external: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    segments: list[tuple[str, str]] = [("question", request.question)]
    segments.extend(("patient_terms", item) for item in request.patient_terms)
    segments.extend(("reviewed_external_terms", item) for item in reviewed_external)
    for key, value in request.facets.items():
        segments.extend((f"facets.{key}", item) for item in _strings(value))
    return tuple((source, text) for source, text in segments if str(text).strip())


def _expand_term_mapping(
    entries: tuple[TermMappingEntry, ...],
    segments: tuple[tuple[str, str], ...],
    *,
    trace_source: str = "reviewed_term_mapping",
    origin: str = MAPPING_ORIGIN_REQUEST,
) -> _MappingExpansion:
    expanded: list[TermMappingEntry] = []
    concepts: list[str] = []
    english: list[str] = []
    mesh: list[str] = []
    trace: list[Mapping[str, Any]] = []
    unmatched: list[str] = []
    # legacy 冻结：请求行的 trace 形状与 v6.6.5 逐字段一致，只给库行加 origin。
    origin_fields = {"origin": origin} if origin == MAPPING_ORIGIN_LIBRARY else {}
    for entry in entries:
        hit = _first_surface_match(entry.surfaces(), segments)
        if hit is None:
            unmatched.append(entry.concept)
            trace.append({
                "concept": entry.concept, "status": "not_matched",
                "english": list(entry.english), "mesh": list(entry.mesh),
                "aliases": list(entry.aliases), **origin_fields,
            })
            continue
        surface, matched_in = hit
        expanded.append(entry)
        concepts.append(entry.concept)
        english.extend(entry.english)
        mesh.extend(entry.mesh)
        trace.append({
            "concept": entry.concept, "status": "expanded",
            "matched_surface": surface, "matched_in": matched_in,
            "terms": list(entry.english), "mesh_terms": list(entry.mesh),
            "aliases": list(entry.aliases), "source": trace_source, **origin_fields,
        })
    return _MappingExpansion(
        entries=tuple(expanded), concepts=tuple(concepts),
        english=tuple(english), mesh=tuple(mesh),
        trace=tuple(trace), unmatched=tuple(unmatched),
    )


def _first_surface_match(
    surfaces: tuple[str, ...], segments: tuple[tuple[str, str], ...],
) -> tuple[str, str] | None:
    for surface in surfaces:
        for source, text in segments:
            if _contains_surface(surface, text):
                return surface, source
    return None


def _contains_surface(surface: str, text: str) -> bool:
    """Concept presence check.

    CJK surfaces use substring matching; ASCII surfaces use token boundaries so
    an alias such as ``RDS`` does not match inside ``NRDS``.
    """
    needle = str(surface or "").strip()
    haystack = str(text or "")
    if not needle or not haystack:
        return False
    if needle.isascii():
        pattern = r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])"
        return re.search(pattern, haystack, re.IGNORECASE) is not None
    return needle.lower() in haystack.lower()


def _mapping_query_variants(
    question: str,
    concepts: tuple[str, ...],
    english: tuple[str, ...],
    mesh: tuple[str, ...],
    entries: tuple[TermMappingEntry, ...],
) -> tuple[tuple[str, ...], tuple[Mapping[str, Any], ...]]:
    """Build concept-scoped query variants for the reviewed mapping path.

    Keeping a variant per concept (and its English/MeSH products) preserves the
    concept-term binding; BM25 still takes the best variant per document, so a
    concept variant cannot lower an existing document's score.
    """
    variants: list[str] = []
    trace: list[Mapping[str, Any]] = []
    seen: set[str] = set()

    def add(role: str, text: str, concept: str | None = None) -> None:
        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned:
            return
        key = cleaned.casefold()
        if key in seen:
            return
        seen.add(key)
        variants.append(cleaned)
        entry: dict[str, Any] = {"role": role, "text": cleaned}
        if concept:
            entry["concept"] = concept
        trace.append(entry)

    add("original_question", question)
    add("zh_concepts", " ".join(concepts))
    for entry in entries:
        add("mapping_english", " ".join(entry.english), entry.concept)
        add("mapping_mesh", " ".join(entry.mesh), entry.concept)
        add("mapping_combined", " ".join((*entry.english, *entry.mesh)), entry.concept)
    add("english_terms", " ".join(english))
    add("mesh_terms", " ".join(mesh))
    add("english_and_mesh", " ".join((*english, *mesh)))
    add("concepts_and_terms", " ".join((*concepts, *english, *mesh)))
    return tuple(variants), tuple(trace)


def _infer_intent(question: str) -> str:
    lower = question.lower()
    recommendation = any(marker in lower for marker in _RECOMMENDATION_MARKERS)
    update = any(marker in lower for marker in _UPDATE_MARKERS)
    safety = any(marker in lower for marker in ("安全", "不良反应", "禁忌", "adverse", "safety"))
    # 同时出现"推荐/指南"与"最新/更新"线索时（如“指南推荐和最新证据是什么？”），
    # 以指南推荐为主动作、PubMed 新证据为辅助：联合检索时指南来源保留主名额。
    if recommendation and update:
        return "guideline_recommendation"
    if update:
        return "evidence_update"
    if recommendation:
        return "guideline_recommendation"
    if safety:
        return "drug_safety"
    return "clinical_question"


# ---------------------------------------------------------------------------
# PRESS-style query review (检索式自检)
# ---------------------------------------------------------------------------
# 依据 PRESS（McGowan et al. 2016, DOI 10.1016/j.jclinepi.2016.01.021）与
# Bramer et al. (JMLA 2018) 的检索式检查要点：把问题概念、主题词、自由词、
# 条件与数值逐项对照检索计划。"检索式无报错"不等于"检索正确"，因此以下检查
# 只报告发现，不修改检索计划；结果写入 trace.query_review 供人工核对。
_NEGATION_MARKERS = ("未记录", "没有", "无", "否认", "阴性", "排除", "not recorded", "no evidence", "denies", "negative")
_NUMERIC_RE = re.compile(r"\d+(?:\.\d+)?")


def review_query_plan(plan: QueryPlan, *, english_source_present: bool) -> dict[str, Any]:
    """PRESS 风格检索式自检：逐项检查概念映射、数值、否定与英文覆盖。

    返回 ``{"status": "ok"|"review_needed", "checks": [...], "findings": [...]}``。
    自检不改变检索计划，只把人工应复核的点显式列出（这不是检索质量评分）。
    """
    findings: list[str] = []
    checks: list[dict[str, Any]] = []
    question = plan.original_question

    # 1) 问题概念→英文覆盖：完全无英文术语时，英文来源只能靠 CJK 二元组（通常零命中）。
    has_english = bool(plan.english_terms or plan.question_english_terms or plan.machine_translations)
    checks.append({
        "item": "english_coverage",
        "status": "ok" if has_english or not english_source_present else "review_needed",
        "detail": (
            f"英文检索词 {len(plan.english_terms)} 个（审核）+ {len(plan.question_english_terms)} 个（问题内提取）+ {len(plan.machine_translations)} 个机器生成查询（未人工审核）"
            if has_english else "无任何英文检索词。"
        ),
    })
    if english_source_present and not has_english:
        findings.append(
            "检索式没有任何英文词：英文来源（PubMed）只可能得到零命中或不可靠结果；"
            "请补充 english_terms/user_terms 审核映射，或改用 xlingual/m3 等跨语言方法。"
        )

    # 2) 审核映射：记录实际生效的展开来源（问题内英文词 / 审核映射 / RM3 / query2doc）。
    mapped_sources = sorted({str(entry.get("source")) for entry in plan.expansion_trace})
    checks.append({
        "item": "expansion_sources",
        "status": "ok",
        "detail": "；".join(mapped_sources) or "未发生术语展开。",
    })
    if english_source_present and not plan.english_terms and not plan.question_english_terms and not plan.machine_translations and "reviewed_user_terms" not in mapped_sources and "reviewed_term_mapping" not in mapped_sources:
        findings.append(
            "审核映射未展开：user_terms/term_mapping 的键必须与问题/患者术语原文一致（含大小写）才会生效；"
            "请核对键的写法，或直接提供 english_terms。"
        )

    # 2b) 映射表覆盖：未命中的概念不展开，必须显式列出而不是静默丢弃。
    # 但映射库是词库（收几十个概念），未命中是常态——库行只计数不刷警告；
    # 请求自带的手写行未命中才可能是键写错，逐条列出。无库时文案与旧版逐字段一致。
    mapping_trace = tuple(getattr(plan, "mapping_trace", ()) or ())
    if mapping_trace:
        expanded = [str(entry.get("concept")) for entry in mapping_trace if entry.get("status") == "expanded"]
        unmatched_entries = [entry for entry in mapping_trace if entry.get("status") != "expanded"]
        library_rows = sum(1 for entry in mapping_trace if entry.get("origin") == "library")
        if library_rows:
            unmatched = [
                str(entry.get("concept")) for entry in unmatched_entries
                if entry.get("origin", "request") == "request"
            ]
            unmatched_library = len(unmatched_entries) - len(unmatched)
            checks.append({
                "item": "term_mapping_coverage",
                "status": "review_needed" if unmatched else "ok",
                "detail": f"映射行：命中 {len(expanded)}，请求行未命中 {len(unmatched)}"
                          + (f"（{', '.join(unmatched)}）" if unmatched else "")
                          + f"；库行未命中 {unmatched_library}/{library_rows}（词库常态，不提示）",
            })
        else:
            unmatched = [str(entry.get("concept")) for entry in unmatched_entries]
            checks.append({
                "item": "term_mapping_coverage",
                "status": "review_needed" if unmatched else "ok",
                "detail": f"映射表概念：命中 {len(expanded)}，未命中 {len(unmatched)}"
                          + (f"（未命中：{', '.join(unmatched)}）" if unmatched else ""),
            })
        if unmatched:
            findings.append(
                "术语映射表中以下概念未出现在问题/患者术语/facets 中，未展开："
                + ", ".join(unmatched)
                + "；若这是有意提供的备选概念可忽略，否则请核对键的写法。"
            )

    # 3) 数值一致性：问题中的数值必须有英文表述承载（否则年龄/剂量/阈值会丢失）。
    numbers = _NUMERIC_RE.findall(question)
    if numbers:
        numbers_in_english = {token for term in (*plan.english_terms, *plan.mesh_terms) for token in _NUMERIC_RE.findall(term)}
        combined = " ".join((*plan.english_terms, *plan.mesh_terms, *plan.concepts, *plan.machine_translations))
        missing = [value for value in sorted(set(numbers)) if value not in combined and value not in numbers_in_english]
        checks.append({
            "item": "numeric_preservation",
            "status": "review_needed" if missing else "ok",
            "detail": f"问题数值：{', '.join(sorted(set(numbers)))}",
        })
        if missing:
            findings.append(f"问题中的数值 {', '.join(missing)} 未出现在任何英文/概念检索词中；英文侧检索可能丢失该条件。")

    # 4) 否定/时间条件：中文否定标记存在时，确认检索词未把它改写成肯定命题。
    negation = [marker for marker in _NEGATION_MARKERS if marker in question.lower()]
    checks.append({
        "item": "negation_and_conditions",
        "status": "ok" if not negation else "review_needed",
        "detail": f"检测到否定/缺失类表述：{', '.join(dict.fromkeys(negation))}" if negation else "未检测到否定类表述。",
    })
    if negation:
        findings.append(
            "问题包含否定/缺失类表述（如“未记录”“没有证据”）：系统只按词面检索，不能把“缺记录”当作阴性事实；"
            "请在解读结果时保留这一条件。"
        )

    # 5) 重复/冲突术语：同一术语既作为英文词又作为 MeSH 出现（无害但应显式）。
    overlap = sorted({term.lower() for term in plan.english_terms} & {term.lower() for term in plan.mesh_terms})
    checks.append({
        "item": "term_overlap",
        "status": "ok",
        "detail": f"英文术语与 MeSH 重叠：{', '.join(overlap)}" if overlap else "英文术语与 MeSH 无重叠。",
    })

    return {
        "status": "review_needed" if findings else "ok",
        "checks": checks,
        "findings": findings,
        "note": "检索式自检只报告需人工复核的点，不修改检索计划，也不表示检索质量结论。",
    }


def _facet_values(facets: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for value in facets.values():
        values.extend(_strings(value))
    return tuple(values)


def _extract_terms(text: str) -> tuple[str, ...]:
    ascii_terms = re.findall(r"[A-Za-z][A-Za-z0-9+./-]{1,}", text)
    cjk_terms = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    return _unique((*ascii_terms, *cjk_terms))


def _question_english_terms(question: str) -> tuple[str, ...]:
    """问题中可直接用于英文检索的词。

    规则：≥2 字符、含至少一个字母（排除 "38.9" 这类数值）、不在功能词表内；
    去重后保持出现顺序。仅作用于问题文本；调用方审核术语不做过滤。
    """
    words = re.findall(r"[A-Za-z][A-Za-z0-9+./-]{1,}", question)
    kept = [
        word.lower() for word in words
        if any(ch.isalpha() for ch in word) and word.lower() not in _QUESTION_EN_STOPWORDS
    ]
    return _unique(kept)


_GENERIC_QUERY_TERMS = frozenset({
    "guideline", "guidelines", "recommendation", "recommendations",
    "evidence", "management", "treatment", "therapy", "diagnosis",
    "clinical", "patient", "study", "review", "update", "question",
})

_GENERIC_POPULATION_TERMS = frozenset({
    "neonatal", "newborn", "newborn infant", "infant", "preterm", "preterm infant",
    "premature", "premature infant", "low birth weight infant",
    "very low birth weight infant", "extremely low birth weight infant",
})

_UNIT_PATTERN = re.compile(
    r"^(?:[<>]=?\s*)?\d*(?:\.\d+)?\s*"
    r"(?:mmol|mol|µmol|μmol|umol|mg|g|kg|ml|dl|l|mmhg|cmh2o|bpm|°c|%)"
    r"(?:/(?:l|dl|ml|kg|min))?$",
    re.IGNORECASE,
)


def is_unit_term(term: str) -> bool:
    """True for measurement units such as ``mmol/L`` or ``2.1 mmol/L``.

    Numeric conditions are not indexed as text in PubMed; making a unit a
    required term turns a valid clinical question into zero hits.
    """
    text = str(term or "").strip()
    return bool(_UNIT_PATTERN.fullmatch(text)) or bool(re.fullmatch(r"[<>]=?\d+(?:\.\d+)?", text))


def is_generic_population(term: str) -> bool:
    return str(term or "").strip().lower() in _GENERIC_POPULATION_TERMS


def reviewed_topic_terms(plan: QueryPlan) -> tuple[str, ...]:
    """Reviewed English/MeSH terms that name a topic, in caller-provided order.

    Callers are expected to put the disease concept first. Population-only terms
    (``infant``, ``low birth weight infant``) and units are removed so they
    cannot displace the disease term in PubMed or guideline topic matching.
    When the caller supplied no English/MeSH fields, fall back to the ASCII
    concepts already present in the plan (snapshot-reviewed terms and question
    English words); this keeps the no-reviewed-terms path usable.
    """
    terms = _unique((*plan.english_terms, *plan.mesh_terms))
    if not terms:
        terms = _unique(
            str(item) for item in plan.concepts
            if str(item).isascii() and not is_unit_term(str(item))
        )
    return tuple(
        term for term in terms
        if not is_unit_term(term)
        and not is_generic_population(term)
        and term.strip().lower() not in _GENERIC_QUERY_TERMS
    )


def primary_topic(plan: QueryPlan) -> str:
    """Prefer a multi-word disease phrase over an acronym or population term.

    ``CPAP`` alone is a therapy, not a disease anchor; ``respiratory distress
    syndrome`` is. Reviewed lists usually put the disease first, but the
    multi-word preference makes the choice robust when they do not.
    """
    terms = reviewed_topic_terms(plan)
    for term in terms:
        if len(term.split()) >= 2:
            return term
    return terms[0] if terms else ""


def topic_synonyms(plan: QueryPlan) -> tuple[str, ...]:
    primary = primary_topic(plan)
    return tuple(term for term in reviewed_topic_terms(plan) if term != primary)


def population_clause(plan: QueryPlan) -> str:
    """PubMed population restriction, only when the plan actually mentions one."""
    text = " ".join((*plan.english_terms, *plan.mesh_terms, plan.original_question or "")).lower()
    if any(token in text for token in ("neonat", "newborn", "infant", "preterm", "新生儿", "早产儿", "婴儿")):
        return "neonatal OR newborn OR infant OR preterm"
    return ""


MAPPING_EXPANSION_SOURCES = ("reviewed_term_mapping", "term_mapping_library")


def mapping_active(plan: QueryPlan) -> bool:
    """True when at least one reviewed or library mapping concept matched."""
    return any(
        isinstance(entry, Mapping) and entry.get("status") == "expanded"
        and entry.get("source") in MAPPING_EXPANSION_SOURCES
        for entry in (plan.mapping_trace or ())
    )


def expanded_mapping_entries(plan: QueryPlan) -> tuple[TermMappingEntry, ...]:
    expanded = {
        str(entry.get("concept") or "").casefold()
        for entry in (plan.mapping_trace or ())
        if isinstance(entry, Mapping) and entry.get("status") == "expanded"
    }
    return tuple(
        entry for entry in (plan.term_mapping or ())
        if entry.concept.casefold() in expanded and (entry.english or entry.mesh)
    )


def primary_mapping_entry(plan: QueryPlan) -> TermMappingEntry | None:
    """The first expanded mapping row with an English or MeSH product."""
    entries = expanded_mapping_entries(plan)
    return entries[0] if entries else None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _unique(items) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = str(item).strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            out.append(cleaned)
            seen.add(key)
    return tuple(out)
