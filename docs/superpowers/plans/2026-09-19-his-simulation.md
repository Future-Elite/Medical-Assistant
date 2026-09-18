# 模拟 HIS 系统（虚拟患者时序数据生成与测试平台）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一套模拟 HIS 系统：SpringBoot 后端（REST 接口 + 时间线查询 + Redis 缓存）、独立数据生成器（≥1 万患者 / ≥5 万就诊记录）、Vue3 前端（患者列表/时间轴/录入/看板）与功能/性能测试报告。

**Architecture:** Maven 多模块后端（`his-common` 实体与 Mapper、`his-server` REST 服务、`his-generator` 独立生成器）＋ Vue3 单页前端。时间线是核心：把某患者所有 visit/diagnosis/prescription/lab_test 按时间戳合并升序返回；生成器用 `rule_config` 规则表约束「诊断↔用药」。

**Tech Stack:** Java 21 · Spring Boot 3.3.x · MyBatis-Plus 3.5.x · MySQL 8 · Redis 8 · Vue 3 + Vite · Element Plus · Pinia · ECharts · Maven 3.9。

**Spec:** `docs/superpowers/specs/2026-09-19-his-simulation-design.md`

## Global Constraints

- Java 21；Spring Boot 3.3.x；MyBatis-Plus 用 `mybatis-plus-spring-boot3-starter`（3.5.x）。
- 数据库：MySQL 8，库名 `his_demo`，专用账号 `his_user`（密码 `his@2026`），utf8mb4。
- 所有 REST 接口前缀 `/api`，统一响应结构 `{ code, message, data }`（`code=0` 成功）。
- 时间线事件 `type` 只能取 `VISIT`/`DIAGNOSIS`/`PRESCRIPTION`/`LAB_TEST`，按 `time` 升序。
- 时间字段统一 `LocalDateTime`（数据库 `DATETIME`）。
- 生成器目标数据量：患者 ≥ 10000、就诊记录 ≥ 50000；同一患者就诊时间严格递增。
- 实施顺序：后端 → 生成器 → 前端 → 测试。前端端口 5173，后端端口 8080。

---

### Task 1: 项目脚手架 + 数据库初始化

**Files:**
- Create: `backend/pom.xml`（聚合父 POM）
- Create: `backend/his-common/pom.xml`
- Create: `backend/his-server/pom.xml`
- Create: `backend/his-generator/pom.xml`
- Create: `backend/sql/schema.sql`（建库建表）
- Create: `backend/sql/seed.sql`（药品字典 + 规则表种子数据）
- Create: `backend/his-server/src/main/resources/application.yml`
- Create: `backend/his-server/src/main/resources/application-generator.yml`（生成器共用）

**Interfaces:**
- Produces: Maven 模块 `com.his:his-common`、`com.his:his-server`、`com.his:his-generator`；MySQL 库 `his_demo` 与 8 张表。

- [ ] **Step 1: 创建数据库与专用账号**（root/123456）

```bash
mysql -uroot -p123456 -e "
CREATE DATABASE IF NOT EXISTS his_demo DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'his_user'@'localhost' IDENTIFIED BY 'his@2026';
GRANT ALL PRIVILEGES ON his_demo.* TO 'his_user'@'localhost';
FLUSH PRIVILEGES;"
```

- [ ] **Step 2: 编写 `backend/sql/schema.sql`**

内容（8 张表，字段与索引严格按 spec 第 5 节）：

```sql
CREATE TABLE patient (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(50) NOT NULL,
  gender TINYINT NOT NULL DEFAULT 0 COMMENT '0男1女',
  birth_date DATE,
  id_card VARCHAR(18),
  phone VARCHAR(20),
  address VARCHAR(255),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_patient_name (name), KEY idx_patient_id_card (id_card)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='患者表';

CREATE TABLE visit (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  patient_id BIGINT NOT NULL,
  visit_no VARCHAR(32) NOT NULL,
  department VARCHAR(50), doctor VARCHAR(50),
  visit_time DATETIME NOT NULL,
  status TINYINT NOT NULL DEFAULT 0, remark VARCHAR(255),
  KEY idx_visit_patient_time (patient_id, visit_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='就诊记录表';

CREATE TABLE diagnosis (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  patient_id BIGINT NOT NULL, visit_id BIGINT NOT NULL,
  icd_code VARCHAR(20), diagnosis_name VARCHAR(100) NOT NULL,
  doctor VARCHAR(50), diagnosed_at DATETIME NOT NULL,
  KEY idx_diag_patient_time (patient_id, diagnosed_at), KEY idx_diag_visit (visit_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='诊断表';

CREATE TABLE prescription (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  patient_id BIGINT NOT NULL, visit_id BIGINT NOT NULL,
  doctor VARCHAR(50), prescribed_at DATETIME NOT NULL,
  total_amount DECIMAL(10,2) DEFAULT 0,
  KEY idx_pres_patient_time (patient_id, prescribed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='处方表';

CREATE TABLE prescription_item (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  prescription_id BIGINT NOT NULL, drug_id BIGINT,
  drug_name VARCHAR(100), dosage VARCHAR(50), frequency VARCHAR(50),
  days INT, quantity DECIMAL(10,2), unit VARCHAR(20),
  KEY idx_item_prescription (prescription_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='处方明细表';

CREATE TABLE drug (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL, category VARCHAR(50),
  spec VARCHAR(50), unit VARCHAR(20), price DECIMAL(10,2)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='药品字典表';

CREATE TABLE lab_test (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  patient_id BIGINT NOT NULL, visit_id BIGINT NOT NULL,
  item_code VARCHAR(20), item_name VARCHAR(100) NOT NULL,
  result_value VARCHAR(50), unit VARCHAR(20),
  reference_range VARCHAR(50), is_abnormal TINYINT DEFAULT 0,
  tested_at DATETIME NOT NULL,
  KEY idx_lab_patient_time (patient_id, tested_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='检验结果表';

CREATE TABLE rule_config (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  diagnosis_code VARCHAR(20), diagnosis_name VARCHAR(100) NOT NULL,
  drug_ids VARCHAR(500), enabled TINYINT DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='诊断用药配置规则表';
```

- [ ] **Step 3: 编写 `backend/sql/seed.sql`**

药品字典 12 条 + 规则表 8 条（诊断→药品映射），示例：

```sql
INSERT INTO drug (name, category, spec, unit, price) VALUES
('阿莫西林胶囊','抗感染','0.25g*24粒','盒',18.50),
('感冒灵颗粒','感冒','10g*9袋','盒',12.00),
('布洛芬缓释胶囊','解热镇痛','0.3g*20粒','盒',15.80),
('硝苯地平控释片','心血管','30mg*7片','盒',28.00),
('二甲双胍片','降糖','0.5g*20片','盒',9.60),
('奥美拉唑肠溶胶囊','消化','20mg*14粒','盒',22.00),
('蒙脱石散','消化','3g*10袋','盒',16.50),
('左氧氟沙星片','抗感染','0.5g*7片','盒',26.00),
('阿司匹林肠溶片','心血管','100mg*30片','盒',13.20),
('苯磺酸氨氯地平片','心血管','5mg*7片','盒',19.80),
('格列美脲片','降糖','2mg*15片','盒',24.50),
('复方甘草口服液','呼吸','100ml','瓶',8.90);

INSERT INTO rule_config (diagnosis_code, diagnosis_name, drug_ids, enabled) VALUES
('J06.9','上呼吸道感染','2,1',1),
('J20.9','急性支气管炎','12,8',1),
('I10','原发性高血压','4,9,10',1),
('E11.9','2型糖尿病','5,11',1),
('K29.5','慢性胃炎','6',1),
('A09.9','急性胃肠炎','7,6',1),
('G43.9','偏头痛','3',1),
('R50.9','发热','2,3',1);
```

- [ ] **Step 4: 执行建表与种子数据**

