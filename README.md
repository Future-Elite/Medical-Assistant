# Medical Assistant

Medical Assistant 是面向医疗工作流研究的“中国版医疗智能助手”原型，目标是探索如何将大语言模型（LLM）、医院信息系统（HIS）、医学影像系统（PACS）和外部医学知识源（PubMed、指南、药品资料等）连接起来，为医生提供可核对、可追溯、受安全约束的辅助信息。


## 项目背景

当前医疗智能助手的发展方向，不只是让模型回答医学问题，还包括：

- 读取经过授权的患者信息和病历上下文；
- 结合检查结果、用药变化和疾病时间线理解当前问题；
- 根据任务需要检索 PubMed、指南、药品说明书和临床试验等外部医学资料；
- 给回答提供可打开、可定位的证据引用；
- 对回答中的主张进行证据支持检查；
- 在证据不足、来源冲突或超出系统能力时明确提示不确定性或拒答；
- 对不同模型和不同工作流进行统一的安全性、引用质量和任务效果评测。

本项目借鉴这类医疗产品的总体工作方式，但不复刻任何商业产品，也不声称具备商业医疗产品的完整能力或等价效果。

## 总体架构

```text
                         LLM
                          |
          +---------------+---------------+
          |               |               |
         HIS             PACS           PubMed
          |               |               |
          +---------------+---------------+
                          |
                    Medical Agent
                          |
          +---------------+---------------+
          |               |               |
         RAG             Tool            Safety
          |               |               |
          +---------------+---------------+
                          |
                 中国版医疗智能助手
```

项目希望形成的完整工作流是：

```text
医生提出问题
    -> 读取相关患者事实和检查/用药时间线
    -> 判断是否需要外部医学依据
    -> 检索 PubMed、指南或其他授权来源
    -> 返回带来源定位的证据包
    -> 由 Agent/LLM 在证据范围内组织回答
    -> 检查主张、引用和安全条件
    -> 输出可核对的信息、不确定性或拒答原因
```


## 项目模块

项目按照初步分析报告规划为以下模块：

### 模块一：基础模型选型与评测基线（LLM）

目标：

- 选择 3 到 4 个国内外可用的大语言模型或兼容 API；
- 建立统一的模型调用接口；
- 使用覆盖医学知识、推理、引用和安全的测试题集进行基线评测；
- 记录模型能力、成本、延迟和安全表现，为后续系统选择基础模型。

当前仓库不训练自有基础模型。LLM 在系统中是可替换部件，模型接口和调用结果不能直接解释为临床有效性。

### 模块二：双源数据库建设（HIS、PACS、PubMed）

目标是将患者侧数据与外部医学知识分开管理，并为 Agent 提供统一的只读查询接口：

- **HIS**：构造带时间线的患者数据，包括诊断、用药、检验和就诊记录；
- **PACS**：后续接入影像数据或影像结构化描述；
- **PubMed 与其他外部来源**：检索医学文献、指南、药品资料和临床试验信息；
- **统一查询接口**：让上层 Agent 通过受控工具获取数据，而不是直接绕过权限访问底层数据。

当前仓库使用合成或录制数据进行原型测试，不接入真实 HIS/PACS。当前实时外部来源主要是 PubMed E-utilities；正式指南库、药品数据库和临床试验库尚未接入。

### 模块三：RAG 检索增强与证据校验

RAG 模块负责：

- 根据问题和受控术语构建查询计划；
- 从已注册的知识源检索候选资料；
- 进行时间过滤、去重、相关度和来源排序；
- 返回带文档 ID、来源、原文摘录和定位信息的证据包；
- 为证据生成可追溯引用和关键原文句定位；
- 检查回答主张是否引用了本轮检索得到的证据；
- 在 MiniCheck 等可选校验器可用时进行句子级主张检查。

当前 RAG 代码位于 [`rag/`](rag/)，详细调用说明见 [`rag/README.md`](rag/README.md)。

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
        "document_id": "guide-1",
        "kind": "guideline",
        "title": "示例指南",
        "text": "示例证据文本。",
        "updated_at": "2024-01-01",
        "locator": "第 1 章",
    }, source_id="demo-source"),),
))

