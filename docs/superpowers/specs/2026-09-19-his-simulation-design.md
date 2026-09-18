# 模拟 HIS 系统 —— 虚拟患者时序数据生成与测试平台 · 设计文档

- 日期：2026-09-19
- 状态：待评审
- 题目：基于模拟 HIS 系统的虚拟患者时序数据生成与测试平台设计与实现

## 1. 项目概述

对医院信息系统（HIS）门诊诊疗核心业务流程进行调研与抽象，构建一套**模拟 HIS 系统**：
明确患者管理、诊断记录、用药医嘱、检验结果等关键业务实体及其关系，重点表达**时间维度**；
后端提供 REST 风格的核心业务接口（含按患者 ID 查询完整诊疗时间线），前端提供患者列表、
患者详情时间轴、诊疗录入表单与数据看板。配套一个**独立的数据生成程序**，批量产生符合基本
医学逻辑的虚拟患者时序数据（≥1 万名患者、≥5 万条就诊记录），并完成功能与性能测试分析。

## 2. 需求分解（6 部分）

1. **业务建模 + 数据库设计**：抽象关键实体与关系，设计逻辑/物理模型，重点考虑时间维度表达。
2. **后端服务（REST）**：患者注册、挂号登记、诊断录入、处方开具、检验结果录入；按患者 ID
   查询完整诊疗时间线（按时间升序返回全部就诊事件及关联明细）。
3. **Web 前端**：患者列表、患者详情时间线、诊疗录入表单；**时间轴视图为重点**。
4. **数据生成程序**：独立程序，批量生成 1–3 年跨度的多次就诊记录（时间戳/诊断/药品/检验），
   就诊时间顺序正确，用**配置规则表**约束诊断↔用药合理性；目标 ≥1 万名患者、≥5 万条记录；
   并据此设计功能测试用例，记录批量写入与时间线查询响应时间。
5. **数据看板**：随机抽取患者以时间轴展示事件链；就诊频率、用药分布统计图表。
6. **测试与分析**：功能/性能测试结果表格化整理，从数据完整性、时间一致性、统计分布合理性
   评估，总结局限与改进方向。

## 3. 技术选型

| 层 | 技术 |
|---|---|
| 后端 | Spring Boot 3.3.x（Java 21）· MyBatis-Plus 3.5.x |
| 数据库 | MySQL 8（库 `his_demo`，专用账号 `his_user`） |
| 缓存 | Redis 8（时间线查询、看板统计缓存） |
| 前端 | Vue 3 + Vite · Element Plus · Pinia · ECharts |
| 生成器 | 独立 Maven 模块 `his-generator`，JDBC 批量写入，可 `java -jar` 独立运行 |

## 4. 项目结构

```
his_demo/
├── backend/                    # Maven 多模块（聚合工程）
│   ├── pom.xml
│   ├── his-common/             # 公共：实体、Mapper、DTO、工具
│   ├── his-server/             # SpringBoot 应用（REST API + 缓存）
│   └── his-generator/          # 独立数据生成器（SpringBoot CLI，CommandLineRunner）
├── frontend/                   # Vue3 应用
└── docs/superpowers/specs/     # 设计文档
```

## 5. 数据库设计

统一约定：所有时间字段用 `DATETIME`；主键 `BIGINT AUTO_INCREMENT`；`patient_id`/`visit_id`
建索引以支撑时间线与批量写入。

### 5.1 patient 患者
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| name | VARCHAR(50) | 姓名 |
| gender | TINYINT | 0 男 / 1 女 |
| birth_date | DATE | 出生日期 |
| id_card | VARCHAR(18) | 身份证号 |
| phone | VARCHAR(20) | 电话 |
| address | VARCHAR(255) | 地址 |
| created_at | DATETIME | 建档时间 |

### 5.2 visit 就诊记录（挂号）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| patient_id | BIGINT | 关联患者 |
| visit_no | VARCHAR(32) | 就诊号 |
| department | VARCHAR(50) | 科室 |
| doctor | VARCHAR(50) | 医生 |
| visit_time | DATETIME | 就诊时间 |
| status | TINYINT | 状态 |
| remark | VARCHAR(255) | 备注 |

### 5.3 diagnosis 诊断
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| patient_id | BIGINT | |
| visit_id | BIGINT | |
| icd_code | VARCHAR(20) | ICD 编码 |
| diagnosis_name | VARCHAR(100) | 诊断名称 |
| doctor | VARCHAR(50) | |
| diagnosed_at | DATETIME | 诊断时间 |

### 5.4 prescription 处方（用药医嘱）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| patient_id | BIGINT | |
| visit_id | BIGINT | |
| doctor | VARCHAR(50) | |
| prescribed_at | DATETIME | 开方时间 |
| total_amount | DECIMAL(10,2) | 总金额 |

### 5.5 prescription_item 处方明细
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| prescription_id | BIGINT | 关联处方 |
| drug_id | BIGINT | 药品 id |
| drug_name | VARCHAR(100) | 药品名（冗余） |
| dosage | VARCHAR(50) | 用量 |
| frequency | VARCHAR(50) | 频次 |
| days | INT | 天数 |
| quantity | DECIMAL(10,2) | 数量 |
| unit | VARCHAR(20) | 单位 |

