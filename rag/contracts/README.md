# Agent/HIS/RAG 调用说明

## 1. 三方职责

```text
Agent
  1. 调用只读 HIS 工具
  2. 获取带 as_of 的 PatientSnapshot（新生儿：出生胎龄、生后小时、呼吸支持等）
  3. 将 PatientSnapshot 和问题传给 RAG
  4. 读取 EvidencePackage
  5. 根据证据和审计结果组织上层回答

HIS 工具
  - 只读
  - 返回结构化患者事实和稳定 record_path/field_path
  - 在 HIS 边界执行 as_of
  - 不写回病历、医嘱或处方

RAG
  - 在本地 PatientSnapshot 中检索患者事实
  - 使用人工审核的最小术语检索 PubMed 和新生儿指南
  - 保留患者事实、PubMed、指南三个来源
  - 输出引用、版本、时间、适用性状态和 trace
  - 不生成临床决策
```

RAG 不应该拥有一个可以自行读取真实 HIS 的隐式连接器。Agent 先读取 HIS，再显式传入快照，便于权限、审计和测试。

v6.6.4 的 live 指南注册表**只含新生儿指南元数据**（肺炎 2025、RDS 2025 更新版、RDS 中文解读）；成人指南不再注册。live 模式还注册 `guideline-fulltext-live`：检索时实时查询 Europe PMC，仅对 `isOpenAccess=Y` 的记录抓取 JATS 全文并按章节分块；非 OA 记录只返回书目/摘要与链接（`full_text_allowed=false`）。全文不落盘、不缓存、不再分发，调用方负责在复用前核对 `license`。

## 2. 推荐调用顺序

### 第一步：Agent 读取 HIS

HIS 快照由**调用方**提供（服务不持有内置患者数据）：Agent 从自己的只读 HIS 取出结构化快照，
随请求传入。工作台的 `/api/his/snapshot` 端点只做契约演示——把它按 `as_of` 过滤后原样回显：

```http
POST /api/his/snapshot
Content-Type: application/json

{
  "patient_snapshot": {
    "patient_ref": "syn-neo-001",
    "as_of": "2026-04-15",
    "source": "caller_his",
    "reviewed_external_terms": ["respiratory distress syndrome"],
    "facts": [
      {"collection": "birth", "event_type": "birth", "record_path": "birth[0]",
       "field_path": "birth[0].ga_days", "time": "2026-04-10",
       "fields": {"subject_role": "patient", "ga_days": 212, "ga_text": "30周+2天",
                  "birth_weight_g": 1450, "data_status": "observed_positive"}},
      {"collection": "respiratory_support", "event_type": "support",
       "record_path": "respiratory_support[0]", "field_path": "respiratory_support[0].mode",
       "start": "2026-04-10", "end": null,
       "fields": {"subject_role": "patient", "mode": "CPAP", "fio2": "0.30",
                  "fio2_unit": "fraction", "data_status": "observed_positive"}}
    ]
  }
}
```

缺少 `patient_ref`/`as_of` 会显式报 400。返回的是过滤后的 `PatientSnapshot`。快照中的记录必须保留：

- `record_path`，例如 `respiratory_support[0]`；
- `field_path`，例如 `respiratory_support[0].mode`；
- 单点日期或 `start/end`；
- 结构化字段（含 `subject_role` 与 `data_status`）；
- `suppressed_future` 和 warnings。

**新生儿年龄字段约定**（详见 `../demo/README.md` §2.1）：

| 字段 | 含义 | 约束 |
|---|---|---|
| `ga_days` | 出生胎龄总天数 | 不得由出生体重反推 |
| `ga_text` | 原始“周+天”文本 | 与 `ga_days` 同时保留 |
| `pna_hours` | 生后小时数 | 急性呼吸场景至少到小时 |
| PMA | `ga_days + floor(pna_hours/24)` | 由 Agent 派生并标注；RAG 不推导 |
| 矫正年龄 | 随访场景 | 不与 PMA 混用 |

**数据状态词表**：`observed_positive` / `observed_negative` / `not_recorded` / `pending` / `unavailable` / `hypothesis`。
`pending`（培养待回报）与 `not_recorded`（未记载）不得转译成阴性。母体事实用 `fields.subject_role="mother"`，患儿事实为 `"patient"`。

### 第二步：Agent 调用 RAG

