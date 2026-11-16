# Neonatal Guideline Retrieval Test Bundle

This bundle contains six parsed neonatal guidelines for internal RAG testing. It was prepared from the Markdown files already in the MedLLM workspace; source PDFs are not included.

## Configurations

- Prose: recursive splitting, 800-character target, 120-character overlap.
- Tables: one row per chunk, retaining the row's column labels.
- Figures: Native text representation for flowcharts and charts. Complete diagram structures from the November experiment are reused. The transport and sepsis flowcharts use the Mermaid structures extracted from the source Markdown and reviewed against their source images. QR codes are labeled as undecoded image payloads and are not treated as clinical evidence.

Native descriptions were prepared with assistant visual review of source images, alongside MinerU-extracted Mermaid structures. This is visual understanding during corpus preparation; Medical-Assistant does not invoke a vision model at runtime to create these descriptions. No unverified curve sample values were transcribed.

`chunks.jsonl` contains all six guides in the MedLLM chunk schema. `custom_sources.json` contains the same documents in Medical-Assistant's custom-source schema, split into source batches of at most 40 documents. Configure this JSON as `custom_sources_path` or register its `sources` entries with `/api/sources/custom`.

## Provenance and limits

Page and element metadata is retained only where an existing structured MinerU sidecar could be matched. Missing provenance stays empty; it is not inferred from the Markdown order. Embedded image payloads and machine-local asset paths are omitted from the export. Each image-bearing chunk keeps a SHA-256 image reference where available.

This is a retrieval test corpus, not a clinically reviewed knowledge base. The six source documents and representations have not all been manually reviewed by a clinical expert. Check the source document license before redistribution outside the project.
