# RAG 直接调用使用指南

本目录是可被 Python Agent 直接调用的只读 RAG 组件，**v6.6.4 起收窄为新生儿专科场景**，**v6.6.5 起审核术语改用概念绑定的 `term_mapping` 映射表，并为指南增加结构化适用条件核对**：在证据检索之上提供受控中英文/MeSH 查询规划、字段化 BM25 与可选语义/RRF 融合、患者事实 + PubMed + 版本化指南的分源联合检索、引用打开、指南适用性核对和回答审计。另含可选的**查询保真审计与门控**研究特性（`fidelity_gate`，默认 `off`，见第 5.8 节），用于审计中文问题→英文检索计划的转换产物。

推荐的主调用路径是：

```text
（Agent 先通过只读 HIS 工具获取 PatientSnapshot）
  -> 构造连接器
  -> 注册到 ConnectorRegistry
  -> 创建 EvidenceRetrievalPipeline
  -> 创建 EvidenceRAG（EvidenceRAG 也可由服务端 create_tool 按来源模式构造）
  -> retrieve_evidence
  -> open_citation / audit_grounded_answer
```

HTTP 服务和浏览器工作台只是这个核心调用层之上的附加封装，见第 13 节。不需要启动 `server.py` 就可以直接使用 RAG。

当前版本在包 `__init__.py` 中以 `__version__` 声明（当前 `6.7.0`）；运行时 trace 的 `rag_version` 与 `/api/health` 均读取该值，代码中不硬编码版本字符串。

集成分支 v6.6.4：translate 在外部来源召回前生成候选，查询保真 reject 也在外发前生效。`query_plan.machine_translations` 与审核术语分开，实际 PubMed 查询记录在 `trace.connector_trace[].query`。中文 Agent 默认通过现有 translate 槽接入 Qwen 查询转换，英文查询不需转换；未配置或失败仍在 method_trace/warnings 明确报告。

**v6.6.5 查询映射表与指南适用性**：`term_mapping` 把中文概念与英文/MeSH 绑定，映射路径生成概念分组查询变体，PubMed 使用 `[tiab]` / `[MeSH Terms]`；`metadata.applicability_criteria` 声明逐条适用条件，`applicability[].criteria` 给出逐条核对结果。旧 `english_terms` / `mesh_terms` / `user_terms` 与旧 `population_criteria` 分支保留，legacy 路径已做 v6.6.4→v6.6.5 冻结语义差分（6 场景 × 5 策略，逐字段一致）。工作台节点 03 改为 `term_mapping` 映射表输入，结果区新增“指南适用性”面板。

**新生儿专科化（v6.6.4 引入，v6.6.5 沿用）**（详见 §13.4 与 `demo/README.md`）：live 指南注册表只含新生儿指南元数据；演示档案 `rag/demo/` 提供 9 例精选合成新生儿病例与三个调用方来源（元数据 + 合成指南块 + 合成文献），必须显式 `POST /api/demo/guidelines/load` 才注册进 custom 来源。另有独立档案 `rag/demo/his_generated/` 的 11 例场景病例与 `rag/demo/his_generated_30/` 的 30 例 HIS 风格批量病例（同 schema，`load_demo_cases(<该目录>)`；不进入默认 `/api/demo/cases`）。工作台为 01–09 流程化工作台（左侧流程导航 + 纵向步骤卡 + 结果选项卡），输入与 `RetrievalRequest` 逐字段对应。引擎层相对 v6.6.3：13 个文件字节级一致（`pipeline/ranking/fidelity/gate/bm25/lexical/evidence/patient_retrieval/his/connectors/models/neural_adapters/retrieval_adapters`），`query.py`/`pubmed.py`/`guidelines.py`/`guideline_fulltext.py` 为检索修复，`audit.py` 默认改为结构化审计；2026-10-17 差分早于上述改动，不覆盖它们。

模块不会读取原始病历、写回 HIS/PACS、生成诊断或治疗决定。`patient_terms` 只能传入人工审核的最小化术语，不得传入患者姓名、住院号、身份证号、自由文本病史或原始病历。RAG 不自行读取真实 HIS：`patient_snapshot` 由 Agent 读取并授权传入。

## 1. 直接调用的最短示例

在仓库根目录运行下面的代码（包从仓库根导入）。这个示例使用内存中的文档，不联网，适合先验证调用流程：

```python
from rag.agent import EvidenceRAG
from rag.connectors import ConnectorRegistry, InMemoryConnector
from rag.models import SourceDocument
from rag.pipeline import EvidenceRetrievalPipeline

# 1. 创建并注册知识源
registry = ConnectorRegistry()
registry.register(InMemoryConnector(
    source_id="demo-source",
    kinds=("guideline",),
    documents=(SourceDocument.from_dict({
        "document_id": "guide-1",
        "kind": "guideline",
        "title": "示例指南",
        "text": "示例证据文本。",
        "updated_at": "2024-01-01",
        "locator": "第 1 章",
    }, source_id="demo-source"),),
))

# 2. 创建检索管线和 Agent 工具
pipeline = EvidenceRetrievalPipeline(registry)
rag = EvidenceRAG(pipeline)

# 3. 检索证据
result = rag.retrieve_evidence({
    "request_id": "python-demo-001",
    "question": "示例指南的证据是什么？",
    "source_kinds": ["guideline"],
    "top_k": 5,
})

print(result["retrieval_id"])
print(result["citations"])

# 4. 打开本轮检索中的引用
citation = rag.open_citation({
    "retrieval_id": result["retrieval_id"],
    "citation_id": result["citations"][0]["citation_id"],
})

# 5. 审计带引用的回答
audit = rag.audit_grounded_answer({
    "retrieval_id": result["retrieval_id"],
    "answer": f"示例证据支持该陈述[{citation['citation']['citation_id']}]。",
})
print(audit["claims"])
```

运行方式：

```bash
python3 path/to/your_script.py
```

也可以只设置导入路径（在仓库根执行）：

```bash
PYTHONPATH=. python3 path/to/your_script.py
```

## 2. 环境和依赖

核心 RAG 代码只使用 Python 标准库，需要 Python 3.10 或更高版本：

```bash
python3 --version
```

如果只使用 `InMemoryConnector`、`CallableConnector`、`VersionedGuidelineConnector`、词面检索方法（bm25/ql/rm3）、本地排序和引用结构审计，不需要安装额外依赖。神经检索方法 `dense`/`xlingual`/`translate`/`splade`/`colbert`/`query2doc` 与 cross-encoder 重排需要 `torch`/`transformers` 与本地模型缓存（在国内网络可经 `HF_ENDPOINT=https://hf-mirror.com` 预下载）。

审计默认只做**引用结构检查**：每条主张必须引用本次 `retrieval_id` 内的 `citation_id`；语义支持标记 `not_checked`，精确率/召回率为 `None`，不用词面规则冒充模型校验。需要语义主张校验的调用方自行注入 `ClaimVerifier` 实现（见 `rag/audit.py` 的 `ClaimVerifier` 协议）。神经方法不可用时在 `trace.method_trace` 中标记 `not_configured`（附原因），绝不静默降级。

环境变量：

| 变量 | 作用 |
|---|---|
| `RAG_SERVER_PORT` | 覆盖服务端口（无需修改 `config.py`）。 |
| `RAG_ALLOW_MODEL_DOWNLOAD=1` | 显式允许联网下载缺失的 HF 模型；缺省离线，缓存未命中时快速失败并给出预下载指引。 |

## 3. 核心对象和调用关系

### 3.1 `ConnectorRegistry`

位置：`rag/connectors.py`

```python
registry = ConnectorRegistry()
registry.register(connector)
```

作用是保存当前可用的知识源连接器，并按照查询计划选择连接器。

主要方法：

| 方法 | 入参 | 返回值 | 说明 |
|---|---|---|---|
| `register(connector)` | 实现连接器协议的对象 | `None` | 注册一个连接器；`source_id` 必须非空且不能重复。 |
| `selected(source_order)` | `Sequence[str]` | 连接器列表 | 按查询计划中的来源顺序返回已注册连接器。 |
| `describe()` | 无 | `list[dict]` | 返回每个连接器的 `source_id` 和 `kinds`。 |

注册重复的 `source_id` 会抛出 `ValueError("duplicate connector: ...")`。

### 3.2 `EvidenceRetrievalPipeline`

位置：`rag/pipeline.py`

```python
pipeline = EvidenceRetrievalPipeline(registry)
package = pipeline.retrieve(request)
```

构造函数：

```python
EvidenceRetrievalPipeline(
    registry: ConnectorRegistry,
    *,
    neural_retrievers: dict | None = None,   # 方法名 -> 语义检索适配器（dense/xlingual/splade/colbert/query2doc/translate）
    reranker=None,                            # cross-encoder 重排适配器
    evidence_budget: int = 3,                 # 每条引用最多提取的关键句数
    semantic_retriever=None,                  # 兼容旧单 dense 槽位；等价于注册 dense
)
```

`retrieve()` 接受一个 `RetrievalRequest`，执行以下步骤：

1. 创建本轮唯一 `retrieval_id`。
2. 若有 `patient_snapshot`，先做本地患者事实检索（保留 `record_path`/`field_path`）。
3. 根据问题、最小化患者术语、审核术语映射、英文/MeSH 术语和 facets 生成 `QueryPlan`。
4. 按来源顺序调用连接器（受 `source_kinds` 过滤）。
5. 收集连接器返回的 `SourceDocument` 并保留 connector trace。
6. 解析 `retrieval_method`，逐步执行词面/神经方法（`method_trace`）；多方法经 RRF 融合；零分路线不参与 RRF。
7. 按 DOI/PMID/标题去重；按 `as_of` 排除未来、撤回和被替代文献；剔除与查询无检索相关度的候选。
8. 权威性/时效性加权、MMR 多样化，得到不超过 `top_k` 条文档；可选 cross-encoder 重排（`rerank=true`）。
9. 生成绑定 `retrieval_id` 的 `Citation`（含版本/推荐/适用人群字段）和关键证据句定位。
10. 返回 `EvidencePackage`（含 `patient_hits`、`evidence_groups`、`applicability`、`source_separation` 和完整 trace）。

