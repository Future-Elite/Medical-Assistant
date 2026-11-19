# EHR 式指南知识图谱与 Graph-RAG

本目录把 EHR 知识图谱需求中的**指南知识图谱、条件判断、推荐检索与动作校验**实现在 RAG 包内，供 Agent、工作台和外部调用方直接调用。它回答的是“当前病例条件下，指南能支持哪个动作、依据哪段原文、还缺什么”，不是“找一段相似文本”。

> **边界**：内置图是 `synthetic_fixture` 演示夹具，`publishable: false`，不构成临床证据、诊疗建议或医嘱授权。本目录不训练模型、不联网、不写回 EHR。真实指南入库前必须完成来源、版本、审核状态和证据定位核验。

## 1. 为什么 EHR 需要图谱，而不是只做向量检索

EHR 侧的输入不是一段文章，而是**带主体、时间、来源和状态的结构化事实**：同一份病历里有患儿的血气、母亲的 GBS、正在运行的 CPAP、尚未回报的血培养，还有“未记录”和“阴性”的区别。指南侧也不是一段文章，而是**带人群、指征、排除、监测、证据和动作绑定的条件结构**。两边真正要对齐的是“哪个事实满足哪条推荐、还差哪个条件、这个动作是否可执行”，这本质上是关系问题：

1. **事实必须能追到记录**。`concept:rds` 不是模型“觉得像”，而要沿 `MENTIONS ← record → patient` 回到具体记录、时间和主体；否则无法解释“为什么这个病例适用”。
2. **条件必须能组合**。一条推荐往往同时要求“早产 + 呼吸窘迫 + 低氧 + 无禁忌”，还要区分 `met / not_met / unknown / conflict`。平铺的文本相似度没有地方承载这种合取、析取、否定和缺省语义。
3. **推荐必须能追到证据**。`recommendation → SUPPORTED_BY → evidence_span → PART_OF_SOURCE → source` 让每条建议落到版本化原文的 locator；纯向量 top-k 给不出稳定的引用链。
4. **动作必须能绑定运行时**。指南说“可考虑 CPAP”，EHR 还需要知道 `action:cpap` 在运行时目录里对应哪个可执行项、允许哪些操作和参数，才能判断候选计划是否可模拟、是否与当前活动治疗冲突。
5. **缺信息必须显式保留**。`not_recorded` 只能得到 `unknown`，不能当成“没有该病”；`unknown` 不能放行动作；`mismatch` 不是临床不适用。图上的条件求值天然保留这些状态，文本检索的“相关性分数”无法表达。
6. **患者图与指南图必须分开**。患者图是**每请求、只读、从可见快照构建**的事实图；指南图是**版本化、审核后发布**的知识图。两者只在检索时按概念/动作/条件连接，不能把患者事实写进知识库，也不能让知识库反向修改病历。

因此本目录采用两张图的受控连接：`PatientSnapshot → patient_graph` 提供事实，`knowledge release → guideline graph` 提供推荐，`retrieve_knowledge()` 在两者之间做术语归一、受限遍历和四态条件求值，`validate_actions()` 再对候选动作做运行时与证据校验。

## 2. 目录与职责

