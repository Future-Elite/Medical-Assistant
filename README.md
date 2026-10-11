# Medical Assistant（医生助手原型）

面向医疗工作流研究的**只读**原型：把医院信息系统（HIS）里的患者数据与外部医学证据（PubMed、指南等）在同一个可审计的工作流里连起来，为医生提供**可核对、可追溯、带引用**的辅助信息。

> **状态**：研究原型，非生产系统。只用**合成数据**；不接生产 HIS/PACS、不写回病历/医嘱/处方；输出不是诊断、治疗建议或临床决策。
> **组件**：[`rag/`](rag/) 证据检索（Python）· [`his/`](his/) HIS 演示系统（Spring Boot + Vue，合成数据）· `agent/` Medical Agent（独立维护）· [`contracts/`](contracts/) 跨组件契约

---

## 1. 这是什么

医生助手类产品的工作方式并不只是"让模型回答问题"，而是：读取经授权的患者上下文 → 按需检索外部医学资料 → 返回可打开、可定位的引用 → 检查回答中的主张是否有证据支持 → 证据不足时明确不确定性或拒答。

本项目借鉴这套工作方式做**可运行、可审计的原型**，用于研究与评测，不复刻任何商业产品，也不声称具备其完整能力或等价效果。

明确不做：

- 不训练自有基础大模型（LLM 是可替换部件）；
- 不接生产 HIS/PACS、不读真实病历、不写回任何临床数据；
- 不输出诊断、处方或治疗建议；所有输出都必须能回到来源核对。

## 2. 架构与数据流

```text
                         医生提问
                            │
                    ┌───────▼────────┐
                    │  Agent（编排）  │  ← 独立维护线
                    └───┬────────┬───┘
        只读读取患者数据  │        │  调用检索工具
                    ┌───▼───┐ ┌──▼────────────┐
                    │  HIS  │ │      RAG      │  ← 本仓库 rag/
                    │(合成) │ │ 证据检索+审计  │
                    └───────┘ └──┬────────────┘
                                 │ 只发送审核后的检索词
                        ┌────────▼─────────┐
                        │ PubMed / 指南等  │
                        └──────────────────┘
```

**分层是硬约束（`contracts/his_rag.md`）**：

- **HIS → Agent → RAG**：HIS 由 Agent 只读读取，整理成结构化 **`patient_snapshot` 载荷**后传给 RAG；**RAG 不连接 HIS**（包内没有 HIS 客户端或端点，有结构性测试守护）；
- 与 HIS 的交互只允许 `GET`；患者标识（姓名/身份证/电话/地址）在载荷生成时剔除，绝不进入证据包；
- 送往外部知识源的只有**人工审核后的检索词**，不发送患者数据；
- RAG 只接收快照，负责时间边界（`as_of`）、引用与回答审计。

完整工作流：

```text
医生提出问题
  → Agent 读取相关患者事实与检查/用药时间线（只读）
  → 判断是否需要外部医学依据
  → RAG 检索 PubMed、指南或其他授权来源（分源联合检索）
  → 返回带来源定位与引用的证据包
  → 由 Agent/LLM 在证据范围内组织回答
  → 检查主张、引用与安全条件
  → 输出可核对的信息、不确定性或拒答原因
```

## 3. 快速开始

要求 **Python 3.10+**；核心 RAG 只用标准库（`rag/requirements.txt` 仅含可选校验依赖）。在仓库根目录执行：

```bash
# 测试（离线：不联网、不加载模型、只用合成数据）
python3 -m unittest discover -s rag/tests -p 'test_*.py'   # 包内测试
python3 -m unittest discover -s tests -t . -p 'test_*.py'  # 跨组件契约测试（HIS→Agent→RAG）
python3 -m compileall -q rag contracts
```

直接调用 RAG（最短示例；完整 API 见 [`rag/README.md`](rag/README.md)）：

