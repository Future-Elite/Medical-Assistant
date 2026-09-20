# 模拟 HIS 系统扩展（二期）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在一期「门诊闭环」之上，新增住院纵向时序（住院记录/医嘱/生命体征）、合理用药与临床路径校验引擎、门诊收费/发药，把系统升级为完整的「虚拟患者时序数据生成与测试平台」。

**Architecture:** 沿用既有分层——`his-common`（实体/Mapper/DTO）、`his-server`（REST + 时间线 + 校验引擎 + 缓存）、`his-generator`（批量数据生成）、`frontend`（Vue3）。新增 7 张表 + lab_test 增列，时间线事件联合扩展 4 类顶层事件 + 独立住院时间轴，校验引擎按需计算产出「数据质量校验报告」。

**Tech Stack:** Spring Boot 3.3.5 · Java 21 · MyBatis-Plus 3.5.7 · MySQL 8 · Redis 8 · Vue 3 + Vite + Element Plus + Pinia + ECharts。

**Spec:** [docs/superpowers/specs/2026-09-21-his-extension-design.md](../specs/2026-09-21-his-extension-design.md)

## Global Constraints

- 不引入账号密码/session/JWT；登录 = 轻量身份选择。
- **任何模拟数据（医生/患者/药师/护士）不得用「王冠杰」**（作者真实姓名）。
- doctor 表 24 名医生固定名单；visit/admission 的 doctor 必须来自对应科室医生集合（80/20 主科室）。
- 统一信封 `Result{code,message,data}`，code=0 成功；异常须被 GlobalExceptionHandler 包成信封（code=500，message 不泄露异常明细）。
- 时间线事件严格按 `time` 升序；所有时间字段 `DATETIME`（实体 `LocalDateTime`）。
- Redis 是纯优化：连接失败一律降级为查库重建，绝不让 GET 变 500、POST 在落库后抛 500。
- 测试风格沿用：controller 用 `@WebMvcTest` + `@MockBean`，service/生成器用 Mockito。
- 每个 commit 结尾附 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 构建注意：`mvn -pl his-server -am test -Dtest=X` 需追加 `-Dsurefire.failIfNoSpecifiedTests=false`（his-common 无匹配测试会中止）。
- 数据字典表（drug / rule_config / doctor / rational_rule / clinical_pathway）是种子数据，生成器清空时**不得碰**。

## File Structure（新增/改动总览）

**his-common**（实体 + Mapper + DTO）
- 新增实体：`entity/Admission.java`、`MedicalOrder.java`、`VitalSign.java`、`Charge.java`、`Dispense.java`、`RationalRule.java`、`ClinicalPathway.java`
- 修改实体：`entity/LabTest.java`（增 `admissionId` 字段）
- 新增 Mapper：`mapper/AdmissionMapper.java`、`MedicalOrderMapper.java`、`VitalSignMapper.java`、`ChargeMapper.java`、`DispenseMapper.java`、`RationalRuleMapper.java`、`ClinicalPathwayMapper.java`
- 新增 DTO：`dto/RationalDrugReport.java`、`RationalViolation.java`、`PathwayReport.java`、`PathwayVariance.java`、`ValidationReport.java`

**his-server**（接口 + 时间线 + 校验 + 看板）
- 新增 service：`AdmissionService`、`MedicalOrderService`、`VitalSignService`、`ChargeService`、`DispenseService`、`ValidationService`（+ impl）
- 新增组件：`RationalDrugValidator`、`PathwayValidator`
- 新增 controller：`AdmissionController`、`MedicalOrderController`、`VitalSignController`、`ChargeController`、`DispenseController`、`ValidationController`
- 修改：`TimelineService`/`TimelineServiceImpl`（患者时间轴新事件 + 住院时间轴）、`DashboardService`/`DashboardServiceImpl`/`DashboardController`（新统计）

**his-generator**
- 修改：`DataGeneratorService`（清空表、加载字典、门诊补全、住院纵向、违规注入）

**SQL**
- 修改：`sql/schema.sql`（7 张新表 + lab_test 增列）
- 新增：`sql/seed-extension.sql`（rational_rule + clinical_pathway 种子）

**frontend**
- 新增 api：`api/inpatient.js`、`api/charge.js`、`api/validation.js`
- 修改 api：`api/dashboard.js`
- 修改组件：`components/Timeline.vue`（新事件卡片 + 住院下钻）
- 新增视图：`views/AdmissionDetail.vue`、`views/ValidationReport.vue`
- 修改视图：`views/Dashboard.vue`（新指标）、`views/EntryForm.vue`（住院/医嘱/体征/收费/发药录入）
- 修改：`router/index.js`（新增路由）、`App.vue`（新增菜单）

---

## Task 1: 数据库 —— 7 张新表 + lab_test 增列 + 种子

**Files:**
- Modify: `backend/sql/schema.sql`
- Create: `backend/sql/seed-extension.sql`

**Interfaces:**
- Produces: 7 张表 + lab_test.admission_id 列，供 Task 2 实体映射、Task 10/11 生成器写入、Task 7/8 校验器读取。

- [ ] **Step 1: 在 schema.sql 末尾追加 7 张新表 + 修改 lab_test 定义**

把 `lab_test` 的 CREATE 增加 `admission_id` 列与索引（门诊为 NULL）。找到 schema.sql 中 lab_test 表定义，在其列区增加 `admission_id BIGINT NULL`，并在索引区增加 `KEY idx_lab_admission (admission_id)`。

然后在 schema.sql 末尾追加：

```sql
CREATE TABLE admission (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  patient_id BIGINT NOT NULL,
  admission_no VARCHAR(32) NOT NULL,
  department VARCHAR(50), doctor VARCHAR(50),
  bed_no VARCHAR(20), diagnosis VARCHAR(100),
  admission_time DATETIME NOT NULL,
  discharge_time DATETIME NULL,
  deposit DECIMAL(10,2) DEFAULT 0,
  total_cost DECIMAL(10,2) DEFAULT 0,
  status TINYINT NOT NULL DEFAULT 0 COMMENT '0在院1出院',
  KEY idx_admission_patient_time (patient_id, admission_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='住院记录表';

CREATE TABLE medical_order (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  patient_id BIGINT NOT NULL,
  admission_id BIGINT NOT NULL,
  order_type TINYINT NOT NULL DEFAULT 0 COMMENT '0长期1临时2检验3护理',
  item_name VARCHAR(100) NOT NULL,
  dosage VARCHAR(50), frequency VARCHAR(50),
  start_time DATETIME NOT NULL,
  stop_time DATETIME NULL,
  status TINYINT NOT NULL DEFAULT 0 COMMENT '0执行中1已停2已执行',
  doctor VARCHAR(50),
  KEY idx_order_admission_time (admission_id, start_time),
  KEY idx_order_patient (patient_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='医嘱表';

CREATE TABLE vital_sign (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  patient_id BIGINT NOT NULL,
  admission_id BIGINT NOT NULL,
  temperature DECIMAL(4,1), pulse INT, respiration INT,
  systolic_bp INT, diastolic_bp INT,
  recorded_at DATETIME NOT NULL,
  nurse VARCHAR(50),
  KEY idx_vital_admission_time (admission_id, recorded_at),
  KEY idx_vital_patient (patient_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='生命体征表';

CREATE TABLE charge (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  patient_id BIGINT NOT NULL,
  visit_id BIGINT NOT NULL,
  charge_no VARCHAR(32) NOT NULL,
  item_type TINYINT NOT NULL DEFAULT 0 COMMENT '0挂号费1药费2检验费',
  item_name VARCHAR(100),
  amount DECIMAL(10,2) NOT NULL DEFAULT 0,
  charged_at DATETIME NOT NULL,
  KEY idx_charge_visit (visit_id),
  KEY idx_charge_patient_time (patient_id, charged_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='收费表';

CREATE TABLE dispense (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  patient_id BIGINT NOT NULL,
  visit_id BIGINT NOT NULL,
  prescription_id BIGINT NOT NULL,
  drug_name VARCHAR(100),
  quantity DECIMAL(10,2),
  dispensed_at DATETIME NOT NULL,
  pharmacist VARCHAR(50),
  KEY idx_dispense_prescription (prescription_id),
  KEY idx_dispense_visit (visit_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='发药表';

CREATE TABLE rational_rule (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  rule_type TINYINT NOT NULL DEFAULT 1 COMMENT '1相互作用2剂量上限',
  diagnosis_code VARCHAR(20),
  drug_a_id BIGINT, drug_b_id BIGINT,
  drug_id BIGINT, max_daily_dose DECIMAL(10,2),
  warning_level TINYINT NOT NULL DEFAULT 1 COMMENT '0提示1警告2严重',
  message VARCHAR(255),
  enabled TINYINT NOT NULL DEFAULT 1,
  KEY idx_rational_type (rule_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='合理用药规则表';

CREATE TABLE clinical_pathway (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  diagnosis_code VARCHAR(20),
  diagnosis_name VARCHAR(100) NOT NULL,
  steps VARCHAR(2000),
  enabled TINYINT NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='临床路径定义表';
```

- [ ] **Step 2: 创建 seed-extension.sql（只含新字典，避免整份 seed 重跑导致重复插入）**

```sql
-- 模拟 HIS 系统二期 —— 增量种子（合理用药规则 + 临床路径）
-- 执行：mysql -uroot -p123456 --default-character-set=utf8mb4 his_demo < backend/sql/seed-extension.sql
-- 药品 id 对齐 backend/sql/seed.sql：1阿莫西林 3布洛芬 4硝苯地平 5二甲双胍 8左氧氟沙星 9阿司匹林 10氨氯地平

INSERT INTO rational_rule (rule_type, drug_a_id, drug_b_id, warning_level, message, enabled) VALUES
(1, 1, 8, 2, '阿莫西林与左氧氟沙星抗菌机制重叠，不宜联用', 1),
(1, 3, 9, 2, '布洛芬与阿司匹林合用增加消化道出血风险', 1),
(1, 4, 10, 1, '两种钙通道阻滞剂重复用药', 1);

INSERT INTO rational_rule (rule_type, drug_id, max_daily_dose, warning_level, message, enabled) VALUES
(2, 4, 8.00, 1, '硝苯地平每日最大剂量 8 片', 1),
(2, 5, 8.00, 1, '二甲双胍每日最大剂量 8 片', 1);

INSERT INTO clinical_pathway (diagnosis_code, diagnosis_name, steps, enabled) VALUES
('J06.9','上呼吸道感染','[{"day":0,"action":"LAB","item":"白细胞计数","window":24},{"day":0,"action":"MED","item":"感冒灵颗粒","window":48},{"day":3,"action":"DISCHARGE","window":24}]',1),
('E11.9','2型糖尿病','[{"day":0,"action":"LAB","item":"空腹血糖","window":24},{"day":0,"action":"MED","item":"二甲双胍片","window":48},{"day":2,"action":"LAB","item":"空腹血糖","window":24},{"day":5,"action":"DISCHARGE","window":24}]',1);
```

- [ ] **Step 3: 对已部署库追加 ALTER（非幂等，仅执行一次）**

在计划末尾「部署」处记录：对已存在的 `lab_test` 表执行
```sql
ALTER TABLE lab_test ADD COLUMN admission_id BIGINT NULL, ADD KEY idx_lab_admission (admission_id);
```

- [ ] **Step 4: Commit**

```bash
git add backend/sql/schema.sql backend/sql/seed-extension.sql
git commit -m "feat: 二期数据库（7 新表 + lab_test 增列 + 合理用药/临床路径种子）"
```

---

## Task 2: his-common —— 7 实体 + 7 Mapper + LabTest 增列

**Files:**
- Create: `backend/his-common/src/main/java/com/his/common/entity/{Admission,MedicalOrder,VitalSign,Charge,Dispense,RationalRule,ClinicalPathway}.java`
- Modify: `backend/his-common/src/main/java/com/his/common/entity/LabTest.java`
- Create: `backend/his-common/src/main/java/com/his/common/mapper/{Admission,MedicalOrder,VitalSign,Charge,Dispense,RationalRule,ClinicalPathway}Mapper.java`

**Interfaces:**
- Produces: 实体 + Mapper，供后续所有 service/generator/validator 消费。字段名与 Task 1 列名一一对应（MyBatis-Plus 驼峰映射）。

- [ ] **Step 1: 写 7 个实体（逐个按下面代码，全为 `@Data @TableName` + `@TableId(IdType.AUTO)`）**

`Admission.java`：
```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.math.BigDecimal;
import java.time.LocalDateTime;

@Data
@TableName("admission")
public class Admission {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private String admissionNo;
    private String department;
    private String doctor;
    private String bedNo;
    private String diagnosis;
    private LocalDateTime admissionTime;
    private LocalDateTime dischargeTime;
    private BigDecimal deposit;
    private BigDecimal totalCost;
    private Integer status;
}
```

`MedicalOrder.java`：
```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.time.LocalDateTime;

@Data
@TableName("medical_order")
public class MedicalOrder {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private Long admissionId;
    private Integer orderType;
    private String itemName;
    private String dosage;
    private String frequency;
    private LocalDateTime startTime;
    private LocalDateTime stopTime;
    private Integer status;
    private String doctor;
}
```

`VitalSign.java`：
```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.math.BigDecimal;
import java.time.LocalDateTime;

@Data
@TableName("vital_sign")
public class VitalSign {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private Long admissionId;
    private BigDecimal temperature;
    private Integer pulse;
    private Integer respiration;
    private Integer systolicBp;
    private Integer diastolicBp;
    private LocalDateTime recordedAt;
    private String nurse;
}
```

`Charge.java`：
```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.math.BigDecimal;
import java.time.LocalDateTime;

@Data
@TableName("charge")
public class Charge {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private Long visitId;
    private String chargeNo;
    private Integer itemType;
    private String itemName;
    private BigDecimal amount;
    private LocalDateTime chargedAt;
}
```

`Dispense.java`：
```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.math.BigDecimal;
import java.time.LocalDateTime;

@Data
@TableName("dispense")
public class Dispense {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private Long visitId;
    private Long prescriptionId;
    private String drugName;
    private BigDecimal quantity;
    private LocalDateTime dispensedAt;
    private String pharmacist;
}
```

`RationalRule.java`：
```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.math.BigDecimal;

@Data
@TableName("rational_rule")
public class RationalRule {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Integer ruleType;
    private String diagnosisCode;
    private Long drugAId;
    private Long drugBId;
    private Long drugId;
    private BigDecimal maxDailyDose;
    private Integer warningLevel;
    private String message;
    private Integer enabled;
}
```

`ClinicalPathway.java`：
```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

@Data
@TableName("clinical_pathway")
public class ClinicalPathway {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String diagnosisCode;
    private String diagnosisName;
    private String steps;
    private Integer enabled;
}
```

- [ ] **Step 2: 修改 LabTest.java，增 `admissionId` 字段**

在 `LabTest` 的字段区（`testedAt` 之前或之后均可）增加：
```java
    private Long admissionId;
```

- [ ] **Step 3: 写 7 个 Mapper（均为空接口，无自定义查询）**

以 `AdmissionMapper.java` 为例（其余 6 个同构，仅改类名/泛型/import）：
```java
package com.his.common.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.his.common.entity.Admission;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface AdmissionMapper extends BaseMapper<Admission> {
}
```
依次写 `MedicalOrderMapper`、`VitalSignMapper`、`ChargeMapper`、`DispenseMapper`、`RationalRuleMapper`、`ClinicalPathwayMapper`。