### 5.6 drug 药品字典
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| name | VARCHAR(100) | 名称 |
| category | VARCHAR(50) | 分类 |
| spec | VARCHAR(50) | 规格 |
| unit | VARCHAR(20) | 单位 |
| price | DECIMAL(10,2) | 单价 |

### 5.7 lab_test 检验结果
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| patient_id | BIGINT | |
| visit_id | BIGINT | |
| item_code | VARCHAR(20) | 项目编码 |
| item_name | VARCHAR(100) | 项目名 |
| result_value | VARCHAR(50) | 结果值 |
| unit | VARCHAR(20) | 单位 |
| reference_range | VARCHAR(50) | 参考范围 |
| is_abnormal | TINYINT | 0 正常 / 1 异常 |
| tested_at | DATETIME | 检验时间 |

### 5.8 rule_config 配置规则表（生成器约束）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| diagnosis_code | VARCHAR(20) | 诊断编码 |
| diagnosis_name | VARCHAR(100) | 诊断名 |
| drug_ids | VARCHAR(500) | 关联药品 id 列表（逗号分隔） |
| enabled | TINYINT | 是否启用 |

## 6. 后端 API 设计（REST）

统一前缀 `/api`；统一响应结构 `{ code, message, data }`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/patients | 患者注册 |
| GET | /api/patients | 分页 + 条件搜索 |
| GET | /api/patients/{id} | 患者详情 |
| **GET** | **/api/patients/{id}/timeline** | **核心：按时间升序返回全部就诊事件及明细** |
| POST | /api/visits | 挂号登记 |
| POST | /api/diagnoses | 诊断录入 |
| POST | /api/prescriptions | 处方开具 |
| POST | /api/lab-tests | 检验结果录入 |
| GET | /api/dashboard/random-patient | 随机抽取患者 |
| GET | /api/dashboard/stats/visit-frequency | 就诊频率统计 |
| GET | /api/dashboard/stats/drug-distribution | 用药分布统计 |
| POST | /api/generator/run | 触发批量生成（可选，供演示） |

时间线事件结构：
```json
{
  "patientId": 1,
  "events": [
    { "type": "VISIT",        "time": "2024-01-01 09:00:00", "data": { ... } },
    { "type": "DIAGNOSIS",    "time": "2024-01-01 09:10:00", "data": { ... } },
    { "type": "PRESCRIPTION", "time": "2024-01-01 09:15:00", "data": { "items": [...] } },
    { "type": "LAB_TEST",     "time": "2024-01-01 10:00:00", "data": { ... } }
  ]
}
```
`type` ∈ VISIT / DIAGNOSIS / PRESCRIPTION / LAB_TEST，事件按 `time` 升序。

## 7. 前端页面设计

1. **患者列表**：搜索（姓名/身份证/电话）+ 分页，点击进入详情。
2. **患者详情**：基本信息 + **时间轴视图**（事件卡片，按时间升序渲染，本课题展示重点）。
3. **诊疗录入表单**：挂号 / 诊断 / 处方（含药品明细）/ 检验，提交后刷新时间轴。
4. **数据看板**：随机抽取患者时间轴 + 就诊频率（折线/柱状）+ 用药分布（饼图/柱状）。

## 8. 数据生成器设计（独立模块 his-generator）

- **规则表驱动**：`rule_config` 定义「诊断 → 药品」映射，保证诊断↔用药基本合理。
- **医学逻辑**：内置一批真实常见诊断（如感冒、高血压、糖尿病、胃炎等）及对应常用药、
  检验项目（血常规、血糖、血压等）。
- **时序生成**：每位患者随机 1–3 年就诊史，就诊次数随机（5–20 次），每次就诊生成
  挂号→诊断→处方（含明细）→检验，**时间严格递增**。
- **批量导入**：JDBC batch 写入，目标 **≥1 万名患者、≥5 万条就诊记录**，打印批量写入耗时。
- **运行方式**：`java -jar his-generator.jar` 或 `mvn -pl his-generator spring-boot:run`。

## 9. Redis 设计

- 键 `timeline:{patientId}`：缓存时间线查询结果（TTL 30 分钟）。
- 键 `stats:visit-frequency`、`stats:drug-distribution`：缓存看板统计（TTL 10 分钟）。
- 失效策略：该患者新增任一事件（挂号/诊断/处方/检验）时删除对应 `timeline:{patientId}`。

## 10. 测试设计

- **功能测试用例**：患者建档、诊断录入、处方开具、时间线查询，逐条验证并记录结果。
- **性能测试**：记录批量写入耗时、时间线查询响应时间（含缓存前后对比）。
- **结果整理**：以表格记录各用例执行结果，从数据完整性、时间一致性、统计分布合理性评估，
  总结局限与改进方向（交付为 `docs/` 下测试报告）。

## 11. 实施顺序

1. 后端：数据库建库建表 → his-common 实体/Mapper → his-server REST 接口 → Redis 缓存。
2. 生成器：his-generator 规则表 + 数据生成 + 批量导入，验证数据量与正确性。
3. 前端：患者列表 → 患者详情时间轴 → 诊疗录入 → 数据看板。
4. 测试：功能/性能测试并整理报告。
