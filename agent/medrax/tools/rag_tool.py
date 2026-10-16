"""Adapter to main's RAG v6.6 API, retaining the complete evidence package."""

import json
import os
import re
from contextlib import nullcontext
from dataclasses import replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Type
from uuid import uuid4

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from rag.agent import EvidenceRAG
from rag import config as rag_config
from rag.audit import MiniCheckVerifier
from rag.server import create_tool
from rag.custom_sources import load_custom_library
from rag.neural_adapters import LocalZhEnTranslator
from rag.models import RetrievalRequest
from rag.pubmed import PUBMED_SEARCH_CACHE
from medrax.answer_audit import QwenClaimTranslator
from medrax.query_converter import QwenQueryConverter
from medrax.medical_session import ACTIVE_TURN
from .evidence import EvidenceAggregator


class MedicalEvidenceRAGInput(BaseModel):
    search_query: Optional[str] = Field(default=None, min_length=2, description="Focused medical literature query, Chinese or English. Exclude HIS-reading instructions and requested article counts. Refine or broaden this query when evidence is insufficient.")
    question: Optional[str] = Field(default=None, min_length=2, description="Legacy alias for search_query. Prefer search_query; the actual user question is retained by the application.")
    publication_from: Optional[str] = Field(default=None, description="Inclusive earliest publication date in YYYY-MM-DD format. Keep dates out of search_query.")
    publication_to: Optional[str] = Field(default=None, description="Inclusive latest publication date in YYYY-MM-DD format. Keep dates out of search_query.")
    evidence_gap: Optional[str] = Field(default=None, description="Required for a changed follow-up retrieval: what was missing or unsuitable in the preceding evidence?")
    adjustment_reason: Optional[str] = Field(default=None, description="Required for a changed follow-up retrieval: what changed and how should it address the gap? Explain any broadening.")
    top_k: int = Field(default=8, ge=1, le=20)
    as_of: Optional[str] = None
    source_kinds: List[Literal["pubmed", "guideline", "drug", "clinical_trial", "local_knowledge"]] = Field(default_factory=list)
    clinical_intent: Optional[Literal["guideline_recommendation", "evidence_update", "drug_safety", "clinical_question"]] = None
    retrieval_method: str = "bm25"
    rerank: bool = False
    fidelity_gate: Literal["off", "audit", "warn", "downweight", "reject"] = "warn"


@lru_cache(maxsize=1)
def _get_rag() -> EvidenceRAG:
    overrides = {field: os.environ[name] for field, name in (
        ("ncbi_email", "NCBI_EMAIL"), ("ncbi_api_key", "NCBI_API_KEY"),
    ) if os.getenv(name)}
    if overrides:
        rag_config.CONFIG = replace(rag_config.get_config(), **overrides)
    mode = os.getenv("RAG_SOURCE_MODE", "live")
    library = None
    if mode == "custom":
        path = os.getenv("RAG_CUSTOM_SOURCES_FILE")
        if not path:
            raise ValueError("custom mode requires RAG_CUSTOM_SOURCES_FILE")
        library = load_custom_library(path)
    rag = create_tool(mode, custom_library=library)
    # Reuse the RAG translate/fidelity path with the configured Qwen service;
    # no additional Marian model is required on this machine.
    rag.pipeline.translator = LocalZhEnTranslator(
        translate=_get_query_converter().convert, expander_id="qwen-query-conversion",
    )
    rag.auditor.verifier = MiniCheckVerifier(
        model_name=os.getenv("MINICHECK_MODEL", "flan-t5-large"),
        cache_dir=os.getenv("MINICHECK_CACHE_DIR") or None,
    )
    return rag


@lru_cache(maxsize=1)
def _get_claim_translator():
    return QwenClaimTranslator()


@lru_cache(maxsize=1)
def _get_query_converter():
    return QwenQueryConverter()


def reviewed_terms():
    path = Path(os.getenv("MEDICAL_TERMS_FILE") or str(Path(__file__).resolve().parents[2] / "config" / "medical_terms.json"))
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _publication_range(publication_from, publication_to):
    values = []
    for name, value in (("publication_from", publication_from), ("publication_to", publication_to)):
        value = str(value or "").strip()
        if not value:
            values.append(None)
            continue
        try:
            values.append(date.fromisoformat(value).isoformat())
        except ValueError as exc:
            raise ValueError(f"{name} must use YYYY-MM-DD format") from exc
    if values[0] and values[1] and values[0] > values[1]:
        raise ValueError("publication_from must not be later than publication_to")
    return tuple(values)