```json
{
  "request_id": "agent-neo-001",
  "question": "该患儿新生儿呼吸窘迫综合征的肺表面活性物质指征和呼吸支持推荐是什么？",
  "as_of": "2026-04-15",
  "top_k": 6,
  "source_kinds": ["guideline", "pubmed"],
  "patient_snapshot": {
    "patient_ref": "syn-neo-001",
    "as_of": "2026-04-15",
    "source": "caller_his",
    "reviewed_external_terms": ["respiratory distress syndrome", "pulmonary surfactant"],
    "facts": [
      {"collection": "respiratory_support", "event_type": "support", "record_path": "respiratory_support[0]",
       "field_path": "respiratory_support[0].mode", "start": "2026-04-10", "end": null,
       "fields": {"subject_role": "patient", "mode": "CPAP", "fio2": "0.30"}}
    ]
  },
  "english_terms": ["respiratory distress syndrome", "pulmonary surfactant"],
  "mesh_terms": ["Respiratory Distress Syndrome, Newborn", "Pulmonary Surfactants"]
}
```

### 第三步：Agent 读取结果

重点读取：

- `patient_hits`：患者事实命中；
- `evidence_groups.pubmed`：PubMed 结果；
- `evidence_groups.guideline`：指南结果；
- `citations`：只能在同一个 `retrieval_id` 内打开；
- `applicability`：结构化适用性提示；
- `warnings`：连接器失败、时间过滤、版本替代和数据限制；
- `trace.patient_context`：是否收到患者快照、可见事实数和未来事实抑制数；
- `trace.external_terms`：发往外部检索的最小审核术语；
- `trace.source_separation`：来源分组状态；
- `trace.joint_selection`：分源联合选择轨迹（名额分配，见第 9 节）；
- `trace.query_review`：检索式自检结果（见第 9.2 节）。

**引用关联规则（稳定外键）**：`ranked_documents[].citation_id`、`evidence_groups[*][].citation_id`
与 `citations[].citation_id` 是**同一个值**，用它关联引用；**不要**按数组下标在两份列表之间自行配对
（下标同序只是实现巧合，不是接口）。管线内置一致性校验：外键错配会直接抛错，而不是静默返回。
`citation_id` 还是审计回答的标注依据（`[<retrieval_id>:cit-N]`）与 `open_citation` 的入参。

## 3. 外部数据最小化规则

允许发送给 PubMed/指南连接器：

- 原始问题中完成检索所需的非敏感内容；
- 人工审核的英文术语；
- 人工审核的 MeSH 术语；
- `as_of`；
- 来源和研究类型过滤。

禁止发送：

- `patient_ref`；
- 患者姓名、身份证号、住院号；
- HIS 原始 JSON（含母体记录原文）；
- 病历自由文本、主诉、病史叙述；
- 未经审核的患者事实字段；
- 凭据。

当前实现的 `trace.external_terms` 只记录最小外部术语，不记录患者快照原文。

## 4. 时间规则

- `as_of` 是患者事实和外部证据的可见日期边界；
- 未来患者记录不会进入 `patient_hits`；
- 开放呼吸支持/用药区间可以在 `as_of` 继续有效，但不泄露之后的停止时间；
- 未来 PubMed 文献、撤回文献和已替代指南会被过滤；**指南版本标签不等于发表时间**（如 RDS `version_id="2025"`、`published_at="2026-03-09"`，回顾时点早于发表日则不视为可用）；
- 无法判断指南是否适用于患者时，输出 `not_assessed` 或 `unknown`，不能写成“适用”。

## 5. 适用性状态解释

`applicability` 只表示结构化字段匹配提示：

- `possible_match`：患者事实与指南结构化条件存在匹配，需要人工核对；
- `mismatch`：没有找到指南要求的条件，不等于临床上一定不适用；
- `unknown`：部分条件找到，部分条件缺失；
- `not_assessed`：指南没有结构化条件，或没有提供患者快照。

这些状态不是诊断、治疗建议、指南符合性证明或临床决策。

## 6. Agent 不应做的事

- 不把 `score`、BM25、RRF 或 semantic score 当作证据质量；
- 不把一条 PubMed 研究结果改写成当前指南推荐；
- 不把一般医学知识写成患者事实；
- 不把 `mismatch` 写成患者不能使用某治疗；
- 不使用不属于当前 `retrieval_id` 的 citation；
- 不把 `not_checked` 写成已完成人工或模型验证；
- 不因病历没有记录就断言患者没有该疾病或用药；
- 不把 `pending` 培养写成阴性，不把母体记录写成患儿事实；
- 不绕过引用审计直接输出结论。