```bash
mysql -uroot -p123456 his_demo < backend/sql/schema.sql
mysql -uroot -p123456 his_demo < backend/sql/seed.sql
mysql -uhis_user -phis@2026 -e "SELECT COUNT(*) FROM his_demo.drug; SELECT COUNT(*) FROM his_demo.rule_config;"
```
Expected：`drug=12`、`rule_config=8`，且 `his_user` 可正常查询。

- [ ] **Step 5: 编写父 POM `backend/pom.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.3.5</version>
    <relativePath/>
  </parent>
  <groupId>com.his</groupId>
  <artifactId>his-demo</artifactId>
  <version>1.0.0</version>
  <packaging>pom</packaging>
  <modules>
    <module>his-common</module>
    <module>his-server</module>
    <module>his-generator</module>
  </modules>
  <properties>
    <java.version>21</java.version>
    <mybatis-plus.version>3.5.7</mybatis-plus.version>
  </properties>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>com.baomidou</groupId>
        <artifactId>mybatis-plus-spring-boot3-starter</artifactId>
        <version>${mybatis-plus.version}</version>
      </dependency>
    </dependencies>
  </dependencyManagement>
</project>
```

- [ ] **Step 6: 编写三个子模块 POM**

`his-common/pom.xml`：依赖 `mybatis-plus-spring-boot3-starter`、`lombok`（provided）、`jackson-databind`。

`his-server/pom.xml`：依赖 `his-common`、`spring-boot-starter-web`、`spring-boot-starter-data-redis`、`mysql-connector-j`、`spring-boot-starter-test`（test）。

`his-generator/pom.xml`：依赖 `his-common`、`mybatis-plus-spring-boot3-starter`、`mysql-connector-j`、`lombok`，配置 `spring-boot-maven-plugin`。

- [ ] **Step 7: 编写 `his-server` 的 `application.yml`**

```yaml
server:
  port: 8080
spring:
  datasource:
    url: jdbc:mysql://localhost:3306/his_demo?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai&rewriteBatchedStatements=true
    username: his_user
    password: his@2026
    driver-class-name: com.mysql.cj.jdbc.Driver
  data:
    redis:
      host: localhost
      port: 6379
mybatis-plus:
  configuration:
    map-underscore-to-camel-case: true
    log-impl: org.apache.ibatis.logging.slf4j.Slf4jImpl
```

- [ ] **Step 8: 验证构建通过**

```bash
cd backend && mvn -q compile
```
Expected：BUILD SUCCESS，三模块均编译通过。

- [ ] **Step 9: Commit**

```bash
git init && git add backend docs && git commit -m "chore: init backend multi-module skeleton + schema/seed"
```
（若用户要求纳入 git；否则跳过 `git init`。）

---

### Task 2: his-common 实体与 Mapper

**Files:**
- Create: `backend/his-common/src/main/java/com/his/common/entity/{Patient,Visit,Diagnosis,Prescription,PrescriptionItem,Drug,LabTest,RuleConfig}.java`
- Create: `backend/his-common/src/main/java/com/his/common/mapper/{...Mapper}.java`（同名 8 个）
- Create: `backend/his-common/src/main/java/com/his/common/common/Result.java`
- Create: `backend/his-common/src/main/java/com/his/common/dto/TimelineEvent.java`
- Create: `backend/his-common/src/main/java/com/his/common/dto/TimelineVO.java`

**Interfaces:**
- Produces（后续任务依赖的确切类型）：
  - 实体类字段见 spec 第 5 节，Lombok `@Data`、`@TableName("patient")` 等、`@TableId(type = IdType.AUTO)`。
  - `Result<T>`：静态方法 `Result.ok(T data)`、`Result.fail(String message)`，字段 `int code; String message; T data;`。
  - `TimelineEvent`：字段 `String type; LocalDateTime time; Object data;`，构造器 `TimelineEvent(String type, LocalDateTime time, Object data)`。
  - `TimelineVO`：字段 `Long patientId; List<TimelineEvent> events;`。

- [ ] **Step 1: 编写 8 个实体**

以 `Patient` 与 `PrescriptionItem` 为例（其余按 spec 字段表照写）：

```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Data
@TableName("patient")
public class Patient {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String name;
    private Integer gender;
    private LocalDate birthDate;
    private String idCard;
    private String phone;
    private String address;
    private LocalDateTime createdAt;
}
```

```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.math.BigDecimal;

@Data
@TableName("prescription_item")
public class PrescriptionItem {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long prescriptionId;
    private Long drugId;
    private String drugName;
    private String dosage;
    private String frequency;
    private Integer days;
    private BigDecimal quantity;
    private String unit;
}
```

`Prescription` 实体含非持久化明细列表（时间线用）：

```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;
import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;

@Data
@TableName("prescription")
public class Prescription {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private Long visitId;
    private String doctor;
    private LocalDateTime prescribedAt;
    private BigDecimal totalAmount;
    @TableField(exist = false)
    private List<PrescriptionItem> items;
}
```

其余实体（字段严格对应 schema.sql）：`Visit`(id, patientId, visitNo, department, doctor, visitTime[LocalDateTime], status, remark)、`Diagnosis`(id, patientId, visitId, icdCode, diagnosisName, doctor, diagnosedAt[LocalDateTime])、`Drug`(id, name, category, spec, unit, price[BigDecimal])、`LabTest`(id, patientId, visitId, itemCode, itemName, resultValue, unit, referenceRange, isAbnormal, testedAt[LocalDateTime])、`RuleConfig`(id, diagnosisCode, diagnosisName, drugIds, enabled)。

- [ ] **Step 2: 编写 8 个 Mapper**

```java
package com.his.common.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.his.common.entity.Patient;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface PatientMapper extends BaseMapper<Patient> {
}
```
`VisitMapper`/`DiagnosisMapper`/`PrescriptionMapper`/`PrescriptionItemMapper`/`DrugMapper`/`LabTestMapper`/`RuleConfigMapper` 同理，仅改实体名。

- [ ] **Step 3: 编写 `Result`、`TimelineEvent`、`TimelineVO`**

```java
package com.his.common.common;

import lombok.Data;

@Data
public class Result<T> {
    private int code;
    private String message;
    private T data;

    public static <T> Result<T> ok(T data) {
        Result<T> r = new Result<>();
        r.code = 0; r.message = "success"; r.data = data; return r;
    }
    public static <T> Result<T> fail(String message) {
        Result<T> r = new Result<>();
        r.code = 1; r.message = message; return r;
    }
}
```

```java
package com.his.common.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import java.time.LocalDateTime;

@Data
@AllArgsConstructor
public class TimelineEvent {
    private String type;
    private LocalDateTime time;
    private Object data;
}
```

```java
package com.his.common.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import java.util.List;

@Data
@AllArgsConstructor
public class TimelineVO {
    private Long patientId;
    private List<TimelineEvent> events;
}
```

- [ ] **Step 4: 编译验证**

```bash
cd backend && mvn -q -pl his-common compile
```
Expected：BUILD SUCCESS。

- [ ] **Step 5: Commit**

```bash
git add backend/his-common && git commit -m "feat(common): add entities, mappers, Result and Timeline DTOs"
```

---

### Task 3: 核心业务 REST 接口（患者/挂号/诊断/处方/检验）

**Files:**
- Create: `backend/his-server/src/main/java/com/his/server/HisServerApplication.java`
- Create: `backend/his-server/src/main/java/com/his/server/config/MybatisPlusConfig.java`
- Create: `backend/his-server/src/main/java/com/his/server/service/{PatientService,VisitService,DiagnosisService,PrescriptionService,LabTestService}.java`
- Create: `backend/his-server/src/main/java/com/his/server/controller/{PatientController,VisitController,DiagnosisController,PrescriptionController,LabTestController}.java`
- Test: `backend/his-server/src/test/java/com/his/server/controller/PatientControllerTest.java`

