# 录入工作流实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「诊断/处方/检验」录入从自由文本手填改为带医生身份、按科室范围、有上下文提示的工作流。

**Architecture:** 新增 `doctor` 表（字典，seed 灌入 24 名医生）；后端加 3 个查询接口（医生列表、按科室查患者、按患者+科室查就诊）；生成器改为「主科室 80/20」分布且 doctor↔科室严格一致；前端 `EntryForm.vue` 顶部医生选择条 + 共享患者/就诊选择 + 上下文条。

**Tech Stack:** Spring Boot 3.3.5 / Java 21 / MyBatis-Plus 3.5.7 / MySQL 8 / Redis 8；Vue 3 + Vite + Element Plus + Pinia。

**Spec:** `docs/superpowers/specs/2026-09-20-entry-workflow-design.md`（绑定权威，冲突以它为准）。

## Global Constraints

- 不引入账号密码 / session / JWT 鉴权；「登录」= 轻量身份选择。
- `doctor` 表 24 名医生名单固定（见 spec 1.2）；**任何模拟数据不得使用「王冠杰」这个名字**。
- 主科室 80/20 分布；`visit.doctor` 必须来自 `visit.department` 对应的医生集合（doctor↔科室一致）。
- `lab_test` 不加 doctor 字段；检验录入只做上下文展示。
- 挂号登记 tab 保持现状。
- 后端所有接口返回统一信封 `Result{code,message,data}`，`code=0` 成功；异常由 `GlobalExceptionHandler` 包装。
- 创建接口的缓存失效逻辑（`CacheInvalidator.evictTimeline`）保持不变，不动四个 `POST /api/*` 创建接口。
- 测试沿用现有基建：controller 用 `@WebMvcTest`+`@MockBean`，service/生成器用 Mockito；`@WebMvcTest` 切片自动排除 `MybatisPlusConfig`，故无需 mock mapper。
- 每个 git commit 信息结尾附一行 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 后端测试命令：`cd backend && mvn -q test`（应全绿）。前端验证：`cd frontend && npm run build`。

---

## 文件结构（本次改动全景）

**后端 his-common（新建/修改）：**
- `entity/Doctor.java`（新）
- `mapper/DoctorMapper.java`（新）
- `mapper/PatientMapper.java`（加 `selectPageByDepartment` @Select）

**后端 his-server：**
- `service/DoctorService.java` + `service/impl/DoctorServiceImpl.java`（新）
- `controller/DoctorController.java`（新）
- `service/PatientService.java` + `impl/PatientServiceImpl.java`（加 `pageByDepartment`）
- `controller/PatientController.java`（list 加 `department` 参数）
- `controller/VisitController.java`（加 GET list）

**后端 his-generator：**
- `DataGeneratorService.java`（加载医生 + 主科室分布 + doctor↔科室一致）
- `MedicalData.java`（删 `DEPARTMENTS`/`DOCTORS`）

**SQL：**
- `backend/sql/schema.sql`（加 doctor 表 + visit 科室索引）
- `backend/sql/seed.sql`（加 24 名医生）

**前端：**
- `src/api/business.js`（加 `listDoctors`/`listVisits`）
- `src/views/EntryForm.vue`（重做）

**测试：**
- `his-server/.../controller/DoctorControllerTest.java`（新）
- `his-server/.../controller/PatientControllerTest.java`（加用例）
- `his-server/.../controller/VisitControllerTest.java`（新）
- `his-generator/.../DataGeneratorServiceTest.java`（更新 buildService + 加用例）

---

### Task 1: doctor 表 + Doctor 实体/Mapper + SQL

**Files:**
- Create: `backend/his-common/src/main/java/com/his/common/entity/Doctor.java`
- Create: `backend/his-common/src/main/java/com/his/common/mapper/DoctorMapper.java`
- Modify: `backend/sql/schema.sql`
- Modify: `backend/sql/seed.sql`

**Interfaces:**
- Produces: `Doctor`（`id/name/department`）、`DoctorMapper`（`BaseMapper<Doctor>`），供 Task 2/5 使用；表 `doctor` 供 Task 3/5 依赖。

- [ ] **Step 1: 建 `Doctor` 实体**

`backend/his-common/src/main/java/com/his/common/entity/Doctor.java`：

```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

@Data
@TableName("doctor")
public class Doctor {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String name;
    private String department;
}
```

- [ ] **Step 2: 建 `DoctorMapper`**

`backend/his-common/src/main/java/com/his/common/mapper/DoctorMapper.java`：

```java
package com.his.common.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.his.common.entity.Doctor;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface DoctorMapper extends BaseMapper<Doctor> {
}
```

- [ ] **Step 3: schema.sql 加 doctor 表 + visit 索引**

在 `backend/sql/schema.sql` 的 `visit` 建表语句内，把索引行改为（追加一行）：

```sql
  status TINYINT NOT NULL DEFAULT 0, remark VARCHAR(255),
  KEY idx_visit_patient_time (patient_id, visit_time),
  KEY idx_visit_department (department)
```

在文件末尾追加 doctor 表：

```sql
CREATE TABLE doctor (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(50) NOT NULL,
  department VARCHAR(50) NOT NULL,
  KEY idx_doctor_department (department)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='医生表';
```

- [ ] **Step 4: seed.sql 加 24 名医生**

在 `backend/sql/seed.sql` 末尾追加：