```python
from rag.agent import EvidenceRAG
from rag.connectors import ConnectorRegistry, InMemoryConnector
from rag.models import SourceDocument
from rag.pipeline import EvidenceRetrievalPipeline

registry = ConnectorRegistry()
registry.register(InMemoryConnector(
    source_id="demo-source",
    kinds=("guideline",),
    documents=(SourceDocument.from_dict({
        "document_id": "guide-1", "kind": "guideline", "title": "示例指南",
        "text": "示例证据文本。", "updated_at": "2024-01-01", "locator": "第 1 章",
    }, source_id="demo-source"),),
))
rag = EvidenceRAG(EvidenceRetrievalPipeline(registry))

result = rag.retrieve_evidence({
    "request_id": "demo-001", "question": "示例指南的证据是什么？",
    "source_kinds": ["guideline"], "top_k": 5,
})
citation = rag.open_citation({
    "retrieval_id": result["retrieval_id"],
    "citation_id": result["citations"][0]["citation_id"],
})
audit = rag.audit_grounded_answer({
    "retrieval_id": result["retrieval_id"],
    "answer": f"示例证据支持该陈述[{citation['citation']['citation_id']}]。",
})
```

可选：本地 HTTP 服务与浏览器工作台（默认 <http://127.0.0.1:8797/>，可用 `RAG_SERVER_PORT` 覆盖）：