连接器异常会被记录到返回包的 `warnings` 和 `trace.connector_trace`，通常不会直接中断整个管线。

### 3.3 `EvidenceRAG`

位置：`rag/agent.py`

```python
rag = EvidenceRAG(
    pipeline,
    sessions=None,
    auditor=None,
)
```

构造参数：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `pipeline` | `EvidenceRetrievalPipeline` | 必填 | 实际执行检索的管线。 |
| `sessions` | `EvidenceSessionStore \| None` | 自动创建 | 保存 `retrieval_id -> EvidencePackage` 的内存会话。 |
| `auditor` | `GroundedAnswerAuditor \| None` | 自动创建 | 默认只做引用结构审计；语义校验需显式注入 `ClaimVerifier`。 |

`EvidenceRAG` 是 Agent 级 JSON 接口，不是回答生成器。它提供三个直接调用方法：

- `retrieve_evidence(arguments)`：联合检索并保存证据包。
- `open_citation(arguments)`：打开本次检索中的一条引用。
- `audit_grounded_answer(arguments)`：审计回答中的主张与本次检索引用。

## 4. 检索输入：`RetrievalRequest`

位置：`rag/models.py`

可以直接构造数据类，更适合 Agent 或 JSON 输入的方式是 `from_dict()`：

```python
request = RetrievalRequest.from_dict({
    "request_id": "request-001",
    "question": "该患儿新生儿呼吸窘迫综合征的指南推荐和最新证据是什么？",
    "as_of": "2026-04-15",
    "patient_terms": ["新生儿呼吸窘迫综合征"],
    "source_kinds": ["pubmed", "guideline"],
    "top_k": 8,
    "term_mapping": [
        {
            "concept": "新生儿呼吸窘迫综合征",
            "aliases": ["NRDS"],
            "english": ["respiratory distress syndrome"],
            "mesh": ["Respiratory Distress Syndrome, Newborn"],
        },
        {
            "concept": "肺表面活性物质",
            "english": ["pulmonary surfactant"],
            "mesh": ["Pulmonary Surfactants"],
        },
    ],
})
package = pipeline.retrieve(request)
```

### 4.1 字段说明

| 字段 | Python 类型 | 默认值 | 是否必填 | 说明 |
|---|---|---:|---:|---|
| `request_id` | `str` | 自动生成 `request-<随机值>` | 否 | 调用方请求标识。建议业务侧自行生成唯一值。 |
| `question` | `str` | 无 | 是 | 中文临床原始问题（可含英文药名/缩写）。原样保留、不翻译；去除首尾空格后不能为空。问题中的英文词会被自动提取为 `question_english_terms` 参与英文检索。 |
| `task` | `str` | `"clinical_question"` | 否 | 任务标签，目前仅记录，不触发额外流程。 |
| `as_of` | `str \| None` | `None` | 否 | 证据与患者事实的可见截止边界，推荐 `YYYY-MM-DD`。未来记录、撤回文献与被替代指南在此边界外被排除。 |
| `patient_terms` | `tuple[str, ...]` | `()` | 否 | 人工审核的最小患者相关术语（中英皆可）；不允许放原始病历、患者标识或自由文本病史。 |
| `source_kinds` | `tuple[str, ...]` | `()` | 否 | 来源类型，例如 `pubmed`、`guideline`、`drug`、`clinical_trial`、`local_knowledge`。 |
| `filters` | `Mapping[str, Any]` | `{}` | 否 | 结构化过滤项，如 `{"guideline_version": "2024"}` 限定指南版本。 |
| `top_k` | `int` | `8` | 否 | 最终返回文档数，必须在 1 到 50 之间。 |
| `user_terms` | `Mapping[str, Sequence[str]]` | `{}` | 否 | **兼容字段（旧调用方）**：中文概念→英文映射；新调用方请用 `term_mapping` 以同时绑定 MeSH。仅当键出现在问题/患者术语文本中才展开，展开记录在 `query_plan.expansion_trace`（`source=reviewed_user_terms`）。 |
| `query_language` | `str` | `"zh"` | 否 | 问题语言标记（字段别名 `language` 亦可）。 |
| `clinical_intent` | `str \| None` | `None` | 否 | `guideline_recommendation` / `evidence_update` / `drug_safety` / `clinical_question`；缺省时由问题文本推断。 |
| `facets` | `Mapping[str, Sequence[str]]` | `{}` | 否 | 结构化临床问题槽位（population/outcome 等），取值作为检索概念参与匹配。 |
| `term_mapping` | `tuple[TermMappingEntry, ...]` | `()` | 否 | **推荐输入**：审核术语映射表。数组行 `{"concept","aliases","english","mesh"}`，或对象 `{概念: {english, mesh, aliases}}` / 旧式 `{概念: [english]}`。英文与 MeSH 与概念绑定，仅当概念/别名出现在问题、患者术语、审核外部术语或 facets 时展开；未命中概念写入 `query_plan.mapping_trace`。映射路径启用概念分组查询变体与 PubMed `[tiab]`/`[MeSH Terms]` 查询式。v6.7.0 起另支持**通用术语映射库**（`config.term_mapping_library_path`）：库行自动参与同一展开流程、同概念请求行优先，`mapping_trace` 逐行记 `origin`（`request`/`library`）。 |
| `english_terms` | `tuple[str, ...]` | `()` | 否 | **兼容字段（旧调用方）**：直接英文术语列表，参与英文检索与实时 PubMed 查询。新调用方应把英文放入 `term_mapping` 对应概念行，以保留概念—术语绑定。 |
| `mesh_terms` | `tuple[str, ...]` | `()` | 否 | **兼容字段（旧调用方）**：直接 MeSH 列表，参与英文检索与实时 PubMed 查询。新调用方应把 MeSH 放入 `term_mapping` 对应概念行。 |
| `patient_snapshot` | `Any \| None` | `None` | 否 | Agent 从只读 HIS 获取的结构化快照对象（解码在管线内完成）。 |
| `source_mode` | `str` | `"live"` | 否 | 仅两种：`live`（实时 PubMed + 新生儿指南元数据 + Europe PMC 指南/同行评议全文）与 `custom`（调用方注册的自有知识库）。病例默认 `live`，不使用合成指南块；实时失败不自动回退，custom 库为空时显式报错。 |
| `retrieval_method` | `str \| tuple[str, ...] \| None` | `None`（等价 bm25） | 否 | 检索方法，见第 12 节。 |
| `rerank` | `bool` | `False` | 否 | `true` 时在排序后追加 cross-encoder 重排。 |
| `fidelity_gate` | `str` | `"off"` | 否 | 查询保真门控（研究特性）：`off`/`audit`/`warn`/`downweight`/`reject`，见第 5.8 节。 |

校验和转换规则：

- `question` 为空或只有空白时抛出 `ValueError("question must be a non-empty string")`。
- `top_k` 先执行 `int()` 转换；不能转换或不在 1 到 50 时抛出 `ValueError("top_k must be between 1 and 50")`。
- 未知检索方法名抛出 `ValueError("unknown retrieval_method: ...")`。
- `fidelity_gate` 不在 `off`/`audit`/`warn`/`downweight`/`reject` 之内时抛出 `ValueError("fidelity_gate must be one of: ...")`。
- 字符串形式的 `patient_terms`、`source_kinds` 会被转换为单元素序列；空字符串会被丢弃。
- `patient_snapshot` 可以是 `PatientSnapshot` 对象或字典（经 `snapshot_from_dict()` 解析）。
- `as_of` 本身不在 `from_dict()` 中强制校验日期；无效日期在排序阶段会按无法解析处理。调用方应在进入 RAG 前自行校验。
- 未提供 `request_id` 时自动生成唯一值。
- `source_kinds` 指向未注册类型时，查询计划可能回退到全部已知连接器，不能将它视作严格的来源白名单。

### 4.2 查询计划 `QueryPlan`

RAG 从 `question`、`patient_terms` 和 `facets` 提取中英文概念。审核英文/MeSH 优先来自 `term_mapping`（概念绑定），旧 `user_terms`/`english_terms`/`mesh_terms` 仍兼容。`QueryPlan` 字段如下：

| 字段 | 类型 | 说明 |
|---|---|---|
| `original_question` | `str` | 原始中文问题（原样保留）。 |
| `variants` | `tuple[str, ...]` | 原始问题、概念组合和受控扩展组合。 |
| `concepts` | `tuple[str, ...]` | 提取后的概念。 |
| `source_order` | `tuple[str, ...]` | 实际连接器执行顺序。推荐类问题会优先使用指南类型连接器。 |
| `filters` | `Mapping[str, Any]` | 查询过滤记录，自动包含 `source_kinds`。 |
| `rationale` | `tuple[str, ...]` | 查询计划形成原因。 |
| `query_language` | `str` | 问题语言标记。 |
| `clinical_intent` | `str` | 推断或指定的临床意图。 |
| `facets` | `Mapping[str, tuple[str, ...]]` | 结构化槽位。 |
| `english_terms` / `mesh_terms` | `tuple[str, ...]` | 审核英文/MeSH 术语（映射表展开在前，兼容列表在后）。 |
| `expansion_trace` | `tuple[Mapping[str, Any], ...]` | 术语扩展轨迹（`reviewed_term_mapping` / `reviewed_user_terms` / 问题英文词 / 机器生成来源）。 |
| `term_mapping` | `tuple[TermMappingEntry, ...]` | 本次请求提供的审核映射表（原样记录）。 |
| `mapping_trace` | `tuple[Mapping[str, Any], ...]` | 映射表逐行轨迹：`expanded` / `not_matched`、命中表面、命中位置、英文/MeSH 产物。 |
| `variant_trace` | `tuple[Mapping[str, Any], ...]` | 查询变体角色（仅映射路径生成）：`original_question`、`zh_concepts`、`mapping_english`、`mapping_mesh`、`mapping_combined`、`english_terms`、`mesh_terms`、`english_and_mesh`、`concepts_and_terms`。 |
| `question_english_terms` | `tuple[str, ...]` | 问题中自动提取的英文词（系统生成，调用方不要手工填写）。 |

## 5. 直接调用检索：`EvidenceRAG.retrieve_evidence`

