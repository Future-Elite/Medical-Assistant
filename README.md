# ChinaLLM 医疗 Agent 与 RAG 检索组件

ChinaLLM 是面向医学研究与教学的中国医疗 LLM/Agent 原型。本仓库是 RAG 检索组件的正式发布线，提供只读的患者事实检索、外部证据检索、引用打开与回答审计能力。

> **当前分支**：`v6.8.0` 概念保真评测研发分支；正式发布线以 `origin/rag` 为准。本分支不改变默认检索路径。

> 本项目是研究原型，不是生产医疗系统。输出不构成诊断、治疗建议或临床决策；不连接生产 HIS/PACS，不写回病历、医嘱或处方。

## 1. 组件分层

```text
HIS（只读快照） → Agent（编排与工具调用） → RAG（检索、引用、审计）
```

- **HIS**：由调用方读取并以 `patient_snapshot` 传入；RAG 不连接 HIS。
- **Agent**：负责编排、工具调用与回答生成；在独立维护线，不在本仓库。
- **RAG**：本仓库主体，负责查询规划、分源检索、联合选择、引用与审计。

## 2. 能力范围

- 分源联合检索：患者事实、PubMed、版本化指南。
- 查询规划：术语映射、概念分组变体、PubMed 字段限定检索。
- 检索方法：BM25、QL、RM3、可选语义检索与 RRF 融合。
- 重排：MedCPT cross-encoder 与 Qwen3-Reranker，可选候选池。
- 指南解析：PDF/Markdown 分块、页码标注、结构化适用性核对。
- 工业级校验：MeSH 实体链接、PubMed 指南生命周期治理、MiniCheck 主张级校验。
- 查询保真审计与分级门控（研究特性，默认关闭）。
- 概念保真评测：槽级概念身份、MeSH 下位/同支混淆探针、14 条中文查询与 23 条合成英文文档的离线 pilot（v6.8.0 研究特性）。
- 规则派生数据集与评测底座：出院小结 → HIS 形快照 → 事实 qrels、反事实对、rubric 与检索基线（研究特性）。
- 指南适用性工作流：同族换指南、补事实续跑、注入式外部检索、结构化 `scope_mismatch`（研究特性）。
- 逐轮检索控制器（研究层，默认不改变标准检索）。
- 患者知识图谱与图检索：从调用方传入的可见快照按请求构建只读病例覆盖图，患者事实检索走受限图检索（v6.7.3）。
- 指南知识图：EHR 式指南知识层，含四态条件、Graph-RAG 检索、动作校验、复查/病程门控与发布包生命周期（合成夹具，不是临床审核）。
- 双向图检索与 Agentic 迭代检索：k 本本地指南 × 单个患者图的双向融合；Interpreter → Explorer → Arbiter 确定性迭代（合成契约场景）。
- 在线药品标签：openFDA 标签连接器（美国标签，不是中国药品库或剂量建议）。
- 只读 HTTP 服务与 React 工作台。

## 3. 环境与快速开始

核心检索只依赖 Python 3.10+ 标准库。PDF 指南解析需要 `guideline_parsing/requirements.txt`（MinerU）；仅重建前端时需要 Node.js 20+。

```bash
# 启动只读服务（仓库根目录）
PYTHONPATH=. python3 -m rag.server
# 打开 http://127.0.0.1:8798/
```

可选能力：

- **MiniCheck 主张校验**：由调用方安装 `minicheck` 与本地权重；未配置时主张标签为 `not_checked`。`rag/requirements.txt` 只说明这一边界，不安装包。
- **指南 PDF 解析**：`pip install -r guideline_parsing/requirements.txt`，并用 `RAG_MINERU_BIN` 指向独立环境中的 `mineru-kit`。
- **前端重建**：

```bash
cd rag/web
npm install
npm run build
```

前端构建产物已随仓库提供。

## 4. 目录结构

