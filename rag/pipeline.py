"""Evidence retrieval pipeline with per-source ranking and auditable joint selection.

Retrieval is organized in three stages:

1. **Collect** — every selected connector returns its own candidates (one
   ``SearchResult`` per source, with its own trace entry). Duplicates are then
   removed globally (DOI > PMID > title|kind); a shared key across *different*
   sources is reported as a conflict and never silently merged into one claim.
2. **Rank per source** — each source kind (guideline / pubmed / …) is ranked
   independently with the selected retrieval methods, RRF fusion, the
   relevance gate and MMR. Term statistics (IDF/QL) are therefore computed in
   the source's own vocabulary, and the raw scores of different sources are
   never compared.
3. **Joint selection** — per-source lists are merged with a visibility
   guarantee (every source with relevant candidates keeps at least one slot),
   an intent-based share for the preferred source and merit fill by
   within-source normalized score. The merge is traced in
   ``trace.joint_selection`` and the merged list is interleaved by within-source
   rank so no single source consumes all of ``top_k``.

query fidelity research layer (see the paper's method section): when a request selects a
non-``off`` fidelity gate policy, a :class:`~rag.fidelity.QueryFidelityAuditor`
audits the *query-conversion products* (reviewed mapping terms, machine
translation variants, explicit multilingual terms) before fusion, and a
:class:`~rag.gate.FidelityGate` optionally down-weights or rejects the
affected route's signal. The audit and every decision are traced in
``trace.query_fidelity``; the query plan itself is never silently rewritten
(rejected translation variants are dropped from retrieval input only, and the
fact is explicit in the trace and warnings).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from . import __version__
from .applicability import assess_applicability
from .connectors import ConnectorRegistry
from .evidence import extract_key_evidence_sentences
from .fidelity import FidelityLexicon, QueryFidelityAuditor, ROUTE_LABELS
from .gate import FidelityGate
from .his import PatientSnapshot
from .models import Citation, EvidencePackage, RetrievalRequest
from .patient_retrieval import retrieve_patient_facts
from .query import build_query_plan, review_query_plan
from .ranking import deduplicate_documents, merge_joint_selection, rank_documents

NEURAL_METHOD_SET = ("dense", "xlingual", "m3", "splade", "colbert")
MULTILINGUAL_METHOD_SET = ("xlingual", "m3")


class EvidenceRetrievalPipeline:
    def __init__(self, registry: ConnectorRegistry, *, semantic_retriever=None, reranker=None, evidence_budget: int = 3,
                 neural_retrievers: dict | None = None,
                 fidelity_lexicon: FidelityLexicon | None = None,
                 fidelity_default_policy: str = "off",
                 fidelity_penalty: float = 0.5):
        """``neural_retrievers`` maps method name -> SemanticRetriever-style adapter
        for dense/xlingual/m3/splade/colbert/query2doc/translate. ``semantic_retriever``
        stays supported as the legacy single-dense slot; it is registered under
        "dense" when no explicit dense adapter is present.

        ``fidelity_*`` configures the query-fidelity research layer. The default
        policy is ``off`` (frozen baseline semantics); a request can select
        ``audit`` / ``warn`` / ``downweight`` / ``reject`` per call via
        ``RetrievalRequest.fidelity_gate``. No lexicon is built in: callers pass
        their own :class:`FidelityLexicon` (content licensing is the caller's)."""
        self.registry = registry
        self.semantic_retriever = semantic_retriever
        self.reranker = reranker
        self.evidence_budget = evidence_budget
        self.neural_retrievers: dict = dict(neural_retrievers or {})
        if semantic_retriever is not None and "dense" not in self.neural_retrievers:
            self.neural_retrievers["dense"] = semantic_retriever
        self.query2doc_expander = self.neural_retrievers.pop("query2doc", None)
        self.translator = self.neural_retrievers.pop("translate", None)
        self.fidelity_lexicon = fidelity_lexicon
        self.fidelity_default_policy = str(fidelity_default_policy or "off").lower()
        if self.fidelity_default_policy not in ("off", "audit", "warn", "downweight", "reject"):
            raise ValueError("unknown fidelity_default_policy: " + self.fidelity_default_policy)
        self.fidelity_penalty = float(fidelity_penalty)
        self.fidelity_auditor = QueryFidelityAuditor(lexicon=fidelity_lexicon)

    def retrieve(self, request: RetrievalRequest) -> EvidencePackage:
        retrieval_id = f"retrieval-{uuid4().hex}"
        retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        known_sources = self.registry.describe()
        snapshot = request.patient_snapshot if isinstance(request.patient_snapshot, PatientSnapshot) else None
        patient_hits, patient_trace = retrieve_patient_facts(snapshot, (request.question, *request.patient_terms, *request.english_terms, *request.mesh_terms), top_k=max(request.top_k * 2, 20)) if snapshot else ((), {"patient_context_id": None, "visible_fact_count": 0, "patient_hit_count": 0, "suppressed_future": 0, "warnings": []})
        plan = build_query_plan(request, known_sources)
        selected_connectors = self.registry.selected(plan.source_order)
        english_source_present = any("pubmed" in set(connector.kinds) for connector in selected_connectors)
        documents = []
        warnings: list[str] = list(patient_trace.get("warnings", []))
        warnings.extend(getattr(self.registry, "warnings", ()))
        connector_trace: list[dict[str, object]] = []
        method_trace: list[dict[str, object]] = []
        kind_order: list[str] = []

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
        translations_produced: tuple[str, ...] = ()
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
                translations_produced = tuple(translations)
                plan = _with_translations(plan, translations)
                entry = {"method": "translate", "status": "ok", "generated": len(translations), "translations": [text[:300] for text in translations], "expander_id": getattr(self.translator, "expander_id", type(self.translator).__name__)}
                mismatches = _numeric_mismatches(plan.original_question, translations)
                if mismatches:
                    entry["numeric_mismatches"] = mismatches
                    warnings.append("translate 译文的数字/单位与中文原问题不一致（" + "; ".join(mismatches) + "）；翻译扩展可能改写医学实体，请核对检索术语。")
                method_trace.append(entry)
                if "bm25" not in effective_methods:
                    effective_methods.append("bm25")
                    method_trace.append({"method": "bm25", "status": "ok", "kind": "lexical", "note": "translate 英文变体的最终打分器"})
            elif method in NEURAL_METHOD_SET:
                method_trace.append({"method": method, "status": "pending"})
            else:
                warnings.append(f"未知检索方法 {method}；已忽略。")
                method_trace.append({"method": str(method), "status": "unknown"})
        # Convert and gate before live recall, not just after candidates arrive.
        # A rejected machine route must never be sent to an external connector.
        fidelity_policy = str(request.fidelity_gate or self.fidelity_default_policy or "off").lower()
        recall_fidelity = None
        if translations_produced and fidelity_policy != "off":
            report = self.fidelity_auditor.audit(
                plan, translations=translations_produced, translate_requested=True,
                translate_configured=self.translator is not None, multilingual_methods=(),
            )
            decisions = FidelityGate(policy=fidelity_policy, penalty=self.fidelity_penalty).decide(report)
            recall_fidelity = {"report": report.to_dict(), "decisions": [item.to_dict() for item in decisions]}
            if FidelityGate.variant_weight(decisions, "translate") is None:
                plan = _without_translations(plan, translations_produced)

        query_review = review_query_plan(plan, english_source_present=english_source_present)
        warnings.extend(f"检索式自检：{finding}" for finding in query_review["findings"])
        for connector in selected_connectors:
            for kind in connector.kinds:
                if kind not in kind_order:
                    kind_order.append(kind)
            try:
                result = connector.search(plan, limit=max(request.top_k * 5, 50))
            except Exception as exc:
                warning = str(exc).strip() or type(exc).__name__
                warnings.append(f"来源 {connector.source_id} 检索失败：{warning}。")
                connector_trace.append({"source_id": connector.source_id, "status": "error", "error_type": type(exc).__name__, "error": warning[:500]})
                continue
            documents.extend(result.documents)
            warnings.extend(result.warnings)
            connector_trace.append({"source_id": result.source_id, "status": "ok", "returned": len(result.documents),
                                    "kind": list(connector.kinds), "query": getattr(connector, "last_query", None)})
        deduped, duplicate_count, conflicts = deduplicate_documents(documents)
        if duplicate_count:
            warnings.append(f"已按 DOI/PMID/标题跨来源去重 {duplicate_count} 条候选。")
        if conflicts:
            warnings.append("检测到跨来源共享 DOI/PMID/标题的候选；已保留权威来源，但不能据此消除指南与文献的语义冲突。")

        neural_scores: dict[str, dict[str, float]] = {}
        neural_trace: list[dict[str, object]] = []
        for method in methods:
            if method not in NEURAL_METHOD_SET:
                continue
            adapter = self.neural_retrievers.get(method)
            entry = next(item for item in method_trace if item.get("method") == method)
            if adapter is None or not deduped:
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
                result = adapter.search(plan, tuple(deduped), limit=max(request.top_k * 5, 50))
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

        # --- query fidelity research layer: query-side fidelity audit + gate ---------------
        # 在分源排序之前运行：审计对象是查询转换产物（审核映射/译文/多语言编码显式词），
        # 处置只影响被判定不合格的路线信号；查询计划不被静默改写（被拒绝的译文变体
        # 只从检索输入中移除，且逐条写入 trace 与 warnings）。
        fidelity_report_dict: dict[str, object] | None = None
        fidelity_trace: dict[str, object] = {"policy": fidelity_policy, "applied": False}
        fidelity_variant_weights: dict[str, float] = {}
        fidelity_translate_rejected = False
        if fidelity_policy != "off":
            translate_requested = "translate" in methods
            translate_configured = self.translator is not None
            report = self.fidelity_auditor.audit(
                plan,
                translations=tuple(translations_produced),
                translate_requested=translate_requested,
                translate_configured=translate_configured,
                multilingual_methods=tuple(method for method in methods if method in MULTILINGUAL_METHOD_SET),
            )
            gate = FidelityGate(policy=fidelity_policy, penalty=self.fidelity_penalty)
            decisions = gate.decide(report)
            fidelity_report_dict = report.to_dict()
            fidelity_trace.update({
                "applied": True,
                "decisions": [decision.to_dict() for decision in decisions],
            })
            # Apply to neural scores (xlingual/m3 route).
            neural_scores, adjustments = FidelityGate.apply_to_neural_scores(neural_scores, decisions)
            # Apply to translation variants (translate route): reject drops the
            # variants from the scoring input; downweight scales the lexical
            # contribution of documents whose best-matching variant is a
            # translation (per-variant weights in rank_documents).
            translate_weight = FidelityGate.variant_weight(decisions, "translate")
            if translate_weight is not None and translations_produced:
                if translate_weight != 1.0:
                    for text in translations_produced:
                        fidelity_variant_weights[text] = translate_weight
            elif translate_weight is None and translations_produced:
                fidelity_translate_rejected = True
                plan = _without_translations(plan, translations_produced)
                # translate 路线本身不占 effective_methods 名额（其词面信号记在 bm25）；
                # 标记 method_trace 供审计。
                for item in method_trace:
                    if item.get("method") == "translate":
                        item["fidelity_gate"] = "rejected"
            if adjustments or translate_weight is not None or fidelity_translate_rejected:
                fidelity_trace["signal_adjustments"] = [
                    *adjustments,
                    *([{
                        "route": "translate", "action": "reject" if fidelity_translate_rejected else "downweight",
                        "variants_affected": len(translations_produced),
                        "reason": next((decision.reason for decision in decisions if decision.route == "translate"), ""),
                    }] if translations_produced and translate_weight != 1.0 else []),
                ]
            # Surface gate actions in warnings (auditable; L0/L1 不产生信号变化).
            for decision in decisions:
                if decision.action in ("downweight", "reject"):
                    warnings.append(f"查询保真门控：{ROUTE_LABELS.get(decision.route, decision.route)} 路线 {decision.action}（{decision.level}）——{decision.reason}")
                elif decision.action == "warn":
                    warnings.append(f"查询保真门控（警示）：{ROUTE_LABELS.get(decision.route, decision.route)} 路线发现查询保真风险——{decision.reason}")

        # --- Stage 2: rank each source pool independently ---------------------------
        by_kind: dict[str, list] = defaultdict(list)
        for document in deduped:
            by_kind[document.kind].append(document)
        for kind in sorted(by_kind):
            if kind not in kind_order:
                kind_order.append(kind)
        methods_tuple = tuple(effective_methods) or ("bm25",)
        per_source_ranked: dict[str, list] = {}
        for kind in kind_order:
            pool = by_kind.get(kind) or []
            if not pool:
                continue
            pool_keys = {document.identity_key() for document in pool}
            subset_scores = {
                method: {key: value for key, value in scores.items() if key in pool_keys}
                for method, scores in neural_scores.items()
            }
            ranked_kind, kind_warnings = rank_documents(
                plan, pool, as_of=request.as_of, top_k=request.top_k,
                methods=methods_tuple, neural_scores=subset_scores, source_label=_kind_label(kind),
                variant_weights=fidelity_variant_weights or None,
            )
            per_source_ranked[kind] = ranked_kind
            warnings.extend(kind_warnings)

        # --- Stage 3: joint selection (visibility guarantee + intent share + merit) --
        ranked, joint_trace, joint_warnings = merge_joint_selection(
            per_source_ranked, top_k=request.top_k, intent=plan.clinical_intent, source_order=kind_order,
        )
        warnings.extend(joint_warnings)
        joint_trace["candidates_per_source"] = {kind: len(by_kind.get(kind) or []) for kind in kind_order}
        joint_trace["relevant_per_source"] = {kind: len(per_source_ranked.get(kind) or []) for kind in kind_order}

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
        # 先在每条 ranked 上写 citation_id（稳定外键），再构造 citations：
        # 调用方按 citation_id 关联引用；下标同序只是巧合，不作为接口。
        ranked = [replace(item, citation_id=f"{retrieval_id}:cit-{index + 1}") for index, item in enumerate(ranked)]
        citations = tuple(_citation(retrieval_id, index + 1, item, plan.variants, retrieved_at, self.evidence_budget) for index, item in enumerate(ranked))
        _assert_citation_linkage(ranked, citations)
        # 所有视图（evidence_groups / applicability）都在最终 ranked 上生成，
        # 与 citations 同源；此前 evidence_groups 在 rerank 之前计算，重排后会与 citations 不一致。
        evidence_groups: dict[str, tuple] = {}
        for kind in {item.document.kind for item in ranked}:
            evidence_groups[kind] = tuple(item for item in ranked if item.document.kind == kind)
        applicability = tuple(_assess_applicability(item.document, snapshot, patient_hits) for item in ranked if item.document.kind == "guideline")
        return EvidencePackage(
            retrieval_id=retrieval_id, request_id=request.request_id, query_plan=plan,
            ranked_documents=tuple(ranked), citations=citations, warnings=tuple(dict.fromkeys(warnings)),
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
                "recall_query_fidelity": recall_fidelity,
                "candidate_count": len(documents),
                "deduplicated_count": len(deduped),
                "selected_count": len(ranked),
                "retrieval_method": "+".join(effective_methods) if effective_methods else None,
                "method_trace": method_trace,
                "query_review": query_review,
                "query_fidelity": {
                    **fidelity_trace,
                    "report": fidelity_report_dict,
                },
                "joint_selection": joint_trace,
                "semantic_trace": semantic_trace, "reranker": getattr(self.reranker, "reranker_id", None) if self.reranker else None,
                "rerank_trace": rerank_trace,
                "generation_performed": False, "clinical_decision_performed": False,
                "query_plan": {
                    "language": plan.query_language, "intent": plan.clinical_intent,
                    "english_terms": list(plan.english_terms),
                    "question_english_terms": list(plan.question_english_terms),
                    "mesh_terms": list(plan.mesh_terms),
                    "term_mapping": {
                        "provided": len(plan.term_mapping),
                        "expanded": sum(1 for entry in plan.mapping_trace if entry.get("status") == "expanded"),
                        "unmatched": [str(entry.get("concept")) for entry in plan.mapping_trace if entry.get("status") != "expanded"],
                    },
                    "variant_roles": [str(entry.get("role")) for entry in plan.variant_trace],
                },
                "patient_context": {
                    "received": snapshot is not None,
                    "context_id": patient_trace.get("patient_context_id"),
                    "visible_fact_count": patient_trace.get("visible_fact_count", 0),
                    "patient_hit_count": patient_trace.get("patient_hit_count", 0),
                    "suppressed_future": patient_trace.get("suppressed_future", 0),
                },
                "external_terms": list(dict.fromkeys((*plan.english_terms, *plan.question_english_terms))),
                "machine_queries": list(plan.machine_translations),
                "source_separation": {"status": "grouped", "groups": sorted(evidence_groups)},
            },
        )