```sql
INSERT INTO doctor (name, department) VALUES
('李建华','内科'),('张国庆','内科'),('刘志强','内科'),('陈秀英','内科'),('赵雪梅','内科'),
('杨文博','外科'),('黄建军','外科'),('周国平','外科'),('吴丽华','外科'),('徐永强','外科'),
('孙晓丽','儿科'),('胡雪丹','儿科'),('朱慧敏','儿科'),('高建明','儿科'),
('林丽霞','妇科'),('何美玲','妇科'),('郭秀兰','妇科'),('马静怡','妇科'),
('罗志远','五官科'),('梁晓东','五官科'),('宋慧娟','五官科'),
('唐建华','皮肤科'),('冯雪琴','皮肤科'),('邓丽娟','皮肤科');
```

- [ ] **Step 5: 编译验证**

Run: `cd backend && mvn -q -pl his-common -am compile`
Expected: BUILD SUCCESS（实体/Mapper 可编译）。

- [ ] **Step 6: Commit**

```bash
git add backend/his-common/src/main/java/com/his/common/entity/Doctor.java backend/his-common/src/main/java/com/his/common/mapper/DoctorMapper.java backend/sql/schema.sql backend/sql/seed.sql
git commit -m "feat: 新增 doctor 表与 Doctor 实体/Mapper，seed 24 名医生

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: GET /api/doctors（DoctorService + DoctorController）

**Files:**
- Test: `backend/his-server/src/test/java/com/his/server/controller/DoctorControllerTest.java`
- Create: `backend/his-server/src/main/java/com/his/server/service/DoctorService.java`
- Create: `backend/his-server/src/main/java/com/his/server/service/impl/DoctorServiceImpl.java`
- Create: `backend/his-server/src/main/java/com/his/server/controller/DoctorController.java`

**Interfaces:**
- Consumes: Task 1 的 `Doctor` / `DoctorMapper`。
- Produces: `GET /api/doctors` → `Result<List<Doctor>>`，供 Task 6 前端登录下拉使用。

- [ ] **Step 1: 写失败测试**

`backend/his-server/src/test/java/com/his/server/controller/DoctorControllerTest.java`：

```java
package com.his.server.controller;

import com.his.common.entity.Doctor;
import com.his.server.service.DoctorService;
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

