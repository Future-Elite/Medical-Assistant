# RAG 直接调用使用指南

本目录是可被 Python Agent 直接调用的只读 RAG 组件：在通用证据检索之上提供受控中英文/MeSH 查询规划、字段化 BM25 与可选语义/RRF 融合、患者事实 + PubMed + 版本化指南的分源联合检索、引用打开和回答审计。

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

当前版本在包 `__init__.py` 中以 `__version__` 声明（当前 `6.4.0`）；运行时 trace 的 `rag_version` 与 `/api/health` 均读取该值，代码中不硬编码版本字符串。

模块不会读取原始病历、写回 HIS/PACS、生成诊断或治疗决定。`patient_terms` 只能传入人工审核的最小化术语，不得传入患者姓名、住院号、身份证号、自由文本病史或原始病历。RAG 不自行读取真实 HIS：`patient_snapshot` 由 Agent 读取并授权传入。

## 1. 直接调用的最短示例

从 `code/src/Medical-Assistant` 目录运行下面的代码。这个示例使用内存中的合成文档，不联网，适合先验证调用流程：

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
cd code/src/Medical-Assistant
python3 path/to/your_script.py
```

也可以只设置导入路径：

```bash
PYTHONPATH=code/src/Medical-Assistant python3 path/to/your_script.py
```

## 2. 环境和依赖

核心 RAG 代码只使用 Python 标准库，需要 Python 3.10 或更高版本：

```bash
python3 --version
```

如果只使用 `InMemoryConnector`、`CallableConnector`、`VersionedGuidelineConnector`、词面检索方法（bm25/ql/rm3）、本地排序和审计，不需要安装额外依赖。以下能力是可选依赖：

- MiniCheck 真实主张校验（`requirements.txt`）；
- 神经检索方法 `dense`/`xlingual`/`translate`/`splade`/`colbert`/`query2doc` 与 cross-encoder 重排（`torch`/`transformers` 与本地模型缓存；本工作区经 `HF_ENDPOINT=https://hf-mirror.com` 预下载）。

```bash
cd code/src/Medical-Assistant/rag
python3 -m pip install -r requirements.txt
```

没有安装或配置 MiniCheck 时，审计仍可运行，但主张标签会明确标记为 `not_checked`，不会用词面规则冒充模型校验。神经方法不可用时在 `trace.method_trace` 中标记 `not_configured`（附原因），绝不静默降级。

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
| `auditor` | `GroundedAnswerAuditor \| None` | 自动创建 | 默认使用 `MiniCheckVerifier`。 |

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
    "question": "该患者高血压的指南推荐和最新证据是什么？",
    "as_of": "2025-12-31",
    "patient_terms": ["高血压"],
    "source_kinds": ["pubmed", "guideline"],
    "top_k": 8,
    "user_terms": {"高血压": ["hypertension"]},
    "english_terms": ["hypertension"],
    "mesh_terms": ["Hypertension"],
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
| `user_terms` | `Mapping[str, Sequence[str]]` | `{}` | 否 | 人工审核的中文概念→英文映射，如 `{"高血压": ["hypertension"]}`；仅当键出现在问题/患者术语文本中才展开，展开记录在 `query_plan.expansion_trace`，不自动翻译。 |
| `query_language` | `str` | `"zh"` | 否 | 问题语言标记（字段别名 `language` 亦可）。 |
| `clinical_intent` | `str \| None` | `None` | 否 | `guideline_recommendation` / `evidence_update` / `drug_safety` / `clinical_question`；缺省时由问题文本推断。 |
| `facets` | `Mapping[str, Sequence[str]]` | `{}` | 否 | 结构化临床问题槽位（population/outcome 等），取值作为检索概念参与匹配。 |
| `english_terms` | `tuple[str, ...]` | `()` | 否 | 人工审核的英文医学术语/缩写，直接参与英文检索与实时 PubMed 查询。 |
| `mesh_terms` | `tuple[str, ...]` | `()` | 否 | 人工审核的 MeSH 主题词，参与英文检索与实时 PubMed 查询。 |
| `patient_snapshot` | `Any \| None` | `None` | 否 | Agent 从只读 HIS 获取的结构化快照对象（解码在管线内完成）。 |
| `source_mode` | `str` | `"simulated"` | 否 | `simulated` / `live_pubmed` / `live`；实时失败不自动回退。 |
| `retrieval_method` | `str \| tuple[str, ...] \| None` | `None`（等价 bm25） | 否 | 检索方法，见第 12 节。 |
| `rerank` | `bool` | `False` | 否 | `true` 时在排序后追加 cross-encoder 重排。 |

