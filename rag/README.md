# RAG 检索组件

本包提供可被 Python Agent 直接调用的只读 RAG 能力：受控中英文/MeSH 查询规划、字段化 BM25 与可选语义融合、患者事实与外部证据的分源联合检索、引用打开、指南适用性核对与回答审计。

本包不读取原始病历、不连接 HIS、不写回任何临床系统。患者快照由调用方读取并授权传入。

## 1. 安装

```bash
# 基础能力仅依赖标准库（从仓库根执行）
PYTHONPATH=. python3 -c "import rag; print(rag.__version__)"

# 可选依赖说明见 requirements.txt（MiniCheck 主张校验由调用方注入）
pip install -r requirements.txt
```

## 2. 目录结构

```text
rag/
├── agent.py                 # EvidenceRAG：检索 / 打开引用 / 回答审计
├── pipeline.py              # 分源联合检索与证据包生成
├── models.py                # 请求 / 文档 / 证据包契约
├── config.py                # 运行配置
├── server.py                # 只读 HTTP 服务
├── connectors.py            # 连接器契约与注册表
├── pubmed.py                # PubMed E-utilities 连接器
├── guidelines.py            # 版本化指南元数据连接器
├── custom_sources.py        # 调用方自定义来源库
├── neural_adapters.py       # 可选神经检索/重排适配器
├── audit.py                 # 回答审计与主张校验适配
├── retrieval/               # 查询规划、BM25/QL/RM3、排序、证据句、路由、控制器
├── sources/                 # 指南 PDF / 全文 / 分块 / 解析来源 / 生命周期
├── quality/                 # 适用性、术语映射、MeSH 链接、主张校验、观测
├── eval/                    # 查询保真实验入口
├── web/                     # React 工作台（源码与构建产物）
├── demo/                    # 最小演示数据
├── contracts/               # 机器可读契约与示例
├── validation/              # 验证工件
└── tests/                   # 按 retrieval / sources / quality / core / gui 分组
```

## 3. 快速开始

### 3.1 直接调用

```python
from rag.agent import EvidenceRAG
from rag.connectors import ConnectorRegistry, InMemoryConnector
from rag.models import SourceDocument
from rag.pipeline import EvidenceRetrievalPipeline

registry = ConnectorRegistry()
registry.register(InMemoryConnector(
    source_id="local-knowledge",
    kinds=("local_knowledge",),
    documents=(SourceDocument(
        source_id="local-knowledge",
        document_id="doc-1",
        kind="local_knowledge",
        title="示例文档",
        text="新生儿呼吸窘迫综合征的初始支持包括无创通气与肺表面活性物质。",
    ),),
))

rag = EvidenceRAG(EvidenceRetrievalPipeline(registry=registry))
package = rag.retrieve_evidence({
    "question": "新生儿呼吸窘迫综合征的初始支持是什么？",
    "source_kinds": ["local_knowledge"],
    "source_mode": "custom",
    "top_k": 5,
})
print(package["ranked_documents"][0]["document"]["title"])
print(package["citations"][0]["quote"])
```

### 3.2 HTTP 服务

```bash
PYTHONPATH=. python3 -m rag.server
# http://127.0.0.1:8798/
```

服务默认只读，不生成回答，不执行临床决策。

## 4. 请求与响应

### 4.1 `RetrievalRequest`

| 字段 | 类型 | 说明 |
|---|---|---|
| `question` | `str` | 临床问题 |
| `task` | `str` | `clinical_question` 或 `evidence_update` |
| `query_language` | `str` | `zh` 或 `en` |
| `clinical_intent` | `str \| None` | 意图，影响来源路由 |
| `as_of` | `str \| None` | 证据截止日期（ISO 8601） |
| `patient_terms` | `tuple[str, ...]` | 人工审核的最小化患者术语 |
| `term_mapping` | `tuple[TermMappingEntry, ...]` | 概念绑定的英文 / MeSH / 别名映射 |
| `facets` | `Mapping` | 结构化检索面 |
| `filters` | `Mapping` | 来源与时间过滤 |
| `source_kinds` | `tuple[str, ...]` | `guideline` / `pubmed` 等 |
| `source_mode` | `str` | `live` 或 `custom` |
| `retrieval_method` | `str \| None` | `bm25` / `ql` / `rm3` / `dense` / `m3` / 组合 RRF |
| `rerank` | `bool` | 是否启用重排 |
| `fidelity_gate` | `str` | `off` / `audit` / `warn` / `downweight` / `reject` |
| `top_k` | `int` | 返回条数 |
| `patient_snapshot` | `PatientSnapshot \| None` | 调用方传入的只读快照 |

### 4.2 `EvidencePackage`

| 字段 | 说明 |
|---|---|
| `retrieval_id` | 本次检索标识；引用只能在本轮打开 |
| `request_id` | 调用方请求标识 |
| `ranked_documents` | 排序后的文档与分数构成 |
| `citations` | 引文、定位与关键证据句 |
| `patient_hits` | 患者事实命中 |
| `applicability` | 指南结构化条件核对结果 |
| `evidence_groups` | 按来源分组的证据 |
| `warnings` | 连接器失败、数据缺失、过滤说明 |
| `trace` | 查询计划、来源路由、联合选择、重排、审计与校验轨迹 |

