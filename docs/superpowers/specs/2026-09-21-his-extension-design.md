# 模拟 HIS 系统扩展（二期）—— 虚拟患者时序数据生成与测试平台 · 设计文档

- 日期：2026-09-21
- 状态：待评审
- 题目：基于模拟 HIS 系统的虚拟患者时序数据生成与测试平台设计与实现
- 前置文档：[2026-09-19 设计文档](2026-09-19-his-simulation-design.md)、[2026-09-20 录入工作流设计](2026-09-20-entry-workflow-design.md)

## 1. 背景与目标

一期已交付「门诊闭环」：患者 → 挂号(visit) → 诊断(diagnosis) → 处方(prescription+明细) → 检验(lab_test)，
配药品字典、诊断↔用药规则表(rule_config)、医生表，以及一个批量数据生成器与患者时间轴/数据看板。

对照生产 HIS 的完整模块清单，一期系统在「时序数据」维度偏薄——数据是单次门诊的扁平事件链，
缺少**住院纵向数据**；同时缺少「测试平台」所需的**规则校验能力**。二期在不引入纯运营/平台类模块的前提下，
补三块必须能力，全部服务于课题内核「虚拟患者**时序数据**生成与**测试**平台」：

1. **丰富时序数据**：引入住院域（住院记录 + 医嘱 + 生命体征），把时间序列从「单次门诊」升级为「跨天数的纵向数据」。
2. **强化测试与校验**：引入合理用药规则校验 + 临床路径变异检测，让生成的数据可被规则「测试」，产出数据质量校验报告。
3. **铺开门诊业务**：补门诊收费、药房发药，补齐门诊生命周期闭环，并为时间线新增两个时间点事件。

## 2. 范围对照（加什么 / 不加什么）

| 生产模块 | 二期取什么 | 结论 |
|---|---|---|
| 住院管理 | 入院登记 / 出院结算 | ✅ 加 |
| 住院医生工作站 | 医嘱管理 + 诊断管理 | ✅ 加（收敛为 `medical_order`） |
| 住院护士工作站 | 三测单 + 医嘱执行 | ✅ 加（收敛为 `vital_sign`） |
| 合理用药 | 用药安全规则库 + 处方点评/安全警示 | ✅ 加（`rational_rule` + 校验引擎） |
| 临床路径 | 路径制定 + 变异管理 | ✅ 加（`clinical_pathway` + 变异检测） |
| 门诊收费 | 门诊收费 + 结算 | ✅ 加（`charge`） |
| 药房管理 | 门诊发药 | ✅ 加（`dispense`） |
| EMPI 主索引 / IC卡 / 健康卡 / 身份证识别 / 条码打印 | — | ❌ 身份介质类，与时序无关 |
| 门诊预约（预约池/排班/分诊/爽约） | — | ❌ 对时序加成小 |
| 药库管理 / 静脉配置中心 / 治疗室 | — | ❌ 运营后勤类 |
| 设备管理 / 总务管理 | — | ❌ 后勤类 |
| 发票管理 / 医保接口 / 院感系统 | — | ❌ 平台/政策类，与内核无关 |
| 单点登录 / 系统管理 / 基础数据管理 | — | ❌ 平台类（且违背「无账号密码」约束） |

## 3. 数据模型（7 张新表 + 1 处既有表增列）

统一约定沿用一期：时间字段 `DATETIME`、主键 `BIGINT AUTO_INCREMENT`、`patient_id`/`visit_id`/`admission_id`
与时间字段建组合索引以支撑时间线与批量写入。

### 3.1 `admission` 住院记录
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| patient_id | BIGINT NOT NULL | 关联患者 |
| admission_no | VARCHAR(32) NOT NULL | 住院号 |
| department | VARCHAR(50) | 科室 |
| doctor | VARCHAR(50) | 医生 |
| bed_no | VARCHAR(20) | 床位号 |
| diagnosis | VARCHAR(100) | 入院诊断 |
| admission_time | DATETIME NOT NULL | 入院时间 |
| discharge_time | DATETIME NULL | 出院时间（NULL=在院） |
| deposit | DECIMAL(10,2) DEFAULT 0 | 押金 |
| total_cost | DECIMAL(10,2) DEFAULT 0 | 总费用 |
| status | TINYINT DEFAULT 0 | 0 在院 / 1 出院 |
| | KEY (patient_id, admission_time) | |