校验和转换规则：

- `question` 为空或只有空白时抛出 `ValueError("question must be a non-empty string")`。
- `top_k` 先执行 `int()` 转换；不能转换或不在 1 到 50 时抛出 `ValueError("top_k must be between 1 and 50")`。
- 未知检索方法名抛出 `ValueError("unknown retrieval_method: ...")`。
- 字符串形式的 `patient_terms`、`source_kinds` 会被转换为单元素序列；空字符串会被丢弃。
- `patient_snapshot` 可以是 `PatientSnapshot` 对象或字典（经 `snapshot_from_dict()` 解析）。
- `as_of` 本身不在 `from_dict()` 中强制校验日期；无效日期在排序阶段会按无法解析处理。调用方应在进入 RAG 前自行校验。
- 未提供 `request_id` 时自动生成唯一值。
- `source_kinds` 指向未注册类型时，查询计划可能回退到全部已知连接器，不能将它视作严格的来源白名单。

### 4.2 查询计划 `QueryPlan`

RAG 从 `question`、`patient_terms` 和 `facets` 提取中英文概念，只扩展 `user_terms`/`english_terms`/`mesh_terms` 显式提供的术语。`QueryPlan` 字段如下：

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
| `english_terms` / `mesh_terms` | `tuple[str, ...]` | 人工审核的英文/MeSH 术语。 |
| `expansion_trace` | `tuple[Mapping[str, Any], ...]` | 术语扩展轨迹（含审核映射与机器翻译来源）。 |
| `question_english_terms` | `tuple[str, ...]` | 问题中自动提取的英文词（系统生成，调用方不要手工填写）。 |

## 5. 直接调用检索：`EvidenceRAG.retrieve_evidence`

### 5.1 调用方式

