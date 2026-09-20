# 录入工作流设计：医生身份 + 科室范围 + 上下文提示

> 本 spec 是录入功能改造的**绑定权威**。计划（plan）从这里论证，冲突以本 spec 为准。

## 目标

把「诊断录入 / 处方开具 / 检验录入」从**自由文本手填**改造为**带医生身份、按科室范围、有上下文提示**的工作流：

- 医生先「登录」（轻量身份选择，无密码）；
- 医生只看到**自己科室**的患者；
- 录入时处处有提示：当前医生是谁、当前患者是谁、当前就诊是哪次；
- 诊断/处方自动带上登录医生；检验同流程但**不存医生字段**。
- 「挂号登记」保持不变。

## 决策（用户已确认，不可再改）

1. **轻量身份选择**（无账号密码），不引入 user 表 / 鉴权。
2. **新建 `doctor` 表**，每科室 3–5 名医生（共 24 名），**名单不含作者「王冠杰」**。
3. **「本科室的患者」= 在该科室有过至少一次就诊（`visit.department = 本科室`）的患者**；选中患者后，就诊下拉**只显示该患者在本科室的就诊**。
4. **生成器改「主科室」分布**（80% 主科室 / 20% 其他科室）并**重新生成数据**，否则科室过滤形同虚设。
5. **检验（`lab_test`）不加医生字段**，只在顶部上下文展示当前医生。

## 现状（改造前）

- 无 `doctor` / `department` / `user` 表；医生、科室只是 `visit`/`diagnosis`/`prescription` 上的字符串字段。
- 生成器 `DataGeneratorService.newVisits()` 里 `department` 与 `doctor` **随机且独立**地取（`MedicalData.DEPARTMENTS` / `MedicalData.DOCTORS` 各随机一个），同一患者 5–20 次就诊随机撒到 6 个科室 → 几乎每个患者「属于」每个科室。
- 后端只有 `POST /api/visits|diagnoses|prescriptions|lab-tests`，没有「查就诊列表」「按科室查患者」。
- 前端 `EntryForm.vue` 四个 tab，`patientId`/`visitId`/`doctor` 全是手填数字/文本，无登录、无范围、无提示。

---

## 一、数据模型

### 1.1 新增 `doctor` 表（schema.sql 追加）

```sql
CREATE TABLE doctor (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(50) NOT NULL,
  department VARCHAR(50) NOT NULL,
  KEY idx_doctor_department (department)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='医生表';
```

### 1.2 医生种子（seed.sql 追加，24 条）

| 科室 | 医生 |
|---|---|
| 内科 | 李建华、张国庆、刘志强、陈秀英、赵雪梅 |
| 外科 | 杨文博、黄建军、周国平、吴丽华、徐永强 |
| 儿科 | 孙晓丽、胡雪丹、朱慧敏、高建明 |
| 妇科 | 林丽霞、何美玲、郭秀兰、马静怡 |
| 五官科 | 罗志远、梁晓东、宋慧娟 |
| 皮肤科 | 唐建华、冯雪琴、邓丽娟 |

约束：**任何模拟数据（医生/患者）都不得使用「王冠杰」这个名字**（含后续任何新增假人名）。

### 1.3 `visit` 表加索引

在 `schema.sql` 的 `CREATE TABLE visit` 语句内追加：

```sql
KEY idx_visit_department (department)
```

（与现有 `KEY idx_visit_patient_time` 并列。作用：科室过滤查询提速。）
**实施注意**：`schema.sql` 只对全新库生效；对已存在的运行库，需另执行一次 `ALTER TABLE visit ADD KEY idx_visit_department (department);`（或整体重跑 schema+seed）。

---

## 二、生成器改造（`his-generator`）

改 `DataGeneratorService`：

1. 新增依赖 `DoctorMapper`（`@RequiredArgsConstructor` 构造器随之变化——**须同步更新 `DataGeneratorServiceTest.buildService()`**）。
2. 启动时 `doctorMapper.selectList(null)` 加载医生，按 `department` 分组为 `Map<String, List<Doctor>> doctorsByDept`。
   - 若为空，抛 `IllegalStateException`，信息含「doctor」，提示先执行 `backend/sql/seed.sql`（与现有 rule_config 快速失败一致）。
3. `newVisits(Patient p)`：
   - 取「有医生的科室集合」作为合法科室集（= 6 个科室）。
   - 每名患者随机一个 `homeDept`（主科室）。
   - 每次就诊：**80% 概率 `department = homeDept`；20% 概率从「其余科室」（≠ homeDept）均匀随机**。
   - `doctor` = 从 `doctorsByDept.get(department)` 里随机取一个，`visit.doctor = doctor.name`。
   - 由此 **doctor ↔ department 严格一致**（修复现有数据里医生与科室对不上的问题）。
4. `clearDataTables()` 的 `DATA_TABLES` **不含 `doctor`**（doctor 与 drug/rule_config 一样是字典，不随重跑清空）。
5. 删除 `MedicalData.DEPARTMENTS` / `MedicalData.DOCTORS` 两个已无引用的数组（`SURNAMES`/`GIVEN`/`LAB_ITEMS` 保留）。

**重新生成**：实施完成后须重跑生成器（`patient-count: 10000`，`clear-before-run: true` 幂等），让主科室分布与 doctor↔科室一致性生效。

---

## 三、后端接口

### 3.1 新增实体 / Mapper / Service / Controller

- `his-common/entity/Doctor.java`：`@TableName("doctor")`，字段 `id(Long, @TableId AUTO)`、`name(String)`、`department(String)`。
- `his-common/mapper/DoctorMapper.java`：`BaseMapper<Doctor>` + `@Mapper`。
- `his-server/service/DoctorService.java` + `impl/DoctorServiceImpl.java`（`IService<Doctor>` / `ServiceImpl`）。

