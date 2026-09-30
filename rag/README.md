# RAG 直接调用使用指南

本目录的核心是一个可被 Python Agent 直接调用的只读 RAG 组件。推荐的主调用路径是：

```text
构造连接器
  -> 注册到 ConnectorRegistry
  -> 创建 EvidenceRetrievalPipeline
  -> 创建 EvidenceRAG
  -> retrieve_evidence
  -> open_citation / audit_grounded_answer
```

HTTP 服务和浏览器工作台只是这个核心调用层之上的附加封装，见本文最后的“HTTP 与浏览器附录”。不需要启动 `server.py` 就可以直接使用 RAG。

模块不会读取原始病历、写回 HIS/PACS、生成诊断或治疗决定。`patient_terms` 只能传入人工审核的最小化术语，不得传入患者姓名、住院号、身份证号、自由文本病史或原始病历。

当前服务提供实时 PubMed 连接器；核心模块也提供内存连接器和可调用函数连接器，便于离线测试或接入其他只读知识源。

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

如果只使用 `InMemoryConnector`、`CallableConnector`、本地排序和审计，不需要安装额外依赖。MiniCheck 是可选依赖，只有需要真实句子级主张校验时才安装：

```bash
cd code/src/Medical-Assistant/rag
python3 -m pip install -r requirements.txt
```

没有安装或配置 MiniCheck 时，审计仍可运行，但主张标签会明确标记为 `not_checked`，不会用词面规则冒充模型校验。

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
EvidenceRetrievalPipeline(registry: ConnectorRegistry)
```

`retrieve()` 接受一个 `RetrievalRequest`，执行以下步骤：

1. 创建本轮唯一 `retrieval_id`。
2. 根据问题、患者最小术语和人工术语映射生成 `QueryPlan`。
3. 按来源顺序调用连接器。
4. 收集连接器返回的 `SourceDocument`。
5. 按 DOI、PMID 或标题去重。
6. 按 `as_of` 排除未来文献和撤回文献。
7. 计算词面相关度、来源内 RRF、权威性和时效性。
8. 使用多样化选择得到不超过 `top_k` 条文档。
9. 为每条文档生成 `Citation` 和关键证据句定位。
10. 返回 `EvidencePackage`。

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

- `retrieve_evidence(arguments)`：检索并保存证据包。
- `open_citation(arguments)`：打开本次检索中的一条引用。
- `audit_grounded_answer(arguments)`：审计回答中的主张与本次检索引用。

## 4. 检索输入：`RetrievalRequest`

位置：`rag/models.py`

可以直接构造数据类：

```python
from rag.models import RetrievalRequest