```text
.
├── README.md
├── rag/                         # RAG 组件（Python 包 rag）
│   ├── README.md                # 组件使用指南
│   ├── __init__.py              # 公共 API、__version__、旧导入路径别名
│   ├── server.py                # 只读 HTTP 服务
│   ├── core/                    # 请求 / 文档 / 证据包契约、运行配置、HIS 快照
│   ├── agent/                   # EvidenceRAG：检索 / 打开引用 / 回答审计
│   ├── retrieval/               # 查询规划、BM25/QL/RM3、联合检索、路由、控制器、迭代检索
│   ├── patient_graph/           # 患者事实的每请求只读知识图与受限图检索
│   ├── knowledge/               # EHR 式指南知识图、四态条件、Graph-RAG、动作校验
│   ├── concept_fidelity/        # 概念保真评测：槽级身份、MeSH 探针、离线 pilot harness
│   ├── sources/                 # PubMed / 指南 / 药品 / 自定义来源连接器与指南解析
│   ├── adapters/                # MedCPT / Qwen3 / BGE-M3 / EHR 视图惰性适配
│   ├── quality/                 # 适用性、术语映射、MeSH 链接、主张校验、生命周期索引、观测
│   ├── eval/                    # 查询保真入口；eval/qrels 数据集构建与评测
│   ├── web/                     # React + Vite 工作台（src/ 源码，dist/ 构建产物）
│   ├── demo/                    # 最小演示数据（50 例合成病例、5 例链路演示与指南元数据）
│   ├── contracts/               # 机器可读契约与示例
│   ├── tests/                   # 按 core / retrieval / sources / quality / patient_graph / knowledge / concept_fidelity / gui / eval 分组
│   └── requirements.txt
├── guideline_parsing/           # 本地指南解析器（与 rag/ 并列）
└── data/                        # 本地运行数据与评测工件（不进入版本控制）
```

`data/` 不进入版本控制。运行时优先读取 `data/`；缺失时自动回退到 `rag/demo/` 的最小演示数据。指南解析数据通过 `RAG_GUIDELINE_DATA` 指向外部目录。

构建、审计与评测脚本（`tools/`）属于内部研发工具，不随发布分支分发；需要复现评测时从研发树获取。

## 5. 版本

| 层 | 位置 | 当前值 |
|---|---|---|
| RAG 包 | `rag/__init__.py` 的 `__version__` | `6.8.0` |
| Agent/HIS/RAG 契约 | `rag/contracts/agent_contract.json` 的 `contract_metadata.contract_version` | `6.8.0` |
| 组合版本 | git tag `medassist-<x.y.z>` | 尚未发布 |