```python
result = rag.retrieve_evidence({
    "request_id": "request-002",
    "question": "该患者高血压的指南推荐和最新证据是什么？",
    "as_of": "2025-12-31",
    "patient_terms": ["高血压"],
    "english_terms": ["hypertension"],
    "source_kinds": ["pubmed", "guideline"],
    "top_k": 5,
    "user_terms": {"高血压": ["hypertension"]},
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
| `ranked_documents` | `list[dict]` | 排序后的文档和分数。 |
| `citations` | `list[dict]` | 可追溯引用。 |
| `warnings` | `list[str]` | 连接器、方法、过滤、去重或无结果警告。 |
| `trace` | `dict` | 检索过程信息。 |
| `patient_hits` | `list[dict]` | 本地患者事实命中（无快照时为空）。 |
| `evidence_groups` | `dict[str, list[dict]]` | 按来源类型分组的排序结果（保持来源分离）。 |
| `applicability` | `list[dict]` | 指南条目与患者事实的结构化适用性提示（`possible_match`/`mismatch`/`unknown`/`not_assessed`，不是临床决策）。 |
| `source_separation` | `dict` | 分组状态摘要。 |
| `patient_context` | `dict` | 患者上下文摘要（可见事实数、未来抑制数）。 |

`EvidencePackage.to_dict()` 会递归把 dataclass、Enum、tuple 转成 JSON 兼容的字典、字符串和数组。

### 5.3 `ranked_documents` 字段

每个排序文档包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `document` | `dict` | 原始 `SourceDocument`。 |
| `score` | `float` | 综合排序分数，不是证据质量或推荐等级。 |
| `lexical_score` | `float` | 词面相关度。 |
| `fusion_score` | `float` | RRF 融合分数。 |
| `authority_score` | `float` | 来源权威性先验。 |
| `freshness_score` | `float` | 相对 `as_of` 的时效性分数。 |
| `evidence_status` | `str` | `current`、`unknown_date`、`future_at_query_time`、`withdrawn` 或 `superseded`。 |
| `matched_queries` | `list[str]` | 命中的查询变体。 |
| `rank_explanation` | `list[str]` | 排序解释。 |
| `retriever_id` / `semantic_score` / `fusion_method` / `reranker_id` 等 | 可选 | 多方法检索与重排的逐路记录。 |

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
| `rag_version` | `str` | 包 `__version__`（当前 `6.4.0`）。 |
| `source_mode` | `str` | 本轮来源模式。 |
| `retrieved_at` | `str` | UTC 时间戳。 |
| `connector_trace` | `list[dict]` | 每个连接器的执行状态。成功含 `source_id`、`status`、`returned`、`kind`；失败含错误类型和阶段。 |
| `candidate_count` / `selected_count` | `int` | 候选与最终选中数量。 |
| `retrieval_method` | `str \| None` | 最终生效方法串（如 `"bm25+dense"`）。 |
| `method_trace` | `list[dict]` | 逐方法状态（`ok`/`not_configured`/`error`/`fallback`/`unknown`），含原因与返回数。 |
| `semantic_trace` | `dict` | 语义检索状态汇总。 |
| `rerank_trace` | `dict` | 重排请求状态（`not_requested`/`ok`/`not_configured`/`error`）。 |
| `query_plan` | `dict` | 语言、意图、英文/问题英文/MeSH 术语。 |
| `external_terms` | `list[str]` | 实际发往外部的术语（审核英文词 + 问题内英文词）。 |
| `patient_context` | `dict` | 快照接收状态、可见事实数、命中数、未来抑制数。 |
| `source_separation` | `dict` | 分源分组状态。 |
| `generation_performed` / `clinical_decision_performed` | `bool` | 固定为 `false`。 |

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

返回字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `retrieval_id` | `str` | 被审计的检索 ID。 |
| `verifier` | `dict` | `name` 和 `available`。MiniCheck 未配置时 `available=false`，主张标 `not_checked`。 |
| `claims` | `list[dict]` | 按句切分的主张结果。 |
| `alce_like` | `dict` | 引用完整性、精度和召回风格指标（空分母为 `None`）。 |
| `warnings` | `list[str]` | 缺引用、跨轮引用、校验器不可用等提示。 |
| `limitations` | `list[str]` | 审计限制。 |

`claims[]` 字段：`claim`、`verifier_claim`、`citation_ids`、`valid_citation_ids`、`invalid_citation_ids`、`citation_checks`、`ragtruth_label`（`supported`/`evident_baseless_info`/`citation_missing`/`invalid_citation`/`not_checked`）。

这些指标不等于临床正确率、指南符合率或医生审核结果。

## 8. 联合检索：HIS 快照与患者事实

位置：`rag/his.py`、`rag/patient_retrieval.py`

RAG 不自行读取真实 HIS。调用链是：Agent 通过只读 HIS 工具获取 `PatientSnapshot`，再将快照显式放入 `patient_snapshot` 字段。

```python
from rag.his import InMemoryHISAdapter, snapshot_from_dict

snapshot = snapshot_from_dict({
    "patient_ref": "syn-patient-001",
    "as_of": "2025-12-31",
    "facts": [...],
    "reviewed_external_terms": ["hypertension"],
})