### 5.1 调用方式

```python
result = rag.retrieve_evidence({
    "request_id": "request-002",
    "question": "该患儿新生儿呼吸窘迫综合征的指南推荐和最新证据是什么？",
    "as_of": "2026-04-15",
    "patient_terms": ["新生儿呼吸窘迫综合征"],
    "english_terms": ["respiratory distress syndrome"],
    "source_kinds": ["pubmed", "guideline"],
    "top_k": 5,
    "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]},
})
```

输入是一个 `Mapping[str, Any]`，字段与 `RetrievalRequest.from_dict()` 相同。该方法会：

1. 将字典转换成 `RetrievalRequest`。
2. 调用 `pipeline.retrieve()`。
3. 把返回的 `EvidencePackage` 保存到 `EvidenceSessionStore`。
4. 调用 `package.to_dict()`，返回普通 JSON 兼容字典。

### 5.2 返回的 `EvidencePackage`

| 字段 | 类型 | 说明 |
|---|---|---|
| `retrieval_id` | `str` | 本轮检索唯一 ID（`retrieval-<hex>`）。引用和审计必须使用同一个值。 |
| `request_id` | `str` | 输入请求 ID 或自动生成值。 |
| `query_plan` | `dict` | 实际查询计划。 |
| `ranked_documents` | `list[dict]` | 联合选择后的最终排序；每项含 `citation_id`（见 5.3）。 |
| `citations` | `list[dict]` | 可追溯引用；`citation_id` 与 `ranked_documents`/`evidence_groups` 中同名字段是同一稳定外键。 |
| `warnings` | `list[str]` | 连接器、方法、过滤、去重或无结果警告。 |
| `trace` | `dict` | 检索过程信息。 |
| `patient_hits` | `list[dict]` | 本地患者事实命中（无快照时为空）。 |
| `evidence_groups` | `dict[str, list[dict]]` | 按来源类型分组的排序结果（保持来源分离）。 |
| `applicability` | `list[dict]` | 指南条目与患者事实的结构化适用性核对（`possible_match`/`mismatch`/`unknown`/`not_assessed`，不是临床决策）。每项含 `criteria`：逐条 `criterion_id`/`field`/`operator`/`expected`/`status`/`observed_value`/`patient_record_paths`/`reason`。 |
| `source_separation` | `dict` | 分组状态摘要。 |
| `patient_context` | `dict` | 患者上下文摘要（可见事实数、未来抑制数）。 |

`EvidencePackage.to_dict()` 会递归把 dataclass、Enum、tuple 转成 JSON 兼容的字典、字符串和数组。

### 5.3 `ranked_documents` 字段

每个排序文档包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `citation_id` | `str \| None` | **稳定外键**（`<retrieval_id>:cit-N`），与本条 `citations[].citation_id` 同值；用它关联引用，不要依赖两个列表的下标同序。 |
| `document` | `dict` | 原始 `SourceDocument`。 |
| `score` | `float` | 来源内排序分数；跨来源不可比，也不是证据质量或推荐等级。 |
| `lexical_score` | `float` | 词面相关度。 |
| `fusion_score` | `float` | RRF 融合分数。 |
| `authority_score` | `float` | 来源权威性先验。 |
| `freshness_score` | `float` | 相对 `as_of` 的时效性分数。 |
| `evidence_status` | `str` | `current`、`unknown_date`、`future_at_query_time`、`withdrawn` 或 `superseded`。 |
| `matched_queries` | `list[str]` | 命中的查询变体。 |
| `rank_explanation` | `list[str]` | 排序解释。 |
| `retriever_id` / `semantic_score` / `fusion_method` / `reranker_id` 等 | 可选 | 多方法检索与重排的逐路记录。 |

**引用关联（稳定外键）**：`ranked_documents[].citation_id`、`evidence_groups[*][].citation_id` 与 `citations[].citation_id` 是同一值，调用方按外键关联；管线内置 `_assert_citation_linkage()`，错配直接抛错而不是静默返回。

### 5.4 `SourceDocument` 字段

连接器返回 `SourceDocument` 对象或字典。使用字典时，`document_id`、`kind`、`title`、`text` 必填且不能为空：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `source_id` | `str` | 否 | 所属连接器 ID；连接器通常通过 `source_id=` 参数注入。 |
| `document_id` | `str` | 是 | 来源内文档 ID。 |
| `kind` | `str` | 是 | 文档类型。 |
| `title` | `str` | 是 | 文档标题。 |
| `text` | `str` | 是 | 可检索正文。 |
| `url` | `str \| None` | 否 | 原文链接。 |
| `published_at` | `str \| None` | 否 | 发布日期。 |
| `updated_at` | `str \| None` | 否 | 更新日期，排序时优先于发布日期。 |
| `locator` | `str \| None` | 否 | 章节、页码或 `Abstract` 等定位。 |
| `pmid` / `doi` | `str \| None` | 否 | PubMed PMID / DOI（去重键：DOI > PMID > 标题）。 |
| `authority` | `str \| None` | 否 | 权威性描述。 |
| `version_id` | `str \| None` | 否 | 指南版本（如 `"2024"`）。 |
| `recommendation_id` | `str \| None` | 否 | 推荐编号（如 `"2.1"`）。 |
| `population` | `str \| None` | 否 | 适用人群字段。 |
| `status` | `str \| None` | 否 | 状态（`withdrawn`/`retracted`/`superseded` 在排序前过滤）。 |
| `superseded_by` | `str \| None` | 否 | 被替代指向。 |
| `index_snapshot` | `str \| None` | 否 | 索引快照标记。 |
| `metadata` | `Mapping[str, Any]` | 否 | 来源附加信息。未知输入字段也会放入这里。 |

### 5.5 `citations` 字段

每条引用包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `citation_id` | `str` | 本轮唯一引用 ID，格式 `retrieval-...:cit-N`。 |
| `source_id` / `document_id` / `kind` / `title` | `str` | 来源与文档标识。 |
| `quote` | `str` | 规范化摘录，默认最多约 360 字符。 |
| `locator` / `url` / `pmid` / `doi` | `str \| None` | 原文定位与标识。 |
| `evidence_status` | `str` | 证据状态。 |
| `actions` | `list[dict]` | 当前包含 `open_evidence` 动作（含版本/推荐/定位参数）。 |
| `key_evidence_sentences` | `list[dict]` | 关键证据句定位（句子文本、句序、字符起止偏移、匹配查询、词面分数）。 |
| `version_id` / `recommendation_id` / `population` | `str \| None` | 指南版本化字段。 |
| `retrieved_at` / `index_snapshot` | `str \| None` | 检索时间与快照标记。 |

`key_evidence_sentences` 是定位辅助，不是临床正确性判断；调用方应使用偏移回到原文核对。空结果表示“该文档在此透明启发式下无匹配句”，不表示无证据。

### 5.6 `trace` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `rag_version` | `str` | 包 `__version__`（当前 `6.7.0`）。 |
| `source_mode` | `str` | 本轮来源模式。 |
| `retrieved_at` | `str` | UTC 时间戳。 |
| `connector_trace` | `list[dict]` | 每个连接器的执行状态。成功含 `source_id`、`status`、`returned`、`kind`；失败含错误类型和阶段。 |
| `candidate_count` / `deduplicated_count` / `selected_count` | `int` | 候选数、跨来源去重后数量与最终选中数量。 |
| `joint_selection` | `dict` | 分源联合选择轨迹：`strategy`、`guaranteed_minimums`、`selected_per_source`、`merit_filled_per_source`、`rationale` 等（见第 5.7 节）。 |
| `query_review` | `dict` | PRESS 风格检索式自检（见第 5.7 节）。 |
| `query_fidelity` | `dict` | 查询保真审计与门控轨迹：`policy`、`applied`、`decisions`、`signal_adjustments`、`report`（见第 5.8 节）。策略为 `off` 时 `applied=false`、`report` 为 `null`。 |
| `retrieval_method` | `str \| None` | 最终生效方法串（如 `"bm25+dense"`）。 |
| `method_trace` | `list[dict]` | 逐方法状态（`ok`/`not_configured`/`error`/`fallback`/`unknown`），含原因与返回数。 |
| `semantic_trace` | `dict` | 语义检索状态汇总。 |
| `rerank_trace` | `dict` | 重排请求状态（`not_requested`/`ok`/`not_configured`/`error`）。 |
| `route_decision` | `dict \| absent` | 规则路由决策（仅 `routing_mode != off` 时出现）：`rule`、`selected_sources`、`rationale`、`notes`、`fallback_all`、`mode`、`applied`、`clinical_intent`、`english_coverage`；`active` 另有 `source_order_after`（见第 5.10 节）。 |
| `query_plan` | `dict` | 语言、意图、英文/问题英文/MeSH 术语，以及 `term_mapping` 的提供/展开/未命中计数和 `variant_roles`。 |
| `external_terms` | `list[str]` | 实际发往外部的术语（审核英文词 + 问题内英文词）。 |
| `patient_context` | `dict` | 快照接收状态、可见事实数、命中数、未来抑制数。 |
| `source_separation` | `dict` | 分源分组状态。 |
| `generation_performed` / `clinical_decision_performed` | `bool` | 固定为 `false`。 |

### 5.7 分源联合检索与检索式自检

**分源联合检索（三级）**：每个来源在自己的词表内独立排序（IDF/QL/RRF/相关度门/MMR），再由联合选择合并：

1. **来源可见性保证**：每个有相关候选的来源至少保留 1 个名额（`top_k` 不足时给出警告）；
2. **意图配额**：`guideline_recommendation` → 指南至少 `ceil(top_k/2)`；`evidence_update`/`drug_safety` → PubMed 同额；
3. **来源内排名轮转补齐**：其余名额按来源轮转（**不比较跨来源分数**）；
4. **交错输出**：最终列表按来源内排名交错，避免单一来源占满 `top_k`。

`ranked_documents[].score` 是**来源内**分数，跨来源不可比；跨来源顺序由联合选择规则决定，解释见 `trace.joint_selection.rationale`。