**Interfaces:**
- Consumes: 实体/Mapper/`Result`（Task 2）。
- Produces（前端与测试依赖的 REST 契约）：
  - `POST /api/patients` 入参 `{name, gender, birthDate, idCard, phone, address}` → `Result<Patient>`
  - `GET /api/patients?page=1&size=10&keyword=` → `Result<Page<Patient>>`（MyBatis-Plus `IPage`）
  - `GET /api/patients/{id}` → `Result<Patient>`
  - `POST /api/visits` 入参 `{patientId, visitNo, department, doctor, visitTime, status, remark}` → `Result<Visit>`
  - `POST /api/diagnoses` 入参 `{patientId, visitId, icdCode, diagnosisName, doctor, diagnosedAt}` → `Result<Diagnosis>`
  - `POST /api/prescriptions` 入参 `{patientId, visitId, doctor, prescribedAt, items:[{drugId, drugName, dosage, frequency, days, quantity, unit}]}` → `Result<Prescription>`
  - `POST /api/lab-tests` 入参 `{patientId, visitId, itemCode, itemName, resultValue, unit, referenceRange, isAbnormal, testedAt}` → `Result<LabTest>`

- [ ] **Step 1: 编写启动类与 MyBatis-Plus 分页配置**

```java
package com.his.server;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication(scanBasePackages = "com.his")
@MapperScan("com.his.common.mapper")
public class HisServerApplication {
    public static void main(String[] args) {
        SpringApplication.run(HisServerApplication.class, args);
    }
}
```

```java
package com.his.server.config;

import com.baomidou.mybatisplus.annotation.DbType;
import com.baomidou.mybatisplus.extension.plugins.MybatisPlusInterceptor;
import com.baomidou.mybatisplus.extension.plugins.inner.PaginationInnerInterceptor;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class MybatisPlusConfig {
    @Bean
    public MybatisPlusInterceptor mybatisPlusInterceptor() {
        MybatisPlusInterceptor interceptor = new MybatisPlusInterceptor();
        interceptor.addInnerInterceptor(new PaginationInnerInterceptor(DbType.MYSQL));
        return interceptor;
    }
}
```

- [ ] **Step 2: 编写 Service 层（继承 MyBatis-Plus `ServiceImpl`）**

以 `PatientService` 为例：

```java
package com.his.server.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.his.common.entity.Patient;

public interface PatientService extends IService<Patient> {
}
```

```java
package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Patient;
import com.his.common.mapper.PatientMapper;
import com.his.server.service.PatientService;
import org.springframework.stereotype.Service;

@Service
public class PatientServiceImpl extends ServiceImpl<PatientMapper, Patient> implements PatientService {
}
```
`VisitService`/`DiagnosisService`/`LabTestService` 同理。`PrescriptionService` 额外需要 `saveWithItems`（见 Step 3）。

- [ ] **Step 3: 编写处方 Service（含明细事务写入）**

```java
package com.his.server.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.his.common.entity.Prescription;
import com.his.common.entity.PrescriptionItem;
import java.math.BigDecimal;
import java.util.List;

public interface PrescriptionService extends IService<Prescription> {
    Prescription saveWithItems(Prescription prescription, List<PrescriptionItem> items);
}
```

```java
package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Drug;
import com.his.common.entity.Prescription;
import com.his.common.entity.PrescriptionItem;
import com.his.common.mapper.DrugMapper;
import com.his.common.mapper.PrescriptionItemMapper;
import com.his.common.mapper.PrescriptionMapper;
import com.his.server.service.PrescriptionService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.List;

@Service
@RequiredArgsConstructor
public class PrescriptionServiceImpl extends ServiceImpl<PrescriptionMapper, Prescription>
        implements PrescriptionService {

    private final PrescriptionItemMapper itemMapper;
    private final DrugMapper drugMapper;

    @Override
    @Transactional(rollbackFor = Exception.class)
    public Prescription saveWithItems(Prescription prescription, List<PrescriptionItem> items) {
        BigDecimal total = items.stream()
                .map(i -> {
                    BigDecimal price = BigDecimal.ZERO;
                    if (i.getDrugId() != null) {
                        Drug drug = drugMapper.selectById(i.getDrugId());
                        if (drug != null && drug.getPrice() != null) price = drug.getPrice();
                    }
                    return price.multiply(i.getQuantity() == null ? BigDecimal.ZERO : i.getQuantity());
                })
                .reduce(BigDecimal.ZERO, BigDecimal::add);
        prescription.setTotalAmount(total);
        save(prescription);
        items.forEach(i -> i.setPrescriptionId(prescription.getId()));
        items.forEach(itemMapper::insert);
        return prescription;
    }
}
```

- [ ] **Step 4: 编写 5 个 Controller**

以 `PatientController` 为例（其余同构）：

```java
package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.his.common.common.Result;
import com.his.common.entity.Patient;
import com.his.server.service.PatientService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/patients")
@RequiredArgsConstructor
public class PatientController {

    private final PatientService patientService;

    @PostMapping
    public Result<Patient> create(@RequestBody Patient patient) {
        patientService.save(patient);
        return Result.ok(patient);
    }

    @GetMapping
    public Result<Page<Patient>> list(@RequestParam(defaultValue = "1") long page,
                                      @RequestParam(defaultValue = "10") long size,
                                      @RequestParam(required = false) String keyword) {
        LambdaQueryWrapper<Patient> qw = new LambdaQueryWrapper<>();
        if (keyword != null && !keyword.isBlank()) {
            qw.like(Patient::getName, keyword)
              .or().like(Patient::getIdCard, keyword)
              .or().like(Patient::getPhone, keyword);
        }
        qw.orderByDesc(Patient::getId);
        return Result.ok(patientService.page(new Page<>(page, size), qw));
    }

    @GetMapping("/{id}")
    public Result<Patient> get(@PathVariable Long id) {
        return Result.ok(patientService.getById(id));
    }
}
```

`VisitController`/`DiagnosisController`/`LabTestController`：仅 `@PostMapping` 调 `service.save()`，路径 `/api/visits`、`/api/diagnoses`、`/api/lab-tests`。`PrescriptionController`：`@PostMapping` 解析请求体（含 `items` 列表）后调 `prescriptionService.saveWithItems`。

- [ ] **Step 5: 编写患者 Controller 单元测试（MockMvc + Mock Service）**

```java
package com.his.server.controller;

import com.his.common.entity.Patient;
import com.his.server.service.PatientService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(PatientController.class)
class PatientControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean PatientService patientService;

    @Test
    void create_returnsPatient() throws Exception {
        Patient p = new Patient();
        p.setId(1L); p.setName("张三");
        when(patientService.save(any())).thenReturn(true);
        mockMvc.perform(post("/api/patients")
                        .contentType("application/json")
                        .content("{\"name\":\"张三\",\"gender\":0}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));
    }

    @Test
    void getById_returnsPatient() throws Exception {
        Patient p = new Patient();
        p.setId(1L); p.setName("张三");
        when(patientService.getById(1L)).thenReturn(p);
        mockMvc.perform(get("/api/patients/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.name").value("张三"));
    }
}
```

- [ ] **Step 6: 运行测试**

```bash
cd backend && mvn -q -pl his-server test
```
Expected：2 tests PASS。

- [ ] **Step 7: 启动应用冒烟验证**

```bash
cd backend && mvn -q -pl his-server spring-boot:run &
curl -s -X POST http://localhost:8080/api/patients -H "Content-Type: application/json" \
  -d '{"name":"测试患者","gender":0,"birthDate":"1990-01-01","phone":"13800000000"}'
curl -s "http://localhost:8080/api/patients?page=1&size=5"
```
Expected：返回 `code=0` 且患者列表含刚插入记录。启动 Redis（`redis-server`）不启动也不影响本任务（未用到）。

- [ ] **Step 8: Commit**

```bash
git add backend/his-server && git commit -m "feat(server): core REST endpoints for patient/visit/diagnosis/prescription/lab"
```

---

### Task 4: 时间线查询接口 + Redis 缓存

