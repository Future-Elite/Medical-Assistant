# Running the integrated Medical Agent

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
preloaded before the Gradio port opens. The script enables offline Hugging Face
loading; the weights must already exist in the specified cache. Missing weights
cause an explicit startup error. Qwen and PubMed still need network connectivity.

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