**检索式自检（`trace.query_review`）**：以 PRESS 风格逐项检查 `english_coverage`（英文来源却无英文词）、`expansion_sources`、`numeric_preservation`（问题数值未进英文/概念词）、`negation_and_conditions`、`term_overlap`。**只报告需人工复核的点，不修改检索计划**，不是检索质量评分。

### 5.8 查询保真审计与门控（研究特性，默认关闭）

`fidelity_gate` 审计的是**中文问题→英文检索计划的转换产物**——审核术语映射（`reviewed_mapping`）、机器译文变体（`translate`）、多语言编码输入（`multilingual_encoding`）——逐槽位检查实体覆盖、数值保留、否定/时间表达与词表实体串换，判定级别 `L0`–`L3` 与判定名（`preserved`、`suspected_drop`、`unexpected_entity`、`numeric_loss`、`numeric_added`、`suspected_negation_loss`、`suspected_temporal_loss`、`unverifiable`、`no_explicit_conversion`）写入 `trace.query_fidelity.report`。

| 策略 | 行为 |
|---|---|
| `off`（默认） | 不审计、不改信号，与既有基线一致。 |
| `audit` | 审计并记录；不改信号，不产生警告。 |
| `warn` | 审计 + 显式警告；不改信号。 |
| `downweight` | `L1`→警告；`L2`→该路信号按 `fidelity_penalty`（默认 0.5）降权后参与融合；`L3`→该路退出检索融合。 |
| `reject` | `L1`→警告；`L2`/`L3`→该路退出检索融合。 |

边界与不确定性：

- **按路线处置，不静默改写**：只有产出问题的路线受影响；查询计划与计划术语永不修改。被拒绝的译文只从检索打分输入移除，`query_plan.expansion_trace` 与 `method_trace` 保留记录并标 `fidelity_gate: rejected`；原信号值保留在 `trace.query_fidelity`。
- **审核映射是质量上界分支**：`downweight`/`reject` 对它降级为警告（放弃调用方已审核的术语在 v0 中不可辩护），决策 `reason` 中写明。
- **不会把不相关变成相关**：降权/拒绝只作用于 `translate` 的词面变体分量与 `xlingual`/`m3` 的语义分数，RRF 融合与相关度门规则不变。
- **审计器是确定性词面基线**：后缀/标记规则 + 调用方可选词表；不是医学 NER、不是蕴含判断，判定名是**假设**，其质量需由人工标注实验测量。包内不内置词表（内容许可由调用方负责），未提供词表时实体串换检测标"不可检测"。
- **规则强化（v6.6.2）**：数值检查比对**单位族**（"2月龄"→"2-month-old"/"2 months" 视为保留；"38.9℃"→"38.9 mg" 报 `numeric_loss`，单位缺失或未知则不报）；英文否定/时间词改为**词边界匹配**并补齐等价表达（`not documented`/`no evidence`/`absence of`…、`past`/`pre-existing`/`long-term`…），同时与检索式自检 `query.py` 的否定标记保持覆盖对齐（有测试守护）。
- **与检索式自检分工**：第 5.7 节 `query_review` 只报告供人复核；门控是**会改变信号的机制**，两者独立、互不联动。

### 5.9 指南结构化适用性（v6.6.5）

指南文档可在 `metadata.applicability_criteria` 声明逐条条件；`applicability[].criteria` 返回每条条件与**可见患者事实**的核对结果。没有该字段时，若存在旧 `population_criteria` 映射则沿用旧词面语义，否则 `not_assessed`。

```json
{
  "applicability_criteria": [
    {"criterion_id": "preterm", "field": "ga_days", "operator": "lt", "value": 259,
     "unit": "days", "required": true, "review_status": "reviewed",
     "label": "早产儿（胎龄 < 37 周）",
     "source": {"locator": "指南适用人群", "quote": "preterm infants"}},
    {"criterion_id": "dx", "field": "diagnosis", "operator": "contains_any",
     "value": ["呼吸窘迫综合征", "RDS"], "required": true, "review_status": "reviewed",
     "source": {"locator": "指南标题", "quote": "Respiratory Distress Syndrome"}},
    {"criterion_id": "fio2", "field": "respiratory_support.fio2", "operator": "ge",
     "value": 0.30, "unit": "fraction", "required": false, "review_status": "reviewed",
     "source": {"locator": "推荐 3.1", "quote": "FiO2"}}
  ]
}
```

也接受带逻辑的包装形式：`{"logic": "any_of", "criteria": [...]}`；`logic` 仅支持 `all_of`（默认）与 `any_of`，不支持嵌套分组。

字段与语义：

| 项 | 说明 |
|---|---|
| `field` | 患者事实字段。`collection.field` 限定集合（如 `respiratory_support.fio2`、`maternal.gbs_status`）；`diagnosis` 是 `diagnoses.name` 的别名。 |
| `operator` | `exists` / `eq` / `ne` / `in` / `not_in` / `gt` / `ge` / `lt` / `le` / `between` / `contains` / `contains_any` / `contains_all`。未知操作符返回 `unknown`，不猜。 |
| `value` | 期望值；`between` 需两个数。字符串比较大小写不敏感；数值比较做数值转换。 |
| `unit` | 可选；与事实的 `<field>_unit` 或 `unit` 比较。缺失单位不视为矛盾；单位不一致返回 `unknown`，不做换算。 |
| `subject_role` | 默认 `patient`；母体条件显式写 `mother`。患者条件不会读取母体事实。 |
| `required` | 默认 `true`；非必需条件只记录逐条结果，不决定总体状态。 |
| `review_status` | `reviewed` / `synthetic_fixture` 才参与核对；`draft` / `scope_only` / `not_structurable` 只展示并返回 `unknown`。默认 `draft`。 |
| `source` | 必需来源定位（`locator`/`quote`）；参与核对的条件缺少定位时返回 `unknown`。 |

总体状态：`possible_match`（必需条件均匹配；`any_of` 时任一匹配）、`mismatch`（`all_of` 至少一项必需条件不符；`any_of` 全部不符）、`unknown`（部分必需条件缺少可用记录、条件未审核或缺定位）、`not_assessed`（无快照或无结构化条件）。硬边界：

- `not_recorded` / `pending` / `unavailable` / `hypothesis` **不会**变成 `mismatch` 或阴性；字段为空同样按 `unknown` 处理。
- 只评估 `review_status=reviewed/synthetic_fixture` 且带原文定位的结构化条件；**不解析指南正文自由文本**，不生成治疗阈值。
- `mismatch` 只表示字段核对不一致，不是“临床不适用”；`possible_match` 不是“指南符合”。
- **试算预览（`applicability_preview`，默认关）**：未参与判定的条件可附 `preview` 字段——用同一比对规则试算“若经人工审核会判什么”（`preview.status` + `observed_value`/`patient_record_paths`/`unit`），供界面展示机制；官方 `status` 保持 `unknown`，预览明确标注“不构成判定”。开启后工作台适用性面板并排显示“条件 / 病历实际 / 试算预览”。

### 5.10 规则路由 v0（`routing_mode`）

`EvidenceRetrievalPipeline(..., routing_mode=...)` 在查询计划之后、选连接器之前决定外部来源组合；三种模式：

| 模式 | 行为 |
|---|---|
| `off`（默认） | legacy：所有选中来源照旧执行；trace 不出现 `route_decision`。 |
| `shadow` | 计算决策并写入 `trace.route_decision`，**不改变**来源顺序、排序和返回；用于实验性运行与决策分布统计。 |
| `active` | 按决策收窄/重排 `request.source_kinds`，重建查询计划后再检索；trace 另记 `source_order_after`。 |

规则 v0（`rag/routing.py`，纯函数 `decide_route()`，按顺序先命中先返回）：

1. `no_english_drop_pubmed`：查询计划没有任何英文覆盖时去掉 PubMed 分支；若 PubMed 是唯一来源，回退全来源，避免空检索。
2. `guideline_recommendation_first`：指南推荐意图（问题含指南/推荐/建议/治疗/剂量等）指南优先。
3. `evidence_update_pubmed_first`：证据更新意图（最新/更新/recent 等）PubMed 优先。
4. `drug_safety_pubmed_first`：药物安全意图（安全/不良反应/禁忌 等）PubMed 优先。
5. `default_all_sources`：无特定线索，全部可用来源按 legacy 顺序。

边界：只决定**外部来源**；患者事实始终本地检索，不参与路由；请求显式给了 `source_kinds` 时只在其内选择；不决定“要不要检索”（Agent 职责）。实验读数：39 例演示病例 × 3 模式 × 2 种映射配置共 234 次离线检索，shadow top-k 39/39 与 off 一致，active 在现有合成语料上 top-k 也 39/39 不变；无英文探针 active 连接器 3→2、候选 22→13、警告 4→0。**top-k 不变不代表路由有效或无害**：当前语料尚未覆盖证据更新/药物安全/无英文等真实分支，质量结论需冻结查询集 + 人工标注。`trace.route_decision` 逐条留痕，便于回溯误判。合法值之外显式报错。

### 5.11 逐轮检索控制器（`controller-v1-trial`，研究层）

`RetrievalController` 把“这次选哪些来源”升级为“逐轮决定查不查、查哪个逻辑来源、查完停不停”。默认 `/api/retrieve` **不启用**；调用方显式构造：

```python
from rag.controller import ControllerPolicy, RetrievalController, replay_controller_result
controller = RetrievalController(pipeline, policy=ControllerPolicy(min_coverage=0.5))
result = controller.run(request)          # dict：rounds / stop / final / replay
view = replay_controller_result(result)   # 只读回放表
```

逻辑来源由 `role_for_source()` 映射：`guideline_registry`、`europe_pmc`、`authorized_pdf`、`pubmed`、`other`；`patient_facts` 固定为 round 0，本地检索、不参与外部路由。每轮选择使用 `score(role) = intent_prior - cost_weight*cost`；无英文覆盖时跳过 PubMed/Europe PMC；已查、预算不足、不可用都写入 `considered[]` 的 `skip` 与原因。

停止原因：`sufficient_evidence`（证据数 + 词面覆盖 + 必需角色）、`budget_exhausted`、`no_new_evidence`（连续零增量）、`max_rounds`、`sources_exhausted`、`no_eligible_source`、`no_positive_expected_gain`。`sufficiency/coverage` 是检索层启发式，不是临床充分性；`sources_exhausted` 不是“没有证据”。