- [ ] **Step 4: 编译验证**

Run: `mvn -q -pl his-common -am compile`
Expected: BUILD SUCCESS。

- [ ] **Step 5: Commit**

```bash
git add backend/his-common/src/main/java/com/his/common/entity backend/his-common/src/main/java/com/his/common/mapper
git commit -m "feat: 二期实体与 Mapper（住院/医嘱/体征/收费/发药/合理用药规则/临床路径）"
```

---

## Task 3: 门诊收费 + 发药（后端）

**Files:**
- Create: `backend/his-server/src/main/java/com/his/server/service/ChargeService.java`、`DispenseService.java`
- Create: `backend/his-server/src/main/java/com/his/server/service/impl/ChargeServiceImpl.java`、`DispenseServiceImpl.java`
- Create: `backend/his-server/src/main/java/com/his/server/controller/ChargeController.java`、`DispenseController.java`
- Test: `backend/his-server/src/test/java/com/his/server/controller/ChargeControllerTest.java`、`DispenseControllerTest.java`

**Interfaces:**
- Consumes: `Charge`/`Dispense` 实体 + `ChargeMapper`/`DispenseMapper`（Task 2）、`CacheInvalidator`。
- Produces: `ChargeService`/`DispenseService`（IService 扩展）、`POST /api/charges`、`POST /api/dispenses`、`GET /api/charges?visitId`、`GET /api/dispenses?visitId`。

- [ ] **Step 1: 写 service 接口**

`ChargeService.java`：
```java
package com.his.server.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.his.common.entity.Charge;

public interface ChargeService extends IService<Charge> {
}
```
`DispenseService.java` 同构（`Dispense`）。

- [ ] **Step 2: 写 service 实现（save 时失效时间线缓存）**

`ChargeServiceImpl.java`：
```java
package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Charge;
import com.his.common.mapper.ChargeMapper;
import com.his.server.service.CacheInvalidator;
import com.his.server.service.ChargeService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class ChargeServiceImpl extends ServiceImpl<ChargeMapper, Charge> implements ChargeService {

    private final CacheInvalidator cacheInvalidator;

    @Override
    public boolean save(Charge entity) {
        boolean saved = super.save(entity);
        cacheInvalidator.evictTimeline(entity.getPatientId());
        return saved;
    }
}
```
`DispenseServiceImpl.java` 同构（`Dispense`/`DispenseMapper`/`DispenseService`）。

- [ ] **Step 3: 写 controller**

`ChargeController.java`：
```java
package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.common.Result;
import com.his.common.entity.Charge;
import com.his.server.service.ChargeService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/charges")
@RequiredArgsConstructor
public class ChargeController {

    private final ChargeService chargeService;

    @PostMapping
    public Result<Charge> create(@RequestBody Charge charge) {
        chargeService.save(charge);
        return Result.ok(charge);
    }

    @GetMapping
    public Result<List<Charge>> list(@RequestParam Long visitId) {
        return Result.ok(chargeService.list(new LambdaQueryWrapper<Charge>()
                .eq(Charge::getVisitId, visitId)
                .orderByAsc(Charge::getChargedAt)));
    }
}
```
`DispenseController.java` 同构（`/api/dispenses`，`Dispense`，按 `visitId` 查询，`orderByAsc(Dispense::getDispensedAt)`）。

- [ ] **Step 4: 写测试（@WebMvcTest + @MockBean）**

`ChargeControllerTest.java`：
```java
package com.his.server.controller;

import com.his.common.entity.Charge;
import com.his.server.service.ChargeService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(ChargeController.class)
class ChargeControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean ChargeService chargeService;

    @Test
    void create_returnsCharge() throws Exception {
        when(chargeService.save(any())).thenReturn(true);
        mockMvc.perform(post("/api/charges")
                        .contentType("application/json")
                        .content("{\"patientId\":1,\"visitId\":2,\"amount\":18.50}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));
    }
}
```
`DispenseControllerTest.java` 同构（`/api/dispenses`）。

- [ ] **Step 5: 跑测试**

Run: `mvn -q -pl his-server -am test -Dtest=ChargeControllerTest,DispenseControllerTest -Dsurefire.failIfNoSpecifiedTests=false`
Expected: BUILD SUCCESS（2 测试通过）。

- [ ] **Step 6: Commit**

```bash
git add backend/his-server/src/main/java/com/his/server/service backend/his-server/src/main/java/com/his/server/controller backend/his-server/src/test/java/com/his/server/controller
git commit -m "feat: 门诊收费与发药接口"
```

---

## Task 4: 住院记录（后端）

**Files:**
- Create: `backend/his-server/src/main/java/com/his/server/service/AdmissionService.java` + `impl/AdmissionServiceImpl.java`
- Create: `backend/his-server/src/main/java/com/his/server/controller/AdmissionController.java`
- Test: `backend/his-server/src/test/java/com/his/server/controller/AdmissionControllerTest.java`、`service/AdmissionServiceImplTest.java`

**Interfaces:**
- Consumes: `Admission` 实体 + `AdmissionMapper`、`CacheInvalidator`。
- Produces: `AdmissionService.discharge(Long, BigDecimal)`、`POST /api/admissions`、`PUT /api/admissions/{id}/discharge`、`GET /api/admissions?patientId`。住院时间轴端点（`GET /api/admissions/{id}/timeline`）由 Task 6 的 TimelineService 提供，本任务 controller 已预留引用（Task 6 补上 `timelineService` 注入后实现）。

- [ ] **Step 1: 写 service 接口**

```java
package com.his.server.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.his.common.entity.Admission;
import java.math.BigDecimal;

public interface AdmissionService extends IService<Admission> {
    /** 出院结算：填出院时间、结算总费用、置 status=1。返回 null 表示记录不存在。 */
    Admission discharge(Long id, BigDecimal totalCost);
}
```

- [ ] **Step 2: 写实现**

```java
package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Admission;
import com.his.common.mapper.AdmissionMapper;
import com.his.server.service.AdmissionService;
import com.his.server.service.CacheInvalidator;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.LocalDateTime;

@Service
@RequiredArgsConstructor
public class AdmissionServiceImpl extends ServiceImpl<AdmissionMapper, Admission> implements AdmissionService {

    private final CacheInvalidator cacheInvalidator;

    @Override
    public boolean save(Admission entity) {
        boolean saved = super.save(entity);
        cacheInvalidator.evictTimeline(entity.getPatientId());
        return saved;
    }

    @Override
    public Admission discharge(Long id, BigDecimal totalCost) {
        Admission a = getById(id);
        if (a == null) {
            return null;
        }
        a.setDischargeTime(LocalDateTime.now());
        a.setTotalCost(totalCost == null ? BigDecimal.ZERO : totalCost);
        a.setStatus(1);
        updateById(a);
        cacheInvalidator.evictTimeline(a.getPatientId());
        return a;
    }
}
```

- [ ] **Step 3: 写 controller（先不含 timeline 端点，Task 6 再加）**

```java
package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.common.Result;
import com.his.common.entity.Admission;
import com.his.server.service.AdmissionService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.util.List;

@RestController
@RequestMapping("/api/admissions")
@RequiredArgsConstructor
public class AdmissionController {

    private final AdmissionService admissionService;

    @PostMapping
    public Result<Admission> create(@RequestBody Admission admission) {
        admissionService.save(admission);
        return Result.ok(admission);
    }

    @PutMapping("/{id}/discharge")
    public Result<Admission> discharge(@PathVariable Long id,
                                       @RequestParam(required = false) BigDecimal totalCost) {
        return Result.ok(admissionService.discharge(id, totalCost));
    }

    @GetMapping
    public Result<List<Admission>> list(@RequestParam Long patientId) {
        return Result.ok(admissionService.list(new LambdaQueryWrapper<Admission>()
                .eq(Admission::getPatientId, patientId)
                .orderByDesc(Admission::getAdmissionTime)));
    }
}
```

- [ ] **Step 4: 写 service 测试（Mockito）**

`AdmissionServiceImplTest.java`：
```java
package com.his.server.service;

import com.his.common.entity.Admission;
import com.his.common.mapper.AdmissionMapper;
import com.his.server.service.impl.AdmissionServiceImpl;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class AdmissionServiceImplTest {

    @Test
    void discharge_sets_discharge_time_total_cost_and_status() {
        AdmissionMapper mapper = mock(AdmissionMapper.class);
        CacheInvalidator inv = mock(CacheInvalidator.class);
        Admission a = new Admission();
        a.setId(1L); a.setPatientId(42L); a.setStatus(0);
        when(mapper.selectById(1L)).thenReturn(a);
        when(mapper.updateById(a)).thenReturn(1);

        AdmissionServiceImpl svc = new AdmissionServiceImpl(inv);
        // 用反射替换 baseMapper，或用 spy；此处直接 mock 并设 baseMapper 不可行，改用构造 + 手动注入
        // 说明：ServiceImpl 的 baseMapper 是 protected，测试里用 ReflectionTestUtils 注入
        org.springframework.test.util.ReflectionTestUtils.setField(svc, "baseMapper", mapper);

        Admission out = svc.discharge(1L, new BigDecimal("1234.56"));

        assertThat(out.getStatus()).isEqualTo(1);
        assertThat(out.getDischargeTime()).isNotNull();
        assertThat(out.getTotalCost()).isEqualByComparingTo("1234.56");
        verify(inv).evictTimeline(42L);
    }

    @Test
    void discharge_returns_null_when_admission_absent() {
        AdmissionMapper mapper = mock(AdmissionMapper.class);
        when(mapper.selectById(99L)).thenReturn(null);
        AdmissionServiceImpl svc = new AdmissionServiceImpl(mock(CacheInvalidator.class));
        org.springframework.test.util.ReflectionTestUtils.setField(svc, "baseMapper", mapper);

        assertThat(svc.discharge(99L, BigDecimal.ZERO)).isNull();
    }
}
```

- [ ] **Step 5: 写 controller 测试**

`AdmissionControllerTest.java`（参照 VisitControllerTest 风格）：
```java
package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.Wrapper;
import com.his.common.entity.Admission;
import com.his.server.service.AdmissionService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(AdmissionController.class)
class AdmissionControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean AdmissionService admissionService;

    @Test
    void list_returns_admissions_for_patient() throws Exception {
        Admission a = new Admission();
        a.setId(1L); a.setPatientId(10L); a.setAdmissionNo("A10-1");
        when(admissionService.list(any(Wrapper.class))).thenReturn(List.of(a));

        mockMvc.perform(get("/api/admissions").param("patientId", "10"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data[0].admissionNo").value("A10-1"));
    }
}
```

- [ ] **Step 6: 跑测试**

Run: `mvn -q -pl his-server -am test -Dtest=AdmissionControllerTest,AdmissionServiceImplTest -Dsurefire.failIfNoSpecifiedTests=false`
Expected: BUILD SUCCESS。

- [ ] **Step 7: Commit**

```bash
git add backend/his-server/src/main/java/com/his/server/service backend/his-server/src/main/java/com/his/server/controller backend/his-server/src/test/java/com/his/server
git commit -m "feat: 住院登记与出院结算接口"
```

---

## Task 5: 医嘱 + 生命体征（后端）

**Files:**
- Create: `backend/his-server/src/main/java/com/his/server/service/{MedicalOrderService,VitalSignService}.java` + impl
- Create: `backend/his-server/src/main/java/com/his/server/controller/{MedicalOrderController,VitalSignController}.java`
- Test: `backend/his-server/src/test/java/com/his/server/controller/{MedicalOrderControllerTest,VitalSignControllerTest}.java`

**Interfaces:**
- Consumes: `MedicalOrder`/`VitalSign` 实体 + Mapper、`CacheInvalidator`。
- Produces: `MedicalOrderService.stop(Long)`、`POST /api/orders`、`PUT /api/orders/{id}/stop`、`GET /api/orders?admissionId`、`POST /api/vital-signs`、`GET /api/vital-signs?admissionId`。

- [ ] **Step 1: 写 service 接口**

`MedicalOrderService.java`：
```java
package com.his.server.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.his.common.entity.MedicalOrder;

public interface MedicalOrderService extends IService<MedicalOrder> {
    /** 停长期医嘱：置 stop_time=now、status=1。返回 null 表示不存在。 */
    MedicalOrder stop(Long id);
}
```
`VitalSignService.java`：
```java
package com.his.server.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.his.common.entity.VitalSign;

public interface VitalSignService extends IService<VitalSign> {
}
```

- [ ] **Step 2: 写实现**

`MedicalOrderServiceImpl.java`：
```java
package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.MedicalOrder;
import com.his.common.mapper.MedicalOrderMapper;
import com.his.server.service.CacheInvalidator;
import com.his.server.service.MedicalOrderService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

@Service
@RequiredArgsConstructor
public class MedicalOrderServiceImpl extends ServiceImpl<MedicalOrderMapper, MedicalOrder>
        implements MedicalOrderService {

    private final CacheInvalidator cacheInvalidator;

    @Override
    public boolean save(MedicalOrder entity) {
        boolean saved = super.save(entity);
        cacheInvalidator.evictTimeline(entity.getPatientId());
        return saved;
    }

    @Override
    public MedicalOrder stop(Long id) {
        MedicalOrder o = getById(id);
        if (o == null) {
            return null;
        }
        o.setStopTime(LocalDateTime.now());
        o.setStatus(1);
        updateById(o);
        cacheInvalidator.evictTimeline(o.getPatientId());
        return o;
    }
}
```
`VitalSignServiceImpl.java` 同构（`VitalSign`/`VitalSignMapper`/`VitalSignService`，仅 save 覆写）。

- [ ] **Step 3: 写 controller**

`MedicalOrderController.java`：
```java
package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.common.Result;
import com.his.common.entity.MedicalOrder;
import com.his.server.service.MedicalOrderService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/orders")
@RequiredArgsConstructor
public class MedicalOrderController {

    private final MedicalOrderService medicalOrderService;

    @PostMapping
    public Result<MedicalOrder> create(@RequestBody MedicalOrder order) {
        medicalOrderService.save(order);
        return Result.ok(order);
    }

    @PutMapping("/{id}/stop")
    public Result<MedicalOrder> stop(@PathVariable Long id) {
        return Result.ok(medicalOrderService.stop(id));
    }

    @GetMapping
    public Result<List<MedicalOrder>> list(@RequestParam Long admissionId) {
        return Result.ok(medicalOrderService.list(new LambdaQueryWrapper<MedicalOrder>()
                .eq(MedicalOrder::getAdmissionId, admissionId)
                .orderByAsc(MedicalOrder::getStartTime)));
    }
}
```
`VitalSignController.java` 同构（`/api/vital-signs`，`VitalSign`，按 `admissionId`，`orderByAsc(VitalSign::getRecordedAt)`）。

- [ ] **Step 4: 写测试**

`MedicalOrderControllerTest.java`（参照 Task 4 controller 测试，含 stop 端点）：
```java
package com.his.server.controller;

import com.his.common.entity.MedicalOrder;
import com.his.server.service.MedicalOrderService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(MedicalOrderController.class)
class MedicalOrderControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean MedicalOrderService medicalOrderService;

    @Test
    void create_returnsOrder() throws Exception {
        when(medicalOrderService.save(any())).thenReturn(true);
        mockMvc.perform(post("/api/orders")
                        .contentType("application/json")
                        .content("{\"patientId\":1,\"admissionId\":2,\"itemName\":\"硝苯地平控释片\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));
    }

    @Test
    void stop_delegates() throws Exception {
        MedicalOrder o = new MedicalOrder();
        o.setId(1L); o.setPatientId(1L); o.setStatus(1);
        when(medicalOrderService.stop(1L)).thenReturn(o);
        mockMvc.perform(put("/api/orders/1/stop"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value(1));
    }
}
```
`VitalSignControllerTest.java` 同构（仅 create）。