| 文件 | 职责 |
|---|---|
| `model.py` | 加载版本化知识 JSON，构建 `concept/action/panel/population/condition/recommendation/source/evidence_span/runtime_binding` 节点与类型化边；校验引用；计算内容 SHA-256；术语归一 |
| `conditions.py` | 四态条件 AST 与事实存储：`all/any/not/exists/eq/ne/lt/lte/gt/gte/in/contains/between` |
| `patient_facts.py` | 从 `PatientSnapshot.visible_records()` 提取结构化事实（GA、体重、感染风险、诊断、症状、呼吸支持、化验、待检、活动治疗），不解析自由文本 |
| `retrieval.py` | 术语解析 → 种子 → 受限图遍历 → 推荐维度求值 → 监测面板/冲突/缺失信息 → 可审计 `graph_path` |
| `validation.py` | 候选动作校验：引用推荐是否在本轮上下文、方向/操作、人群/指征/排除/时间、证据审核、运行时绑定、参数、待检重复、组合冲突、活动治疗、未来动作时间线 |
| `planning.py` | 复查计划（只展开已初始化指标/面板）、观察稳定性/危险征象、维持方案比较器、病程结束门控 |
| `session.py` | 有界 Agent 补查回路：`needs_evidence` → 实际执行查询 → `graph_query` trace → 预算耗尽显式报错 |
| `release.py` | release 生命周期：注册、发布（要求 reviewed/publishable、证据 span 与 source 已审核、locator/quote、source 状态与许可）、撤回、回滚 |
| `service.py` | 对外 facade；保存检索上下文与快照哈希；拒绝陈旧快照 |
| `fixtures/neonatal_graph_rag_demo.json` | 演示用合成释放（3 sources / 7 spans / 10 recommendations / 15 runtime bindings） |

患者图本身在 `../patient_graph/`：`patient/record/concept/subject/time/episode` 节点，`HAS_RECORD/MENTIONS/DERIVED_FROM/RECORDED_AT/OCCURS_IN/TEMPORALLY_BEFORE` 等边。患者事实检索只有这一条图路径。

## 3. 数据模型

### 3.1 节点

- `concept`：临床概念（如 RDS、低氧、感染风险）。
- `action`：可执行动作（如 CPAP、抗菌药、血培养）。
- `panel`：监测面板（如血气），列出 `metric_keys`。
- `population`：人群条件（如早产、足月）。
- `condition`：条件 AST 容器，`expr` 为四态表达式。
- `recommendation`：推荐；含 `direction`（`support/consider/monitor/not_routine`）、`supported_operations`、指征/排除/时间条件、监测面板、证据、运行时绑定。
- `source` / `evidence_span`：来源版本与可定位证据片段（`locator`、`quote`、审核状态）。
- `runtime_binding`：动作到运行时目录的绑定（`runtime_action_ids`、`operations`、`parameters`、`exclusive_group`、`executable`）。

### 3.2 边

`ADDRESSED_BY`、`RECOMMENDS`、`CONSTRAINS`、`APPLIES_TO`、`REQUIRES`、`EXCLUDES`、`MONITOR_WITH`、`SUPPORTED_BY`、`PART_OF_SOURCE`、`CONFLICTS_WITH`、`BINDS_TO`。检索只沿白名单边扩展，避免把图变成任意跳转。

### 3.3 术语归一

`resolve_term()` 返回 `resolved / ambiguous / unresolved` 与候选列表。候选带 `standard_codes`（MeSH/LOINC/SNOMED CT/ICD/ATC 等）；没有授权术语库时保持空字典，不编造编码。**歧义不静默取第一个候选**：演示夹具中 `PS` 同时指向 `concept:pulmonary_stenosis` 与 `action:surfactant`，检索返回 `needs_disambiguation`，动作校验返回 `TERM_AMBIGUOUS`。

## 4. 四态条件

每个条件求值得到：

- `met`：事实满足；
- `not_met`：事实明确不满足；
- `unknown`：缺记录、`not_recorded`、`pending` 或无法判断；
- `conflict`：同一时点出现互相矛盾的值。

组合规则与风险控制：

- `all`：任一 `not_met` → `not_met`；否则任一 `unknown` → `unknown`；否则 `met`。
- `any`：任一 `met` → `met`；否则任一 `unknown` → `unknown`；否则 `not_met`。
- `not`：`met ↔ not_met`；`unknown`/`conflict` 原样传播。
- 同一事实多次记录默认取最新值；同一时点冲突值保留为 `conflict`，不覆盖。
- `not_recorded` 只产生 `unknown`；`unknown` 不得被推荐求值或动作校验当成已满足。
- `mismatch` 不是临床不适用；`possible_match` 不是指南符合。

## 5. 检索流程

`retrieve_knowledge(graph, snapshot, queries, intent=None, limit=18, max_hops=3)`：

