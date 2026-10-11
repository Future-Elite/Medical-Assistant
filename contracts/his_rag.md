---
contract_version: 0.1.0
status: 已实现并测试（2026-10-11；适配器 `rag/his_http.py`，样例与测试见第 6 节）
---

# HIS × RAG 只读快照契约

HIS 演示系统（`his/`，Spring Boot，默认端口 8080）与 RAG 组件（`rag/`）之间的**只读**患者快照约定。RAG 侧的消费方是 `rag/his.py` 定义的 `HISAdapter.get_snapshot(patient_ref, as_of) -> PatientSnapshot`，实现见 `rag/his_http.py`（`HisHttpAdapter`）；本文件规定用到 HIS 的哪些接口、字段如何进入快照。

## 1. 范围与不变式

- **只读**：只使用 HIS 的 `GET` 接口；任何写操作（病历、医嘱、处方）不在契约内，也不得出现在调用方代码里。适配器的传输层只接收 URL 与超时，构造不出写请求。
- **数据**：只接合成数据（`his/backend/his-generator` 生成）；`patient_ref` 就是合成患者的整数 id，**不做任何患者标识映射**。
- **标识剔除**：`name`、`idCard`、`phone`、`address`、`patientId` 在映射时丢弃，绝不进入快照或证据包；患者记录只保留 `gender`、`birthDate`（`createdAt` 作为该事实的时间边界，不作为字段）。外部连接器收到的只有审核后的检索词。
- **不改写**：临床字段原样进入 `fields`，不翻译、不改写、不推断、不填默认值；缺失就是缺失。
- **单一时间实现**：`as_of` 过滤只在 RAG 侧（`rag/his.py` 的 `filter_snapshot`）实现，HIS 接口不带时间窗参数。
- **可审计的缺口**：没有实体载荷、没有 `type`、载荷不是对象、时间线里混入非对象元素等情况都会保留事实或记入快照 `warnings`，不静默丢弃。

## 2. 使用的只读接口

| 用途 | 接口 | 返回 |
|---|---|---|
| 患者存在性与基本信息 | `GET /api/patients/{id}` | `Patient` |
| 患者级时间线 | `GET /api/patients/{id}/timeline` | `TimelineVO{patientId, events:[{type, time, data}]}`，按时间排序 |
| 住院级时间线 | `GET /api/admissions/{id}/timeline` | 同上；含只在住院期间出现的事件（见第 3 节事件来源） |

患者级时间线**不含** `ORDER` / `VITAL_SIGN` / 住院检验（HIS 侧按 `admissionId` 划分）；适配器默认会为患者级时间线里出现的每个入院取一次住院级时间线并按时间合并（`include_admission_timelines=False` 可关闭）。`GET /api/visits`、`GET /api/vital-signs`、`GET /api/orders` 等列表接口**不在本契约内**（两条时间线已覆盖；列表接口语义待需要时另行约定）。

响应统一为 `{code, message, data}` 包裹，`code == 0` 为成功；调用方必须检查 `code`，不能只看 HTTP 状态。

## 3. 映射规则：`TimelineEvent` → `PatientFact`

每个 `TimelineEvent` 映射为一条 `PatientFact`（字段定义见 `rag/his.py`）：

| `PatientFact` 字段 | 来源 | 说明 |
|---|---|---|
| `collection` | `type` 小写 | 例：`VISIT`→`visit`、`LAB_TEST`→`lab_test` |
| `event_type` | `type` 原值 | 保留 HIS 原始类型，便于回溯；缺 `type` 时记 `UNKNOWN` 并告警 |
| `record_path` | 生成 | `his:timeline:<type>:<序号>`（按类型自增，序号由合并排序后的顺序决定） |
| `field_path` | 生成 | 默认与 `record_path` 相同 |
| `fields` | `data` 的键值 | 实体字段原样进入（剔除第 1 节列出的标识字段） |
| `time` | `time` | 事件时间（ISO 8601，本地时区 `Asia/Shanghai`） |
| `start` / `end` | 见下 | 仅区间型事件使用 |
| `source_type` | 固定 `patient_record` | 快照来源类型 |

另有一条患者基本信息事实：`collection="patient"`、`event_type="PATIENT"`、`record_path="his:patient"`、`fields={gender, birthDate}`（其余字段为标识或簿记，见第 1 节）。

区间规则（`start` / `end`）：

- `ORDER`：`start = startTime`（= 事件时间），`end = stopTime`；**`end` 为空表示医嘱进行中**。
- `ADMISSION`：`start = admissionTime`，`end = dischargeTime`（为空表示在院）；`DISCHARGE` 同时作为一条时间点事实保留，用于标记出院事件本身。
- 其余类型：时间点事实，`start`/`end` 为空。
- 无时间字段的记录：保留为事实并记 `warning`，不参与时间窗口。