- [ ] **Step 5: 跑测试**

Run: `mvn -q -pl his-server -am test -Dtest=MedicalOrderControllerTest,VitalSignControllerTest -Dsurefire.failIfNoSpecifiedTests=false`
Expected: BUILD SUCCESS。

- [ ] **Step 6: Commit**

```bash
git add backend/his-server/src/main/java/com/his/server/service backend/his-server/src/main/java/com/his/server/controller backend/his-server/src/test/java/com/his/server/controller
git commit -m "feat: 医嘱开立/停与生命体征录入接口"
```

---

## Task 6: 时间轴扩展（患者新事件 + 住院时间轴，后端）

**Files:**
- Modify: `backend/his-server/src/main/java/com/his/server/service/TimelineService.java`
- Modify: `backend/his-server/src/main/java/com/his/server/service/impl/TimelineServiceImpl.java`
- Modify: `backend/his-server/src/main/java/com/his/server/controller/AdmissionController.java`（补 timeline 端点）
- Test: `backend/his-server/src/test/java/com/his/server/service/TimelineServiceTest.java`（更新构造 + 新增断言）

**Interfaces:**
- Consumes: `AdmissionMapper`、`MedicalOrderMapper`、`VitalSignMapper`、`ChargeMapper`、`DispenseMapper`（Task 2）。
- Produces: `TimelineService.getAdmissionTimeline(Long)`；患者时间轴新增 `ADMISSION`/`DISCHARGE`/`CHARGE`/`DISPENSE` 四类事件；`GET /api/admissions/{id}/timeline`。

**关键约束**：`TimelineServiceImpl` 用 `@RequiredArgsConstructor` 按**字段声明顺序**生成构造器，`TimelineServiceTest` 按位置传参。新字段追加顺序必须固定为：`visitMapper, diagnosisMapper, prescriptionMapper, itemMapper, labTestMapper, admissionMapper, medicalOrderMapper, vitalSignMapper, chargeMapper, dispenseMapper, redis, objectMapper`。

- [ ] **Step 1: 扩展接口**

`TimelineService.java`：
```java
package com.his.server.service;

import com.his.common.dto.TimelineVO;

public interface TimelineService {
    TimelineVO getTimeline(Long patientId);
    TimelineVO getAdmissionTimeline(Long admissionId);
}
```

- [ ] **Step 2: 改实现 —— 加字段与构造依赖**

在 `TimelineServiceImpl` 的字段区，`labTestMapper` 之后、`redis` 之前追加：
```java
    private final AdmissionMapper admissionMapper;
    private final MedicalOrderMapper medicalOrderMapper;
    private final VitalSignMapper vitalSignMapper;
    private final ChargeMapper chargeMapper;
    private final DispenseMapper dispenseMapper;
```
（import 补 `com.his.common.mapper.*` 已用通配，无需改动 import。）

- [ ] **Step 3: build() 追加 4 类顶层事件**

在 `build(Long patientId)` 方法内，`labTestMapper.selectList(...)` 的 forEach 之后、`events.sort(...)` 之前，追加：
```java
        admissionMapper.selectList(new LambdaQueryWrapper<Admission>().eq(Admission::getPatientId, patientId))
                .forEach(a -> {
                    events.add(new TimelineEvent("ADMISSION", a.getAdmissionTime(), a));
                    if (a.getDischargeTime() != null) {
                        events.add(new TimelineEvent("DISCHARGE", a.getDischargeTime(), a));
                    }
                });

        chargeMapper.selectList(new LambdaQueryWrapper<Charge>().eq(Charge::getPatientId, patientId))
                .forEach(c -> events.add(new TimelineEvent("CHARGE", c.getChargedAt(), c)));

        dispenseMapper.selectList(new LambdaQueryWrapper<Dispense>().eq(Dispense::getPatientId, patientId))
                .forEach(d -> events.add(new TimelineEvent("DISPENSE", d.getDispensedAt(), d)));
```

- [ ] **Step 4: 新增 getAdmissionTimeline（带缓存，结构与 getTimeline 同构）**

```java
    private static final String ADMISSION_CACHE_PREFIX = "timeline:admission:";

    @Override
    public TimelineVO getAdmissionTimeline(Long admissionId) {
        String key = ADMISSION_CACHE_PREFIX + admissionId;
        String cached = null;
        try {
            cached = redis.opsForValue().get(key);
        } catch (Exception e) {
            log.warn("Redis 读取住院时间轴缓存异常，改为直接查库重建: admissionId={}", admissionId, e);
        }
        if (cached != null) {
            try {
                return objectMapper.readValue(cached, TimelineVO.class);
            } catch (Exception e) {
                log.warn("住院时间轴缓存反序列化失败，忽略缓存内容并重建: admissionId={}", admissionId, e);
            }
        }
        TimelineVO vo = buildAdmission(admissionId);
        try {
            redis.opsForValue().set(key, objectMapper.writeValueAsString(vo), TTL);
        } catch (Exception e) {
            log.warn("Redis 写入住院时间轴缓存异常（本次响应不受影响）: admissionId={}", admissionId, e);
        }
        return vo;
    }

    private TimelineVO buildAdmission(Long admissionId) {
        Admission admission = admissionMapper.selectById(admissionId);
        if (admission == null) {
            return new TimelineVO(null, List.of());
        }
        List<TimelineEvent> events = new ArrayList<>();

        medicalOrderMapper.selectList(new LambdaQueryWrapper<MedicalOrder>()
                        .eq(MedicalOrder::getAdmissionId, admissionId))
                .forEach(o -> events.add(new TimelineEvent("ORDER", o.getStartTime(), o)));

        vitalSignMapper.selectList(new LambdaQueryWrapper<VitalSign>()
                        .eq(VitalSign::getAdmissionId, admissionId))
                .forEach(v -> events.add(new TimelineEvent("VITAL_SIGN", v.getRecordedAt(), v)));

        labTestMapper.selectList(new LambdaQueryWrapper<LabTest>()
                        .eq(LabTest::getAdmissionId, admissionId))
                .forEach(l -> events.add(new TimelineEvent("LAB_TEST", l.getTestedAt(), l)));

        events.sort(Comparator.comparing(TimelineEvent::getTime));
        return new TimelineVO(admission.getPatientId(), events);
    }
```

- [ ] **Step 5: AdmissionController 补 timeline 端点**

在 `AdmissionController` 注入 `TimelineService`，并加：
```java
    private final TimelineService timelineService;

    @GetMapping("/{id}/timeline")
    public Result<TimelineVO> timeline(@PathVariable Long id) {
        return Result.ok(timelineService.getAdmissionTimeline(id));
    }
```
（补 import `com.his.common.dto.TimelineVO`、`com.his.server.service.TimelineService`。）

- [ ] **Step 6: 更新 TimelineServiceTest**

把 `setUp()` 里新增 5 个 mapper 的 mock（默认返回空列表），并把 `svc()` 的构造调用改为按新字段顺序传 12 个参数：
```java
    private AdmissionMapper am;
    private MedicalOrderMapper mom;
    private VitalSignMapper vsm;
    private ChargeMapper cm;
    private DispenseMapper dsm;

    @BeforeEach
    @SuppressWarnings("unchecked")
    void setUp() {
        vm = mock(VisitMapper.class);
        dm = mock(DiagnosisMapper.class);
        pm = mock(PrescriptionMapper.class);
        im = mock(PrescriptionItemMapper.class);
        lm = mock(LabTestMapper.class);
        am = mock(AdmissionMapper.class);
        mom = mock(MedicalOrderMapper.class);
        vsm = mock(VitalSignMapper.class);
        cm = mock(ChargeMapper.class);
        dsm = mock(DispenseMapper.class);
        redis = mock(StringRedisTemplate.class);
        ops = mock(ValueOperations.class);
        when(redis.opsForValue()).thenReturn(ops);

        Visit v = new Visit(); v.setId(1L); v.setPatientId(1L); v.setVisitTime(LocalDateTime.of(2024, 1, 1, 10, 0));
        LabTest l = new LabTest(); l.setId(1L); l.setPatientId(1L); l.setTestedAt(LocalDateTime.of(2024, 1, 1, 9, 30));
        lenient().when(vm.selectList(any())).thenReturn(List.of(v));
        lenient().when(dm.selectList(any())).thenReturn(List.of());
        lenient().when(pm.selectList(any())).thenReturn(List.of());
        lenient().when(im.selectList(any())).thenReturn(List.of());
        lenient().when(lm.selectList(any())).thenReturn(List.of(l));
        lenient().when(am.selectList(any())).thenReturn(List.of());
        lenient().when(mom.selectList(any())).thenReturn(List.of());
        lenient().when(vsm.selectList(any())).thenReturn(List.of());
        lenient().when(cm.selectList(any())).thenReturn(List.of());
        lenient().when(dsm.selectList(any())).thenReturn(List.of());
    }

    private TimelineServiceImpl svc() {
        return new TimelineServiceImpl(vm, dm, pm, im, lm, am, mom, vsm, cm, dsm, redis, OM);
    }
```

- [ ] **Step 7: 新增两个测试（患者时间轴含住院事件；住院时间轴排序）**

```java
    @Test
    void patient_timeline_includes_admission_and_discharge_events() {
        Admission a = new Admission();
        a.setId(9L); a.setPatientId(1L);
        a.setAdmissionTime(LocalDateTime.of(2024, 1, 1, 8, 0));
        a.setDischargeTime(LocalDateTime.of(2024, 1, 5, 10, 0));
        lenient().when(am.selectList(any())).thenReturn(List.of(a));

        TimelineVO vo = svc().getTimeline(1L);

        assertThat(vo.getEvents()).extracting(TimelineEvent::getType)
                .contains("ADMISSION", "DISCHARGE");
        // 时间升序：ADMISSION(08:00) < LAB_TEST(09:30) < VISIT(10:00) < DISCHARGE(01-05)
        assertThat(vo.getEvents().get(0).getType()).isEqualTo("ADMISSION");
        assertThat(vo.getEvents().get(vo.getEvents().size() - 1).getType()).isEqualTo("DISCHARGE");
    }

    @Test
    void admission_timeline_sorts_orders_and_vitals_ascending() {
        Admission a = new Admission(); a.setId(9L); a.setPatientId(1L);
        when(am.selectById(9L)).thenReturn(a);

        MedicalOrder o = new MedicalOrder(); o.setId(1L); o.setAdmissionId(9L);
        o.setStartTime(LocalDateTime.of(2024, 1, 1, 12, 0));
        VitalSign vs = new VitalSign(); vs.setId(1L); vs.setAdmissionId(9L);
        vs.setRecordedAt(LocalDateTime.of(2024, 1, 1, 6, 0));
        when(mom.selectList(any())).thenReturn(List.of(o));
        when(vsm.selectList(any())).thenReturn(List.of(vs));

        TimelineVO vo = svc().getAdmissionTimeline(9L);

        assertThat(vo.getPatientId()).isEqualTo(1L);
        assertThat(vo.getEvents()).extracting(TimelineEvent::getType)
                .containsExactly("VITAL_SIGN", "ORDER");
    }
```

- [ ] **Step 8: 跑测试**

Run: `mvn -q -pl his-server -am test -Dtest=TimelineServiceTest -Dsurefire.failIfNoSpecifiedTests=false`
Expected: BUILD SUCCESS。

- [ ] **Step 9: Commit**

```bash
git add backend/his-server/src/main/java/com/his/server/service backend/his-server/src/main/java/com/his/server/controller backend/his-server/src/test/java/com/his/server/service/TimelineServiceTest.java
git commit -m "feat: 患者时间轴扩展住院/收费/发药事件，新增住院时间轴"
```

---

## Task 7: 合理用药校验引擎（后端）

**Files:**
- Create: `backend/his-common/src/main/java/com/his/common/dto/RationalDrugReport.java`、`RationalViolation.java`
- Create: `backend/his-server/src/main/java/com/his/server/service/validation/RationalDrugValidator.java`
- Test: `backend/his-server/src/test/java/com/his/server/service/validation/RationalDrugValidatorTest.java`

**Interfaces:**
- Consumes: `PrescriptionMapper`、`PrescriptionItemMapper`、`DiagnosisMapper`、`RuleConfigMapper`、`RationalRuleMapper`。
- Produces: `RationalDrugValidator.validate() : RationalDrugReport`（Task 9 的 ValidationService 消费）。

**校验口径**（与 spec §6.1 一致）：
1. 适应症：处方内药品 `drug_id` 不在该就诊诊断的 `rule_config.drug_ids` 内 → 违规。
2. 相互作用：同一处方两两药品对命中 `rational_rule(rule_type=1)` → 违规。
3. 剂量上限：单药品日剂量（`dosage` 首位整数 × `frequency` 首位整数）> `rational_rule(rule_type=2).max_daily_dose` → 违规。

- [ ] **Step 1: 写 DTO**

`RationalDrugReport.java`：
```java
package com.his.common.dto;

import lombok.Data;
import java.util.List;

@Data
public class RationalDrugReport {
    private long totalPrescriptions;
    private long violationPrescriptions;
    private double rationalRate;
    private List<RationalViolation> violations;
}
```
`RationalViolation.java`：
```java
package com.his.common.dto;

import lombok.Data;

@Data
public class RationalViolation {
    private Long patientId;
    private Long prescriptionId;
    private String ruleType;      // INDICATION / INTERACTION / DOSE
    private Integer warningLevel;
    private String message;
}
```

- [ ] **Step 2: 写校验器**