**Files:**
- Create: `backend/his-server/src/main/java/com/his/server/service/TimelineService.java`（+ impl）
- Modify: `backend/his-server/src/main/java/com/his/server/controller/PatientController.java`（增加 timeline 端点）
- Modify: `backend/his-server/src/main/java/com/his/server/config/`（新增 `RedisConfig`，Jackson 序列化）
- Test: `backend/his-server/src/test/java/com/his/server/service/TimelineServiceTest.java`

**Interfaces:**
- Consumes: 8 个 Mapper、`TimelineEvent`/`TimelineVO`（Task 2）。
- Produces: `GET /api/patients/{id}/timeline` → `Result<TimelineVO>`；`TimelineService.getTimeline(Long patientId)` → `TimelineVO`；缓存键 `timeline:{patientId}`。

- [ ] **Step 1: 编写 TimelineService**

```java
package com.his.server.service;

import com.his.common.dto.TimelineVO;

public interface TimelineService {
    TimelineVO getTimeline(Long patientId);
}
```

```java
package com.his.server.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.his.common.dto.TimelineEvent;
import com.his.common.dto.TimelineVO;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import com.his.server.service.TimelineService;
import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class TimelineServiceImpl implements TimelineService {

    private static final String CACHE_PREFIX = "timeline:";
    private static final Duration TTL = Duration.ofMinutes(30);

    private final VisitMapper visitMapper;
    private final DiagnosisMapper diagnosisMapper;
    private final PrescriptionMapper prescriptionMapper;
    private final PrescriptionItemMapper itemMapper;
    private final LabTestMapper labTestMapper;
    private final StringRedisTemplate redis;
    private final ObjectMapper objectMapper;

    @Override
    public TimelineVO getTimeline(Long patientId) {
        String key = CACHE_PREFIX + patientId;
        String cached = redis.opsForValue().get(key);
        if (cached != null) {
            try {
                return objectMapper.readValue(cached, TimelineVO.class);
            } catch (Exception ignored) { /* fall through to rebuild */ }
        }
        TimelineVO vo = build(patientId);
        try {
            redis.opsForValue().set(key, objectMapper.writeValueAsString(vo), TTL);
        } catch (Exception ignored) { }
        return vo;
    }

    private TimelineVO build(Long patientId) {
        List<TimelineEvent> events = new ArrayList<>();

        visitMapper.selectList(new LambdaQueryWrapper<Visit>().eq(Visit::getPatientId, patientId))
                .forEach(v -> events.add(new TimelineEvent("VISIT", v.getVisitTime(), v)));

        diagnosisMapper.selectList(new LambdaQueryWrapper<Diagnosis>().eq(Diagnosis::getPatientId, patientId))
                .forEach(d -> events.add(new TimelineEvent("DIAGNOSIS", d.getDiagnosedAt(), d)));

        List<Prescription> pres = prescriptionMapper.selectList(
                new LambdaQueryWrapper<Prescription>().eq(Prescription::getPatientId, patientId));
        Map<Long, List<PrescriptionItem>> itemMap = pres.isEmpty() ? Map.of() :
                itemMapper.selectList(new LambdaQueryWrapper<PrescriptionItem>()
                        .in(PrescriptionItem::getPrescriptionId,
                            pres.stream().map(Prescription::getId).collect(Collectors.toList())))
                        .stream().collect(Collectors.groupingBy(PrescriptionItem::getPrescriptionId));
        for (Prescription p : pres) {
            p.setItems(itemMap.getOrDefault(p.getId(), List.of()));
            events.add(new TimelineEvent("PRESCRIPTION", p.getPrescribedAt(), p));
        }

        labTestMapper.selectList(new LambdaQueryWrapper<LabTest>().eq(LabTest::getPatientId, patientId))
                .forEach(l -> events.add(new TimelineEvent("LAB_TEST", l.getTestedAt(), l)));

        events.sort(Comparator.comparing(TimelineEvent::getTime));
        return new TimelineVO(patientId, events);
    }
}
```

> 注：`Prescription.items`（`@TableField(exist = false)`）已在 Task 2 实体中定义。

- [ ] **Step 2: 编写 RedisConfig（JSON 序列化）**

```java
package com.his.server.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;

@Configuration
public class RedisConfig {
    @Bean
    public StringRedisTemplate stringRedisTemplate(RedisConnectionFactory factory) {
        return new StringRedisTemplate(factory);
    }
}
```
（`ObjectMapper` 直接注入 Spring 自带的；确保其注册了 `JavaTimeModule` 以序列化 `LocalDateTime`——在 `application.yml` 加 `spring.jackson.serialization.write-dates-as-timestamps: false` 并由 Spring Boot 默认注册 JavaTimeModule。）

- [ ] **Step 3: 在 PatientController 增加 timeline 端点**

```java
private final TimelineService timelineService;

@GetMapping("/{id}/timeline")
public Result<TimelineVO> timeline(@PathVariable Long id) {
    return Result.ok(timelineService.getTimeline(id));
}
```

- [ ] **Step 4: 缓存失效钩子**

在 `VisitServiceImpl`/`DiagnosisServiceImpl`/`PrescriptionServiceImpl`/`LabTestServiceImpl` 的写方法里，保存成功后调用 `redis.delete("timeline:" + patientId)`。为复用，新增一个 `CacheInvalidator`：

```java
package com.his.server.service;

import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import lombok.RequiredArgsConstructor;

@Component
@RequiredArgsConstructor
public class CacheInvalidator {
    private final StringRedisTemplate redis;
    public void evictTimeline(Long patientId) {
        redis.delete("timeline:" + patientId);
    }
}
```
四个 Service 的 `save` 覆写为：`super.save(entity); cacheInvalidator.evictTimeline(entity.getPatientId());`。

- [ ] **Step 5: 编写 TimelineService 单元测试（排序正确性，用 Mockito 桩 Mapper）**

```java
package com.his.server.service;

import com.his.common.dto.TimelineVO;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class TimelineServiceTest {

    @Test
    void events_are_sorted_ascending_by_time() {
        VisitMapper vm = mock(VisitMapper.class);
        DiagnosisMapper dm = mock(DiagnosisMapper.class);
        PrescriptionMapper pm = mock(PrescriptionMapper.class);
        PrescriptionItemMapper im = mock(PrescriptionItemMapper.class);
        LabTestMapper lm = mock(LabTestMapper.class);
        StringRedisTemplate redis = mock(StringRedisTemplate.class);
        when(redis.opsForValue()).thenReturn(mock(ValueOperations.class));
        ObjectMapper om = new ObjectMapper();

        Visit v = new Visit(); v.setId(1L); v.setPatientId(1L); v.setVisitTime(LocalDateTime.of(2024,1,1,10,0));
        LabTest l = new LabTest(); l.setId(1L); l.setPatientId(1L); l.setTestedAt(LocalDateTime.of(2024,1,1,9,30));
        when(vm.selectList(any())).thenReturn(List.of(v));
        when(dm.selectList(any())).thenReturn(List.of());
        when(pm.selectList(any())).thenReturn(List.of());
        when(im.selectList(any())).thenReturn(List.of());
        when(lm.selectList(any())).thenReturn(List.of(l));

        TimelineServiceImpl svc = new TimelineServiceImpl(vm, dm, pm, im, lm, redis, om);
        TimelineVO vo = svc.getTimeline(1L);

        assertThat(vo.getEvents()).hasSize(2);
        assertThat(vo.getEvents().get(0).getType()).isEqualTo("LAB_TEST");
        assertThat(vo.getEvents().get(1).getType()).isEqualTo("VISIT");
    }
}
```

- [ ] **Step 6: 运行测试**

```bash
cd backend && mvn -q -pl his-server test
```
Expected：全部 PASS（含 Task 3 的 2 个 + 本任务 1 个）。

- [ ] **Step 7: 冒烟验证时间线接口**

```bash
curl -s "http://localhost:8080/api/patients/1/timeline"
```
Expected：返回 `events` 数组，且 `time` 升序；Redis 里出现 `timeline:1` 键（`redis-cli keys "timeline:*"`）。

- [ ] **Step 8: Commit**

