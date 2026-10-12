"""LangChain adapter for the repository's unified medical evidence RAG."""

from functools import lru_cache
from typing import Any, Dict, List, Literal, Optional, Tuple, Type
from uuid import uuid4

from langchain_core.callbacks import CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from rag.agent import EvidenceRAG
from rag.runtime import create_evidence_rag

from medrax.answer_audit import QwenClaimTranslator

from .evidence import EvidenceAggregator


class MedicalEvidenceRAGInput(BaseModel):
    question: str = Field(..., min_length=2, description="Medical evidence question")
    top_k: int = Field(default=8, ge=1, le=20, description="Maximum evidence records")
    as_of: Optional[str] = Field(default=None, description="Evidence cutoff date, YYYY-MM-DD")
    user_terms: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Optional reviewed Chinese-to-English biomedical term mapping",
    )


@lru_cache(maxsize=1)
def _get_rag() -> EvidenceRAG:
    return create_evidence_rag()


@lru_cache(maxsize=1)
def _get_claim_translator() -> QwenClaimTranslator:
    return QwenClaimTranslator()


class MedicalEvidenceRAGTool(BaseTool):
    """Expose the repository RAG as the Agent's single external-evidence tool."""

    name: str = "retrieve_medical_evidence"
    description: str = (
        "Retrieve citable external medical evidence through the unified RAG. "
        "The RAG currently searches PubMed and will incorporate additional registered "
        "evidence connectors later. Use for biomedical studies, reviews, guidelines, "
        "treatment efficacy, and disease mechanisms."
    )
    args_schema: Type[BaseModel] = MedicalEvidenceRAGInput
    response_format: Literal["content_and_artifact"] = "content_and_artifact"

    def audit_grounded_answer(self, retrieval_id: str, answer: str, claim_records=None) -> Dict[str, Any]:
        """Audit a draft against evidence retained by this tool's RAG session."""
        return _get_rag().audit_grounded_answer({
            "retrieval_id": retrieval_id,
            "answer": answer,
            "claim_records": claim_records,
        })

    def preload_auditor(self) -> None:
        verifier = _get_rag().auditor.verifier
        if not verifier.available:
            raise RuntimeError(getattr(verifier, "unavailable_reason", "MiniCheck unavailable"))

    def translate_claims(self, claims: List[str]) -> List[str]:
        """Translate cited claims with Qwen before MiniCheck verification."""
        return _get_claim_translator()(claims)

    def _run(
        self,
        question: str,
        top_k: int = 8,
        as_of: Optional[str] = None,
        user_terms: Optional[Dict[str, List[str]]] = None,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        query = question.strip()
        request = {
            "request_id": f"agent-{uuid4().hex}",
            "question": query,
            "top_k": top_k,
            "user_terms": user_terms or {},
        }
        if as_of:
            request["as_of"] = as_of
        tool_args = {
            "question": query,
            "top_k": top_k,
            "as_of": as_of,
            "user_terms": user_terms or {},
        }

        try:
            package = _get_rag().retrieve_evidence(request)
        except Exception as exc:
            artifact = {
                "query": query,
                "count": 0,
                "evidence": [],
                "tool_args": tool_args,
                "error": str(exc),
            }
            return "The medical evidence RAG failed; no evidence was retrieved.", artifact

        citations = {
            str(item.get("document_id") or ""): item
            for item in package.get("citations", [])
        }
        records = []
        for ranked in package.get("ranked_documents", []):
            document = ranked.get("document") or {}
            citation = citations.get(str(document.get("document_id") or ""), {})
            pmid = str(document.get("pmid") or citation.get("pmid") or "").strip()
            citation_id = str(citation.get("citation_id") or "").strip()
            source_id = f"PMID:{pmid}" if pmid else (
                f"RAG_CITATION:{citation_id}" if citation_id else ""
            )
            if not source_id:
                continue
            records.append({
                "source_type": str(document.get("kind") or "rag"),
                "source_id": source_id,
                "title": document.get("title") or "Medical evidence",
                "content": str(document.get("text") or citation.get("quote") or "")[:6000],
                "date": document.get("updated_at") or document.get("published_at") or "",
                "url": document.get("url") or citation.get("url"),
                "metadata": {
                    "retrieval_id": package.get("retrieval_id"),
                    "citation_id": citation_id or None,
                    "document_id": document.get("document_id"),
                    "rag_source_id": document.get("source_id"),
                    "doi": document.get("doi") or citation.get("doi"),
                    "locator": citation.get("locator") or document.get("locator"),
                    "score": ranked.get("score"),
                },
            })

        evidence = EvidenceAggregator.aggregate(records)
        artifact = {
            "query": query,
            "count": len(evidence),
            "evidence": evidence,
            "tool_args": tool_args,
            "retrieval_id": package.get("retrieval_id"),
            "query_plan": package.get("query_plan"),
            "warnings": package.get("warnings", []),
            "trace": package.get("trace", {}),
        }
        if not evidence:
            warnings = " ".join(str(item) for item in artifact["warnings"])
            return f"The medical evidence RAG returned no citable evidence. {warnings}".strip(), artifact
        return EvidenceAggregator.format_for_model(evidence), artifact