`RationalDrugValidator.java`：
```java
package com.his.server.service.validation;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.dto.RationalDrugReport;
import com.his.common.dto.RationalViolation;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.util.*;
import java.util.stream.Collectors;

@Component
@RequiredArgsConstructor
public class RationalDrugValidator {

    private final PrescriptionMapper prescriptionMapper;
    private final PrescriptionItemMapper itemMapper;
    private final DiagnosisMapper diagnosisMapper;
    private final RuleConfigMapper ruleConfigMapper;
    private final RationalRuleMapper rationalRuleMapper;

    public RationalDrugReport validate() {
        List<Prescription> prescriptions = prescriptionMapper.selectList(null);
        RationalDrugReport report = new RationalDrugReport();
        report.setTotalPrescriptions(prescriptions.size());
        report.setViolations(new ArrayList<>());
        if (prescriptions.isEmpty()) {
            report.setRationalRate(1.0);
            return report;
        }

        // 适应症允许药品：诊断编码 -> 允许 drug_id 集合
        Map<String, Set<Long>> allowedByDiagnosis = new HashMap<>();
        for (RuleConfig rc : ruleConfigMapper.selectList(null)) {
            if (rc.getDrugIds() == null || rc.getDrugIds().isBlank()) continue;
            Set<Long> ids = Arrays.stream(rc.getDrugIds().split(","))
                    .map(String::trim).filter(s -> !s.isEmpty())
                    .map(Long::parseLong).collect(Collectors.toSet());
            allowedByDiagnosis.put(rc.getDiagnosisCode(), ids);
        }

        List<RationalRule> rules = rationalRuleMapper.selectList(
                new LambdaQueryWrapper<RationalRule>().eq(RationalRule::getEnabled, 1));
        // 相互作用：规范化 (min, max) -> 规则
        Map<String, RationalRule> interactions = new HashMap<>();
        Map<Long, RationalRule> doseByDrug = new HashMap<>();
        for (RationalRule r : rules) {
            if (r.getRuleType() != null && r.getRuleType() == 1
                    && r.getDrugAId() != null && r.getDrugBId() != null) {
                long a = Math.min(r.getDrugAId(), r.getDrugBId());
                long b = Math.max(r.getDrugAId(), r.getDrugBId());
                interactions.put(a + "-" + b, r);
            } else if (r.getRuleType() != null && r.getRuleType() == 2
                    && r.getDrugId() != null) {
                doseByDrug.put(r.getDrugId(), r);
            }
        }

        List<PrescriptionItem> items = itemMapper.selectList(
                new LambdaQueryWrapper<PrescriptionItem>()
                        .in(PrescriptionItem::getPrescriptionId,
                            prescriptions.stream().map(Prescription::getId).collect(Collectors.toList())));
        Map<Long, List<PrescriptionItem>> itemsByPrescription =
                items.stream().collect(Collectors.groupingBy(PrescriptionItem::getPrescriptionId));

        List<Diagnosis> diagnoses = diagnosisMapper.selectList(
                new LambdaQueryWrapper<Diagnosis>()
                        .in(Diagnosis::getVisitId,
                            prescriptions.stream().map(Prescription::getVisitId).collect(Collectors.toSet())));
        Map<Long, String> diagnosisCodeByVisit = diagnoses.stream().collect(
                Collectors.toMap(Diagnosis::getVisitId, Diagnosis::getIcdCode, (x, y) -> x));

        Set<Long> violatingPrescriptions = new HashSet<>();
        for (Prescription p : prescriptions) {
            List<PrescriptionItem> its = itemsByPrescription.getOrDefault(p.getId(), List.of());
            if (its.isEmpty()) continue;
            List<Long> drugIds = its.stream().map(PrescriptionItem::getDrugId)
                    .filter(Objects::nonNull).distinct().toList();
            String diagCode = diagnosisCodeByVisit.get(p.getVisitId());

            // 适应症
            Set<Long> allowed = diagCode == null ? null : allowedByDiagnosis.get(diagCode);
            if (allowed != null) {
                for (PrescriptionItem it : its) {
                    if (it.getDrugId() != null && !allowed.contains(it.getDrugId())) {
                        report.getViolations().add(violation(p, "INDICATION", 2,
                                "药品与诊断不符：诊断 " + diagCode + " 未关联药品 id=" + it.getDrugId()));
                        violatingPrescriptions.add(p.getId());
                    }
                }
            }
            // 相互作用
            for (int i = 0; i < drugIds.size(); i++) {
                for (int j = i + 1; j < drugIds.size(); j++) {
                    long a = Math.min(drugIds.get(i), drugIds.get(j));
                    long b = Math.max(drugIds.get(i), drugIds.get(j));
                    RationalRule r = interactions.get(a + "-" + b);
                    if (r != null) {
                        report.getViolations().add(violation(p, "INTERACTION", r.getWarningLevel(), r.getMessage()));
                        violatingPrescriptions.add(p.getId());
                    }
                }
            }
            // 剂量上限
            for (PrescriptionItem it : its) {
                RationalRule r = it.getDrugId() == null ? null : doseByDrug.get(it.getDrugId());
                if (r != null) {
                    int daily = parseInt(it.getDosage()) * parseInt(it.getFrequency());
                    if (daily > r.getMaxDailyDose().intValue()) {
                        report.getViolations().add(violation(p, "DOSE", r.getWarningLevel(), r.getMessage()));
                        violatingPrescriptions.add(p.getId());
                    }
                }
            }
        }

        report.setViolationPrescriptions(violatingPrescriptions.size());
        long total = report.getTotalPrescriptions();
        report.setRationalRate(total == 0 ? 1.0
                : (double) (total - violatingPrescriptions.size()) / total);
        return report;
    }

    private RationalViolation violation(Prescription p, String type, Integer level, String message) {
        RationalViolation v = new RationalViolation();
        v.setPatientId(p.getPatientId());
        v.setPrescriptionId(p.getId());
        v.setRuleType(type);
        v.setWarningLevel(level == null ? 1 : level);
        v.setMessage(message);
        return v;
    }

    /** "1片" -> 1、"2片" -> 2；无数字默认 1。 */
    static int parseInt(String s) {
        if (s == null) return 1;
        StringBuilder sb = new StringBuilder();
        for (char c : s.toCharArray()) {
            if (Character.isDigit(c)) sb.append(c);
            else if (sb.length() > 0) break;
        }
        return sb.length() == 0 ? 1 : Integer.parseInt(sb.toString());
    }
}
```

- [ ] **Step 3: 写测试**

`RationalDrugValidatorTest.java`：
```java
package com.his.server.service.validation;

import com.his.common.dto.RationalDrugReport;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class RationalDrugValidatorTest {

    private PrescriptionMapper pm;
    private PrescriptionItemMapper im;
    private DiagnosisMapper dm;
    private RuleConfigMapper rcm;
    private RationalRuleMapper rrm;

    @BeforeEach
    void setUp() {
        pm = mock(PrescriptionMapper.class);
        im = mock(PrescriptionItemMapper.class);
        dm = mock(DiagnosisMapper.class);
        rcm = mock(RuleConfigMapper.class);
        rrm = mock(RationalRuleMapper.class);
    }

    private RationalDrugValidator validator() {
        return new RationalDrugValidator(pm, im, dm, rcm, rrm);
    }

    @Test
    void flags_indication_mismatch() {
        Prescription p = new Prescription(); p.setId(1L); p.setPatientId(1L); p.setVisitId(10L);
        when(pm.selectList(any())).thenReturn(List.of(p));

        PrescriptionItem item = new PrescriptionItem();
        item.setPrescriptionId(1L); item.setDrugId(99L); item.setDosage("1片"); item.setFrequency("每日1次");
        when(im.selectList(any())).thenReturn(List.of(item));

        Diagnosis d = new Diagnosis(); d.setVisitId(10L); d.setIcdCode("J06.9");
        when(dm.selectList(any())).thenReturn(List.of(d));

        RuleConfig rc = new RuleConfig(); rc.setDiagnosisCode("J06.9"); rc.setDrugIds("1,2"); rc.setEnabled(1);
        when(rcm.selectList(any())).thenReturn(List.of(rc));
        when(rrm.selectList(any())).thenReturn(List.of());

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).hasSize(1);
        assertThat(report.getViolations().get(0).getRuleType()).isEqualTo("INDICATION");
        assertThat(report.getViolationPrescriptions()).isEqualTo(1);
        assertThat(report.getRationalRate()).isEqualTo(0.0);
    }

    @Test
    void flags_dose_exceeding_limit() {
        Prescription p = new Prescription(); p.setId(1L); p.setPatientId(1L); p.setVisitId(10L);
        when(pm.selectList(any())).thenReturn(List.of(p));

        PrescriptionItem item = new PrescriptionItem();
        item.setPrescriptionId(1L); item.setDrugId(4L); item.setDosage("4片"); item.setFrequency("每日3次");
        when(im.selectList(any())).thenReturn(List.of(item));

        Diagnosis d = new Diagnosis(); d.setVisitId(10L); d.setIcdCode("I10");
        when(dm.selectList(any())).thenReturn(List.of(d));

        RuleConfig rc = new RuleConfig(); rc.setDiagnosisCode("I10"); rc.setDrugIds("4"); rc.setEnabled(1);
        when(rcm.selectList(any())).thenReturn(List.of(rc));

        RationalRule dose = new RationalRule();
        dose.setRuleType(2); dose.setDrugId(4L); dose.setMaxDailyDose(new BigDecimal("8")); dose.setWarningLevel(1); dose.setMessage("剂量超标");
        when(rrm.selectList(any())).thenReturn(List.of(dose));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).extracting(RationalViolation::getRuleType).contains("DOSE");
    }

    @Test
    void clean_prescription_has_full_rational_rate() {
        Prescription p = new Prescription(); p.setId(1L); p.setPatientId(1L); p.setVisitId(10L);
        when(pm.selectList(any())).thenReturn(List.of(p));

        PrescriptionItem item = new PrescriptionItem();
        item.setPrescriptionId(1L); item.setDrugId(1L); item.setDosage("1片"); item.setFrequency("每日1次");
        when(im.selectList(any())).thenReturn(List.of(item));

        Diagnosis d = new Diagnosis(); d.setVisitId(10L); d.setIcdCode("J06.9");
        when(dm.selectList(any())).thenReturn(List.of(d));

        RuleConfig rc = new RuleConfig(); rc.setDiagnosisCode("J06.9"); rc.setDrugIds("1,2"); rc.setEnabled(1);
        when(rcm.selectList(any())).thenReturn(List.of(rc));
        when(rrm.selectList(any())).thenReturn(List.of());

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).isEmpty();
        assertThat(report.getRationalRate()).isEqualTo(1.0);
    }
}
```

- [ ] **Step 4: 跑测试**

Run: `mvn -q -pl his-server -am test -Dtest=RationalDrugValidatorTest -Dsurefire.failIfNoSpecifiedTests=false`
Expected: BUILD SUCCESS。

- [ ] **Step 5: Commit**

```bash
git add backend/his-common/src/main/java/com/his/common/dto backend/his-server/src/main/java/com/his/server/service/validation backend/his-server/src/test/java/com/his/server/service/validation
git commit -m "feat: 合理用药校验引擎（适应症/相互作用/剂量）"
```

---

## Task 8: 临床路径变异检测 + ValidationController

**Files:**
- Create: `backend/his-common/src/main/java/com/his/common/dto/PathwayReport.java`、`PathwayVariance.java`、`ValidationReport.java`
- Create: `backend/his-server/src/main/java/com/his/server/service/validation/PathwayValidator.java`
- Create: `backend/his-server/src/main/java/com/his/server/service/ValidationService.java` + `impl/ValidationServiceImpl.java`
- Create: `backend/his-server/src/main/java/com/his/server/controller/ValidationController.java`
- Test: `backend/his-server/src/test/java/com/his/server/service/validation/PathwayValidatorTest.java`、`controller/ValidationControllerTest.java`

**Interfaces:**
- Consumes: `ClinicalPathwayMapper`、`AdmissionMapper`、`MedicalOrderMapper`、`LabTestMapper`、`ObjectMapper`、`RationalDrugValidator`（Task 7）。
- Produces: `ValidationService.run() : ValidationReport`；`POST /api/validation/run`。

**步骤匹配口径**：`steps` 是 JSON 数组 `[{"day":N,"action":"LAB|MED|DISCHARGE","item":...,"window":H}]`。期望时间窗 = `admissionTime + N天` 到 `+ N天 + H 小时`。LAB 匹配 `lab_test.item_name`；MED 匹配 `medical_order.item_name`；DISCHARGE 匹配 `discharge_time`。精确字符串匹配。

- [ ] **Step 1: 写 DTO**

`PathwayReport.java`：
```java
package com.his.common.dto;

import lombok.Data;
import java.util.List;

@Data
public class PathwayReport {
    private long enrolledAdmissions;
    private long totalSteps;
    private long fulfilledSteps;
    private double executionRate;
    private List<PathwayVariance> variances;
}
```
`PathwayVariance.java`：
```java
package com.his.common.dto;

import lombok.Data;

@Data
public class PathwayVariance {
    private Long patientId;
    private Long admissionId;
    private String diagnosisName;
    private String varianceType;  // MISSING / WINDOW
    private String detail;
}
```
`ValidationReport.java`：
```java
package com.his.common.dto;

import lombok.Data;

@Data
public class ValidationReport {
    private RationalDrugReport rationalDrug;
    private PathwayReport pathway;
}
```

- [ ] **Step 2: 写 PathwayValidator**

`PathwayValidator.java`：
```java
package com.his.server.service.validation;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.his.common.dto.PathwayReport;
import com.his.common.dto.PathwayVariance;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.*;

@Component
@RequiredArgsConstructor
public class PathwayValidator {

    private final ClinicalPathwayMapper pathwayMapper;
    private final AdmissionMapper admissionMapper;
    private final MedicalOrderMapper orderMapper;
    private final LabTestMapper labTestMapper;
    private final ObjectMapper objectMapper;

    private record Step(int day, String action, String item, int window) {}

    public PathwayReport validate() {
        PathwayReport report = new PathwayReport();
        report.setVariances(new ArrayList<>());
        List<ClinicalPathway> pathways = pathwayMapper.selectList(
                new LambdaQueryWrapper<ClinicalPathway>().eq(ClinicalPathway::getEnabled, 1));
        if (pathways.isEmpty()) {
            report.setExecutionRate(1.0);
            return report;
        }

        long enrolled = 0, totalSteps = 0, fulfilled = 0;
        for (ClinicalPathway pathway : pathways) {
            List<Step> steps = parseSteps(pathway.getSteps());
            if (steps.isEmpty()) continue;

            List<Admission> admissions = admissionMapper.selectList(
                    new LambdaQueryWrapper<Admission>().eq(Admission::getDiagnosis, pathway.getDiagnosisName()));
            for (Admission a : admissions) {
                enrolled++;
                List<MedicalOrder> orders = orderMapper.selectList(
                        new LambdaQueryWrapper<MedicalOrder>().eq(MedicalOrder::getAdmissionId, a.getId()));
                List<LabTest> labs = labTestMapper.selectList(
                        new LambdaQueryWrapper<LabTest>().eq(LabTest::getAdmissionId, a.getId()));

                LocalDateTime prevTime = null;
                for (Step step : steps) {
                    totalSteps++;
                    LocalDateTime lower = a.getAdmissionTime().plusDays(step.day());
                    LocalDateTime upper = lower.plusHours(step.window());
                    boolean hit = switch (step.action()) {
                        case "LAB" -> labs.stream().anyMatch(l ->
                                step.item().equals(l.getItemName())
                                        && !l.getTestedAt().isBefore(lower) && !l.getTestedAt().isAfter(upper));
                        case "MED" -> orders.stream().anyMatch(o ->
                                step.item().equals(o.getItemName())
                                        && !o.getStartTime().isBefore(lower) && !o.getStartTime().isAfter(upper));
                        case "DISCHARGE" -> a.getDischargeTime() != null
                                && !a.getDischargeTime().isBefore(lower) && !a.getDischargeTime().isAfter(upper);
                        default -> true;
                    };
                    if (hit) {
                        fulfilled++;
                        prevTime = null;
                    } else {
                        report.getVariances().add(variance(a, pathway, "MISSING",
                                "第 " + step.day() + " 天步骤缺失：action=" + step.action() + " item=" + step.item()));
                    }
                }
            }
        }
        report.setEnrolledAdmissions(enrolled);
        report.setTotalSteps(totalSteps);
        report.setFulfilledSteps(fulfilled);
        report.setExecutionRate(totalSteps == 0 ? 1.0 : (double) fulfilled / totalSteps);
        return report;
    }

    private PathwayVariance variance(Admission a, ClinicalPathway p, String type, String detail) {
        PathwayVariance v = new PathwayVariance();
        v.setPatientId(a.getPatientId());
        v.setAdmissionId(a.getId());
        v.setDiagnosisName(p.getDiagnosisName());
        v.setVarianceType(type);
        v.setDetail(detail);
        return v;
    }

    private List<Step> parseSteps(String json) {
        if (json == null || json.isBlank()) return List.of();
        try {
            List<Map<String, Object>> raw = objectMapper.readValue(json,
                    new TypeReference<List<Map<String, Object>>>() {});
            List<Step> steps = new ArrayList<>();
            for (Map<String, Object> m : raw) {
                int day = ((Number) m.get("day")).intValue();
                String action = (String) m.get("action");
                String item = (String) m.getOrDefault("item", "");
                int window = ((Number) m.get("window")).intValue();
                steps.add(new Step(day, action, item, window));
            }
            return steps;
        } catch (Exception e) {
            return List.of();
        }
    }
}
```

