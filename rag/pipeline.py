"""Evidence retrieval pipeline; generation and clinical decision making stay outside."""

from __future__ import annotations

from uuid import uuid4

from .connectors import ConnectorRegistry
from .evidence import extract_key_evidence_sentences
from .models import Citation, EvidencePackage, RetrievalRequest
from .query import build_query_plan
from .ranking import rank_documents


class EvidenceRetrievalPipeline:
    def __init__(self, registry: ConnectorRegistry) -> None:
        self.registry = registry

    def retrieve(self, request: RetrievalRequest) -> EvidencePackage:
        retrieval_id = f"retrieval-{uuid4().hex}"
        known_sources = self.registry.describe()
        plan = build_query_plan(request, known_sources)
        documents = []
        warnings: list[str] = []
        connector_trace: list[dict[str, object]] = []
        for connector in self.registry.selected(plan.source_order):
            try:
                result = connector.search(plan, limit=max(request.top_k * 3, 20))
            except Exception as exc:  # Integration failure must remain visible to the Agent.
                warning = str(exc).strip() or type(exc).__name__
                warnings.append(f"来源 {connector.source_id} 检索失败：{warning}。")
                connector_trace.append({
                    "source_id": connector.source_id,
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": warning[:500],
                    "stage": getattr(exc, "stage", None),
                    "http_status": getattr(exc, "status_code", None),
                })
                continue
            documents.extend(result.documents)
            warnings.extend(result.warnings)
            connector_trace.append({"source_id": result.source_id, "status": "ok", "returned": len(result.documents)})
        ranked, rank_warnings = rank_documents(plan, documents, as_of=request.as_of, top_k=request.top_k)
        warnings.extend(rank_warnings)
        citations = tuple(_citation(retrieval_id, index + 1, item) for index, item in enumerate(ranked))
        return EvidencePackage(
            retrieval_id=retrieval_id, request_id=request.request_id,
            query_plan=plan, ranked_documents=tuple(ranked), citations=citations,
            warnings=tuple(warnings),
            trace={
                "connector_trace": connector_trace,
                "key_sentence_budget": 3,
                "candidate_count": len(documents), "selected_count": len(ranked),
                "generation_performed": False,
                "clinical_decision_performed": False,
            },
        )


def _citation(retrieval_id: str, index: int, ranked) -> Citation:
    doc = ranked.document
    quote = _quote(doc.text)
    locator = doc.locator or str(doc.metadata.get("section") or "") or None
    action = {
        "name": "open_evidence", "label": "查看原文证据",
        "arguments": {"source_id": doc.source_id, "document_id": doc.document_id, "locator": locator, "url": doc.url},
    }
    key_sentences = extract_key_evidence_sentences(
        doc.document_id, doc.text, ranked.matched_queries, budget=3
    )
    return Citation(
        citation_id=f"{retrieval_id}:cit-{index}", source_id=doc.source_id, document_id=doc.document_id,
        kind=doc.kind, title=doc.title, quote=quote, locator=locator, url=doc.url,
        pmid=doc.pmid, doi=doc.doi, evidence_status=ranked.evidence_status, actions=(action,),
        key_evidence_sentences=tuple(sentence.__dict__ for sentence in key_sentences),
    )


def _quote(text: str, limit: int = 360) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."