`pipeline.retrieve(request, only_source_ids=(...))` 是控制器专用白名单：只限制实际运行的连接器，**不会**写入 `request.source_kinds` 的 connector-level kind 过滤；默认 `None` 保持 legacy。逐轮证据摘要各自绑定本轮 `retrieval_id`，跨轮 citation 合并尚未实现。设计说明与 R1→R4 路线见研发侧报告（不随本分支发布）。

## 6. 打开引用：`EvidenceRAG.open_citation`

调用必须使用刚才检索得到的两个 ID：

```python
opened = rag.open_citation({
    "retrieval_id": result["retrieval_id"],
    "citation_id": result["citations"][0]["citation_id"],
})

citation = opened["citation"]
print(citation["title"], citation["version_id"], citation["evidence_status"])
```

返回结构包含 `citation_id`、`title`、`quote`、`key_evidence_sentences`、`locator`、`url`、`pmid`、`doi`、`version_id`、`recommendation_id`、`population`、`evidence_status`、`actions`。

错误：

```text
ValueError: unknown retrieval_id; retrieve evidence before opening a citation
ValueError: citation_id does not belong to this retrieval
```

`EvidenceSessionStore` 当前是进程内字典，服务重启或进程切换后 ID 不再可用。多进程部署时应实现持久化会话存储，并通过 `sessions=` 注入。

## 7. 回答审计：`EvidenceRAG.audit_grounded_answer`

调用：

```python
audit = rag.audit_grounded_answer({
    "retrieval_id": result["retrieval_id"],
    "answer": f"该研究报告了相关结果[{result['citations'][0]['citation_id']}]。",
})
```

输入字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `retrieval_id` | `str` | 是 | 要使用的单轮检索 ID。 |
| `answer` | `str` | 是 | 待审计回答，不能为空。建议每条可验证主张后写 `[citation_id]`。 |

`citation_id` 的获取：`ranked_documents[].citation_id`（或 `evidence_groups[*][].citation_id`）与 `citations[].citation_id` 是**同一稳定外键**，用外键关联；不要按下标在 `ranked_documents` 与 `citations` 之间配对。审计只接受本次 `retrieval_id` 作用域内的 ID，跨检索引用会被判 `invalid_citation`。

返回字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `retrieval_id` | `str` | 被审计的检索 ID。 |
| `verifier` | `dict` | `name`、`available`、`mode`。默认 `name="none"`、`available=false`、`mode="structural_only"`，主张标 `not_checked`。 |
| `claims` | `list[dict]` | 按句切分的主张结果。 |
| `alce_like` | `dict` | 引用完整性、精度和召回风格指标（空分母为 `None`）。 |
| `warnings` | `list[str]` | 缺引用、跨轮引用、校验器不可用等提示。 |
| `limitations` | `list[str]` | 审计限制。 |

`claims[]` 字段：`claim`、`verifier_claim`、`citation_ids`、`valid_citation_ids`、`invalid_citation_ids`、`citation_checks`、`ragtruth_label`（`supported`/`evident_baseless_info`/`citation_missing`/`invalid_citation`/`not_checked`）。

默认审计不注入校验器，因此 `citation_precision`/`citation_recall` 为 `None`、主张标签为 `not_checked`；`citation_completeness` 与 `invalid_citation` 仍可用于发现漏引和跨检索引用。需要语义支持判断时，向 `GroundedAnswerAuditor(verifier=...)` 注入实现 `ClaimVerifier` 协议的对象；此时 `verifier.mode="claim_verification"`。这些指标不等于临床正确率、指南符合率或医生审核结果。

## 8. 联合检索：HIS 快照与患者事实

位置：`rag/his.py`、`rag/patient_retrieval.py`

RAG 不自行读取真实 HIS。调用链是：Agent 通过只读 HIS 工具获取 `PatientSnapshot`，再将快照显式放入 `patient_snapshot` 字段。

```python
from rag.his import InMemoryHISAdapter, snapshot_from_dict

snapshot = snapshot_from_dict({
    "patient_ref": "syn-neo-001",
    "as_of": "2026-04-15",
    "facts": [...],
    "reviewed_external_terms": ["respiratory distress syndrome"],
})

result = rag.retrieve_evidence({
    "question": "该患儿新生儿呼吸窘迫综合征的指南推荐和最新证据是什么？",
    "as_of": "2026-04-15",
    "patient_terms": ["新生儿呼吸窘迫综合征"],
    "english_terms": ["respiratory distress syndrome"],
    "patient_snapshot": snapshot,
})
```

约束与语义：

- `PatientSnapshot` 按 `as_of` 过滤（`visible_facts()` / `filter_snapshot()`）；未来事实计入 `suppressed_future`；
- 患者事实命中保留 `record_path`/`field_path`，供人工回溯（`patient_hits`）；
- **同一问题下患者事实与外部证据是不同来源**：回答可由已有患者记录支持时，外部证据缺失不应单独导致拒答；缺少病历记录也不等于是否定事实；
- 外部连接器只接收完成检索所需的最小信息（`reviewed_external_terms`、审核英文词、MeSH、问题与 `as_of`）；**绝不发送** `patient_ref`、自由文本、病历或凭据；
- `applicability` 只表示结构化字段匹配提示（`possible_match`/`mismatch`/`unknown`/`not_assessed`），不是临床决策；无结构化条件时返回 `not_assessed`，不会根据自由文本相似度声称患者适用。

### 8.1 结构化快照的传入（Agent 读取 HIS，RAG 不连 HIS）

链路是 **HIS → Agent → RAG**：**Agent** 通过只读接口读取 HIS，把结构化数据整理成 `patient_snapshot` 载荷传入 RAG；**RAG 自身不连接、不请求 HIS**（包内没有任何 HIS 客户端，也没有 HIS 地址/端点常量）。

```python
snapshot = {                       # Agent 侧构造（结构见 contracts/README.md §2；HIS→Agent→RAG 分工见 Medical-Assistant/contracts/his_rag.md）
    "patient_ref": "syn-neo-001",
    "as_of": "2026-04-15",
    "source": "caller_his",
    "facts": [...],                # 带时间边界的扁平事实
    "reviewed_external_terms": ["respiratory distress syndrome"],   # 人工审核后才放入
}

result = rag.retrieve_evidence({
    "question": "该患儿新生儿呼吸窘迫综合征的呼吸支持建议和依据是什么？",
    "as_of": "2026-04-15",
    "patient_terms": ["新生儿呼吸窘迫综合征"],
    "english_terms": ["respiratory distress syndrome"],
    "patient_snapshot": snapshot,   # dict 或 PatientSnapshot 对象都可以
})
```

RAG 侧的职责（也是它与 HIS 的唯一关系）：

- **接收与校验**：`snapshot_from_dict()` 校验 `patient_ref`/`as_of`/事实结构，缺字段直接报错，不猜测；
- **时间边界**：`as_of` 可见性只由 `filter_snapshot()` 实现（未来事实计入 `suppressed_future`）；
- **不回连**：RAG 不持有 HIS 地址，不发 HIS 请求；HIS 的连接、错误与字段映射属于 Agent 侧（参考实现在发布线仓库 `Medical-Assistant/contracts/agent_his_adapter.py`，映射规则见 `Medical-Assistant/contracts/his_rag.md`；本包不含该文件）；
- **最小信息**：`reviewed_external_terms` 由调用方人工审核后提供，RAG 不从原始字段推断。

Agent 侧参考实现（发布线仓库 `Medical-Assistant/contracts/agent_his_adapter.py`，**不属于本 rag 包**）演示了只读读取 HIS 三个端点、剔除患者标识、把时间线映射为上面的载荷；跨组件测试位于发布线仓库的 `tests/test_his_rag_snapshot.py`。本包的结构性边界由 `tests/test_joint_retrieval.py` 守护（患者事实只进 `patient_hits`，不进入 `trace.external_terms`）。

## 9. 实时 PubMed 的直接调用

实时 PubMed 连接器位置：`rag/pubmed.py`。它只接收 `QueryPlan`，不会接收原始患者病历。

**查询构造**：实时查询优先使用疾病主题锚点 + 新生儿人群，而不是把全部术语用 `AND` 串起来。锚点取审核英文/MeSH 中首个多词疾病术语；单位（`mmol/L`、`μmol/L` 等）和纯数值不进入 PubMed 查询；锚点返回 0 条时才回退到同义词 `OR` 组。问题里的 ASCII 单位不会压过审核术语；没有任何可用英文术语时不发送 PubMed 请求，并在 warnings 中说明。实际查询见 `trace.connector_trace[].query`。

```python
from rag.agent import EvidenceRAG
from rag.connectors import ConnectorRegistry
from rag.pipeline import EvidenceRetrievalPipeline
from rag.pubmed import PubMedConnector

registry = ConnectorRegistry()
registry.register(PubMedConnector())
rag = EvidenceRAG(EvidenceRetrievalPipeline(registry))

result = rag.retrieve_evidence({
    "request_id": "pubmed-demo-001",
    "question": "新生儿呼吸窘迫综合征的肺表面活性物质治疗有哪些近期研究证据？",
    "as_of": "2025-01-01",
    "patient_terms": ["respiratory distress syndrome"],
    "english_terms": ["respiratory distress syndrome"],
    "source_kinds": ["pubmed"],
    "top_k": 5,
})
```

`PubMedConnector` 构造参数：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `source_id` | `str` | `pubmed-live` | 连接器 ID。 |
| `kinds` | `tuple[str, ...]` | `("pubmed",)` | 支持的来源类型。 |
| `transport` | `Callable \| None` | `None` | 可注入传输层，用于离线测试（生产路径直连 NCBI）。 |
| `timeout_seconds` | `float \| None` | 从配置读取 | 单次网络请求超时（必须为正）。 |
| `max_results` | `int` | `20` | 单次检索最大返回数。 |
| `tool_name` | `str` | `chinallm_rag` | NCBI 请求的 `tool` 参数和 User-Agent 前缀。 |

行为说明：

