# 患者知识图谱与图检索（v6.7.3）

本目录把“患者事实传入后”的一步从**扁平词面扫描**改成**可见快照图谱 + 受限图检索**。它服务的是 RAG 的患者事实检索，不训练模型、不连接 HIS、不写回病历，也不把图上的可达路径解释成诊断或治疗因果。

对应外部 EHR 知识图谱需求文档（不随仓库分发）的病例快照、病例覆盖层、Graph-RAG 检索、证据路径与审计部分；指南侧推荐/条件/动作校验不在本目录范围。

**外部 EHR 接入**：HTTP 请求体、EHR 视图映射、响应字段与最小客户端示例见 [INTEGRATION.md](INTEGRATION.md)。 机器可读契约见 [patient_snapshot.schema.json](patient_snapshot.schema.json) 与 [patient_graph.openapi.yaml](patient_graph.openapi.yaml)。

## 1. 边界与非目标

- **只读**：输入是调用方已经完成授权与 `as_of` 过滤的 `PatientSnapshot`；图不重读 HIS。
- **只看可见事实**：`snapshot.visible_records()` 之外的记录不建节点；未来报告、出院后复查、隐藏状态不进入图。
- **不创造事实**：缺失字段保持 `not_recorded`，不填默认 0、不按体重反推胎龄、不把“无记录”当“正常”。
- **不宣称临床关系**：`HAS_RECORD`、`MENTIONS`、`DERIVED_FROM`、`TEMPORALLY_BEFORE` 是检索/溯源边，不是因果边，也不是治疗授权。
- **不替代条件引擎**：`quality/applicability.py` 仍负责指南结构化条件的 `match/mismatch/unknown`；本目录只提供事实、状态、时间、主体与路径。

## 2. 图模式

### 2.1 节点

| 类型 | 含义 | 关键属性 |
|---|---|---|
| `patient` | 快照根节点 | `patient_ref`、`as_of`、`source` |
| `record` | 一条可见 `PatientRecord`（`patient-snapshot`） | `record_id`、`record_type`、`event_type`、`event_time/start/end`、`status`、`subject`、`field_names`、`field_presence`、`search_tokens`；图响应中的 `record_path/collection/time` 是这三者的稳定检索别名 |
| `concept` | 患者事实词表概念 | `concept_id`、`kind`、`label` |
| `subject` | 主体 | `patient` / `mother`（母婴不合并） |
| `time` | 采样/记录日 | ISO 日期 |
| `episode` | 决策时点 | `as_of` |

`record` 不保存原始叙述文本；`search_tokens` 也排除叙述字段与长文本，只保留结构化字段/取值的词面 token，避免图响应回显整段病历。命中时由 `fact_evidence_text()` 从事实生成短证据行。

### 2.2 边

| 关系 | 起点 → 终点 | 语义 |
|---|---|---|
| `HAS_RECORD` | patient → record | 快照包含该记录 |
| `HAS_SUBJECT` | record → subject | 记录主体 |
| `MENTIONS` | record → concept | 字段/取值提及某概念；带 `field`、`status`、`value_present`、`match=field/value` |
| `DERIVED_FROM` | derived record → raw record | 派生事实的来源记录（`source_paths`） |
| `RECORDED_AT` | record → time | 记录时间 |
| `OCCURS_IN` | record → episode | 在决策时点可见 |
| `TEMPORALLY_BEFORE` | earlier record → later record | 可见记录的时间链；缺时间记录不补默认日期 |

检索扩展只允许 `DERIVED_FROM` 与（查询含“最新/最近/既往”时）`TEMPORALLY_BEFORE`。没有共享概念的任意游走，避免一个通用概念把整张病历拉进来。

### 2.3 快照哈希

`snapshot_sha256 = sha256(canonical JSON(patient_ref, as_of, source, visible records))`，字段排序、`ensure_ascii=False`、紧凑分隔符。同一快照与同一构建器产生同一哈希；任一可见记录变化都会改变哈希。哈希进入 `trace.patient_graph.graph.snapshot_sha256` 与 `patient_context.graph_snapshot_sha256`，用于审计与复现。

