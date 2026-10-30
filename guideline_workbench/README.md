# Guideline Workbench

A small desktop workbench for parsing Chinese medical-guideline PDFs, producing structure-aware chunks, comparing retrieval representations, and querying the parent repository's Medical-Assistant RAG.

The workbench lives in its own directory so it does not replace or alter the repository's existing RAG, Agent, HIS, or root documentation. It imports the repository's `rag/` and `agent/` packages directly.

## Scope

- MinerU converts guideline PDFs to Markdown.
- The chunker compares fixed, recursive, and structure-oriented text chunking, three table representations, and two figure-reference options.
- The evaluation runner uses manually annotated evidence anchors and the repository's BM25 retrieval path.
- The Tkinter GUI exposes guideline processing, RAG retrieval, and evaluation workflows.
- Qwen-backed generated answers are optional. Configure a DashScope key only when using that feature.

This is a research prototype. It is not a clinical decision-support product. The pilot benchmark requires clinical review.

## Setup

Use Python 3.12 for MinerU and install dependencies from the repository root:

```powershell
conda create -n medllm-mineru python=3.12 -y
conda activate medllm-mineru
python -m pip install -r guideline_workbench/requirements.txt
```

Install the optional Agent dependencies only when enabling generated answers:

```powershell
python -m pip install -r guideline_workbench/guideline_processing/agent-requirements.txt
```

For local development, copy `.env.example` to `.env` and set `DASHSCOPE_API_KEY`. The key is ignored by Git. BM25 retrieval does not need an API key.

## Run the GUI

From the repository root:

```powershell
conda activate medllm-mineru
python guideline_workbench/app.py
```

The GUI has tabs for PubMed collection, guideline processing, Medical-Assistant RAG retrieval, and retrieval evaluation.

## Process a guideline

From the repository root:

```powershell
python -m guideline_workbench.guideline_processing.pipeline `
  --input "guideline_workbench/data/guidelines/example-guideline.pdf" `
  --output "guideline_workbench/data/example_chunks.jsonl" `
  --specialty 新生儿 `
  --version 2025 `
  --tier advanced
```

Input PDFs are not committed. Place documents under `guideline_workbench/data/guidelines/` locally; generated Markdown, chunks, libraries, and reports are ignored by Git.

Supported options:

- `--chunk-strategy`: `fixed`, `recursive`, or `structure`.
- `--table-strategy`: `markdown`, `row`, or `row_context`.
- `--figure-strategy`: `reference` or `caption_context`.
- `--chunk-size` and `--overlap`: character counts, not tokenizer tokens.

## Run tests and experiments

Run the workbench unit tests from the repository root:

```powershell
$env:PYTHONPATH = "guideline_workbench"
python -m unittest discover -s guideline_workbench/guideline_processing/tests -v
```

The benchmark JSONL is tracked at `guideline_workbench/data/guideline_eval_pilot.jsonl`. To run the comparison, the three source PDFs must first be converted to Markdown and placed under `guideline_workbench/data/parsed_markdown/` with the filenames configured in `run_chunking_experiments.py`. Then run:

```powershell
python -m guideline_workbench.guideline_processing.run_chunking_experiments
```

The runner writes 11 variant chunk sets and BM25 libraries, `latest.json`, and `summary.csv` under `guideline_workbench/data/chunking_experiments/`. These generated files are local experiment artifacts and are not tracked.

## Demo RAG against supplied Markdown

After placing supported Markdown files under `guideline_workbench/data/parsed_markdown/`:

```powershell
python -m guideline_workbench.guideline_processing.run_demo
```

The demo creates a local custom-source library, retrieves fixed questions with the repository RAG, and checks citations. The library and chunk files are ignored by Git.

## Data and credentials

- Do not commit `.env`, API keys, patient information, downloaded guideline PDFs, MinerU model/cache files, or generated indexes.
- Keep only the small manually authored retrieval benchmark under version control.
- Download and redistribution rights for each source document remain the user's responsibility.