事件来源与字段清单（以 `his/backend/his-common` 实体类为准）：

| 事件 `type` | 来源 | `fields` 键（不含已剔除标识） |
|---|---|---|
| `VISIT` | 患者时间线 | `id`、`visitNo`、`department`、`doctor`、`visitTime`、`status`、`remark` |
| `DIAGNOSIS` | 患者时间线 | `id`、`visitId`、`icdCode`、`diagnosisName`、`doctor`、`diagnosedAt` |
| `PRESCRIPTION` | 患者时间线 | `id`、`visitId`、`doctor`、`prescribedAt`、`totalAmount`、`items[]`（明细原样嵌套，不展开） |
| `LAB_TEST` | 患者时间线（`admissionId` 为空）/ 住院时间线（`admissionId` 非空） | `id`、`visitId`、`admissionId`、`itemCode`、`itemName`、`resultValue`、`unit`、`referenceRange`、`isAbnormal`、`testedAt` |
| `ADMISSION` / `DISCHARGE` | 患者时间线（同一入院对象各出一条） | `id`、`admissionNo`、`department`、`doctor`、`bedNo`、`diagnosis`、`admissionRoute`、`dischargeDisposition`、`admissionDiagnosis`、`dischargeDiagnosis`、`admissionTime`、`dischargeTime`、`deposit`、`totalCost`、`status` |
| `CHARGE` | 患者时间线 | `id`、`visitId`、`chargeNo`、`itemType`、`itemName`、`amount`、`chargedAt` |
| `DISPENSE` | 患者时间线 | `id`、`visitId`、`prescriptionId`、`drugName`、`quantity`、`dispensedAt`、`pharmacist` |
| `ORDER` | 住院时间线 | `id`、`admissionId`、`orderType`、`itemName`、`dosage`、`frequency`、`startTime`、`stopTime`、`status`、`doctor` |
| `VITAL_SIGN` | 住院时间线 | `id`、`admissionId`、`temperature`、`pulse`、`respiration`、`systolicBp`、`diastolicBp`、`recordedAt`、`nurse` |

合并与排序：患者级与住院级事件先按 `time` 稳定排序再逐条映射，`record_path` 序号即最终顺序，保证同一份响应永远得到同一份快照。

## 4. 存在性与缺失语义

- 患者不存在（`data` 为空/`null`）：适配器抛 `ValueError("unknown patient_ref: <id>")`，**不构造空快照**。
- 业务错误码（`code != 0`）或缺少 `{code,message,data}` 包裹：抛 `HISHttpError`（`stage` = `patient`/`timeline`/`admission_timeline`）。
- 网络/HTTP 失败：`HISHttpError`（含 `status_code`，5xx 与连接失败标 `retryable=True`）。
- 时间线为空：返回合法但无事实的快照（只有患者事实），RAG 据此只做证据检索，不把"无记录"解释为否定事实。
- 住院时间线返回的患者与本快照患者不一致：忽略该住院事件并记 `warning`（防御性校验）。
- 字段缺失：以 `null`/空值原样进入 `fields`；不推断、不填默认值。

## 5. 变更纪律

- 顶部 `contract_version` 独立演进：字段增删、类型变化或语义变化（含映射规则）必须 +1，并在提交信息（`docs(contracts):`）中写明。
- 与 RAG 包内契约保持同步：快照字段 schema 与 RAG 工具契约在 `rag/contracts/agent_contract.json`（`contract_version` 随 RAG 包 `__version__` 走）；本文件描述 HIS 侧映射，不重复定义 schema。
- 新增其他组件对（如 agent → his、agent → rag）时，在本目录**新增**文件，不重写既有文件（保证各条线合并到 `main` 时不冲突）。
- 样例是契约的一部分：`examples/` 里的响应样例与黄金快照（`his_rag_snapshot_expected.json`）必须与映射实现同步更新。

## 6. 样例与测试

| 路径 | 内容 |
|---|---|
| `examples/his_rag_patient_2001.json` | `GET /api/patients/2001` 的合成响应（刻意含标识字段，用于验证剔除） |
| `examples/his_rag_patient_2001_timeline.json` | 患者级时间线合成响应（含一条 2026 年未来记录） |
| `examples/his_rag_admission_7101_timeline.json` | 住院级时间线合成响应（`ORDER`/`VITAL_SIGN`/住院 `LAB_TEST`） |
| `examples/his_rag_snapshot_expected.json` | 上述响应在 `as_of=2025-12-31` 下的黄金快照投影（逐字比对） |

```bash
# 跨组件契约测试（离线，用 examples 里的样例；在仓库根目录运行）
python3 -m unittest discover -s tests -t . -p 'test_*.py'
# 适配器单测（含本机 HTTP 实测）
python3 -m unittest rag.tests.test_his_http
```