```bash
git add backend/his-server backend/his-common && git commit -m "feat(server): timeline query endpoint with Redis cache + invalidation"
```

---

### Task 5: 数据看板统计接口

**Files:**
- Create: `backend/his-server/src/main/java/com/his/server/service/DashboardService.java`（+ impl）
- Create: `backend/his-server/src/main/java/com/his/server/controller/DashboardController.java`
- Test: `backend/his-server/src/test/java/com/his/server/service/DashboardServiceTest.java`

**Interfaces:**
- Produces:
  - `GET /api/dashboard/random-patient` → `Result<Patient>`
  - `GET /api/dashboard/stats/visit-frequency` → `Result<List<Map<String,Object>>>`（每月就诊数：`{date:"2024-01", count:123}`）
  - `GET /api/dashboard/stats/drug-distribution` → `Result<List<Map<String,Object>>>`（药品使用次数：`{drugName:"感冒灵颗粒", count:456}`）

- [ ] **Step 1: 编写 DashboardService**

```java
package com.his.server.service;

import com.his.common.entity.Patient;
import java.util.List;
import java.util.Map;

public interface DashboardService {
    Patient randomPatient();
    List<Map<String, Object>> visitFrequency();
    List<Map<String, Object>> drugDistribution();
}
```

实现要点（`DashboardServiceImpl`）：
- `randomPatient()`：`patientMapper.selectList(null)` 取 id 列表后随机挑一个再 `selectById`（数据量大时改用 `SELECT id FROM patient ORDER BY RAND() LIMIT 1`）。
- `visitFrequency()`：`visitMapper.selectList(null)` 内存按 `visitTime` 的 `yyyy-MM` 分组计数（先简单内存实现，5 万条可接受），返回升序列表。
- `drugDistribution()`：`itemMapper.selectList(null)` 内存按 `drugName` 分组计数，按 count 降序取前 20。

- [ ] **Step 2: 编写 DashboardController**

```java
package com.his.server.controller;

import com.his.common.common.Result;
import com.his.common.entity.Patient;
import com.his.server.service.DashboardService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/dashboard")
@RequiredArgsConstructor
public class DashboardController {
    private final DashboardService dashboardService;

    @GetMapping("/random-patient")
    public Result<Patient> randomPatient() { return Result.ok(dashboardService.randomPatient()); }

    @GetMapping("/stats/visit-frequency")
    public Result<List<Map<String, Object>>> visitFrequency() {
        return Result.ok(dashboardService.visitFrequency());
    }

    @GetMapping("/stats/drug-distribution")
    public Result<List<Map<String, Object>>> drugDistribution() {
        return Result.ok(dashboardService.drugDistribution());
    }
}
```

- [ ] **Step 3: 编写单元测试（分组计数正确性）**

```java
package com.his.server.service;

import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class DashboardServiceTest {

    @Test
    void drugDistribution_groups_and_sorts_by_count_desc() {
        VisitMapper vm = mock(VisitMapper.class);
        PrescriptionItemMapper im = mock(PrescriptionItemMapper.class);
        PatientMapper pm = mock(PatientMapper.class);

        PrescriptionItem a = new PrescriptionItem(); a.setDrugName("感冒灵颗粒");
        PrescriptionItem b = new PrescriptionItem(); b.setDrugName("布洛芬");
        PrescriptionItem c = new PrescriptionItem(); c.setDrugName("感冒灵颗粒");
        when(im.selectList(any())).thenReturn(List.of(a, b, c));

        DashboardServiceImpl svc = new DashboardServiceImpl(pm, vm, im);
        List<Map<String, Object>> dist = svc.drugDistribution();

        assertThat(dist).hasSize(2);
        assertThat(dist.get(0).get("drugName")).isEqualTo("感冒灵颗粒");
        assertThat(dist.get(0).get("count")).isEqualTo(2L);
        assertThat(dist.get(1).get("drugName")).isEqualTo("布洛芬");
    }

    @Test
    void visitFrequency_merges_same_month() {
        VisitMapper vm = mock(VisitMapper.class);
        PrescriptionItemMapper im = mock(PrescriptionItemMapper.class);
        PatientMapper pm = mock(PatientMapper.class);

        Visit v1 = new Visit(); v1.setVisitTime(LocalDateTime.of(2024, 1, 5, 9, 0));
        Visit v2 = new Visit(); v2.setVisitTime(LocalDateTime.of(2024, 1, 20, 10, 0));
        Visit v3 = new Visit(); v3.setVisitTime(LocalDateTime.of(2024, 2, 1, 11, 0));
        when(vm.selectList(any())).thenReturn(List.of(v1, v2, v3));

        DashboardServiceImpl svc = new DashboardServiceImpl(pm, vm, im);
        List<Map<String, Object>> freq = svc.visitFrequency();

        assertThat(freq).hasSize(2);
        assertThat(freq.get(0).get("date")).isEqualTo("2024-01");
        assertThat(freq.get(0).get("count")).isEqualTo(2L);
    }
}
```

> 注：`DashboardServiceImpl` 构造器为 `(PatientMapper, VisitMapper, PrescriptionItemMapper)`，用 `@RequiredArgsConstructor` 或显式构造器注入均可。

- [ ] **Step 4: 运行测试 + 冒烟**

```bash
cd backend && mvn -q -pl his-server test
curl -s "http://localhost:8080/api/dashboard/stats/drug-distribution"
```
Expected：测试 PASS；接口返回非空数组（导入数据前可为空数组）。

- [ ] **Step 5: Commit**

```bash
git add backend/his-server && git commit -m "feat(server): dashboard stats + random patient endpoints"
```

---

### Task 6: 数据生成器 his-generator（独立模块）

**Files:**
- Create: `backend/his-generator/src/main/java/com/his/generator/HisGeneratorApplication.java`
- Create: `backend/his-generator/src/main/java/com/his/generator/GeneratorRunner.java`
- Create: `backend/his-generator/src/main/java/com/his/generator/MedicalData.java`
- Create: `backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java`
- Create: `backend/his-generator/src/main/resources/application.yml`
- Test: `backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java`

**Interfaces:**
- Consumes: 实体/Mapper（Task 2）、`rule_config`/`drug` 表数据（Task 1）。
- Produces: 可 `java -jar` 运行的生成器；`DataGeneratorService.generate(long patientCount)` 返回 `Map<String,Long>`（各表写入条数 + 耗时毫秒）。

- [ ] **Step 1: 编写生成器配置与启动类**

`application.yml`：

```yaml
spring:
  main:
    web-application-type: none
  datasource:
    url: jdbc:mysql://localhost:3306/his_demo?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai&rewriteBatchedStatements=true
    username: his_user
    password: his@2026
    driver-class-name: com.mysql.cj.jdbc.Driver
generator:
  patient-count: 10000
  min-visits: 5
  max-visits: 20
```

```java
package com.his.generator;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication(scanBasePackages = "com.his")
@MapperScan("com.his.common.mapper")
public class HisGeneratorApplication {
    public static void main(String[] args) {
        System.exit(SpringApplication.exit(
            SpringApplication.run(HisGeneratorApplication.class, args)));
    }
}
```

- [ ] **Step 2: 编写 MedicalData（内置医学字典）**

```java
package com.his.generator;

import java.util.List;

public final class MedicalData {
    public record DiagnosisRule(String code, String name, List<Long> drugIds) {}
    public record LabItem(String code, String name, String unit, String ref, double low, double high) {}

    public static final String[] DEPARTMENTS = {"内科","外科","儿科","妇科","五官科","皮肤科"};
    public static final String[] DOCTORS = {"王冠杰","李静","张伟","刘洋","陈晨","赵敏"};
    public static final String[] SURNAMES = {"王","李","张","刘","陈","杨","赵","黄","周","吴"};
    public static final String[] GIVEN = {"伟","芳","娜","敏","静","磊","军","洋","勇","艳","杰","涛","明","超","秀英"};

    public static final List<LabItem> LAB_ITEMS = List.of(
        new LabItem("BLD-RBC","红细胞计数","10^12/L","3.5-5.5",3.5,5.5),
        new LabItem("BLD-WBC","白细胞计数","10^9/L","4.0-10.0",4.0,10.0),
        new LabItem("BLD-GLU","空腹血糖","mmol/L","3.9-6.1",3.9,6.1),
        new LabItem("BLD-HB","血红蛋白","g/L","110-160",110,160),
        new LabItem("BLD-PLT","血小板计数","10^9/L","100-300",100,300)
    );
    // 规则从 rule_config 表动态加载，见 DataGeneratorService
    private MedicalData() {}
}
```