### 3.2 `GET /api/doctors`

返回 `Result<List<Doctor>>`，按 `department` 升序、`id` 升序。前端登录下拉与分组的数据源。

### 3.3 `GET /api/patients` 扩展 `department` 参数

在 `PatientController.list` 增加可选 `@RequestParam(required=false) String department`：

- 未传 `department` → 走现有逻辑（keyword 模糊 + 分页）。
- 传了 `department` → 走新方法 `PatientService.pageByDepartment(page, size, department, keyword)`，用一条**参数化**自定义 SQL（防注入）：

```sql
SELECT * FROM patient WHERE id IN
  (SELECT DISTINCT patient_id FROM visit WHERE department = #{department})
  [AND (name LIKE CONCAT('%', #{keyword}, '%')
    OR id_card LIKE CONCAT('%', #{keyword}, '%')
    OR phone LIKE CONCAT('%', #{keyword}, '%'))]
ORDER BY id DESC
```

实现：`PatientMapper` 加 `@Select`（`<script>` 动态 SQL）+ `IPage<Patient>` 分页；列名映射依赖已有的 `map-underscore-to-camel-case`（id_card→idCard）。`PatientService` 接口与 `PatientServiceImpl` 增加对应方法。

### 3.4 `GET /api/visits`（新增查询）

`VisitController` 新增：

```
GET /api/visits?patientId=<Long>&department=<String?可选>
```

`LambdaQueryWrapper`：`eq(patientId)`；`department` 非空时 `eq(department)`；`orderByDesc(visitTime)`。返回 `Result<List<Visit>>`。用于就诊联动下拉（只显示该患者在本科室的就诊）。

### 3.5 创建接口不变

`POST /api/visits|diagnoses|prescriptions|lab-tests` 四个接口**不改**（含各 `ServiceImpl.save` 里的 `CacheInvalidator.evictTimeline` 缓存失效逻辑不变）。

---

## 四、前端（`EntryForm.vue` 为主）

### 4.1 API 封装

- `api/business.js` 新增 `listDoctors = () => request.get('/doctors')`、`listVisits = (params) => request.get('/visits', { params })`。
- `api/patient.js` 的 `listPatients(params)` 已通用，直接传 `{ department, keyword, size }` 即可，无需改。

### 4.2 页面结构

```
[页头] 诊疗录入 ｜ （查看时间轴 按钮，沿用 lastPatientId）
[当前医生条] el-select（按科室分组 el-option-group，数据来自 /doctors）
             未选择时提示「请先选择医生（登录）」
[当前接诊块]（仅当已选医生且在诊断/处方/检验 tab 时展示）
             患者：[远程搜索下拉 → /patients?department=当前科室&keyword=…]
             就诊：[联动下拉 → /visits?patientId=…&department=当前科室]
             上下文条：当前医生 李建华(内科)｜患者 张三(男/34岁)｜就诊 V1-1·内科·2026-09-20
[el-tabs]
    挂号登记  —— 保持不变（自由填写）
    诊断录入  —— 需医生+患者+就诊；表单仅剩 ICD编码/诊断名称/诊断时间；doctor 只读=当前医生
    处方开具  —— 需医生+患者+就诊；表单开方时间+药品明细；doctor 只读=当前医生
    检验录入  —— 需医生+患者+就诊；表单项目编码/名称/结果/单位/参考范围/判定/时间；无 doctor 字段
```

### 4.3 交互规则

- 三个录入 tab 在**未选医生**时：表单区替换为 `el-alert`「请先选择医生（登录）」。
- 选了医生但**未选患者/就诊**时：提交按钮禁用，并提示先选患者/就诊。
- 诊断/处方提交体里的 `patientId`/`visitId`/`doctor` 取自共享选择（`doctor = currentDoctor.name`）；检验提交体只有 `patientId`/`visitId` + 检验字段。
- 患者下拉为 `filterable remote`（远程搜索，`size=20`）；就诊下拉在患者选中后才加载。
- 药品字典 `DRUGS` 硬编码数组**保持不变**（后端暂无 /api/drugs，不在本次范围）。

---

## 五、测试

沿用现有基建：controller 用 `@WebMvcTest`+`@MockBean`，service/生成器用 Mockito。

- `DoctorControllerTest`：`GET /api/doctors` 返回列表（mock `DoctorService.list`）。
- `PatientControllerTest` 增补：传 `department` 时调用 `pageByDepartment`（mock 校验参数透传）。
- `VisitControllerTest`：`GET /api/visits` 带 `patientId`/`department` 时返回列表（mock `VisitService.list`）。
- `DataGeneratorServiceTest` 增补（并同步更新 `buildService()` 以 mock `DoctorMapper`）：
  - 每个 `visit.doctor` 都存在于其 `visit.department` 对应的医生集合里（doctor↔科室一致）；
  - 每名患者的主科室占比约 80%（统计 ≥ 某阈值，防随机误报）；
  - doctor 表为空时 `generate` 快速失败（信息含「doctor」）。
- 前端：`npm run build` 通过（项目无前端测试框架）。

---

## 六、非目标（本次不做）

- 不引入账号密码 / session / JWT 鉴权。
- 不给「挂号登记」加登录或科室限制（用户明示保持不变）。
- 不给 `lab_test` 加医生字段。
- 不给诊断加 `rule_config` 下拉建议、不给药品字典加 `/api/drugs` 接口（可作为后续增强）。
- 不改患者列表页 `PatientList.vue`、时间轴 `Timeline.vue`、看板 `Dashboard.vue`。