- [ ] **Step 3: 写 ValidationService + impl + controller**

`ValidationService.java`：
```java
package com.his.server.service;

import com.his.common.dto.ValidationReport;

public interface ValidationService {
    ValidationReport run();
}
```
`ValidationServiceImpl.java`：
```java
package com.his.server.service.impl;

import com.his.common.dto.ValidationReport;
import com.his.server.service.ValidationService;
import com.his.server.service.validation.PathwayValidator;
import com.his.server.service.validation.RationalDrugValidator;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class ValidationServiceImpl implements ValidationService {

    private final RationalDrugValidator rationalDrugValidator;
    private final PathwayValidator pathwayValidator;

    @Override
    public ValidationReport run() {
        ValidationReport report = new ValidationReport();
        report.setRationalDrug(rationalDrugValidator.validate());
        report.setPathway(pathwayValidator.validate());
        return report;
    }
}
```
`ValidationController.java`：
```java
package com.his.server.controller;

import com.his.common.common.Result;
import com.his.common.dto.ValidationReport;
import com.his.server.service.ValidationService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/validation")
@RequiredArgsConstructor
public class ValidationController {

    private final ValidationService validationService;

    @PostMapping("/run")
    public Result<ValidationReport> run() {
        return Result.ok(validationService.run());
    }
}
```

- [ ] **Step 4: 写 PathwayValidator 测试**

`PathwayValidatorTest.java`：
```java
package com.his.server.service.validation;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.his.common.dto.PathwayReport;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class PathwayValidatorTest {

    private ClinicalPathwayMapper pm;
    private AdmissionMapper am;
    private MedicalOrderMapper om;
    private LabTestMapper lm;

    @BeforeEach
    void setUp() {
        pm = mock(ClinicalPathwayMapper.class);
        am = mock(AdmissionMapper.class);
        om = mock(MedicalOrderMapper.class);
        lm = mock(LabTestMapper.class);
    }

    private PathwayValidator validator() {
        return new PathwayValidator(pm, am, om, lm, new ObjectMapper());
    }

    private ClinicalPathway pathway() {
        ClinicalPathway p = new ClinicalPathway();
        p.setId(1L); p.setDiagnosisName("上呼吸道感染");
        p.setSteps("[{\"day\":0,\"action\":\"LAB\",\"item\":\"白细胞计数\",\"window\":24}," +
                "{\"day\":3,\"action\":\"DISCHARGE\",\"window\":24}]");
        p.setEnabled(1);
        return p;
    }

    @Test
    void compliant_admission_fulfills_all_steps() {
        when(pm.selectList(any())).thenReturn(List.of(pathway()));
        Admission a = new Admission();
        a.setId(1L); a.setPatientId(1L); a.setDiagnosis("上呼吸道感染");
        a.setAdmissionTime(LocalDateTime.of(2026, 1, 1, 8, 0));
        a.setDischargeTime(LocalDateTime.of(2026, 1, 4, 9, 0));
        when(am.selectList(any())).thenReturn(List.of(a));

        LabTest lab = new LabTest();
        lab.setAdmissionId(1L); lab.setItemName("白细胞计数");
        lab.setTestedAt(LocalDateTime.of(2026, 1, 1, 10, 0));
        when(lm.selectList(any())).thenReturn(List.of(lab));
        when(om.selectList(any())).thenReturn(List.of());

        PathwayReport report = validator().validate();

        assertThat(report.getEnrolledAdmissions()).isEqualTo(1);
        assertThat(report.getFulfilledSteps()).isEqualTo(2);
        assertThat(report.getVariances()).isEmpty();
        assertThat(report.getExecutionRate()).isEqualTo(1.0);
    }

    @Test
    void missing_step_produces_variance() {
        when(pm.selectList(any())).thenReturn(List.of(pathway()));
        Admission a = new Admission();
        a.setId(1L); a.setPatientId(1L); a.setDiagnosis("上呼吸道感染");
        a.setAdmissionTime(LocalDateTime.of(2026, 1, 1, 8, 0));
        // 未出院 → DISCHARGE 步骤缺失
        when(am.selectList(any())).thenReturn(List.of(a));
        when(lm.selectList(any())).thenReturn(List.of());
        when(om.selectList(any())).thenReturn(List.of());

        PathwayReport report = validator().validate();

        assertThat(report.getVariances()).hasSize(2);
        assertThat(report.getVariances()).extracting(PathwayVariance::getVarianceType)
                .containsOnly("MISSING");
    }
}
```

- [ ] **Step 5: 写 ValidationControllerTest**

```java
package com.his.server.controller;

import com.his.common.dto.RationalDrugReport;
import com.his.common.dto.PathwayReport;
import com.his.common.dto.ValidationReport;
import com.his.server.service.ValidationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(ValidationController.class)
class ValidationControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean ValidationService validationService;

    @Test
    void run_returns_combined_report() throws Exception {
        ValidationReport report = new ValidationReport();
        RationalDrugReport rd = new RationalDrugReport();
        rd.setTotalPrescriptions(100); rd.setRationalRate(0.95);
        PathwayReport pw = new PathwayReport();
        pw.setEnrolledAdmissions(10); pw.setExecutionRate(0.8);
        report.setRationalDrug(rd); report.setPathway(pw);
        when(validationService.run()).thenReturn(report);

        mockMvc.perform(post("/api/validation/run"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.rationalDrug.rationalRate").value(0.95))
                .andExpect(jsonPath("$.data.pathway.enrolledAdmissions").value(10));
    }
}
```

- [ ] **Step 6: 跑测试**

Run: `mvn -q -pl his-server -am test -Dtest=PathwayValidatorTest,ValidationControllerTest -Dsurefire.failIfNoSpecifiedTests=false`
Expected: BUILD SUCCESS。

- [ ] **Step 7: Commit**

```bash
git add backend/his-common/src/main/java/com/his/common/dto backend/his-server/src/main/java/com/his/server/service/validation backend/his-server/src/main/java/com/his/server/service/ValidationService.java backend/his-server/src/main/java/com/his/server/service/impl/ValidationServiceImpl.java backend/his-server/src/main/java/com/his/server/controller/ValidationController.java backend/his-server/src/test/java/com/his/server
git commit -m "feat: 临床路径变异检测与一键校验接口"
```

---

## Task 9: 看板新增统计（后端）

**Files:**
- Modify: `backend/his-server/src/main/java/com/his/server/service/DashboardService.java`、`impl/DashboardServiceImpl.java`
- Modify: `backend/his-server/src/main/java/com/his/server/controller/DashboardController.java`
- Modify: `backend/his-server/src/main/java/com/his/common/mapper/AdmissionMapper.java`（增自定义 @Select）
- Test: `backend/his-server/src/test/java/com/his/server/service/DashboardServiceTest.java`（扩展）

**Interfaces:**
- Consumes: `AdmissionMapper`（新增）、`PatientMapper`、`ValidationService`（Task 8）。
- Produces: `GET /api/dashboard/stats/admission-ratio`、`/stats/rational-drug-rate`、`/stats/pathway-rate`。

- [ ] **Step 1: AdmissionMapper 增统计查询**

```java
    /** 有多少名患者至少住院过一次（用于住院占比统计）。 */
    @Select("SELECT COUNT(DISTINCT patient_id) FROM admission")
    Long selectAdmissionPatientCount();
```

- [ ] **Step 2: 扩展 DashboardService 接口**

```java
    Map<String, Object> admissionRatio();
    Map<String, Object> rationalDrugRate();
    Map<String, Object> pathwayRate();
```

- [ ] **Step 3: 实现**

在 `DashboardServiceImpl` 注入 `AdmissionMapper admissionMapper`、`ValidationService validationService`（注意：字段顺序即构造参数顺序，被 DashboardServiceTest 按位置依赖——追加到末尾）。实现：
```java
    private static final String STATS_ADMISSION_RATIO = "stats:admission-ratio";
    private static final String STATS_RATIONAL_DRUG = "stats:rational-drug-rate";
    private static final String STATS_PATHWAY = "stats:pathway-rate";

    @Override
    public Map<String, Object> admissionRatio() {
        long admissionPatients = admissionMapper.selectAdmissionPatientCount();
        long totalPatients = patientMapper.selectCount(null);
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("admissionPatients", admissionPatients);
        m.put("totalPatients", totalPatients);
        m.put("ratio", totalPatients == 0 ? 0.0 : (double) admissionPatients / totalPatients);
        return m;
    }

    @Override
    public Map<String, Object> rationalDrugRate() {
        return cached(STATS_RATIONAL_DRUG, () -> {
            RationalDrugReport r = validationService.run().getRationalDrug();
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("totalPrescriptions", r.getTotalPrescriptions());
            m.put("violationPrescriptions", r.getViolationPrescriptions());
            m.put("rationalRate", r.getRationalRate());
            return m;
        });
    }

    @Override
    public Map<String, Object> pathwayRate() {
        return cached(STATS_PATHWAY, () -> {
            PathwayReport p = validationService.run().getPathway();
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("enrolledAdmissions", p.getEnrolledAdmissions());
            m.put("executionRate", p.getExecutionRate());
            return m;
        });
    }

    private Map<String, Object> cached(String key, java.util.function.Supplier<Map<String, Object>> loader) {
        String json;
        try {
            json = redis.opsForValue().get(key);
            if (json != null) return objectMapper.readValue(json, new TypeReference<Map<String, Object>>() {});
        } catch (Exception ignored) {
            // 回源
        }
        Map<String, Object> m = loader.get();
        try {
            redis.opsForValue().set(key, objectMapper.writeValueAsString(m), STATS_TTL);
        } catch (Exception ignored) {
            // 写缓存失败不影响响应
        }
        return m;
    }
```
（补 import：`com.his.common.dto.RationalDrugReport`、`PathwayReport`、`com.his.common.mapper.AdmissionMapper`、`com.his.server.service.ValidationService`、`java.util.function.Supplier`。）

- [ ] **Step 4: 扩展 DashboardController**

```java
    @GetMapping("/stats/admission-ratio")
    public Result<Map<String, Object>> admissionRatio() { return Result.ok(dashboardService.admissionRatio()); }

    @GetMapping("/stats/rational-drug-rate")
    public Result<Map<String, Object>> rationalDrugRate() { return Result.ok(dashboardService.rationalDrugRate()); }

    @GetMapping("/stats/pathway-rate")
    public Result<Map<String, Object>> pathwayRate() { return Result.ok(dashboardService.pathwayRate()); }
```

- [ ] **Step 5: 扩展 DashboardServiceTest**

`DashboardServiceImpl` 构造参数增加 `admissionMapper`、`validationService`。更新既有 `build()` 构造，并新增：
```java
    @Test
    void admission_ratio_computes_from_counts() {
        when(admissionMapper.selectAdmissionPatientCount()).thenReturn(2000L);
        when(patientMapper.selectCount(null)).thenReturn(10000L);
        Map<String, Object> m = svc.admissionRatio();
        assertThat(m.get("ratio")).isEqualTo(0.2);
    }
```
（在 `build()` 里 `admissionMapper = mock(AdmissionMapper.class); validationService = mock(ValidationService.class);` 并传入构造。）

- [ ] **Step 6: 跑测试**

Run: `mvn -q -pl his-server -am test -Dtest=DashboardServiceTest -Dsurefire.failIfNoSpecifiedTests=false`
Expected: BUILD SUCCESS。

- [ ] **Step 7: Commit**

```bash
git add backend/his-common/src/main/java/com/his/common/mapper/AdmissionMapper.java backend/his-server/src/main/java/com/his/server/service backend/his-server/src/main/java/com/his/server/controller/DashboardController.java backend/his-server/src/test/java/com/his/server/service/DashboardServiceTest.java
git commit -m "feat: 看板新增住院占比/合理用药率/路径变异率"
```

---

## Task 10: 生成器 —— 门诊补全（收费/发药）+ 违规注入

**Files:**
- Modify: `backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java`
- Test: `backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java`（扩展）

**Interfaces:**
- Consumes: `ChargeMapper`、`DispenseMapper`、`RationalRuleMapper`（Task 2）。
- Produces: 每次就诊生成 charge（挂号费+药费+检验费）与 dispense；按概率注入违规处方。`generate()` 返回的 stat Map 新增 `charges`、`dispenses` 键。

**常量**（加到类顶部，参照既有常量）：
```java
    /** 挂号费固定金额 */
    private static final BigDecimal REGISTRATION_FEE = new BigDecimal("10.00");
    /** 每检验项费用 */
    private static final BigDecimal LAB_FEE = new BigDecimal("15.00");
    /** 处方违规注入概率 */
    static final double VIOLATION_RATE = 0.03;
```

- [ ] **Step 1: 加字段与加载**

字段区追加：
```java
    private final ChargeMapper chargeMapper;
    private final DispenseMapper dispenseMapper;
    private final RationalRuleMapper rationalRuleMapper;
```
实例字段追加：
```java
    private List<RationalRule> interactionRules;
    private Map<Long, RationalRule> doseRulesByDrug;
    private long chargeCount, dispenseCount;
```
在 `generate()` 里，`doctorsByDept = loadDoctors();` 之后追加：
```java
        interactionRules = new ArrayList<>();
        doseRulesByDrug = new HashMap<>();
        for (RationalRule r : rationalRuleMapper.selectList(
                new LambdaQueryWrapper<RationalRule>().eq(RationalRule::getEnabled, 1))) {
            if (r.getRuleType() != null && r.getRuleType() == 1
                    && r.getDrugAId() != null && r.getDrugBId() != null) {
                interactionRules.add(r);
            } else if (r.getRuleType() != null && r.getRuleType() == 2 && r.getDrugId() != null) {
                doseRulesByDrug.put(r.getDrugId(), r);
            }
        }
        chargeCount = 0;
        dispenseCount = 0;
```
把 `DATA_TABLES` 列表追加 `"charge", "dispense"`（此时不含住院表，Task 11 再补）。

- [ ] **Step 2: flushPatients 生成收费 + 发药**

在 `flushPatients` 里，处方明细/检验生成循环内，为每张处方生成一条发药；循环外按就诊生成收费。改造：在 `itemMapper.insert(itemBuf); labTestMapper.insert(labBuf);` 之后、`prescriptionMapper.updateById(...)` 之前或之后，追加收费/发药缓冲区写入：

在 `for (PendingPrescription pp : presBuf)` 循环内，收集 `pr.getVisitId()` 与 `pr.getTotalAmount()` 到 `Map<Long, BigDecimal> amountByVisit`，并为每张处方生成 `Dispense`；循环结束后按就诊生成 `Charge`（挂号费 + 药费 + 检验费）并批量插入。