1. 解析查询术语；有歧义时状态为 `needs_disambiguation`，不强行选候选。
2. 从查询、患者诊断/症状/活动治疗/呼吸支持、异常 SpO2 等建立种子；无种子时若存在 GA 则退化为评估种子。
3. 沿白名单边做受限遍历，收集推荐路径与证据路径。
4. 对每条推荐求值 `population / indication / exclusion / temporal / evidence_status / runtime_capability`。
5. 仅 `met` 的条件放行；`not_met` 的排除或指征进入 `excluded_recommendations`；`unknown` 保留在 `missing_information`，推荐标记 `needs_information`，不能 `eligible_for_simulation`。
6. 输出监测面板（仅初始化已有指标的 panel）、冲突、来源、证据片段、患者事实、`graph_path` 和边界声明。

返回对象核心字段：`knowledge_context_id`、`knowledge_release_id`、`release_sha256`、`snapshot_id`、`snapshot_sha256`、`term_resolutions`、`recommendations`、`excluded_recommendations`、`available_monitoring_panels`、`conflicts`、`missing_information`、`sources`、`evidence_spans`、`graph`、`patient_facts`、`diagnostics`、`status`、`boundary`。

## 5a. 双向图检索融合（指南适用性升级）

`retrieve_bidirectional_knowledge(graph, snapshot, queries, ...)` 在原有“患者事实 → 指南推荐”之外，增加“指南条件 → 患者证据”的反向查询；默认由 `KnowledgeService.search(..., fusion=True)` 启用。

- **正向**：`retrieve_patient_facts()` 从患者图检索事实；命中的 `concept_ids/matched_terms/collection/event_type` 经规范术语解析后作为额外种子加入指南图遍历。种子数量有上限，歧义词不强行绑定。
- **反向**：对候选推荐的 population/indication/exclusion/temporal 条件摘要发起患者图查询；命中的记录路径回填到对应推荐。
- **融合结果**：每条推荐增加 `patient_graph_evidence`（record_path/status/concept_ids/graph_path/evidence_text/origin）与 `fusion`（fact_references、matched/total、coverage、forward/backward matched）；顶层 `fusion` 记录正向/反向命中数、反向查询词、trace 摘要与覆盖率。
- **语义不变**：融合只补充证据与路径，不改变四态条件、`eligible_for_simulation`、缺失信息或动作校验；`fusion=false` 可复现原检索结果。
- **API**：`POST /api/knowledge/search` 接受 `"fusion": true|false`，默认 true；缓存键包含 fusion 模式，同一上下文不会误用另一模式。

当前测试：`tests/knowledge/test_knowledge_fusion.py`（正向/反向证据、资格不变、缓存隔离）与 `tests/knowledge/test_knowledge_api.py` 的 fusion 开关；120 场景与 held-out CQ 在默认 fusion 下仍全部通过。融合覆盖率是检索层指标，不是临床准确率或适用性金标准。

## 5b. 适用性链路桥接（`applicability_bridge.py`）

`attach_fusion_to_applicability(result, fusion)` 把双向图检索结果接到已有的 `/api/applicability` 链路上；`POST /api/applicability` 接受可选 `knowledge_fusion` 对象，`EvidenceRAG.resolve_applicability_workflow` 在返回前调用桥接。

- **逐条条件**：按 `criterion.record_paths` 精确匹配优先，其次按字段前缀与记录路径的 token 匹配，最后按概念重叠；结果写入 `graph_support`（`same_record`/`same_record_type`/`concept_overlap`/`none`）、`graph_record_paths`、`graph_concept_ids`、`graph_origin`。
- **候选**：`graph_candidates` 保留推荐 ID、摘要、来源、患者记录路径、覆盖率与四态 `applicability`；按目标指南来源标记 `same_target`，跨指南候选明确写入 `scope=cross_guideline`。
- **链路节点**：前端在链路中增加“双向图证据”和“指南图谱候选/其他指南图候选”，显示正向/反向命中、覆盖率与候选旁证；跨指南候选标注“不同源，只作旁证”。
- **语义不变**：不改 `match/mismatch/unknown`、`outcome`、`stop_reason` 或 `eligible_for_simulation`；只在 `next_action` 增加 `graph_candidate_count`/`graph_text`，必要时 `graph_action=review_graph_candidates`。
- **边界**：`graph_unknown_dimensions` 只统计同目标指南候选；空结果不是阴性；图证据是患者记录定位，不是指南适用性判定或临床批准。