- 先调用 ESearch 取 PMID，再调用 EFetch 解析 XML，转换为包含 PMID、标题、摘要、DOI、期刊、日期和 URL 的 `SourceDocument`；
- 查询术语优先级：审核 `english_terms` → 问题内 `question_english_terms` → MeSH；完全没有英文词时退回概念的 ASCII 词（优先）或中文片段；绝不序列化患者快照；
- 网络/HTTP/解析失败以 `PubMedError(stage, status_code, retryable)` 形式返回，由管线转为 warning 和 trace；显式失败、不静默回退到合成数据；
- 运行环境可能导出失效的 HTTP(S)_PROXY；连接器显式直连公开 HTTPS，并在未配置 `ncbi_email` 时给出中文告警。

配置 `ncbi_email` 和 `ncbi_api_key` 的方式见第 13 节。实时结果受 NCBI 网络、服务状态、摘要完整性和排序变化影响，不是冻结实验数据集。

## 10. 指南连接器

位置：`rag/guidelines.py`、`rag/guideline_pdf.py`

- `GuidelineMetadataConnector`：只提供官方元数据和 canonical URL（标记 `metadata_only`），不索引全文；
- `LiveGuidelineFullTextConnector`：检索时实时查询 Europe PMC；仅对 `isOpenAccess=Y` 的记录抓取 JATS 全文并按章节分块，非 OA 记录只返回书目/摘要与链接（`full_text_allowed=false`）；全文不落盘、不缓存、不再分发；
- `LocalGuidelinePdfConnector`（`guideline-pdf-local`）：调用方自备 PDF（`config.guideline_pdf_dir`，仓库外）；检索时实时解析并按章节分块，manifest 或 PDF 不可用时显式警告；全文不落盘、不缓存、不再分发；
- `VersionedGuidelineConnector`：离线/已授权只读指南源，校验非指南文档、警告缺失 `version_id`/`locator` 条目，支持 `filters["guideline_version"]` 过滤；
- `LicensedGuidelineSnapshotConnector`：只有在 `full_text_allowed=true` 且 `rights_basis` 已记录时才索引全文；
- 新生儿指南注册表 `neonatal_guideline_metadata_registry()` 当前含三条经官方页面核实的记录：新生儿感染性肺炎（2025，中文）、欧洲 RDS 2025 更新版（Neonatology 2026-03-09，PMID 41802129）、RDS 中文解读（二级来源，`official=false`）；**成人指南不再注册**；
- 版本标签与发表时间分开保存（RDS `version_id="2025"`、`published_at="2026-03-09"`）；回顾性 `as_of` 早于发表日时按未来文献排除；
- 版本、更新时间和替代关系只有来源明确提供或人工审核时才填写，不根据年份猜测 `current`。

## 11. 自定义连接器

### 11.1 连接器协议

连接器需要以下属性和方法：

```python
class KnowledgeConnector:
    source_id: str
    kinds: tuple[str, ...]

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        ...
```

约束：`source_id` 非空且唯一；`search()` 只能返回该连接器拥有的文档；返回值必须是 `SearchResult`；不得把原始病历或患者标识发送到外部知识源。

### 11.2 `CallableConnector`

```python
from rag.connectors import CallableConnector, ConnectorRegistry

def fetch(plan, limit):
    return [{"document_id": "local-1", "kind": "guideline", "title": "本地只读指南", "text": "指南正文。", "locator": "第 2 节"}]

registry = ConnectorRegistry()
registry.register(CallableConnector(source_id="local-guideline", kinds=("guideline",), fetch=fetch))
```

回调可返回 `SourceDocument` 或字典；如果返回文档的 `source_id` 不属于当前连接器，会抛出 `ValueError`。

### 11.3 `InMemoryConnector`

只用于离线演示和测试，只返回预先提供的文档，并根据 `plan.filters["source_kinds"]` 和文档 `kind` 做简单过滤。

## 12. 检索方法（可切换）

`retrieval_method` 选择底层检索方法；多选（如 `"bm25,dense"` 或 `["bm25","rm3","dense"]`）对多路召回排名做 RRF 融合（k=60）。

| 方法 | 类型 | 依赖 | 模型 | 说明 |
|---|---|---|---|---|
| `bm25` | 词面 | 标准库 | — | 字段化 BM25（title 2.5 / mesh 2.0 / keywords 1.5 / text 1.0 / population 1.0）。 |
| `ql` | 词面 | 标准库 | — | Dirichlet 平滑查询似然（mu=2000）。 |
| `rm3` | 词面 | 标准库 | — | 伪相关反馈（top-5 反馈文档 / top-10 扩展词）。 |
| `dense` | 神经 | torch + transformers | MedCPT 双塔 | 英文域编码器；中文问题需经审核英文术语。 |
| `xlingual` | 神经 | torch + transformers | `intfloat/multilingual-e5-base` | 中文原问题 + 审核英文术语直接编码，跨语言检索英文语料。 |
| `m3` | 神经 | torch + transformers | `BAAI/bge-m3` | BGE-M3 多语言双塔（官方 **CLS 池化**，`last_hidden_state[:,0]` + L2 归一化）；中文问题直接编码、跨语言检索英文语料；本包只使用其 dense 功能，不用 sparse/multi-vector 头。依据 MMed-Bench-IR（多语言稠密检索器优于英文域生物医学编码器与词面方法）。 |
| `translate` | 神经 | torch + transformers | `Helsinki-NLP/opus-mt-zh-en` | 中文问题机器译为英文，**只作为额外查询变体**；原问题保留；数字不一致写入警告与 `method_trace.numeric_mismatches`。 |
| `splade` | 神经 | torch + transformers | `naver/splade-cocondenser-ensembledistil` | 稀疏学习权重。 |
| `colbert` | 神经 | torch + transformers | `colbert-ir/colbertv2.0` | MaxSim 后期交互。 |
| `query2doc` | 神经 | torch + transformers | `flan-t5-small`（可注入） | 生成伪文档作为额外查询变体参与 BM25。 |
| `rerank`（开关） | 重排 | torch + transformers | MedCPT cross-encoder（默认）/ Qwen3-Reranker-0.6B | `rerank=true` 时在排序后重排；重排器由 `config.reranker_model` 选择，见下方「重排器与重排时机」。 |

关键语义：

- **重排器与重排时机（v6.7.0）**：`config.reranker_model` 选 `medcpt`（默认，MedCPT cross-encoder）或 `qwen3`（`Qwen/Qwen3-Reranker-0.6B`，本地缓存权重、多语言，**中文问题可见**；官方 chat template 的 `system`/`query`/`document` 三角色 + yes/no 词元概率打分，指令文本 `QWEN3_RERANKER_INSTRUCTION` 冻结为常量；需 `transformers>=4.51`）。`config.rerank_pool` 控制重排时机：`0`（默认）= 标准模式——重排只作用于联合选择后的 `top_k`；`>0` 且请求 `rerank=true` 时，分源排序与联合选择按 `max(top_k, rerank_pool)` 收集候选，重排后再截断到 `top_k`，`trace.rerank_trace` 记录 `pool_enabled`/`pool_size`/`reranked_count`。请求 `rerank=false` 时池不生效。重排分数只表示检索相关性，不是证据质量。**工作台步骤 05 可直接选择重排器与候选池**：`POST /api/settings/reranker`（`{"model":"medcpt|qwen3","rerank_pool":N}`）在进程内即时生效，下一次检索按新配置重建；已打开的旧 `retrieval_id` 仍绑定原检索工具。重启服务后回到 env/config。

- **零信号路线不参与 RRF**（2026-10-09 修复）：某一路对全部候选给零分时（如中文问题对英文语料 BM25），该路排名只是任意顺序，计入 RRF 等于注入噪声；只有正分候选参与该路排名。
- 神经方法不可用（缺依赖/模型/加载失败）时在 `trace.method_trace` 标 `not_configured`（附原因）；全部所选方法都不可用且无神经信号时回退 `bm25` 并记 `fallback`；**绝不拿词面分数冒充 dense/SPLADE/ColBERT**。
- 查询相关度门：词面与语义分数都为零的候选在打分前剔除，权威性/新鲜度/RRF 不会为“仅存在于来源中”的文档回填分数。
- 分数只表示检索相关性，不表示证据质量、推荐强度或患者适用性。
- **语义召回的实体风险显式化**：稠密方法对全部候选给正分，可能带回"语义相近但病种不同"的条目（实测：新生儿肺炎问题带上不相关的中文指南）。系统不做静默过滤，而是把"入选候选中仅由语义方法召回（词面零分）"的条目逐条写入 `warnings`，要求核对实体一致性。
- 神经依赖（torch/transformers）需单独安装；模型经 `HF_ENDPOINT` 镜像下载到 HF 缓存，离线用 `HF_HUB_OFFLINE=1`（适配器导入时自动开启离线模式，并通过 `RAG_ALLOW_MODEL_DOWNLOAD=1` 显式放开下载）。
- **查询保真门控（可选，见第 5.8 节）**：`downweight`/`reject` 按判定级别对 `translate`（译文变体）与 `multilingual_encoding`（`xlingual`/`m3` 语义分数）路线降权或退出融合，`reviewed_mapping` 路线只警示；融合规则与相关度门不变。

## 13. 配置、服务与浏览器工作台

### 13.1 配置文件

配置文件：[`config.py`](config.py)。核心检索不依赖任何外部服务；`RAGConfig` 字段：

