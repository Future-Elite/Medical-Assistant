---
contract_version: 0.2.0
status: 已实现并测试（2026-10-11；Agent 侧参考实现 `contracts/agent_his_adapter.py`，样例与测试见第 6 节）
---

# HIS × RAG 只读快照契约

**分层：HIS → Agent → RAG。** Agent（不是 RAG）通过只读接口读取 HIS，把结构化数据整理成 `patient_snapshot` 载荷传入 RAG；**RAG 只接收快照、不连接 HIS**，包内没有 HIS 客户端、HIS 地址或 HIS 端点（由跨组件测试守护）。

本文件规定：用到 HIS 的哪些只读接口、HIS 响应如何映射为快照载荷、载荷如何被 RAG 接收。

- Agent 侧参考实现：`contracts/agent_his_adapter.py`（读取 + 映射，**不属于 rag 包**）
- RAG 侧接收端：`rag/his.py` 的 `snapshot_from_dict()` → `PatientSnapshot`（dict 或对象皆可）

## 1. 范围与不变式

- **只读**：Agent 只使用 HIS 的 `GET` 接口；任何写操作（病历、医嘱、处方）不在契约内。参考实现的传输层只接收 URL 与超时，构造不出写请求。
- **分层**：RAG 不持有 HIS 地址、不发起 HIS 请求；HIS 的连接、错误处理与字段映射属于 Agent 侧。
- **数据**：只接合成数据（`his/backend/his-generator` 生成）；`patient_ref` 就是合成患者的整数 id，**不做任何患者标识映射**。
- **标识剔除**（Agent 侧映射责任）：`name`、`idCard`、`phone`、`address`、`patientId` 丢弃，绝不进入载荷或证据包；患者事实只保留 `gender`、`birthDate`（`createdAt` 作为该事实的时间边界，不作为字段）。
- **不改写**：临床字段原样进入 `fields`，不翻译、不改写、不推断、不填默认值；缺失就是缺失。
- **单一时间实现**：Agent 侧载荷携带**全部**事实（含未来记录）；`as_of` 可见性只由 RAG 侧 `rag.his.filter_snapshot()` 决定（未来事实计入 `suppressed_future`）。
- **可审计的缺口**：没有实体载荷、没有 `type`、载荷不是对象、时间线混入非对象元素等情况保留事实或记入载荷 `warnings`，不静默丢弃。

## 2. Agent 读取的只读接口

| 用途 | 接口 | 返回 |
|---|---|---|
| 患者存在性与基本信息 | `GET /api/patients/{id}` | `Patient` |
| 患者级时间线 | `GET /api/patients/{id}/timeline` | `TimelineVO{patientId, events:[{type, time, data}]}`，按时间排序 |
| 住院级时间线 | `GET /api/admissions/{id}/timeline` | 同上；含只在住院期间出现的事件（见 §3 事件来源） |

患者级时间线**不含** `ORDER` / `VITAL_SIGN` / 住院检验（HIS 侧按 `admissionId` 划分）；参考实现会为患者级时间线里出现的每个入院取一次住院级时间线并按时间合并（可关闭）。`GET /api/visits`、`GET /api/vital-signs`、`GET /api/orders` 等列表接口**不在本契约内**。

响应统一为 `{code, message, data}` 包裹，`code == 0` 为成功；Agent 必须检查 `code`，不能只看 HTTP 状态。

## 3. 映射规则：`TimelineEvent` → 快照事实

每个 `TimelineEvent` 映射为一条事实（`PatientFact`，字段定义见 `rag/his.py`）：

| 载荷字段 | 来源 | 说明 |
|---|---|---|
| `collection` | `type` 小写 | 例：`VISIT`→`visit`、`LAB_TEST`→`lab_test` |
| `event_type` | `type` 原值 | 保留 HIS 原始类型；缺 `type` 时记 `UNKNOWN` 并告警 |
| `record_path` | 生成 | `his:timeline:<type>:<序号>`（按类型自增，序号由合并排序后的顺序决定） |
| `field_path` | 生成 | 默认与 `record_path` 相同 |
| `fields` | `data` 的键值 | 实体字段原样进入（剔除 §1 的标识字段） |
| `time` | `time` | 事件时间（ISO 8601，本地时区 `Asia/Shanghai`） |
| `start` / `end` | 见下 | 仅区间型事件使用 |

另有一条患者基本信息事实：`collection="patient"`、`event_type="PATIENT"`、`record_path="his:patient"`、`fields={gender, birthDate}`。

区间规则：`ORDER` 用 `start=startTime`、`end=stopTime`（**`end` 为空表示医嘱进行中**）；`ADMISSION` 用 `start=admissionTime`、`end=dischargeTime`（为空表示在院），`DISCHARGE` 另作为时间点事实保留；其余类型为时间点事实；无时间字段的记录保留并记 `warning`。

事件来源与字段清单（以 `his/backend/his-common` 实体类为准）：