result = rag.retrieve_evidence({
    "question": "该患者高血压的指南推荐和最新证据是什么？",
    "as_of": "2025-12-31",
    "patient_terms": ["高血压"],
    "english_terms": ["hypertension"],
    "patient_snapshot": snapshot,
})
```

约束与语义：

- `PatientSnapshot` 按 `as_of` 过滤（`visible_facts()` / `filter_snapshot()`）；未来事实计入 `suppressed_future`；
- 患者事实命中保留 `record_path`/`field_path`，供人工回溯（`patient_hits`）；
- **同一问题下患者事实与外部证据是不同来源**：回答可由已有患者记录支持时，外部证据缺失不应单独导致拒答；缺少病历记录也不等于是否定事实；
- 外部连接器只接收完成检索所需的最小信息（`reviewed_external_terms`、审核英文词、MeSH、问题与 `as_of`）；**绝不发送** `patient_ref`、自由文本、病历或凭据；
- `applicability` 只表示结构化字段匹配提示（`possible_match`/`mismatch`/`unknown`/`not_assessed`），不是临床决策；无结构化条件时返回 `not_assessed`，不会根据自由文本相似度声称患者适用。

## 9. 实时 PubMed 的直接调用

实时 PubMed 连接器位置：`rag/pubmed.py`。它只接收 `QueryPlan`，不会接收原始患者病历。

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
    "question": "高血压治疗有哪些近期研究证据？",
    "as_of": "2025-01-01",
    "patient_terms": ["hypertension"],
    "english_terms": ["hypertension"],
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

位置：`rag/guidelines.py`

- `GuidelineMetadataConnector`：只提供官方元数据和 canonical URL（标记 `metadata_only`），不索引全文；
- `VersionedGuidelineConnector`：离线/已授权只读指南源，校验非指南文档、警告缺失 `version_id`/`locator` 条目，支持 `filters["guideline_version"]` 过滤；
- `LicensedGuidelineSnapshotConnector`：只有在 `full_text_allowed=true` 且 `rights_basis` 已记录时才索引全文；
- WHO 正式指南注册表 `who_guideline_metadata_registry()` 当前含两条经官方页面核实的记录（2021 高血压药物治疗指南、2018 降糖二/三线药物与胰岛素指南）；标注为 "Technical document" 的条目不作为指南入库；
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
| `translate` | 神经 | torch + transformers | `Helsinki-NLP/opus-mt-zh-en` | 中文问题机器译为英文，**只作为额外查询变体**；原问题保留；数字不一致写入警告与 `method_trace.numeric_mismatches`。 |
| `splade` | 神经 | torch + transformers | `naver/splade-cocondenser-ensembledistil` | 稀疏学习权重。 |
| `colbert` | 神经 | torch + transformers | `colbert-ir/colbertv2.0` | MaxSim 后期交互。 |
| `query2doc` | 神经 | torch + transformers | `flan-t5-small`（可注入） | 生成伪文档作为额外查询变体参与 BM25。 |
| `rerank`（开关） | 重排 | torch + transformers | MedCPT cross-encoder | `rerank=true` 时在排序后重排。 |

关键语义：

- **零信号路线不参与 RRF**（2026-10-09 修复）：某一路对全部候选给零分时（如中文问题对英文语料 BM25），该路排名只是任意顺序，计入 RRF 等于注入噪声；只有正分候选参与该路排名。
- 神经方法不可用（缺依赖/模型/加载失败）时在 `trace.method_trace` 标 `not_configured`（附原因）；全部所选方法都不可用且无神经信号时回退 `bm25` 并记 `fallback`；**绝不拿词面分数冒充 dense/SPLADE/ColBERT**。
- 查询相关度门：词面与语义分数都为零的候选在打分前剔除，权威性/新鲜度/RRF 不会为“仅存在于来源中”的文档回填分数。
- 分数只表示检索相关性，不表示证据质量、推荐强度或患者适用性。
- 神经依赖只装仓库 `.venv`；模型经 `HF_ENDPOINT` 镜像下载到 HF 缓存，离线用 `HF_HUB_OFFLINE=1`（适配器导入时自动开启离线模式并通过 `RAG_ALLOW_MODEL_DOWNLOAD=1` 显式放开下载）。

## 13. 配置、服务与浏览器工作台

### 13.1 配置文件

配置文件：[`config.py`](config.py)。核心检索不依赖任何外部服务；`RAGConfig` 字段：

| 字段 | 类型 | 默认值 | 作用 |
|---|---|---:|---|
| `openai_api_key` / `openai_model` / `openai_base_url` / `openai_api_mode` | `str` | `""` / `"auto"` | 预留的 OpenAI-compatible 配置位（当前包内不发起 LLM 调用）。 |
| `ncbi_email` | `str` | `""` | PubMed 联系邮箱（NCBI 礼貌访问）。 |
| `ncbi_api_key` | `str` | `""` | 可选 NCBI key。 |
| `minicheck_model` / `minicheck_cache_dir` | `str` | `""` | MiniCheck 模型与缓存目录。 |
| `request_timeout_seconds` | `float` | `60.0` | PubMed 请求超时。 |
| `server_host` / `server_port` | `str` / `int` | `127.0.0.1` / `8788` | 本地 HTTP 服务地址。 |
| `source_mode` | `str` | `"simulated"` | 默认来源模式。 |
| `allow_live_pubmed` / `allow_live_guidelines` | `bool` | `True` / `True` | 实时来源开关；关闭后相应模式不可用。 |

密钥只保存在本地配置，不要写入脚本、日志、README 或提交记录。修改 `config.py` 后需重启服务。

### 13.2 来源模式

- `simulated`（默认）：全部使用 synthetic HIS/PubMed/指南 fixture，离线可跑；
- `live_pubmed`：实时 PubMed（E-utilities）+ WHO 官方指南元数据/链接记录；
- `live`：只有实时 PubMed 和实时指南配置都启用时才允许。

实时失败显式报错，**不静默回退** synthetic。启动后通过 `GET /api/health` 检查 `active_mode`、`available_modes` 和 connector 状态。

### 13.3 HTTP 服务

```bash
cd code/src/Medical-Assistant/rag
python3 server.py
```

默认地址 <http://127.0.0.1:8788/>（可用 `RAG_SERVER_PORT` 覆盖）。启动时若目标端口被旧的 RAG 服务实例占用，会在严格校验进程身份（`/proc/<pid>/cmdline` 匹配 `-m rag.server` 或 `.../rag/server.py`）后自动清退；**无关进程绝不触碰**，被其他程序占用时明确报错并给出持有者 PID。

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/`、`/index.html` | 浏览器工作台（`evidence_retrieval_workbench.html`）。 |
| `GET` | `/api/health` | 进程与配置摘要（含 `rag_version`、来源模式、检索方法就绪状态），不触发检索、不验证网络，脱敏。 |
| `GET` | `/api/tools` | 返回 `EvidenceRAG.tool_specifications()`。 |
| `POST` | `/api/his/snapshot` | 读取 synthetic HIS 快照（演示用）。 |
| `POST` | `/api/retrieve` | 接收检索字段（与 `RetrievalRequest.from_dict()` 相同）。 |
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

