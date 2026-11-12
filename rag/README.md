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
├── quality/                 # 适用性、术语映射、MeSH 链接、主张校验、指南图、生命周期索引、观测
├── eval/                    # 查询保真实验入口；eval/qrels/ 规则派生数据集构建与评测；eval/*_fixture/ 评测夹具
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
| `GET` | `/api/demo/cases`、`/api/demo/applicability-cases`、`/api/demo/guidelines` | 演示病例（50 例档案 / 5 例链路演示）与演示指南库 |
| `POST` | `/api/demo/guidelines/load` | 显式注册演示指南库 |
| `GET` | `/api/sources` | 已注册来源清单 |
| `POST` | `/api/sources/custom`、`/api/sources/custom/clear` | 调用方自定义来源登记与清空 |
| `POST` | `/api/retrieve` | 联合检索 |
| `POST` | `/api/applicability` | 指南适用性工作流（含并列检索与工业级图试点） |
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

- 演示数据：`rag/demo/` 提供 50 例合成病例、5 例指南适用性链路演示病例与指南元数据；仓库级 `data/` 不随发布分支分发，运行时优先读取 `data/`，缺失时回退到 `rag/demo/`。
- 指南解析：`guideline_parsing/` 与 `rag/` 并列，`RAG_GUIDELINE_DATA` 指向外部数据目录；支持 PDF、预解析 Markdown、分块与适用性 sidecar。
- 受限指南的全文与解析产物留在仓库外；仅开放获取内容可随仓库分发。

## 8. 测试

```bash
PYTHONPATH=. python3 -m unittest discover -s rag/tests -p "test_*.py"
```

测试按 `retrieval`、`sources`、`quality`、`core`、`gui`、`eval` 分组；当前 **505 tests**（2026-11-12 实测，0 skipped）。分发包不含仓库级 `data/` 时，`tests/sources/test_ehr_generated_neonatal.py` 的 8 个用例与 2 个依赖指南生命周期缓存的用例会自动跳过。

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

## 11. 规则派生数据集与工业级试点（v6.7.2，研究特性）

本节能力**不改变** `/api/retrieve` 默认路径（v6.7.1 ↔ v6.7.2 六场景差分 0）；它们用于数据集构建、评测与单份指南的试点，不是临床结论。

- **数据集构建**（`eval/qrels/`）：`xlsx_reader.py` 用标准库读医院导出 xlsx，`hospital_adapter.py` 转成 HIS 形快照；`fact_rules.py` 从出院小结抽孕周（整数周 → `[7w, 7w+6]` 天）、出生体重、日龄、呼吸支持、黄疸/胆红素、听力筛查与治疗事实；缺失提及记 `not_recorded`，只有明确否定才 `observed_negative`。
- **独立 gold**：`gold_rules.py` 不导入 `rag.quality.applicability`，criterion 级 `match/mismatch/unknown`、指南级 `possible_match/mismatch/unknown`；阈值落在整数周区间内判 `unknown`（`granularity_boundary`），不猜。
- **切分与产物**：住院号级去重切分（住院号 ≠ 个人 ID，不称患者级），1,000 train / 200 dev / 500 test；500 反事实对、500 合成问题、20,400 条患者事实 qrels。`patient_ref` 用 HMAC(salt)，盐只从 `CHINALLM_PATIENT_SALT` 读取、不落盘；原文与证据 span 只写项目内 `private_data/`（`CHINALLM_PRIVATE_DATA_DIR` 指向它），仓库只留聚合。
- **规则基线**：`rubric.py` + `tools/run_v672_rubric.py` 跑 R1–R8；规则基线与 gold 同源，满分是管线自洽检查，不是模型效果。S4 TF-IDF+LR（`model_baseline.py`）0.868/0.848 vs 规则 0.991/1.000，**未超过规则**，只作负结果下限。
- **患者事实检索基线**：BM25 `Recall@1` 0.187 / `Recall@5` 0.618；multilingual-e5 零样本 `Recall@1` 0.340 / `Recall@5` 0.923（通用模型，不是临床模型；胎龄/抗生素/听力仍接近 0）。聚合在 `validation/v672_fact_*_2026-11-1*.json`。
- **可执行适用性解析器**：`eval/qrels/applicability_resolver.py` + `EvidenceRAG.resolve_applicability_workflow`。支持核对目标、同族换本地指南、补事实续跑（`resolution_id + supplied_facts`）、注入式外部检索、查表停止与结构化 `scope_mismatch`；跨族覆盖只放 `other_local_guidelines`，不当作目标答案。离线数据集行为评测（不联网、不传 gold）：A–D 329/400，A 29/100 同族覆盖、71 例仅跨族按设计停止，B/C/D 各 100/100；GA 未接入文本胎龄提取。
- **工业级图试点**：`quality/guideline_graph.py` + `eval/industrial_fixture/vlbw_feeding_2015_graph.json`，只编码 1 份指南（VLBW 喂养 2015）的 4 条建议节点。确定性条件优先，BM25 只做候选排序，模型槽位只能把结果提到 `needs_review`。180 例真实 VLBW 回顾性审计：`possible_match` 178 / `unknown` 2，硬例外 1、NEC 相关 24、时间窗 0/180 可评估。
- **主张级裁定与最小反事实证书**：`eval/qrels/claim_adjudication.py`。720 条主张（concordant 326 / insufficient 369 / guideline_review 24 / guideline_exception 1）；证书用 uniform-cost 搜索在已有结构化字段里找最小改动集（最多 2 字段），时间/文本缺口写 `requires_unstructured_data` 不猜。500 对训练侧单字段反事实正向 246/246、反向 254/254；200 对 dev/test held-out 正向 100/100、反向 100/100，单字段最小性 100/100，bootstrap 95% CI [1.0, 1.0]。证书是规则一致性检查，不是因果结论。
- **生命周期并列**：`quality/guideline_lifecycle_index.py` 读 `data/guideline_lifecycle_cache.json`，把本地指南 PMID 映射为按 `as_of` 的 `current/retracted/superseded/not_yet_published/not_checked`；缺 PMID 或缓存记录一律 `not_checked`，不联网补查。
- **边界**：数据集是规则派生 silver，不是临床 gold（无临床复核、无外部金标准）；`mismatch` 不是临床不适用，`unknown` 不是阴性；指南条件与图试点条件是 `synthetic_fixture`；试点是单份指南回顾性审计，不是临床验证，也不是完整 GAG/PEG、FHIR CPG/CQL 或 CDS Hooks 实现。
