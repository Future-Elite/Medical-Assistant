"""Adapter to main's RAG v6.6 API, retaining the complete evidence package."""

import json
import os
import re
from dataclasses import replace
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
from medrax.answer_audit import QwenClaimTranslator
from medrax.query_converter import QwenQueryConverter
from medrax.medical_session import ACTIVE_TURN
from .evidence import EvidenceAggregator


class MedicalEvidenceRAGInput(BaseModel):
    question: str = Field(..., min_length=2, description="Original medical question; retain its original language")
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


class MedicalEvidenceRAGTool(BaseTool):
    name: str = "retrieve_medical_evidence"
    description: str = (
        "Retrieve medical evidence through the unified RAG: PubMed and official guideline "
        "metadata in live mode, registered knowledge libraries in custom mode. The caller's "
        "HIS snapshot is automatically injected when loaded. Preserve the original Chinese "
        "question. For insufficient results, change source filters, top_k or configured "
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

    def _run(self, question, top_k=8, as_of=None, source_kinds=None,
             clinical_intent=None, retrieval_method="bm25", rerank=False, fidelity_gate="warn"):
        turn = ACTIVE_TURN.get()
        if turn:
            turn.consume(self.name)
        terms = reviewed_terms()
        original = turn.question if turn else question.strip()
        methods = RetrievalRequest.parse_methods(retrieval_method)
        if re.search(r"[\u3400-\u9fff]", original) and "translate" not in methods:
            methods = (*methods, "translate")
        request = {
            "request_id": f"agent-{uuid4().hex}",
            "question": original,
            "top_k": top_k, "user_terms": terms,
            "source_kinds": source_kinds or [], "clinical_intent": clinical_intent,
            "retrieval_method": ",".join(methods), "rerank": rerank,
            "fidelity_gate": fidelity_gate, "source_mode": os.getenv("RAG_SOURCE_MODE", "live"),
        }
        cutoff = turn.session.as_of if turn else as_of
        if cutoff:
            request["as_of"] = cutoff
        if turn and turn.session.snapshot:
            request["patient_snapshot"] = turn.session.snapshot
        # Log the effective request without persisting the complete patient snapshot.
        tool_args = {key: value for key, value in request.items() if key != "patient_snapshot"}
        try:
            package = _get_rag().retrieve_evidence(request)
        except Exception as exc:
            artifact = {"query": request["question"], "evidence": [], "count": 0,
                        "tool_args": tool_args, "error": str(exc)}
            if turn:
                turn.retrieval_errors.append(str(exc))
                turn.emit({"event": "evidence_search", "artifact": artifact, "tool_name": self.name})
            return "RAG failed. No citable evidence was retrieved; report the error or retry within budget.", artifact

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
                    "evidence": evidence, "tool_args": tool_args}
        if turn:
            turn.session.retrievals[package["retrieval_id"]] = artifact
            turn.emit({"event": "evidence_search", "artifact": artifact, "tool_name": self.name})
        context = {key: artifact.get(key) for key in (
            "retrieval_id", "patient_hits", "patient_context", "applicability", "warnings", "trace"
        )}
        content = EvidenceAggregator.format_for_model(evidence) if evidence else "No citable external evidence found."
        return content + "\nRAG context: " + json.dumps(context, ensure_ascii=False), artifact