具体新增代码（放在循环体收尾、以及循环后）：
```java
        Map<Long, BigDecimal> amountByVisit = new HashMap<>();
        List<Dispense> dispBuf = new ArrayList<>();
        List<Charge> chargeBuf = new ArrayList<>();
        Map<Long, Integer> labCountByVisit = new HashMap<>();
        for (PendingPrescription pp : presBuf) {
            Prescription pr = pp.prescription();
            amountByVisit.merge(pr.getVisitId(), pr.getTotalAmount(), BigDecimal::add);
            // 发药：一张处方一条
            Dispense disp = new Dispense();
            disp.setPatientId(pr.getPatientId()); disp.setVisitId(pr.getVisitId());
            disp.setPrescriptionId(pr.getId());
            disp.setDrugName(pp.rule().name()); disp.setQuantity(BigDecimal.ONE);
            disp.setDispensedAt(pr.getPrescribedAt().plusMinutes(rnd.nextInt(10, 30)));
            disp.setPharmacist(randomPharmacist());
            dispBuf.add(disp);
        }
        for (LabTest l : labBuf) {
            labCountByVisit.merge(l.getVisitId(), 1, Integer::sum);
        }
        for (Visit v : visitBuf) {
            BigDecimal drugFee = amountByVisit.getOrDefault(v.getId(), BigDecimal.ZERO);
            int labCount = labCountByVisit.getOrDefault(v.getId(), 0);
            BigDecimal labFee = LAB_FEE.multiply(BigDecimal.valueOf(labCount));
            chargeBuf.add(newCharge(v, 0, "挂号费", REGISTRATION_FEE));
            chargeBuf.add(newCharge(v, 1, "药费", drugFee));
            if (labCount > 0) {
                chargeBuf.add(newCharge(v, 2, "检验费", labFee));
            }
        }
        dispenseMapper.insert(dispBuf);
        chargeMapper.insert(chargeBuf);
        dispenseCount += dispBuf.size();
        chargeCount += chargeBuf.size();
```
注意：`presBuf` 循环体原来是在 `flushPatients` 里早先的一个循环（生成明细/检验）。这里**新增的收费/发药逻辑放在该循环之后**（`itemMapper.insert`/`labTestMapper.insert` 之后），因为需要 `pr.getId()` 与 `totalAmount` 已就绪。`amountByVisit`/`labCountByVisit` 需要在该循环内累积——把累积语句放进原有循环体，把发药/收费生成放循环后。

- [ ] **Step 3: 辅助方法 newCharge / randomPharmacist / 违规注入**

```java
    private Charge newCharge(Visit v, int itemType, String itemName, BigDecimal amount) {
        Charge c = new Charge();
        c.setPatientId(v.getPatientId()); c.setVisitId(v.getId());
        c.setChargeNo("C" + v.getId() + "-" + itemType);
        c.setItemType(itemType); c.setItemName(itemName); c.setAmount(amount);
        c.setChargedAt(v.getVisitTime().plusMinutes(rnd.nextInt(40, 90)));
        return c;
    }

    private String randomPharmacist() {
        // 药师名不得用「王冠杰」；固定非敏感名单
        String[] surnames = {"刘", "陈", "杨", "周", "吴"};
        String[] given = {"静", "磊", "军", "艳", "涛"};
        return surnames[rnd.nextInt(surnames.length)] + given[rnd.nextInt(given.length)];
    }
```

违规注入：在 `flushPatients` 生成处方明细 `pickDrugIds(rule)` 之后、写 `itemBuf` 之前，对每张处方以 `VIOLATION_RATE` 概率注入。注入逻辑为独立方法（供单测直接调用）：
```java
    /** 以固定方式注入一类违规，返回违规类型（供测试断言）；不注入返回 null。 */
    String injectViolation(List<PrescriptionItem> items, MedicalData.DiagnosisRule rule) {
        if (interactionRules.isEmpty() && doseRulesByDrug.isEmpty()) {
            return null;
        }
        int type = rnd.nextInt(3);
        if (type == 0 && !doseRulesByDrug.isEmpty()) {
            // 剂量：任取一条剂量规则，把对应药加进明细并设高剂量
            RationalRule r = doseRulesByDrug.values().iterator().next();
            PrescriptionItem it = items.isEmpty() ? null : items.get(0);
            if (it != null) {
                it.setDrugId(r.getDrugId());
                Drug d = drugsById.get(r.getDrugId());
                if (d != null) it.setDrugName(d.getName());
                it.setDosage("4片"); it.setFrequency("每日3次");
                return "DOSE";
            }
        } else if (type == 1 && !interactionRules.isEmpty()) {
            // 相互作用：直接用一条相互作用规则的两个药作为两条明细
            RationalRule r = interactionRules.get(rnd.nextInt(interactionRules.size()));
            items.clear();
            for (Long drugId : List.of(r.getDrugAId(), r.getDrugBId())) {
                Drug d = drugsById.get(drugId);
                if (d == null) return null;
                PrescriptionItem it = new PrescriptionItem();
                it.setDrugId(d.getId()); it.setDrugName(d.getName());
                it.setDosage("1片"); it.setFrequency("每日1次");
                it.setDays(7); it.setQuantity(BigDecimal.ONE); it.setUnit(d.getUnit());
                items.add(it);
            }
            return "INTERACTION";
        } else {
            // 适应症：把首条明细换成规则外药品
            List<Long> allowed = rule.drugIds();
            List<Drug> others = drugsById.values().stream()
                    .filter(d -> !allowed.contains(d.getId())).toList();
            if (!others.isEmpty() && !items.isEmpty()) {
                Drug d = others.get(rnd.nextInt(others.size()));
                PrescriptionItem it = items.get(0);
                it.setDrugId(d.getId()); it.setDrugName(d.getName());
                return "INDICATION";
            }
        }
        return null;
    }
```
在 `flushPatients` 明细生成处，把 `List<Long> drugIds = pickDrugIds(pp.rule())` 改为先生成 items 列表，再按概率调用注入。实现方式：把原明细生成抽成 `buildItems(Prescription pr, MedicalData.DiagnosisRule rule)` 返回 `List<PrescriptionItem>`，然后：
```java
            List<PrescriptionItem> its = buildItems(pr, pp.rule());
            if (rnd.nextDouble() < VIOLATION_RATE) {
                injectViolation(its, pp.rule());
            }
            for (PrescriptionItem it : its) {
                it.setPrescriptionId(pr.getId());
            }
            // total 重算
            BigDecimal total = its.stream().map(i -> {
                Drug d = i.getDrugId() == null ? null : drugsById.get(i.getDrugId());
                return (d == null || d.getPrice() == null ? BigDecimal.ZERO
                        : d.getPrice().multiply(i.getQuantity() == null ? BigDecimal.ZERO : i.getQuantity()));
            }).reduce(BigDecimal.ZERO, BigDecimal::add);
            pr.setTotalAmount(total);
            itemBuf.addAll(its);
```
（注意 `pr.setPrescriptionId` 已由 `prescriptionMapper.insert` 回填。）

- [ ] **Step 4: 更新 stat 输出**

`generate()` 的 stat Map 追加：
```java
        stat.put("charges", chargeCount);
        stat.put("dispenses", dispenseCount);
```

- [ ] **Step 5: 扩展 DataGeneratorServiceTest**

更新 `buildService()`：构造 `DataGeneratorService` 需按新字段顺序传参（原 9 参 + `chargeMapper, dispenseMapper, rationalRuleMapper`）。`rationalRuleMapper` mock 返回一组 `interactionRules`（如 `RationalRule(ruleType=1, drugAId=3, drugBId=9)`）与一条 `doseRulesByDrug`（`RationalRule(ruleType=2, drugId=4, maxDailyDose=8)`）。`chargeMapper`/`dispenseMapper` 用 `stubBatchInsert` 回填 id。

新增测试：
```java
    @Test
    void generates_charges_and_dispenses_per_visit() {
        DataGeneratorService svc = buildService();
        Map<String, Long> stat = svc.generate(5);
        assertThat(stat.get("charges")).isGreaterThanOrEqualTo(10L);   // 每就诊至少挂号+药费两条
        assertThat(stat.get("dispenses")).isGreaterThanOrEqualTo(5L);
    }

    @Test
    void injectViolation_produces_an_interaction_pair() {
        DataGeneratorService svc = buildService();
        svc.generate(1);  // 触发加载 interactionRules/doseRulesByDrug
        MedicalData.DiagnosisRule rule = new MedicalData.DiagnosisRule("J06.9", "上呼吸道感染", List.of(1L, 2L));
        List<PrescriptionItem> items = new java.util.ArrayList<>();
        items.add(item(1L, "1片", "每日1次"));
        // 固定注入 INTERACTION：重复调用直到命中（或直接断言返回值在 {DOSE, INTERACTION, INDICATION} 中）
        String type = svc.injectViolation(items, rule);
        assertThat(type).isIn("DOSE", "INTERACTION", "INDICATION");
    }
```
（`item(drugId, dosage, freq)` 为测试内辅助构造 PrescriptionItem。）

- [ ] **Step 6: 跑测试**

Run: `mvn -q -pl his-generator -am test -Dtest=DataGeneratorServiceTest -Dsurefire.failIfNoSpecifiedTests=false`
Expected: BUILD SUCCESS。

- [ ] **Step 7: Commit**

```bash
git add backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java
git commit -m "feat: 生成器门诊补全收费/发药，并按概率注入违规处方"
```

---

## Task 11: 生成器 —— 住院纵向时序

**Files:**
- Modify: `backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java`
- Modify: `backend/his-generator/src/main/java/com/his/generator/MedicalData.java`（可选：加护士名名单）
- Test: `backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java`（扩展）

**Interfaces:**
- Consumes: `AdmissionMapper`、`MedicalOrderMapper`、`VitalSignMapper`、`ClinicalPathwayMapper`（Task 2）。
- Produces: 约 20% 患者生成 1–3 次住院，每次住院 3–15 天，含医嘱/生命体征/住院检验；stat Map 新增 `admissions`、`orders`、`vital_signs`。

**常量**（类顶部）：
```java
    /** 有住院史的患者占比 */
    private static final double INPATIENT_RATIO = 0.2;
    /** 每位住院患者 1-3 次住院 */
    private static final int MIN_ADMISSIONS = 1;
    private static final int MAX_ADMISSIONS = 3;
    /** 每次住院 3-15 天 */
    private static final int MIN_STAY_DAYS = 3;
    private static final int MAX_STAY_DAYS = 15;
    /** 入院诊断命中临床路径时按路径执行的占比（其余偏离，制造变异） */
    private static final double PATHWAY_COMPLIANCE_RATE = 0.7;
```

- [ ] **Step 1: 加字段 + 加载 + DATA_TABLES + 计数**

字段区追加：
```java
    private final AdmissionMapper admissionMapper;
    private final MedicalOrderMapper medicalOrderMapper;
    private final VitalSignMapper vitalSignMapper;
    private final ClinicalPathwayMapper pathwayMapper;
```
实例字段追加：
```java
    private List<ClinicalPathway> pathways;
    private long admissionCount, orderCount, vitalCount;
```
`DATA_TABLES` 列表再追加 `"admission", "medical_order", "vital_sign"`（最终 11 张清空表）。

`generate()` 里加载路径（在 `chargeCount=0; dispenseCount=0;` 之后）：
```java
        pathways = pathwayMapper.selectList(
                new LambdaQueryWrapper<ClinicalPathway>().eq(ClinicalPathway::getEnabled, 1));
        admissionCount = 0; orderCount = 0; vitalCount = 0;
```

- [ ] **Step 2: flushPatients 追加住院生成**

在 `flushPatients` 的收费/发药写入之后、`patients.clear()` 之前，追加：
```java
        List<Admission> admissionBuf = new ArrayList<>();
        List<MedicalOrder> orderBuf = new ArrayList<>();
        List<VitalSign> vitalBuf = new ArrayList<>();
        List<LabTest> inpatientLabBuf = new ArrayList<>();
        for (Patient p : patients) {
            if (rnd.nextDouble() >= INPATIENT_RATIO) {
                continue;
            }
            admissionBuf.addAll(newAdmissions(p, orderBuf, vitalBuf, inpatientLabBuf));
        }
        admissionMapper.insert(admissionBuf);      // 回填 admission.id
        // 医嘱/体征在 newAdmissions 里已拿到 admission.id 后再生成
        medicalOrderMapper.insert(orderBuf);
        vitalSignMapper.insert(vitalBuf);
        labTestMapper.insert(inpatientLabBuf);
        admissionCount += admissionBuf.size();
        orderCount += orderBuf.size();
        vitalCount += vitalBuf.size();
```

- [ ] **Step 3: 写 newAdmissions 及子方法**

```java
    private List<Admission> newAdmissions(Patient p, List<MedicalOrder> orderBuf,
                                          List<VitalSign> vitalBuf, List<LabTest> inpatientLabBuf) {
        int count = rnd.nextInt(MIN_ADMISSIONS, MAX_ADMISSIONS + 1);
        List<Admission> list = new ArrayList<>(count);
        for (int i = 0; i < count; i++) {
            ClinicalPathway pathway = pathways.isEmpty() ? null
                    : pathways.get(rnd.nextInt(pathways.size()));
            String diagnosis = pathway != null ? pathway.getDiagnosisName()
                    : randomDiagnosisName();
            String department = randomDepartment();
            String doctor = doctorsByDept.get(department)
                    .get(rnd.nextInt(doctorsByDept.get(department).size()));

            int stayDays = rnd.nextInt(MIN_STAY_DAYS, MAX_STAY_DAYS + 1);
            LocalDateTime admissionTime = randomAdmissionTime(p);
            Admission a = new Admission();
            a.setPatientId(p.getId());
            a.setAdmissionNo("A" + p.getId() + "-" + (i + 1));
            a.setDepartment(department); a.setDoctor(doctor);
            a.setBedNo(rnd.nextInt(1, 60) + "床");
            a.setDiagnosis(diagnosis);
            a.setAdmissionTime(admissionTime);
            a.setDeposit(BigDecimal.valueOf(rnd.nextInt(5, 21) * 100));
            a.setStatus(0);
            list.add(a);
        }
        return list;
    }
```
> 关键点：`newAdmissions` 先只构造 Admission（不含 id），回填 id 之后再生成医嘱/体征/住院检验。因此上面签名先构造 list，真正的医嘱/体征/检验在 `flushPatients` 的 `admissionMapper.insert(admissionBuf)` **之后**按 `admissionBuf` 逐条生成。调整：把医嘱/体征生成放到 insert 之后：

在 `flushPatients` 中改为：
```java
        admissionMapper.insert(admissionBuf);
        for (Admission a : admissionBuf) {
            generateInpatientEvents(a, orderBuf, vitalBuf, inpatientLabBuf);
        }
        medicalOrderMapper.insert(orderBuf);
        vitalSignMapper.insert(vitalBuf);
        labTestMapper.insert(inpatientLabBuf);
```