request = RetrievalRequest(
    request_id="request-001",
    question="高血压治疗的近期研究证据是什么？",
    task="clinical_question",
    as_of="2025-01-01",
    patient_terms=("hypertension",),
    source_kinds=("pubmed",),
    filters={},
    top_k=8,
    user_terms={"高血压": ("hypertension",)},
)
package = pipeline.retrieve(request)
```

更适合 Agent 或 JSON 输入的方式是：

```python
request = RetrievalRequest.from_dict({
    "request_id": "request-001",
    "question": "高血压治疗的近期研究证据是什么？",
    "as_of": "2025-01-01",
    "patient_terms": ["hypertension"],
    "source_kinds": ["pubmed"],
    "top_k": 8,
    "user_terms": {"高血压": ["hypertension"]},
})
```

### 4.1 字段说明

| 字段 | Python 类型 | 默认值 | 是否必填 | 说明 |
|---|---|---:|---:|---|
| `request_id` | `str` | 自动生成 `request-<随机值>` | 否 | 调用方请求标识。建议业务侧自行生成唯一值。 |
| `question` | `str` | 无 | 是 | 查询问题。去除首尾空格后不能为空。 |
| `task` | `str` | `"clinical_question"` | 否 | 任务标签，目前仅记录，不触发额外流程。 |
| `as_of` | `str \| None` | `None` | 否 | 查询截止日期，推荐 `YYYY-MM-DD`。晚于该日期的文献会被排除。 |
| `patient_terms` | `tuple[str, ...]` | `()` | 否 | 人工审核的最小患者相关术语；不允许放原始病历。 |
| `source_kinds` | `tuple[str, ...]` | `()` | 否 | 来源类型，例如 `pubmed`、`guideline`、`drug`、`clinical_trial`、`local_knowledge`。 |
| `filters` | `Mapping[str, Any]` | `{}` | 否 | 传给查询计划和连接器的附加信息。当前管线不会通用执行所有过滤字段。 |
| `top_k` | `int` | `8` | 否 | 最终返回文档数，必须在 1 到 50 之间。 |
| `user_terms` | `Mapping[str, Sequence[str]]` | `{}` | 否 | 人工审核的术语映射，例如 `{"高血压": ["hypertension"]}`。 |

校验和转换规则：

- `question` 为空或只有空白时抛出 `ValueError("question must be a non-empty string")`。
- `top_k` 先执行 `int()` 转换；不能转换或不在 1 到 50 时抛出 `ValueError`。
- 字符串形式的 `patient_terms` 和 `source_kinds` 会被转换为单元素序列。
- 序列中的空字符串会被丢弃，并对其他值执行字符串转换。
- `as_of` 本身不在 `from_dict()` 中强制校验日期；无效日期在排序阶段会按无法解析处理。调用方应在进入 RAG 前自行校验。
- 未提供 `request_id` 时自动生成唯一值。
- `source_kinds` 指向未注册类型时，查询计划可能回退到全部已知连接器，不能将它视作严格的来源白名单。

### 4.2 查询计划 `QueryPlan`

RAG 会从 `question` 和 `patient_terms` 提取中英文概念，并只扩展 `user_terms` 显式提供的术语。`QueryPlan` 字段如下：

| 字段 | 类型 | 说明 |
|---|---|---|
| `original_question` | `str` | 原始问题。 |
| `variants` | `tuple[str, ...]` | 原始问题、概念组合和受控扩展组合。 |
| `concepts` | `tuple[str, ...]` | 提取后的概念。 |
| `source_order` | `tuple[str, ...]` | 实际连接器执行顺序。推荐类问题会优先使用指南类型连接器。 |
| `filters` | `Mapping[str, Any]` | 查询过滤记录，自动包含 `source_kinds`。 |
| `rationale` | `tuple[str, ...]` | 查询计划形成原因。 |

## 5. 直接调用检索：`EvidenceRAG.retrieve_evidence`

### 5.1 调用方式

```python
result = rag.retrieve_evidence({
    "request_id": "request-002",
    "question": "高血压治疗有哪些近期研究证据？",
    "as_of": "2025-01-01",
    "patient_terms": ["hypertension"],
    "source_kinds": ["pubmed"],
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
| `retrieval_id` | `str` | 本轮检索唯一 ID。引用和审计必须使用同一个值。 |
| `request_id` | `str` | 输入请求 ID 或自动生成值。 |
| `query_plan` | `dict` | 实际查询计划。 |
| `ranked_documents` | `list[dict]` | 排序后的文档和分数。 |
| `citations` | `list[dict]` | 可追溯引用。 |
| `warnings` | `list[str]` | 连接器、过滤、去重或无结果警告。 |
| `trace` | `dict` | 检索过程信息。 |

`EvidencePackage.to_dict()` 会递归把 dataclass、Enum、tuple 转成 JSON 兼容的字典、字符串和数组。

### 5.3 `ranked_documents` 字段

每个排序文档包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `document` | `dict` | 原始 `SourceDocument`。 |
| `score` | `float` | 综合排序分数，不是证据质量或推荐等级。 |
| `lexical_score` | `float` | 词面相关度。 |
| `fusion_score` | `float` | 来源内倒数排名融合分数。 |
| `authority_score` | `float` | 来源权威性先验。 |
| `freshness_score` | `float` | 相对 `as_of` 的时效性分数。 |
| `evidence_status` | `str` | `current`、`unknown_date`、`future_at_query_time` 或 `withdrawn`。 |
| `matched_queries` | `list[str]` | 命中的查询变体。 |
| `rank_explanation` | `list[str]` | 排序解释。 |

### 5.4 `SourceDocument` 字段

连接器返回 `SourceDocument` 对象或字典。使用字典时，以下四个字段必填且不能为空：

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
| `pmid` | `str \| None` | 否 | PubMed PMID。 |
| `doi` | `str \| None` | 否 | DOI，用于去重。 |
| `authority` | `str \| None` | 否 | 权威性描述。 |
| `metadata` | `Mapping[str, Any]` | 否 | 来源附加信息。未知输入字段也会放入这里。 |

示例：

```python
document = SourceDocument.from_dict({
    "document_id": "pmid-123",
    "kind": "pubmed",
    "title": "Recorded title",
    "text": "Recorded abstract.",
    "published_at": "2024-01-01",
    "pmid": "123",
    "doi": "10.1000/example",
    "locator": "Abstract",
    "journal": "Example Journal",  # 未知字段会进入 metadata
}, source_id="pubmed-live")
```

### 5.5 `citations` 字段

每条引用包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `citation_id` | `str` | 本轮唯一引用 ID，格式类似 `retrieval-...:cit-1`。 |
| `source_id` | `str` | 连接器 ID。 |
| `document_id` | `str` | 被引用文档 ID。 |
| `kind` | `str` | 文档类型。 |
| `title` | `str` | 标题。 |
| `quote` | `str` | 规范化摘录，默认最多约 360 字符。 |
| `locator` | `str \| None` | 原文定位。 |
| `url` | `str \| None` | 原文 URL。 |
| `pmid` | `str \| None` | PMID。 |
| `doi` | `str \| None` | DOI。 |
| `evidence_status` | `str` | 证据状态。 |
| `actions` | `list[dict]` | 当前包含 `open_evidence` 动作。 |
| `key_evidence_sentences` | `list[dict]` | 关键证据句定位。 |

`key_evidence_sentences` 是定位辅助，不是临床正确性判断。每项会记录句子文本、句序、字符起止偏移、匹配查询和词面分数，调用方应使用偏移回到原文核对。

### 5.6 `trace` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `connector_trace` | `list[dict]` | 每个连接器的执行状态。成功含 `source_id`、`status`、`returned`；失败含错误类型和阶段。 |
| `key_sentence_budget` | `int` | 每条引用最多提取的关键句数，当前为 3。 |
| `candidate_count` | `int` | 收集到的候选数量。 |
| `selected_count` | `int` | 最终选中数量。 |
| `generation_performed` | `bool` | 核心检索管线不生成答案，固定为 `false`。 |
| `clinical_decision_performed` | `bool` | 固定为 `false`。 |

## 6. 打开引用：`EvidenceRAG.open_citation`

调用必须使用刚才检索得到的两个 ID：

```python
opened = rag.open_citation({
    "retrieval_id": result["retrieval_id"],
    "citation_id": result["citations"][0]["citation_id"],
})

citation = opened["citation"]
print(citation["title"])
print(citation["quote"])
print(citation["url"])
```

输入字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `retrieval_id` | `str` | 是 | `retrieve_evidence` 返回的检索 ID。 |
| `citation_id` | `str` | 是 | 同一轮 `citations[]` 中的 ID。 |

返回结构：

```python
{
    "retrieval_id": "...",
    "citation": {
        "citation_id": "...",
        "title": "...",
        "quote": "...",
        "key_evidence_sentences": [...],
        "locator": "...",
        "url": "...",
        "pmid": "...",
        "doi": "...",
        "actions": [...],
    },
}
```

如果检索 ID 不存在，抛出：

```text
ValueError: unknown retrieval_id; retrieve evidence before opening a citation
```

如果引用不属于该轮检索，抛出：

```text
ValueError: citation_id does not belong to this retrieval
```

`EvidenceSessionStore` 当前是进程内字典，服务重启或进程切换后 ID 不再可用。多进程部署时应实现持久化会话存储，并通过 `sessions=` 注入。

## 7. 回答审计：`EvidenceRAG.audit_grounded_answer`

调用：

```python
audit = rag.audit_grounded_answer({
    "retrieval_id": result["retrieval_id"],
    "answer": (
        f"该研究报告了相关结果"
        f"[{result['citations'][0]['citation_id']}]。"
    ),
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
| `verifier` | `dict` | `name` 和 `available`。默认名称为 `minicheck`。 |
| `claims` | `list[dict]` | 按句切分的主张结果。 |
| `alce_like` | `dict` | 引用完整性、精度和召回风格指标。 |
| `warnings` | `list[str]` | 缺引用、跨轮引用、校验器不可用等提示。 |
| `limitations` | `list[str]` | 审计限制。 |

`claims[]` 字段：

- `claim`：原始句子。
- `verifier_claim`：去除引用标记后的句子。
- `citation_ids`：句中所有引用 ID。
- `valid_citation_ids`：属于本轮检索的引用 ID。
- `invalid_citation_ids`：无效或跨轮引用 ID。
- `citation_checks`：每个有效引用的 `citation_id`、`supported`、`score`。
- `ragtruth_label`：`supported`、`evident_baseless_info`、`citation_missing`、`invalid_citation` 或 `not_checked`。

`alce_like` 字段：

- `claim_count`
- `citation_completeness`
- `citation_precision`
- `citation_recall`
- `valid_citation_links`

没有主张、没有有效链接或没有完成 MiniCheck 时，相应比率可能为 `None`/`null`。这些指标不等于临床正确率、指南符合率或医生审核结果。

## 8. 实时 PubMed 的直接调用

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
    "source_kinds": ["pubmed"],
    "top_k": 5,
})

for citation in result["citations"]:
    print(citation["citation_id"], citation["title"], citation["url"])
```

`PubMedConnector` 构造参数：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `source_id` | `str` | `pubmed-live` | 连接器 ID。 |
| `kinds` | `tuple[str, ...]` | `("pubmed",)` | 支持的来源类型。 |
| `email` | `str \| None` | 从 `config.py` 读取 | NCBI 联系邮箱。 |
| `tool_name` | `str` | `chinallm_rag` | NCBI 请求的工具名和 User-Agent 前缀。 |
| `timeout_seconds` | `float` | 从配置读取 | 单次网络请求超时。 |

PubMed 会先调用 E-utilities 搜索 PMID，再抓取 XML 文献，转换为包含 PMID、标题、摘要、DOI、期刊、日期和 URL 的 `SourceDocument`。网络、HTTP 或 XML 解析失败会以连接器警告和 trace 形式返回。

配置 `ncbi_email` 和 `ncbi_api_key` 的方式见第 11 节。实时结果受 NCBI 网络、服务状态、摘要完整性和排序变化影响，不是冻结实验数据集。

## 9. 自定义连接器

### 9.1 连接器协议

连接器需要以下属性和方法：

```python
class KnowledgeConnector:
    source_id: str
    kinds: tuple[str, ...]

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        ...
```

约束：

- `source_id` 必须非空且在注册表中唯一。
- `kinds` 表示该连接器支持的来源类型。
- `search()` 只能返回该连接器拥有的文档。
- 返回值必须是 `SearchResult`，包含 `source_id`、`documents`、`warnings`。
- 不得把原始病历或患者标识发送到外部知识源。

### 9.2 `CallableConnector`

已有客户端可以通过回调接入：

```python
from rag.connectors import CallableConnector, ConnectorRegistry
from rag.models import SourceDocument


def fetch(plan, limit):
    return [{
        "document_id": "local-1",
        "kind": "guideline",
        "title": "本地只读指南",
        "text": "指南正文。",
        "locator": "第 2 节",
    }]

registry = ConnectorRegistry()
registry.register(CallableConnector(
    source_id="local-guideline",
    kinds=("guideline",),
    fetch=fetch,
))
```

回调可以返回 `SourceDocument` 或字典。字典会通过 `SourceDocument.from_dict()` 解析；如果返回文档的 `source_id` 不属于当前连接器，会抛出 `ValueError`。

### 9.3 `InMemoryConnector`

只用于离线演示和测试：

```python
from rag.connectors import InMemoryConnector

registry.register(InMemoryConnector(
    source_id="fixture",
    kinds=("guideline",),
    documents=(document,),
))
```

它只返回预先提供的文档，并根据 `plan.filters["source_kinds"]` 和文档 `kind` 做简单过滤。

## 10. 查询、排序和证据状态

检索流程会进行 DOI/PMID/标题去重、时间状态处理、词面相关度、来源内 RRF、权威性、时效性和多样化选择。

证据状态：

- `current`：日期有效且不晚于 `as_of`。
- `unknown_date`：没有可解析日期，保留但需谨慎解释。
- `future_at_query_time`：晚于 `as_of`，排除。
- `withdrawn`：`metadata.status` 为 `withdrawn` 或 `retracted`，排除。

排序分数、命中词和关键句只用于检索解释与定位，不是临床证据质量、指南推荐等级或医学事实正确性。

## 11. 配置和可选 LLM 摘要

配置文件：[`config.py`](config.py)。核心检索不依赖 LLM。需要摘要时，可以单独直接调用 `OpenAIResponsesLLM`：

```python
from rag.llm import OpenAIResponsesLLM

llm = OpenAIResponsesLLM()
summary = llm.summarize(
    question="高血压治疗有哪些近期研究证据？",
    evidence_package=result,
)

# summary: {"provider": ..., "model": ..., "text": ...}
audit = rag.audit_grounded_answer({
    "retrieval_id": result["retrieval_id"],
    "answer": summary["text"],
})
summary["audit"] = audit
```

配置字段：

| 字段 | 类型 | 默认值 | 作用 |
|---|---|---:|---|
| `openai_api_key` | `str` | `""` | OpenAI-compatible HTTP API key。 |
| `openai_model` | `str` | `""` | 模型 ID。 |
| `openai_base_url` | `str` | `""` | 服务根地址；没有路径时补 `/v1`。 |
| `openai_api_mode` | `str` | `"auto"` | `auto`、`responses` 或 `chat_completions`。 |
| `ncbi_email` | `str` | `""` | PubMed 联系邮箱。 |
| `ncbi_api_key` | `str` | `""` | 可选 NCBI key。 |
| `minicheck_model` | `str` | `""` | MiniCheck 模型名称。 |
| `minicheck_cache_dir` | `str` | `""` | MiniCheck 缓存目录。 |
| `request_timeout_seconds` | `float` | `60.0` | PubMed 请求超时；直接用默认 `OpenAIResponsesLLM()` 时其默认超时为 180 秒。 |

密钥只保存在本地配置，不要写入脚本、日志、README 或提交记录。LLM 只接收问题和当前证据包中的引用摘录；未配置或请求失败时不会生成替代答案。

## 12. 异常处理和调用方责任

直接调用时主要异常如下：

| 异常 | 触发条件 |
|---|---|
| `ValueError("question must be a non-empty string")` | 问题为空。 |
| `ValueError("top_k must be between 1 and 50")` | `top_k` 越界。 |
| `ValueError("duplicate connector: ...")` | 注册重复连接器。 |
| `ValueError("source document missing: ...")` | 文档缺少必填字段。 |
| `ValueError("unknown retrieval_id; ...")` | 打开引用或审计时会话不存在。 |
| `ValueError("citation_id does not belong to this retrieval")` | 引用属于另一轮检索。 |
| `ValueError("answer must be a non-empty string")` | 审计答案为空。 |
| `PubMedError` | PubMed 网络、HTTP 或响应解析失败；通常由检索管线转为 warning。 |
| `LLMConfigurationError` | LLM key 或模型未配置。 |
| `LLMRequestError` | LLM 请求失败、协议不兼容或返回格式无文本。 |

调用方应：

1. 对 `question`、`top_k`、日期和术语做业务侧校验。
2. 在 `warnings` 和 `trace.connector_trace` 中检查连接器是否失败。
3. 不把空结果解释为“没有相关医学证据”。
4. 通过 `retrieval_id` 绑定引用和审计，禁止跨检索复用 `citation_id`。
5. 根据 `url`、`locator` 和完整原文核对摘录。
6. 将 `not_checked` 与模型不可用明确区分，不把它当作支持或不支持结论。

## 13. HTTP 与浏览器附录

HTTP 和浏览器是可选封装。启动方式：

```bash
cd code/src/Medical-Assistant/rag
python3 server.py
```

默认页面：[http://127.0.0.1:8788/](http://127.0.0.1:8788/)。服务入口为 `rag/server.py`，其内部仍然通过 `EvidenceRAG`、`EvidenceRetrievalPipeline` 和 `PubMedConnector` 完成检索。

### 13.1 HTTP 路由

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/`、`/index.html` | 浏览器工作台。 |
| `GET` | `/api/health` | 返回 PubMed、LLM、MiniCheck 的脱敏状态。 |
| `GET` | `/api/tools` | 返回 `EvidenceRAG.tool_specifications()`。 |
| `POST` | `/api/retrieve` | 接收检索字段，并额外支持 `generate_summary`。 |
| `POST` | `/api/citation` | 接收 `retrieval_id`、`citation_id`。 |
| `POST` | `/api/audit` | 接收 `retrieval_id`、`answer`。 |

HTTP 的 `/api/retrieve` 请求字段与 `RetrievalRequest.from_dict()` 相同，只有 `generate_summary` 是 HTTP 层附加字段，默认值为 `true`。POST 请求体必须是 JSON 对象，最大 1,000,000 字节。

### 13.2 HTTP 状态码

| 状态 | 说明 |
|---:|---|
| `200` | 请求成功；连接器失败也可能以 warning 形式返回。 |
| `400` | JSON 非对象、请求体过大、字段校验失败、引用 ID 无效。响应为 `{"error": "..."}`。 |
| `404` | 未知路径或接口。 |
| `502` | 未捕获服务端异常，响应包含 `error`、`exception_type`、`stage`、`error_id`、`http_status`。 |

HTTP 健康检查只检查服务进程和配置摘要，不会验证 PubMed 网络可达性，也不会触发检索。

### 13.3 浏览器工作台

工作台支持输入中文问题、截止日期、`top_k`、最小化术语、来源范围和摘要开关。当前只启用实时 PubMed，其他来源显示为未连接。GUI 默认显示 6 条结果，而核心 API 默认 `top_k=8`；这不影响直接调用的默认值。

## 14. 测试

从 `code/src/Medical-Assistant` 目录运行：

```bash
cd code/src/Medical-Assistant
python3 -m unittest discover -s rag/tests -p 'test_*.py'
python3 -m compileall -q rag
```

测试使用合成或录制数据，不请求实时 PubMed、不请求 LLM，也不下载 MiniCheck 模型。

## 15. 使用边界

- 当前 HTTP 服务只注册实时 PubMed，不代表已接入正式指南库、药品数据库、临床试验库或真实 HIS/PACS。
- 实时 PubMed 受网络、NCBI 状态、摘要完整性和排序变化影响，不是冻结实验数据集。
- 缺失日期不等于证据无效；缺失病历记录也不等于患者没有某种疾病。
- 排序分数、关键词匹配、关键证据句和引用存在性不等于临床正确性或指南符合性。
- LLM 摘要、MiniCheck 和 ALCE/RAGTruth 风格指标都不是诊断、处方、治疗建议或临床有效性证明。
- 使用者必须核对完整原文、来源定位、适用人群、时间范围和撤回状态；证据不足时保留不确定性或拒答。