def _summarize_retrieval_history(turn):
    returned_by_source = {}
    unique_selected = {}
    for attempt in turn.retrieval_history:
        for connector in attempt["connectors"]:
            source = connector.get("source_id") or "unknown"
            returned_by_source[source] = returned_by_source.get(source, 0) + int(connector.get("returned") or 0)
        for record in attempt["selected_sources"]:
            unique_selected[record["source_id"]] = record["source_type"]
    unique_selected_by_source = {}
    for kind in unique_selected.values():
        unique_selected_by_source[kind] = unique_selected_by_source.get(kind, 0) + 1
    return {
        "attempt_count": len(turn.retrieval_history),
        "any_connector_results_returned": any(returned_by_source.values()),
        "returned_across_attempts_by_source": returned_by_source,
        "unique_selected_count": len(unique_selected),
        "unique_selected_by_source": unique_selected_by_source,
        "attempts": [dict(attempt) for attempt in turn.retrieval_history],
        "tool_call_budget": {
            "limit": turn.limits["retrieve_medical_evidence"],
            "used": turn.counts.get("retrieve_medical_evidence", 0),
            "remaining": max(0, turn.limits["retrieve_medical_evidence"]
                             - turn.counts.get("retrieve_medical_evidence", 0)),
            "rejected_and_reused_calls_count": True,
        },
    }


def _record_retrieval_summary(turn, artifact):
    """Build the authoritative, turn-wide retrieval history shown to the model."""
    trace = artifact.get("trace") or {}
    connectors = [{key: row.get(key) for key in (
        "source_id", "kind", "status", "query", "returned", "error_type",
    ) if row.get(key) is not None} for row in trace.get("connector_trace", [])]
    selected_by_source = {}
    selected_sources = []
    for record in artifact.get("evidence", []):
        kind = record.get("source_type") or "unknown"
        selected_by_source[kind] = selected_by_source.get(kind, 0) + 1
        if record.get("source_id"):
            selected_sources.append({"source_id": record["source_id"], "source_type": kind})
    turn.retrieval_history.append({
        "attempt": turn.retrieval_attempts,
        "retrieval_id": artifact.get("retrieval_id"),
        "search_query": artifact.get("search_query") or artifact.get("query"),
        "publication_from": (artifact.get("tool_args") or {}).get("publication_from"),
        "publication_to": (artifact.get("tool_args") or {}).get("publication_to"),
        "connectors": connectors,
        "candidate_count": trace.get("candidate_count", 0),
        "selected_count": trace.get("selected_count", len(artifact.get("evidence", []))),
        "selected_by_source": selected_by_source,
        "selected_sources": selected_sources,
        "error": artifact.get("error"),
    })
    return _summarize_retrieval_history(turn)


def _with_retrieval_summary(content, turn, summary=None):
    if not turn:
        return content
    summary = summary or _summarize_retrieval_history(turn)
    remaining = summary["tool_call_budget"]["remaining"]
    instruction = (
        "Retrieval budget exhausted: generate the final answer from existing evidence. "
        "Do not call tools again. State evidence gaps explicitly."
        if remaining == 0 else
        f"{remaining} retrieval tool calls remain; this is a maximum, not a target. "
        "If existing evidence answers the question, stop retrieving and answer now. "
        "For a changed follow-up query, supply evidence_gap and adjustment_reason "
        "and target one specific gap."
    )
    return (
        "TURN RETRIEVAL SUMMARY (program-generated and authoritative; later empty attempts "
        "do not erase earlier results):\n" + json.dumps(summary, ensure_ascii=False) +
        "\nRETRIEVAL BUDGET INSTRUCTION:\n" + instruction +
        "\nCURRENT TOOL RESULT:\n" + content
    )