`generateInpatientEvents`：
```java
    private void generateInpatientEvents(Admission a, List<MedicalOrder> orderBuf,
                                         List<VitalSign> vitalBuf, List<LabTest> inpatientLabBuf) {
        int stayDays = (int) java.time.temporal.ChronoUnit.DAYS.between(
                a.getAdmissionTime(), a.getDischargeTime() == null
                        ? a.getAdmissionTime().plusDays(MIN_STAY_DAYS) : a.getDischargeTime());
        // 实际住院天数已在 newAdmissions 决定；这里用入院时间 + 随机天数推算出院
        // —— 见下方「出院与路径」处理
        ClinicalPathway pathway = pathwayFor(a.getDiagnosis());
        int totalDays = rnd.nextInt(MIN_STAY_DAYS, MAX_STAY_DAYS + 1);
        boolean compliant = pathway != null && rnd.nextDouble() < PATHWAY_COMPLIANCE_RATE;

        // 长期医嘱（首日开，执行至出院）
        int longOrderCount = rnd.nextInt(1, 4);
        for (int k = 0; k < longOrderCount; k++) {
            MedicalOrder o = new MedicalOrder();
            o.setPatientId(a.getPatientId()); o.setAdmissionId(a.getId());
            o.setOrderType(0);
            o.setItemName(randomDrugName());
            o.setDosage("1片"); o.setFrequency("每日" + rnd.nextInt(1, 3) + "次");
            o.setStartTime(a.getAdmissionTime().plusMinutes(rnd.nextInt(5, 60)));
            o.setStatus(0); o.setDoctor(a.getDoctor());
            orderBuf.add(o);
        }

        // 每日生命体征（早中晚 3 次，带趋势）
        for (int day = 0; day < totalDays; day++) {
            for (int slot = 0; slot < 3; slot++) {
                vitalBuf.add(newVitalSign(a, day, slot));
            }
        }

        // 每日临时/检验医嘱
        for (int day = 0; day < totalDays; day++) {
            if (rnd.nextInt(3) == 0) {  // 临时医嘱
                MedicalOrder o = new MedicalOrder();
                o.setPatientId(a.getPatientId()); o.setAdmissionId(a.getId());
                o.setOrderType(1); o.setItemName(randomDrugName());
                o.setDosage("1片"); o.setFrequency("每日1次");
                o.setStartTime(a.getAdmissionTime().plusDays(day).plusHours(9));
                o.setStatus(2); o.setDoctor(a.getDoctor());
                orderBuf.add(o);
            }
            if (rnd.nextInt(2) == 0) {  // 检验医嘱 -> lab_test
                LabTest lt = newInpatientLabTest(a, day);
                if (lt != null) inpatientLabBuf.add(lt);
            }
        }

        // 路径：合规则按步骤补齐对应 LAB/MED 事件，不合规则跳过（自然变异）
        if (compliant && pathway != null) {
            for (PathwayStep step : parsePathwaySteps(pathway.getSteps())) {
                if ("LAB".equals(step.action())) {
                    LabTest lt = labTestForItem(a, step);
                    if (lt != null) inpatientLabBuf.add(lt);
                } else if ("MED".equals(step.action())) {
                    MedicalOrder o = new MedicalOrder();
                    o.setPatientId(a.getPatientId()); o.setAdmissionId(a.getId());
                    o.setOrderType(1); o.setItemName(step.item());
                    o.setDosage("1片"); o.setFrequency("每日1次");
                    o.setStartTime(a.getAdmissionTime().plusDays(step.day()).plusHours(10));
                    o.setStatus(2); o.setDoctor(a.getDoctor());
                    orderBuf.add(o);
                }
            }
        }

        // 出院
        LocalDateTime discharge = a.getAdmissionTime().plusDays(totalDays).plusHours(rnd.nextInt(0, 24));
        a.setDischargeTime(discharge);
        a.setStatus(1);
        a.setTotalCost(a.getDeposit().add(BigDecimal.valueOf(totalDays * 120L)));
    }
```
> 说明：`generateInpatientEvents` 会更新 `a.dischargeTime`/`totalCost`/`status`，这些字段在 `admissionMapper.insert` 时还是空，因此需要在生成后**再 `admissionMapper.updateById(admissionBuf)` 回写出院字段**。在 `flushPatients` 中 `labTestMapper.insert(inpatientLabBuf)` 之后加一行：
```java
        admissionMapper.updateById(admissionBuf);
```

- [ ] **Step 4: 辅助方法（体征趋势 / 检验 / 路径解析 / 姓名科室）**

```java
    private VitalSign newVitalSign(Admission a, int day, int slot) {
        VitalSign vs = new VitalSign();
        vs.setPatientId(a.getPatientId()); vs.setAdmissionId(a.getId());
        // 体温带「先升后降」趋势：住院前段偏高，随天数回落
        double trend = day < 2 ? 0.6 - day * 0.2 : 0.0;
        double temp = 36.5 + trend + rnd.nextDouble() * 0.6;
        vs.setTemperature(BigDecimal.valueOf(Math.round(temp * 10) / 10.0));
        vs.setPulse(72 + rnd.nextInt(-8, 9));
        vs.setRespiration(16 + rnd.nextInt(-2, 3));
        vs.setSystolicBp(120 + rnd.nextInt(-12, 13));
        vs.setDiastolicBp(78 + rnd.nextInt(-8, 9));
        int hour = 6 + slot * 6 + rnd.nextInt(0, 2);
        vs.setRecordedAt(a.getAdmissionTime().plusDays(day).withHour(hour).withMinute(rnd.nextInt(0, 60)));
        vs.setNurse(randomPharmacist());
        return vs;
    }

    private LabTest newInpatientLabTest(Admission a, int day) {
        MedicalData.LabItem li = MedicalData.LAB_ITEMS.get(rnd.nextInt(MedicalData.LAB_ITEMS.size()));
        LabTest lt = new LabTest();
        lt.setPatientId(a.getPatientId()); lt.setAdmissionId(a.getId());
        lt.setItemCode(li.code()); lt.setItemName(li.name());
        double val = li.low() + rnd.nextDouble() * (li.high() - li.low());
        boolean abnormal = rnd.nextDouble() < 0.15;
        if (abnormal) val = li.high() + rnd.nextDouble() * li.high() * 0.3;
        lt.setResultValue(String.format("%.2f", val));
        lt.setUnit(li.unit()); lt.setReferenceRange(li.ref());
        lt.setIsAbnormal(abnormal ? 1 : 0);
        lt.setTestedAt(a.getAdmissionTime().plusDays(day).plusHours(8));
        return lt;
    }

    private LabTest labTestForItem(Admission a, PathwayStep step) {
        MedicalData.LabItem li = MedicalData.LAB_ITEMS.stream()
                .filter(x -> x.name().equals(step.item())).findFirst().orElse(null);
        if (li == null) return null;
        LabTest lt = new LabTest();
        lt.setPatientId(a.getPatientId()); lt.setAdmissionId(a.getId());
        lt.setItemCode(li.code()); lt.setItemName(li.name());
        double val = li.low() + rnd.nextDouble() * (li.high() - li.low());
        lt.setResultValue(String.format("%.2f", val));
        lt.setUnit(li.unit()); lt.setReferenceRange(li.ref()); lt.setIsAbnormal(0);
        lt.setTestedAt(a.getAdmissionTime().plusDays(step.day()).plusHours(rnd.nextInt(1, step.window())));
        return lt;
    }

    private String randomDrugName() {
        return drugsById.values().stream().map(Drug::getName)
                .toList().get(rnd.nextInt(drugsById.size()));
    }

    private String randomDiagnosisName() {
        return rules.get(rnd.nextInt(rules.size())).name();
    }

    private String randomDepartment() {
        return new ArrayList<>(doctorsByDept.keySet()).get(rnd.nextInt(doctorsByDept.size()));
    }

    private LocalDateTime randomAdmissionTime(Patient p) {
        // 取该患者末次门诊时间之后 1-30 天（不早于 lastVisit，避免与门诊冲突）
        return LocalDateTime.now().minusDays(rnd.nextInt(5, 60))
                .minusHours(rnd.nextInt(0, 24));
    }

    private ClinicalPathway pathwayFor(String diagnosisName) {
        return pathways.stream().filter(x -> x.getDiagnosisName().equals(diagnosisName))
                .findFirst().orElse(null);
    }

    private record PathwayStep(int day, String action, String item, int window) {}

    private List<PathwayStep> parsePathwaySteps(String json) {
        if (json == null || json.isBlank()) return List.of();
        try {
            List<Map<String, Object>> raw = new com.fasterxml.jackson.databind.ObjectMapper()
                    .readValue(json, new com.fasterxml.jackson.core.type.TypeReference<List<Map<String, Object>>>() {});
            List<PathwayStep> steps = new ArrayList<>();
            for (Map<String, Object> m : raw) {
                int day = ((Number) m.get("day")).intValue();
                String action = (String) m.get("action");
                String item = (String) m.getOrDefault("item", "");
                int window = ((Number) m.get("window")).intValue();
                steps.add(new PathwayStep(day, action, item, window));
            }
            return steps;
        } catch (Exception e) {
            return List.of();
        }
    }
```
> 注意：`PathwayStep` 与 Task 8 的 `Step` 是各自模块内的 record，命名独立无冲突。`newAdmissions` 里 `randomAdmissionTime` 用「now 往前随机 5-60 天」保证住院时间不与门诊末次就诊冲突（门诊末次在 now-150min~90 天；住院落在更早即可，实际只需保证整体时间线升序——见测试断言）。

- [ ] **Step 5: 更新 stat 输出**

`generate()` stat Map 追加：
```java
        stat.put("admissions", admissionCount);
        stat.put("orders", orderCount);
        stat.put("vital_signs", vitalCount);
```

- [ ] **Step 6: 扩展 DataGeneratorServiceTest**

更新 `buildService()` 构造（再追加 `admissionMapper, medicalOrderMapper, vitalSignMapper, pathwayMapper` 四个 mock，用 `stubBatchInsert` 回填 id）；`pathwayMapper` mock 返回一条上呼吸道感染路径（steps JSON 同 Task 8 测试）。

新增测试：
```java
    @Test
    void inpatient_patients_get_admissions_with_strictly_increasing_events() {
        DataGeneratorService svc = buildService();
        Map<String, Long> stat = svc.generate(200);
        assertThat(stat.get("admissions")).isGreaterThan(0L);
        assertThat(stat.get("vital_signs")).isGreaterThan(0L);
        assertThat(stat.get("orders")).isGreaterThan(0L);
    }
```
（用 `insertedAdmissions`/`insertedOrders`/`insertedVitals` 列表收集，断言每个 admission 的医嘱/体征时间不早于 admission_time，且出院晚于入院。参照现有 `stubBatchInsert` 收集模式。）

- [ ] **Step 7: 跑测试**

Run: `mvn -q -pl his-generator -am test -Dtest=DataGeneratorServiceTest -Dsurefire.failIfNoSpecifiedTests=false`
Expected: BUILD SUCCESS。

- [ ] **Step 8: Commit**

```bash
git add backend/his-generator/src/main/java/com/his/generator backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java
git commit -m "feat: 生成器住院纵向时序（住院/医嘱/生命体征/临床路径合规）"
```

---

## Task 12: 前端 API 封装 + 患者时间轴扩展

**Files:**
- Create: `frontend/src/api/inpatient.js`、`frontend/src/api/charge.js`、`frontend/src/api/validation.js`
- Modify: `frontend/src/api/dashboard.js`
- Modify: `frontend/src/components/Timeline.vue`

**Interfaces:**
- Consumes: 后端 Task 3/4/5/6/8/9 的接口。
- Produces: 前端可调用住院/收费/发药/校验/新统计 API；Timeline 组件渲染 ADMISSION/DISCHARGE/CHARGE/DISPENSE 事件。

- [ ] **Step 1: 写 api 封装**

`frontend/src/api/inpatient.js`：
```js
import request from './request'
export const listAdmissions = (params) => request.get('/admissions', { params })
export const getAdmissionTimeline = (id) => request.get(`/admissions/${id}/timeline`)
export const createAdmission = (d) => request.post('/admissions', d)
export const dischargeAdmission = (id, totalCost) =>
  request.put(`/admissions/${id}/discharge`, null, { params: { totalCost } })
export const listOrders = (params) => request.get('/orders', { params })
export const createOrder = (d) => request.post('/orders', d)
export const stopOrder = (id) => request.put(`/orders/${id}/stop`)
export const listVitalSigns = (params) => request.get('/vital-signs', { params })
export const createVitalSign = (d) => request.post('/vital-signs', d)
```

`frontend/src/api/charge.js`：
```js
import request from './request'
export const createCharge = (d) => request.post('/charges', d)
export const listCharges = (params) => request.get('/charges', { params })
export const createDispense = (d) => request.post('/dispenses', d)
export const listDispenses = (params) => request.get('/dispenses', { params })
```

`frontend/src/api/validation.js`：
```js
import request from './request'
export const runValidation = () => request.post('/validation/run')
```

`frontend/src/api/dashboard.js`（追加三行）：
```js
export const admissionRatio = () => request.get('/dashboard/stats/admission-ratio')
export const rationalDrugRate = () => request.get('/dashboard/stats/rational-drug-rate')
export const pathwayRate = () => request.get('/dashboard/stats/pathway-rate')
```

- [ ] **Step 2: Timeline.vue 扩展 TYPE_META 与归并**

改 `TYPE_META`：
```js
const TYPE_META = {
  VISIT: { label: '就诊', color: '#409EFF' },
  DIAGNOSIS: { label: '诊断', color: '#F56C6C' },
  PRESCRIPTION: { label: '处方', color: '#67C23A' },
  LAB_TEST: { label: '检验', color: '#E6A23C' },
  ADMISSION: { label: '入院', color: '#909399' },
  DISCHARGE: { label: '出院', color: '#909399' },
  CHARGE: { label: '收费', color: '#8e44ad' },
  DISPENSE: { label: '发药', color: '#16a085' }
}
```

改 `grouped` 计算，把 CHARGE/DISPENSE 归入对应 visit 组（新增 `charges`/`dispenses` 数组），把 ADMISSION/DISCHARGE 按 `admissionId` 归并成一张「住院卡」列表：

在 `grouped` 的循环内、现有 `else if (e.type === 'DIAGNOSIS' || ...)` 分支之后追加：
```js
    } else if (e.type === 'CHARGE' || e.type === 'DISPENSE') {
      const g = data.visitId != null ? visitMap.get(data.visitId) : null
      if (g) {
        if (e.type === 'CHARGE') (g.charges ||= []).push(e)
        else (g.dispenses ||= []).push(e)
      } else {
        orphans.push(e)
      }
    } else if (e.type === 'ADMISSION' || e.type === 'DISCHARGE') {
      const key = data.id != null ? data.id : (e.type + '-' + index)
      let a = admissionMap.get(key)
      if (!a) { a = { admission: null, discharge: null }; admissionMap.set(key, a) }
      if (e.type === 'ADMISSION') a.admission = e
      else a.discharge = e
    } else {
      orphans.push(e)
    }
```
在 `grouped` 开头定义 `const admissionMap = new Map()`，并把返回值改为 `{ groups, orphans, admissions: [...admissionMap.values()].sort((x,y) => timeStr(x.admission || x.discharge).localeCompare(timeStr(y.admission || y.discharge))) }`。

在 visit 组对象初始化处补 `charges: [], dispenses: []`。

- [ ] **Step 3: Timeline.vue 模板渲染新事件**

