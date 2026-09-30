"""Small agent-facing tool surface with retrieval-scoped citation interaction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .audit import GroundedAnswerAuditor, MiniCheckVerifier
from .models import EvidencePackage, RetrievalRequest
from .pipeline import EvidenceRetrievalPipeline


@dataclass
class EvidenceSessionStore:
    """Replace with Redis/database storage in a multi-process Agent deployment."""

    _packages: dict[str, EvidencePackage] = field(default_factory=dict)

    def put(self, package: EvidencePackage) -> None:
        self._packages[package.retrieval_id] = package

    def get(self, retrieval_id: str) -> EvidencePackage:
        try:
            return self._packages[retrieval_id]
        except KeyError:
            raise ValueError("unknown retrieval_id; retrieve evidence before opening a citation") from None


class EvidenceRAG:
    """JSON tools intended for function-calling agents, not an answer generator."""

    def __init__(self, pipeline: EvidenceRetrievalPipeline, sessions: EvidenceSessionStore | None = None,
                 auditor: GroundedAnswerAuditor | None = None) -> None:
        self.pipeline = pipeline
        self.sessions = sessions or EvidenceSessionStore()
        self.auditor = auditor or GroundedAnswerAuditor(MiniCheckVerifier())

    def retrieve_evidence(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        request = RetrievalRequest.from_dict(arguments)
        package = self.pipeline.retrieve(request)
        self.sessions.put(package)
        return package.to_dict()

    def open_citation(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        package = self.sessions.get(str(arguments.get("retrieval_id") or ""))
        citation_id = str(arguments.get("citation_id") or "")
        citation = next((item for item in package.citations if item.citation_id == citation_id), None)
        if citation is None:
            raise ValueError("citation_id does not belong to this retrieval")
        return {"retrieval_id": package.retrieval_id, "citation": {
            "citation_id": citation.citation_id, "title": citation.title, "quote": citation.quote,
            "key_evidence_sentences": list(citation.key_evidence_sentences),
            "locator": citation.locator, "url": citation.url, "pmid": citation.pmid, "doi": citation.doi,
            "actions": list(citation.actions),
        }}

    def audit_grounded_answer(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Audit answer claims against citations from exactly one retrieval."""
        package = self.sessions.get(str(arguments.get("retrieval_id") or ""))
        return self.auditor.audit(package=package, answer=str(arguments.get("answer") or ""))

    @staticmethod
    def tool_specifications() -> list[dict[str, Any]]:
        return [
            {"name": "retrieve_evidence", "description": "检索可引用的指南、PubMed 与已注册证据来源；不生成诊疗结论。",
             "parameters": {"type": "object", "required": ["question"], "properties": {
                 "request_id": {"type": "string"}, "question": {"type": "string"},
                 "task": {"type": "string"}, "as_of": {"type": "string", "description": "YYYY-MM-DD"},
                 "patient_terms": {"type": "array", "items": {"type": "string"}},
                 "source_kinds": {"type": "array", "items": {"type": "string"}},
                 "filters": {"type": "object"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
                 "user_terms": {"type": "object", "description": "已审核的术语映射"},
             }}},
            {"name": "open_citation", "description": "打开本次检索中一条引文的原文定位与可执行证据动作。",
             "parameters": {"type": "object", "required": ["retrieval_id", "citation_id"], "properties": {
                 "retrieval_id": {"type": "string"}, "citation_id": {"type": "string"},
             }}},
            {"name": "audit_grounded_answer", "description": "以本次检索证据审计回答的主张与引用；不生成或修改诊疗结论。",
             "parameters": {"type": "object", "required": ["retrieval_id", "answer"], "properties": {
                 "retrieval_id": {"type": "string"}, "answer": {"type": "string"},
             }}},
        ]