### 3.2 `medical_order` 医嘱
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| patient_id | BIGINT NOT NULL | |
| admission_id | BIGINT NOT NULL | 关联住院 |
| order_type | TINYINT DEFAULT 0 | 0 长期 / 1 临时 / 2 检验 / 3 护理 |
| item_name | VARCHAR(100) NOT NULL | 医嘱名称 |
| dosage | VARCHAR(50) | 用量 |
| frequency | VARCHAR(50) | 频次 |
| start_time | DATETIME NOT NULL | 开始时间 |
| stop_time | DATETIME NULL | 停止时间（长期医嘱用，NULL=执行中） |
| status | TINYINT DEFAULT 0 | 0 执行中 / 1 已停 / 2 已执行 |
| doctor | VARCHAR(50) | |
| | KEY (admission_id, start_time), KEY (patient_id) | |

### 3.3 `vital_sign` 生命体征（三测单）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| patient_id | BIGINT NOT NULL | |
| admission_id | BIGINT NOT NULL | |
| temperature | DECIMAL(4,1) | 体温 ℃ |
| pulse | INT | 脉搏 次/分 |
| respiration | INT | 呼吸 次/分 |
| systolic_bp | INT | 收缩压 mmHg |
| diastolic_bp | INT | 舒张压 mmHg |
| recorded_at | DATETIME NOT NULL | 测量时间 |
| nurse | VARCHAR(50) | 护士 |
| | KEY (admission_id, recorded_at), KEY (patient_id) | |

### 3.4 `charge` 收费
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| patient_id | BIGINT NOT NULL | |
| visit_id | BIGINT NOT NULL | 关联就诊 |
| charge_no | VARCHAR(32) NOT NULL | 收费单号 |
| item_type | TINYINT DEFAULT 0 | 0 挂号费 / 1 药费 / 2 检验费 |
| item_name | VARCHAR(100) | 项目名 |
| amount | DECIMAL(10,2) NOT NULL DEFAULT 0 | 金额 |
| charged_at | DATETIME NOT NULL | 收费时间 |
| | KEY (visit_id), KEY (patient_id, charged_at) | |

### 3.5 `dispense` 发药
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| patient_id | BIGINT NOT NULL | |
| visit_id | BIGINT NOT NULL | |
| prescription_id | BIGINT NOT NULL | 关联处方 |
| drug_name | VARCHAR(100) | 药品名 |
| quantity | DECIMAL(10,2) | 发药数量 |
| dispensed_at | DATETIME NOT NULL | 发药时间 |
| pharmacist | VARCHAR(50) | 药师 |
| | KEY (prescription_id), KEY (visit_id) | |

### 3.6 `rational_rule` 合理用药规则
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| rule_type | TINYINT DEFAULT 1 | 1 相互作用 / 2 剂量上限（适应症校验复用 rule_config） |
| diagnosis_code | VARCHAR(20) | 适应症用（诊断编码） |
| drug_a_id | BIGINT | 相互作用用（药品 A） |
| drug_b_id | BIGINT | 相互作用用（药品 B） |
| drug_id | BIGINT | 剂量上限用（药品） |
| max_daily_dose | DECIMAL(10,2) | 剂量上限（每日最大量） |
| warning_level | TINYINT DEFAULT 1 | 0 提示 / 1 警告 / 2 严重 |
| message | VARCHAR(255) | 警示文案 |
| enabled | TINYINT DEFAULT 1 | |
| | KEY (rule_type) | |

> `rule_config`（一期）仍负责「生成器：诊断→该开什么药」；`rational_rule` 负责「校验器：处方是否合理」。
> 两者角色独立，校验器能发现生成器（故意）埋下的违规，这正是「测试平台」的演示点。

### 3.7 `clinical_pathway` 临床路径
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| diagnosis_code | VARCHAR(20) | 诊断编码 |
| diagnosis_name | VARCHAR(100) NOT NULL | 诊断名 |
| steps | VARCHAR(2000) | JSON 有序步骤数组 |
| enabled | TINYINT DEFAULT 1 | |

`steps` JSON 结构（每步：第 N 天 → 动作 → 项目 → 期望时间窗）：
```json
[
  { "day": 0, "action": "LAB",  "item": "血常规",   "window": 24 },
  { "day": 0, "action": "MED",  "item": "抗生素",   "window": 48 },
  { "day": 3, "action": "LAB",  "item": "复查血常规", "window": 24 },
  { "day": 5, "action": "DISCHARGE" }
]
```

### 3.8 `lab_test` 增列（既有表，非新表）

为把住院期间的检验结果挂到对应住院下，既有 `lab_test` 表增列 `admission_id BIGINT NULL`（门诊检验为 NULL），
并加索引 `KEY idx_lab_admission (admission_id)`。

## 4. 时间线事件扩展

一期事件类型：`VISIT` / `DIAGNOSIS` / `PRESCRIPTION` / `LAB_TEST`。