## 3. 患者事实词表

`patient_fact_lexicon.json`（`patient-fact-lexicon-1.1`）是**检索词表**，不是临床规则库：

- `concepts`：约 40 个事实/临床概念，带中文、英文、缩写别名、结构化字段、集合限制。
- `question_intents`：12+ 个问题意图（性别、日龄、胎龄、入院/出院诊断、结局、住院日、最高胆红素、光疗、抗生素、听力筛查、呼吸支持、CRP、血培养），把“问什么”映射到记录类型、字段和聚合偏好。
- **区分而不是合并**：
  - `exclude_aliases`：`总胆红素` 不吞并 `直接胆红素`；`血氧` 不吞并 `导管前/后血氧`；通用 `培养` 不吞并 `血培养/尿培养`。
  - `field_values`：血培养/尿培养在 `specimen` 冲突时不匹配；无标本且字段名本身不含标本时也不匹配，避免仅凭集合名合并。
- 未命中词表时，查询文本进入 `unresolved_terms`，不强行绑定到第一个候选。

词表是 HIS/出院小结记录的**字段语义**，不是指南推荐。它来自现有数据契约（`rag/core/his.py` 的 `patient-snapshot`、`v6.7.2` 数据集的 `derived_*` 字段与 `fact_rules.py`），不来自 qrels 的答案值；意图→集合的映射按 HIS 字段语义确定；旧 qrels 已删除，历史分数只能解释为**记录定位**，不是独立泛化测试。

## 4. 图检索

### 4.1 流程

1. **解析**：`resolve_queries()` 把问题拆成 intent、concept、subject、temporal、negated/uncertain 与未解析词。
2. **种子**：
   - intent → 该意图的集合/字段（集合顺序有先验，字段存在加分）；
   - concept → `MENTIONS` 边连接的记录；
   - 仅当 intent/concept 都没解析出时，才用 legacy tokenizer 做词面兜底；
   - 主体明确时（母亲/母体/maternal），过滤非该主体的记录。
3. **受限扩展**：从得分最高的种子出发，最多 `max_hops` 跳，只走 `DERIVED_FROM` 与时间链；节点预算 `max_nodes`，超出写 `truncated` 警告。
4. **排序**：`base + 0.08 × status_weight + 0.02 subject + 时间提示 − 0.05 × hop`；状态权重 `observed_positive 1.0 / observed_negative 0.95 / uncertain 0.7 / pending 0.45 / not_recorded 0.3`。分数是检索相关性，不是概率、证据质量或临床适用性。
5. **输出**：每条命中带 `record_path`、`status`、`concept_ids`、`graph_path`、`match_method`、`evidence_text`。

### 4.2 状态与缺失

- `not_recorded` 字段仍产生 `MENTIONS` 边：系统能回答“这条记录本该有该事实，但未记录”，而不是静默返回空。
- `pending`、`uncertain`、`observed_negative` 原样进入命中 `status`，不被改写成阳性或正常。
- 查询解析出概念但图中没有任何记录时返回 `no_results`，不使用共享 bigram 的另一条记录冒充命中。
- 否定词只作为诊断提示，不把 `unknown` 翻成 `met`；四态条件判断仍由 `quality/applicability.py` 负责。

### 4.3 预算与复现

默认预算：`max_hops=2`、`max_nodes=240`、`max_candidates=400`、`top_k` 由请求决定。`trace.patient_graph` 记录：

- `query`：原始查询、intent、concept、别名词、subject、temporal、negated、unresolved_terms；
- `query_terms`：患者图命中的概念标签/匹配别名（非标识、非原始记录），供本地检索回流使用（`patient_fact_query_terms()`）；
- `seeds`、`expanded`、`hits`、`budget`、`status`、`warnings`；
- `graph`：`snapshot_sha256`、节点/边/记录/概念计数。