测试：`tests/knowledge/test_applicability_bridge.py`（不改变状态/结论、逐条支持、候选标记、输入不变）与 `tests/gui/test_workbench_contract.py` 的 HTTP `knowledge_fusion` 契约。

## 5c. k 个本地指南 × 1 个患者图（`local_guideline_fusion.py`）

**顺序固定**：离线 `guideline_parsing` 解析/分块 → `build_guideline_graph.py` 为每份指南建图 → 07 执行检索得到 Top-k 指南（按 `source_id` 去重）→ `fuse_local_guidelines(guideline_ids=...)` 加载这 k 份指南的预建图（缺失时只对该指南从 chunk 现场建图）→ 与单个患者图做双向融合。没有检索结果时才回退到 `select_local_guidelines()` 的 chunk IDF 选择。查询时不重新解析 PDF，也不为每次查询重建全部 37 张图。

- **指南选择**：`select_local_guidelines(source, queries, top_guidelines=k, required_guideline_ids=[target])`；目标指南即使查询分数低也会保留在 k 中，其余按块分数补足。返回每本指南的匹配块、页码、分数与 `chunks_matched/chunks_total`。
- **正向**：患者图命中（`retrieve_patient_graph`）的 `matched_terms/concept_ids/evidence_text` 作为推荐候选的额外词面信号；命中记录按 token 交集附加到对应推荐。
- **反向**：每本指南推荐节点的 `condition/exception` 原始条件转成患者图查询（已映射字段用中文标签，未映射保留原文 + 双语同义词扩展），命中记录回填到该推荐。
- **k 路合并**：每个指南先保留 top-3 推荐，再按指南轮转合并到 `limit`，避免一本高分指南占满；`recommendations[].source_ids=[guideline_id]`，因此适用性桥接能识别 `same_target`。
- **输出**：`guidelines[]`、`guideline_selection`、`recommendations[]`、`fusion.forward/backward/stats`、`diagnostics.pipeline_order`；`recommendations` 与 `/api/knowledge/search` 兼容，可直接作为 `knowledge_fusion` 传给 `/api/applicability`。
- **边界**：所有节点 `review_status=extracted`、`applicability` 维度 `unknown`；这是检索/复核层，不是指南适用性判定、临床建议或“患者没有该事实”的阴性证据。

测试：`tests/knowledge/test_local_guideline_fusion.py`；HTTP 契约见 `tests/gui/test_workbench_contract.py` 的 `/api/knowledge/guideline-fusion`。

## 5d. Agentic 迭代检索（2026-11-17）

`rag/retrieval/agentic.py` 按最新医学 agentic RAG 论文实现确定性 following-work：

- **Interpreter**（SEMA-RAG I-Agent）：把查询与患者事实结构化为 `ClinicalSchema`（intent/entities/target_actions/constraints/initial_ambiguous）；原始查询歧义不被后续轮次静默消解。
- **Explorer**（SEMA-RAG E-Agent + MA-RAG + MedTrust-RAG）：每轮调用 `KnowledgeService.search(..., fusion=True)`；`EvidenceLedger` 按推荐/证据 ID 去重累积；`MedicalGap` 从缺失条件、冲突 action、未解析词生成下一轮 query；停止条件为 sufficient / not_applicable / needs_review / needs_disambiguation / needs_information / no_results / budget_exhausted。
- **Arbiter**（SEMA-RAG A-Agent + MedTrust/MedAgent-R1 gate）：输出证据包、rounds、gap、`citation_coverage`、`patient_evidence_coverage`；不生成临床答案。

接口：`POST /api/knowledge/iterative-retrieve`（支持 `patient_snapshot` 或 `ehr_view`；`max_rounds` 默认 3、`max_queries_per_round` 默认 5）。