**患者时间轴**（`GET /api/patients/{id}/timeline`）新增 4 类顶层事件，与一期事件按 `time` 合并、严格升序：

- `ADMISSION`（入院，data 指向住院记录，含 admissionId）
- `DISCHARGE`（出院，data 指向住院记录，含 admissionId）
- `CHARGE`（收费，data 指向收费记录）
- `DISPENSE`（发药，data 指向发药记录）

**住院时间轴**（`GET /api/admissions/{id}/timeline`，新）独立承载一次住院的纵向细节，按 `time` 升序：

- `ORDER`（医嘱开立；长期医嘱渲染为 start_time→stop_time 区间，不另发停止事件）
- `VITAL_SIGN`（生命体征，早中晚多次）
- `LAB_TEST`（住院期间的检验，复用一期 lab_test，经 `admission_id` 关联）

> 设计取舍：`ORDER`/`VITAL_SIGN` **不**铺进患者时间轴顶层（一次住院可能上千条，会淹没骨架）；患者时间轴保留骨架，
> 点击入院事件下钻到住院时间轴看纵向细节。此分层既保住查询性能，也更贴近真实病历结构。

## 5. 生成器扩展（his-generator）

沿用一期 `clear-before-run` 策略：清空 `patient/visit/diagnosis/prescription/prescription_item/lab_test`，
**并新增清空** `admission/medical_order/vital_sign/charge/dispense`；保留 `drug/rule_config/doctor`，**并新增保留**
`rational_rule/clinical_pathway`（种子字典类）。

### 5.1 门诊补全（收费 + 发药）

每次就诊在处方/检验之后追加，顺序固定为：
`挂号(visit) < 诊断(diagnosis) < 处方(prescription) < 检验(lab_test) < 收费(charge) < 发药(dispense)`，时间严格递增。

- 收费：挂号费固定 `10.00`；药费 = 该处方 `total_amount`；检验费 = 每检验项 `15.00`。一个就诊生成 1–3 条 charge。
- 发药：每张处方生成 1 条 dispense（药物名 = 处方明细，数量 = 明细数量），`dispensed_at` 在处方之后。

### 5.2 住院纵向时序

- **住院患者占比** `0.20`（约 20% 患者有住院史，可配置）。
- 每住院患者生成 **1–3 次**住院（`admission`），每次住院 **3–15 天**，科室/医生沿用 80/20 主科室规则与医生↔科室约束。
- **生命体征**：每天 3 次（早 6–8 点、午 12–14 点、晚 18–20 点）。数值围绕正常范围波动，且**带病情趋势**——
  例如发热/术后患者体温「先升后降」、血压/脉搏随住院天数演化，而非纯随机噪声。
- **医嘱**：住院首日开 **1–3 条长期医嘱**（执行至出院）；**临时医嘱**每天 0–2 条、**检验医嘱**每天 0–1 条；
  检验医嘱触发一条关联的 `lab_test`（带 `admission_id`）。
- **出院**：`discharge_time = admission_time + 住院天数`；`total_cost = 押金 + 床位费(按天) + 药费 + 检验费`。
- 同一患者**完整合并时间线仍严格升序**，住院段与门诊就诊段时间不冲突。

### 5.3 故意违规注入（服务测试平台）

为让「测试平台」有事可测，生成器以 **~3% 概率（可配置）** 给部分处方注入**不合理**内容（随机其一）：
诊断↔药品不匹配（`rule_config` 范围外）、药品相互作用（命中 `rational_rule`）、剂量超上限。这些违规会被
5.4 校验引擎捕获并在报告中体现——以此演示「生成 → 校验 → 报告」闭环。

### 5.4 运行方式

沿用 `java -jar his-generator.jar`，打印分表批量写入耗时（新增住院/体征/医嘱/收费/发药各段耗时）。

## 6. 校验引擎（后端 service，供 `/api/validation/run` 调用）

### 6.1 合理用药校验

加载 `rational_rule` + `rule_config`，扫描全库处方（`prescription` + `prescription_item`），逐条判定：
- **适应症**：处方内药品是否匹配该就诊诊断的 `rule_config.drug_ids`。
- **相互作用**：同一处方/同一患者重叠用药是否命中 `rational_rule(rule_type=1)`。
- **剂量上限**：单药品日剂量是否超过 `rational_rule(rule_type=2).max_daily_dose`。

输出：违规明细（patientId、prescriptionId、ruleId、warning_level、message）+ 汇总（总处方数、违规处方数、**合理率**、违规 TopN 药品）。

### 6.2 临床路径变异检测