- [ ] **Step 3: 编写 DataGeneratorService（核心生成逻辑）**

```java
package com.his.generator;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.*;
import java.util.concurrent.ThreadLocalRandom;

@Service
@RequiredArgsConstructor
public class DataGeneratorService {

    private final PatientMapper patientMapper;
    private final VisitMapper visitMapper;
    private final DiagnosisMapper diagnosisMapper;
    private final PrescriptionMapper prescriptionMapper;
    private final PrescriptionItemMapper itemMapper;
    private final LabTestMapper labTestMapper;
    private final DrugMapper drugMapper;
    private final RuleConfigMapper ruleConfigMapper;

    private List<Drug> drugs;
    private List<MedicalData.DiagnosisRule> rules;
    private ThreadLocalRandom rnd;

    /** 生成 patientCount 名患者及各自就诊链，返回统计信息 */
    public Map<String, Long> generate(long patientCount) {
        long start = System.currentTimeMillis();
        rnd = ThreadLocalRandom.current();
        drugs = drugMapper.selectList(null);
        rules = loadRules();
        long visits = 0, diagnoses = 0, pres = 0, items = 0, labs = 0;
        for (long i = 0; i < patientCount; i++) {
            long[] cnt = generateOnePatient();
            visits += cnt[0]; diagnoses += cnt[1]; pres += cnt[2]; items += cnt[3]; labs += cnt[4];
        }
        Map<String, Long> stat = new LinkedHashMap<>();
        stat.put("patients", patientCount);
        stat.put("visits", visits);
        stat.put("diagnoses", diagnoses);
        stat.put("prescriptions", pres);
        stat.put("prescription_items", items);
        stat.put("lab_tests", labs);
        stat.put("elapsed_ms", System.currentTimeMillis() - start);
        return stat;
    }

    private long[] generateOnePatient() {
        Patient p = new Patient();
        p.setName(randomName());
        p.setGender(rnd.nextInt(2));
        int age = rnd.nextInt(18, 90);
        p.setBirthDate(LocalDate.now().minusYears(age));
        p.setIdCard(String.format("110101%d%04d%03d", rnd.nextInt(1940, 2020), rnd.nextInt(10000), rnd.nextInt(1000)));
        p.setPhone("13" + String.format("%09d", rnd.nextInt(1_000_000_000)));
        p.setAddress(randomName() + "市" + randomName() + "区" + rnd.nextInt(1, 999) + "号");
        p.setCreatedAt(LocalDateTime.now());
        patientMapper.insert(p);

        int visitCount = rnd.nextInt(5, 21);            // 5-20 次就诊
        int years = rnd.nextInt(1, 4);                  // 跨度 1-3 年
        LocalDateTime base = LocalDateTime.now().minusYears(years);
        LocalDateTime lastTime = base;

        long vCount=0,dCount=0,prCount=0,itCount=0,lCount=0;
        for (int j = 0; j < visitCount; j++) {
            // 时间严格递增：每次就诊间隔 3-120 天
            lastTime = lastTime.plusDays(rnd.nextInt(3, 120)).plusMinutes(rnd.nextInt(0, 600));
            Visit v = new Visit();
            v.setPatientId(p.getId());
            v.setVisitNo("V" + p.getId() + "-" + (j + 1));
            v.setDepartment(MedicalData.DEPARTMENTS[rnd.nextInt(MedicalData.DEPARTMENTS.length)]);
            v.setDoctor(MedicalData.DOCTORS[rnd.nextInt(MedicalData.DOCTORS.length)]);
            v.setVisitTime(lastTime);
            v.setStatus(0);
            visitMapper.insert(v);
            vCount++;

            // 诊断（从规则表随机选）
            MedicalData.DiagnosisRule rule = rules.get(rnd.nextInt(rules.size()));
            Diagnosis d = new Diagnosis();
            d.setPatientId(p.getId()); d.setVisitId(v.getId());
            d.setIcdCode(rule.code()); d.setDiagnosisName(rule.name());
            d.setDoctor(v.getDoctor()); d.setDiagnosedAt(lastTime.plusMinutes(rnd.nextInt(5, 20)));
            diagnosisMapper.insert(d);
            dCount++;

            // 处方（诊断对应药品，保证诊断↔用药合理）
            Prescription pr = new Prescription();
            pr.setPatientId(p.getId()); pr.setVisitId(v.getId());
            pr.setDoctor(v.getDoctor()); pr.setPrescribedAt(d.getDiagnosedAt().plusMinutes(rnd.nextInt(2, 10)));
            BigDecimal total = BigDecimal.ZERO;
            int drugCount = rnd.nextInt(1, Math.min(3, rule.drugIds().size()) + 1);
            List<Long> chosen = new ArrayList<>(rule.drugIds());
            Collections.shuffle(chosen, rnd);
            for (int k = 0; k < drugCount; k++) {
                Drug drug = drugs.stream().filter(x -> x.getId().equals(chosen.get(k))).findFirst().orElse(null);
                if (drug == null) continue;
                PrescriptionItem it = new PrescriptionItem();
                it.setPrescriptionId(pr.getId());
                it.setDrugId(drug.getId()); it.setDrugName(drug.getName());
                it.setDosage(rnd.nextInt(1, 3) + "片"); it.setFrequency("每日" + rnd.nextInt(1, 3) + "次");
                it.setDays(rnd.nextInt(3, 15)); it.setQuantity(BigDecimal.valueOf(rnd.nextInt(1, 4)));
                it.setUnit(drug.getUnit());
                total = total.add(drug.getPrice().multiply(it.getQuantity()));
                itemMapper.insert(it);
                itCount++;
            }
            pr.setTotalAmount(total);
            prescriptionMapper.insert(pr);
            prCount++;

            // 检验（随机 1-3 项）
            int labCount = rnd.nextInt(1, 4);
            for (int k = 0; k < labCount; k++) {
                MedicalData.LabItem li = MedicalData.LAB_ITEMS.get(rnd.nextInt(MedicalData.LAB_ITEMS.size()));
                LabTest lt = new LabTest();
                lt.setPatientId(p.getId()); lt.setVisitId(v.getId());
                lt.setItemCode(li.code()); lt.setItemName(li.name());
                double val = li.low() + rnd.nextDouble() * (li.high() - li.low());
                boolean abnormal = rnd.nextDouble() < 0.15;
                if (abnormal) val = li.high() + rnd.nextDouble() * li.high() * 0.3;
                lt.setResultValue(String.format("%.2f", val));
                lt.setUnit(li.unit()); lt.setReferenceRange(li.ref());
                lt.setIsAbnormal(abnormal ? 1 : 0);
                lt.setTestedAt(pr.getPrescribedAt().plusMinutes(rnd.nextInt(20, 90)));
                labTestMapper.insert(lt);
                lCount++;
            }
        }
        return new long[]{vCount,dCount,prCount,itCount,lCount};
    }

    private List<MedicalData.DiagnosisRule> loadRules() {
        return ruleConfigMapper.selectList(new LambdaQueryWrapper<RuleConfig>().eq(RuleConfig::getEnabled, 1))
                .stream()
                .map(r -> new MedicalData.DiagnosisRule(r.getDiagnosisCode(), r.getDiagnosisName(),
                        Arrays.stream(r.getDrugIds().split(",")).map(String::trim).filter(s->!s.isEmpty()).map(Long::parseLong).toList()))
                .filter(r -> !r.drugIds().isEmpty())
                .toList();
    }

    private String randomName() {
        return MedicalData.SURNAMES[rnd.nextInt(MedicalData.SURNAMES.length)]
             + MedicalData.GIVEN[rnd.nextInt(MedicalData.GIVEN.length)];
    }
}
```