## 7. 接口字段定义（中文原始问题与英文术语）

这一节定义请求里"中文的东西"和"英文的东西"分别是什么、会被怎么用。字段的机器可读定义见同目录 `agent_contract.json`（`RetrievalRequest` / `QueryPlan` schema），运行时可通过 `GET /api/tools` 获取同一份语义。

### 7.1 输入字段

| 字段 | 语言 | 谁提供 | 作用 | 约束 |
|---|---|---|---|---|
| `question` | 中文（可夹英文药名/缩写） | Agent | **中文临床原始问题，原样保留、不做翻译**；作为精确查询参与词面检索，并用于结果审计（`query_plan.original_question`） | 非空；不要把原始病历粘贴进来 |
| `as_of` | ISO 日期 | Agent | 证据与患者事实的可见截止边界 | 未来记录/文献、撤回文献、被替代指南在边界外被排除 |
| `patient_terms` | 中英皆可 | Agent | 人工审核的最小化患者相关术语，作为检索概念参与匹配 | 不得放患者标识或自由文本病史 |
| `user_terms` | 键=中文概念，值=英文术语 | Agent（人工审核） | **中文概念→英文术语的唯一展开通道**：仅当键出现在问题/患者术语文本中才生效，展开记录到 `query_plan.expansion_trace`（`source=reviewed_user_terms`） | 不调用自动翻译/同义词服务；未提供映射的中文概念不会生成英文查询词 |
| `english_terms` | 英文 | Agent（人工审核） | 审核过的英文医学术语/缩写（如 `respiratory distress syndrome`、`pulmonary surfactant`、`CPAP`），**优先参与**英文来源检索与实时 PubMed 查询 | 请填审核后的词；系统不做同义词扩展 |
| `mesh_terms` | 英文（MeSH） | Agent（人工审核） | 审核过的 MeSH 主题词（如 `Respiratory Distress Syndrome, Newborn`） | 同上 |
| `facets` | 中英皆可 | Agent | 结构化问题槽位（population/outcome 等），取值作为检索概念参与匹配 | 避免把人群、结局压成不可审计长句 |
| `filters` | 任意 | Agent | 结构化过滤，如 `{"guideline_version": "2025"}` | 版本过滤只匹配来源明确提供的版本字段 |
| `retrieval_method` | — | Agent | 底层检索方法：`bm25`（默认）/`ql`/`rm3`/`dense`（MedCPT，英文域）/`xlingual`（multilingual-e5，中文问题直接编码跨语言）/`m3`（BGE-M3）/`translate`（zh→en 机器译文附加查询变体）/`splade`/`colbert`/`query2doc`，或 `"bm25,xlingual"`/`["bm25","rm3","dense"]` 多路 RRF 融合 | 未指定时为 bm25；未知方法名请求直接 400；神经方法不可用时该方法标 `not_configured`（trace/warnings），不静默降级；零分路线不参与 RRF |
| `rerank` | — | Agent | `true` 时在召回后用 MedCPT cross-encoder 重排 | 未配置时 `rerank_trace.status=not_configured`；重排分数不是证据质量 |
| `patient_snapshot` | 结构化为准 | Agent（从只读 HIS 获取） | 患者事实检索的输入；`reviewed_external_terms` 是快照内审核过的最小外部术语 | 快照原文不会发送给外部连接器 |
| `source_mode` | — | Agent/工作台 | `live` 或 `custom` | 仅两种；`custom` 库为空时显式报错 |

### 7.2 输出/系统自动字段（调用方不要手工填）

| 字段 | 语言 | 含义 |
|---|---|---|
| `query_plan.original_question` | 中文 | 原问题的原样记录（审计：确认没有用英文查询替换原问题） |
| `query_plan.question_english_terms` | 英文 | **系统从问题中自动提取的英文词**（≥2 字符、含字母、过滤常见功能词与纯数值），直接参与英文检索与实时 PubMed 查询 |
| `query_plan.concepts` | 中英 | 从问题、患者术语、facets 提取的概念；中文为连续片段，匹配时按 CJK 二元组切分 |
| `query_plan.variants` | 中英 | 实际参与词面打分的查询变体：原问题 / 概念 / 概念+英文 / 英文+MeSH |
| `query_plan.english_terms` | 英文 | 审核英文术语 + `user_terms` 展开结果的合集（只读视图） |
| `query_plan.expansion_trace` | 中英 | 术语展开轨迹，`source` 取值：`question_ascii`（问题英文词自动提取）、`reviewed_user_terms`（审核映射展开）、`pseudo_relevance_feedback`（RM3 伪相关反馈）、`query2doc_pseudo_document`（Query2doc 伪文档扩展，仅 `retrieval_method=query2doc` 时出现）、`machine_translation_zh_en`（zh→en 机器译文变体，仅 `retrieval_method=translate` 时出现） |
| `trace.external_terms` | 英文/必要中文 | **实际发往外部检索的术语合集** = `english_terms` + `question_english_terms`；患者快照内容绝不在其中 |
| `trace.method_trace` | — | 每个所选检索方法的执行状态（`ok`/`not_configured`/`error`/`fallback`/`unknown`）与 retriever_id；`trace.retrieval_method` 是最终生效的方法串；`trace.rerank_trace` 是重排状态 |