## 6. HTTP API

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/`、`/index.html` | React 工作台（`rag/web/dist`） |
| `GET` | `/assets/*` | 前端静态资源 |
| `GET` | `/api/health` | 进程、来源模式与能力状态（脱敏） |
| `GET` | `/api/metrics` | 进程内计数与延迟分位 |
| `GET` | `/api/tools` | Agent 工具规格 |
| `GET` | `/api/sources` | 已注册来源 |
| `POST` | `/api/sources/custom`、`/api/sources/custom/clear` | 注册 / 清空调用方来源库 |
| `GET` | `/api/guidelines` | 指南列表与解析选项 |
| `GET` | `/api/guidelines/chunks` | 指南分块预览 |
| `POST` | `/api/guidelines/parse` | 解析指南 |
| `GET` | `/api/guidelines/pdf`、`/api/guidelines/pdf/meta`、`/api/guidelines/pdf/page` | PDF 状态、页信息与页面渲染 |
| `POST` | `/api/guidelines/applicability` | 指南条件与患者事实核对 |
| `POST` | `/api/guidelines/save-live` | 保存实时指南并复用本地解析器 |
| `GET` | `/api/demo/cases`、`/api/demo/applicability-cases`、`/api/demo/guidelines` | 合成演示病例（50 例档案 / 5 例链路演示）与指南库 |
| `POST` | `/api/demo/guidelines/load` | 显式注册演示指南库 |
| `POST` | `/api/his/snapshot` | 回显并按 `as_of` 过滤调用方快照 |
| `POST` | `/api/patient-graph`、`/api/patient-graph/search` | 患者快照建图与患者事实图检索 |
| `GET` | `/api/knowledge`、`/api/knowledge/graph` | 知识图概览 |
| `POST` | `/api/knowledge/search` | 指南知识图检索（含双向融合） |
| `POST` | `/api/knowledge/actions/validate` | 动作校验（四态条件、时间线与观察稳定性） |
| `GET` | `/api/knowledge/guideline-graphs`、`POST /api/knowledge/guideline-graphs/build` | 指南图谱读取与构建 |
| `POST` | `/api/knowledge/guideline-fusion` | k 本本地指南 × 单个患者图的双向融合 |
| `POST` | `/api/knowledge/iterative-retrieve` | Agentic 迭代检索 |
| `GET` | `/api/knowledge/releases`、`POST /api/knowledge/releases/publish`、`.../withdraw`、`.../rollback` | 知识发布包生命周期 |
| `POST` | `/api/knowledge/review-plan`、`/api/knowledge/episode-end` | 复查计划与病程结束门控 |
| `POST` | `/api/retrieve` | 联合检索证据 |
| `POST` | `/api/applicability` | 指南适用性工作流（并列检索与生命周期信号） |
| `POST` | `/api/controller/retrieve` | 逐轮控制器试运行 |
| `POST` | `/api/citation` | 打开本轮引用 |
| `POST` | `/api/audit` | 审计回答主张与引用 |
| `POST` | `/api/settings/reranker` | 进程内切换重排器与候选池 |
| `POST` | `/api/settings/industrial` | 进程内切换实体链接、生命周期与主张校验 |

请求体必须为 JSON 对象，最大 1,000,000 字节。错误响应包含 `error`；服务端异常额外包含 `exception_type`、`stage`、`error_id`，并对凭据脱敏。

## 7. 配置

主要环境变量：

| 变量 | 作用 |
|---|---|
| `RAG_SERVER_HOST`、`RAG_SERVER_PORT` | 服务监听地址与端口（默认 `127.0.0.1:8798`） |
| `RAG_ALLOW_LIVE_PUBMED` | 启用 PubMed 实时来源 |
| `RAG_GUIDELINE_DATA` | 指南解析数据目录 |
| `RAG_GUIDELINE_CHUNKS` | 预生成分块目录 |
| `RAG_GUIDELINE_PDF_DIR` | 调用方 PDF 目录 |
| `RAG_TERM_MAPPING_LIBRARY` | 通用术语映射库 |
| `RAG_MESH_LEXICON` | MeSH 词表路径 |
| `RAG_MINERU_BIN` | MinerU 可执行文件路径 |
| `RAG_ROUTING_MODE` | 规则路由：`off` / `shadow` / `active` |
| `RAG_ENTITY_LINKING_MODE` | MeSH 实体链接：`off` / `shadow` / `active` |
| `RAG_GUIDELINE_LIFECYCLE_MODE` | 指南生命周期：`off` / `shadow` / `active` |
| `RAG_PATIENT_GRAPH_MAX_HOPS`、`RAG_PATIENT_GRAPH_MAX_NODES` | 患者图遍历跳数与节点预算 |
| `RAG_PATIENT_GRAPH_LEXICON` | 患者事实词表路径 |

凭据通过环境变量或本地未跟踪配置注入，不写入代码、日志或提交记录。

## 8. 测试

```bash
# RAG 包（从 rag/ 目录执行，按 core / retrieval / sources / quality / patient_graph / knowledge / gui / eval 分组）
cd rag
PYTHONPATH=.. python3 -m unittest discover -s tests -p "test_*.py"
cd ..

# 本地指南解析器
PYTHONPATH=. python3 -m unittest discover -s guideline_parsing/tests -p "test_*.py"
```

当前：RAG 包 **650 tests**、指南解析器 **8 tests**（2026-11-19 实测）。分发包不含仓库级 `data/` 时，22 个依赖本地数据的用例自动跳过。

## 9. 安全与合规

- **只读**：不写回 HIS/PACS，不生成诊断或治疗决策。
- **患者数据**：真实患者数据不得进入仓库、日志或未授权外部服务；演示数据为合成数据并标记 `synthetic=true`。
- **最小信息**：外部连接器只接收完成检索所需的最小信息，不发送原始病历或可识别信息。
- **引用可追溯**：证据必须带来源、定位与引用；证据不足时显示不确定性，不把缺记录解释成否定事实。
- **许可边界**：受限指南的全文与解析产物留在仓库外；仅开放获取内容可随仓库分发。

## 10. 贡献

- 提交前运行 §8 的两组测试。
- 确认未混入凭据、患者数据、研发仓库路径或临时文件。
- 一个版本一次提交，提交信息写明版本与变更范围；发布分支以 `origin/rag` 为准。