def _assert_citation_linkage(ranked, citations) -> None:
    """ranked_documents 与 citations 必须逐条一一对应（稳定外键契约）。

    两者都由管线在同一位置生成；此处用断言把"错配"变成显式错误而不是静默返回，
    防止后续维护在任一列表上插入/过滤元素后破坏关联。
    """
    if len(ranked) != len(citations):
        raise ValueError(
            f"citation linkage broken: {len(ranked)} ranked documents vs {len(citations)} citations"
        )
    for index, (item, citation) in enumerate(zip(ranked, citations)):
        if item.citation_id != citation.citation_id:
            raise ValueError(
                f"citation linkage broken at index {index}: ranked.citation_id={item.citation_id!r} "
                f"vs citation.citation_id={citation.citation_id!r}"
            )


def _kind_label(kind: str) -> str:
    return {"guideline": "指南", "pubmed": "PubMed", "drug": "药品", "clinical_trial": "临床试验", "local_knowledge": "本地知识"}.get(kind, kind)


def _with_pseudo_documents(plan, pseudo_docs: tuple[str, ...]):
    """Query2doc：把生成器产出的伪文档作为额外查询变体并记录扩展轨迹。

    伪文档只参与词面打分（作为 variant），不改动原问题、审核术语与来源过滤。
    """
    expanded_variants = tuple(dict.fromkeys((*pseudo_docs, *plan.variants)))
    trace_entry = {"concept": "query2doc", "terms": [doc[:200] for doc in pseudo_docs], "source": "query2doc_pseudo_document"}
    return replace(
        plan,
        variants=expanded_variants,
        rationale=(*plan.rationale, "query2doc：已生成伪文档并加入查询变体。"),
        expansion_trace=(*plan.expansion_trace, trace_entry),
    )