### 7.3 一次中文问题里各字段如何配合（示例）

可直接运行的完整示例文件见 `examples/`：`agent_joint_retrieval_request.json`（RDS）、`agent_joint_retrieval_request_pneumonia.json`（肺炎，含 `user_terms` 审核映射）、`agent_joint_retrieval_request_question_english.json`（问题内英文词自动参与）、`agent_joint_retrieval_response.json`（响应字段）与 `agent_error_response.json`（错误）。

问题：`"该患儿 surfactant 治疗新生儿呼吸窘迫综合征的近期证据？"`，Agent 另提供 `user_terms={"新生儿呼吸窘迫综合征": ["neonatal respiratory distress syndrome"]}`。

- `question`：中文原样保留 → 参与词面检索与审计；
- `question_english_terms`：系统自动得到 `["surfactant"]`（常见功能词已过滤）；
- `user_terms` 展开：`新生儿呼吸窘迫综合征` 出现在问题中 → 英文追加 `neonatal respiratory distress syndrome`，记入 `expansion_trace`；
- 若 Agent 还提供 `english_terms=["respiratory distress syndrome","pulmonary surfactant"]`、`mesh_terms=["Respiratory Distress Syndrome, Newborn"]`，则实时 PubMed 收到的查询词为 审核英文 + 问题英文词 + MeSH 的并集（去重后最多 12 个，AND 连接）；
- 如果问题换成 `"该患儿黄疸的光疗阈值是什么？"` 且不提供任何英文术语：中文片段按 CJK 二元组直接匹配中文来源（如中文指南）；英文来源若无词命中则不会强行返回。

### 7.4 中文/英文参与检索的完整规则

- **中文→中文**：中文片段以 CJK 二元组（“新生儿黄疸” → 新生/生儿/儿黄/黄疸）参与词面匹配，可直接命中中文来源；纯数字 token（年份、序号）不参与匹配。
- **英文字段→英文来源**：`english_terms` / `mesh_terms` / `user_terms` 展开 / 问题内英文词，按上述规则进入英文检索；四者都可追溯。
- **不自动翻译**：中文概念绝不会被自动翻译成英文；英文查询词只能来自上面的显式来源。
- **实时模式**：没有任何英文词时，实时 PubMed 优先退回概念中的 ASCII 词，其次才是中文片段；实时失败显式报错，不回退到任何其它数据（服务无内置语料）。

## 8. 来源模式：live 与 custom（仅这两种）

`source_mode` 只有两个合法值；**服务不内置任何本地语料，也没有 offline/simulated 模式**：

- `live`（默认）：实时 NCBI PubMed + **新生儿指南元数据**/链接。需要 `config.allow_live_pubmed` 与 `config.allow_live_guidelines` 同时为 `True`（默认如此）；实时失败显式报错，**不自动回退**到任何其它数据。实时 PubMed 连接器只接收审核后的 `english_terms`/`mesh_terms` 与问题内英文词，绝不发送患者快照。新生儿指南首期是 `metadata_only`，未确认全文许可时只返回官方 URL、稳定 ID、许可声明和定位。
- `custom`：调用方传入的自有知识库（例如医院指南库、自备文献快照）。库为空时检索**显式报错**，不返回空结果冒充"没有证据"。

### 8.1 custom 来源接口（定义）

**HTTP 注册**（每次一个来源；同 `source_id` 覆盖，覆盖前一条数记入事件）：