@WebMvcTest(DoctorController.class)
class DoctorControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean DoctorService doctorService;

    @Test
    void list_returns_doctors() throws Exception {
        Doctor d = new Doctor();
        d.setId(1L); d.setName("李建华"); d.setDepartment("内科");
        when(doctorService.list(any())).thenReturn(List.of(d));

        mockMvc.perform(get("/api/doctors"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data[0].name").value("李建华"))
                .andExpect(jsonPath("$.data[0].department").value("内科"));
    }
}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && mvn -q -pl his-server -am test -Dtest=DoctorControllerTest`
Expected: 编译失败（`DoctorService`/`DoctorController` 不存在）或测试失败。

- [ ] **Step 3: 实现 Service + Controller**

`backend/his-server/src/main/java/com/his/server/service/DoctorService.java`：

```java
package com.his.server.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.his.common.entity.Doctor;

public interface DoctorService extends IService<Doctor> {
}
```

`backend/his-server/src/main/java/com/his/server/service/impl/DoctorServiceImpl.java`：

```java
package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Doctor;
import com.his.common.mapper.DoctorMapper;
import com.his.server.service.DoctorService;
import org.springframework.stereotype.Service;

@Service
public class DoctorServiceImpl extends ServiceImpl<DoctorMapper, Doctor> implements DoctorService {
}
```

`backend/his-server/src/main/java/com/his/server/controller/DoctorController.java`：

```java
package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.common.Result;
import com.his.common.entity.Doctor;
import com.his.server.service.DoctorService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/doctors")
@RequiredArgsConstructor
public class DoctorController {

    private final DoctorService doctorService;

    @GetMapping
    public Result<List<Doctor>> list() {
        return Result.ok(doctorService.list(
                new LambdaQueryWrapper<Doctor>()
                        .orderByAsc(Doctor::getDepartment)
                        .orderByAsc(Doctor::getId)));
    }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && mvn -q -pl his-server -am test -Dtest=DoctorControllerTest`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/his-server/src/main/java/com/his/server/service/DoctorService.java backend/his-server/src/main/java/com/his/server/service/impl/DoctorServiceImpl.java backend/his-server/src/main/java/com/his/server/controller/DoctorController.java backend/his-server/src/test/java/com/his/server/controller/DoctorControllerTest.java
git commit -m "feat: 新增 GET /api/doctors 医生列表接口

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: 按科室分页查患者（GET /api/patients?department=）

**Files:**
- Modify: `backend/his-common/src/main/java/com/his/common/mapper/PatientMapper.java`
- Modify: `backend/his-server/src/main/java/com/his/server/service/PatientService.java`
- Modify: `backend/his-server/src/main/java/com/his/server/service/impl/PatientServiceImpl.java`
- Modify: `backend/his-server/src/main/java/com/his/server/controller/PatientController.java`
- Modify: `backend/his-server/src/test/java/com/his/server/controller/PatientControllerTest.java`

**Interfaces:**
- Consumes: Task 1 的 `doctor` 表（SQL 子查询依赖 `visit.department`）。
- Produces: `PatientService.pageByDepartment(long page, long size, String department, String keyword)` → `Page<Patient>`，供 Task 6 患者搜索下拉。

- [ ] **Step 1: 写失败测试（PatientControllerTest 追加用例）**

在 `PatientControllerTest.java` 追加 import 与方法：

```java
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
```

```java
@Test
void list_with_department_delegates_to_pageByDepartment() throws Exception {
    when(patientService.pageByDepartment(1, 10, "内科", null)).thenReturn(new Page<>());

    mockMvc.perform(get("/api/patients").param("department", "内科"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.code").value(0));

    verify(patientService).pageByDepartment(1, 10, "内科", null);
    verify(patientService, never()).page(any(), any());
}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && mvn -q -pl his-server -am test -Dtest=PatientControllerTest`
Expected: 编译失败（`pageByDepartment` 方法不存在）。

- [ ] **Step 3: Mapper 加自定义分页查询**

`backend/his-common/src/main/java/com/his/common/mapper/PatientMapper.java` 追加 import：

```java
import com.baomidou.mybatisplus.core.metadata.IPage;
import org.apache.ibatis.annotations.Param;
```

并在接口内追加方法：

```java
/**
 * 按科室分页查患者：取「在该科室有过至少一次就诊」的患者。
 * 用参数化子查询避免把 department 拼进 SQL 字符串（防注入）。
 */
@Select("<script>"
        + "SELECT * FROM patient WHERE id IN "
        + "(SELECT DISTINCT patient_id FROM visit WHERE department = #{department})"
        + "<if test=\"keyword != null and keyword != ''\">"
        + " AND (name LIKE CONCAT('%', #{keyword}, '%')"
        + " OR id_card LIKE CONCAT('%', #{keyword}, '%')"
        + " OR phone LIKE CONCAT('%', #{keyword}, '%'))"
        + "</if>"
        + " ORDER BY id DESC"
        + "</script>")
IPage<Patient> selectPageByDepartment(IPage<Patient> page,
                                      @Param("department") String department,
                                      @Param("keyword") String keyword);
```

- [ ] **Step 4: Service 加方法**

`backend/his-server/src/main/java/com/his/server/service/PatientService.java` 追加 import 与方法：

```java
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
```

```java
Page<Patient> pageByDepartment(long page, long size, String department, String keyword);
```

`backend/his-server/src/main/java/com/his/server/service/impl/PatientServiceImpl.java` 改为：

```java
package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Patient;
import com.his.common.mapper.PatientMapper;
import com.his.server.service.PatientService;
import org.springframework.stereotype.Service;

@Service
public class PatientServiceImpl extends ServiceImpl<PatientMapper, Patient> implements PatientService {

    @Override
    public Page<Patient> pageByDepartment(long page, long size, String department, String keyword) {
        // mapper 返回 IPage，但运行时对象就是传入的 Page 实例（分页插件原地改写），强转安全
        return (Page<Patient>) baseMapper.selectPageByDepartment(new Page<>(page, size), department, keyword);
    }
}
```

- [ ] **Step 5: Controller 加 department 参数**

`backend/his-server/src/main/java/com/his/server/controller/PatientController.java` 的 `list` 方法改为：

```java
@GetMapping
public Result<Page<Patient>> list(@RequestParam(defaultValue = "1") long page,
                                  @RequestParam(defaultValue = "10") long size,
                                  @RequestParam(required = false) String keyword,
                                  @RequestParam(required = false) String department) {
    if (department != null && !department.isBlank()) {
        return Result.ok(patientService.pageByDepartment(page, size, department, keyword));
    }
    LambdaQueryWrapper<Patient> qw = new LambdaQueryWrapper<>();
    if (keyword != null && !keyword.isBlank()) {
        qw.like(Patient::getName, keyword)
          .or().like(Patient::getIdCard, keyword)
          .or().like(Patient::getPhone, keyword);
    }
    qw.orderByDesc(Patient::getId);
    return Result.ok(patientService.page(new Page<>(page, size), qw));
}
```

（`Page` 已在文件内 import，`LambdaQueryWrapper` 亦然。）

- [ ] **Step 6: 跑测试确认通过**

Run: `cd backend && mvn -q -pl his-server -am test -Dtest=PatientControllerTest`
Expected: PASS（含新用例）。

- [ ] **Step 7: Commit**

```bash
git add backend/his-common/src/main/java/com/his/common/mapper/PatientMapper.java backend/his-server/src/main/java/com/his/server/service/PatientService.java backend/his-server/src/main/java/com/his/server/service/impl/PatientServiceImpl.java backend/his-server/src/main/java/com/his/server/controller/PatientController.java backend/his-server/src/test/java/com/his/server/controller/PatientControllerTest.java
git commit -m "feat: GET /api/patients 支持按科室分页查患者

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: 就诊查询（GET /api/visits）

**Files:**
- Test: `backend/his-server/src/test/java/com/his/server/controller/VisitControllerTest.java`
- Modify: `backend/his-server/src/main/java/com/his/server/controller/VisitController.java`

**Interfaces:**
- Consumes: `VisitService.list(Wrapper)`（MyBatis-Plus IService 自带）。
- Produces: `GET /api/visits?patientId=&department=` → `Result<List<Visit>>`，供 Task 6 就诊下拉。

- [ ] **Step 1: 写失败测试**

`backend/his-server/src/test/java/com/his/server/controller/VisitControllerTest.java`：

```java
package com.his.server.controller;

import com.his.common.entity.Visit;
import com.his.server.service.VisitService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDateTime;
import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(VisitController.class)
class VisitControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean VisitService visitService;

    @Test
    void list_returns_visits_for_patient() throws Exception {
        Visit v = new Visit();
        v.setId(1L); v.setPatientId(10L); v.setVisitNo("V10-1");
        v.setDepartment("内科"); v.setVisitTime(LocalDateTime.of(2026, 9, 20, 9, 0));
        when(visitService.list(any())).thenReturn(List.of(v));

        mockMvc.perform(get("/api/visits")
                        .param("patientId", "10")
                        .param("department", "内科"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data[0].visitNo").value("V10-1"));
    }
}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && mvn -q -pl his-server -am test -Dtest=VisitControllerTest`
Expected: 405（`/api/visits` 尚无 GET）。

- [ ] **Step 3: Controller 加 GET**

`backend/his-server/src/main/java/com/his/server/controller/VisitController.java` 追加 import：

```java
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import java.util.List;
```

并在类内追加方法：

```java
@GetMapping
public Result<List<Visit>> list(@RequestParam Long patientId,
                                @RequestParam(required = false) String department) {
    LambdaQueryWrapper<Visit> qw = new LambdaQueryWrapper<Visit>()
            .eq(Visit::getPatientId, patientId);
    if (department != null && !department.isBlank()) {
        qw.eq(Visit::getDepartment, department);
    }
    qw.orderByDesc(Visit::getVisitTime);
    return Result.ok(visitService.list(qw));
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && mvn -q -pl his-server -am test -Dtest=VisitControllerTest`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/his-server/src/main/java/com/his/server/controller/VisitController.java backend/his-server/src/test/java/com/his/server/controller/VisitControllerTest.java
git commit -m "feat: 新增 GET /api/visits 按患者+科室查就诊

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: 生成器改造（主科室分布 + doctor↔科室一致）

**Files:**
- Modify: `backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java`
- Modify: `backend/his-generator/src/main/java/com/his/generator/MedicalData.java`
- Modify: `backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java`

**Interfaces:**
- Consumes: Task 1 的 `Doctor` / `DoctorMapper`。
- Produces: 生成数据满足「主科室 80/20 + doctor↔科室一致」，供整个演示数据使用。
- **重要**：`DataGeneratorService` 的 `@RequiredArgsConstructor` 构造器签名因新增 `doctorMapper` 字段而变化，`DataGeneratorServiceTest.buildService()` 必须同步更新。

- [ ] **Step 1: 更新 buildService（让现有测试可编译）**

`DataGeneratorServiceTest.java`：
- 加字段 `private DoctorMapper doctorMapper;`（与 `ruleConfigMapper` 并列）。
- `buildService()` 内新增：

```java
doctorMapper = mock(DoctorMapper.class);
Doctor d1 = new Doctor(); d1.setId(1L); d1.setName("李建华"); d1.setDepartment("内科");
Doctor d2 = new Doctor(); d2.setId(2L); d2.setName("杨文博"); d2.setDepartment("外科");
when(doctorMapper.selectList(any())).thenReturn(List.of(d1, d2));
```

- 构造调用改为（在末尾加 `doctorMapper`）：

```java
return new DataGeneratorService(pm, vm, dm, prescriptionMapper, im, lm, drugM, ruleConfigMapper, doctorMapper, jdbcTemplate);
```

- [ ] **Step 2: 写失败测试（新增三个用例）**

在 `DataGeneratorServiceTest.java` 追加：

```java
@Test
void visit_doctor_matches_its_department() {
    DataGeneratorService svc = buildService();
    svc.generate(50);

    Map<String, List<String>> deptDoctors = Map.of(
            "内科", List.of("李建华"),
            "外科", List.of("杨文博"));
    for (Visit v : insertedVisits) {
        assertThat(deptDoctors.get(v.getDepartment())).contains(v.getDoctor());
    }
}

@Test
void patients_are_concentrated_in_a_home_department() {
    DataGeneratorService svc = buildService();
    svc.generate(200);

    for (List<Visit> visits : visitsByPatient().values()) {
        Map<String, Long> deptCount = visits.stream()
                .collect(Collectors.groupingBy(Visit::getDepartment, Collectors.counting()));
        long max = deptCount.values().stream().mapToLong(Long::longValue).max().orElse(0);
        assertThat(max).isGreaterThan(visits.size() / 2);
    }
}

@Test
void fails_fast_when_doctor_table_empty() {
    DataGeneratorService svc = buildService();
    when(doctorMapper.selectList(any())).thenReturn(List.of());

    assertThatThrownBy(() -> svc.generate(1))
            .isInstanceOf(IllegalStateException.class)
            .hasMessageContaining("doctor");
}
```

（`Collectors` 已在文件 import；`assertThatThrownBy`、`any`、`when` 均已 import。）

- [ ] **Step 3: 跑测试确认失败**

Run: `cd backend && mvn -q -pl his-generator -am test -Dtest=DataGeneratorServiceTest`
Expected: 编译失败（构造器参数数量不匹配）或新用例失败（doctor 仍随机）。

- [ ] **Step 4: MedicalData 删废弃数组**

`backend/his-generator/src/main/java/com/his/generator/MedicalData.java` 删除两行：

```java
public static final String[] DEPARTMENTS = {"内科","外科","儿科","妇科","五官科","皮肤科"};
public static final String[] DOCTORS = {"王冠杰","李静","张伟","刘洋","陈晨","赵敏"};
```

（保留 `SURNAMES`/`GIVEN`/`LAB_ITEMS`。）

- [ ] **Step 5: DataGeneratorService 改造**

新增常量与字段：

```java
/** 每次就诊落在主科室的概率（其余落到其他科室） */
private static final double HOME_DEPT_RATIO = 0.8;
```

```java
private final DoctorMapper doctorMapper;   // 放在 ruleConfigMapper 之后、jdbcTemplate 之前
private Map<String, List<String>> doctorsByDept;
```

在 `generate()` 内、`rules = loadRules();` 之后加一行：

```java
doctorsByDept = loadDoctors();
```

新增方法：

```java
/** 加载医生并按键分组为「科室 → 医生姓名列表」；表为空时快速失败。 */
private Map<String, List<String>> loadDoctors() {
    Map<String, List<String>> byDept = doctorMapper.selectList(null).stream()
            .collect(Collectors.groupingBy(Doctor::getDepartment,
                    Collectors.mapping(Doctor::getName, Collectors.toList())));
    if (byDept.isEmpty()) {
        throw new IllegalStateException(
                "doctor 表为空，请先执行 backend/sql/seed.sql 写入医生种子数据");
    }
    return byDept;
}
```

把 `newVisits(Patient p)` 中的科室/医生赋值改为：

```java
List<String> departments = new ArrayList<>(doctorsByDept.keySet());
String homeDept = departments.get(rnd.nextInt(departments.size()));

long[] offsets = visitOffsets(visitCount, windowMinutes);
List<Visit> visits = new ArrayList<>(visitCount);
for (int j = 0; j < visitCount; j++) {
    String department = (rnd.nextDouble() < HOME_DEPT_RATIO)
            ? homeDept
            : pickOtherDept(departments, homeDept);
    List<String> doctors = doctorsByDept.get(department);
    String doctor = doctors.get(rnd.nextInt(doctors.size()));

    Visit v = new Visit();
    v.setPatientId(p.getId());
    v.setVisitNo("V" + p.getId() + "-" + (j + 1));
    v.setDepartment(department);
    v.setDoctor(doctor);
    v.setVisitTime(windowStart.plusMinutes(offsets[j]));
    v.setStatus(0);
    visits.add(v);
}
```

新增辅助方法：

```java
/** 从「非主科室」里均匀随机取一个（seed 保证 ≥2 个科室）。 */
private String pickOtherDept(List<String> departments, String homeDept) {
    String other;
    do {
        other = departments.get(rnd.nextInt(departments.size()));
    } while (other.equals(homeDept));
    return other;
}
```

（`Doctor` 已由 `import com.his.common.entity.*` 覆盖，`DoctorMapper` 由 `import com.his.common.mapper.*` 覆盖，`Collectors` 已 import。）

- [ ] **Step 6: 跑测试确认通过**

Run: `cd backend && mvn -q -pl his-generator -am test -Dtest=DataGeneratorServiceTest`
Expected: 全部 PASS（旧用例 + 3 新用例）。

- [ ] **Step 7: Commit**

```bash
git add backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java backend/his-generator/src/main/java/com/his/generator/MedicalData.java backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java
git commit -m "feat: 生成器改主科室分布并保证 doctor 与科室一致

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: 前端 EntryForm 改造 + API 封装

**Files:**
- Modify: `frontend/src/api/business.js`
- Modify: `frontend/src/views/EntryForm.vue`

**Interfaces:**
- Consumes: `GET /api/doctors`、`GET /api/patients?department=`、`GET /api/visits?patientId=&department=`、现有 `POST` 创建接口。
- Produces: 带医生身份 + 科室范围 + 上下文提示的录入页。

- [ ] **Step 1: API 封装**

`frontend/src/api/business.js` 末尾追加：

```js
export const listDoctors = () => request.get('/doctors')
export const listVisits = (params) => request.get('/visits', { params })
```

- [ ] **Step 2: 重写 EntryForm.vue（整体替换）**

将 `frontend/src/views/EntryForm.vue` 整文件替换为下面内容（挂号登记 tab 原样保留，其余三个 tab 改为共享患者/就诊 + 只读医生 + 上下文条）：

```vue
<template>
  <el-card shadow="never" class="entry-card">
    <div class="page-head">
      <div>
        <h2 class="page-title">诊疗录入</h2>
        <p class="page-tip">挂号登记自由填写；诊断 / 处方 / 检验需先选医生，再选本科室的患者与就诊。</p>
      </div>
      <el-button v-if="lastPatientId" type="success" plain @click="goTimeline">
        查看时间轴（患者 {{ lastPatientId }}）
      </el-button>
    </div>

    <!-- 医生身份（轻量登录） -->
    <div class="doctor-bar">
      <span class="bar-label">当前医生</span>
      <el-select
        v-model="currentDoctor"
        filterable
        placeholder="请先选择医生（登录）"
        class="doctor-select"
        @change="onDoctorChange"
      >
        <el-option-group v-for="g in groupedDoctors" :key="g.department" :label="g.department">
          <el-option v-for="d in g.doctors" :key="d.id" :label="d.name" :value="d" />
        </el-option-group>
      </el-select>
      <span v-if="currentDoctor" class="doctor-dept">科室：{{ currentDoctor.department }}</span>
    </div>

    <!-- 当前接诊：患者 + 就诊 + 上下文 -->
    <div v-if="currentDoctor" class="reception-bar">
      <el-select
        v-model="currentPatient"
        filterable
        remote
        clearable
        :remote-method="searchPatients"
        :loading="patientLoading"
        value-key="id"
        placeholder="搜索本科室患者（姓名/身份证/电话）"
        class="reception-select"
        @change="onPatientChange"
      >
        <el-option v-for="p in patientOptions" :key="p.id" :label="patientLabel(p)" :value="p" />
      </el-select>

      <el-select
        v-model="currentVisit"
        clearable
        :disabled="!currentPatient"
        placeholder="选择就诊"
        class="reception-select"
      >
        <el-option v-for="v in visitOptions" :key="v.id" :label="visitLabel(v)" :value="v" />
      </el-select>

      <div v-if="currentPatient" class="ctx-line">
        患者 {{ currentPatient.name }}
        <span class="muted">（{{ genderText(currentPatient.gender) }}/{{ ageText(currentPatient.birthDate) }}）</span>
        <template v-if="currentVisit">
          ｜ 就诊 {{ currentVisit.visitNo }} · {{ currentVisit.department }} · {{ formatDateTime(currentVisit.visitTime) }}
        </template>
      </div>
    </div>

    <el-tabs v-model="activeTab">
      <!-- 1. 挂号登记（不变） -->
      <el-tab-pane label="挂号登记" name="visit">
        <el-form ref="visitFormRef" :model="visitForm" :rules="visitRules" label-width="96px" class="entry-form">
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item label="患者 ID" prop="patientId">
                <el-input-number v-model="visitForm.patientId" class="full" :min="1" :precision="0" :controls="false" placeholder="必填" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="就诊号" prop="visitNo">
                <el-input v-model="visitForm.visitNo" placeholder="如 GH20240101001" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="科室" prop="department">
                <el-input v-model="visitForm.department" placeholder="如 内科" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="医生" prop="doctor">
                <el-input v-model="visitForm.doctor" placeholder="如 张三" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="就诊时间" prop="visitTime">
                <el-date-picker v-model="visitForm.visitTime" class="full" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选择就诊时间" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="状态" prop="status">
                <el-select v-model="visitForm.status" class="full">
                  <el-option label="待就诊" :value="0" />
                  <el-option label="已完成" :value="1" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="24">
              <el-form-item label="备注" prop="remark">
                <el-input v-model="visitForm.remark" type="textarea" :rows="2" maxlength="200" show-word-limit placeholder="可选" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitVisit">提交挂号</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 2. 诊断录入 -->
      <el-tab-pane label="诊断录入" name="diagnosis">
        <el-alert v-if="!currentDoctor" class="tab-guard" type="info" show-icon :closable="false" title="请先选择医生（登录）" />
        <el-alert v-else-if="!currentPatient || !currentVisit" class="tab-guard" type="info" show-icon :closable="false" title="请先选择患者和就诊" />
        <el-form v-else ref="diagnosisFormRef" :model="diagnosisForm" :rules="diagnosisRules" label-width="96px" class="entry-form">
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item label="ICD 编码" prop="icdCode">
                <el-input v-model="diagnosisForm.icdCode" placeholder="如 J06.9" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="诊断名称" prop="diagnosisName">
                <el-input v-model="diagnosisForm.diagnosisName" placeholder="如 上呼吸道感染" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="诊断时间" prop="diagnosedAt">
                <el-date-picker v-model="diagnosisForm.diagnosedAt" class="full" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选择诊断时间" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitDiagnosis">提交诊断</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 3. 处方开具 -->
      <el-tab-pane label="处方开具" name="prescription">
        <el-alert v-if="!currentDoctor" class="tab-guard" type="info" show-icon :closable="false" title="请先选择医生（登录）" />
        <el-alert v-else-if="!currentPatient || !currentVisit" class="tab-guard" type="info" show-icon :closable="false" title="请先选择患者和就诊" />
        <el-form v-else ref="prescriptionFormRef" :model="prescriptionForm" :rules="prescriptionRules" label-width="96px" class="entry-form">
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item label="开方时间" prop="prescribedAt">
                <el-date-picker v-model="prescriptionForm.prescribedAt" class="full" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选择开方时间" />
              </el-form-item>
            </el-col>
          </el-row>

          <el-form-item label="药品明细" required>
            <el-table :data="prescriptionForm.items" border size="small" class="item-table">
              <el-table-column label="药名" min-width="200">
                <template #default="{ row }">
                  <el-select v-model="row.drugId" class="full" filterable placeholder="选择药品" @change="onDrugChange(row)">
                    <el-option v-for="drug in DRUGS" :key="drug.id" :label="`${drug.name}（${drug.unit} ¥${drug.price.toFixed(2)}）`" :value="drug.id" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="用量" width="120">
                <template #default="{ row }"><el-input v-model="row.dosage" placeholder="如 1粒" /></template>
              </el-table-column>
              <el-table-column label="频次" width="120">
                <template #default="{ row }"><el-input v-model="row.frequency" placeholder="如 每日3次" /></template>
              </el-table-column>
              <el-table-column label="天数" width="100">
                <template #default="{ row }"><el-input-number v-model="row.days" class="full" :min="1" :precision="0" :controls="false" /></template>
              </el-table-column>
              <el-table-column label="数量" width="100">
                <template #default="{ row }"><el-input-number v-model="row.quantity" class="full" :min="0.01" :precision="2" :controls="false" /></template>
              </el-table-column>
              <el-table-column label="单位" width="90">
                <template #default="{ row }"><el-input v-model="row.unit" /></template>
              </el-table-column>
              <el-table-column label="操作" width="80" align="center">
                <template #default="{ $index }"><el-button link type="danger" @click="removeItem($index)">删除</el-button></template>
              </el-table-column>
            </el-table>
            <el-button class="add-item" @click="addItem">+ 添加药品</el-button>
            <div class="item-tip">金额由后端按药品单价 × 数量计算，请务必选择药品。</div>
          </el-form-item>

          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitPrescription">提交处方</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 4. 检验录入 -->
      <el-tab-pane label="检验录入" name="lab">
        <el-alert v-if="!currentDoctor" class="tab-guard" type="info" show-icon :closable="false" title="请先选择医生（登录）" />
        <el-alert v-else-if="!currentPatient || !currentVisit" class="tab-guard" type="info" show-icon :closable="false" title="请先选择患者和就诊" />
        <el-form v-else ref="labFormRef" :model="labForm" :rules="labRules" label-width="96px" class="entry-form">
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item label="项目编码" prop="itemCode">
                <el-input v-model="labForm.itemCode" placeholder="如 WBC" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="项目名称" prop="itemName">
                <el-input v-model="labForm.itemName" placeholder="如 白细胞计数" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="结果值" prop="resultValue">
                <el-input v-model="labForm.resultValue" placeholder="如 12.5" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="单位" prop="unit">
                <el-input v-model="labForm.unit" placeholder="如 10^9/L" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="参考范围" prop="referenceRange">
                <el-input v-model="labForm.referenceRange" placeholder="如 4.0-10.0" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="检验时间" prop="testedAt">
                <el-date-picker v-model="labForm.testedAt" class="full" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选择检验时间" />
              </el-form-item>
            </el-col>
            <el-col :span="24">
              <el-form-item label="结果判定" prop="isAbnormal">
                <el-switch v-model="labForm.isAbnormal" :active-value="1" :inactive-value="0" active-text="异常" inactive-text="正常" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitLab">提交检验</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { createVisit, createDiagnosis, createPrescription, createLabTest, listDoctors, listVisits } from '../api/business'
import { listPatients } from '../api/patient'

const router = useRouter()

const activeTab = ref('visit')
const submitting = ref(false)
const lastPatientId = ref(null)

// 药品字典：后端未提供 /api/drugs 接口，硬编码 seed 的 12 条药品（id 即插入顺序）
const DRUGS = [
  { id: 1, name: '阿莫西林胶囊', unit: '盒', price: 18.5 },
  { id: 2, name: '感冒灵颗粒', unit: '盒', price: 12.0 },
  { id: 3, name: '布洛芬缓释胶囊', unit: '盒', price: 15.8 },
  { id: 4, name: '硝苯地平控释片', unit: '盒', price: 28.0 },
  { id: 5, name: '二甲双胍片', unit: '盒', price: 9.6 },
  { id: 6, name: '奥美拉唑肠溶胶囊', unit: '盒', price: 22.0 },
  { id: 7, name: '蒙脱石散', unit: '盒', price: 16.5 },
  { id: 8, name: '左氧氟沙星片', unit: '盒', price: 26.0 },
  { id: 9, name: '阿司匹林肠溶片', unit: '盒', price: 13.2 },
  { id: 10, name: '苯磺酸氨氯地平片', unit: '盒', price: 19.8 },
  { id: 11, name: '格列美脲片', unit: '盒', price: 24.5 },
  { id: 12, name: '复方甘草口服液', unit: '瓶', price: 8.9 }
]

const nowIso = () => {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

// ---- 医生身份（轻量登录） ----
const doctors = ref([])
const currentDoctor = ref(null)
const groupedDoctors = computed(() => {
  const map = new Map()
  for (const d of doctors.value) {
    if (!map.has(d.department)) map.set(d.department, [])
    map.get(d.department).push(d)
  }
  return [...map.entries()].map(([department, ds]) => ({ department, doctors: ds }))
})

// ---- 当前接诊 ----
const currentPatient = ref(null)
const currentVisit = ref(null)
const patientOptions = ref([])
const visitOptions = ref([])
const patientLoading = ref(false)

const genderText = (g) => (g === 0 ? '男' : g === 1 ? '女' : '-')
const ageText = (birthDate) => {
  if (!birthDate) return '-'
  const birth = new Date(String(birthDate).replace(/-/g, '/'))
  if (Number.isNaN(birth.getTime())) return '-'
  const today = new Date()
  let age = today.getFullYear() - birth.getFullYear()
  const monthDiff = today.getMonth() - birth.getMonth()
  if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birth.getDate())) age -= 1
  return age >= 0 ? `${age}岁` : '-'
}
const formatDateTime = (t) => (t ? String(t).replace('T', ' ').slice(0, 19) : '')

const patientLabel = (p) => `${p.name}｜${genderText(p.gender)} ${ageText(p.birthDate)}｜${p.idCard || '-'}`
const visitLabel = (v) => `${v.visitNo} · ${v.department} · ${formatDateTime(v.visitTime)}`

const searchPatients = async (keyword) => {
  if (!currentDoctor.value) return
  patientLoading.value = true
  try {
    const res = await listPatients({ department: currentDoctor.value.department, keyword: keyword || '', size: 20 })
    patientOptions.value = res.data.records || []
  } finally {
    patientLoading.value = false
  }
}

const onDoctorChange = () => {
  currentPatient.value = null
  currentVisit.value = null
  patientOptions.value = []
  visitOptions.value = []
}

const onPatientChange = async () => {
  currentVisit.value = null
  visitOptions.value = []
  if (!currentPatient.value) return
  const res = await listVisits({ patientId: currentPatient.value.id, department: currentDoctor.value.department })
  visitOptions.value = res.data || []
}

// ---- 表单 ----
const newVisitForm = () => ({
  patientId: null, visitNo: '', department: '', doctor: '', visitTime: nowIso(), status: 0, remark: ''
})
const newDiagnosisForm = () => ({ icdCode: '', diagnosisName: '', diagnosedAt: nowIso() })
const newItem = () => ({ drugId: null, drugName: '', dosage: '', frequency: '', days: 3, quantity: 1, unit: '' })
const newPrescriptionForm = () => ({ prescribedAt: nowIso(), items: [newItem()] })
const newLabForm = () => ({
  itemCode: '', itemName: '', resultValue: '', unit: '', referenceRange: '', isAbnormal: 0, testedAt: nowIso()
})

const visitForm = ref(newVisitForm())
const diagnosisForm = ref(newDiagnosisForm())
const prescriptionForm = ref(newPrescriptionForm())
const labForm = ref(newLabForm())

const visitFormRef = ref()
const diagnosisFormRef = ref()
const prescriptionFormRef = ref()
const labFormRef = ref()

const required = (message) => ({ required: true, message, trigger: ['blur', 'change'] })

const visitRules = {
  patientId: [required('请输入患者 ID')],
  visitNo: [required('请输入就诊号')],
  visitTime: [required('请选择就诊时间')]
}
const diagnosisRules = {
  diagnosisName: [required('请输入诊断名称')],
  diagnosedAt: [required('请选择诊断时间')]
}
const prescriptionRules = {
  prescribedAt: [required('请选择开方时间')]
}
const labRules = {
  itemName: [required('请输入检验项目名称')],
  testedAt: [required('请选择检验时间')]
}

const validateForm = async (formRef) => {
  if (!formRef.value) return false
  try {
    await formRef.value.validate()
    return true
  } catch {
    return false
  }
}

const submit = async (fn, successMessage, patientId) => {
  submitting.value = true
  try {
    await fn()
  } catch {
    return false
  } finally {
    submitting.value = false
  }
  ElMessage.success(successMessage)
  lastPatientId.value = patientId
  return true
}

const submitVisit = async () => {
  if (!(await validateForm(visitFormRef))) return
  const payload = { ...visitForm.value }
  if (await submit(() => createVisit(payload), '挂号登记成功', payload.patientId)) {
    visitForm.value = newVisitForm()
  }
}

const submitDiagnosis = async () => {
  if (!(await validateForm(diagnosisFormRef))) return
  const payload = {
    patientId: currentPatient.value.id,
    visitId: currentVisit.value.id,
    doctor: currentDoctor.value.name,
    ...diagnosisForm.value
  }
  if (await submit(() => createDiagnosis(payload), '诊断录入成功', payload.patientId)) {
    diagnosisForm.value = newDiagnosisForm()
  }
}

const addItem = () => prescriptionForm.value.items.push(newItem())
const removeItem = (index) => prescriptionForm.value.items.splice(index, 1)

const onDrugChange = (row) => {
  const drug = DRUGS.find((d) => d.id === row.drugId)
  if (!drug) return
  row.drugName = drug.name
  row.unit = drug.unit
}

const submitPrescription = async () => {
  if (!(await validateForm(prescriptionFormRef))) return
  const rows = prescriptionForm.value.items
  if (!rows.length) { ElMessage.warning('请至少添加一条药品明细'); return }
  if (rows.some((i) => !i.drugId)) { ElMessage.warning('每条明细都必须选择药品'); return }
  const payload = {
    patientId: currentPatient.value.id,
    visitId: currentVisit.value.id,
    doctor: currentDoctor.value.name,
    prescribedAt: prescriptionForm.value.prescribedAt,
    items: rows.map((i) => ({
      drugId: i.drugId,
      drugName: i.drugName,
      dosage: i.dosage,
      frequency: i.frequency,
      days: i.days,
      quantity: Number(i.quantity),
      unit: i.unit
    }))
  }
  if (await submit(() => createPrescription(payload), '处方开具成功', payload.patientId)) {
    prescriptionForm.value = newPrescriptionForm()
  }
}

const submitLab = async () => {
  if (!(await validateForm(labFormRef))) return
  const payload = { patientId: currentPatient.value.id, visitId: currentVisit.value.id, ...labForm.value }
  if (await submit(() => createLabTest(payload), '检验录入成功', payload.patientId)) {
    labForm.value = newLabForm()
  }
}

const goTimeline = () => router.push('/patient/' + lastPatientId.value)

onMounted(async () => {
  const res = await listDoctors()
  doctors.value = res.data || []
})
</script>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.page-title { margin: 0; font-size: 18px; font-weight: 600; color: #303133; }
.page-tip { margin: 4px 0 0; font-size: 13px; color: #909399; }
.entry-form { max-width: 900px; }
.full { width: 100%; }
.item-table { width: 100%; }
.add-item { margin-top: 8px; }
.item-tip { margin-top: 4px; font-size: 12px; color: #909399; }

.doctor-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  margin-bottom: 12px;
  background: #f5f7fa;
  border-radius: 6px;
}
.bar-label { font-size: 14px; font-weight: 600; color: #303133; }
.doctor-select { width: 240px; }
.doctor-dept { font-size: 13px; color: #606266; }

.reception-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 12px;
}
.reception-select { width: 320px; }
.ctx-line { font-size: 13px; color: #303133; }
.muted { color: #909399; }
.tab-guard { margin-bottom: 12px; }
</style>
```

- [ ] **Step 3: 前端构建验证**

Run: `cd frontend && npm run build`
Expected: build 成功，无错误。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/business.js frontend/src/views/EntryForm.vue
git commit -m "feat: 录入页加医生身份与科室范围选择，改造诊断/处方/检验录入

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## 部署与数据重跑（所有任务合入后，人工执行）

1. 对运行库应用新表与种子（若库已存在）：
   ```bash
   mysql -uhis_user -phis@2026 --default-character-set=utf8mb4 his_demo -e "ALTER TABLE visit ADD KEY idx_visit_department (department); CREATE TABLE IF NOT EXISTS doctor (id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(50) NOT NULL, department VARCHAR(50) NOT NULL, KEY idx_doctor_department (department)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='医生表';"
   ```
   然后导入 24 名医生（或直接重跑 `backend/sql/seed.sql`）。
2. 重新生成数据（幂等，`clear-before-run: true` 会清空业务表但保留 doctor/drug/rule_config）：
   ```bash
   cd backend && mvn -q -pl his-generator -am spring-boot:run
   ```
3. 启动后端 + 前端，手动验证：登录医生 → 看到本科室患者 → 选患者/就诊 → 提交诊断/处方/检验，时间轴同步更新。