排序是确定性的：同快照、同预算、同词表版本得到同序结果。

## 5. 接入点（图检索是唯一路径）

v6.7.3 删除了旧的扁平词面患者检索、`off/shadow` 开关与旧测试/工具；患者事实只有图检索一条路径。

| 位置 | 行为 |
|---|---|
| `EvidenceRetrievalPipeline.retrieve` | 调用 `rag.patient_graph.service.retrieve_patient_facts`；`trace.patient_graph` 与 `patient_context.graph_*` 始终输出 |
| `RetrievalController` round 0 | 同样走图检索；`rounds[0].result` 含 `patient_graph_engine/status/nodes` |
| `GET /api/health` | `patient_graph` 返回 `engine=patient_graph`、`max_hops`、`max_nodes`、词表来源 |
| `POST /api/patient-graph` | 由 `patient_snapshot` 或 `ehr_view` 构建图，返回节点/边/哈希/计数，不回显原始 `records` |
| `POST /api/patient-graph/search` | 查询词 + `patient_snapshot`/`ehr_view` → 命中与路径 + trace |
| 工作台 01 步骤 | “患者知识图谱”卡片：构建、患者检索、SVG 节点-边图、类型图例、命中高亮、节点详情、缩放/重置、仅看命中邻域；结果页患者事实页显示状态/概念/路径 |

`RAG_PATIENT_GRAPH_MAX_HOPS`、`RAG_PATIENT_GRAPH_MAX_NODES`、`RAG_PATIENT_GRAPH_LEXICON` 只调整预算与词表，不切换检索引擎。

## 6. 旧 qrels 与当前验证

20,400 条事实 qrels（`datasets/v6.7.2_dataset_2026-11-07/patient_fact_qrels.jsonl`）已于 **2026-11-16 删除**，6.7.3 不再保留或引用该 qrels，也不再报告基于它的 R@1/R@5 数字。`tools/eval/run_patient_graph_eval.py` 只保留为未来重新生成 qrels 后的复测工具；没有 qrels 时显式报错，不产生替代指标。

当前患者图谱验证改为：

- `tests/patient_graph/`：建图、快照哈希稳定性、未来记录过滤、主体隔离、派生来源、意图定位、状态保留、无结果、时间链、预算截断与可复现性；
- `tests/core/test_his_snapshot.py`：`event_time`/`reported_time` 可见性、日期/时间边界与 v2 字段契约；
- `tests/core/test_ehr_view_adapter.py`：EHR `PatientEnvironment.view()` 投影、未来报告过滤、与知识图联动；
- 真实指南 PDF 解析与指南图构建使用 `tools/build/build_guideline_graph.py`，输出在 `data/generated/validation/guideline_graph_library.json`；这是指南图谱验证，不冒充患者图检索准确率。

图谱只负责记录定位与来源/时间/主体保留；没有人工 qrels 时不能声称临床准确率、独立泛化或检索质量提升。

## 7. 性能

`tools/benchmark/benchmark_patient_graph.py`（10 条合成记录、500 次、本机 CPU）：图构建 p50 7.22 ms / p95 7.82 ms；术语解析 p50 0.70 ms / p95 0.77 ms；图检索 p50 0.95 ms / p95 1.08 ms。工件：`data/generated/validation/v673_patient_graph_benchmark_2026-11-16.json`。这是小样本微基准，不是医院规模吞吐或 SLA。

## 8. 测试与复现

```bash
cd rag
PYTHONPATH=.. python3 -m unittest discover -s tests -p 'test_*.py'
```

患者图专项在 `tests/patient_graph/`：建图/哈希/未来过滤/主体隔离/派生来源/意图定位/状态保留/无结果/时间最新/预算截断/可复现/HTTP 契约；HIS 快照可见性/最小化/主体/时间过滤在 `tests/core/test_his_snapshot.py`。旧的 `tests/retrieval/test_patient_retrieval.py` 与 `off/shadow/active` 测试已删除。全量测试数见本轮实跑与模块 README。