```http
POST /api/sources/custom
Content-Type: application/json

{
  "source_id": "hospital-neonatal-guideline-library",
  "kinds": ["guideline", "pubmed"],
  "description": "可选说明",
  "version": "2026-10",
  "documents": [
    {
      "document_id": "local-neo-rds-2026",
      "kind": "guideline",
      "title": "本地新生儿呼吸窘迫综合征路径（示例）",
      "text": "对早产儿呼吸窘迫综合征，…",
      "version_id": "2026",
      "recommendation_id": "1.1",
      "population": "早产儿",
      "locator": "第1章 推荐1.1",
      "published_at": "2026-06-01",
      "status": "current"
    }
  ]
}
```

- `source_id` / `documents` 必填，`documents` 非空；`document_id` / `kind` / `title` / `text` 每篇必填。
- 文档归属固定为来源的 `source_id`：显式写了不同值会报错（避免悄悄改写归属）；省略则继承。
- `kinds` 省略时按 `documents[].kind` 推断；显式声明必须覆盖全部文档 kind。
- 未知字段进入 `metadata` 保留，不丢弃（与内置文档同规则）。
- `version` 是调用方库快照标签；**缺失时每次检索都会带出审计警告**（结果不标记 caller 库版本）。

**其它端点**：
- `POST /api/sources/custom/clear`：清空已注册来源（返回移除数量）；
- `GET /api/sources`：当前两种模式的可用性、已注册来源清单、live 指南注册表与最近注册事件（审计用）。

**Python 接口**：

```python
from rag import CustomSource, CustomSourceLibrary, custom_source_from_dict

library = CustomSourceLibrary()
library.register_payload({"source_id": "lib-1", "documents": [...]})  # 校验并注册
registry, warnings = library.build_registry()                        # 转成检索用连接器
```

**启动预载**：在 `config.py` 设置 `custom_sources_path = "/abs/path/libs.json"`，文件为 `{"sources": [<上文的单来源 payload>, ...]}`（或 JSON 数组）。

**存储边界**：自定义库只在内存中，进程重启即清空；服务不把它写入磁盘、不缓存全文。调用方负责其内容的许可与合规。

### 8.2 新生儿演示档案（`rag/demo/`，不是第三种模式）

```text
GET  /api/demo/cases               读取 9 例合成病例（snapshot + preset）
GET  /api/demo/guidelines          查看三个演示来源（元数据/合成指南/合成文献）及是否已注册
POST /api/demo/guidelines/load     显式登记演示指南库（写入 custom 库）
```

演示库 schema、合成标记、年龄字段与修改规则见 [`../demo/README.md`](../demo/README.md)。未显式加载时 `source_mode=custom` 检索会报“no custom sources registered”。

## 9. 分源联合检索与检索式自检

### 9.1 分源联合检索（三级流程）

```text
第 1 级 收集      各 connector 返回自己的候选；全局去重（DOI > PMID > 标题|kind）；
                  跨来源共享键报 conflicts，不静默合并
第 2 级 分源排序  每个 kind 在自己的池内独立执行：
                  IDF/QL 统计 → 方法打分 → RRF 融合 → 相关度门 → MMR
第 3 级 联合选择  ① 来源可见性保证：每个有相关候选的来源至少 1 个名额
                  ② 意图配额：guideline_recommendation → 指南 ceil(top_k/2)；
                     evidence_update / drug_safety → PubMed 同额
                  ③ 来源内排名轮转补齐（不比较跨来源分数）
                  ④ 按来源内排名交错输出
```

调用方应读 `trace.joint_selection` 审计名额分配：`strategy`、`participating_sources`、`intent_preference`、`guaranteed_minimums`、`selected_per_source`、`merit_filled_per_source`、`candidates_per_source`、`relevant_per_source`、`rationale`。

**为什么补齐用排名轮转**：不同来源的原始检索分数不可比（各自词表的 IDF 尺度不同）。RRF 只融合排名，补齐同样只用来源内排名。`top_k` 不足以覆盖全部有候选来源时，会写入可见性警告，不静默丢弃。

**跨来源分数不可比**：`RankedDocument.score` 是**来源内**分数，只在同一来源内可比。`evidence_groups` 是最终联合选择结果按 kind 的分组视图。

### 9.2 检索式自检（PRESS 风格）

每次检索输出 `trace.query_review`：`english_coverage`（英文来源却无英文词）、`expansion_sources`（实际生效的展开来源）、`numeric_preservation`（问题数值未进入英文/概念词）、`negation_and_conditions`（否定/缺失类表述）、`term_overlap`（英文词与 MeSH 重叠）。

**自检只报告需人工复核的点，不修改检索计划**，不是检索质量评分或临床结论；`status=ok` 仅表示上述启发式检查未触发。

### 9.3 语义召回的实体风险