rag = EvidenceRAG(EvidenceRetrievalPipeline(registry))
result = rag.retrieve_evidence({
    "request_id": "demo-001",
    "question": "示例指南的证据是什么？",
    "source_kinds": ["guideline"],
    "top_k": 5,
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

### 模块四：Medical Agent 工具调用

目标是把医疗任务拆分为可审计的独立工具，例如：

- 查询患者信息；
- 查询检验结果和变化趋势；
- 查询药物变更及相关记录；
- 查询医学文献、指南或药品资料；
- 打开证据原文和定位；
- 对回答进行引用与安全审计。

后续 Agent 应能够根据问题决定是否调用工具、调用哪些工具以及是否需要继续检索，并保留完整的思考和行动日志供复核。当前 `rag/agent.py` 已提供检索、打开引用和回答审计的工具接口，但“由 Agent 自主决定是否检索、检索几轮和何时停止”的完整闭环尚未完成。

### 模块五：多模型终测与安全压力测试

目标是将不同候选模型接入相同的 RAG、Tool 和 Safety 组件，进行统一对比：

- 医学知识和工作流任务表现；
- 引用完整性、引用支持性和来源归属；
- 时间边界和患者事实使用；
- 证据不足时的拒答和不确定性表达；
- 错误剂量、禁忌用药、急症和高风险场景的安全表现；
- 延迟、成本和工具调用稳定性。

当前实验和测试主要验证接口、机制和原型行为。自动判定器、词面支持率和引用存在性不等于人工金标准或临床安全率。

### 模块六：影像接入预研与真实医院数据验证

后续规划包括：

- 接入公开的医学影像数据集，提取或生成结构化影像描述；
- 将影像文本作为 Agent 的新增只读工具；
- 探索病历、医学文献和影像描述的联合分析；
- 在获得明确授权、隐私保护和伦理条件后，评估真实医院数据上的系统流程。

该模块目前属于预研和规划，不代表仓库已经支持真实 PACS、DICOM 或医院部署。

## 当前已实现内容

| 能力 | 当前状态 |
|---|---|
| 合成数据和录制数据上的原型测试 | 已有 |
| Python RAG 直接调用 | 已有 |
| 可插拔只读连接器 | 已有 |
| 实时 PubMed 检索 | 已有 |
| 引用和原文定位 | 已有 |
| 关键证据句定位 | 已有，主要用于回溯，不是医学蕴含判断 |
| 回答主张审计 | 已有 |
| OpenAI-compatible LLM 摘要接口 | 可选接口，需本地配置 |
| MiniCheck 主张校验 | 可选，依赖和模型可用时才执行 |
| 真实 HIS/PACS 接入 | 未实现 |
| 完整自主 Medical Agent | 未完成 |
| 生产部署和临床验证 | 未实现 |

## 安装和运行

要求 Python 3.10 或更高版本。核心 RAG 代码只使用 Python 标准库：

```bash
cd code/src/Medical-Assistant
python3 --version
```

运行离线测试和编译检查：

```bash
python3 -m unittest discover -s rag/tests -p 'test_*.py'
python3 -m compileall -q rag
```

当前测试使用合成或录制数据，不请求实时 PubMed、不请求 LLM，也不下载 MiniCheck 模型。

如需启用 MiniCheck：

```bash
python3 -m pip install -r rag/requirements.txt
```

## 本地工作台与 HTTP 接口

Python 直接调用是推荐入口。需要本地 HTTP 服务或浏览器工作台时，可以启动：

```bash
cd code/src/Medical-Assistant/rag
python3 server.py
```

默认地址为 <http://127.0.0.1:8788/>。

当前 HTTP 服务提供：

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/`、`/index.html` | 浏览器工作台。 |
| `GET` | `/api/health` | 服务、PubMed、LLM 和 MiniCheck 的脱敏状态。 |
| `GET` | `/api/tools` | Agent 工具规格。 |
| `POST` | `/api/retrieve` | 检索证据，可选生成摘要。 |
| `POST` | `/api/citation` | 打开本轮检索中的引用。 |
| `POST` | `/api/audit` | 审计回答主张和引用。 |

HTTP 字段、响应结构、状态码和错误处理见 [`rag/README.md`](rag/README.md)。

## 目录结构

```text
Medical-Assistant/
├── README.md
└── rag/
    ├── README.md          # RAG 直接调用和接口指南
    ├── agent.py           # Medical Agent 的证据工具接口
    ├── audit.py           # 回答审计和 MiniCheck 适配器
    ├── config.py          # 本地配置
    ├── connectors.py      # 知识源连接器和注册表
    ├── evidence.py        # 关键证据句定位
    ├── llm.py             # 可选 LLM 摘要适配器
    ├── models.py          # 请求、文档和证据包契约
    ├── pipeline.py        # 检索和证据包生成
    ├── pubmed.py          # 实时 PubMed 连接器
    ├── query.py           # 查询计划和受控术语扩展
    ├── ranking.py         # 去重、排序和多样化
    ├── server.py          # HTTP 服务
    ├── demo.html          # 本地浏览器工作台
    ├── requirements.txt   # 可选依赖
    └── tests/             # 离线测试
```
