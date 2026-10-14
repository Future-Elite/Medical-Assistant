# 参与开发：分支、提交与版本规范

> 本仓库是"医生助手"原型的多组件仓库（`rag/` + `his/` + `agent/`，跨组件契约与测试在顶层 `contracts/`、`tests/`）。本文件规定分支模型、提交信息、版本号与发布流程，所有协作者提交前请遵循。
> 跨组件契约见 [`contracts/README.md`](contracts/README.md)、跨组件测试见 [`tests/README.md`](tests/README.md)；RAG 包本身的调用方式见 [`rag/README.md`](rag/README.md)。

## 1. 组件与分支

| 目录 | 组件 | 维护线 | 说明 |
|---|---|---|---|
| `rag/` | RAG 证据检索（Python 包 `rag`） | 分支 `rag` | 正式发布线；版本在 `rag/__init__.py` 的 `__version__` |
| `his/` | HIS 演示系统（Spring Boot + Vue，合成数据） | 分支 `his` | 由 HIS 维护者负责；本仓库用 subtree 挂载，只做同步 |
| `agent/` | Medical Agent | 分支 `agent` | 由 Agent 维护者负责（Agent 最终调用 `rag/` 与 `his/`）；已通过集成分支并入 `main`，`git subtree` 挂载 |
| `contracts/` | 跨组件契约（有版本号） | `main` | 只描述组件之间的接口；组件**内部**契约留在组件目录（如 `rag/contracts/`） |
| `tests/` | 跨组件测试 | `main` | 离线优先（stub/录制数据，不依赖 MySQL 与网络）；模块内单测留在模块自己的 `tests/` |

### 分支一览（2026-10-13）

| 分支 | 维护者 | 内容 | 规则 |
|---|---|---|---|
| `main` | 集成区（本项目） | 集成/发布线：`rag/` + `his/` + `contracts/` + `tests/` + 根文档 | 只接受合并与顶层集成区改动；`rag/` 不在其上直接改 |
| `rag` | RAG 线（本项目） | RAG 包开发 | 里程碑与 `main` 对齐；此后可临时领先 |
| `agent` | Agent 维护者 | Medical Agent 代码 | 他人线：不改动、不评论；其改动走该分支 |
| `feat/medical-agent-integration` | Agent 维护者 | Agent × RAG × HIS 集成 | 他人线：已并入 `main`（`4976389`）；保留与否由其维护者决定 |
| `his` | HIS 维护者 | HIS 演示系统 | 只通过 `git subtree pull` 同步进 `main/his/` |
| `hisdata` | 数据维护者 | 上海合成患者数据（Synthea 版，T2DM 端到端） | 他人线、独立历史；若其产物要进 `his/` 由 HIS 维护者决定 |

分支规则：

- **`main`：集成/发布线。** 永远代表"各组件能一起跑通"的组合，只接受合并与顶层集成区（`contracts/`、`tests/`、根 `README.md`）的改动；**不直接在 `main` 上修改 `rag/`**。
- **`rag`：RAG 组件线。** `rag/` 内的改动落在 `rag` 线（或其短分支）并同步到 `main`；两条线在里程碑对齐（例如发布或大特性落地时把 `rag` 快进到 `main`，此后 `main` 可临时领先——属设计而非漂移）。
- **`his`：HIS 组件线（他人维护）。** **不要直接修改 `his/` 下的文件**——它们会随 subtree 同步被覆盖或产生冲突；需要 HIS 侧改动时先与 HIS 维护者沟通，走 `his` 分支。
- **`agent`：Agent 组件线（他人维护）。** 同理：不直接改 `agent/` 下的文件，改动走 `agent` 分支；`main` 只做整合。
- **他人线的集成分支（如 `feat/medical-agent-integration`）由各自维护者整合。** 我们不改动、不代为合并；需要联动时先对齐 `contracts/` 的契约版本（当前 `his_rag.md` 0.2.0）与 `rag` 包版本（当前 6.6.2）。
- **集成工作用短分支。** 从 `main` 开 `feat/…`、`fix/…`，完成并测试后合并回 `main`。
- **`contracts/` 与 `tests/` 优先用「新增文件」扩展。** 新增不产生合并冲突；确需扩展他人已有的文件（如 `contracts/README.md` 已由集成区保留原文并追加索引）时：**保留原文再追加**、在提交信息中说明、并知会该文件的维护线——这会产生一次内容冲突，需在对应分支合并前对齐。

## 2. 提交信息（Conventional Commits）

格式：`<type>(<scope>): <描述>`；`scope` 用目录名或模块名；描述里保留版本号（如 `v6.7.0`）。当前各组件的版本值见根 [README](README.md) 的「组件与接口版本」表，不在此重复。

