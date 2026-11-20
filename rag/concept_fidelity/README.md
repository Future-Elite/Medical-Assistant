# 概念保真（concept_fidelity）Phase 0

本模块把“中文查询转换成英文检索计划后，是否仍在问同一个医学概念”做成可标注、可计算的槽级对象，并提供离线 harness。它是评测与数据契约层，**不训练模型、不下载权重、不做临床有效性声称**。

本分支是 6.8.0 研发评测分支，未合入 `origin/rag` 发布线；默认 `/api/retrieve`、患者图、指南图与既有配置行为不变。

## 1. 槽级 schema

`schema.py`：

| 对象 | 字段 | 约束 |
|---|---|---|
| `ConceptState` | `slot`、`surface`、`concept_id`、`confidence`、`ambiguity_set`、`status`、`source` | `status` 只允许 `resolved / ambiguous / unresolved / not_checked`；`resolved` 必须有 MeSH UI；`ambiguous` 至少两个候选；`unresolved/not_checked` 不得带 UI；`confidence ∈ [0,1]` |
| `ConceptIdentity` | `slot`、`source_surface`、`target_concept_id`、`converted_concept_id`、`label`、`evidence` | 标签只允许 `same_concept / synonym_ok / hypernym_drift / hyponym_drift / sense_ambiguity / dropped / fabricated / numeric_mismatch / negation_mismatch / not_checked`；`same_concept`/`synonym_ok` 要求转换后 UI 与目标一致；`not_checked` 不得带转换 UI |
| `ConversionProduct` | `route`、`query_zh`、`query_converted`、`concepts`、`identities` | route ∈ `reviewed_mapping / machine_translation / multilingual_encoding / naive_lexical / manual`；identity 的 slot 必须存在于 concepts |

查询集可选带 `conversions: [ConversionProduct]`；有转换产物时 harness 使用其 `query_converted` 与 `identities`，没有时按路线合成 `same_concept`/`not_checked` 并在报告中标明。

未知 status/label **报错**，不静默归为阳性。

## 2. MeSH 混淆探针

`mesh.ConceptIndex` 复用 `rag/demo/mesh_lexicon_neonatal_2026.json`（771 个 C16 描述符）：

- `descendants(ui)`：树号前缀下所有下位 descriptor，用于上下位漂移。
- `siblings(ui)`：共享直接父树号的 descriptor，用于同支混淆。
- `confusion_set(ui, size)`：先下位、后同支，按树深与标签 token 重叠确定性排序。
- `chinese_surfaces(ui)`：只取 `reviewed_aliases` 中的中文表面；没有中文别名时不会自动翻译。

`probes.build_sibling_probes()` 为每个有混淆集的 descriptor 生成 target/confusable 探针；中文表面缺失时保留英文探针并在报告中计数。

## 3. 冻结 pilot 数据

| 文件 | 内容 | 状态 |
|---|---|---|
| `data/pilot_queries_2026-11-20.jsonl` | 14 条中文临床问题；每条含 MeSH 目标概念、易混概念、审核英文上界（`reviewed_english`）、gold 文档 ID | `pilot: true`、`review_status: draft`、`not_for_clinical_use: true` |
| `data/pilot_corpus_2026-11-20.jsonl` | 23 条英文合成文档；每条带 `concept_ids`、`synthetic: true` | 合成评测文本，不是指南原文、不是临床建议 |

这是 **pilot v0.1**，不是 CMedTEB-CL 正式基准。扩到 200–300 查询 / 2–5k 文档、双人标注 + 仲裁（κ≥0.6）之前，任何数字只能称“pilot 读数”。

## 4. 指标

`metrics.py`：

- **entity-correct Recall@k**：top-k 中带目标 MeSH 标签的文档数 / gold 目标文档数；分母为 0 时 `None`。
- **wrong-entity contamination@k**：top-k 中带易混 MeSH 标签的文档数 / k。
- **sibling discrimination**：目标文档最佳排名早于易混文档为 1，否则 0；两者都未召回为 `None`。
- **binary nDCG@k / MRR**：目标文档为唯一相关项。
- **concept identity**：`same_concept`/`synonym_ok` 比例、drift 比例、`not_checked` 比例；按 slot 汇总。

空分母一律 `None`，不写 0；pilot 语料上的满分只说明 harness 与数据契约可运行，不说明模型质量。

## 5. Harness

- `BM25Retriever`：离线、确定性、无模型下载。
- `DenseRetriever`：接受调用方注入的 `encode(texts) -> vectors`；不内置模型、不联网。
- `run_phase0(query_path, corpus_path, lexicon_path, retriever=..., k=..., probe_limit=...)`：返回 pilot 报告，含 `pilot: true`、`not_clinical: true`、探针计数与逐查询指标。
- 默认两条路线：`reviewed_mapping`（审核英文上界）与 `zh_direct`（中文直接词面诊断）；`zh_direct` 在 BM25 上通常零命中，这是词面跨语言不匹配的诊断，不是检索质量结论。

## 6. 测试

```bash
cd rag
PYTHONPATH=.. python3 -m unittest discover -s tests/concept_fidelity -p 'test_*.py'
```

覆盖：schema 拒绝非法状态/标签、MeSH 下位/同支/混淆集、查询集重复 ID 与未知概念、手算指标、BM25/注入式 dense、pilot 数据契约。

## 7. 边界与下一步

- **不做**：训练编码器、下载权重、调用外部 API、使用真实患者数据、把 pilot 读数写成 benchmark 或临床结论。
- **Phase 0 之后**：冻结 200–300 查询与人工标注子集 → 跑 8–10 个零样本编码器（决策门 G1）→ 只有零样本兄弟概念区分确实低且与污染相关时，才进入后续训练（G2/G3）。
- **与发布线**：本模块只在 6.8.0 研发分支；未合入 `origin/rag`，也不改变 `/api/retrieve` 默认路径。