评测：`rag/knowledge/fixtures/agentic_retrieval_eval.json`，10 个合成契约场景；full 10/10、mean 1.8 轮、evidence recall 1.0；single-shot 5/10。single-shot 证据召回也可为 1.0，差异在停止/缺口语义，不是临床准确率。文献映射与实现报告属内部研发记录，不随仓库分发。

## 6. 动作校验

`validate_actions(graph, context, candidate_plan, facts=...)` 对每个动作检查：

- 引用的推荐必须在**本轮检索上下文**内；引用被排除的推荐得到 `CONTRAINDICATION_PRESENT`，引用不存在的得到 `RECOMMENDATION_NOT_RETRIEVED`。
- 操作必须在推荐 `supported_operations` 内；`not_routine` 不能授权 `start`。
- `population/indication/temporal = not_met` → 阻断；`unknown` → `needs_information`；`exclusion = met` → `CONTRAINDICATION_PRESENT`。
- 证据必须审核；运行时绑定必须可执行、支持该操作和参数。
- `continue` 必须有匹配的活动治疗；`order_test` 目标仍 `pending` 时拒绝重复申请。
- 同一时点互斥呼吸支持不能同时 `start`；同一目标不能同时 `start` 与 `stop`；`offset_hours` 必须从 0 开始并按升序排列。
- 校验按时间线模拟活动治疗：未来动作若与当前 bundle 的互斥组冲突，返回 `RECOMMENDATION_CONFLICT`，并写入 `timeline[].offset_hours`。
- `wait/continue` 观察必须 `clinically_well=true` 且无危险征象；缺评估返回 `CLINICAL_REVIEW_REQUIRED`，呼吸暂停/惊厥/休克/心肺复苏等返回 `RED_FLAG_PRESENT`。
- 快照在检索后变化时返回 `SNAPSHOT_STALE`，必须重新检索。

返回对象包含 `actions`、`timeline`、`combination_errors`、`errors`；`eligible_for_simulation=true` 只表示“通过了本知识/运行时检查”，**不是临床安全、最佳治疗或医师批准**。

### 6.1 复查、维持与病程门控

- `build_review_plan(context, panel_ids, metric_keys)`：只允许已初始化面板/指标；面板不存在或指标没有真实基线时抛 `PlanningError`，不会用固定血气或虚构结果填充。
- `build_maintenance_comparator(primary_plan)`：生成 `continue` 对照方案，继承主方案的 `review_metrics/review_panels`，`proposal_order` 较大；`select_candidate()` 平局时按 proposed 在前，不默认维持胜出。
- `episode_end_gate(facts, explicit_clinical_end=...)`：默认拒绝“评分指标正常 → 自动结束病程”；有活动治疗继续阻断，必须提供明确临床结束依据。

### 6.2 Agent 补查回路

`KnowledgeSession(service, snapshot, queries, max_evidence_rounds=2)`：

- `request_more(["LISA"])` 实际调用 `service.search()`，把 `queries`、`normalized_terms`、`recommendation_ids`、`graph`、`snapshot_sha256` 写入 `trace`。
- `run_loop(propose)` 接收返回 `{"status":"needs_evidence","knowledge_queries":[...]}` 的 proposal 回调；执行补查后再次提案。
- 超过 `max_evidence_rounds` 抛 `KnowledgeBudgetError`，不修改快照、不伪造证据。

### 6.3 Release 生命周期

`KnowledgeReleaseRegistry` 提供 `register/publish/withdraw/rollback/list_releases`。发布要求 `publishable=true`、`review_status in {reviewed,approved}`、所有 evidence span 与 source 已审核、span 带 locator/quote、source `document_status` 有效且有许可/存储说明；默认 demo release 会被拒绝发布。真实指南图尚未接入，因此当前默认 release 保持 `draft`、`publishable:false`。

## 7. 调用方式

### Python