| type | 用途 |
|---|---|
| `feat` | 新能力 |
| `fix` | 缺陷修复 |
| `docs` | 文档 |
| `test` | 测试 |
| `refactor` | 重构（不改变行为） |
| `chore` | 同步、依赖、构建等杂项 |

示例：

```text
feat(rag): 升级到 v6.7.0 —— <新检索能力>
fix(rag): 移除越层的 HIS 客户端（RAG 不直连 HIS）
docs(contracts): HIS→Agent→RAG 分层契约 0.2.0
test(contracts): Agent 传入快照的跨组件测试（离线）
chore(his): 同步 his 线至 <short-sha>
docs: 更新项目结构与组件版本表
```

纪律：

- 一个提交只做一件事；**HIS 的 subtree 同步单独提交**，绝不与其他改动混在一个提交里。
- 需要大改动时在提交正文里写清"改了什么、为什么、如何验证"（命令与结果）。
- 提交信息与文件内容里都不出现凭据、本机路径或患者数据。

## 3. 版本号：三层独立 + 组合 tag

| 层 | 位置 | 由谁维护 | 规则 |
|---|---|---|---|
| RAG 包 | `rag/__init__.py` 的 `__version__` | RAG 线 | 语义化版本；包契约字段变化时同步更新 `rag/contracts/agent_contract.json` 的 `contract_version` |
| HIS | `his/backend/pom.xml` 的 `<version>` | HIS 维护者 | 同步时原样保留，本仓库不改 |
| HIS × RAG 接口契约 | `contracts/his_rag.md` 的 `contract_version` | 集成区（`main`） | 字段增删或语义变化必须 +1，并在提交信息里写明 |
| **组合版本** | **git tag `medassist-<x.y.z>`** | `main` | 打在"各组件能一起跑通"的 main 提交上 |

组合 tag 的说明里写清三件套，便于复现：

```text
medassist-0.1.0
rag 6.6.2 / his 1.0.0@<short-sha> / his_rag_contract 0.2.0
```

发布流程：契约冻结 → 全量测试通过 → 打 tag → 更新根 `README.md` 的"组件与接口版本"表。

## 4. 同步 HIS（subtree）

```bash
git checkout main
git fetch origin --prune
git subtree pull --prefix=his origin/his -m "chore(his): 同步 his 线至 <short-sha>"
```

首次挂载（已完成，2026-10-11）：

```bash
git subtree add --prefix=his origin/his -m "chore(his): 以 subtree 挂载 HIS 线（origin/his@<short-sha>）"
```

## 5. 提交前检查

在仓库根目录运行（离线，不联网、不加载模型、只用合成数据）：

```bash
python3 -m unittest discover -s rag/tests -p 'test_*.py'   # 包内测试（当前 168 例）
python3 -m unittest discover -s tests -t . -p 'test_*.py'  # 跨组件契约测试（当前 22 例；见下）
python3 -m compileall -q rag contracts
```

- 改动 `rag/`：必须跑包内测试；若涉及契约或映射（`rag/contracts/`、`contracts/`），同时跑跨组件测试。
- 改动 `contracts/` 或 `tests/`：跨组件测试必须通过；映射或字段变化要同步更新 `contracts/examples/` 的黄金样例。
- 改动文档：确认命令、路径、版本值与当前仓库一致（版本值以根 README 表为准）。
- `tests/test_medical_agent_integration.py` 属于 Agent 侧集成测试，需要 medrax 环境（`langchain_core`/`pydantic`/`gradio`）+ Qwen + MiniCheck；**没有该环境时报导入错误属预期**，不是回归——其余跨组件测试无额外依赖。
- 测试数量与结论以**本次运行输出**为准；不要把历史报告里的数字当作当前状态。

## 6. 发布纪律（不可协商）

- **分层**：HIS → Agent → RAG。**RAG 包不连接 HIS**（不得出现 HIS 客户端、HIS 地址或 `/api/patients` 等端点）；HIS 由 Agent 读取后以 `patient_snapshot` 载荷传入（规则与参考实现见 `contracts/his_rag.md`）。
- **只读**：与 HIS 的交互只允许只读接口（`GET`）；任何写 HIS 的代码（落病历、医嘱、处方）不得进入本仓库。
- **数据**：示例、测试与演示一律使用合成数据并明确标注；不写入真实患者数据或受限文献内容。
- **凭据**：不把凭据写进任何文件（含示例、测试、文档与提交信息）。`his/` 内的本地 demo 配置值（数据库口令等）不要复制到集成层、示例或测试里。
- **构建产物**：不提交 `target/`、`node_modules/`、`dist/`、`__pycache__/` 等产物。
- **历史**：不 force-push `main`/`rag`；确需改写历史时先建立备份（如 `refs/original/`）并事先说明影响。
- **发布线脱敏**：本仓库文件不出现开发工作区的内部路径、本机信息（虚拟环境路径、本机耗时）或任何密钥。
