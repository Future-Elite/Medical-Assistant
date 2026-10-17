# Running the integrated Medical Agent

`ANSWER_AUDIT_MODE=observe` is now the default integration mode. HIS/patient-scope
and public/internal citation checks remain blocking. MiniCheck/translation audit
results are logged by a background worker after the response passes the hard
checks, without delaying output or triggering revisions. Each audit uses a frozen
copy of its patient's session and original thread/turn identifiers. Background
events are written only to JSONL, not sent to an ended UI request. Empty/error
retrievals retain the generated patient-record section and display an evidence-gap
notice. MiniCheck loads lazily in the worker. Audits run sequentially; pending
jobs are in memory and may be lost when the service exits.

Set `ANSWER_AUDIT_MODE=strict bash agent/inference.sh` to restore blocking audits
and up to two draft revisions. Logs include `audit_mode`, and observe audit events
include `blocking: false`; final response status is `observed`, never `passed`.

This branch integrates the committed Agent with main's RAG/HIS code. Main has not
been merged or changed. The existing imaging mode remains separately selectable.

Use the existing medrax environment; no new dependency was introduced. Configure
the Agent's untracked `.env` using the example in this directory, or export the
same variables before launch. Do not commit credentials or machine-specific paths.

```bash
MEDRAX_PYTHON=/absolute/path/to/medrax/bin/python \
MINICHECK_CACHE_DIR=/absolute/path/to/minicheck/cache \
bash agent/inference.sh
```

`inference.sh` sets the repository import path and runs from agent/. MiniCheck is
preloaded before the Gradio port opens only in strict mode. The script enables offline Hugging Face
loading; the weights must already exist in the specified cache. Missing weights
cause an explicit startup error in strict mode; observe mode logs loading or
translation failures in the background. Qwen and PubMed still need network connectivity.

The UI accepts a synthetic HIS patient ID and an ISO cutoff date. Leave the ID
empty for literature-only questions. HIS defaults to the read-only synthetic mock
at localhost:18080. Startup checks it and starts an owned mock process if absent;
that process stops when this Agent exits. An already-running service is reused.
Set HIS_BASE_URL (or the second inference.sh argument) to connect a deployed HIS;
an unavailable explicit endpoint fails early rather than silently switching data.
A selected patient is not automatically
read: Qwen calls the read-only HIS tool when patient facts are required.

`bash agent/inference.sh /path/to/minicheck/cache http://127.0.0.1:18080`
passes both settings explicitly. Model-facing RAG arguments no longer include
user_terms. The program supplies reviewed mappings; Chinese questions also use
Qwen-generated English candidate queries through the RAG translate slot before
live recall. Candidates are labelled separately, fidelity-audited, cached and
logged with the actual PubMed query. No additional local translation model is
needed. English questions do not invoke query conversion. A rejected machine
route is removed before recall; warn/downweight keep candidates and report risks.

Live mode provides PubMed and WHO guideline metadata. To use a registered local
knowledge library, set RAG_SOURCE_MODE=custom and RAG_CUSTOM_SOURCES_FILE to a
file following rag/custom_sources.py's library schema.

## Focused queries and follow-up retrieval

The Agent retains the caller's text as `artifact.original_question` and
`tool_args.original_question`. The model supplies `search_query` for the medical
literature topic. The legacy `question` argument is accepted as an alias.
RAG receives this focused text as its `question`; consequently
`query_plan.original_question` means the unexpanded **search query**, not the
complete user instruction. Chinese-to-English conversion and fidelity checks
operate on that focused text. Do not send patient identifiers or HIS-reading
instructions as search terms.

A changed follow-up call must include `evidence_gap` and `adjustment_reason`.
These describe why previous evidence is insufficient and how changing the query,
sources or ranking parameters is expected to help. Missing reasons produce a
`retrieval_rejected` event and no external request. Reason presence is checked;
the program does not certify the model's reasoning as clinically correct.

Identical effective requests within one turn reuse the same evidence package
and citation IDs, emitting `evidence_reused` with `cache_hit=true`. Changed
requests emit `evidence_search`. PubMed candidates can still be reused when the
actual query and candidate limit are unchanged: see
`trace.connector_trace[].cache_hit`. Local ranking runs again for changed
parameters. WHO requests are not cached at this connector layer. Caches are
per-turn and include patient context at the package level; they are not reused
across turns. All tool invocations still consume the three-call budget.
Each tool response and retrieval summary reports the call limit, used calls and
remaining calls, including rejected and reused calls. Three is a maximum, not a
required number of searches. Once exhausted, model middleware removes tools and
requests a final answer using the accumulated evidence, with explicit gaps.
Parallel calls exceeding the LangChain limit receive tool errors instead of
aborting the entire turn. Citation and patient-scope checks still apply to the
final answer.

PubMed executes the first accepted translated query, without OR-ing it with a
broad reviewed-term baseline. With no translated query it uses the reviewed /
question-derived terms as before. To broaden a targeted search, the Agent must
send a separate changed `search_query` and explain the gap. The original and
broadened packages retain separate retrieval IDs and actual connector queries.
No simultaneous broad route is added.

## Checks

From the repository root:

```bash
PYTHONPATH=.:agent python -m unittest discover -s tests -t .
python -m unittest discover -s rag/tests -t .
```

From agent/:

```bash
PYTHONPATH=..:. python -m unittest tests.test_answer_audit tests.test_interface_audit tests.test_rag_tool
```

The integration contract and implementation limits are documented in
`contracts/agent_orchestration.md`. Do not interpret passing offline tests as a
real Qwen/MiniCheck/HIS deployment validation.