```python
from rag import get_default_service, snapshot_from_dict

service = get_default_service()
context = service.search(["CPAP", "呼吸窘迫"], snapshot)
result = service.validate_actions(
    {"actions": [{
        "operation": "start", "target": "action:cpap", "offset_hours": 0,
        "guideline_rule_ids": ["recommendation:DEMO-RDS-CPAP"],
    }]},
    snapshot,
    context["knowledge_context_id"],
)
```

也可直接使用 `load_knowledge_graph()` / `retrieve_knowledge()` / `validate_actions()` / `KnowledgeSession` / `build_review_plan()`；顶层 `rag` 已导出这些符号。

```python
review = service.review_plan(context["knowledge_context_id"], panel_ids=["panel:cbc"])
session = service.session(snapshot, ["CPAP"], max_evidence_rounds=2)
session.request_more(["LISA"])
```

### HTTP

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/knowledge` | 图统计与 release 摘要 |
| GET | `/api/knowledge/graph` | 完整节点/边/release |
| GET | `/api/knowledge/releases` | release 清单、生命周期状态与 active release |
| GET | `/api/knowledge/guideline-graphs` | 真实指南图库摘要（不返回节点正文） |
| GET | `/api/knowledge/guideline-graphs/<guideline_id>` | 单指南图（提取候选，非临床审核） |
| GET | `/api/knowledge/releases/<release_id>` | 单个 release 清单与哈希 |
| GET | `/api/knowledge/subgraph` | 按推荐/术语返回有限子图、分页与截断 |
| GET | `/api/knowledge/audit` | 检索审计日志（retrieval_id/快照/release/结果/预算） |
| GET | `/api/knowledge/evidence/<span_id>` | 证据片段与来源；未知 span 返回 404 |
| POST | `/api/knowledge/terms/resolve` | `{terms, release_id?}`；返回 resolved/ambiguous/unresolved |
| POST | `/api/knowledge/search` | `{patient_snapshot或ehr_view, queries, intent?, limit?, max_hops?}` |
| POST | `/api/knowledge/actions/validate` | `{patient_snapshot, knowledge_context_id, candidate_plan}` |
| POST | `/api/knowledge/review-plan` | `{knowledge_context_id, review_panels?, review_metrics?}` |
| POST | `/api/knowledge/episode-end` | `{knowledge_context_id, explicit_clinical_end?, end_reason?}` |
| POST | `/api/knowledge/releases/publish` | `{release_id, rationale?}`；demo release 会被拒绝 |
| POST | `/api/knowledge/releases/withdraw` | `{release_id, reason?}` |
| POST | `/api/knowledge/releases/rollback` | `{release_id, target_release_id, reason?}` |

均为只读检索接口；release 生命周期接口只改变进程内 release 状态，不修改图内容、不执行医嘱。默认 demo release 不允许发布。

## 8. 演示夹具说明

`fixtures/neonatal_graph_rag_demo.json` 是**合成夹具**，用于契约测试与工作流演示：

- release：`DEMO-RELEASE-NOT-FOR-PRODUCTION`，`knowledge_version=demo-neonatal-2026-11-15`；
- 规模：100 个业务节点 + 201 个别名节点 / 328 条边 / 10 条推荐 / 7 个证据片段 / 15 个运行时绑定 / 11 个监测面板；
- 覆盖：RDS 的 CPAP/NIPPV/有创通气/肺表面活性物质、停止 CPAP、肺炎抗菌药、EOS 不常规抗菌药、低风险观察、血培养、胸片；
- 故意包含歧义别名 `PS` 与 `not_routine` 推荐，用于验证“不静默选候选”和“不 routine 不能授权 start”；
- 所有文本、来源、证据均为合成，`publishable:false`。真实指南不得直接替换本文件而不做版本与审核登记。

## 9. 测试

```bash
cd rag
PYTHONPATH=.. python3 -m unittest \
  tests.knowledge.test_knowledge \
  tests.knowledge.test_knowledge_planning \
  tests.knowledge.test_knowledge_api \
  tests.knowledge.test_knowledge_quality \
  tests.knowledge.test_knowledge_p0 -v
