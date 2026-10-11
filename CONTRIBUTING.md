# 参与开发：分支、提交与版本规范

> 本仓库是"医生助手"原型的多组件仓库（`rag/` + `his/` + `integration/`）。本文件规定分支模型、提交信息、版本号与发布流程，所有协作者提交前请遵循。
> 跨系统集成的接口约定见 [`integration/README.md`](integration/README.md)；RAG 包本身的调用方式见 [`rag/README.md`](rag/README.md)。

## 1. 组件与分支

| 目录 | 组件 | 维护线 | 说明 |
|---|---|---|---|
| `rag/` | RAG 证据检索（Python 包 `rag`） | 分支 `rag` | 正式发布线；版本在 `rag/__init__.py` 的 `__version__` |
| `his/` | HIS 演示系统（Spring Boot + Vue，合成数据） | 分支 `his` | 由 HIS 维护者负责；本仓库用 subtree 挂载，只做同步 |
| `integration/` | 跨系统集成层（契约、样例、端到端测试） | `main` | 只描述接口，不放任何一方的内部实现 |
| `agent/` | Medical Agent | 分支 `agent` | 由 Agent 维护者负责，不在本项目维护范围内 |

分支规则：

- **`main`：集成/发布线。** 永远代表"各组件能一起跑通"的组合，只接受合并与集成层改动；**不直接在 `main` 上修改 `rag/`**。
- **`rag`：RAG 组件线。** `rag/` 内的所有改动先落这条线，再合并进 `main`。
- **`his`：HIS 组件线（他人维护）。** **不要直接修改 `his/` 下的文件**——它们会随 subtree 同步被覆盖或产生冲突；需要 HIS 侧改动时先与 HIS 维护者沟通，走 `his` 分支。
- **集成工作用短分支。** 从 `main` 开 `feat/…`、`fix/…`，完成并测试后合并回 `main`。
- `agent` 线不在本项目范围内：不合并、不评论、不改动。

## 2. 提交信息（Conventional Commits）

格式：`<type>(<scope>): <描述>`；`scope` 用目录名或模块名；描述里保留版本号（如 `v6.6.0`）。

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
feat(rag): 升级到 v6.6.0 —— HIS 只读快照适配器 his_http.py
feat(integration): HIS×RAG 快照契约 0.1.0 与合成样例
test(integration): 快照映射端到端（离线 stub，不依赖 MySQL）
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
| HIS × RAG 接口契约 | `integration/his_rag_contract.md` 的 `contract_version` | 集成层 | 字段增删或语义变化必须 +1，并在提交信息里写明 |
| **组合版本** | **git tag `medassist-<x.y.z>`** | `main` | 打在"各组件能一起跑通"的 main 提交上 |

组合 tag 的说明里写清三件套，便于复现：

```text
medassist-0.1.0
rag 6.6.0 / his 1.0.0@<short-sha> / his_rag_contract 0.1.0
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

## 5. 发布纪律（不可协商）

- **只读**：集成只允许调用 HIS 的只读接口（`GET`）；任何写 HIS 的代码（落病历、医嘱、处方）不得进入本仓库。
- **数据**：示例、测试与演示一律使用合成数据并明确标注；不写入真实患者数据或受限文献内容。
- **凭据**：不把凭据写进任何文件（含示例、测试、文档与提交信息）。`his/` 内的本地 demo 配置值（数据库口令等）不要复制到集成层、示例或测试里。
- **构建产物**：不提交 `target/`、`node_modules/`、`dist/`、`__pycache__/` 等产物。
- **历史**：不 force-push `main`/`rag`；确需改写历史时先建立备份（如 `refs/original/`）并事先说明影响。
- **发布线脱敏**：本仓库文件不出现开发工作区的内部路径、本机信息（虚拟环境路径、本机耗时）或任何密钥。