```bash
python3 -m rag.server          # 或 cd rag && python3 server.py
```

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/`、`/index.html` | 浏览器工作台（单文件 HTML，无前端框架） |
| `GET` | `/api/health` | 服务与来源模式就绪状态（脱敏） |
| `GET` | `/api/tools` | Agent 工具规格（`tool_specifications()`） |
| `POST` | `/api/retrieve` | 联合检索证据（来源模式/检索方法/重排/查询保真门控参数） |
| `POST` | `/api/his/snapshot` | 回显调用方传入的 `patient_snapshot` 并按 `as_of` 过滤（服务不持有患者数据） |
| `POST` | `/api/citation` | 打开本轮检索中的引用 |
| `POST` | `/api/audit` | 审计回答主张与引用 |

## 4. 目录结构

```text
Medical-Assistant/
├── README.md
├── CONTRIBUTING.md          # 分支、提交、版本与发布规范（本仓库权威）
├── rag/                     # RAG 组件（Python 包 `rag`，正式发布线）
│   ├── README.md            # 直接调用与接口指南
│   ├── agent.py             # EvidenceRAG：检索 / 打开引用 / 回答审计
│   ├── pipeline.py          # 分源联合检索、联合选择与证据包生成
│   ├── ranking.py           # 分源排序、RRF、相关度门、MMR、联合选择
│   ├── query.py             # 查询计划、受控术语扩展、检索式自检
│   ├── fidelity.py / gate.py # 查询保真审计与分级门控（研究特性，默认关闭）
│   ├── his.py               # 只读 HIS 快照契约（PatientSnapshot 接收端）
│   ├── connectors.py / pubmed.py / guidelines.py / custom_sources.py
│   ├── server.py            # HTTP 服务与浏览器工作台（只读）
│   ├── contracts/           # RAG 包自身契约（JSON + 说明 + 示例，随包发布）
│   ├── eval/                # 离线实验指标骨架（实验前模式）
│   └── tests/               # 包内离线测试（合成数据仅存在于 tests/）
├── his/                     # HIS 演示系统（Spring Boot + Vue，合成数据；subtree 挂载，他人维护）
├── contracts/               # 跨组件契约（有版本号；HIS→Agent→RAG 分层与载荷）
└── tests/                   # 跨组件契约测试（离线；Python 3.10+，标准库即可）
```

## 5. 组件与接口版本

三层版本各自独立演进；"能一起跑通"的组合用 git tag 表示（发布规范见 [CONTRIBUTING.md](CONTRIBUTING.md)）。

| 层 | 位置 | 当前值 |
|---|---|---|
| RAG 包 | `rag/__init__.py` 的 `__version__` | `6.6.2` |
| HIS | `his/backend/pom.xml` 的 `<version>` | `1.0.0`（随 subtree 同步） |
| HIS × RAG 接口契约 | `contracts/his_rag.md` 的 `contract_version` | `0.2.0` |
| 组合版本（release） | git tag `medassist-<x.y.z>` | 暂无（真实 HIS 联调通过后打 `medassist-0.1.0`） |

## 6. 能力状态

| 能力 | 状态 |
|---|---|
| Python RAG 直接调用（检索 / 引用 / 审计） | 已有 |
| 可插拔只读连接器；实时 PubMed 检索 | 已有 |
| 分源联合检索（患者事实 + PubMed + 版本化指南）与来源分离输出 | 已有 |
| 引用打开、关键证据句定位、回答主张审计 | 已有（定位/审计是辅助口径，不是临床正确性） |
| 可切换检索方法（bm25/ql/rm3 + 可选神经方法、RRF 融合） | 已有；神经方法未配置时显式 `not_configured` |
| 查询保真审计与分级门控（研究特性，默认 `off`） | 已有（确定性词面基线；见 §8 边界） |
| HIS 演示系统（Spring Boot + Vue，合成数据） | 已有，HIS 维护者负责，subtree 同步 |
| HIS → Agent → RAG 只读快照打通 | 已有：Agent 侧参考实现 + 载荷契约 + 跨组件测试；真实 HIS 联调待本地执行 |
| Medical Agent 自主编排闭环 | 未完成（独立维护线） |
| PACS / 影像接入、真实医院数据验证 | 未实现（预研） |
| 生产部署与临床验证 | 未实现 |

## 7. 契约

跨组件契约与包内契约分开维护：

| 契约 | 位置 | 说明 |
|---|---|---|
| HIS × RAG 只读快照（分层、端点、映射、载荷） | [`contracts/his_rag.md`](contracts/his_rag.md)（`contract_version: 0.2.0`） | HIS → Agent → RAG；样例与载荷黄金样例在 [`contracts/examples/`](contracts/examples/) |
| Agent 侧参考实现（读 HIS → 载荷） | [`contracts/agent_his_adapter.py`](contracts/agent_his_adapter.py) | 不属于 `rag` 包；演示只读读取、标识剔除与映射 |
| RAG 包接口契约（工具规格、快照 schema、来源模式与示例） | [`rag/contracts/agent_contract.json`](rag/contracts/agent_contract.json) | `contract_version` 与包 `__version__` 同步，随包发布 |
| 契约索引与纪律 | [`contracts/README.md`](contracts/README.md) | 变更必须版本 +1，样例同步更新 |

## 8. 安全与使用边界

- **只读**：所有工具与连接器只读；不写 HIS/PACS，不落病历、医嘱、处方。
- **数据**：仓库内测试、示例、演示一律使用**合成数据**并标注；不写入真实患者数据；`patient_terms` 只能传入人工审核的最小化术语。
- **最小信息**：送往外部连接器的只有完成检索所需的最小信息（审核英文词、MeSH、问题与 `as_of`）；患者标识、自由文本病史、凭据一律不发。
- **判定口径**：关键词匹配、排序分数、关键证据句、引用存在性、查询保真判定与 MiniCheck/ALCE 风格指标都**不是**临床正确性、指南符合性或医生审核结论；未测量不写成零错误率。
- **公开集结果**（PubMedQA/BioASQ 等）只支持对应数据与任务下的结论，不代表中国患者或 HIS 场景评测。
- **查询保真层是研究特性**：默认 `off`；审计器是确定性词面基线（单位族/否定/时间启发式），判定名（`suspected_*`/`unverifiable`）是对人工标注量的假设，质量需由实验测量。
- **发布纪律**：[CONTRIBUTING.md](CONTRIBUTING.md) 规定不得提交凭据、真实患者数据、构建产物；不 force-push `main`/`rag`。

## 9. 规划中的模块（研究路线）

1. **基础模型选型与评测基线（LLM）**——统一模型接口 + 基线评测（不训练自有模型）；
2. **双源数据库建设（HIS / PACS / PubMed）**——患者侧与外部知识分开管理，经受控只读工具访问；
3. **RAG 检索增强与证据校验**——当前主力，见 `rag/`；
4. **Medical Agent 工具调用**——自主决定检索时机/轮数的完整闭环（独立维护线）；
5. **多模型终测与安全压力测试**——同一 RAG/Tool/Safety 下横向对比；
6. **影像接入与真实数据验证**——公开影像数据集预研；真实医院数据需另行授权与伦理条件。

## 10. 参与开发

- 分支、提交信息、版本与发布流程：[CONTRIBUTING.md](CONTRIBUTING.md)（本仓库权威规范）
- 跨组件契约：[contracts/README.md](contracts/README.md)
- RAG 包调用与接口：[rag/README.md](rag/README.md)
- HIS 组件：[`his/`](his/)（由 HIS 维护者负责；本仓库不直接修改其文件）

**许可**：本仓库尚未附许可协议（待定）。