| 字段 | 类型 | 默认值 | 作用 |
|---|---|---:|---|
| `openai_api_key` / `openai_model` / `openai_base_url` / `openai_api_mode` | `str` | `""` / `"auto"` | 预留的 OpenAI-compatible 配置位（当前包内不发起 LLM 调用）。 |
| `ncbi_email` | `str` | `""` | PubMed 联系邮箱（NCBI 礼貌访问）。 |
| `ncbi_api_key` | `str` | `""` | 可选 NCBI key。 |
| `request_timeout_seconds` | `float` | `60.0` | PubMed 请求超时。 |
| `server_host` / `server_port` | `str` / `int` | `127.0.0.1` / `8798` | 本地 HTTP 服务地址。 |
| `source_mode` | `str` | `"live"` | 默认来源模式（仅 `live`/`custom`）。 |
| `allow_live_pubmed` / `allow_live_guidelines` | `bool` | `True` / `True` | 实时来源开关；关闭后 `live` 模式不可用。 |
| `custom_sources_path` | `str` | `""` | 可选：启动时预载的自定义来源库文件（`{"sources":[...]}`）。 |
| `fidelity_lexicon_path` | `str` | `""` | 可选：`FidelityLexicon` JSON 路径（结构见 `contracts/examples/fidelity_lexicon_example.json`）；环境变量 `RAG_FIDELITY_LEXICON` 可覆盖；留空时实体串换检测标"不可检测"。 |
| `fidelity_default_policy` | `str` | `"off"` | 默认查询保真策略（第 5.8 节）；单次请求可用 `fidelity_gate` 覆盖，环境变量 `RAG_FIDELITY_POLICY` 可覆盖。 |
| `fidelity_penalty` | `float` | `0.5` | `downweight` 的乘性系数，取值须在 `[0, 1]`。 |
| `term_mapping_library_path` | `str` | `""` | 可选通用术语映射库（`{"library": name, "rows": [...]}` 或行数组；环境变量 `RAG_TERM_MAPPING_LIBRARY` 可覆盖）。配置一次全站生效，概念/别名命中才展开，请求行优先；留空=不启用（legacy）。文件缺失/损坏显式报错。构建器支持 `--mesh-enrich`：经 NCBI E-utilities 从 **NLM MeSH 实取**官方主题词/Descriptor UI/入口词（`mesh_grounded`/`mesh_top_match`/`not_found` 逐行留痕），中文别名仍来自数据/词表、待人工核验。 |
| `reranker_model` | `str` | `"medcpt"` | 重排器选择：`medcpt`（MedCPT cross-encoder，默认）/ `qwen3`（Qwen3-Reranker-0.6B，本地权重）。环境变量 `RAG_RERANKER_MODEL` 可覆盖；未知值显式报错。工作台可用 `POST /api/settings/reranker` 在进程内即时覆盖。 |
| `applicability_preview` | `bool` | `False` | 适用性**试算预览**：`true` 时给未参与判定的条件（未审核/缺出处）附加 `preview`（同一比对规则的试算结果 + 观测值/病历路径）。`status` 不变，预览不构成判定。环境变量 `RAG_APPLICABILITY_PREVIEW` 可覆盖。 |
| `routing_mode` | `str` | `"off"` | 规则路由模式：`off`（关闭，默认）/ `shadow`（只记录决策）/ `active`（收窄/重排外部来源）。环境变量 `RAG_ROUTING_MODE` 可覆盖；其它值显式报错。见第 5.10 节。 |

密钥只保存在本地配置，不要写入脚本、日志、README 或提交记录。修改 `config.py` 后需重启服务。

### 13.2 来源模式（仅两种）

服务**不内置任何本地语料**；`source_mode` 只有两个合法值：

- `live`（默认）：实时 PubMed（E-utilities）+ 新生儿指南元数据/链接记录 + 开放获取指南全文（`allow_live_guideline_fulltext`，默认 `True`）；前两者需要 `allow_live_pubmed` 与 `allow_live_guidelines` 同时为 `True`；
- `custom`：调用方注册的自有知识库（如医院指南库、自备文献快照）。库为空时检索**显式报错**，不返回空结果冒充"没有证据"。

指南全文只对 Europe PMC 标记为 `isOpenAccess=Y` 的记录实时抓取；非 OA 记录只返回书目/摘要与链接，`full_text_allowed=false`，并在 warnings 中说明。调用方自备的已授权 PDF 通过 `LocalGuidelinePdfConnector`（`guideline-pdf-local`）参与同一检索，目录在仓库外，检索时实时解析。全文不落盘、不缓存、不再分发。

**主题过滤与抓取上限**：指南连接器用审核术语中的疾病短语做主题匹配，先过滤与主题不符的记录，再抓取全文；每次最多处理 `max_articles=5` 篇文章，每篇最多保留 `max_chunks_per_article=3` 个章节块，避免单篇文章的多个 chunk 占满 `top_k`。新生儿指南元数据注册表同样按疾病 token 过滤，只有查询主题相关的元数据记录会进入候选。两个上限均为连接器构造参数，可按调用方预算调整。

实时失败显式报错，**不静默回退**到任何其它数据。

**custom 注册接口**（三种方式，同一 payload schema）：

```text
HTTP   POST /api/sources/custom        注册/覆盖一个来源（同 source_id 覆盖，事件可审计）
HTTP   POST /api/sources/custom/clear  清空已注册来源
HTTP   GET  /api/sources               查看可用模式、已注册来源与最近事件
Python CustomSourceLibrary.register_payload() -> build_registry()
配置   config.custom_sources_path      启动预载库文件
```

payload 最小形态（完整 schema 见 `contracts/agent_contract.json` 的 `CustomSourcePayload`；示例见 `contracts/examples/agent_custom_source_register.json`）：

```json
{
  "source_id": "hospital-guideline-library",
  "kinds": ["guideline", "pubmed"],
  "version": "2025-12",
  "documents": [
    {"document_id": "g-1", "kind": "guideline", "title": "…", "text": "…",
     "version_id": "2025", "recommendation_id": "2.1", "locator": "第2章·推荐2.1"}
  ]
}
```

规则：`source_id`/`documents` 必填且 `documents` 非空；每篇必填 `document_id`/`kind`/`title`/`text`；文档归属固定为来源 `source_id`（显式写不同值报错）；`kinds` 省略按文档推断且必须覆盖全部 kind；未知字段进 `metadata`；`version` 缺省会产生审计警告。**库只在内存**，重启清空；调用方负责内容许可与合规。

启动后通过 `GET /api/health` 检查 `active_mode`、`available_modes` 和 connector 状态。

### 13.3 HTTP 服务

```bash
cd rag
python3 server.py
```

