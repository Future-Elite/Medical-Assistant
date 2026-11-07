# rag/demo

本目录提供不依赖仓库级 `data/` 的最小演示数据，供 `/api/demo/cases`、`/api/demo/guidelines` 与测试在发布分支直接使用。

| 文件 | 用途 |
|---|---|
| `neonatal_cases.json` | 50 例合成病例与检索预设 |
| `neonatal_guidelines.json` | 演示指南库（元数据 + 合成块 + 合成文献） |
| `manifest.json`、`case_index.csv` | 数据清单与索引 |
| `mesh_lexicon_neonatal_2026.json` | NLM MeSH 2026 新生儿域词表快照 |

仓库级 `data/` 不随发布分支分发。运行时优先读取 `data/`，缺失时回退到本目录；指南解析数据需通过 `RAG_GUIDELINE_DATA` 指向外部目录。

全部病例为合成数据（`synthetic=true`），仅用于流程与界面测试，不构成诊疗建议。