```

测试覆盖：release 哈希稳定性、术语 resolved/ambiguous/unresolved、四态条件与组合、最新值/冲突/缺省、图检索路径与排除、监测面板、未来事实隔离、动作支持/阻断/待检重复/未来互斥组合/时间线/陈旧快照、观察稳定性与危险征象、复查面板/指标基线、维持方案继承、病程结束门控、Agent 补查与预算、release 发布/撤回/回滚、HTTP 路由与错误码、结构/来源/CQ 验证与发布门控。

## 10. 验证与质量评估

验证方法来自公开的 KG/SHACL/CQ/GraphRAG 评估工作，按本地图模型落地为确定性检查，不引入 LLM 打分：

| 方法 | 来源 | 本地检查 |
|---|---|---|
| SHACL 风格结构约束 | Pareti & Konstantinidis, arXiv:2112.01441；Figuera et al., arXiv:2101.07136 | 引用完整性、节点/边类型、条件 AST 操作符与事实名、binding/operation 覆盖、冲突对称、release 字段 |
| KG 质量维度与来源审计 | Huaman, arXiv:2208.07779；Li et al., arXiv:2212.00994；Cortes et al., arXiv:2508.21774 | completeness/provenance/consistency/versioning/understandability/licensing；证据链与 source 元数据 |
| 能力问题（CQ） | Wisniewski et al., arXiv:1811.09529；Keet & Khan, arXiv:2412.13688；JMIR 2023, DOI 10.2196/46477 | 33 条开发/回归 CQ + 12 条 held-out CQ：正向、缺失、冲突、未记录、未来事实、歧义/未解析、动作校验、陈旧快照、发布门控、证据定位 |
| GraphRAG 评估与负例 | Han et al., arXiv:2502.11371；Zeng et al., arXiv:2506.06331；Xiao et al., arXiv:2506.02404 | 不可回答/无种子/未来事实负例；推荐与证据召回；拒绝伪造路径 |
| 幻觉/遗漏/路径真实性 | Ghanem & Cruz, arXiv:2502.05239 | 每个返回的 evidence path step 必须是 release 中真实存在的边；span/source 必须存在 |
| 临床 GraphRAG 评估协议 | BMJ Digital Health 2026, DOI 10.1136/bmjdh-2026-000041 | 用固定病例快照 + 问题集做患者特异性检索/动作检查（本地为合成病例） |

运行：

```bash
# 验证脚本属内部研发工具（tools/），不随发布分支分发；在研发树根目录执行：
PYTHONPATH=. python3 tools/validate/validate_knowledge_graph.py --strict
```

输出：`data/generated/validation/knowledge_graph_validation_<date>.json` 与 `.md`；held-out 用
`--cqs rag/knowledge/fixtures/competency_questions_heldout.json` 单独输出。

**2026-11-16 实测**（合成 fixture，非临床）：

- 结构：560 项检查，0 error；
- 来源/证据链：47 项检查，0 error；20 条 warning 均来自 `synthetic_fixture` 未做临床审核；
- 患者图：50 例、2,665 条 record、5,580 项不变量检查，0 error；
- CQ：开发/回归 33/33 通过（1.0）；held-out 12/12 通过（1.0）；
- 发布门控：demo release 拒绝发布；reviewed release 可发布；未审核 span/source、缺 locator/quote、retracted source、缺许可均被阻断；
- 质量维度：completeness/provenance/consistency/versioning/understandability/licensing 均 1.0，总分 1.0；
- `goal.met = true`（目标：结构/来源/患者图 0 error、CQ 100%、release gate 通过、质量分 ≥0.9）。

验证过程中发现并修复的真实问题：

1. `evidence_paths` 把 `recommendation -[RECOMMENDS]-> action` 反向渲染成 `action -[RECOMMENDS]-> recommendation`；已按真实边方向输出（`retrieval.py::_path_strings`）。
2. 排除条件/条件值冲突未进入 `missing_information`，导致 `unknown` exclusion 与 `conflict` 被静默；已补 `排除条件未知` / `临床指征存在冲突值` 等状态。
3. `抗菌药` 未映射到 `action:antimicrobial`，面板节点无别名；已补别名并加入 CQ/质量维度。

边界：这些结果只证明合成 release 满足本地结构、来源与 CQ 契约；CQ 通过不等于临床正确，不能写成临床有效性、指南依从性或医院可部署性。真实指南图仍需来源、版本、审核、证据定位与临床专家复核。

## 9a. 真实指南图构建（2026-11-16）

`guideline_graph.py` 把 `guideline_parsing` 的 chunk 转成单指南图：`guideline/recommendation/condition/exception/timeframe/chunk/topic`，并通过 `supported_by`/`requires`/`excludes`/`within_time` 保留原文定位。它不是人工审核后的推荐库：节点 `review_status=extracted`、`publishable=false`，`condition.field` 无法映射时保持 `unmapped`。

- 构建：`tools/build/build_guideline_graph.py --chunks-dir data/generated/parsing --manifest data/guidelines/guideline_pdfs.json --output data/generated/validation/guideline_graph_library.json --strict`
- 读取：`load_guideline_graph_library()` 与 `GET /api/knowledge/guideline-graphs`、`GET /api/knowledge/guideline-graphs/{guideline_id}`
- 本次实跑：37 份本地 PDF、37 张图、802 推荐候选、852 条件候选、103 例外候选、38 时间窗、276 关系；结构校验 0 error、406 warning（多为字段未映射/缺页码章节）。
- 边界：这是结构提取与定位，不是临床审核；没有人工金标准，不能用候选数量或 0 error 代替推荐召回率/条件准确率。详见内部研发报告（不随仓库分发）。

### 10.1 P0 收尾结果（2026-11-16）

- 全量 P0 矩阵（62 项）：**62 完成 / 0 partial / 0 missing / 0 not_applicable**。真实指南 PDF 来源已登记并可解析；临床审核作为 `human_gates.clinical_review` 单独记录为 pending，不混入代码完成率。矩阵与证据见 `data/generated/validation/ehr_requirement_coverage_2026-11-16.json`；验收报告属内部研发记录，不随仓库分发。
- 120 条合成评估场景（dev 80 / lock 40；pre 42 / during 54 / post 12 / action 12）：pass 1.0、retrieval_recall 1.0、negative 1.0、action_safety 1.0、term 1.0；是合成契约，不是临床 gold。
- 规模基准：10,000 推荐 / 86,239 边，p50 610.0 ms、p95 663.9 ms、p99 667.2 ms；50 并发 50 调用完成（吞吐 0.632/s，Python GIL 限制），不是医院并发/SLA。
- 新增 HTTP/Python 契约、检索审计日志、token/字节证据包预算、缓存键（release+snapshot+query+intent+limit+hops）、review 状态机、setting/analyte/examination/reference 节点、supersession、检出限/单位/孕周精度与不可形式化条件。
- 已知非完备项：真实指南 PDF 来源已登记并可解析，但推荐/阈值/适用条件的临床审核仍是 pending 人工门禁；UI 仍以现有工作台卡片为主，未做独立医学证据页；NFR 超时为事后诊断而非硬中断。

## 11. 未完成与限制

- 仅演示夹具，未接入真实指南库；正式图谱仍需指南解析需求、来源、版本、审核与证据定位治理。当前 `publishable:false`，发布 API 会拒绝 demo release。
- 未实现完整 GAG/PEG、FHIR CPG/CQL、CDS Hooks、VoI；本模块不声称与这些标准等价。
- 条件求值只读取 `PatientSnapshot.visible_records()` 的结构化字段，不解析自由文本病史；字段未映射时保持 `unknown`。`clinically_well` 必须是显式 true；缺记录不会被视为稳定。
- 运行时绑定只做契约级校验，不执行真实医嘱、设备或药品操作。
- 检索、校验、复查/维持/病程门控是确定性的规则/图算法，不是临床推理模型；不得将 `eligible_for_simulation` 或 `episode_end_gate` 写成临床有效性、安全性或疾病缓解证据。
