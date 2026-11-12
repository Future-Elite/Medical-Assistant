# ChinaLLM 医疗 Agent 与 RAG 检索组件

ChinaLLM 是面向医学研究与教学的中国医疗 LLM/Agent 原型。本仓库是 RAG 检索组件的正式发布线，提供只读的患者事实检索、外部证据检索、引用打开与回答审计能力。

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
- 规则派生数据集与评测底座：出院小结 → HIS 形快照 → 事实 qrels、反事实对、rubric 与检索基线（研究特性）。
- 指南适用性工作流：同族换指南、补事实续跑、注入式外部检索、结构化 `scope_mismatch`（研究特性）。
- 单份指南工业级图试点：主张级裁定与最小反事实证书（研究特性，不是临床验证）。
- 逐轮检索控制器（研究层，默认不改变标准检索）。
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
│   ├── agent.py                 # EvidenceRAG：检索 / 打开引用 / 回答审计
│   ├── pipeline.py              # 分源联合检索与证据包生成
│   ├── models.py                # 请求 / 文档 / 证据包契约
│   ├── config.py                # 运行配置
│   ├── server.py                # 只读 HTTP 服务
│   ├── retrieval/               # 查询规划、BM25/QL/RM3、排序、路由、控制器
│   ├── sources/                 # 指南 PDF / 全文 / 分块 / 解析来源 / 生命周期
│   ├── quality/                 # 适用性、术语映射、MeSH 链接、主张校验、指南图、生命周期索引、观测
│   ├── eval/                    # 查询保真入口；eval/qrels 数据集构建与评测；eval/*_fixture 评测夹具
│   ├── web/                     # React + Vite 工作台（src/ 源码，dist/ 构建产物）
│   ├── demo/                    # 最小演示数据（50 例合成病例、5 例链路演示与指南元数据）
│   ├── contracts/               # 机器可读契约与示例
│   ├── validation/              # 兼容性、路由、控制器、MeSH、生命周期与 v6.7.2 数据集验证工件
│   ├── tests/                   # 按 retrieval / sources / quality / core / gui 分目录
│   └── requirements.txt
├── guideline_parsing/           # 本地指南解析器（与 rag/ 并列）
├── tools/                       # 构建、审计与评测脚本
└── data/                        # 仓库级运行数据（不进入版本控制）
```

`data/` 不进入版本控制。运行时优先读取 `data/`；缺失时自动回退到 `rag/demo/` 的最小演示数据。指南解析数据通过 `RAG_GUIDELINE_DATA` 指向外部目录。

## 5. 版本

| 层 | 位置 | 当前值 |
|---|---|---|
| RAG 包 | `rag/__init__.py` 的 `__version__` | `6.7.2` |
| Agent/HIS/RAG 契约 | `rag/contracts/agent_contract.json` 的 `contract_metadata.contract_version` | `6.7.2` |
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
| `POST` | `/api/retrieve` | 联合检索证据 |
| `POST` | `/api/applicability` | 指南适用性工作流（并列检索与工业级图试点） |
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
| `RAG_ALLOW_LIVE_PUBMED`、`RAG_ALLOW_LIVE_GUIDELINES` | 启用实时来源 |
| `RAG_GUIDELINE_DATA` | 指南解析数据目录 |
| `RAG_GUIDELINE_CHUNKS` | 预生成分块目录 |
| `RAG_GUIDELINE_PDF_DIR` | 调用方 PDF 目录 |
| `RAG_TERM_MAPPING_LIBRARY` | 通用术语映射库 |
| `RAG_MESH_LEXICON` | MeSH 词表路径 |
| `RAG_MINERU_BIN`、`RAG_MINERU_VENV` | MinerU 可执行文件或其独立环境根目录 |
| `RAG_ROUTING_MODE` | 规则路由：`off` / `shadow` / `active` |
| `RAG_ENTITY_LINKING_MODE` | MeSH 实体链接：`off` / `shadow` / `active` |
| `RAG_GUIDELINE_LIFECYCLE_MODE` | 指南生命周期：`off` / `shadow` / `active` |

凭据通过环境变量或本地未跟踪配置注入，不写入代码、日志或提交记录。

## 8. 测试

```bash
# RAG 包（按 retrieval / sources / quality / core / gui 分组）
PYTHONPATH=. python3 -m unittest discover -s rag/tests -p "test_*.py"

# 本地指南解析器
PYTHONPATH=. python3 -m unittest discover -s guideline_parsing/tests -p "test_*.py"
```

当前：RAG 包 **505 tests**，指南解析器 **8 tests**（2026-11-12 实测）。分发包不含仓库级 `data/` 时，10 个依赖本地数据的用例自动跳过。

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