对每条 `clinical_pathway`，比对命中该诊断的住院患者**实际事件序列**（医嘱 + 检验 + 出院时间）vs 标准步骤：
- **缺项**：路径要求的某步（如第 3 天复查血常规）在该患者住院期间未发生。
- **顺序错**：实际发生顺序与 steps 顺序不一致。
- **时间窗超出**：某步实际发生时间偏离 `day + window` 范围。

输出：变异明细（patientId、admissionId、pathwayId、variance_type、detail）+ 汇总（入径患者数、**路径执行率**、**变异率**）。

### 6.3 校验报告

`POST /api/validation/run` 返回统一报告：
```json
{
  "rationalDrug": { "summary": { ... }, "violations": [ ... ] },
  "pathway":     { "summary": { ... }, "variances": [ ... ] }
}
```
校验结果**不落库**，按需计算，保证「测试平台」可随时对当前数据跑一次校验。

## 7. 后端 API（新增）

统一前缀 `/api`、统一响应 `Result{code,message,data}`（code=0 成功）。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/admissions | 入院登记 |
| PUT | /api/admissions/{id}/discharge | 出院结算 |
| GET | /api/admissions?patientId | 某患者住院列表 |
| GET | /api/admissions/{id}/timeline | 住院时间轴（ORDER/VITAL_SIGN/LAB_TEST 升序） |
| POST | /api/orders | 医嘱开立 |
| PUT | /api/orders/{id}/stop | 停长期医嘱 |
| GET | /api/orders?admissionId | 医嘱列表 |
| POST | /api/vital-signs | 体征录入 |
| GET | /api/vital-signs?admissionId | 体征列表 |
| POST | /api/charges | 收费登记 |
| GET | /api/charges?visitId | 收费明细 |
| POST | /api/dispenses | 发药登记 |
| GET | /api/dispenses?visitId | 发药明细 |
| POST | /api/validation/run | 一键校验（合理用药 + 路径变异） |
| GET | /api/dashboard/stats/… | 看板新增：住院占比 / 合理用药率 / 路径变异率 |

> 一期 `GET /api/patients/{id}/timeline` 扩展新事件类型；Redis 新增键 `timeline:admission:{admissionId}`
> （TTL 30 分钟）缓存住院时间轴，新事件写入时失效对应键。

## 8. 前端页面

1. **患者详情时间轴（改）**：事件卡片扩展 4 类新事件（入院/出院/收费/发药）；入院事件可点击下钻到住院时间轴。
2. **住院时间轴页（新）**：选一次住院，展示体征趋势折线（体温/血压，ECharts）+ 医嘱列表 + 每日体征表。
3. **校验报告页（新）**：汇总指标卡（合理用药率、路径变异率、违规 TopN）+ 违规/变异明细表。
4. **数据看板（改）**：新增住院占比、合理用药率、路径变异率指标卡。
5. **录入表单（改）**：新增「住院登记 / 医嘱 / 体征 / 收费 / 发药」录入，保证演示闭环。

## 9. 测试设计

- **单元/集成**：新 service、校验引擎、生成器扩展 TDD，沿用 @WebMvcTest + @MockBean 风格。
- **功能测试用例**：入院→出院、医嘱开停、体征录入、合理用药校验、路径变异检测、新时间线事件排序与下钻。
- **性能测试**：住院/体征/医嘱批量写入耗时、住院时间轴查询延迟（缓存前后对比）、校验引擎扫描 10 万+ 处方耗时。
- **报告**：扩展 `docs/test-report.md`，新增「住院数据完整性、体征趋势合理性、校验报告准确性」三类评估。

## 10. 实施顺序

1. 数据库：7 张新表 + 种子（rational_rule / clinical_pathway）。
2. his-common：新实体 + Mapper。
3. his-server：REST 接口 + 时间线扩展 + 校验引擎 + Redis 缓存。
4. his-generator：门诊补全 + 住院纵向 + 故意违规注入。
5. 前端：患者时间轴扩展 → 住院时间轴页 → 校验报告页 → 看板/录入改造。
6. 测试：功能/性能测试并扩展报告。

## 11. 全局约束（沿用一期，绑定）

- **不引入账号密码/session/JWT**；登录 = 轻量身份选择（纯前端下拉）。
- **任何模拟数据（医生/患者/药师/护士）不得用「王冠杰」**（作者真实姓名）。
- doctor 表 24 名医生固定名单；visit/admission 的 doctor 必须来自对应科室医生集合（80/20 主科室）。
- 统一信封 `Result{code,message,data}`，code=0 成功。
- 时间线事件严格按 `time` 升序；所有时间字段 `DATETIME`。
- 每个 commit 结尾附 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