| 事件 `type` | 来源 | `fields` 键（不含已剔除标识） |
|---|---|---|
| `VISIT` | 患者时间线 | `id`、`visitNo`、`department`、`doctor`、`visitTime`、`status`、`remark` |
| `DIAGNOSIS` | 患者时间线 | `id`、`visitId`、`icdCode`、`diagnosisName`、`doctor`、`diagnosedAt` |
| `PRESCRIPTION` | 患者时间线 | `id`、`visitId`、`doctor`、`prescribedAt`、`totalAmount`、`items[]`（明细原样嵌套） |
| `LAB_TEST` | 患者时间线（`admissionId` 空）/ 住院时间线（`admissionId` 非空） | `id`、`visitId`、`admissionId`、`itemCode`、`itemName`、`resultValue`、`unit`、`referenceRange`、`isAbnormal`、`testedAt` |
| `ADMISSION` / `DISCHARGE` | 患者时间线（同一入院对象各出一条） | `id`、`admissionNo`、`department`、`doctor`、`bedNo`、`diagnosis`、`admissionRoute`、`dischargeDisposition`、`admissionDiagnosis`、`dischargeDiagnosis`、`admissionTime`、`dischargeTime`、`deposit`、`totalCost`、`status` |
| `CHARGE` | 患者时间线 | `id`、`visitId`、`chargeNo`、`itemType`、`itemName`、`amount`、`chargedAt` |
| `DISPENSE` | 患者时间线 | `id`、`visitId`、`prescriptionId`、`drugName`、`quantity`、`dispensedAt`、`pharmacist` |
| `ORDER` | 住院时间线 | `id`、`admissionId`、`orderType`、`itemName`、`dosage`、`frequency`、`startTime`、`stopTime`、`status`、`doctor` |
| `VITAL_SIGN` | 住院时间线 | `id`、`admissionId`、`temperature`、`pulse`、`respiration`、`systolicBp`、`diastolicBp`、`recordedAt`、`nurse` |

载荷形状（交给 RAG 的 `patient_snapshot`）：

```json
{
  "patient_ref": "2001",
  "as_of": "2025-12-31",
  "source": "his_demo",
  "facts": [{"collection": "...", "event_type": "...", "record_path": "...", "field_path": "...", "fields": {}, "time": null, "start": null, "end": null}],
  "reviewed_external_terms": [],
  "warnings": []
}
```

合并与排序：患者级与住院级事件先按 `time` 稳定排序再逐条映射，`record_path` 序号即最终顺序，保证同一份响应永远得到同一份载荷。`reviewed_external_terms` 不由参考实现推断：只由调用方（人工审核）提供。

## 4. 存在性与错误语义（Agent 侧）

- 患者不存在（`data` 为空/`null`）：抛 `ValueError("unknown patient_ref: <id>")`，**不构造空载荷**。
- 业务错误码（`code != 0`）或缺少 `{code,message,data}` 包裹：抛 `HISClientError`（`stage` = `patient`/`timeline`/`admission_timeline`）。
- 网络/HTTP 失败：`HISClientError`（含 `status_code`，5xx 与连接失败标 `retryable=True`）。
- 时间线为空：合法载荷，只有患者事实；RAG 据此只做证据检索，不把"无记录"解释为否定事实。
- 住院时间线返回的患者与本快照患者不一致：忽略该住院事件并记 `warning`。
- RAG 侧接收：`snapshot_from_dict()` 校验 `patient_ref`/`as_of`（ISO 日期）与事实结构，缺字段直接报错。

## 5. 变更纪律

- 顶部 `contract_version` 独立演进：字段增删、类型变化、映射规则或**层次/载体**变化必须 +1，并在提交信息（`docs(contracts):`）中写明。0.2.0 的变化：明确 HIS → Agent → RAG 分层，HIS 客户端从 rag 包移出为 Agent 侧参考实现。
- 与 RAG 包内契约保持同步：快照字段 schema 与 RAG 工具契约在 `rag/contracts/agent_contract.json`（`contract_version` 随 RAG 包 `__version__` 走）；本文件描述 Agent 侧读取与映射，不重复定义 schema。
- 新增其他组件对（如 agent → rag 的通用约定）时，在本目录**新增**文件，不重写既有文件（保证各条线合并到 `main` 时不冲突）。
- 样例是契约的一部分：`examples/` 的响应样例与载荷黄金样例必须与映射实现同步更新。

## 6. 样例与测试

| 路径 | 内容 |
|---|---|
| `examples/his_rag_patient_2001.json` | `GET /api/patients/2001` 的合成响应（刻意含标识字段，用于验证剔除） |
| `examples/his_rag_patient_2001_timeline.json` | 患者级时间线合成响应（含一条 2026 年未来记录） |
| `examples/his_rag_admission_7101_timeline.json` | 住院级时间线合成响应（`ORDER`/`VITAL_SIGN`/住院 `LAB_TEST`） |
| `examples/his_rag_snapshot_payload_expected.json` | 上述响应映射出的载荷黄金样例（逐字比对；含全部事实，`as_of` 可见性由 RAG 决定） |
| `agent_his_adapter.py` | Agent 侧参考实现：读取三个只读端点 + 映射为载荷（不含时间裁剪） |

```bash
# 跨组件契约测试（离线；在仓库根目录运行）
python3 -m unittest discover -s tests -t . -p 'test_*.py'
```