稠密方法（`dense`/`xlingual`/`m3`/`splade`/`colbert`）会对全部候选给正分，可能带回"语义相近但病种不同"的条目。系统**不做静默过滤**，而是把入选候选中"仅由语义方法召回（词面零分）"的条目逐条写入 warnings，提示核对实体一致性。调用方不应把语义分数解释为同实体证据。

## 9.4 查询保真门控（研究层）

`fidelity_gate` 字段（默认 `off`，保持基线语义）可选：`off/audit/warn/downweight/reject`。

- **审计对象**：中文问题→英文检索计划的转换产物——审核术语映射（`english_terms`/`mesh_terms`）、
  机器译文变体（`translate`）、多语言编码的显式输入词。逐槽位检查：实体覆盖、数值保留、
  否定/时间表达、词表实体串换（`unexpected_entity`）。
- **判定名**：`suspected_drop` / `unexpected_entity` / `numeric_loss` / `numeric_added` /
  `suspected_negation_loss` / `suspected_temporal_loss` / `unverifiable` /
  `no_explicit_conversion`——是确定性词面基线的**假设**，与人工标注量表不同。
- **处置**：`downweight` 对 L2 降权（penalty 默认 0.5）、L3 退出融合；`reject` 对 L2/L3 都退出融合；
  审核映射路线为质量上界分支，v0 对降权/拒绝**降级为警示**（决策 reason 中写明）。
- **审计轨迹**：`trace.query_fidelity` = `policy` / `applied` / `decisions` / `report`（slots + 逐路线
  findings）/ `signal_adjustments`。
- **不静默改写**：被拒绝的译文只从打分输入移除；`query_plan.expansion_trace` 保留翻译记录并标
  `fidelity_gate: rejected`；计划术语（english_terms 等）永不修改。
- **词表**：不内置；调用方提供 `FidelityLexicon`（JSON 结构示例见
  `examples/fidelity_lexicon_example.json`）；未提供词表时串换检测标"不可检测"。

## 10. 查询相关度门与演示病例

检索结果中的每条候选必须至少被一个检索信号命中（词面 BM25 分数 > 0 或语义分数 > 0），否则不会仅因"来源里有该条目"而获得 RRF/权威性/新鲜度分数。无关候选被排除时会写入 warnings（“已排除 N 条与查询无检索相关度……”）。

工作台的演示病例来自 `rag/demo/neonatal_cases.json`（9 例，下拉框“合成病例”），选择后自动填充节点 01–05 的全部 `RetrievalRequest` 字段：

1. **neo-rds**：30周+2天、生后6小时、CPAP FiO₂ 0.30；`as_of=2026-03-10`（RDS 2025 已发表）。
2. **neo-rds-retro**：同结构、`as_of=2026-02-25`（早于 2026-03-09 发表）→ 未来文献被排除。
3. **neo-pneumonia**：生后36小时发热 38.9℃、CRP 升高、血培养 pending。
4. **neo-ttn**：生后8小时呼吸增快、胸片叶间裂积液。
5. **neo-jaundice**：生后72小时总胆红素 285 μmol/L。
6. **neo-pphn**：胎粪吸入、导管前后 SpO₂ 差、iNO。
7. **neo-bpd**：28周、经后年龄36周仍需 CPAP。
8. **neo-hypoglycemia**：胎龄 not_recorded、出生体重 2100 g、血糖 2.1 mmol/L。
9. **neo-gbs**：母体 GBS 阳性、胎膜早破20小时、新生儿无症状。

注：纯数字查询 token（如“2月龄”的“2”、“2026年”的“2026”）不参与词面匹配，避免误串年份或序号；时间语境由 `as_of` 与过滤器表达。演示病例的 `english_terms` 必须人工审核后写入 `preset`，GUI 不自动翻译。

**问题参与检索（中英文规则）：**
- 问题中的英文词（≥2 字符、含字母、过滤常见功能词与纯数值）自动加入英文检索词，记录在 `trace.query_plan.question_english_terms`，并随 `trace.external_terms` 发往实时 PubMed；
- 问题中的中文片段通过 CJK 二元组参与匹配（可直接命中中文来源），分词与 `patient_retrieval`/`evidence` 一致；
- 中文概念展开为英文仅使用调用方审核的 `user_terms` 映射，不自动翻译；未提供映射的中文概念不会生成英文查询词；
- 实时 PubMed 在没有任何英文词时，优先退回概念中的 ASCII 词，其次才是中文片段。
