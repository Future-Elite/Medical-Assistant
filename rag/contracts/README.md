# Agent/HIS/RAG 调用说明

## 1. 三方职责

```text
Agent
  1. 调用只读 HIS 工具
  2. 获取带 as_of 的 PatientSnapshot
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
  - 使用人工审核的最小术语检索 PubMed 和指南
  - 保留患者事实、PubMed、指南三个来源
  - 输出引用、版本、时间、适用性状态和 trace
  - 不生成临床决策
```

RAG 不应该拥有一个可以自行读取真实 HIS 的隐式连接器。Agent 先读取 HIS，再显式传入快照，便于权限、审计和测试。

## 2. 推荐调用顺序

### 第一步：Agent 读取 HIS

HTTP 工作台示例：

```http
POST /api/his/snapshot
Content-Type: application/json

{"patient_ref":"syn-patient-001","as_of":"2025-12-31"}
```

返回的是 `PatientSnapshot`。快照中的记录必须保留：

- `record_path`，例如 `medications[0]`；
- `field_path`，例如 `medications[0].name`；
- 单点日期或 `start/end`；
- 结构化字段；
- `suppressed_future` 和 warnings。

### 第二步：Agent 调用 RAG

```json
{
  "request_id": "agent-case-001",
  "question": "该患者当前高血压相关指南推荐和近期研究证据是什么？",
  "as_of": "2025-12-31",
  "top_k": 6,
  "source_kinds": ["pubmed", "guideline"],
  "patient_snapshot": {
    "patient_ref": "syn-patient-001",
    "as_of": "2025-12-31",
    "source": "synthetic_chart",
    "reviewed_external_terms": ["hypertension"],
    "facts": [
      {
        "collection": "diagnoses",
        "event_type": "diagnosis",
        "record_path": "diagnoses[0]",
        "field_path": "diagnoses[0].name",
        "time": "2024-02-01",
        "fields": {"name": "高血压"}
      }
    ]
  },
  "english_terms": ["hypertension"],
  "mesh_terms": ["Hypertension"]
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
- `trace.source_separation`：来源分组状态。

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
- HIS 原始 JSON；
- 病历自由文本、主诉、病史叙述；
- 未经审核的患者事实字段；
- 凭据。

当前实现的 `trace.external_terms` 只记录最小外部术语，不记录患者快照原文。

## 4. 时间规则

- `as_of` 是患者事实和外部证据的可见日期边界；
- 未来患者记录不会进入 `patient_hits`；
- 开放用药区间可以在 `as_of` 继续有效，但不泄露之后的停药时间；
- 未来 PubMed 文献、撤回文献和已替代指南会被过滤；
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
| `english_terms` | 英文 | Agent（人工审核） | 审核过的英文医学术语/缩写（如 `type 2 diabetes`、`HbA1c`、`metformin`），**优先参与**英文来源检索与实时 PubMed 查询 | 请填审核后的词；系统不做同义词扩展 |
| `mesh_terms` | 英文（MeSH） | Agent（人工审核） | 审核过的 MeSH 主题词（如 `Diabetes Mellitus, Type 2`） | 同上 |
| `facets` | 中英皆可 | Agent | 结构化问题槽位（population/outcome 等），取值作为检索概念参与匹配 | 避免把人群、结局压成不可审计长句 |
| `filters` | 任意 | Agent | 结构化过滤，如 `{"guideline_version": "2024"}` | 版本过滤只匹配来源明确提供的版本字段 |
| `retrieval_method` | — | Agent | 底层检索方法：`bm25`（默认）/`ql`/`rm3`/`dense`（MedCPT，英文域）/`xlingual`（multilingual-e5，中文问题直接编码跨语言）/`translate`（zh→en 机器译文附加查询变体）/`splade`/`colbert`/`query2doc`，或 `"bm25,xlingual"`/`["bm25","rm3","dense"]` 多路 RRF 融合 | 未指定时为 bm25；未知方法名请求直接 400；神经方法不可用时该方法标 `not_configured`（trace/warnings），不静默降级；零分路线不参与 RRF |
| `rerank` | — | Agent | `true` 时在召回后用 MedCPT cross-encoder 重排 | 未配置时 `rerank_trace.status=not_configured`；重排分数不是证据质量 |
| `patient_snapshot` | 结构化为准 | Agent（从只读 HIS 获取） | 患者事实检索的输入；`reviewed_external_terms` 是快照内审核过的最小外部术语 | 快照原文不会发送给外部连接器 |

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

可直接运行的完整示例文件见 `examples/`：`agent_joint_retrieval_request.json`（高血压）、`agent_joint_retrieval_request_diabetes.json`（含 `user_terms` 审核映射）、`agent_joint_retrieval_request_question_english.json`（问题内英文词自动参与）、`agent_joint_retrieval_response.json`（响应字段）与 `agent_error_response.json`（错误）。

问题：`"该患者 metformin 治疗2型糖尿病的近期证据？"`，Agent 另提供 `user_terms={"2型糖尿病": ["type 2 diabetes"]}`。

- `question`：中文原样保留 → 参与词面检索与审计；
- `question_english_terms`：系统自动得到 `["metformin"]`（常见功能词已过滤）；
- `user_terms` 展开：`2型糖尿病` 出现在问题中 → 英文追加 `type 2 diabetes`，记入 `expansion_trace`；
- 若 Agent 还提供 `english_terms=["metformin","glycemic control"]`、`mesh_terms=["Diabetes Mellitus, Type 2"]`，则实时 PubMed 收到的查询词为 审核英文 + 问题英文词 + MeSH 的并集（去重后最多 12 个，AND 连接）；
- 如果问题换成 `"该患儿支气管哮喘的长期管理建议和依据是什么？"` 且不提供任何英文术语：中文片段按 CJK 二元组直接匹配中文来源（如中文指南）；英文来源若无词命中则不会强行返回。

### 7.4 中文/英文参与检索的完整规则

- **中文→中文**：中文片段以 CJK 二元组（“新生儿黄疸” → 新生/生儿/儿黄/黄疸）参与词面匹配，可直接命中中文来源；纯数字 token（年份、序号）不参与匹配。
- **英文字段→英文来源**：`english_terms` / `mesh_terms` / `user_terms` 展开 / 问题内英文词，按上述规则进入英文检索；四者都可追溯。
- **不自动翻译**：中文概念绝不会被自动翻译成英文；英文查询词只能来自上面的显式来源。
- **实时模式**：没有任何英文词时，实时 PubMed 优先退回概念中的 ASCII 词，其次才是中文片段；实时失败不自动回退 synthetic 数据。


## 8. 实时来源模式

请求可增加 `source_mode`：

- `simulated`：默认 synthetic fixture；
- `live_pubmed`：真实 NCBI PubMed + 官方指南元数据/链接；
- `live`：需要 PubMed 和指南 live adapter 都被本地配置启用（当前本地配置 `allow_live_pubmed`/`allow_live_guidelines` 默认均为 `True`；把对应开关改为 `False` 即可禁用该模式）。

实时 PubMed 连接器只接收审核后的 `english_terms`/`mesh_terms` 和必要的非敏感问题内容。WHO 指南首期是 `metadata_only`，未确认全文许可时只返回官方 URL、稳定 ID、许可声明和定位，不把全文送入检索。实时失败不会自动 fallback 到 synthetic。


当前 HTTP 服务使用 `synthetic_chart`、`pubmed-fixture` 和 `guideline-fixture`，只证明接口和机制可运行。真实 HIS/PACS、实时 PubMed、正式授权指南库、中文人工标注和临床有效性验证尚未完成。

## 9. 查询相关度门与演示示例

检索结果中的每条候选必须至少被一个检索信号命中（词面 BM25 分数 > 0 或语义分数 > 0），否则不会仅因"来源里有该条目"而获得 RRF/权威性/新鲜度分数。无关候选被排除时会写入 warnings（“已排除 N 条与查询无检索相关度……”）。

工作台内置八个演示预设（下拉框“演示示例”），选择后自动填充问题、患者引用、中英文术语与 MeSH：

1. **示例1 高血压**：`syn-patient-001` + `as_of=2025-12-31`，问题“该患者高血压的指南推荐和最新证据是什么？”。预期：返回高血压指南（ver 2024, rec 2.1）和高血压文献；无相关度候选被剔除提示。
2. **示例2 2型糖尿病**：`syn-patient-002` + `as_of=2025-12-31`，问题“该患者2型糖尿病的初始降糖治疗有哪些指南推荐和近期研究证据？”。预期：返回糖尿病指南（ver 2025, rec 3.2）和 metformin 文献；高血压指南不会串入（相关度门）。术语：`english_terms=["type 2 diabetes","metformin"]`、`mesh_terms=["Diabetes Mellitus, Type 2"]`。
3. **示例3 时间与版本过滤**：`syn-patient-001` + `as_of=2024-06-01`。预期：仅保留 2024 指南与 2024 文献；2023 旧版被替代、2025 后条目属于未来而被排除。
4. **示例4 新生儿黄疸**：`syn-patient-003` + `as_of=2025-12-31`，问题“该患儿新生儿黄疸的胆红素阈值和光疗指征有哪些推荐和近期证据？”。预期：命中黄疸诊断与胆红素化验；返回黄疸指南（ver 2025, rec 4.1）与光疗文献（pmid-1004）。
5. **示例5 婴儿发热**：`syn-patient-004` + `as_of=2025-12-31`，问题“2月龄婴儿发热的评估推荐和近期证据是什么？”。预期：返回婴儿发热评估指南（ver 2024, rec 2.3）与队列研究（pmid-1005）；疫苗/黄疸条目不会串入。术语：`english_terms=["fever evaluation"]`、`mesh_terms=["Fever"]`。
6. **示例6 婴儿疫苗**：`syn-patient-005` + `as_of=2025-12-31`，问题“该婴儿的常规疫苗接种推荐和不良反应证据是什么？”。预期：命中接种记录；返回免疫规划指南（ver 2025, rec 1.2）与安全性监测文献（pmid-1006）。
7. **示例7 药品安全**：`syn-patient-006` + `as_of=2025-12-31`，问题“该患者服用氨氯地平后出现的周围性水肿，有哪些相关研究证据？”。预期：`clinical_intent=drug_safety`；命中用药与症状记录；仅返回氨氯地平相关文献（pmid-1007），无匹配指南。
8. **示例8 中文直接匹配**：`syn-patient-007` + `as_of=2025-12-31`，问题“该患儿支气管哮喘的长期管理建议和依据是什么？”，不填任何英文术语。预期：中文问题通过 CJK 二元组直接命中中文指南（guide-cn-asthma-2025, ver 2025, rec 1.1），并命中哮喘诊断记录；英文/其他病种来源不串入。

注：纯数字查询 token（如“2月龄”的“2”、“2024年”的“2024”）不参与词面匹配，避免误串“type 2 diabetes”或年份相近的无关条目；时间语境由 `as_of` 与过滤器表达。

**问题参与检索（中英文规则）：**
- 问题中的英文词（≥2 字符、含字母、过滤常见功能词与纯数值）自动加入英文检索词，记录在 `trace.query_plan.question_english_terms`，并随 `trace.external_terms` 发往实时 PubMed；
- 问题中的中文片段通过 CJK 二元组参与匹配（可直接命中中文来源），分词与 `patient_retrieval`/`evidence` 一致；
- 中文概念展开为英文仅使用调用方审核的 `user_terms` 映射，不自动翻译；未提供映射的中文概念不会生成英文查询词；
- 实时 PubMed 在没有任何英文词时，优先退回概念中的 ASCII 词，其次才是中文片段。
