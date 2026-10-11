---
contract_version: 0.1.0
status: draft（2026-10-11；映射细目待随适配器实现补齐）
---

# HIS × RAG 只读快照契约

HIS 演示系统（`his/`，Spring Boot，默认端口 8080）与 RAG 组件（`rag/`）之间的**只读**患者快照约定。RAG 侧的消费方是 `rag/his.py` 定义的 `HISAdapter.get_snapshot(patient_ref, as_of) -> PatientSnapshot`；本文件规定用到 HIS 的哪些接口、字段如何进入快照。

## 1. 范围与不变式

- **只读**：只使用 HIS 的 `GET` 接口；任何写操作（病历、医嘱、处方）不在契约内，也不得出现在调用方代码里。
- **数据**：只接合成数据（`his/backend/his-generator` 生成）；`patient_ref` 就是合成患者的整数 id，**不做任何患者标识映射**，不引入真实姓名、住院号、身份证号等。
- **最小信息**：快照只在 RAG 内部作为检索上下文；送往外部知识源（PubMed 等）的只有审核后的检索词。
- **不推断**：字段缺失按缺失处理，不填默认值；"无记录"不等于否定事实。

## 2. 使用的只读接口

| 用途 | 接口 | 返回 |
|---|---|---|
| 患者存在性与基本信息 | `GET /api/patients/{id}` | `Patient` |
| 患者事实主来源 | `GET /api/patients/{id}/timeline` | `TimelineVO{patientId, events:[{type, time, data}]}`，按时间排序 |
| 备用（v0.1.0 暂不纳入） | `GET /api/visits`、`GET /api/vital-signs`、`GET /api/orders` | 列表查询 |

响应统一为 `{code, message, data}` 包裹，`code == 0` 为成功；调用方必须检查 `code`，不能只看 HTTP 状态。

## 3. 映射规则：`TimelineEvent` → `PatientFact`

每个 `TimelineEvent` 映射为一条 `PatientFact`（字段定义见 `rag/his.py`）：

| `PatientFact` 字段 | 来源 | 说明 |
|---|---|---|
| `collection` | `type` 小写 | 例：`VISIT`→`visit`、`LAB_TEST`→`lab_test` |
| `event_type` | `type` 原值 | 保留 HIS 原始类型，便于回溯 |
| `record_path` | 生成 | `his:timeline:<type>:<序号>`，用于定位与去重 |
| `field_path` | 生成 | 默认与 `record_path` 相同 |
| `fields` | `data` 的键值 | 实体字段原样进入，不翻译、不改写 |
| `time` | `time` | 事件时间（ISO 8601，本地时区 `Asia/Shanghai`） |
| `start` / `end` | 由时间字段派生 | 仅区间型事件使用，见下 |
| `source_type` | 固定 `patient_record` | 快照来源类型 |

`type` 取值（以 `TimelineServiceImpl` 实际输出为准，已核实 10 种）：`VISIT`、`DIAGNOSIS`、`PRESCRIPTION`、`LAB_TEST`、`ADMISSION`、`DISCHARGE`、`CHARGE`、`DISPENSE`、`ORDER`、`VITAL_SIGN`。

时间与区间规则：

- `ADMISSION` / `DISCHARGE` 由同一条入院记录的入院、出院时间各产生一条事件；快照中作为两条事实保留，`admission_id` 保留在 `fields` 供关联。
- `ORDER` 以 `start` / `end` 承载医嘱起止；无终止时间表示进行中。
- 无任何时间字段的记录：保留为事实并记 `warning`，不参与时间窗口（由 `rag/his.py` 的 `filter_snapshot` 统一处理）。
- **`as_of` 过滤在 RAG 侧完成**，HIS 接口不提供时间窗参数——保证时间边界语义只有一个实现。

## 4. 待补齐（随适配器实现逐项登记）

- 各 `type` 的 `data` 字段清单（以 `his/backend/his-common` 实体类为准）；
- 就诊 / 入院与诊断、检验、处方的关联键（`visit_id` / `admission_id`）在 `fields` 中的保留方式；
- 患者基本信息（性别、出生日期等）是否作为事实注入，以及与 `patient_terms` 的去重方式；
- 映射的可核验样例（按 `contracts/examples/` 约定，用**合成**响应录制，见 `README.md`）。

## 5. 变更纪律

- 顶部 `contract_version` 独立演进：字段增删、类型变化或语义变化必须 +1，并在提交信息里写明。
- 与 RAG 包内契约保持同步：快照字段 schema 与工具契约在 `rag/contracts/agent_contract.json`（随 RAG 包 `__version__` 走）；本文件描述 HIS 侧映射，不重复定义 schema。
- 新增其他组件对（如 agent → his、agent → rag）时，在本目录**新增**文件，不重写既有文件（保证各条线合并到 `main` 时不冲突）。