def _with_translations(plan, translations: tuple[str, ...]):
    """translate：把中文原问题的机器译文作为额外英文查询变体。

    原问题与审核术语保持不变；译文只增加召回分支并记录扩展轨迹，便于审计
    翻译是否改写疾病、药物、数字或人群条件。
    """
    expanded_variants = tuple(dict.fromkeys((*plan.variants, *translations)))
    trace_entry = {"concept": "translate", "terms": [text[:200] for text in translations], "source": "machine_translation_zh_en"}
    return replace(
        plan,
        variants=expanded_variants,
        rationale=(*plan.rationale, "translate：中文原问题的英文机器译文已加入查询变体（不替换原问题）。"),
        expansion_trace=(*plan.expansion_trace, trace_entry),
        machine_translations=tuple(dict.fromkeys((*plan.machine_translations, *translations))),
    )


_NUMBER_RE = __import__("re").compile(r"\d+(?:\.\d+)?")


def _without_translations(plan, translations: tuple[str, ...]):
    """Fidelity gate（翻译路线被拒绝）：从**打分输入**中移除机器译文变体。

    语义说明（防误解）：这不是对查询计划的静默改写——
    - `original_question`、审核术语与 `expansion_trace`（含曾发生的翻译记录）全部保留；
      翻译记录条目上加 `fidelity_gate: rejected` 标记；
    - 只有 `variants`（词面打分实际使用的查询变体集合）移除译文，
      使拒绝动作作用于检索打分；该事实同时写入 `trace.query_fidelity`
      与 warnings，调用方可完整审计"翻译曾被尝试、后被门控拒绝"。
    """
    translated = {text for text in translations if str(text).strip()}
    if not translated:
        return plan
    remaining_variants = tuple(variant for variant in plan.variants if variant not in translated) or (plan.original_question,)
    marked_entries = tuple(
        ({**entry, "fidelity_gate": "rejected"}
         if isinstance(entry, dict) and entry.get("source") == "machine_translation_zh_en" else entry)
        for entry in plan.expansion_trace
    )
    return replace(
        plan,
        variants=remaining_variants,
        rationale=(*plan.rationale, "查询保真门控：translate 路线的机器译文已从检索打分输入中移除（审计记录保留在 expansion_trace）。"),
        expansion_trace=marked_entries,
        machine_translations=tuple(text for text in plan.machine_translations if text not in translated),
    )


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


def _assess_applicability(document, snapshot, patient_hits):
    """Delegate to the structured-criteria evaluator (legacy map preserved)."""
    return assess_applicability(document, snapshot, patient_hits)