> 性能优化：`rewriteBatchedStatements=true` 已配置；如需更快，可把 per-row `insert` 换成 `saveBatch`。本实现先保证正确性。

- [ ] **Step 4: 编写 GeneratorRunner（CommandLineRunner 触发）**

```java
package com.his.generator;

import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

import java.util.Map;

@Component
@RequiredArgsConstructor
public class GeneratorRunner implements CommandLineRunner {

    private final DataGeneratorService service;

    @Value("${generator.patient-count:10000}")
    private long patientCount;

    @Override
    public void run(String... args) {
        Map<String, Long> stat = service.generate(patientCount);
        System.out.println("=== 数据生成完成 ===");
        stat.forEach((k, v) -> System.out.println(k + " = " + v));
    }
}
```

- [ ] **Step 5: 编写生成逻辑单元测试（时间递增 + 规则约束）**

```java
package com.his.generator;

import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.*;
import java.util.concurrent.atomic.AtomicLong;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class DataGeneratorServiceTest {

    private final List<Visit> insertedVisits = new ArrayList<>();
    private final List<Diagnosis> insertedDiagnoses = new ArrayList<>();

    private DataGeneratorService buildService() {
        PatientMapper pm = mock(PatientMapper.class);
        VisitMapper vm = mock(VisitMapper.class);
        DiagnosisMapper dm = mock(DiagnosisMapper.class);
        PrescriptionMapper prm = mock(PrescriptionMapper.class);
        PrescriptionItemMapper im = mock(PrescriptionItemMapper.class);
        LabTestMapper lm = mock(LabTestMapper.class);
        DrugMapper drugM = mock(DrugMapper.class);
        RuleConfigMapper rcM = mock(RuleConfigMapper.class);

        Drug d1 = new Drug(); d1.setId(1L); d1.setName("感冒灵颗粒"); d1.setPrice(new BigDecimal("12.00")); d1.setUnit("盒");
        Drug d2 = new Drug(); d2.setId(2L); d2.setName("布洛芬"); d2.setPrice(new BigDecimal("15.80")); d2.setUnit("盒");
        when(drugM.selectList(any())).thenReturn(List.of(d1, d2));

        RuleConfig r1 = new RuleConfig(); r1.setDiagnosisCode("J06.9"); r1.setDiagnosisName("上呼吸道感染"); r1.setDrugIds("1,2"); r1.setEnabled(1);
        when(rcM.selectList(any())).thenReturn(List.of(r1));

        AtomicLong patientSeq = new AtomicLong(0);
        doAnswer(inv -> { Patient p = inv.getArgument(0); p.setId(patientSeq.incrementAndGet()); return 1; }).when(pm).insert(any());
        doAnswer(inv -> { Visit v = inv.getArgument(0); v.setId(System.nanoTime()); insertedVisits.add(v); return 1; }).when(vm).insert(any());
        doAnswer(inv -> { Diagnosis d = inv.getArgument(0); d.setId(System.nanoTime()); insertedDiagnoses.add(d); return 1; }).when(dm).insert(any());
        doAnswer(inv -> { Prescription p = inv.getArgument(0); p.setId(System.nanoTime()); return 1; }).when(prm).insert(any());
        when(im.insert(any())).thenReturn(1);
        when(lm.insert(any())).thenReturn(1);

        return new DataGeneratorService(pm, vm, dm, prm, im, lm, drugM, rcM);
    }

    @Test
    void visits_are_strictly_increasing_in_time() {
        DataGeneratorService svc = buildService();
        Map<String, Long> stat = svc.generate(3);

        assertThat(stat.get("patients")).isEqualTo(3L);
        assertThat(stat.get("visits")).isGreaterThanOrEqualTo(15L); // 3 × 最少 5 次

        Map<Long, List<Visit>> byPatient = new HashMap<>();
        for (Visit v : insertedVisits) {
            byPatient.computeIfAbsent(v.getPatientId(), k -> new ArrayList<>()).add(v);
        }
        assertThat(byPatient.keySet()).hasSize(3);
        for (List<Visit> visits : byPatient.values()) {
            for (int i = 1; i < visits.size(); i++) {
                assertThat(visits.get(i).getVisitTime()).isAfter(visits.get(i - 1).getVisitTime());
            }
        }
    }

    @Test
    void diagnoses_come_from_rule_config() {
        DataGeneratorService svc = buildService();
        svc.generate(2);

        assertThat(insertedDiagnoses).isNotEmpty();
        for (Diagnosis d : insertedDiagnoses) {
            assertThat(d.getIcdCode()).isEqualTo("J06.9");
        }
    }
}
```

> 注：`DataGeneratorService` 构造器参数顺序须与 `@RequiredArgsConstructor` 生成的字段顺序一致：`(patientMapper, visitMapper, diagnosisMapper, prescriptionMapper, itemMapper, labTestMapper, drugMapper, ruleConfigMapper)`。

- [ ] **Step 6: 运行测试 + 小规模冒烟生成**

```bash
cd backend && mvn -q -pl his-generator test
mvn -q -pl his-generator spring-boot:run -Dspring-boot.run.arguments="--generator.patient-count=10"
```
Expected：测试 PASS；打印 `patients=10`、`visits>=50`、`elapsed_ms`。

- [ ] **Step 7: 全量生成（≥1 万患者 / ≥5 万就诊）**

```bash
cd backend && mvn -q -pl his-generator spring-boot:run -Dspring-boot.run.arguments="--generator.patient-count=10000"
mysql -uhis_user -phis@2026 -e "SELECT COUNT(*) patients FROM his_demo.patient; SELECT COUNT(*) visits FROM his_demo.visit;"
```
Expected：`patients>=10000`、`visits>=50000`，记录 `elapsed_ms` 供测试报告用。

- [ ] **Step 8: Commit**

```bash
git add backend/his-generator && git commit -m "feat(generator): independent data generator with rule-constrained medical logic"
```

---

### Task 7: 前端脚手架 + 患者列表页

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.js`
- Create: `frontend/src/App.vue`
- Create: `frontend/src/router/index.js`
- Create: `frontend/src/api/request.js`
- Create: `frontend/src/api/patient.js`
- Create: `frontend/src/views/PatientList.vue`

**Interfaces:**
- Consumes: `GET /api/patients`（Task 3）契约：`{code, message, data:{records:[Patient], total, current, size}}`。
- Produces: 前端路由骨架 + axios 封装 + 患者列表页（搜索/分页）。

- [ ] **Step 1: 编写 `package.json` 并安装依赖**

```json
{
  "name": "his-frontend",
  "version": "1.0.0",
  "private": true,
  "scripts": { "dev": "vite", "build": "vite build", "preview": "vite preview" },
  "dependencies": {
    "vue": "^3.5.13",
    "vue-router": "^4.4.5",
    "pinia": "^2.2.6",
    "axios": "^1.7.7",
    "element-plus": "^2.8.6",
    "@element-plus/icons-vue": "^2.3.1",
    "echarts": "^5.5.1"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.1.4",
    "vite": "^5.4.10"
  }
}
```

```bash
cd frontend && npm install
```

- [ ] **Step 2: 编写 `vite.config.js`（代理 `/api` 到 8080）**

```js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://localhost:8080', changeOrigin: true } }
  }
})
```

- [ ] **Step 3: 编写入口 `index.html`、`main.js`、`App.vue`、`router/index.js`**

`main.js` 注册 Element Plus、Pinia、Router：

```js
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'

