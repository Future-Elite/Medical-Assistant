"""Evidence retrieval pipeline with source/version-aware trace."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from . import __version__
from .connectors import ConnectorRegistry
from .evidence import extract_key_evidence_sentences
from .his import PatientSnapshot
from .models import ApplicabilityAssessment, Citation, EvidencePackage, RetrievalRequest
from .patient_retrieval import retrieve_patient_facts
from .query import build_query_plan
from .ranking import rank_documents


class EvidenceRetrievalPipeline:
    def __init__(self, registry: ConnectorRegistry, *, semantic_retriever=None, reranker=None, evidence_budget: int = 3,
                 neural_retrievers: dict | None = None):
        """``neural_retrievers`` maps method name -> SemanticRetriever-style adapter
        for dense/splade/colbert/query2doc. ``semantic_retriever`` stays supported
        as the legacy single-dense slot; it is registered under "dense" when no
        explicit dense adapter is present."""
        self.registry = registry
        self.semantic_retriever = semantic_retriever
        self.reranker = reranker
        self.evidence_budget = evidence_budget
        self.neural_retrievers: dict = dict(neural_retrievers or {})
        if semantic_retriever is not None and "dense" not in self.neural_retrievers:
            self.neural_retrievers["dense"] = semantic_retriever
        self.query2doc_expander = self.neural_retrievers.pop("query2doc", None)
        self.translator = self.neural_retrievers.pop("translate", None)

    def retrieve(self, request: RetrievalRequest) -> EvidencePackage:
        retrieval_id = f"retrieval-{uuid4().hex}"
        retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        known_sources = self.registry.describe()
        snapshot = request.patient_snapshot if isinstance(request.patient_snapshot, PatientSnapshot) else None
        patient_hits, patient_trace = retrieve_patient_facts(snapshot, (request.question, *request.patient_terms, *request.english_terms, *request.mesh_terms), top_k=max(request.top_k * 2, 20)) if snapshot else ((), {"patient_context_id": None, "visible_fact_count": 0, "patient_hit_count": 0, "suppressed_future": 0, "warnings": []})
        plan = build_query_plan(request, known_sources)
        documents = []
        warnings: list[str] = list(patient_trace.get("warnings", []))
        connector_trace: list[dict[str, object]] = []
        method_trace: list[dict[str, object]] = []
        for connector in self.registry.selected(plan.source_order):
            try:
                result = connector.search(plan, limit=max(request.top_k * 5, 50))
            except Exception as exc:
                warning = str(exc).strip() or type(exc).__name__
                warnings.append(f"来源 {connector.source_id} 检索失败：{warning}。")
                connector_trace.append({"source_id": connector.source_id, "status": "error", "error_type": type(exc).__name__, "error": warning[:500]})
                continue
            documents.extend(result.documents)
            warnings.extend(result.warnings)
            connector_trace.append({"source_id": result.source_id, "status": "ok", "returned": len(result.documents), "kind": list(connector.kinds)})
        explicit_method = request.retrieval_method is not None
        if isinstance(request.retrieval_method, (tuple, list)):
            methods = tuple(str(method) for method in request.retrieval_method if method)
        elif request.retrieval_method:
            methods = (str(request.retrieval_method),)
        else:
            methods = ("bm25",)
        if not methods:
            methods = ("bm25",)
        # 向后兼容：未显式指定方法时，若注册了 dense（含 legacy semantic_retriever），自动并入。
        if not explicit_method and "dense" in self.neural_retrievers and "dense" not in methods:
            methods = (*methods, "dense")
        effective_methods: list[str] = []
        for method in methods:
            if method in ("bm25", "ql", "rm3"):
                effective_methods.append(method)
                method_trace.append({"method": method, "status": "ok", "kind": "lexical"})
            elif method == "query2doc":
                # Query2doc 先扩展查询（伪文档拼接到 variants），再沿用 BM25 打分。
                if self.query2doc_expander is None:
                    warnings.append("检索方法 query2doc 未配置生成器（not_configured）；未执行查询扩展。")
                    method_trace.append({"method": "query2doc", "status": "not_configured"})
                    continue
                try:
                    pseudo_docs = tuple(self.query2doc_expander.expand(plan))
                except Exception as exc:
                    warnings.append(f"检索方法 query2doc 未完成：{type(exc).__name__}。")
                    method_trace.append({"method": "query2doc", "status": "error", "error_type": type(exc).__name__})
                    continue
                if not pseudo_docs:
                    warnings.append("检索方法 query2doc 未生成伪文档（not_configured）；未执行查询扩展。")
                    method_trace.append({"method": "query2doc", "status": "not_configured"})
                    continue
                plan = _with_pseudo_documents(plan, pseudo_docs)
                method_trace.append({"method": "query2doc", "status": "ok", "generated": len(pseudo_docs)})
                if "bm25" not in effective_methods:
                    effective_methods.append("bm25")
                    method_trace.append({"method": "bm25", "status": "ok", "kind": "lexical", "note": "query2doc 伪文档的最终打分器"})
            elif method == "translate":
                # translate：把中文原问题机器翻译为英文查询变体（translate-then-retrieve）。
                # 只追加英文变体参与词面打分；原问题保留，翻译文本与数字不一致提示记入 trace。
                if self.translator is None:
                    warnings.append("检索方法 translate 未配置翻译模型（not_configured）；未执行翻译扩展。")
                    method_trace.append({"method": "translate", "status": "not_configured"})
                    continue
                try:
                    translations = tuple(self.translator.expand(plan))
                except Exception as exc:
                    warnings.append(f"检索方法 translate 未完成：{type(exc).__name__}。")
                    method_trace.append({"method": "translate", "status": "error", "error_type": type(exc).__name__})
                    continue
                if not translations:
                    warnings.append("检索方法 translate 未产生译文（not_configured）；未执行翻译扩展。")
                    method_trace.append({"method": "translate", "status": "not_configured"})
                    continue
                plan = _with_translations(plan, translations)
                entry = {"method": "translate", "status": "ok", "generated": len(translations), "translations": [text[:300] for text in translations]}
                mismatches = _numeric_mismatches(plan.original_question, translations)
                if mismatches:
                    entry["numeric_mismatches"] = mismatches
                    warnings.append("translate 译文的数字/单位与中文原问题不一致（" + "; ".join(mismatches) + "）；翻译扩展可能改写医学实体，请核对检索术语。")
                method_trace.append(entry)
                if "bm25" not in effective_methods:
                    effective_methods.append("bm25")
                    method_trace.append({"method": "bm25", "status": "ok", "kind": "lexical", "note": "translate 英文变体的最终打分器"})
            elif method in ("dense", "xlingual", "splade", "colbert"):
                method_trace.append({"method": method, "status": "pending"})
            else:
                warnings.append(f"未知检索方法 {method}；已忽略。")
                method_trace.append({"method": str(method), "status": "unknown"})
        neural_scores: dict[str, dict[str, float]] = {}
        neural_trace: list[dict[str, object]] = []
        for method in methods:
            if method not in ("dense", "xlingual", "splade", "colbert"):
                continue
            adapter = self.neural_retrievers.get(method)
            entry = next(item for item in method_trace if item.get("method") == method)
            if adapter is None or not documents:
                warnings.append(f"检索方法 {method} 未配置（not_configured）；该路子分数缺失，不静默降级。")
                entry.update({"status": "not_configured", "retriever_id": getattr(adapter, "retriever_id", None)})
                neural_trace.append(entry)
                continue
            # 依赖/模型缺失属于"未配置"，在调用前显式判定（不靠异常兜底）。
            if hasattr(adapter, "available") and not adapter.available:
                reason = str(getattr(adapter, "unavailable_reason", "") or "依赖或模型不可用。")
                warnings.append(f"检索方法 {method} 未配置（not_configured）：{reason}")
                entry.update({"status": "not_configured", "retriever_id": getattr(adapter, "retriever_id", None), "reason": reason[:200]})
                neural_trace.append(entry)
                continue
            try:
                result = adapter.search(plan, tuple(documents), limit=max(request.top_k * 5, 50))
                scores = {str(key): float(value) for key, value in result.scores.items()}
                neural_scores[method] = scores
                if method not in effective_methods:
                    effective_methods.append(method)
                entry.update({
                    "status": "ok",
                    "retriever_id": getattr(result, "retriever_id", getattr(adapter, "retriever_id", type(adapter).__name__)),
                    "retriever_version": getattr(result, "retriever_version", None),
                    "index_snapshot": getattr(result, "index_snapshot", None),
                    "returned": len(scores),
                })
                neural_trace.append(entry)
            except Exception as exc:
                warnings.append(f"检索方法 {method} 未完成：{type(exc).__name__}。")
                entry.update({"status": "error", "retriever_id": getattr(adapter, "retriever_id", None), "error_type": type(exc).__name__})
                neural_trace.append(entry)
        if not effective_methods and not neural_scores:
            # 用户选择的方法全部不可用（未配置/出错）且没有任何神经信号：
            # 回退到 bm25 并明确写入 trace；这不是静默降级，警告已逐条记录。
            effective_methods = ["bm25"]
            if not any(item.get("method") == "bm25" for item in method_trace):
                method_trace.append({"method": "bm25", "status": "fallback"})
        ranked, rank_warnings = rank_documents(plan, documents, as_of=request.as_of, top_k=request.top_k,
                                               methods=tuple(effective_methods) or ("bm25",),
                                               neural_scores=neural_scores)
        warnings.extend(rank_warnings)
        dense_entry = next((item for item in neural_trace if item.get("method") == "dense"), None)
        if dense_entry is not None and dense_entry.get("status") == "ok":
            semantic_trace = {
                "status": "ok",
                "retriever_id": dense_entry.get("retriever_id"),
                "retriever_version": dense_entry.get("retriever_version"),
                "index_snapshot": dense_entry.get("index_snapshot"),
                "returned": dense_entry.get("returned"),
                "methods": neural_trace,
            }
        elif neural_trace:
            semantic_trace = {"status": "not_configured", "retriever_id": None, "methods": neural_trace}
        else:
            semantic_trace = {"status": "not_configured", "retriever_id": None}
        evidence_groups: dict[str, tuple] = {}
        for kind in {item.document.kind for item in ranked}:
            evidence_groups[kind] = tuple(item for item in ranked if item.document.kind == kind)
        applicability = tuple(_assess_applicability(item.document, snapshot, patient_hits) for item in ranked if item.document.kind == "guideline")
        rerank_trace: dict[str, object] = {"requested": bool(request.rerank), "status": "not_requested"}
        if request.rerank and ranked:
            if self.reranker is None:
                warnings.append("请求了 cross-encoder 重排，但 reranker 未配置（not_configured）；未重排。")
                rerank_trace = {"requested": True, "status": "not_configured", "reranker_id": None}
            else:
                try:
                    ranked = list(self.reranker.rank(plan, ranked))[:request.top_k]
                    rerank_trace = {"requested": True, "status": "ok", "reranker_id": getattr(self.reranker, "reranker_id", type(self.reranker).__name__)}
                except Exception as exc:
                    warnings.append(f"reranker 未完成：{type(exc).__name__}。")
                    rerank_trace = {"requested": True, "status": "error", "reranker_id": getattr(self.reranker, "reranker_id", None), "error_type": type(exc).__name__}
        citations = tuple(_citation(retrieval_id, index + 1, item, plan.variants, retrieved_at, self.evidence_budget) for index, item in enumerate(ranked))
        return EvidencePackage(
            retrieval_id=retrieval_id, request_id=request.request_id, query_plan=plan,
            ranked_documents=tuple(ranked), citations=citations, warnings=tuple(warnings),
            patient_hits=tuple(_patient_hit_model(hit) for hit in patient_hits),
            evidence_groups=evidence_groups,
            applicability=applicability,
            source_separation={"status": "grouped", "groups": sorted(evidence_groups)},
            patient_context={"context_id": patient_trace.get("patient_context_id"), "visible_fact_count": patient_trace.get("visible_fact_count", 0), "suppressed_future": patient_trace.get("suppressed_future", 0)},
            trace={
                "rag_version": __version__,
                "source_mode": request.source_mode,
                "retrieved_at": retrieved_at,
                "connector_trace": connector_trace,
                "candidate_count": len(documents), "selected_count": len(ranked),
                "retrieval_method": "+".join(effective_methods) if effective_methods else None,
                "method_trace": method_trace,
                "semantic_trace": semantic_trace, "reranker": getattr(self.reranker, "reranker_id", None) if self.reranker else None,
                "rerank_trace": rerank_trace,
                "generation_performed": False, "clinical_decision_performed": False,
                "query_plan": {"language": plan.query_language, "intent": plan.clinical_intent, "english_terms": list(plan.english_terms), "question_english_terms": list(plan.question_english_terms), "mesh_terms": list(plan.mesh_terms)},
                "patient_context": {
                    "received": snapshot is not None,
                    "context_id": patient_trace.get("patient_context_id"),
                    "visible_fact_count": patient_trace.get("visible_fact_count", 0),
                    "patient_hit_count": patient_trace.get("patient_hit_count", 0),
                    "suppressed_future": patient_trace.get("suppressed_future", 0),
                },
                "external_terms": list(dict.fromkeys((*plan.english_terms, *plan.question_english_terms))),
                "source_separation": {"status": "grouped", "groups": sorted(evidence_groups)},
            },
        )


def _with_pseudo_documents(plan, pseudo_docs: tuple[str, ...]):
    """Query2doc：把生成器产出的伪文档作为额外查询变体并记录扩展轨迹。

    伪文档只参与词面打分（作为 variant），不改动原问题、审核术语与来源过滤。
    """
    expanded_variants = tuple(dict.fromkeys((*pseudo_docs, *plan.variants)))
    trace_entry = {"concept": "query2doc", "terms": [doc[:200] for doc in pseudo_docs], "source": "query2doc_pseudo_document"}
    return plan.__class__(
        original_question=plan.original_question,
        variants=expanded_variants,
        concepts=plan.concepts,
        source_order=plan.source_order,
        filters=plan.filters,
        rationale=(*plan.rationale, "query2doc：已生成伪文档并加入查询变体。"),
        query_language=plan.query_language,
        clinical_intent=plan.clinical_intent,
        facets=plan.facets,
        english_terms=plan.english_terms,
        mesh_terms=plan.mesh_terms,
        expansion_trace=(*plan.expansion_trace, trace_entry),
        question_english_terms=plan.question_english_terms,
    )


def _with_translations(plan, translations: tuple[str, ...]):
    """translate：把中文原问题的机器译文作为额外英文查询变体。

    原问题与审核术语保持不变；译文只增加召回分支并记录扩展轨迹，便于审计
    翻译是否改写疾病、药物、数字或人群条件。
    """
    expanded_variants = tuple(dict.fromkeys((*plan.variants, *translations)))
    trace_entry = {"concept": "translate", "terms": [text[:200] for text in translations], "source": "machine_translation_zh_en"}
    return plan.__class__(
        original_question=plan.original_question,
        variants=expanded_variants,
        concepts=plan.concepts,
        source_order=plan.source_order,
        filters=plan.filters,
        rationale=(*plan.rationale, "translate：中文原问题的英文机器译文已加入查询变体（不替换原问题）。"),
        query_language=plan.query_language,
        clinical_intent=plan.clinical_intent,
        facets=plan.facets,
        english_terms=plan.english_terms,
        mesh_terms=plan.mesh_terms,
        expansion_trace=(*plan.expansion_trace, trace_entry),
        question_english_terms=plan.question_english_terms,
    )


_NUMBER_RE = __import__("re").compile(r"\d+(?:\.\d+)?")


def _numeric_mismatches(original: str, translations: tuple[str, ...]) -> list[str]:
    """检测译文中丢失/新增的数值（如 2月龄→February age 丢失月份语义）。

    只做提示，不阻止检索：词面打分对数值敏感，数字被改写意味着召回可能
    偏离原问题；提示进入 warnings 与 method_trace 供人工核对。
    """
    source_numbers = set(_NUMBER_RE.findall(original))
    findings: list[str] = []
    for text in translations:
        target_numbers = set(_NUMBER_RE.findall(text))
        missing = sorted(source_numbers - target_numbers)
        added = sorted(target_numbers - source_numbers)
        if missing:
            findings.append("译文缺少原问题数字 " + ",".join(missing))
        if added:
            findings.append("译文新增原问题没有的数字 " + ",".join(added))
    return findings


def _citation(retrieval_id: str, index: int, ranked, queries, retrieved_at: str, evidence_budget: int) -> Citation:
    doc = ranked.document
    quote = " ".join(doc.text.split())
    if len(quote) > 360:
        quote = quote[:360].rstrip() + "..."
    locator = doc.locator or str(doc.metadata.get("section") or "") or None
    sentences = extract_key_evidence_sentences(doc.document_id, doc.text, queries, budget=evidence_budget)
    action = {"name": "open_evidence", "label": "查看原文证据", "arguments": {"source_id": doc.source_id, "document_id": doc.document_id, "version_id": doc.version_id, "recommendation_id": doc.recommendation_id, "locator": locator, "url": doc.url}}
    return Citation(
        citation_id=f"{retrieval_id}:cit-{index}", source_id=doc.source_id, document_id=doc.document_id,
        kind=doc.kind, title=doc.title, quote=quote, locator=locator, url=doc.url,
        pmid=doc.pmid, doi=doc.doi, evidence_status=ranked.evidence_status, actions=(action,),
        key_evidence_sentences=tuple(_jsonable_sentence(item) for item in sentences), version_id=doc.version_id,
        recommendation_id=doc.recommendation_id, population=doc.population, retrieved_at=retrieved_at,
        index_snapshot=doc.index_snapshot,
    )


def _jsonable_sentence(sentence) -> dict[str, object]:
    return {"sentence_id": sentence.sentence_id, "sentence_index": sentence.sentence_index, "text": sentence.text, "start": sentence.start, "end": sentence.end, "score": sentence.score, "locator": sentence.locator, "matched_terms": list(sentence.matched_terms)}


def _patient_hit_model(hit) -> "PatientHit":
    from .models import PatientHit
    return PatientHit(
        record_path=hit.record_path, field_path=hit.field_path, collection=hit.collection,
        event_type=hit.event_type, time=hit.time, start=hit.start, end=hit.end,
        matched_terms=hit.matched_terms, score=hit.score, evidence_text=hit.evidence_text,
        source_type=hit.source_type,
    )


def _assess_applicability(document, snapshot, patient_hits) -> ApplicabilityAssessment:
    if snapshot is None:
        return ApplicabilityAssessment(document.document_id, "not_assessed", "未提供患者快照。")
    criteria = document.metadata.get("population_criteria")
    if not isinstance(criteria, dict):
        return ApplicabilityAssessment(document.document_id, "not_assessed", "指南未提供结构化适用人群条件；仅展示原文人群字段。")
    matched: list[str] = []
    missing: list[str] = []
    paths: list[str] = []
    visible_text = " ".join(fact.searchable_text().lower() for fact in snapshot.visible_facts())
    for field, expected in criteria.items():
        values = expected if isinstance(expected, (list, tuple, set)) else (expected,)
        if any(str(value).lower() in visible_text for value in values):
            matched.append(str(field))
            paths.extend(hit.record_path for hit in patient_hits if any(str(value).lower() in hit.evidence_text.lower() for value in values))
        else:
            missing.append(str(field))
    if missing and matched:
        status = "unknown"
        reason = "部分适用条件可在患者事实中找到，部分条件缺少结构化记录。"
    elif missing:
        status = "mismatch"
        reason = "未在当前患者事实中找到指南要求的适用条件；这不是临床不适用结论。"
    else:
        status = "possible_match"
        reason = "结构化条件与当前患者事实存在词面匹配；仍需人工核对原文和临床条件。"
    return ApplicabilityAssessment(document.document_id, status, reason, tuple(sorted(set(matched))), tuple(sorted(set(missing))), tuple(sorted(set(paths))))
