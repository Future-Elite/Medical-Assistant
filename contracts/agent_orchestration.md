# Medical Agent integration contract (draft 0.1.0)

This integration includes RAG 6.7.0 (rerank candidate pool, Qwen3 reranker,
term-mapping library, rule routing and the iterative controller research layer;
all new mechanisms default off) and HIS snapshot contract 0.2.0. It is a
read-only synthetic-data prototype. It does not write HIS records or connect PACS.

## Entry point and ownership

`MedicalAgentOrchestrator.run_turn(question, thread_id, patient_ref="", as_of=None)`
owns the complete loop independently of Gradio. Qwen chooses among
`get_patient_snapshot`, `retrieve_medical_evidence`, and `open_medical_citation`.
The program enforces budgets, scope boundaries, post-draft audit and termination.

The caller selects a synthetic integer patient ID; the model cannot choose an ID.
Changing patient or cutoff rotates the internal graph thread and removes historical
retrieval access. Clear Chat also discards the active session. State is process-local.

HIS is read by `contracts.agent_his_adapter.AgentHisClient`. The unmodified mapped
payload is injected into RAG from trusted session state, never model arguments.
RAG's existing time-filter implementation also filters the view shown to Qwen.
RAG does not connect to HIS. Patient facts remain separate from external literature.

## Retrieval and evidence

RAG construction uses main's `rag.server.create_tool`. Live mode provides PubMed
and WHO guideline metadata, not guideline recommendation full text. Custom mode
loads an explicitly supplied knowledge library. Drug/trial schema kinds do not
imply live openFDA or ClinicalTrials connectors are registered.

The adapter preserves the full package: patient_hits, evidence_groups, citations,
applicability, patient_context, warnings and trace. Citation linkage uses the stable
`ranked_documents[].citation_id` foreign key. Ranking order is retained.

The original user question is retained. The small operator-maintained terminology
table in `agent/config/medical_terms.json` is a starting vocabulary, not a complete
medical dictionary. Model suggestions cannot masquerade as reviewed mappings.
user_terms is not a model-facing parameter. Chinese requests automatically select
the RAG translate route using Qwen as an injected converter. Machine queries are
prepared and fidelity-gated before external recall, retained separately in
QueryPlan.machine_translations, and logged in connector_trace.query. English
questions bypass conversion. Conversion sees the original question, never HIS
snapshot contents. Startup checks the configured HIS endpoint; only the default
synthetic localhost:18080 mock is auto-started and managed by the Agent process.

## Audit and termination

Structured `claim_records` (claim_zh, claim_en, citation_ids) are an optional,
backwards-compatible extension to the RAG Python auditing API in this branch.
Text-based callers continue to work. The published tool schema remains unchanged;
this extension is used by the internal audit path.

Only Chinese claims are translated. English bypasses Qwen translation. An unchanged
translation and an unchanged (claim, evidence-text) check may reuse process-local
results. Each claim retains its complete preceding text through semicolons.

For multi-retrieval answers, citation IDs are resolved against the active session;
each package is audited separately, then checks are combined in original claim order.
At least one supporting cited document is sufficient for a claim. This does not
verify every citation link, clinical safety, or citation completeness. HIS facts
are not passed off as PubMed citations, and MiniCheck here checks external claims.

Limits apply across all rounds of one user turn: 3 retrieval attempts, 1 HIS read,
4 citation opens, 12 model calls, 4 orchestration rounds and 2 draft revisions.
Empty/metadata-only retrievals trigger gap feedback and bounded re-retrieval.
Audit failures may trigger revision and further evidence retrieval within those
budgets. Translation/execution errors are blocked without revising the medical
answer. Exhaustion returns an explicit message, not an unaudited draft.

Evidence sufficiency is currently a basic program guard plus Qwen's prompted
assessment. Semantic conflict resolution, SafetyValidator, CitationCompletenessValidator,
durable memory and a human-review queue are not implemented.

## Logs and verification

JSONL records append thread_id, turn_id, patient_scope, tool arguments, evidence
packages, drafts, claim translations, audit results, timings and final status.
Use synthetic patients only; logs include the filtered patient facts returned to
the model. Raw fetched snapshots are not included in effective tool-argument logs.

Offline integration tests use real tools and the real RAG pipeline with recorded
HIS responses and a deterministic verifier. A scripted chat model also exercises
the actual create_agent/LangGraph tool workers. These tests verify control flow
and contracts, not Qwen quality or MiniCheck's medical accuracy.