createApp(App).use(createPinia()).use(router).use(ElementPlus).mount('#app')
```

`router/index.js` 路由：`/` → PatientList、`/patient/:id` → PatientDetail、`/entry` → EntryForm、`/dashboard` → Dashboard。

`App.vue`：`<el-container>` 布局，顶部 `el-menu` 导航（患者列表 / 录入 / 看板）+ `<router-view>`。

- [ ] **Step 4: 编写 axios 封装 `request.js`**

```js
import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({ baseURL: '/api', timeout: 30000 })
request.interceptors.response.use(
  (res) => {
    if (res.data.code !== 0) { ElMessage.error(res.data.message || '请求失败'); return Promise.reject(res.data) }
    return res.data
  },
  (err) => { ElMessage.error(err.message || '网络错误'); return Promise.reject(err) }
)
export default request
```

- [ ] **Step 5: 编写 `api/patient.js`**

```js
import request from './request'
export const listPatients = (params) => request.get('/patients', { params })
export const getPatient = (id) => request.get(`/patients/${id}`)
export const getTimeline = (id) => request.get(`/patients/${id}/timeline`)
export const createPatient = (data) => request.post('/patients', data)
```

- [ ] **Step 6: 编写 PatientList.vue（搜索 + 分页表格）**

`el-table` 显示（姓名/性别/年龄/身份证/电话/地址），`el-input` 关键词搜索、`el-pagination` 分页，行点击 `router.push('/patient/'+row.id)`。

- [ ] **Step 7: 启动前端验证**

```bash
cd frontend && npm run dev
```
浏览器打开 `http://localhost:5173`，确认患者列表正常加载后端数据（需后端 8080 已启动）。

- [ ] **Step 8: Commit**

```bash
git add frontend && git commit -m "feat(frontend): scaffold + patient list page"
```

---

### Task 8: 患者详情时间轴页（重点）

**Files:**
- Create: `frontend/src/views/PatientDetail.vue`
- Create: `frontend/src/components/Timeline.vue`

**Interfaces:**
- Consumes: `GET /api/patients/{id}`、`GET /api/patients/{id}/timeline`（事件 `type/time/data`）。

- [ ] **Step 1: 编写 `Timeline.vue`（事件卡片组件）**

`props: events`；用 Element Plus `el-timeline` 按 `time` 升序渲染；根据 `type` 显示不同颜色与图标：
- `VISIT`（蓝色）：`就诊科室 + 医生 + visitNo`
- `DIAGNOSIS`（红色）：`诊断名（icdCode）`
- `PRESCRIPTION`（绿色）：处方卡片，含药品明细 `items`（药名/用量/频次/数量）
- `LAB_TEST`（橙色）：`项目名 + 结果值 + 参考范围`，`is_abnormal=1` 时标红「异常」

- [ ] **Step 2: 编写 `PatientDetail.vue`**

左侧基本信息卡片（`el-descriptions`：姓名/性别/年龄/身份证/电话/地址/建档时间），右侧 `Timeline` 组件；`onMounted` 并发请求患者详情与时间线。

- [ ] **Step 3: 验证**

打开 `http://localhost:5173/patient/{某个有数据的id}`，确认时间轴事件卡片按时间升序、颜色/图标区分、处方含明细、异常检验标红。

- [ ] **Step 4: Commit**

```bash
git add frontend && git commit -m "feat(frontend): patient detail timeline view"
```

---

### Task 9: 诊疗录入表单页

**Files:**
- Create: `frontend/src/views/EntryForm.vue`
- Create: `frontend/src/api/business.js`

**Interfaces:**
- Consumes: `POST /api/visits`、`/api/diagnoses`、`/api/prescriptions`、`/api/lab-tests`（Task 3）。

- [ ] **Step 1: 编写 `api/business.js`**

```js
import request from './request'
export const createVisit = (d) => request.post('/visits', d)
export const createDiagnosis = (d) => request.post('/diagnoses', d)
export const createPrescription = (d) => request.post('/prescriptions', d)
export const createLabTest = (d) => request.post('/lab-tests', d)
```

- [ ] **Step 2: 编写 `EntryForm.vue`（Tabs 四个表单）**

`el-tabs` 四个标签页：挂号登记、诊断录入、处方开具、检验录入。每个表单提交成功后 `ElMessage.success`。处方页用 `el-table` 动态增删药品明细行（药名/用量/频次/天数/数量/单位）。所有表单需 `patientId` 输入框（患者 ID）。

- [ ] **Step 3: 验证**

填写并提交各表单，随后进入该患者详情页确认新事件出现在时间轴（验证缓存失效）。

- [ ] **Step 4: Commit**

```bash
git add frontend && git commit -m "feat(frontend): entry forms for visit/diagnosis/prescription/lab"
```

---

### Task 10: 数据看板页

**Files:**
- Create: `frontend/src/views/Dashboard.vue`
- Create: `frontend/src/api/dashboard.js`
- Create: `frontend/src/components/Chart.vue`（ECharts 封装）

**Interfaces:**
- Consumes: `GET /api/dashboard/random-patient`、`/stats/visit-frequency`、`/stats/drug-distribution`（Task 5）。

- [ ] **Step 1: 编写 `api/dashboard.js` 与 `Chart.vue`**

`Chart.vue`：`props: option`，`onMounted` 用 `echarts.init` 渲染，`watch(option)` 更新，`resize` 自适应。

- [ ] **Step 2: 编写 `Dashboard.vue`**

- 「随机抽取患者」按钮 → 请求 `random-patient` → 下方渲染该患者 `Timeline`（复用 Task 8 组件）+ 请求其 `timeline`。
- 就诊频率：`visit-frequency` 数据 → ECharts 折线/柱状图（x=月份，y=count）。
- 用药分布：`drug-distribution` 数据 → ECharts 饼图或横向柱状（x=drugName，y=count）。

- [ ] **Step 3: 验证**

打开 `http://localhost:5173/dashboard`，确认随机患者时间轴 + 两张图表正确渲染。

- [ ] **Step 4: Commit**

```bash
git add frontend && git commit -m "feat(frontend): dashboard with random timeline + stats charts"
```

---

### Task 11: 功能与性能测试 + 报告

**Files:**
- Create: `backend/his-server/src/test/java/com/his/server/PerformanceTest.java`（或脚本）
- Create: `docs/test-report.md`

**Interfaces:**
- Consumes: 全部已实现接口与生成数据。

- [ ] **Step 1: 编写功能测试用例清单（记录到报告）**

覆盖：患者建档、患者查询（分页/关键词）、挂号登记、诊断录入、处方开具（含明细）、检验录入、时间线查询（升序）、随机患者、就诊频率、用药分布。逐条记录「用例名 / 步骤 / 预期 / 实际 / 结果」。

- [ ] **Step 2: 编写性能测试脚本（批量写入 + 时间线查询耗时）**

`PerformanceTest.java`：用 `RestTemplate`/`WebClient` 对运行中的后端发起 N 次时间线查询，统计平均/最大/最小响应时间（缓存命中前后各一轮）。批量写入耗时直接取 Task 6 生成器输出的 `elapsed_ms`。

- [ ] **Step 3: 运行并采集数据**

```bash
cd backend && mvn -q -pl his-server test
# 记录各用例结果与响应时间到 docs/test-report.md
```

- [ ] **Step 4: 编写 `docs/test-report.md`**

含：功能用例执行表、性能测试表（批量写入耗时、时间线查询响应时间含缓存对比）、数据完整性/时间一致性/分布合理性评估、系统局限与改进方向（对应 spec 第 6 部分）。

- [ ] **Step 5: Commit**

```bash
git add docs backend/his-server/src/test && git commit -m "test: functional + performance tests and report"
```

---

## 完成标准

- 后端 8080 可启动，`/api/patients/{id}/timeline` 返回升序事件，Redis 缓存生效。
- 生成器可独立运行，导入 ≥1 万患者 / ≥5 万就诊记录，时间递增、诊断↔用药规则约束成立。
- 前端 5173 可访问：患者列表 / 详情时间轴 / 录入表单 / 看板四页可用。
- `docs/test-report.md` 完成功能与性能测试记录与分析。
