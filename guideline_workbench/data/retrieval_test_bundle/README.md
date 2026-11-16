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

## 本次新增内容

- `recursive_row_native/chunks.jsonl`：六份指南的统一切分结果，共 620 个知识块；正文采用 Recursive（800 字符目标长度、120 字符重叠），表格采用逐行 Row 表示，流程图和曲线图采用完整结构 Native 表示。
- `recursive_row_native/custom_sources.json`：与上述知识块一一对应的 Medical-Assistant 自定义来源数据，共 16 批，每批不超过 40 篇，可用于本地检索测试。
- `recursive_row_native/manifest.json`：记录各指南的正文、表格、流程图和曲线图数量，以及导出配置。
- `guideline_processing/build_retrieval_test_bundle.py`：重建上述数据包的脚本；`medical_assistant_export.py` 同时保留结构化页面和元素来源字段。

Native 图示说明：复苏指南和黄疸指南复用 11 月实验整理的完整图示结构；转运和败血症流程图使用从 Markdown 提取并对照原图审阅的 Mermaid 结构。图示整理阶段包含助手视觉审阅，运行时不会调用视觉模型生成图示描述。唯一未解码的 QR 图像仅作非临床证据标记。无可靠来源定位的页码和元素 ID 保持为空，没有推测补写。
