# 集成层（integration）

跨系统集成层：只描述"组件之间如何对接"，不放任何一方的内部实现。当前组件是 **HIS × RAG 的只读患者快照链路**：HIS 提供患者数据与工作流，RAG 按只读快照检索外部循证证据并给出引用与审计。

## 目录约定

| 路径 | 内容 |
|---|---|
| `his_rag_contract.md` | HIS × RAG 接口契约：只读接口清单、快照字段映射表、时间与 `as_of` 语义、`contract_version` |
| `examples/` | 合成样例（患者快照 JSON 等）；不得含真实患者数据 |
| `tests/` | 端到端映射测试；离线、不依赖 MySQL 与网络（使用 stub 或录制响应） |

## 接口纪律

- **只读**：只调用 HIS 的只读接口（`GET`）；任何写操作（病历、医嘱、处方）不得进入。
- **最小信息**：流向 RAG 的只有患者快照（患者引用 + 带时间边界的事实列表）；送往外部知识源（PubMed 等）的只有审核后的检索词。
- **契约版本**：`his_rag_contract.md` 顶部维护 `contract_version`；字段增删或语义变化必须 +1，并在提交信息里写明。
- **合成数据**：样例与测试数据一律合成，并在文件中标注。
- **不复制内部实现**：RAG 侧的适配器实现放在 `rag/` 包内（例如 `rag/his_http.py`），本目录只放契约、样例与测试。

## 已挂载组件

`his/` 由 `git subtree` 从 `origin/his` 挂载，内容由 HIS 维护者负责，本仓库不直接修改其文件：

```bash
git subtree pull --prefix=his origin/his -m "chore(his): 同步 his 线至 <short-sha>"
```

HIS 侧技术栈与只读接口速览（以 `his/` 实际代码为准）：

| 项 | 事实 |
|---|---|
| 后端 | Maven 多模块（`his-common` / `his-generator` / `his-server`），Spring Boot + MyBatis-Plus，默认端口 8080 |
| 前端 | Vue 3 + Vite，开发时代理 `/api` 到后端 |
| 数据 | 由 `his-generator` 生成**合成数据**（可清空重生成） |
| 只读接口 | `GET /api/patients`、`GET /api/patients/{id}`、`GET /api/patients/{id}/timeline`、`GET /api/visits`、`GET /api/vital-signs`、`GET /api/orders` |
| 仅写接口 | 诊断、检验、处方、收费、发药、入院等目前只有 `POST`/`PUT` |

## 当前状态（2026-10-11）

- ✅ 目录与规范就位；`his/` 已挂载（`origin/his@0e6a2d1`）。
- ⏳ `his_rag_contract.md` 与 `examples/`、`tests/` 待编写：先离线定义映射（`timeline event.type` → RAG 的 `PatientFact`），再实现 `rag/his_http.py` 适配器并做本地联调。
- 版本规则、分支与提交规范见 [`../CONTRIBUTING.md`](../CONTRIBUTING.md)。