v6.7.3 是**破坏性升级**：不再提供 `patient_graph_mode=off`，也不再有与 v6.7.2 旧患者检索的逐字段兼容差分；旧工具与测试已归档，不随发布分支分发。

## 9. 参考文献与设计依据

以下文献只支持方法选择与边界讨论，不证明本实现的临床有效性；论文结论均按原文范围引用。

- **患者级检索任务**：PMC-Patients 把 Patient-to-Article / Patient-to-Patient 检索独立成可评测任务（Zhao et al., *Scientific Data* 2023, DOI 10.1038/s41597-023-02814-8）。本实现的“患者事实记录定位”与其患者侧检索相近，但目标是 HIS 记录而非文献。
- **条件级患者匹配**：TrialGPT 的 criterion-level matching 与错误分析说明“逐条条件 + 患者事实”比整段相似度更可审计（Jin et al., *Nature Communications* 2024, DOI 10.1038/s41467-024-53081-z）。本实现借其“先定位事实，再判断条件”的分层，不把试验入组结论迁移到临床指南。
- **纵向患者图谱**：Kim et al. 从结构化/非结构化 EHR 构建可计算纵向病程（*Nature Medicine* 2026, DOI 10.1038/s41591-026-04695-x）；Postiglione et al. 用时序知识图谱 + 本体做未来疾病预测（*IEEE JBHI* 2024, DOI 10.1109/JBHI.2024.3390419）。本实现只取“时间边 + 来源定位”，不做预测。
- **指南图与 GraphRAG**：CKD 指南 GraphRAG 的三臂比较（Sesen et al., *BMJ Digital Health & AI* 2026, DOI 10.1136/bmjdh-2026-000041）、肝病自校正 Agentic Graph RAG（Hu et al., *Frontiers in Medicine* 2025, DOI 10.3389/fmed.2025.1716327）、眼科 EyeRAG 的固定场景多配置评测（Zhao et al., *npj Digital Medicine* 2026, DOI 10.1038/s41746-026-02860-7）。这些工作说明图检索需要固定场景、预算与安全指标；本实现据此输出路径/预算/截断，而不是只报命中数。
- **多指南路由**：Alexiou et al. 的血管外科多指南 agent 把“选哪份指南/何时不检索”做成路由层（*JAMIA* 2026, DOI 10.1093/jamia/ocag178）。本实现的 patient graph 与之互补：先确定患者事实，再由指南侧决定推荐与适用性。
- **新生儿 CIG**：NoviGuide Neonatal 的 “logic-as-data” 与可追溯输出（Danziger et al., *Studies in Health Technology and Informatics* 2024, DOI 10.3233/SHTI230962）支持“图/逻辑与执行分离”的工程取向；它是平台表示方法，不是临床有效性证据。
- **评测诊断**：RAGChecker 的模块级诊断（Ru et al., arXiv:2408.08067, 2024）与 MIRAGE 的检索/生成分开评（Xiong et al., ACL Findings 2024, DOI 10.18653/v1/2024.findings-acl.372）支持本实现把“记录定位”和“临床答案正确性”分开报告。

完整索引与阅读笔记属内部研发记录，不随仓库分发。

## 10. 已知限制

- 词表覆盖的是当前 HIS/出院小结字段与新生儿主题；新增字段/标本/检查需要扩词表并补测试，不能靠改代码特判单份数据。
- 图检索解决“记录定位”，不判断指南推荐是否适用；四态条件引擎、动作校验、知识发布版本仍是独立模块的职责。
- 旧 qrels 已删除；不要从历史报告摘取 R@1 1.0000 作为当前结果。
- 大图（数百条记录/多机构）尚未做吞吐测试；当前预算与索引是进程内实现。
- 母婴主体、否定、时间精度只保留到“状态/时间/主体”层；更细的“待排/既往/母亲 vs 患儿”语义需要条件引擎消费这些字段，不能由检索层直接下结论。