class MedicalEvidenceRAGTool(BaseTool):
    name: str = "retrieve_medical_evidence"
    description: str = (
        "Retrieve medical evidence through the unified RAG: PubMed and official guideline "
        "metadata in live mode, registered knowledge libraries in custom mode. The caller's "
        "HIS snapshot is automatically injected when loaded. Supply a focused search_query; "
        "the application retains the original user question separately. For insufficient results, "
        "use publication_from/publication_to for date limits instead of putting years or PubMed syntax in search_query. "
        "provide evidence_gap and adjustment_reason and change search_query, source filters, top_k or configured "
        "retrieval methods; do not treat rank scores as evidence quality. Metadata-only "
        "guidelines cannot support detailed recommendations. Check warnings/applicability."
    )
    args_schema: Type[BaseModel] = MedicalEvidenceRAGInput
    response_format: Literal["content_and_artifact"] = "content_and_artifact"

    def audit_grounded_answer(self, retrieval_id, answer, claim_records=None):
        return _get_rag().audit_grounded_answer({
            "retrieval_id": retrieval_id, "answer": answer, "claim_records": claim_records,
        })

    def preload_auditor(self):
        verifier = _get_rag().auditor.verifier
        if not verifier.available:
            raise RuntimeError(verifier.unavailable_reason)

    def translate_claims(self, claims):
        return _get_claim_translator()(claims)

    def _run(self, question=None, top_k=8, as_of=None, source_kinds=None,
             clinical_intent=None, retrieval_method="bm25", rerank=False, fidelity_gate="warn",
             search_query=None, publication_from=None, publication_to=None,
             evidence_gap=None, adjustment_reason=None):
        turn = ACTIVE_TURN.get()
        if turn:
            turn.consume(self.name)
        # Tool workers may run concurrently; serialize the check-and-fill operation.
        with turn._retrieval_lock if turn else nullcontext():
            return self._retrieve(question, top_k, as_of, source_kinds, clinical_intent,
                                  retrieval_method, rerank, fidelity_gate,
                                  search_query, publication_from, publication_to,
                                  evidence_gap, adjustment_reason)

    def _retrieve(self, question, top_k, as_of, source_kinds, clinical_intent,
                  retrieval_method, rerank, fidelity_gate, search_query,
                  publication_from, publication_to, evidence_gap, adjustment_reason):
        turn = ACTIVE_TURN.get()
        terms = reviewed_terms()
        effective = " ".join(str(search_query or question or "").split())
        original = turn.question if turn else str(question or effective)
        if not effective:
            return "Provide a focused search_query before retrieving.", {"error": "search_query_required"}
        publication_from, publication_to = _publication_range(publication_from, publication_to)
        methods = RetrievalRequest.parse_methods(retrieval_method)
        if re.search(r"[\u3400-\u9fff]", effective) and "translate" not in methods:
            methods = (*methods, "translate")
        request = {
            "request_id": f"agent-{uuid4().hex}",
            "question": effective,
            "top_k": top_k, "user_terms": terms,
            "source_kinds": source_kinds or [], "clinical_intent": clinical_intent,
            "retrieval_method": ",".join(methods), "rerank": rerank,
            "fidelity_gate": fidelity_gate, "source_mode": os.getenv("RAG_SOURCE_MODE", "live"),
        }
        publication_filters = {
            key: value for key, value in (
                ("publication_from", publication_from), ("publication_to", publication_to),
            ) if value
        }
        if publication_filters:
            request["filters"] = publication_filters
        cutoff = turn.session.as_of if turn else as_of
        if cutoff:
            request["as_of"] = cutoff
        if turn and turn.session.snapshot:
            request["patient_snapshot"] = turn.session.snapshot
        # Log the effective request without persisting the complete patient snapshot.
        tool_args = {key: value for key, value in request.items() if key != "patient_snapshot"}
        tool_args.update(original_question=original, search_query=effective,
                         publication_from=publication_from, publication_to=publication_to,
                         evidence_gap=evidence_gap, adjustment_reason=adjustment_reason)
        # Reason text and request UUID do not change retrieval semantics.
        fingerprint = {k: v for k, v in request.items() if k != "request_id"}
        fingerprint["source_kinds"] = sorted(set(fingerprint["source_kinds"]))
        fingerprint["retrieval_method"] = sorted(methods)
        cache_key = json.dumps(fingerprint, sort_keys=True, ensure_ascii=False, default=str)
        if turn and cache_key in turn.retrieval_cache:
            content, cached = turn.retrieval_cache[cache_key]
            artifact = {**cached, "cache_hit": True, "tool_args": tool_args,
                        "reused_retrieval_id": cached["retrieval_id"]}
            artifact["turn_retrieval_summary"] = _summarize_retrieval_history(turn)
            turn.emit({"event": "evidence_reused", "artifact": artifact, "tool_name": self.name})
            message = "Identical request: reused existing evidence; no new retrieval. Change the query or filters if support is missing.\n" + content
            return _with_retrieval_summary(message, turn, artifact["turn_retrieval_summary"]), artifact
        if turn and turn.retrieval_attempts and not (
            str(evidence_gap or "").strip() and str(adjustment_reason or "").strip()
        ):
            artifact = {"error": "retrieval_adjustment_required", "tool_args": tool_args}
            artifact["turn_retrieval_summary"] = _summarize_retrieval_history(turn)
            turn.emit({"event": "retrieval_rejected", "artifact": artifact, "tool_name": self.name})
            message = "Before another retrieval, supply evidence_gap and adjustment_reason explaining the changed query or parameters. No retrieval was executed."
            return _with_retrieval_summary(message, turn, artifact["turn_retrieval_summary"]), artifact
        if turn:
            turn.retrieval_attempts += 1
        token = PUBMED_SEARCH_CACHE.set(turn.pubmed_cache if turn else None)
        try:
            package = _get_rag().retrieve_evidence(request)
        except Exception as exc:
            artifact = {"query": request["question"], "evidence": [], "count": 0,
                        "tool_args": tool_args, "original_question": original,
                        "search_query": effective, "error": str(exc)}
            if turn:
                turn.retrieval_errors.append(str(exc))
                artifact["turn_retrieval_summary"] = _record_retrieval_summary(turn, artifact)
                turn.emit({"event": "evidence_search", "artifact": artifact, "tool_name": self.name})
            message = "RAG failed. No citable evidence was retrieved; report the error or retry within budget."
            return _with_retrieval_summary(message, turn, artifact.get("turn_retrieval_summary")), artifact
        finally:
            PUBMED_SEARCH_CACHE.reset(token)

        citations = {item["citation_id"]: item for item in package.get("citations", [])}
        records = []
        for ranked in package.get("ranked_documents", []):
            document = ranked["document"]
            citation_id = ranked.get("citation_id")
            citation = citations.get(citation_id)
            if citation is None:
                raise ValueError("ranked document is missing its stable citation foreign key")
            pmid = str(document.get("pmid") or citation.get("pmid") or "")
            source_id = f"PMID:{pmid}" if pmid else f"RAG_CITATION:{citation_id}"
            records.append({
                "source_type": document["kind"], "source_id": source_id,
                "title": document["title"], "content": document.get("text") or citation.get("quote"),
                "date": document.get("published_at"), "url": document.get("url"),
                "metadata": {**(document.get("metadata") or {}),
                             "retrieval_id": package["retrieval_id"], "citation_id": citation_id,
                             "document_id": document["document_id"], "score": ranked.get("score"),
                             "version_id": citation.get("version_id"),
                             "evidence_status": citation.get("evidence_status"),
                             "locator": citation.get("locator")},
            })
        evidence = EvidenceAggregator.aggregate(records)
        artifact = {**package, "query": request["question"], "count": len(evidence),
                    "evidence": evidence, "tool_args": tool_args,
                    "original_question": original, "search_query": effective, "cache_hit": False}
        if turn:
            artifact["turn_retrieval_summary"] = _record_retrieval_summary(turn, artifact)
            turn.session.retrievals[package["retrieval_id"]] = artifact
            turn.emit({"event": "evidence_search", "artifact": artifact, "tool_name": self.name})
        context = {key: artifact.get(key) for key in (
            "retrieval_id", "original_question", "search_query", "patient_hits", "patient_context", "applicability", "warnings", "trace"
        )}
        content = EvidenceAggregator.format_for_model(evidence) if evidence else "No citable external evidence found."
        content = content + "\nRAG context: " + json.dumps(context, ensure_ascii=False)
        if turn:
            turn.retrieval_cache[cache_key] = (content, artifact)
        return _with_retrieval_summary(content, turn, artifact.get("turn_retrieval_summary")), artifact