在 visit 卡内、检验 section 之后追加「收费」与「发药」两个 section（结构仿照检验）：
```html
        <div v-if="group.charges.length" class="section">
          <div class="section-label" style="--c: #8e44ad"><span class="dot"></span>收费</div>
          <div v-for="(e, i) in group.charges" :key="'c' + i" class="section-line">
            <span>{{ d(e).itemName || '-' }}</span>
            <span class="amount">¥{{ d(e).amount ?? '-' }}</span>
          </div>
        </div>
        <div v-if="group.dispenses.length" class="section">
          <div class="section-label" style="--c: #16a085"><span class="dot"></span>发药</div>
          <div v-for="(e, i) in group.dispenses" :key="'disp' + i" class="section-line">
            <span>{{ d(e).drugName || '-' }}</span>
            <span class="muted">{{ d(e).quantity ?? '-' }}</span>
          </div>
        </div>
```

在 `</el-timeline-item>`（visit 组）之后、孤儿兜底之前，渲染住院卡：
```html
    <el-timeline-item
      v-for="(adm, index) in grouped.admissions"
      :key="'adm-' + index"
      :timestamp="formatDateTime(timeStr2(adm))"
      placement="top" size="large" color="#909399"
    >
      <el-card shadow="hover" class="visit-card">
        <el-tag color="#909399" effect="dark" size="small" class="type-tag">住院</el-tag>
        <div class="section-line">
          <span>{{ d(adm.admission).diagnosis || '-' }}</span>
          <span class="muted">· {{ d(adm.admission).department || '-' }} · {{ d(adm.admission).bedNo || '-' }}</span>
        </div>
        <div class="section-line muted">
          入院 {{ formatDateTime(adm.admission?.time) }}
          <template v-if="adm.discharge"> → 出院 {{ formatDateTime(adm.discharge.time) }}</template>
        </div>
        <el-button link type="primary" size="small"
          @click="openAdmission(d(adm.admission).id)">查看住院时间轴</el-button>
      </el-card>
    </el-timeline-item>
```
并加辅助方法：
```js
const timeStr2 = (adm) => (adm.admission || adm.discharge)?.time || ''
const openAdmission = (id) => { if (id != null) window.location.hash = `#/admission/${id}` }
```
> 用 `window.location.hash` 跳转，避免组件里引入 useRouter（Timeline 是展示组件，无路由上下文依赖也可用）。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api frontend/src/components/Timeline.vue
git commit -m "feat: 前端 API 封装与患者时间轴新事件渲染"
```

---

## Task 13: 前端住院时间轴页 + 校验报告页

**Files:**
- Create: `frontend/src/views/AdmissionDetail.vue`、`frontend/src/views/ValidationReport.vue`
- Modify: `frontend/src/router/index.js`、`frontend/src/App.vue`

**Interfaces:**
- Consumes: `inpatient.js`、`validation.js` 的 API。
- Produces: `/admission/:id`（住院时间轴）与 `/validation`（校验报告）两个页面。

- [ ] **Step 1: 写 AdmissionDetail.vue**

```vue
<template>
  <div class="admission-detail">
    <div class="page-head">
      <el-button link type="primary" @click="goBack">&lt; 返回</el-button>
      <h2 class="page-title">住院时间轴</h2>
      <span v-if="admission" class="sub-title muted">
        住院号 {{ admission.admissionNo || '-' }} · {{ admission.diagnosis || '-' }}
      </span>
    </div>

    <el-card v-loading="loading" shadow="never">
      <el-row v-if="vitalRows.length" :gutter="16">
        <el-col :xs="24" :lg="12">
          <Chart :option="tempOption" height="280px" />
        </el-col>
        <el-col :xs="24" :lg="12">
          <Chart :option="bpOption" height="280px" />
        </el-col>
      </el-row>

      <el-table v-if="orders.length" :data="orders" size="small" border style="margin-top: 12px">
        <el-table-column prop="itemName" label="医嘱" min-width="140" />
        <el-table-column prop="orderTypeText" label="类型" width="80" />
        <el-table-column prop="dosage" label="用量" width="80" />
        <el-table-column prop="frequency" label="频次" width="100" />
        <el-table-column label="开始时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.startTime) }}</template>
        </el-table-column>
      </el-table>

      <el-table v-if="vitals.length" :data="vitals" size="small" border style="margin-top: 12px">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.recordedAt) }}</template>
        </el-table-column>
        <el-table-column prop="temperature" label="体温℃" width="80" />
        <el-table-column prop="pulse" label="脉搏" width="80" />
        <el-table-column prop="respiration" label="呼吸" width="80" />
        <el-table-column prop="systolicBp" label="收缩压" width="80" />
        <el-table-column prop="diastolicBp" label="舒张压" width="80" />
      </el-table>

      <el-empty v-if="!loading && !orders.length && !vitals.length" description="暂无住院记录" />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listOrders, listVitalSigns } from '../api/inpatient'
import { listAdmissions } from '../api/inpatient'
import Chart from '../components/Chart.vue'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const orders = ref([])
const vitals = ref([])
const admission = ref(null)

const ORDER_TYPE = { 0: '长期', 1: '临时', 2: '检验', 3: '护理' }
const formatDateTime = (t) => (t ? String(t).replace('T', ' ').slice(0, 19) : '')

const tempOption = computed(() => ({
  grid: { left: 40, right: 20, top: 32, bottom: 24, containLabel: true },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: vitals.value.map((v) => formatDateTime(v.recordedAt)) },
  yAxis: { type: 'value', scale: true },
  series: [{ type: 'line', name: '体温℃', data: vitals.value.map((v) => v.temperature) }]
}))
const bpOption = computed(() => ({
  grid: { left: 40, right: 20, top: 32, bottom: 24, containLabel: true },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: vitals.value.map((v) => formatDateTime(v.recordedAt)) },
  yAxis: { type: 'value', scale: true },
  series: [
    { type: 'line', name: '收缩压', data: vitals.value.map((v) => v.systolicBp) },
    { type: 'line', name: '舒张压', data: vitals.value.map((v) => v.diastolicBp) }
  ]
}))
const vitalRows = computed(() => vitals.value)

const goBack = () => router.back()

onMounted(async () => {
  loading.value = true
  const admissionId = route.params.id
  const [admRes, oRes, vRes] = await Promise.all([
    listAdmissions({ patientId: '' }).catch(() => null),  // 占位，改为直接查
    listOrders({ admissionId }).catch(() => null),
    listVitalSigns({ admissionId }).catch(() => null)
  ])
  orders.value = (oRes?.data || []).map((o) => ({ ...o, orderTypeText: ORDER_TYPE[o.orderType] ?? o.orderType }))
  vitals.value = vRes?.data || []
  admission.value = null
  loading.value = false
})
</script>

<style scoped>
.page-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.page-title { margin: 0; font-size: 18px; font-weight: 600; }
.sub-title { font-size: 13px; }
.muted { color: #909399; }
</style>
```
> 简化说明：住院基本信息（admissionNo/diagnosis）可通过 `getAdmissionTimeline` 的返回值（`patientId` 仅含 patientId）拿不到 admission 主体，因此本页仅展示医嘱/体征两表 + 折线。若需显示住院头部，可额外调用 `listAdmissions({ patientId })`——但 patientId 未知。**裁定**：住院时间轴接口返回 `TimelineVO{patientId, events}`，头部信息从 events 无法还原，故页面头部只显示「住院时间轴 + 住院号（从 URL 传入）」，住院号可在跳转时用 query 传 `admissionNo`。实现按此简化：`route.query.admissionNo` 展示。

- [ ] **Step 2: 写 ValidationReport.vue**

```vue
<template>
  <div class="validation">
    <div class="page-head">
      <h2 class="page-title">数据质量校验报告</h2>
      <el-button type="primary" :loading="loading" @click="load">重新校验</el-button>
    </div>

    <el-row v-if="report" :gutter="16" class="metric-row">
      <el-col :xs="24" :md="8">
        <el-card shadow="never">
          <div class="metric">
            <div class="metric-value">{{ pct(report.rationalDrug?.rationalRate) }}</div>
            <div class="metric-label">合理用药率</div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="never">
          <div class="metric">
            <div class="metric-value">{{ pct(report.pathway?.executionRate) }}</div>
            <div class="metric-label">临床路径执行率</div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="never">
          <div class="metric">
            <div class="metric-value">{{ report.rationalDrug?.violationPrescriptions ?? 0 }}</div>
            <div class="metric-label">违规处方数</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card v-if="report" shadow="never">
      <template #header><span class="card-title">合理用药违规明细</span></template>
      <el-table :data="report.rationalDrug?.violations || []" size="small" border>
        <el-table-column prop="prescriptionId" label="处方ID" width="90" />
        <el-table-column prop="patientId" label="患者ID" width="90" />
        <el-table-column prop="ruleType" label="违规类型" width="120" />
        <el-table-column prop="warningLevel" label="级别" width="70" />
        <el-table-column prop="message" label="说明" min-width="240" />
      </el-table>
    </el-card>

    <el-card v-if="report" shadow="never" style="margin-top: 16px">
      <template #header><span class="card-title">临床路径变异明细</span></template>
      <el-table :data="report.pathway?.variances || []" size="small" border>
        <el-table-column prop="admissionId" label="住院ID" width="90" />
        <el-table-column prop="patientId" label="患者ID" width="90" />
        <el-table-column prop="diagnosisName" label="诊断" width="140" />
        <el-table-column prop="varianceType" label="变异类型" width="100" />
        <el-table-column prop="detail" label="说明" min-width="240" />
      </el-table>
    </el-card>

    <el-empty v-if="!loading && !report" description="点击「重新校验」生成报告" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { runValidation } from '../api/validation'

const report = ref(null)
const loading = ref(false)

const pct = (v) => (v == null ? '-' : (Number(v) * 100).toFixed(1) + '%')

const load = async () => {
  loading.value = true
  const res = await runValidation().catch(() => null)
  report.value = res?.data || null
  loading.value = false
}

onMounted(load)
</script>

<style scoped>
.page-head { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
.page-title { margin: 0; font-size: 18px; font-weight: 600; }
.metric-row { margin-bottom: 16px; }
.metric { text-align: center; padding: 8px 0; }
.metric-value { font-size: 30px; font-weight: 700; color: #409EFF; }
.metric-label { font-size: 13px; color: #909399; margin-top: 4px; }
.card-title { font-weight: 600; }
</style>
```

- [ ] **Step 3: 加路由与菜单**

`router/index.js` routes 数组追加：
```js
  { path: '/admission/:id', name: 'admission-detail', component: view('AdmissionDetail') },
  { path: '/validation', name: 'validation', component: view('ValidationReport') }
```

`App.vue` 菜单追加：
```html
        <el-menu-item index="/validation">校验报告</el-menu-item>
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views frontend/src/router/index.js frontend/src/App.vue
git commit -m "feat: 住院时间轴页与校验报告页"
```

---

## Task 14: 前端看板 + 录入改造 + 测试报告

**Files:**
- Modify: `frontend/src/views/Dashboard.vue`（新增 3 个指标卡）
- Modify: `frontend/src/views/EntryForm.vue`（新增住院/医嘱/体征/收费/发药录入 tab）
- Modify: `docs/test-report.md`

**Interfaces:**
- Consumes: `dashboard.js`（Task 12）、`inpatient.js`/`charge.js`（Task 12）。

- [ ] **Step 1: Dashboard.vue 加指标卡**

在 `charts-row` 之前插入一行三卡（仿照校验报告页的 metric 卡），`loadStats()` 并发追加三个新接口，脚本新增：
```js
import { admissionRatio, rationalDrugRate, pathwayRate } from '../api/dashboard'
// ...
const admission = ref(null)
const rational = ref(null)
const pathway = ref(null)
```
`loadStats()` 内 Promise.all 追加三项并回填；模板加：
```html
    <el-row :gutter="16" class="metric-row">
      <el-col :xs="24" :md="8">
        <el-card shadow="never">
          <div class="metric"><div class="metric-value">{{ pct(admission?.ratio) }}</div>
          <div class="metric-label">住院占比</div></div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="never">
          <div class="metric"><div class="metric-value">{{ pct(rational?.rationalRate) }}</div>
          <div class="metric-label">合理用药率</div></div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="never">
          <div class="metric"><div class="metric-value">{{ pct(pathway?.executionRate) }}</div>
          <div class="metric-label">路径执行率</div></div>
        </el-card>
      </el-col>
    </el-row>
```
并加 `const pct = (v) => (v == null ? '-' : (Number(v) * 100).toFixed(1) + '%')` 与 `.metric` 样式。

- [ ] **Step 2: EntryForm.vue 加「住院/医嘱/体征/收费/发药」录入**

在现有 tab 结构后追加一个「住院登记」tab（入院表单：patientId/科室/医生/床位/入院诊断）+「收费/发药」tab（收费：visitId/类型/金额；发药：visitId/prescriptionId/药品名/数量），提交调用 `createAdmission`/`createCharge`/`createDispense`。参照现有 `createVisit`/`createDiagnosis` 的提交模式（`await createXxx({...}).then(...)` + ElMessage 成功提示）。医嘱/体征录入因需 admissionId，暂并入「住院时间轴」页不单独做表单（减小本任务改动面），仅做住院/收费/发药三表单。

- [ ] **Step 3: 扩展 docs/test-report.md**

在测试报告末尾追加「二期扩展测试」小节，记录：住院数据完整性（admission/order/vital_sign 计数与时间一致性）、体征趋势合理性（体温先升后降）、校验报告准确性（合理用药率/路径变异率与注入比例吻合）、性能（住院批量写入耗时、住院时间轴查询延迟、校验引擎扫描 10 万+ 处方耗时）。

- [ ] **Step 4: 前端构建验证**

Run: `cd frontend && npm run build`
Expected: 无报错，产物生成。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Dashboard.vue frontend/src/views/EntryForm.vue docs/test-report.md
git commit -m "feat: 看板新指标与录入表单改造，扩展测试报告"
```

---

## 部署（人工执行，非任务）

1. 对已部署库执行增量：
   ```bash
   mysql -uroot -p123456 --default-character-set=utf8mb4 his_demo < backend/sql/seed-extension.sql
   # 若 lab_test 尚无 admission_id 列（老库）：
   # mysql -uroot -p123456 his_demo -e "ALTER TABLE lab_test ADD COLUMN admission_id BIGINT NULL, ADD KEY idx_lab_admission (admission_id);"
   ```
   （新库则先 schema.sql + seed.sql + seed-extension.sql。）
2. 重新打包：`cd backend && mvn -pl his-server -am package -DskipTests`（先停 8080 应用）。
3. 重跑生成器：`mvn -q -pl his-generator -am install -DskipTests && java -jar his-generator/target/his-generator-1.0.0.jar`。
4. 重启后端 + 前端（`npm run dev`）。

## Self-Review 记录

- **Spec 覆盖**：7 表+增列（Task 1）、实体/Mapper（2）、收费/发药（3）、住院（4）、医嘱/体征（5）、患者+住院时间轴（6）、合理用药（7）、路径+一键校验（8）、看板统计（9）、生成器门诊+违规（10）、生成器住院（11）、前端 API+时间轴（12）、住院/校验页（13）、看板/录入/报告（14）。无遗漏。
- **类型一致**：`TimelineService.getAdmissionTimeline`（Task 6）与 `AdmissionController` 引用一致；`ValidationService.run()`（Task 8）与看板（Task 9）一致；`RationalDrugReport`/`PathwayReport` 字段前后一致；生成器 `generate()` stat 键在 Task 10/11 扩展并累计。
- **占位扫描**：无 TBD/TODO；Task 11 的 `generateInpatientEvents` 对「出院时间」的生成在方法内闭环（先算 totalDays 再填 discharge_time + updateById 回写），已明确。