工作台 = 单文件 HTML + 标准库 `http.server`，无前端框架、无 CDN、无构建步骤。功能：中文问题、证据截止日期、`top_k`、HIS 患者引用读取、最小化患者/受控术语、审核英文术语、MeSH、临床意图、指南版本、来源模式、检索方法与重排开关、问题槽位；结果区分患者事实、PubMed 和指南来源，展示查询计划、警告、引文详情（版本/推荐/适用人群/关键证据句）和回答审计面板。演示预设 8 例（高血压/2型糖尿病/时间过滤/新生儿黄疸/婴儿发热/婴儿疫苗/药品安全/中文直接匹配）使用 synthetic fixtures。界面不含 LLM 摘要面板；核心检索不生成回答。GUI 默认显示 6 条结果，而核心 API 默认 `top_k=8`。

### 13.5 契约文档

- `contracts/agent_contract.json`：机器可读的 Agent/HIS/RAG 契约（`contract_version` 与包 `__version__` 同步，当前 `6.4.0`），含 `language_rules` 与字段语义；
- `contracts/README.md`：调用顺序、接口字段定义、隐私与错误边界；
- `contracts/examples/`：请求/响应/错误示例；
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

从 `code/src/Medical-Assistant` 目录运行：

```bash
cd code/src/Medical-Assistant
python3 -m unittest discover -s rag/tests -p 'test_*.py'
python3 -m compileall -q rag
```

测试使用合成或录制数据，不请求实时 PubMed、不请求 LLM、不下载模型、不加载神经模型。

## 16. 使用边界

- 当前 HTTP 服务默认 `simulated` 模式使用 synthetic fixtures；`live_pubmed`/`live` 只接入实时 PubMed 和 WHO 指南元数据，不代表已接入正式指南库、药品数据库、临床试验库或真实 HIS/PACS。
- 实时 PubMed 受网络、NCBI 状态、摘要完整性和排序变化影响，不是冻结实验数据集。
- 缺失日期不等于证据无效；缺失病历记录也不等于患者没有某种疾病。
- 排序分数、关键词匹配、关键证据句和引用存在性不等于临床正确性或指南符合性。
- MiniCheck 与 ALCE/RAGTruth 风格指标都不是诊断、处方、治疗建议或临床有效性证明。
- 神经检索/重排不可用时报告 `not_configured`；未测量不写成零错误率。
- 使用者必须核对完整原文、来源定位、适用人群、时间范围和撤回状态；证据不足时保留不确定性或拒答。