默认地址 <http://127.0.0.1:8798/>（可用 `RAG_SERVER_PORT` 覆盖）。启动时若目标端口被旧的 RAG 服务实例占用，会在严格校验进程身份（`/proc/<pid>/cmdline` 匹配 `-m rag.server` 或 `.../rag/server.py`）后自动清退；**无关进程绝不触碰**，被其他程序占用时明确报错并给出持有者 PID。

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/`、`/index.html` | 浏览器工作台（`evidence_retrieval_workbench.html`）。 |
| `GET` | `/api/health` | 进程与配置摘要（含 `rag_version`、来源模式、检索方法就绪状态），不触发检索、不验证网络，脱敏。 |
| `GET` | `/api/tools` | 返回 `EvidenceRAG.tool_specifications()`。 |
| `POST` | `/api/his/snapshot` | 读取 synthetic HIS 快照（演示用）。 |
| `GET` | `/api/demo/cases` | 默认合并精选 9 例 + `his_generated_30/` 的 30 例（共 39，`case_sets` 区分）；11 例场景档案在 `rag/demo/his_generated/`，显式 `load_demo_cases(<目录>)` 只读该目录。 |
| `GET` | `/api/demo/guidelines` | 演示指南库清单与是否已注册（不隐式注册）。 |
| `POST` | `/api/demo/guidelines/load` | 显式把演示指南库注册进 custom 库（同 source_id 覆盖，事件可审计）。 |
| `POST` | `/api/retrieve` | 接收检索字段（与 `RetrievalRequest.from_dict()` 相同）。 |
| `POST` | `/api/controller/retrieve` | 逐轮控制器试运行：同一请求字段 + 可选 `controller_policy`（`max_rounds`/`max_cost`/`min_evidence`/`min_coverage`/…）；返回 `rounds`/`stop`/`final`/`replay`，不合并跨轮 citation。 |
| `POST` | `/api/settings/reranker` | 进程内切换重排器/候选池：`{"model":"medcpt|qwen3","rerank_pool":N}`；下一次检索重建工具，已打开的旧 `retrieval_id` 不受影响。 |
| `POST` | `/api/citation` | 接收 `retrieval_id`、`citation_id`。 |
| `POST` | `/api/audit` | 接收 `retrieval_id`、`answer`。 |

POST 请求体必须是 JSON 对象，最大 1,000,000 字节。

HTTP 状态码：

| 状态 | 说明 |
|---:|---|
| `200` | 请求成功；连接器失败也可能以 warning 形式返回。 |
| `400` | JSON 非对象、请求体过大、字段校验失败、未知 source_mode、引用 ID 无效。响应为 `{"error": "..."}`。 |
| `404` | 未知路径或接口。 |
| `502` | 未捕获服务端异常，响应包含 `error`、`exception_type`、`stage`、`error_id`（已脱敏）。 |

### 13.4 浏览器工作台

工作台 = 单文件 HTML + 标准库 `http.server`，无前端框架、无 CDN、无构建步骤。**v6.7.0 起为流程化选项卡工作台**：顶部固定控制条（主题、后端自检、空白请求、查看结果、执行检索）+ 左侧 01–09 流程导航（每步带状态）+ 中间按真实调用顺序纵向排列的步骤卡 + 右侧状态轨（状态 / 来源 / 日志三个选项卡）。不再提供缩放、平移、拖拽或连线。步骤 08 结果区用选项卡收纳：证据列表（可按来源再分选项卡）、患者事实（本次命中 / 快照事实）、指南适用性、检索诊断（映射来源 / 来源路由 / 检索式自检 / 联合选择 / 查询保真）、原始响应；引文详情与证据列表并排。所有输入都直接发给真实 `/api/his/snapshot`、`/api/retrieve`、`/api/citation`、`/api/audit`；步骤 01 支持直接编辑快照 JSON 后载入，步骤 08 可展开后端原始响应。重构前的旧版页面不随本分支发布。

九个步骤按真实调用顺序固定排列，每个步骤的输入字段与 `RetrievalRequest` 一一对应：

| 步骤 | 输入/动作 | 对应字段或接口 |
|---|---|---|
| 01 病例与快照 | 选择合成病例、调整 as_of、载入快照 | `patient_snapshot`、`as_of`；`POST /api/his/snapshot`、`GET /api/demo/cases` |
| 02 问题与意图 | 中文问题、任务、语言、临床意图 | `question`、`task`、`query_language`、`clinical_intent` |
| 03 术语与映射 | 患者术语、`term_mapping` 映射表（概念→英文/MeSH/别名）、facets | `patient_terms`、`term_mapping`、`facets` |
| 04 范围与过滤 | 来源模式、top_k、来源类型、filters | `source_mode`、`top_k`、`source_kinds`、`filters` |
| 05 检索方法与门控 | 方法、重排开关、重排器/候选池、保真门控 | `retrieval_method`、`rerank`、`fidelity_gate`；`POST /api/settings/reranker` 即时切换 `reranker_model`/`rerank_pool` |
| 06 RetrievalRequest | 只预览不发送，实时 JSON | `request_id` 在执行时生成 |
| 07 执行检索 | 执行按钮；逐轮控制器（研究）按钮 | `POST /api/retrieve`；`POST /api/controller/retrieve`（结果区“检索诊断”显示 rounds/stop/replay） |
| 08 证据结果 | 包摘要、citation_id 输出 | `EvidencePackage` |
| 09 引用与审计 | 打开引文、审计回答 | `POST /api/citation`、`POST /api/audit` |

结果区保留分源结果、联合选择（`trace.joint_selection`）、检索式自检（`trace.query_review`）、查询保真（`trace.query_fidelity`）、患者事实、引文详情与回答审计；每个步骤状态徽章与 API 日志都来自真实响应，不是前端模拟。

**字体与配色**：输入控件统一由 CSS 变量控制（`--fs-body/--fs-label/--fs-input/--fs-code 15px`、`--fs-hint 12.5px`、`--fs-map 12px`、分区标题 15px），固定三列布局在宽屏下保持约 1:1 阅读尺度。工具栏“主题”下拉提供 **浅色 / 深色 / 暖色 / 靛蓝 / 跟随系统** 五档；主题由 `html[data-theme]` 的 CSS 变量组切换，选择写入 localStorage，也可用 `?theme=dark` 直接指定。

**结果呈现**：执行成功后自动滚动到“证据结果”面板（按指南/PubMed 分源卡片、患者事实命中、联合选择、检索式自检、查询保真、引文详情）；步骤 08 同时保留一份紧凑结果列表，便于在当前页面直接点选。工具栏提供“查看结果”跳转按钮。

**失败处理**：`source_mode=custom` 但尚无已注册来源时，执行按钮会先真实调用 `POST /api/demo/guidelines/load` 再检索；若仍失败，结果区显示错误信息与“加载来源库并重试”按钮，并在日志中保留原始状态码。页面启动时会比对 `/api/health` 的 `rag_version`，若后端不是 6.7.0（例如误连到 8797/8798 的旧实例）会在工具栏顶部显示版本不匹配警告。病例 9 例全部为合成新生儿数据（RDS/回顾时点/肺炎/TTN/黄疸/PPHN/BPD/低血糖/GBS），下拉框来自 `rag/demo/neonatal_cases.json`；每例默认 `source_mode=live`，直接检索真实指南与开放获取正文，不默认使用合成指南块。来源库需点击“加载来源库”（`POST /api/demo/guidelines/load`）后才会注册进 custom 库；服务不持有患者数据，也不内置语料。步骤 03 的 `term_mapping` JSON 映射表会在输入时渲染概念—英文—MeSH 预览；JSON 无效会阻止执行。

### 13.5 契约文档

- `contracts/agent_contract.json`：机器可读的 Agent/HIS/RAG 契约（`contract_version` 与包 `__version__` 同步，当前 `6.7.0`），含 `language_rules`、`term_mapping`、`applicability_criteria` 与字段语义；
- `contracts/README.md`：调用顺序、接口字段定义、隐私与错误边界（含查询保真门控字段，见其第 9.4 节）；
- `contracts/examples/`：请求/响应/错误示例（含 `agent_fidelity_gate_request.json`、`fidelity_lexicon_example.json`）；
- 运行时的同一份语义可从 `GET /api/tools` 获取（`agent.py` 的 `tool_specifications()`）。

改契约时三处（JSON / README / `tool_specifications`）必须同步；测试会校验契约版本与 `__version__` 一致。

## 14. 异常处理和调用方责任

直接调用时主要异常如下：

| 异常 | 触发条件 |
|---|---|
| `ValueError("question must be a non-empty string")` | 问题为空。 |
| `ValueError("top_k must be between 1 and 50")` | `top_k` 越界。 |
| `ValueError("unknown retrieval_method: ...")` | 未知检索方法名。 |
| `ValueError("duplicate connector: ...")` | 注册重复连接器。 |
| `ValueError("source document missing: ...")` | 文档缺少必填字段。 |
| `ValueError("connector ... returned document owned by ...")` | 连接器返回不属于自己的文档。 |
| `ValueError("unknown retrieval_id; ...")` | 打开引用或审计时会话不存在。 |
| `ValueError("citation_id does not belong to this retrieval")` | 引用属于另一轮检索。 |
| `ValueError("answer must be a non-empty string")` | 审计答案为空。 |
| `ValueError("unknown source_mode: ...")` | 未知来源模式。 |
| `PubMedError`（含 `stage`/`status_code`/`retryable`） | PubMed 网络、HTTP 或响应解析失败；通常由检索管线转为 warning。 |

调用方应：

1. 对 `question`、`top_k`、日期和术语做业务侧校验。
2. 在 `warnings` 和 `trace.connector_trace`/`method_trace` 中检查连接器与方法是否失败或未配置。
3. 不把空结果解释为“没有相关医学证据”。
4. 通过 `retrieval_id` 绑定引用和审计，禁止跨检索复用 `citation_id`。
5. 根据 `url`、`locator` 和完整原文核对摘录。
6. 将 `not_checked`、`not_configured` 与“已校验为不支持”明确区分。

## 15. 测试

在仓库根目录运行：

```bash
python3 -m unittest discover -s rag/tests -p 'test_*.py'
python3 -m compileall -q rag
```

测试使用合成或录制数据，不请求实时 PubMed、不请求 LLM、不下载模型、不加载神经模型。

测试覆盖含查询保真审计与门控（`tests/test_fidelity.py`、`tests/test_gate.py`）、离线实验骨架的指标层（`tests/test_fidelity_metrics.py`），v6.6.5 的映射表与结构化适用性（`tests/test_query_mapping_applicability.py`：映射解析/展开/别名边界、概念分组变体、PubMed 字段标签、逐条条件核对、data_status 与 subject_role 语义、legacy 分支），v6.7.0 的重排池与 Qwen3 重排器（`tests/test_rerank_upgrade_670.py`：注入打分离线、池行为、legacy 路径不变）、通用术语映射库（`tests/test_term_mapping_library.py`：加载校验、库行展开与溯源、请求优先、构建器抽取）、规则路由（`tests/test_routing.py`：五条决策、无英文回退、请求来源过滤、off/shadow/active 模式、非法值），以及逐轮控制器（`tests/test_controller.py`：患者事实首轮、来源限制、无英文跳过、预算/轮数停止、回放与确定性）。当前全量 **352 tests：343 通过 + 9 跳过**（当次实跑；跳过项为不随发布分支分发的研发侧构建器测试）。

**Agent 集成兼容面**：`main` 分支的 `agent/` 直接导入 `rag.audit.MiniCheckVerifier`、`rag.pubmed.PUBMED_SEARCH_CACHE` 与 `rag.guidelines.who_guideline_metadata_registry`，并读取 `trace.connector_trace[].date_filter` / `cache_hit`。这些接口作为兼容面保留：MiniCheck 默认未配置模型、主张标签保持 `not_checked`；PubMed 缓存由调用方按轮次通过 `ContextVar` 设置，未设置时不缓存，时间窗参数按 `publication_from`/`publication_to` 校验；WHO 成人元数据只作旧注册表助手，**不进入 live 新生儿注册表**。回归测试见 `tests/test_agent_compat.py`。跨组件契约测试在仓库根 `tests/`（HIS→Agent→RAG 链路，用 `contracts/examples/` 的合成样例，离线运行）：`python3 -m unittest discover -s tests -t . -p 'test_*.py'`。`rag/eval/run_fidelity_experiments.py --plan` 只打印实验臂矩阵与数据需求、不执行检索（实验前模式）。

## 16. 使用边界

- 服务**不内置任何语料**：`live` 模式只接入实时 PubMed 和新生儿指南元数据，不代表已接入正式指南库、药品数据库、临床试验库或真实 HIS/PACS；`custom` 模式的内容由调用方负责许可与合规。测试用合成数据只存在于 `rag/tests/`（`tests/fixtures.py`），演示数据只存在于 `rag/demo/`（全部 `synthetic=true`），产品代码不隐式注册。
- 实时 PubMed 受网络、NCBI 状态、摘要完整性和排序变化影响，不是冻结实验数据集。
- 缺失日期不等于证据无效；缺失病历记录也不等于患者没有某种疾病。
- 排序分数、关键词匹配、关键证据句和引用存在性不等于临床正确性或指南符合性。
- 引用结构审计与 ALCE/RAGTruth 风格指标都不是诊断、处方、治疗建议或临床有效性证明；默认不执行语义主张校验。
- 神经检索/重排不可用时报告 `not_configured`；未测量不写成零错误率。
- HIS 数据由 **Agent 读取后传入**（HIS → Agent → RAG）：RAG 不连接 HIS，也不做字段映射；`as_of` 过滤与拒答语义在 RAG 侧，患者标识剔除是 Agent 侧参考实现的责任（规则见发布线 `Medical-Assistant/contracts/his_rag.md`）。
- 查询保真审计/门控是**研究特性**（默认 `off`）：审计器是确定性词面基线，判定名（`suspected_*`/`unverifiable`）是对人工标注量的假设，不是诊断或证据质量结论；`downweight`/`reject` 只改变包内检索信号；词表不内置，内容许可由调用方负责。
- 指南适用性只核对 `metadata.applicability_criteria` 中**已审核的结构化条件**；不解析指南正文自由文本，不自动生成阈值。`mismatch` 不是临床不适用，`unknown` 不是阴性，`possible_match` 不是指南符合性证明。
- 使用者必须核对完整原文、来源定位、适用人群、时间范围和撤回状态；证据不足时保留不确定性或拒答。