`trace` 中主要字段：`rag_version`、`source_mode`、`retrieval_method`、`candidate_count`、`selected_count`、`connector_trace`、`joint_selection`、`query_review`、`query_fidelity`、`rerank_trace`、`entity_linking`、`guideline_lifecycle`、`route_decision`。

## 5. HTTP API

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/`、`/index.html` | React 工作台 |
| `GET` | `/assets/*` | 前端静态资源 |
| `GET` | `/api/health` | 进程与能力状态 |
| `GET` | `/api/tools` | Agent 工具规格 |
| `POST` | `/api/his/snapshot` | 快照回显与 `as_of` 过滤 |
| `POST` | `/api/retrieve` | 联合检索 |
| `POST` | `/api/controller/retrieve` | 逐轮控制器 |
| `POST` | `/api/citation` | 打开引用 |
| `POST` | `/api/audit` | 回答审计 |
| `POST` | `/api/settings/reranker` | 重排器与候选池 |
| `POST` | `/api/settings/industrial` | 实体链接 / 生命周期 / 主张校验 |
| `GET` | `/api/guidelines` | 指南列表与解析选项 |
| `GET` | `/api/guidelines/chunks` | 分块预览 |
| `POST` | `/api/guidelines/parse` | 解析指南 |
| `GET` | `/api/guidelines/pdf/meta`、`/page` | PDF 信息与页面渲染 |
| `POST` | `/api/guidelines/applicability` | 指南条件核对 |
| `POST` | `/api/guidelines/save-live` | 保存实时指南 |
| `GET` | `/api/metrics` | 进程内观测 |

## 6. 配置

| 变量 | 作用 |
|---|---|
| `RAG_SERVER_HOST`、`RAG_SERVER_PORT` | 监听地址与端口 |
| `RAG_ALLOW_LIVE_PUBMED`、`RAG_ALLOW_LIVE_GUIDELINES` | 启用实时来源 |
| `RAG_GUIDELINE_DATA` | 指南解析数据目录 |
| `RAG_GUIDELINE_CHUNKS` | 预生成分块目录 |
| `RAG_GUIDELINE_PDF_DIR` | 调用方 PDF 目录 |
| `RAG_TERM_MAPPING_LIBRARY` | 通用术语映射库 |
| `RAG_MESH_LEXICON` | MeSH 词表路径 |
| `RAG_MINERU_BIN` | MinerU 可执行文件 |

`config.py` 中的 `openai_api_key`、`ncbi_api_key` 默认为空；需要时通过环境变量注入，不写入代码或日志。

## 7. 数据与指南解析

- 演示数据：`rag/demo/` 提供 50 例合成病例与指南元数据；仓库级 `data/` 不随发布分支分发，运行时优先读取 `data/`，缺失时回退到 `rag/demo/`。
- 指南解析：`guideline_parsing/` 与 `rag/` 并列，`RAG_GUIDELINE_DATA` 指向外部数据目录；支持 PDF、预解析 Markdown、分块与适用性 sidecar。
- 受限指南的全文与解析产物留在仓库外；仅开放获取内容可随仓库分发。

## 8. 测试

```bash
PYTHONPATH=. python3 -m unittest discover -s rag/tests -p "test_*.py"
```

测试按 `retrieval`、`sources`、`quality`、`core`、`gui` 分组；当前 **421 tests**（2026-11-10 实测，0 skipped）。分发包不含仓库级 `data/` 时，`tests/sources/test_ehr_generated_neonatal.py` 的 8 个用例会自动跳过。

## 9. 兼容导入

以下顶层路径保持稳定，供外部 Agent 直接导入：

```python
from rag.agent import EvidenceRAG
from rag.audit import GroundedAnswerAuditor, MiniCheckVerifier
from rag.connectors import ConnectorRegistry, InMemoryConnector
from rag.custom_sources import load_custom_library
from rag.guidelines import GuidelineMetadataConnector, who_guideline_metadata_registry
from rag.his import snapshot_from_dict
from rag.models import QueryPlan, RetrievalRequest
from rag.neural_adapters import LocalZhEnTranslator
from rag.pipeline import EvidenceRetrievalPipeline
from rag.pubmed import PUBMED_SEARCH_CACHE, _safe_term
from rag.server import create_tool
```

内部实现位于 `retrieval/`、`sources/`、`quality/`；调用方无需依赖这些子包路径。

## 10. 边界

- 只读：不写回 HIS/PACS，不生成诊断或治疗建议。
- 证据不足时显示不确定性；不把缺记录解释成否定事实。
- 重排分数、路由决策与控制器停止原因只表示检索层信号，不是临床证据质量或充分性。
- 实体链接、指南生命周期与主张校验均为辅助信号；歧义/冲突不静默选择，不可用时显式标记 `not_checked`。
