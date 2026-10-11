# Shared contracts

Versioned request, response, evidence, and citation contracts shared by `rag/` and `agent/` belong here.

## 本目录放什么

跨**组件之间**的契约（谁调谁、数据怎么传、字段怎么映射），以及配套的样例与参考实现；组件**内部**的接口契约留在组件目录（如 `rag/contracts/`），随该组件发布。

| 文件 | 版本位 | 内容 | 维护线 |
|---|---|---|---|
| `his_rag.md` | `contract_version: 0.2.0`（文件头） | HIS → Agent → RAG 分层与只读快照载荷：Agent 读取的三个只读端点、`TimelineEvent` → 事实映射、标识剔除规则、区间与存在性语义、变更纪律 | `main` |
| `agent_his_adapter.py` | 随 `his_rag.md` | **Agent 侧参考实现**：读 HIS → 生成 `patient_snapshot` 载荷（**不属于 `rag` 包**） | `main` |
| `examples/` | 随 `his_rag.md` | 合成 HIS 响应样例 + 载荷黄金样例（跨组件测试逐字比对） | `main` |
| `../rag/contracts/agent_contract.json` | `contract_version` 与包 `__version__` 同步（当前 `6.6.2`） | RAG 包自身接口契约：工具规格、快照 schema、来源模式与示例（随 RAG 包发布） | `rag` 线 |

## 分层与边界（不可协商）

- **HIS → Agent → RAG**：HIS 由 Agent 只读读取；**RAG 不连接 HIS**——`rag/` 内不得出现 HIS 客户端、HIS 地址或 `/api/patients` 等端点（跨组件测试有结构性断言）。
- 与 HIS 的交互只允许**只读**（`GET`）；不写病历、医嘱、处方。
- 患者标识（姓名/身份证/电话/地址/患者主键）**不进载荷、不进证据包**；外部知识源只收人工审核后的检索词。
- 样例、测试与演示数据一律**合成**，并在文件中标注。

## 变更纪律

- 每个契约在文件头维护自己的版本号；字段增删、类型/语义变化、或分层/载体变化必须 **+1**，并在提交信息（`docs(contracts):`）中写明。
- **扩展方式：新增文件**；改动既有文件时先与对该文件负责的维护线对齐（各条线合并到 `main` 时保持零冲突）。
- 样例是契约的一部分：映射或字段变更必须同步更新 `examples/`（黄金样例与映射实现一起改）。
- 校验命令（离线，在仓库根目录运行）：

```bash
python3 -m unittest discover -s tests -t . -p 'test_*.py'
```
