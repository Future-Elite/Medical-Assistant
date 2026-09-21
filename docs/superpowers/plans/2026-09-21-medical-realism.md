# 医学真实感改造（三期）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把生成器从「各维度独立均匀抽取」改造为「医学本体约束下的逐层条件抽样 + 患者级疾病轨迹」，删除全部故意违规注入，并新增本体一致性校验器；生成数据在医学规则上恒为 100% 合规，且校验器对每一类规则都有可证的正例检出能力。

**Architecture:** 新增 6 张字典表承载医学知识本体（科室↔诊断↔药品↔检验↔手术），生成器启动时全量载入内存、生成过程零 DB 往返；`DataGeneratorService` 改为按患者疾病档案驱动的事件日历逐层抽样；校验层拆为互不重叠的两部分——`RationalDrugValidator`（用药安全，换用新本体作数据源）与新增的 `OntologyConsistencyValidator`（8 类本体一致性）；前端看板汇总为「医学合理性」指标卡。

**Tech Stack:** Spring Boot 3.3.5 · Java 21 · MyBatis-Plus 3.5.7 · MySQL 8 · Redis 8 · Vue 3 + Vite + Element Plus + Pinia + ECharts。

**Spec:** [docs/superpowers/specs/2026-09-21-medical-realism-design.md](../specs/2026-09-21-medical-realism-design.md)

**参考文献说明:** [docs/references/README.md](../../references/README.md)（11 篇已下载全文；3 篇付费墙文献**不得据以陈述机制**）

## Global Constraints

以下为 spec §13 的逐字约束。**每个任务的要求都隐含包含本节。**

- 不引入账号密码 / session / JWT
- 任何模拟数据（医生/患者/药师/护士）**不得使用「王冠杰」**
- 统一信封 `Result{code,message,data}`，`code=0` 成功
- 时间线事件严格按 `time` 升序；所有时间字段 `DATETIME`（实体 `LocalDateTime`）
- Redis 纯优化，失败降级查库，绝不让 GET 变 500
- controller 用 `@WebMvcTest` + `@MockBean`；service/生成器用 Mockito
- **数据字典表（`drug`/`rule_config`/`doctor`/`rational_rule`/`clinical_pathway`）在生成器清空数据时不得触碰**；
  本次新增的 6 张字典表（`dept_diagnosis`/`diagnosis_drug`/`diagnosis_lab`/`drug_dose`/`visit_rate`/`diagnosis_operation`）
  同属字典表，同样不得清空。**`operation` 是数据表**，与 `admission` 等既有 11 张表一并在生成前清空
- **手术必须挂在该次住院下**：不得存在 `admission_id` 为空的手术记录；
  手术的 `[start_time, end_time]` 必须落在所属住院区间内
- **内科医生不得主刀**：`operation.surgeon` 必须取自该住院科室的 `doctor`
- **`admission` 增列后旧列 `diagnosis` 必须继续写入**（等于 `admission_diagnosis`），
  以兼容既有前端与查询；新逻辑以新列为准
- **生成数据不得包含任何故意违规**：不引入注入机制，不放宽本体约束来「制造」待检出的异常
- 校验器对**无法判定**的数据必须显式报出（`unchecked`），不得静默跳过伪装成通过
- 报告数值必须实测，不得编造；未测就标「待实测」并给命令
  - **执行环境实测（2026-09-21，Task 3/4 执行时发现）**：本机**没有**可供非交互调用的 MySQL
    凭据——`~/.my.cnf`、`/etc/my.cnf`、`.mylogin.cnf`、`MYSQL_PWD` 全都不存在，无凭据调用
    会退化成默认用户 `ODBC` 并报 `ERROR 1045`；应用凭据虽在 `application.yml` 里（`his_user`），
    但把它放进命令行会被安全策略判为凭据外泄而拦截。
    故 **Tasks 3–8 的「入库并断言」步骤在执行者这一侧无法完成**。
  - **处理办法（已裁定）**：执行者只做**离线静态校验**——解析自己生成的 `.sql`，与所在任务
    `Expected` 的每个值逐条比对——并在报告里**显式写明这些数字不是查库结果**（Task 3 与 Task 4
    的报告都如此标注，这是正确做法）。入库与断言合并成**一次阶段一收尾部署批**，
    由持有凭据的作者按各任务 Step 1~4 的原样命令一次跑完。
  - **不得把这批离线数字写成实测数字**，也不得因为「反正作者会跑」就省略离线校验。
    真正的数据库实测从阶段二的 Task 18 起（那些任务的全部结论都依赖查库，届时凭据必须可用）。
- 每个 commit 结尾附 `Co-Authored-By: Claude Code <noreply@anthropic.com>`
- 全程中文交流；代码标识符英文，注释跟随现有中文风格

**构建注意（沿用二期结论）**：`mvn -pl his-server -am test -Dtest=X` 必须追加
`-Dsurefire.failIfNoSpecifiedTests=false`，否则 his-common 无匹配测试会中止构建。

**工作区**：实现开始前须由 `superpowers:using-git-worktrees` 创建隔离工作区。
当前分支为 `main`，**未经作者明示同意不得直接在 main 上实现**。

---

## File Structure（新增/改动总览）

**SQL**
- 修改：`backend/sql/schema.sql` —— 新增 7 张表（6 张字典 + `operation`）+ `admission` 增 4 列
- 新增：`backend/sql/seed-ontology.sql` —— 本体种子（诊断/药品/检验/剂量/手术/就诊率），**单列一个文件，体量大**
- 修改：`backend/sql/verify-final.sql` —— 新增本体一致性与手术域断言
- 修改：`backend/sql/seed-extension.sql` —— 收紧硝苯地平日上限（spec §4.4）

**his-common**
- 新增实体：`entity/DeptDiagnosis.java`、`DiagnosisDrug.java`、`DiagnosisLab.java`、`DrugDose.java`、`VisitRate.java`、`DiagnosisOperation.java`、`Operation.java`
- 修改实体：`entity/Admission.java`（增 `admissionRoute`/`dischargeDisposition`/`admissionDiagnosis`/`dischargeDiagnosis`）
- 新增 Mapper：`mapper/DeptDiagnosisMapper.java` 等 7 个
- 新增 DTO：`dto/OntologyReport.java`、`OntologyViolation.java`；修改 `dto/RationalDrugReport.java`（增 `unchecked`）
- 新增：`ontology/OntologyRules.java` —— 科室优先级匹配与年龄性别判定（Task 10 建，**校验器与生成器共用**；
  放在 his-common 是因为 `his-generator → his-common`、`his-server → his-common` 而两者互不依赖，见 Task 10 的说明）

**his-generator**
- 新增：`OntologyRepository.java` —— 本体表的全量内存加载与索引查询（生成器侧）
- 新增：`PatientProfile.java`、`EventCalendar.java` —— 患者疾病档案与事件日历
- 修改：`MedicalData.java` —— `LAB_ITEMS` 由生成器全局池改为本体表来源；`DiagnosisRule` 相关代码收敛
- 修改：`DataGeneratorService.java` —— 删除违规注入、改为逐层条件抽样、接事件日历、接手术域

**his-server**
- 修改：`service/validation/RationalDrugValidator.java` —— 换 `diagnosis_drug`/`drug_dose` 数据源、修 DOSE 解析、增 `unchecked`
- 新增：`service/validation/OntologyConsistencyValidator.java` —— 8 类本体检查
- 修改：`service/validation/ValidationService(+Impl).java` —— 汇总三类报告（合理用药 + 临床路径 + 本体一致性）
- 修改：`service/impl/TimelineServiceImpl.java` —— 住院时间轴新增 `OPERATION` 事件
- 修改：`service/impl/CacheInvalidator.java`（或既有缓存失效类）—— `operation` 写入时 evict 住院时间轴
- 修改：`service/impl/DashboardServiceImpl.java` —— 「医学合理性」指标卡数据

**frontend**
- 修改：`components/Timeline.vue`（`TYPE_META` 加 `OPERATION`）、`views/AdmissionDetail.vue`（手术信息区）
- 修改：`views/Dashboard.vue`（医学合理性指标卡）
- 修改：`views/ValidationReport.vue`（本体一致率 + 八类明细 + 未判定数）

**docs**
- 修改：`docs/test-report.md` —— 新增 §7 三期医学真实感测试章节（数值必须实测）

---

## 任务分组与顺序

四个阶段**共用本计划**，任务按依赖顺序排列。阶段的归属写在每个任务的标题里。

| 阶段 | 任务 | 交付判据 |
|---|---|---|
| 一 | Task 1–12 | 本体入库且自洽；两个校验器在**正例数据**上逐类检出（§11「校验器（正例，必须有）」）；`unchecked` 显式报出而非静默通过 |
| 二 | Task 13–18 | §8.3 三项判据在**万级生成数据**上为 0，「妇科看肠胃炎」不再出现；同患者重复就诊率 > 0；慢病随访间隔中位数落在设计值附近（实测） |
| 三 | Task 19–22 | 看板显示本体一致率与覆盖率；基线反测数据实测落表 |
| 四 | Task 23–26 | 手术全部落在住院区间内、术者属于该住院科室、术后天数达标 |

> **阶段一的判据为什么不写「§8.3 三项为 0」**：spec §10 把「生成器接本体」列在阶段一，
> 本计划把它移到了阶段二（Task 16/17）——因为条件抽样的第一层是「这个患者能得哪些病」，
> 而那一层就是事件日历本身。把接线留在阶段一，就得先写一个「还没有患者档案版」的
> 条件抽样、阶段二再推翻重写一次。**代价是**：阶段一结束时生成器仍在造「妇科看肠胃炎」，
> §8.3 的三项判据**在生成数据上必然不为 0**，所以那三条判据的验收被推迟到阶段二出口（Task 18）。
> 阶段一能验收的是「校验器本身是对的」——用 §11 要求的正例数据逐类检出，
> 这正是「删掉注入后校验器仍然有效」的代码级证据。

阶段一内部再分两段：**Task 1–9 是「本体入库」**（建表 → 实体 → 七份种子 → 一致性测试与作者审校），
**Task 10–12 是「校验器与汇总」**。任务 3~9 写的都是同一条通路上的字典数据，
必须全部完成才有可校验的数据；但它们各自的**审校面不同**（药品 / 剂量 / 诊断 / 检验 / 手术 / 用药映射 / 机器校验），
故分七个任务、七次提交——一个任务的产出被否掉，不会连累其余六份已通过的内容。

阶段二内部同理再分两段：**Task 13–15 是「三层数据源」**（本体快照 → 患者档案 → 事件日历，
三个都是纯函数或纯数据类，各自带单元测试，互不依赖），
**Task 16–18 是「接线与实测」**（门诊链 → 住院链 → 出口实测）。
门诊与住院**可以分别验收**：Task 16 做完时住院部分仍走旧的随机诊断，
编译通过、测试全绿，可以被独立否掉而不影响门诊 —— 所以它们分两个任务。

Task 16 内部也先落地两个纯工具类（`Weighted` 加权抽样、`LabResults` 化验取值），
各自带单元测试，再接进 `DataGeneratorService`：这两处的错误都是**静默的**
（抽样下标偏一位只是分布略有偏差、参考范围解析错只是值略不合理，都不会报错），
所以先让它们各自可测，否则出口数据一不对，排查要在
「三层数据源 + 加权抽样 + 值生成 + 接线」四处之间来回猜——而这四处分属三个提交。

**裁剪顺序**：阶段三、四均为增量，周期紧张时**先裁四、后裁三**（时间分布是课题「时序数据」的主线）。

---

## Task 1: 数据库 —— 7 张新表 + `admission` 增 4 列（阶段一）

**Files:**
- Modify: `backend/sql/schema.sql`
- Create: `backend/sql/migrate-phase3.sql`
- Test: 无单元测试；本任务的验证是 `information_schema` 结构断言（Step 5）

**Interfaces:**
- Consumes: 无
- Produces: 7 张表（`dept_diagnosis` / `diagnosis_drug` / `diagnosis_lab` / `drug_dose` / `visit_rate` / `diagnosis_operation` / `operation`）
  与 `admission` 的 4 个新列，供 Task 2 实体映射、Task 3–9 种子装载、Task 10/11 校验器读取、Task 21–24 生成器写入。

**两处对 spec §4 的字宽偏离（有意，已核对既有表定义）：**
- `dept_diagnosis.department` 用 `VARCHAR(50)`（spec 写 32）——对齐既有的 `visit.department` / `admission.department`，避免跨表比对时出现隐式截断；
- `operation.surgeon` / `assistant` / `anesthesiologist` 用 `VARCHAR(50)`（spec 写 32）——对齐既有的 `visit.doctor` / `admission.doctor`。
其余字段类型与 spec 逐字一致。

**一处对 spec §4 的结构性修正（必须做，否则 spec 内部自相矛盾）：**

spec §4.2/§4.3/§4.7 把 `diagnosis_drug` / `diagnosis_lab` / `diagnosis_operation` 都建成
**只按 `icd_code` 建键**。但 spec §9.2 同时要求「同 ICD 可出现在多个科室」
（`J06.9` 上呼吸道感染在 内科 与 儿科 都有；`K40.9` 腹股沟疝在 外科 与 儿科 都有），
并且明确要求「儿科（腹股沟疝）**手术概率远低于成人**」。**这两条无法同时成立**：
单一 `icd_code` 键下，儿科与成人只能共用同一行 `probability`。

因此本计划给这三张表各加一列 **`department VARCHAR(50) NULL`**，语义为
**`NULL` = 该诊断不限科室；非空 = 仅该科室的该诊断适用**。这是必要的，不是可选的：
没有它，以下三条**全部无法表达**——
1. 儿科腹股沟疝手术概率远低于成人（spec §9.2 明文要求）；
2. 儿童**禁用喹诺酮类**（左氧氟沙星 id 8 不得出现在儿科用药行上）——这是硬性用药禁忌；
3. 同一诊断在儿科与成人的检验异常率不同。

`dept_diagnosis` 本就以 `(department, icd_code)` 为自然键，故本修正让四张本体表的键**一致化**：
匹配规则统一为「**先取 `department` 精确匹配的行；无匹配时回落到 `department IS NULL` 的行**」。
Task 10/11 的校验器与 Task 13 的 `OntologyRepository` 都按这条规则实现
（校验器侧由 Task 10 的 `OntologyRules` 承载，生成器侧不另写一份，直接复用同一个类）。
`drug_dose` 是**逐药**的剂量规则，与科室无关，不加此列。

- [ ] **Step 1: 在 `schema.sql` 末尾追加 6 张字典表**

```sql
-- ===== 三期：医学知识本体（字典表，生成器清空数据时不得触碰）=====

CREATE TABLE dept_diagnosis (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  department VARCHAR(50) NOT NULL COMMENT '科室名，取值域与 doctor.department / visit.department 一致',
  icd_code VARCHAR(16) NOT NULL COMMENT 'ICD-10 国家临床版编码',
  diagnosis_name VARCHAR(64) NOT NULL,
  gender TINYINT NOT NULL DEFAULT 0 COMMENT '0不限1仅男2仅女',
  min_age INT NOT NULL DEFAULT 0 COMMENT '适用年龄下限（含）',
  max_age INT NOT NULL DEFAULT 89 COMMENT '适用年龄上限（含）',
  chronic TINYINT NOT NULL DEFAULT 0 COMMENT '1=慢病，可被患者终身携带',
  admission_eligible TINYINT NOT NULL DEFAULT 0 COMMENT '1=可收住院',
  weight DECIMAL(6,3) NOT NULL DEFAULT 1.000 COMMENT '同科室同年龄段内的相对常见度，>0',
  month_weights JSON NULL COMMENT '长度12的正数数组，季节乘子；NULL表示无季节性（不是全1）',
  prevalence DECIMAL(6,4) NOT NULL DEFAULT 0 COMMENT '慢病的年龄性别基线患病率；非慢病置0',
  KEY idx_dd_dept (department),
  KEY idx_dd_icd (icd_code),
  KEY idx_dd_chronic (chronic)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='科室↔诊断本体表';

CREATE TABLE diagnosis_drug (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  icd_code VARCHAR(16) NOT NULL,
  department VARCHAR(50) NULL COMMENT 'NULL=全部科室适用；非空=仅该科室的该诊断适用（儿童与成人用药差异靠此表达）',
  drug_id BIGINT NOT NULL COMMENT '外键 → drug.id',
  line TINYINT NOT NULL DEFAULT 1 COMMENT '1一线2二线3辅助用药',
  weight DECIMAL(6,3) NOT NULL DEFAULT 1.000 COMMENT '同一线内相对权重',
  postop TINYINT NOT NULL DEFAULT 0 COMMENT '1=可作术后用药（镇痛/抗感染）',
  KEY idx_ddg_icd (icd_code),
  KEY idx_ddg_dept (department),
  KEY idx_ddg_drug (drug_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='诊断→药品本体表';

CREATE TABLE diagnosis_lab (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  icd_code VARCHAR(16) NOT NULL,
  department VARCHAR(50) NULL COMMENT 'NULL=全部科室适用；非空=仅该科室的该诊断适用',
  item_code VARCHAR(32) NOT NULL COMMENT '检验项目编码，LOINC 中文版风格',
  item_name VARCHAR(64) NOT NULL,
  unit VARCHAR(16),
  ref_range VARCHAR(32) COMMENT '参考范围文本，如 4.0-10.0',
  abnormal_direction VARCHAR(8) NOT NULL DEFAULT 'NONE' COMMENT 'HIGH/LOW/NONE',
  abnormal_prob DECIMAL(4,3) NOT NULL DEFAULT 0 COMMENT '该病该项目的异常概率',
  weight DECIMAL(6,3) NOT NULL DEFAULT 1.000 COMMENT '该项目被开立的相对权重',
  required TINYINT NOT NULL DEFAULT 0 COMMENT '1=该诊断必查，不参与随机抽取',
  KEY idx_ddl_icd (icd_code),
  KEY idx_ddl_dept (department)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='诊断→检验本体表（本表自身即检验项目池）';

CREATE TABLE drug_dose (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  drug_id BIGINT NOT NULL COMMENT '外键 → drug.id',
  single_dose DECIMAL(8,2) NOT NULL COMMENT '单次剂量，以最小可分割给药单位（片/粒/袋/支）计',
  dose_unit VARCHAR(16) NOT NULL COMMENT '片/粒/袋/支/丸/枚/揿/吸/ml/次（外用与滴眼鼻喷按「次」计，见 Task 8 口径）',
  frequency VARCHAR(16) NOT NULL COMMENT '取值域见 Task 8：每日1~4次、每晚1次',
  max_daily_dose DECIMAL(8,2) NOT NULL COMMENT '日上限，与 rational_rule.rule_type=2 直接可比',
  days_min INT NOT NULL DEFAULT 1,
  days_max INT NOT NULL DEFAULT 7,
  UNIQUE KEY uk_drug_dose (drug_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='药品剂量规则表';

CREATE TABLE visit_rate (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  age_min INT NOT NULL,
  age_max INT NOT NULL,
  gender TINYINT NOT NULL DEFAULT 0 COMMENT '0不限1男2女',
  multiplier DECIMAL(6,3) NOT NULL DEFAULT 1.000 COMMENT '该段就诊率乘子（相对基准）',
  KEY idx_vr_age (age_min, age_max)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='就诊率配置表（字典表）';

CREATE TABLE diagnosis_operation (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  icd_code VARCHAR(16) NOT NULL,
  department VARCHAR(50) NULL COMMENT 'NULL=全部科室适用；非空=仅该科室的该诊断适用（儿科手术概率远低于成人靠此表达）',
  operation_code VARCHAR(16) NOT NULL COMMENT 'ICD-9-CM-3 手术操作编码',
  operation_name VARCHAR(64) NOT NULL,
  operation_level TINYINT NOT NULL DEFAULT 1 COMMENT '手术级别 1~4',
  probability DECIMAL(4,3) NOT NULL DEFAULT 0 COMMENT '该诊断需手术的概率 0~1',
  min_postop_days INT NOT NULL DEFAULT 1 COMMENT '术后最少住院天数',
  weight DECIMAL(6,3) NOT NULL DEFAULT 1.000 COMMENT '同一诊断多术式时的相对权重',
  KEY idx_dop_icd (icd_code),
  KEY idx_dop_dept (department),
  KEY idx_dop_code (operation_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='诊断→手术本体表';
```

- [ ] **Step 2: 在 `schema.sql` 末尾追加 `operation` 数据表**

```sql
-- ===== 三期：手术记录（数据表，生成前随其它业务表一同清空）=====

CREATE TABLE operation (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  admission_id BIGINT NOT NULL COMMENT '外键 → admission.id，手术必须挂在一次住院下',
  patient_id BIGINT NOT NULL COMMENT '冗余，便于患者时间轴查询',
  operation_code VARCHAR(16) NOT NULL,
  operation_name VARCHAR(64) NOT NULL,
  operation_level TINYINT NOT NULL COMMENT '1~4，决定术后最少观察天数',
  surgeon VARCHAR(50) NOT NULL COMMENT '主刀医师，必须取自该住院科室的 doctor',
  assistant VARCHAR(50),
  anesthesiologist VARCHAR(50) COMMENT '局麻可空',
  anesthesia_type VARCHAR(16) COMMENT '全身麻醉/椎管内麻醉/局部麻醉/无',
  start_time DATETIME NOT NULL,
  end_time DATETIME NOT NULL,
  status TINYINT NOT NULL DEFAULT 1 COMMENT '0已安排1已完成2已取消',
  KEY idx_op_admission (admission_id),
  KEY idx_op_patient_time (patient_id, start_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='手术记录表';
```

- [ ] **Step 3: 在 `schema.sql` 的 `admission` 建表语句里补 4 列**

找到 `admission` 的 `CREATE TABLE`，在 `diagnosis VARCHAR(100),` 之后插入 4 列
（**注意**：是在既有的 `CREATE TABLE admission (...)` 内部加列，供全新安装使用；
已存在的库走 Step 4 的迁移脚本）：

```sql
  admission_route VARCHAR(16) COMMENT 'OUTPATIENT门诊转入/EMERGENCY急诊/TRANSFER转院',
  discharge_disposition VARCHAR(16) COMMENT 'CURED医嘱离院/IMPROVED好转/TRANSFER转院/DEATH死亡/AGAINST_ADVICE自动出院',
  admission_diagnosis VARCHAR(100) COMMENT '入院诊断',
  discharge_diagnosis VARCHAR(100) COMMENT '出院诊断，可与入院诊断不同（诊断演进）',
```

**保留既有的 `diagnosis` 列并在生成时继续写入**（等于 `admission_diagnosis`），以兼容既有前端与查询。

- [ ] **Step 4: 创建 `backend/sql/migrate-phase3.sql`（已有库的一次性迁移）**

本文件必须包含**两部分**，缺一不可：

1. 给已有库的 `admission` 补 4 列（`ALTER`，见下）；
2. **Step 1 与 Step 2 里那 7 条 `CREATE TABLE` 也要原样放进本文件**（DDL 与 `schema.sql` 逐字一致）。

> **为什么建表语句必须在这里重复一份**：`schema.sql` 的 `CREATE TABLE` **不带
> `IF NOT EXISTS`**（全库 23 条都不带），所以对已存在的库重跑 `schema.sql` 会在第一条
> `CREATE TABLE patient` 上直接报 "Table 'patient' already exists" 中止——**它只能用于全新安装**。
> 而已部署的库跑完只有 `ALTER` 的迁移脚本后，7 张新表一张都不会被建，
> Step 5 的断言 1（应为 7）恒为 0，且 Task 2 的实体映射、Task 3–9 的种子装载
> 都会因表不存在而失败。**只写 `ALTER` 的版本是错的**，不要照抄。

```sql
-- 三期迁移：为已存在的 his_demo 库补表 + 补列。**只能执行一次**。
-- 全新安装不需要本文件——schema.sql 已含全部表与列。
USE his_demo;

ALTER TABLE admission
  ADD COLUMN admission_route VARCHAR(16) COMMENT 'OUTPATIENT门诊转入/EMERGENCY急诊/TRANSFER转院',
  ADD COLUMN discharge_disposition VARCHAR(16) COMMENT 'CURED医嘱离院/IMPROVED好转/TRANSFER转院/DEATH死亡/AGAINST_ADVICE自动出院',
  ADD COLUMN admission_diagnosis VARCHAR(100) COMMENT '入院诊断',
  ADD COLUMN discharge_diagnosis VARCHAR(100) COMMENT '出院诊断，可与入院诊断不同（诊断演进）';

-- 以下 7 张表的 DDL 与 schema.sql 逐字一致（Step 1 / Step 2）
-- CREATE TABLE dept_diagnosis (...);
-- CREATE TABLE diagnosis_drug (...);
-- CREATE TABLE diagnosis_lab (...);
-- CREATE TABLE drug_dose (...);
-- CREATE TABLE visit_rate (...);
-- CREATE TABLE diagnosis_operation (...);
-- CREATE TABLE operation (...);
```

> **重复执行的行为实测**（写进文件注释）：`ALTER` 在最前，第二次执行会先在第 5 行报
> `Duplicate column name 'admission_route'`。**不加 `--force` 会就此中止，后面 7 条建表不执行**；
> 加 `--force` 则连同 7 条 `Table already exists` 一起报出并继续跑完。两种输出都属预期。

- [ ] **Step 5: 应用并断言结构（结构断言，非数值断言）**

```bash
cd backend

# 全新库：直接重建（会删掉旧数据，先确认可接受）
mysql -uroot -p123456 --default-character-set=utf8mb4 < sql/schema.sql

# 已有库：只跑迁移
mysql -uroot -p123456 --default-character-set=utf8mb4 < sql/migrate-phase3.sql
```

断言（应为 7 行 `Base` 表、4 行 `admission` 新列）：

```bash
mysql -uroot -p123456 --default-character-set=utf8mb4 -N -e "
SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='his_demo'
  AND table_name IN ('dept_diagnosis','diagnosis_drug','diagnosis_lab','drug_dose',
                     'visit_rate','diagnosis_operation','operation');"

mysql -uroot -p123456 --default-character-set=utf8mb4 -N -e "
SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='his_demo'
  AND table_name='admission'
  AND column_name IN ('admission_route','discharge_disposition',
                      'admission_diagnosis','discharge_diagnosis');"
```

Expected: 第一条输出 `7`，第二条输出 `4`。若第二条为 `0` 而库中已有 `admission` 表，说明 Step 4 的迁移未执行。

- [ ] **Step 6: Commit**

```bash
git add backend/sql/schema.sql backend/sql/migrate-phase3.sql
git commit -m "feat(sql): 三期建表 —— 6 张本体字典表 + operation + admission 增 4 列" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 2: his-common —— 7 实体 + 7 Mapper + `Admission` 增列（阶段一）

**Files:**
- Create: `backend/his-common/src/main/java/com/his/common/entity/DeptDiagnosis.java`
- Create: `.../entity/DiagnosisDrug.java`、`DiagnosisLab.java`、`DrugDose.java`、`VisitRate.java`、`DiagnosisOperation.java`、`Operation.java`
- Create: `backend/his-common/src/main/java/com/his/common/mapper/DeptDiagnosisMapper.java` 等 7 个
- Modify: `backend/his-common/src/main/java/com/his/common/entity/Admission.java`
- Test: 无新增测试；本任务只保证既有测试与 his-generator 编译通过
  （`SeedSqlConsistencyTest` 在 Task 9 建立）

**Interfaces:**
- Consumes: Task 1 的 7 张表与 `admission` 的 4 个新列
- Produces: 7 个实体类与 7 个 Mapper 接口，供 Task 3–9 装载、Task 10/11 校验、Task 21–24 生成器写入。

**本仓库实体风格（逐条照做，不要引入新写法）**：
- 只有 `@Data`（Lombok）。**不要** `@Getter`/`@Setter`/`@Builder`/`@NoArgsConstructor`/`@AllArgsConstructor`/`implements Serializable`。
- `@TableName("表名")` 写全表名（没有全局 `table-prefix` 配置）。
- 主键恒为 `@TableId(type = IdType.AUTO) private Long id;`（**不写 `value =`**）。
- 字段纯 camelCase，靠既有全局配置 `map-underscore-to-camel-case: true` 映射；**不要**写 `@TableField("列名")`。
- import 顺序：先 `lombok.Data`，再 `com.baomidou...` 或 java 类型。
- **`month_weights` 是 JSON 列，Java 侧用 `String`**，由调用方用 `ObjectMapper` 解析——
  与既有 `ClinicalPathway.steps`（`private String steps;`）的做法一致，不引入 typeHandler。

- [ ] **Step 1: 创建 `DeptDiagnosis.java`**

```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;

/** 科室↔诊断本体表：决定「哪个诊断属于哪个科室、适用于什么年龄性别」。 */
@Data
@TableName("dept_diagnosis")
public class DeptDiagnosis {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String department;
    private String icdCode;
    private String diagnosisName;
    /** 0不限1仅男2仅女。**与 `Patient.gender`（0男1女）不是同一个域**，勿跨表套用。 */
    private Integer gender;
    private Integer minAge;
    private Integer maxAge;
    private Integer chronic;
    private Integer admissionEligible;
    private BigDecimal weight;
    /** JSON 文本，长度 12 的正数数组；由调用方用 ObjectMapper 解析。**列可为 NULL，表示无季节性（不是全 1）**。 */
    private String monthWeights;
    private BigDecimal prevalence;
}
```

- [ ] **Step 2: 创建 `DiagnosisDrug.java` 与 `DiagnosisLab.java`**

```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;

/** 诊断→药品本体表：决定某个诊断该开哪些药。 */
@Data
@TableName("diagnosis_drug")
public class DiagnosisDrug {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String icdCode;
    /** 可为 null，表示该行对全部科室适用；非空表示仅该科室适用。匹配规则见 OntologyRules.select。 */
    private String department;
    private Long drugId;
    private Integer line;
    private BigDecimal weight;
    private Integer postop;
}
```

```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;

/** 诊断→检验本体表：本表自身即检验项目池，不存在全局随机抽取的化验池。 */
@Data
@TableName("diagnosis_lab")
public class DiagnosisLab {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String icdCode;
    /** 可为 null，表示该行对全部科室适用；非空表示仅该科室适用。匹配规则见 OntologyRules.select。 */
    private String department;
    private String itemCode;
    private String itemName;
    private String unit;
    private String refRange;
    private String abnormalDirection;
    private BigDecimal abnormalProb;
    private BigDecimal weight;
    private Integer required;
}
```

- [ ] **Step 3: 创建 `DrugDose.java` 与 `VisitRate.java`**

```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;

/** 药品剂量规则表：单次剂量 × 每日次数 ≤ max_daily_dose，单位与 rational_rule.rule_type=2 直接可比。 */
@Data
@TableName("drug_dose")
public class DrugDose {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long drugId;
    private BigDecimal singleDose;
    private String doseUnit;
    private String frequency;
    private BigDecimal maxDailyDose;
    private Integer daysMin;
    private Integer daysMax;
}
```

```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;

/** 就诊率配置表：年龄段/性别 → 就诊率乘子。 */
@Data
@TableName("visit_rate")
public class VisitRate {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Integer ageMin;
    private Integer ageMax;
    private Integer gender;
    private BigDecimal multiplier;
}
```

- [ ] **Step 4: 创建 `DiagnosisOperation.java` 与 `Operation.java`**

```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;

/** 诊断→手术本体表：决定某个诊断能不能做手术、做哪个术式、术后最少住几天。 */
@Data
@TableName("diagnosis_operation")
public class DiagnosisOperation {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String icdCode;
    /** 可为 null，表示该行对全部科室适用；非空表示仅该科室适用。匹配规则见 OntologyRules.select。 */
    private String department;
    private String operationCode;
    private String operationName;
    private Integer operationLevel;
    private BigDecimal probability;
    private Integer minPostopDays;
    private BigDecimal weight;
}
```

```java
package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

/** 手术记录表：手术必须挂在一次住院下，时间必须落在该次住院区间内。 */
@Data
@TableName("operation")
public class Operation {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long admissionId;
    private Long patientId;
    private String operationCode;
    private String operationName;
    private Integer operationLevel;
    private String surgeon;
    private String assistant;
    private String anesthesiologist;
    private String anesthesiaType;
    private LocalDateTime startTime;
    private LocalDateTime endTime;
    private Integer status;
}
```

- [ ] **Step 5: 创建 7 个 Mapper**

每个文件形如（其余 6 个把名字换成对应实体，**方法体一律留空**——本模块所有自定义 SQL 都写注解，
但本期这 7 个 Mapper 不需要任何自定义方法，全部走 `BaseMapper`）：

`backend/his-common/src/main/java/com/his/common/mapper/DeptDiagnosisMapper.java`
```java
package com.his.common.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.his.common.entity.DeptDiagnosis;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface DeptDiagnosisMapper extends BaseMapper<DeptDiagnosis> {
}
```

另外 6 个：`DiagnosisDrugMapper`、`DiagnosisLabMapper`、`DrugDoseMapper`、`VisitRateMapper`、
`DiagnosisOperationMapper`、`OperationMapper`，逐个对应实体。

> 无需改任何配置：`his-server/config/MybatisPlusConfig.java` 已有
> `@MapperScan("com.his.common.mapper")`，放在本包下自动被扫到。

- [ ] **Step 6: 修改 `Admission.java`，追加 4 个字段**

在既有的 `private String diagnosis;` **之后**插入（其余字段与顺序不动）：

```java
    /** OUTPATIENT门诊转入 / EMERGENCY急诊 / TRANSFER转院 */
    private String admissionRoute;
    /** CURED医嘱离院 / IMPROVED好转 / TRANSFER转院 / DEATH死亡 / AGAINST_ADVICE自动出院 */
    private String dischargeDisposition;
    private String admissionDiagnosis;
    private String dischargeDiagnosis;
```

- [ ] **Step 7: 编译验证**

```bash
cd backend && mvn -o -q -pl his-common -am install -DskipTests
```

Expected: BUILD SUCCESS。

- [ ] **Step 8: Commit**

```bash
git add backend/his-common/src/main/java/com/his/common/entity backend/his-common/src/main/java/com/his/common/mapper
git commit -m "feat(common): 三期实体与 Mapper —— 6 张本体表 + operation + admission 增 4 列" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 3: `seed-ontology.sql` 药品字典扩充 + 就诊率（阶段一）

**Files:**
- Create: `backend/sql/seed-ontology.sql`（本任务建立文件并写入前两节；Task 4–9 继续追加）
- Test: 无单元测试——本任务只入库字典数据；跨表一致性由 Task 9 的
  `SeedSqlConsistencyTest` 与 `verify-final.sql` 断言（**那时才校验，此处不重复**）

**Interfaces:**
- Consumes: Task 1 的 `drug`（既有表）与 `visit_rate`（新表）
- Produces: `drug` 扩至 **196 行**（原 12 + 新增 184）、`visit_rate` 12 行；
  `drug.id` 与 `visit_rate` 供 Task 7 的 `diagnosis_drug`、Task 8 的 `drug_dose`
  与阶段二生成器读取

**为什么药品要扩**（spec §9.2）：现字典仅 12 种药，而 `dept_diagnosis` 要覆盖 6 个科室 72 个诊断。
12 种药无法支撑 72 个诊断的用药映射——**「妇科/皮肤科/五官科无药可用」正是「用药不真实」的直接成因之一**
（现生成器只能从 12 种里随机挑，跨科室乱开是必然而非偶然）。

**药品 id 必须显式指定**：`diagnosis_drug.drug_id` 引用的是数字 id，
若靠自增顺序隐式决定，任何一次插入顺序变动都会静默错位。故本节
**全部 196 行都显式写 `id`**：既有 12 行（id 1~12，`seed.sql` 已定，不得改动）保持不变，
新增 184 行用 id 13~196。**本脚本依赖 `seed.sql` 先执行**，且**只能执行一次**
（重复执行会主键冲突——与 `seed.sql` 相同的前提）。

**价格与规格的性质**：spec §9 只为药品来源列了「国家医保药品目录（2024）——**通用名与剂型**」。
**零售价不在任何来源之内**，故 `price` 一律是**演示用模拟价**，SQL 注释里必须写明这一点；
`spec` 按目录剂型填写。

- [ ] **Step 1: 创建 `seed-ontology.sql` 文件头**

```sql
-- 模拟 HIS 系统三期 —— 医学知识本体种子
-- 执行：mysql -uroot -p123456 --default-character-set=utf8mb4 his_demo < backend/sql/seed-ontology.sql
--
-- 前置：必须先执行 seed.sql（drug 的 id 1~12 由它定义）与 seed-extension.sql。
-- 注意：本脚本的 **drug 段只能执行一次**——drug 主键会拒绝重复插入。
--       visit_rate 段不同：它没有唯一键，故 Step 3 在 INSERT 前先 DELETE，
--       整段可重复执行（重复执行的后果与重建方法都写在 Step 3 的注释里）。
--
-- 数据来源（spec §9）：
--   诊断编码与名称  = ICD-10 国家临床版（卫健委）
--   药品通用名与剂型 = 国家医保药品目录（2024）
--   科室归属/检查项/术式 = 卫健委《临床路径》
--   单次剂量/频次/日上限 = 中国药品说明书（公开）
--   就诊率形状      = 卫健委统计公报（2019）
--   模拟价           = 演示用模拟零售价，**非真实中标价**，不作为任何医学事实引用
--
-- 每条 INSERT 前的注释标注该段的来源；凡标「假设」的取值必须在论文中声明为假设。

-- ===== 1. 药品字典扩充：id 13~196（id 1~12 见 seed.sql，不得改动）=====
-- 来源：国家医保药品目录（2024）通用名与剂型；价格为模拟价
```

- [ ] **Step 2: 写入 184 种新增药品**

按下方清单逐行转成 `INSERT INTO drug (id, name, category, spec, unit, price) VALUES ...`。
列序即：**id / 通用名 / 分类 / 规格 / 单位 / 模拟价**。分类名与既有 12 行保持同一取值域。

```
抗感染（24 种）
13  头孢克肟分散片          抗感染   0.1g*12片        盒  32.00
14  头孢呋辛酯片            抗感染   0.25g*12片       盒  29.50
15  阿奇霉素分散片          抗感染   0.25g*6片        盒  26.80
16  罗红霉素胶囊            抗感染   0.15g*12粒       盒  21.00
17  克拉霉素片              抗感染   0.25g*8片        盒  33.50
18  甲硝唑片                抗感染   0.2g*24片        盒   6.80
19  替硝唑片                抗感染   0.5g*8片         盒  12.40
20  氟康唑胶囊              抗感染   50mg*12粒        盒  24.00
21  伊曲康唑胶囊            抗感染   0.1g*14粒        盒  58.00
22  阿昔洛韦片              抗感染   0.2g*24片        盒  15.60
23  伐昔洛韦片              抗感染   0.3g*6片         盒  42.00
24  奥司他韦胶囊            抗感染   75mg*10粒        盒  68.00
25  利巴韦林颗粒            抗感染   50mg*24袋        盒   9.80
26  吡哌酸片                抗感染   0.25g*24片       盒   7.20
27  诺氟沙星胶囊            抗感染   0.1g*24粒        盒   8.50
28  环丙沙星片              抗感染   0.25g*12片       盒  14.20
29  莫西沙星片              抗感染   0.4g*5片         盒  76.00
30  头孢氨苄胶囊            抗感染   0.25g*24粒       盒  16.40
31  头孢拉定胶囊            抗感染   0.25g*24粒       盒  18.00
32  阿莫西林克拉维酸钾片    抗感染   0.457g*12片      盒  38.60
33  氨苄西林胶囊            抗感染   0.25g*24粒       盒  13.50
34  青霉素V钾片             抗感染   0.236g*24片      盒  11.20
35  多西环素片              抗感染   0.1g*12片        盒  19.80
36  米诺环素胶囊            抗感染   50mg*20粒        盒  45.00

心血管（26 种）
37  缬沙坦胶囊              心血管   80mg*14粒        盒  36.00
38  氯沙坦钾片              心血管   50mg*14片        盒  33.20
39  厄贝沙坦片              心血管   75mg*28片        盒  41.50
40  培哚普利叔丁胺片        心血管   4mg*30片         盒  48.00
41  卡托普利片              心血管   25mg*100片       盒  12.60
42  依那普利片              心血管   10mg*16片        盒  18.40
43  美托洛尔缓释片          心血管   47.5mg*7片       盒  22.80
44  比索洛尔片              心血管   5mg*10片         盒  25.60
45  阿替洛尔片              心血管   25mg*20片        盒   9.40
46  普萘洛尔片              心血管   10mg*100片       盒   8.20
47  呋塞米片                心血管   20mg*100片       盒   7.60
48  氢氯噻嗪片              心血管   25mg*100片       盒   6.90
49  螺内酯片                心血管   20mg*100片       盒  14.80
50  吲达帕胺缓释片          心血管   1.5mg*30片       盒  26.40
51  硝酸甘油片              心血管   0.5mg*100片      盒  16.80
52  单硝酸异山梨酯缓释片    心血管   40mg*7片         盒  23.20
53  瑞舒伐他汀钙片          心血管   10mg*28片        盒  52.00
54  阿托伐他汀钙片          心血管   20mg*7片         盒  34.60
55  辛伐他汀片              心血管   20mg*20片        盒  28.40
56  非诺贝特胶囊            心血管   0.2g*10粒        盒  31.00
57  华法林钠片              心血管   2.5mg*60片       盒  21.60
58  氯吡格雷片              心血管   75mg*7片         盒  39.80
59  替格瑞洛片              心血管   90mg*14片        盒  96.00
60  利伐沙班片              心血管   10mg*5片         盒  78.00
61  地高辛片                心血管   0.25mg*100片     盒  18.90
62  胺碘酮片                心血管   0.2g*10片        盒  27.50

消化（18 种）
63  铝碳酸镁咀嚼片          消化     0.5g*20片        盒  17.40
64  雷贝拉唑钠肠溶片        消化     10mg*14片        盒  44.00
65  兰索拉唑肠溶片          消化     15mg*14片        盒  36.80
66  泮托拉唑钠肠溶片        消化     40mg*14片        盒  40.20
67  法莫替丁片              消化     20mg*20片        盒  15.60
68  枸橼酸铋钾胶囊          消化     0.3g*24粒        盒  19.20
69  多潘立酮片              消化     10mg*30片        盒  13.80
70  莫沙必利片              消化     5mg*24片         盒  22.60
71  消旋山莨菪碱片          消化     5mg*20片         盒   6.40
72  匹维溴铵片              消化     50mg*15片        盒  32.80
73  双歧杆菌三联活菌胶囊    消化     210mg*36粒       盒  41.00
74  地衣芽孢杆菌活菌胶囊    消化     0.25g*20粒       盒  28.60
75  乳果糖口服溶液          消化     100ml            瓶  34.20
76  聚乙二醇4000散          消化     10g*10袋         盒  26.00
77  复方聚乙二醇电解质散    消化     137.15g/袋       盒  38.40
78  熊去氧胆酸胶囊          消化     0.25g*24粒       盒  89.00
79  甘草酸二铵肠溶胶囊      消化     50mg*24粒        盒  35.60
80  水飞蓟宾胶囊            消化     35mg*24粒        盒  46.80

呼吸（16 种）
81  氨溴索口服溶液          呼吸     100ml            瓶  21.60
82  乙酰半胱氨酸泡腾片      呼吸     0.6g*6片         盒  29.80
83  右美沙芬愈创甘油醚糖浆  呼吸     120ml            瓶  24.40
84  复方甲氧那明胶囊        呼吸     12粒             盒  26.20
85  沙丁胺醇气雾剂          呼吸     100μg*200揿      支  32.60
86  布地奈德福莫特罗粉吸入剂 呼吸    60吸             支 168.00
87  噻托溴铵粉雾剂          呼吸     18μg*10粒        盒 112.00
88  孟鲁司特钠片            呼吸     10mg*5片         盒  38.20
89  茶碱缓释片              呼吸     0.1g*24片        盒  14.60
90  氯雷他定片              呼吸     10mg*6片         盒  15.20
91  西替利嗪片              呼吸     10mg*12片        盒  16.80
92  依巴斯汀片              呼吸     10mg*10片        盒  25.40
93  马来酸氯苯那敏片        呼吸     4mg*100片        盒   5.80
94  复方甘草片              呼吸     100片            瓶   9.60
95  肺力咳合剂              呼吸     100ml            瓶  27.80
96  苏黄止咳胶囊            呼吸     0.45g*24粒       盒  42.40

解热镇痛 / 抗痛风 / 抗风湿（14 种）
97  对乙酰氨基酚片          解热镇痛 0.5g*20片        盒   9.20
98  双氯芬酸钠缓释片        解热镇痛 75mg*10片        盒  18.60
99  塞来昔布胶囊            解热镇痛 0.2g*6粒         盒  36.00
100 美洛昔康片              解热镇痛 7.5mg*10片       盒  22.40
101 洛索洛芬钠片            解热镇痛 60mg*20片        盒  28.80
102 依托考昔片              解热镇痛 60mg*5片         盒  42.60
103 吲哚美辛肠溶片          解热镇痛 25mg*100片       盒   8.40
104 艾瑞昔布片              解热镇痛 0.1g*10片        盒  58.00
105 秋水仙碱片              抗痛风   0.5mg*20片        盒  26.60
106 别嘌醇片                抗痛风   0.1g*100片        盒  18.20
107 非布司他片              抗痛风   40mg*14片         盒  68.40
108 苯溴马隆片              抗痛风   50mg*10片         盒  32.00
109 甲氨蝶呤片              抗风湿   2.5mg*16片        盒  21.40
110 雷公藤多苷片            抗风湿   10mg*50片         盒  44.60

降糖（9 种）
111 阿卡波糖片              降糖     50mg*30片        盒  46.20
112 伏格列波糖片            降糖     0.2mg*20片       盒  38.80
113 瑞格列奈片              降糖     1mg*30片         盒  33.40
114 那格列奈片              降糖     0.12g*12片       盒  29.60
115 西格列汀片              降糖     100mg*14片       盒  86.00
116 达格列净片              降糖     10mg*14片        盒  92.40
117 恩格列净片              降糖     10mg*10片        盒  78.60
118 吡格列酮片              降糖     15mg*14片        盒  36.20
119 罗格列酮片              降糖     4mg*14片         盒  34.80

内分泌（3 种）
120 左甲状腺素钠片          内分泌   50μg*100片       盒  28.20
121 甲巯咪唑片              内分泌   10mg*50片        盒  19.40
122 丙硫氧嘧啶片            内分泌   50mg*100片       盒  24.60

神经精神（14 种）
123 阿普唑仑片              神经精神 0.4mg*20片       盒  12.80
124 艾司唑仑片              神经精神 1mg*20片         盒  10.60
125 佐匹克隆片              神经精神 7.5mg*10片       盒  23.40
126 右佐匹克隆片            神经精神 3mg*7片         盒  34.20
127 舍曲林片                神经精神 50mg*14片       盒  42.80
128 帕罗西汀片              神经精神 20mg*14片       盒  46.40
129 氟西汀胶囊              神经精神 20mg*14粒       盒  44.20
130 文拉法辛缓释胶囊        神经精神 75mg*14粒       盒  58.60
131 米氮平片                神经精神 30mg*10片       盒  52.40
132 奥氮平片                神经精神 10mg*7片        盒  76.80
133 喹硫平片                神经精神 0.1g*14片       盒  68.20
134 卡马西平片              神经精神 0.2g*30片       盒  21.60
135 丙戊酸钠缓释片          神经精神 0.5g*30片       盒  48.20
136 左乙拉西坦片            神经精神 0.5g*30片       盒  92.60

泌尿（8 种）
137 坦索罗辛缓释胶囊        泌尿     0.2mg*10粒       盒  48.40
138 非那雄胺片              泌尿     5mg*10片         盒  52.20
139 特拉唑嗪片              泌尿     2mg*14片         盒  26.80
140 托特罗定片              泌尿     2mg*14片         盒  38.60
141 索利那新片              泌尿     5mg*10片         盒  56.40
142 枸橼酸西地那非片        泌尿     50mg*5片         盒  62.00
143 尿毒清颗粒              泌尿     5g*15袋          盒  58.80
144 碳酸氢钠片              泌尿     0.5g*100片       瓶   7.40

皮肤外用（12 种）
145 糠酸莫米松乳膏          皮肤外用 5g               支  28.60
146 丁酸氢化可的松乳膏      皮肤外用 10g              支  16.40
147 卤米松乳膏              皮肤外用 10g              支  32.80
148 复方醋酸地塞米松乳膏    皮肤外用 20g              支  12.60
149 曲安奈德益康唑乳膏      皮肤外用 15g              支  21.80
150 联苯苄唑乳膏            皮肤外用 20g              支  24.20
151 特比萘芬乳膏            皮肤外用 15g              支  27.40
152 阿昔洛韦乳膏            皮肤外用 10g              支  11.60
153 莫匹罗星软膏            皮肤外用 10g              支  29.80
154 夫西地酸乳膏            皮肤外用 15g              支  26.20
155 他克莫司软膏            皮肤外用 10g              支  98.00
156 炉甘石洗剂              皮肤外用 100ml            瓶   8.60

五官科（8 种）
157 氧氟沙星滴耳液          五官科   5ml               支   9.80
158 左氧氟沙星滴眼液        五官科   5ml               支  14.60
159 妥布霉素滴眼液          五官科   5ml               支  12.40
160 玻璃酸钠滴眼液          五官科   5ml               支  36.80
161 糠酸莫米松鼻喷雾剂      五官科   60揿              支  68.40
162 布地奈德鼻喷雾剂        五官科   64μg*120揿       支  74.20
163 复方硼砂含漱液          五官科   250ml             瓶  13.20
164 西地碘含片              五官科   1.5mg*24片        盒  15.40

妇科（6 种）
165 克霉唑阴道片            妇科     0.5g*2片          盒  26.40
166 硝呋太尔制霉素阴道软胶囊 妇科    6粒               盒  38.20
167 甲硝唑阴道泡腾片        妇科     0.2g*7片          盒  17.60
168 保妇康栓                妇科     1.74g*6枚         盒  32.80
169 屈螺酮炔雌醇片          妇科     21片              盒  68.00
170 地屈孕酮片              妇科     10mg*20片         盒  88.40

其他（16 种）
171 维生素C片               其他     0.1g*100片        瓶   6.20
172 复合维生素B片           其他     100片             瓶   5.40
173 维生素B12片             其他     25μg*100片        瓶   7.80
174 叶酸片                  其他     0.4mg*93片        瓶  12.40
175 维生素D3滴剂            其他     400IU*30粒        盒  32.60
176 碳酸钙D3片              其他     0.6g*60片         瓶  38.80
177 葡萄糖酸钙口服溶液      其他     10ml*12支         盒  26.20
178 氯化钾缓释片            其他     0.5g*24片         盒  14.80
179 口服补液盐III           其他     5.125g*6袋        盒  16.40
180 云南白药胶囊            其他     0.25g*16粒        盒  34.20
181 血塞通片                其他     50mg*24片         盒  28.60
182 复方丹参滴丸            其他     27mg*180丸        盒  42.80
183 速效救心丸              其他     40mg*60丸         盒  36.40
184 藿香正气口服液          其他     10ml*10支         盒  18.60
185 连花清瘟胶囊            其他     0.35g*24粒        盒  26.80
186 蓝芩口服液              其他     10ml*6支          盒  24.20
```

汇总校验：174 行 = 24+26+18+16+14+9+3+14+8+12+8+6+16。加原有 12 行，至此 `drug` 共 186 行。
（其中 `内分泌` 3 种是 id 120~122：左甲状腺素钠片、甲巯咪唑片、丙硫氧嘧啶片。
 这三种是甲状腺用药，**不是降糖药**——原先把它们记在 `降糖` 是分类错误，会让读数据的人
 一眼看出「甲巯咪唑是降糖药」这种硬伤，正是本次改造要消灭的那类不真实。`category`
 没有任何代码消费方，改它不动 id、不动 Task 7 的 `diagnosis_drug.drug_id` 映射。）
（这是 id 13~186 这一批的**中途校验**；紧接着还有下面追加的 10 种，最终是 196 行。）

**追加 10 种：起草用药映射时暴露的字典缺口（映射见 Task 7）**

起草 Task 7 的用药映射时发现，186 种药里**缺了几类必需药**，
导致若干诊断**无对症药可开**（若无这批药，痤疮只能用激素乳膏、青光眼只能用人工泪液，
就是把「用药不真实」从旧代码搬到了新本体里）：

```
187 噻吗洛尔滴眼液        五官科   5ml               支  18.60   ← 青光眼降眼压
188 拉坦前列素滴眼液      五官科   2.5ml             支  96.00   ← 青光眼一线
189 甲磺酸倍他司汀片      其他     6mg*20片          盒  26.40   ← 眩晕
190 阿达帕林凝胶          皮肤外用 15g               支  42.80   ← 痤疮
191 过氧苯甲酰凝胶        皮肤外用 15g               支  28.60   ← 痤疮
192 克林霉素磷酸酯凝胶    皮肤外用 10g               支  22.40   ← 痤疮
193 异维A酸软胶囊         其他     10mg*20粒        盒  68.20   ← 重度痤疮（口服，故不归「皮肤外用」）
194 维A酸乳膏             皮肤外用 15g               支  16.80   ← 痤疮/银屑病
195 卡泊三醇软膏          皮肤外用 15g               支  78.40   ← 银屑病
196 复方甘草酸苷片        其他     50mg*36片         盒  46.20   ← 慢性肝病辅助
```

**`spec` 列的书写口径（已知的演示用近似，不算缺陷）**

`spec` 只用于界面展示，**没有任何代码消费方**（生成器只读 `drug.id` 与 `drug.name`）。
其中两类行按行业惯例只写包装量、不写含量：

- **复方/复合制剂**：`复方甲氧那明胶囊`、`布地奈德福莫特罗粉吸入剂`、`屈螺酮炔雌醇片`、
  `硝呋太尔制霉素阴道软胶囊`、`复合维生素B片`——组方本身没有单一含量可写；
- **单方外用/吸入制剂**：`阿达帕林凝胶`、`维A酸乳膏`、`卡泊三醇软膏`、`噻吗洛尔滴眼液`、
  `拉坦前列素滴眼液`——真实包装标签会带浓度（如 `0.1%*15g`），此处从简。

论文中引用 `drug` 表时按此声明即可，**不要求修复轮逐行补齐浓度**（补齐不改变任何生成行为，
且会让 id 13~196 的清单与 Task 7 草拟映射时用的清单不一致）。

**为什么追加而不是回头改前面的行**：`diagnosis_drug.drug_id` 引用数字 id，
把新药插进 13~186 之间会让其后所有 id 顺移，而 Task 4–6 的种子与 Task 8 的一致性测试
都会跟着错位。**只有追加才安全**。

最终 `drug` 共 **196 行**（原 12 + 新增 184）。Step 4 的断言随之更新为：
`drug_total 196`、`drug_new 184`、`drug_max_id 196`。

- [ ] **Step 3: 写入 `visit_rate` 12 行**

```sql
-- ===== 2. 就诊率配置（字典表）=====
-- 来源：卫健委统计公报（2019）——人均年就诊 6.2 次、年龄别 U 形曲线、
--       女性就诊率高于男性（本表三个拆分段的比值分别约 +47.6% / +76.9% / +71.4%，
--       即本表数据落在 47.6%~76.9% 区间；「30%~46%」是过时的草稿数字，已按本表实测比值改正）。
-- 语义：multiplier 是相对「全人群基准 6.2 次/年」的乘子。
--       gender=0 的行是该年龄段的**两性平均**；gender=1/2 的行在生成时**取代**同段 gender=0 行。
--       故每个有性别拆分的年龄段，其男女两行必须平均回该段 gender=0 的值。
--
-- 幂等：visit_rate **没有唯一键**（见 schema.sql 的 idx_vr_age，只是普通索引），
--       也不被生成器清空（不在 DataGeneratorService.DATA_TABLES 的 11 张业务表内）。
--       故本节的「只能执行一次」是纪律约束而非数据库约束——不加下面这行 DELETE 直接重跑，
--       会**静默**累积成 24/36/… 行，而 Step 4 的断言只看行数、查不出来源重复。
--       本表没有任何外键指向它，DELETE 永远安全。
DELETE FROM visit_rate;
INSERT INTO visit_rate (age_min, age_max, gender, multiplier) VALUES
(0,  4,  0, 2.200),   -- 幼儿段高就诊
(5,  14, 0, 1.100),
(15, 24, 0, 0.760),
(15, 24, 1, 0.560),   -- (0.560+0.960)/2 = 0.760 ✓
(15, 24, 2, 0.960),
(25, 44, 0, 0.900),
(25, 44, 1, 0.650),   -- (0.650+1.150)/2 = 0.900 ✓
(25, 44, 2, 1.150),
(45, 64, 0, 1.300),
(45, 64, 1, 1.050),   -- (1.050+1.550)/2 = 1.300 ✓
(45, 64, 2, 1.550),
(65, 89, 0, 2.000);   -- 老年段高就诊，不再作性别拆分
```

- [ ] **Step 4: 入库并断言行数与 id 连续性**

```bash
cd backend
mysql -uroot -p123456 --default-character-set=utf8mb4 his_demo < sql/seed-ontology.sql
```

```bash
mysql -uroot -p123456 --default-character-set=utf8mb4 -N -e "
SELECT 'drug_total', COUNT(*) FROM his_demo.drug
UNION ALL SELECT 'drug_new',  COUNT(*) FROM his_demo.drug WHERE id >= 13
UNION ALL SELECT 'drug_min_id', MIN(id) FROM his_demo.drug
UNION ALL SELECT 'drug_max_id', MAX(id) FROM his_demo.drug
UNION ALL SELECT 'visit_rate', COUNT(*) FROM his_demo.visit_rate;"
```

Expected: `drug_total 196`、`drug_new 184`、`drug_min_id 1`、`drug_max_id 196`、`visit_rate 12`。
**若 `drug_total` 是 360，说明脚本被重复执行了**（字典表不被生成器清空，重复插入会累积）——
此时须 `DELETE FROM drug WHERE id >= 13;` 后重跑。

- [ ] **Step 5: 断言既有 12 种药未被改动**

```bash
mysql -uroot -p123456 --default-character-set=utf8mb4 -N -e "
SELECT id, name, category FROM his_demo.drug WHERE id <= 12 ORDER BY id;"
```

Expected: 逐行为 `1 阿莫西林胶囊 抗感染` … `12 复方甘草口服液 呼吸`，
与 `seed.sql` 第 4~15 行**逐字一致**。**任何一行不同都说明 id 被重排**，
而 `rule_config.drug_ids` / `rational_rule` 引用的是这些 id，重排会让它们静默指向错药。

- [ ] **Step 6: Commit**

```bash
git add backend/sql/seed-ontology.sql
git commit -m "feat(sql): 三期本体种子 —— 药品字典扩充至 196 种 + 就诊率配置" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 4: `dept_diagnosis` 科室↔诊断本体 72 条（阶段一）

**Files:**
- Modify: `backend/sql/seed-ontology.sql`（追加第 3 节）
- Test: 无单元测试——跨表一致性在 Task 9；本任务末尾是本表**自洽性**的 SQL 断言

**Interfaces:**
- Consumes: 无（本表不引用其它新表）
- Produces: `dept_diagnosis` 72 行，是阶段二生成器**唯一的诊断来源**（取代 `MedicalData.DiagnosisRule`），
  也是 Task 11 本体校验器的判定依据；`admission_eligible` 被 Task 11 的 ADMISSION 检查读取

**这张表就是「妇科医生看肠胃炎」的解法**：科室归属不再是医生身上的一个属性、
诊断也不再是独立抽取的标签，而是由本表把二者绑成一条**本体事实**。
生成器只允许从「该科室本表登记过的诊断」里抽，跨科室组合从结构上不可产生。

**每条都标来源，且必须经作者审校**（spec §9.1）：ICD 编码与诊断名取自
ICD-10 国家临床版，科室归属与可住院性取自卫健委《临床路径》。
**编码逐条核对**是本任务最容易被跳过、也最不该跳过的一步——错编码不会让任何测试变红，
只会让论文里的诊断名与编码对不上。Step 1 给出核对办法。

- [ ] **Step 1: 先定季节模式（`month_weights` 的 JSON 字面量）**

72 行各写一个 12 元数组会淹没差异，故把用到的模式集中定义一次，行里只填模式名：

```sql
-- month_weights 的季节模式（长度 12 的正数数组，索引 0=1月 … 11=12月；NULL 表示无季节性）
-- 来源：卫健委《临床路径》各病种说明与统计公报分月数据；无逐病种公开分月数据的按病种性质归类
--   WINTER        呼吸道/冬季高发（流感样、肺炎、慢阻肺急性加重、心脑血管事件）
--   SUMMER        肠道与虫媒/高温相关（感染性腹泻、手足口病、皮肤感染、结膜炎）
--   SPRING_AUTUMN 过敏性疾病双峰（过敏性鼻炎、哮喘、荨麻疹、湿疹）
SET @WINTER        = '[1.4,1.3,1.1,0.9,0.8,0.7,0.7,0.8,0.9,1.0,1.2,1.4]';
SET @SUMMER        = '[0.7,0.7,0.9,1.1,1.3,1.5,1.5,1.4,1.2,1.0,0.8,0.7]';
SET @SPRING_AUTUMN = '[0.8,0.9,1.3,1.4,1.2,1.0,0.9,1.0,1.2,1.3,1.0,0.8]';
```

写入时 `month_weights` 列填 `NULL` 或对应的 `@WINTER` / `@SUMMER` / `@SPRING_AUTUMN`。
三个数组各自之和约等于 12（实测 12.2 / 12.8 / 12.8：WINTER 12.2、SUMMER 12.8、SPRING_AUTUMN 12.8），
**相对形状有意义、绝对水平无意义**——
生成器按「同科室同年龄段内归一化」使用，故不需要它们精确等于 12。

- [ ] **Step 2: 写入 72 行 `dept_diagnosis`**

列序即：**科室 / ICD-10 / 诊断名 / 性别 / 年龄下限 / 年龄上限 / 慢性 / 可住院 / 权重 / 季节 / 患病率**。
性别 `0不限 1仅男 2仅女`；慢性与可住院 `0/1`；患病率仅慢病非零。

**本节开头先加一条 `DELETE`**（与第 2 节的 `visit_rate` 同款纪律）：

```sql
-- 本节可重复执行：dept_diagnosis **没有唯一键**（见 schema.sql，只有 idx_dd_dept/idx_dd_icd/idx_dd_chronic
-- 三个普通索引），重跑本段不会报错，而是**静默**累加成 144/216… 行。
-- 而 72 这个数正是下游的契约：Task 5 的检验组、Task 7 的用药映射都按本表的
-- (department, icd_code) 组合逐一对应，Task 11/13 把它当取样池——多一行就静默改变它们。
-- 本表没有任何外键指向它，DELETE 永远安全。
DELETE FROM dept_diagnosis;
```

> **抽取本节时必须整段取。** Step 1 的 `SET @WINTER/@SUMMER/@SPRING_AUTUMN`
> 与本步的数据行是**两条独立语句**。若只抽 INSERT 部分执行，MySQL 对**未定义的用户变量取 `NULL`**，
> 而 `month_weights` 列允许 `NULL` → **33 行的季节乘子被静默写成 `NULL`**：
> 不报错、不告警，Step 4 原有的十条断言**一条都查不出来**（它们没有一条检查季节列）。
> 这条路径很现实——「只抽 INSERT 灌库」是手工执行时最常见的做法。
> 故 Step 4 追加一条季节完整性自检，且抽取时务必从 `SET` 那行开始。

```
内科（15 条）
内科  I10    原发性高血压                 0  18 89  1 1  4.000  NULL          0.2500
内科  E11.9  2型糖尿病                   0  18 89  1 1  3.600  NULL          0.1100
内科  K29.5  慢性胃炎                     0  18 89  1 1  3.200  NULL          0.1200
内科  E78.5  高脂血症                     0  18 89  1 0  2.800  NULL          0.1800
内科  I25.1  冠状动脉粥样硬化性心脏病     0  40 89  1 1  2.600  WINTER        0.0300
内科  K21.0  胃食管反流病                 0  18 89  1 1  2.400  NULL          0.0600
内科  J44.9  慢性阻塞性肺疾病             0  40 89  1 1  2.200  WINTER        0.0400
内科  N18.9  慢性肾脏病                   0  18 89  1 1  1.400  NULL          0.0200
内科  I48    心房颤动                     0  40 89  1 1  1.200  NULL          0.0100
内科  E05.9  甲状腺功能亢进症             2  18 70  1 1  1.600  NULL          0.0100
内科  J06.9  上呼吸道感染                 0   0 89  0 0  3.000  WINTER        0
内科  J20.9  急性支气管炎                 0   0 89  0 1  2.400  WINTER        0
内科  A09.9  急性胃肠炎                   0   0 89  0 1  2.600  SUMMER        0
内科  R50.9  发热                         0   0 89  0 1  2.200  SUMMER        0
内科  G43.9  偏头痛                       0  10 60  0 0  1.600  NULL          0

外科（13 条）—— 本表是 diagnosis_operation 的挂接来源
外科  K35.8  急性阑尾炎                   0   5 89  0 1  3.200  NULL          0
外科  K80.1  胆囊结石伴胆囊炎             0  25 89  0 1  3.000  NULL          0
外科  S52.5  桡骨远端骨折                 0   5 89  0 1  2.200  NULL          0
外科  E04.1  甲状腺结节                   2  20 70  0 1  2.600  NULL          0
外科  M17.9  膝骨关节炎                   0  45 89  1 1  2.400  WINTER        0.0700
外科  I84.2  内痔                         0  18 89  0 1  2.200  NULL          0
外科  S61.0  手指开放性伤口               0   5 89  0 1  2.000  NULL          0
外科  K40.9  腹股沟疝                     1   0 89  0 1  2.000  NULL          0
外科  L02.9  皮肤脓肿                     0   0 89  0 1  1.800  SUMMER        0
外科  D23.9  皮肤良性肿瘤                 0  10 89  0 1  1.600  NULL          0
外科  K63.5  结肠息肉                     0  40 89  0 1  1.600  NULL          0
外科  K42.9  脐疝                         0   0 89  0 1  1.000  NULL          0
外科  C73    甲状腺恶性肿瘤               2  20 80  0 1  1.000  NULL          0

儿科（11 条）
儿科  J06.9  上呼吸道感染                 0   0 14  0 0  4.000  WINTER        0
儿科  R50.9  发热                         0   0 14  0 1  3.400  WINTER        0
儿科  A09.9  急性胃肠炎                   0   0 14  0 1  3.200  SUMMER        0
儿科  J18.0  支气管肺炎                   0   0 14  0 1  3.000  WINTER        0
儿科  K52.9  小儿腹泻病                   0   0  7  0 1  2.600  SUMMER        0
儿科  J02.9  急性咽炎                     0   0 14  0 1  2.400  WINTER        0
儿科  B08.4  手足口病                     0   0  7  0 1  2.400  SUMMER        0
儿科  H66.9  中耳炎                       0   0 14  0 1  2.000  WINTER        0
儿科  J35.0  慢性扁桃体炎                 0   3 14  0 1  1.800  WINTER        0
儿科  L20.9  特应性皮炎                   0   0 14  0 0  1.800  SPRING_AUTUMN 0
儿科  K40.9  腹股沟疝                     1   0 14  0 1  1.200  NULL          0

妇科（11 条，性别恒为 2）
妇科  N76.0  急性阴道炎                   2  15 60  0 0  3.200  SUMMER        0
妇科  D25.9  子宫肌瘤                     2  25 55  0 1  3.200  NULL          0
妇科  N92.6  月经失调                     2  12 55  0 0  3.000  NULL          0
妇科  N72    宫颈炎                       2  18 60  0 0  2.600  NULL          0
妇科  N73.9  盆腔炎                       2  18 55  0 1  2.400  NULL          0
妇科  N83.2  卵巢囊肿                     2  15 55  0 1  2.400  NULL          0
妇科  N94.6  痛经                         2  12 50  0 0  2.200  NULL          0
妇科  N84.0  子宫内膜息肉                 2  25 55  0 1  2.000  NULL          0
妇科  N80.0  子宫腺肌病                   2  25 50  1 1  1.800  NULL          0.0300
妇科  N97.9  女性不孕症                   2  20 45  0 0  1.400  NULL          0
妇科  N85.6  宫腔粘连                     2  20 45  0 0  1.000  NULL          0

五官科（11 条）
五官科 J02.9  急性咽炎                     0   0 89  0 0  3.200  WINTER        0
五官科 J30.4  过敏性鼻炎                   0   5 89  1 0  3.000  SPRING_AUTUMN 0.1000
五官科 H25.9  老年性白内障                 0  50 89  0 1  3.000  NULL          0
五官科 J31.0  慢性鼻炎                     0   5 89  1 0  2.600  WINTER        0.0800
五官科 J35.0  慢性扁桃体炎                 0   3 60  0 1  2.400  WINTER        0
五官科 H10.9  结膜炎                       0   0 89  0 0  2.400  SUMMER        0
五官科 H66.9  中耳炎                       0   0 89  0 0  2.200  WINTER        0
五官科 J34.2  鼻中隔偏曲                   0  10 70  0 1  1.800  NULL          0
五官科 H40.9  青光眼                       0  40 89  1 1  1.600  NULL          0.0200
五官科 J33.9  鼻息肉                       0  20 70  0 1  1.600  NULL          0
五官科 H81.1  良性阵发性眩晕               0  30 80  0 0  1.400  NULL          0

皮肤科（11 条）—— **一行都不挂手术**（spec §9.2）。注意这不等于「都不可住院」：
spec §9.2 只禁止皮肤科挂手术，没有禁止皮肤科收住院。
蜂窝织炎（静脉抗感染的主要适应证）与甲沟炎（需切开引流）本就该能住院，
所以这两条 `admission_eligible=1`，其余 9 条为 0。
皮肤科 L70.0  痤疮                         0  12 35  0 0  3.400  NULL          0
皮肤科 L30.9  湿疹                         0   0 89  1 0  3.200  SPRING_AUTUMN 0.0600
皮肤科 L50.9  荨麻疹                       0   0 89  0 0  3.000  SPRING_AUTUMN 0
皮肤科 L20.9  特应性皮炎                   0   0 60  1 0  2.400  SPRING_AUTUMN 0.0300
皮肤科 B35.9  体癣                         0   5 89  0 0  2.400  SUMMER        0
皮肤科 L21.9  脂溢性皮炎                   0  15 70  0 0  2.200  NULL          0
皮肤科 B02.9  带状疱疹                     0  40 89  0 0  2.200  NULL          0
皮肤科 L08.9  皮肤感染                     0   0 89  0 0  2.000  SUMMER        0
皮肤科 L60.0  甲沟炎                       0   5 89  0 1  1.800  SUMMER        0
皮肤科 L40.9  银屑病                       0  15 70  1 0  1.600  WINTER        0.0050
皮肤科 L03.9  蜂窝织炎                     0  20 89  0 1  1.400  SUMMER        0
```

**`prevalence` 列的性质**：spec §9 的公开来源表里**没有逐病患病率**（只有就诊率与年龄性别分布）。
故上表患病率是**按常见流行病学量级给出的假设值**，须在论文中标注为「假设」并对其做敏感性分析
（与 spec §5.3 对 σ=0.5 的处理方式一致）。写入时在 SQL 行尾注释 `-- 假设值`。

- [ ] **Step 3: 逐条核对 ICD 编码（本任务不可跳过的一步）**

对 72 行**逐行**核对 ICD 编码与诊断名的对应关系，办法：

1. 打开卫健委发布的 **ICD-10 国家临床版**（或本地已有的电子版）检索每个编码；
2. 确认**编码 ↔ 名称**一致，且为 6 位以内可用的临床版编码；
3. 不一致的行**以官方版本为准修正编码**，并在下一个 Step 的 SQL 注释里记一行
   `-- 编码核对：<官方名称>`；
4. 核不完的**不得入库**——宁可先只入库核对过的，也不要把未核对的编码写进论文依据。

若手头没有官方版本，**停下来告诉作者**，不要凭记忆补全。已核对与未核对的行数须在 Step 5 一并报出。

- [ ] **Step 4: 入库并断言**

**不能整份重跑**：第 1 节的 `drug` id 13~196 已在库里，`mysql` 客户端遇到主键冲突会**中止**，
本节的语句根本执行不到。把本节**从 `SET @WINTER` 那行起**整段抽到临时文件再灌：

```bash
cd backend
# 从本节起点（SET @WINTER）截到文件末尾；行号以当时文件为准，用 grep -n 定位
grep -n 'SET @WINTER' sql/seed-ontology.sql
sed -n '<该行号>,$p' sql/seed-ontology.sql > /tmp/section3.sql
mysql --default-character-set=utf8mb4 his_demo < /tmp/section3.sql
```

```bash
mysql --default-character-set=utf8mb4 -e "
SELECT '总行数' AS chk, COUNT(*) AS v FROM his_demo.dept_diagnosis
UNION ALL SELECT '科室数', COUNT(DISTINCT department) FROM his_demo.dept_diagnosis
UNION ALL SELECT '慢病数', COUNT(*) FROM his_demo.dept_diagnosis WHERE chronic = 1
UNION ALL SELECT '可住院数', COUNT(*) FROM his_demo.dept_diagnosis WHERE admission_eligible = 1
UNION ALL SELECT 'J06.9 可住院(应0，spec §6.2 点名)', COUNT(*) FROM his_demo.dept_diagnosis
  WHERE icd_code = 'J06.9' AND admission_eligible = 1
UNION ALL SELECT '皮肤科可住院(应2：甲沟炎/蜂窝织炎)', COUNT(*) FROM his_demo.dept_diagnosis
  WHERE department = '皮肤科' AND admission_eligible = 1
UNION ALL SELECT '非法性别值(应0)', COUNT(*) FROM his_demo.dept_diagnosis WHERE gender NOT IN (0,1,2)
UNION ALL SELECT '年龄区间倒置(应0)', COUNT(*) FROM his_demo.dept_diagnosis WHERE min_age > max_age
UNION ALL SELECT '非慢病却有患病率(应0)', COUNT(*) FROM his_demo.dept_diagnosis
  WHERE chronic = 0 AND prevalence <> 0
UNION ALL SELECT '季节完整性(应33：72 行里 39 行无季节性写 NULL)', COUNT(*) FROM his_demo.dept_diagnosis
  WHERE month_weights IS NOT NULL
UNION ALL SELECT 'rule_config 的 8 个诊断未覆盖数(应0)', COUNT(*) FROM his_demo.rule_config rc
  WHERE NOT EXISTS (SELECT 1 FROM his_demo.dept_diagnosis dd WHERE dd.icd_code = rc.diagnosis_code);
SELECT department, COUNT(*) AS n FROM his_demo.dept_diagnosis GROUP BY department ORDER BY n DESC;"
```

Expected:
`总行数 72`、`科室数 6`、`慢病数 18`、`可住院数 46`、
`J06.9 可住院(应0) 0`、`皮肤科可住院(应2) 2`、`非法性别值(应0) 0`、`年龄区间倒置(应0) 0`、
`非慢病却有患病率(应0) 0`、`季节完整性(应33) 33`、`rule_config 的 8 个诊断未覆盖数(应0) 0`；

> **`季节完整性 33` 这条断言是补上的漏洞**：72 行里 39 行无季节性写 `NULL`，其余 33 行写季节数组。
> 若某次执行只抽了 INSERT 而漏掉 Step 1 的 `SET`，这 33 行会被静默写成 `NULL`——
> 原十条断言**一条都发现不了**（没有一条看季节列），而季节维度就此消失且无人察觉。
> 实测不是 33（比如是 0）就说明抽取时漏了 `SET` 行，**不要改这个期望值**，去修执行方式。

> **`可住院数 46` 是怎么来的**：72 行里 26 行 `admission_eligible=0`。
> 这个数字**必须与 SQL 输出一致**——手数容易错，而它决定了后面
> `admissionEligibleDiseasesFor` 的池子大小，进而决定住院数据的量。
> 若实测不是 46，**以实测为准改这一行**，并回头查是哪一行的标记与意图不符。
分组输出 6 行，每行 `n` 在 8~15 之间（实际为 内科 15 / 外科 13 / 儿科 11 / 妇科 11 / 五官科 11 / 皮肤科 11）。

> 最后一条断言是**承接旧数据的关键**：既有 `rule_config` 的 8 个诊断必须全部出现在新本体里，
> 否则 Task 7 换数据源后这 8 个诊断的处方会集体落入 `unchecked`。
> 注意 `部门归属`：`J06.9`/`A09.9`/`R50.9` 在本表中各有 内科 与 儿科 两行、`K40.9` 有 外科 与 儿科 两行——
> **同 ICD 跨科室是合法的**，Task 8 的科室检查取并集判定。

- [ ] **Step 5: Commit**

```bash
git add backend/sql/seed-ontology.sql
git commit -m "feat(sql): 科室↔诊断本体 72 条，覆盖 6 科室并纳入既有 8 个诊断" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

提交信息里附一行核对进度，例如
`ICD 编码核对：72/72（来源 ICD-10 国家临床版）` 或 `ICD 编码核对：60/72（余 12 条待作者确认）`。
**不得写「已核对」而实际未核。**

---

## Task 5: `diagnosis_lab` 检验本体 50 项（阶段一）

**Files:**
- Modify: `backend/sql/seed-ontology.sql`（追加第 4 节）
- Test: 无单元测试；本任务末尾是本表自洽性 SQL 断言，跨表一致性在 Task 9

> **本节开头必须加 `DELETE FROM diagnosis_lab;`**（与第 3 节同款纪律）。
> `diagnosis_lab` 在 schema.sql 里**没有唯一键**（只有 `idx_ddl_icd` / `idx_ddl_dept` 两个普通索引），
> 重跑本节不会报错，而是**静默**把约 900 行累加成 1800/2700… 行。
> 而本表是 Task 13 生成器的**检验项目池**（取代旧 `MedicalData.LAB_ITEMS`），
> 也是 Task 23 术前检查的 `required` 来源——行数翻倍会改变抽样分布而不报任何错。
> 本表没有任何外键指向它，DELETE 永远安全。
> 若本节的 SQL 由代码/脚本生成，把这条 `DELETE` 写在**生成器的输出模板里**，
> 不要只写在手工步骤的说明里。

**Interfaces:**
- Consumes: Task 4 的 `dept_diagnosis`（`(科室, ICD)` 组合必须与 Task 4 逐一对应）
- Produces: `diagnosis_lab` 约 900 行；**本表同时是检验项目池**——
  阶段二生成器从这里取项目元数据与异常方向，**取代 `MedicalData.LAB_ITEMS`**
  （现仅 5 项且与诊断无关，是「化验单与病不符」的根因）；
  `required` 列与 `abnormal_prob` 列被 Task 20 的术前检查生成读取

**为什么这张表要同时当项目池**（spec §4.3）：检验项目元数据（单位、参考范围、异常方向）
若再拆一张 `lab_item` 表，就要多维护一处「项目在两个表里说法不一致」的风险。
本项目只有 50 项、且每项的元数据在所有挂接行上都相同，故按 spec 去规范化到本表。

**先看一处必须避开的重复**：`G-PREOP` 已含 `ECG`，若某诊断同时挂 `G-PREOP*` 与 `G-ECG`，
会产生两行 `(科室, ICD, ECG)`。Step 3 的展开规则里给了合并规则，
Step 4 的断言会独立地再查一遍重复——**两处都要有**，因为断言是最后一道网，不是唯一一道。

- [ ] **Step 1: 定义 50 项检验（全表唯一来源）**

列序：**item_code / 项目名 / 单位 / 参考范围 / 异常方向 / 默认异常率**。
「默认异常率」是**一般人群基础异常率**，非该病异常率；诊断针对性的值在 Step 3 里覆盖。
`参考范围` 列直接写进 `ref_range`（无单位的定性项目写 `—`）。

```
血常规与炎症
BLD-WBC   白细胞计数            10^9/L        4.0-10.0     HIGH  0.100
BLD-RBC   红细胞计数            10^12/L       4.0-5.5      LOW   0.080
BLD-HB    血红蛋白              g/L           115-150      LOW   0.120
BLD-PLT   血小板计数            10^9/L        100-300      LOW   0.070
BLD-NEUT  中性粒细胞百分比        %             40-75        HIGH  0.100
BLD-LYMPH 淋巴细胞百分比          %             20-50        HIGH  0.100
BLD-EOS   嗜酸性粒细胞百分比      %             0.4-8        HIGH  0.060
CRP       C反应蛋白             mg/L          0-8          HIGH  0.100
ESR       红细胞沉降率           mm/h          0-20         HIGH  0.080
PCT       降钙素原              ng/mL         0-0.5        HIGH  0.050

凝血功能
PT        凝血酶原时间           s             11-14        HIGH  0.060
APTT      活化部分凝血活酶时间     s             28-45        HIGH  0.060
INR       国际标准化比值          —             0.8-1.2      HIGH  0.050
FIB       纤维蛋白原             g/L           2.0-4.0      NONE  0.060
D-DIMER   D-二聚体              mg/L          0-0.5        HIGH  0.100

感染筛查
HBSAG     乙肝表面抗原            —             阴性          HIGH  0.080
HCVAB     丙肝抗体               —             阴性          HIGH  0.020
TPAB      梅毒螺旋体抗体          —             阴性          HIGH  0.010
HIVAB     HIV抗体               —             阴性          HIGH  0.005

血糖
GLU-F     空腹血糖               mmol/L        3.9-6.1      HIGH  0.100
GLU-2H    餐后2小时血糖           mmol/L        3.9-7.8      HIGH  0.120
HBA1C     糖化血红蛋白            %             4.0-6.0      HIGH  0.100

血脂
TC        总胆固醇               mmol/L        0-5.2        HIGH  0.200
TG        甘油三酯               mmol/L        0-1.7        HIGH  0.220
LDL-C     低密度脂蛋白胆固醇       mmol/L        0-3.4        HIGH  0.180
HDL-C     高密度脂蛋白胆固醇       mmol/L        1.0-1.6      LOW   0.150

肝肾功能
ALT       丙氨酸氨基转移酶         U/L           0-40         HIGH  0.120
AST       天冬氨酸氨基转移酶       U/L           0-40         HIGH  0.100
TBIL      总胆红素               μmol/L        3.4-17.1     HIGH  0.080
ALB       白蛋白                 g/L           40-55        LOW   0.080
CREA      肌酐                  μmol/L        44-133       HIGH  0.080
BUN       尿素氮                 mmol/L        2.9-8.2      HIGH  0.080
EGFR      估算肾小球滤过率         mL/min/1.73m² 90-120       LOW   0.080

电解质
K         血钾                  mmol/L        3.5-5.5      NONE  0.060
NA        血钠                  mmol/L        137-147      NONE  0.050
CA        血钙                  mmol/L        2.1-2.6      NONE  0.060

甲状腺
TSH       促甲状腺激素            mIU/L         0.27-4.2     HIGH  0.100
FT3       游离三碘甲状腺原氨酸      pmol/L        3.1-6.8      NONE  0.060
FT4       游离甲状腺素            pmol/L        12-22        NONE  0.060

尿便
URINE-WBC 尿白细胞              /HPF          0-5          HIGH  0.100
URINE-PRO 尿蛋白                g/L           0-0.15       HIGH  0.080
URINE-GLU 尿糖                  mmol/L        0            HIGH  0.060
STOOL-RT  便常规                /HPF          0-3          HIGH  0.100
STOOL-OB  便隐血                —             阴性          HIGH  0.080

微生物
MP-AB     肺炎支原体抗体          —             阴性          HIGH  0.050

影像与功能（spec §9.2 明确把心电图归入检验项目）
ECG       心电图                —             正常          HIGH  0.150
CXRAY     胸部X线               —             正常          HIGH  0.150
US-ABD    腹部超声              —             正常          HIGH  0.150
US-GYN    妇科超声              —             正常          HIGH  0.150
US-THY    甲状腺超声             —             正常          HIGH  0.150
```

合计 **50 项**（10+5+4+3+4+7+3+3+5+1+5），落在 spec §9.2 的 30~50 项区间上限。

- [ ] **Step 2: 定义检验项目组**

```sql
-- 检验项目组：仅用于本文件的书写便利，**不入库**。展开规则见 Step 3。
-- G-PREOP 是 spec §9.2 点名的术前检查组，含 7+5+4+1 = 17 项
--   G-CBC   = BLD-WBC, BLD-RBC, BLD-HB, BLD-PLT, BLD-NEUT, BLD-LYMPH, BLD-EOS
--             （`BLD-EOS` 必须在组内：下面 6 处 `G-CBC(BLD-EOS=…)` 覆盖写的就是它。
--              按展开规则 3「只覆盖本组内列出的项」，它若不在组里，这些覆盖会被静默丢弃，
--              而 Step 1 的 50 项里有几项本来就不进任何组，`labItemCountInRange` 的
--              30~50 区间断言照样通过——「嗜酸粒细胞参与过敏性疾病检验」的临床意图
--              就这么没了，且没有任何断言会发现。）
--   G-INFLAM= CRP, ESR, PCT
--   G-COAG  = PT, APTT, INR, FIB, D-DIMER
--   G-INFECT= HBSAG, HCVAB, TPAB, HIVAB
--   G-PREOP = G-CBC + G-COAG + G-INFECT + ECG
--   G-GLU   = GLU-F, HBA1C
--   G-GLU2  = GLU-F, GLU-2H, HBA1C
--   G-LIPID = TC, TG, LDL-C, HDL-C
--   G-LIVER = ALT, AST, TBIL, ALB
--   G-RENAL = CREA, BUN, EGFR, URINE-PRO
--   G-URINE = URINE-WBC, URINE-PRO, URINE-GLU
--   G-STOOL = STOOL-RT, STOOL-OB
--   G-THYROID = TSH, FT3, FT4
--   G-THYUS = TSH, FT3, FT4, US-THY
--   G-ECG   = ECG
--   G-CXRAY = CXRAY
--   G-USABD = US-ABD
--   G-USGYN = US-GYN
--   G-ELECT = K, NA, CA
--   G-MPAB  = MP-AB
```

- [ ] **Step 3: 按 (科室, 诊断) 展开为行**

**展开规则**（照此实现，不要自由发挥）：

1. 每个条目形如 `科室 ICD: 组[*][(项=概率,…)] …`；
2. 组不带 `*` → 该组展开出的每行 `required = 0`；带 `*` → `required = 1`；
3. `(项=概率,…)` 只覆盖**本组内**列出的项，其余项用 Step 1 的默认异常率；
4. `weight` 一律 `1.000`（本期不区分项目权重，留列待用）；
5. `department` 一律写该条目的科室名（**不写 NULL**——本表的每一行都对应 Task 4 的某个
   `(科室, ICD)` 组合，故必然有具体科室）；
6. **同一 (科室, ICD, item_code) 被多个组覆盖时合并成一行**：
   `required` 取 `1`（`required` 是「必查」，只要任一组要求必查就是必查）；
   `abnormal_prob` 取两者中的**较大值**（同一诊断下更可能异常的那个来源更贴合该病）；
   其余元数据取自 Step 1，不存在分歧。

```
内科
I10:    G-CBC, G-LIPID, G-RENAL, G-GLU, G-ELECT, G-ECG*
E11.9:  G-GLU2*, G-LIPID, G-RENAL, G-LIVER, G-ELECT
K29.5:  G-CBC, G-STOOL, G-LIVER, G-USABD
E78.5:  G-LIPID*, G-LIVER, G-GLU, G-ECG
I25.1:  G-CBC, G-LIPID(LDL-C=0.600), G-GLU, G-ECG*(ECG=0.550), G-CXRAY, G-RENAL
K21.0:  G-CBC, G-USABD, G-ECG
J44.9:  G-CBC, G-CXRAY(CXRAY=0.800), G-ECG, G-INFLAM(CRP=0.450)
N18.9:  G-RENAL*(CREA=0.850,EGFR=0.800,URINE-PRO=0.700), G-CBC(BLD-HB=0.400), G-ELECT(K=0.350), G-USABD
I48:    G-ECG*(ECG=0.950), G-CBC, G-THYROID(TSH=0.200), G-COAG
E05.9:  G-THYROID*(TSH=0.900,FT3=0.850,FT4=0.850), G-CBC(BLD-WBC=0.250), G-LIVER
J06.9:  G-CBC(BLD-WBC=0.450,BLD-LYMPH=0.500), G-INFLAM(CRP=0.400)
J20.9:  G-CBC(BLD-WBC=0.500), G-INFLAM(CRP=0.500), G-CXRAY(CXRAY=0.350)
A09.9:  G-CBC(BLD-WBC=0.400), G-STOOL(STOOL-RT=0.600), G-ELECT(K=0.250), G-URINE
R50.9:  G-CBC(BLD-WBC=0.550), G-INFLAM(CRP=0.550,PCT=0.350), G-URINE, G-CXRAY
G43.9:  G-CBC, G-ECG

外科（全部含 G-PREOP*，即 spec §9.2 要求的术前检查组，全部 required=1）
K35.8:  G-PREOP*, G-CBC(BLD-WBC=0.850,BLD-NEUT=0.800), G-INFLAM(CRP=0.750,PCT=0.400), G-USABD(US-ABD=0.650)
K80.1:  G-PREOP*, G-LIVER(TBIL=0.450,ALT=0.400), G-USABD*(US-ABD=0.900), G-INFLAM(CRP=0.550)
S52.5:  G-PREOP*, G-CBC(BLD-HB=0.300), G-ELECT(CA=0.200)
E04.1:  G-PREOP*, G-THYUS*(US-THY=0.950,TSH=0.250), G-ELECT(CA=0.150)
M17.9:  G-PREOP*, G-INFLAM(CRP=0.350,ESR=0.400), G-ELECT(CA=0.150)
I84.2:  G-PREOP*, G-CBC(BLD-HB=0.450)
S61.0:  G-PREOP*, G-CBC(BLD-WBC=0.450), G-INFLAM(CRP=0.400)
K40.9:  G-PREOP*, G-USABD(US-ABD=0.300)
L02.9:  G-PREOP*, G-CBC(BLD-WBC=0.750), G-INFLAM(CRP=0.600)
D23.9:  G-PREOP*
K63.5:  G-PREOP*, G-CBC(BLD-HB=0.400), G-STOOL(STOOL-OB=0.550)
K42.9:  G-PREOP*, G-USABD(US-ABD=0.300)
C73:    G-PREOP*, G-THYUS*(US-THY=0.950,TSH=0.200), G-ELECT(CA=0.300), G-LIVER

儿科（department='儿科'，与内科同 ICD 但独立成行，异常率按儿童调整）
J06.9:  G-CBC(BLD-WBC=0.380,BLD-LYMPH=0.580), G-INFLAM(CRP=0.320)
R50.9:  G-CBC(BLD-WBC=0.600), G-INFLAM(CRP=0.580,PCT=0.420), G-URINE, G-CXRAY(CXRAY=0.300)
A09.9:  G-CBC(BLD-WBC=0.350), G-STOOL(STOOL-RT=0.650), G-ELECT(K=0.300), G-URINE
J18.0:  G-CBC(BLD-WBC=0.800,BLD-NEUT=0.750), G-INFLAM(CRP=0.700,PCT=0.450), G-CXRAY*(CXRAY=0.850), G-MPAB(MP-AB=0.350)
K52.9:  G-CBC(BLD-WBC=0.300), G-STOOL(STOOL-RT=0.550), G-ELECT(K=0.350), G-URINE
J02.9:  G-CBC(BLD-WBC=0.450), G-INFLAM(CRP=0.400)
B08.4:  G-CBC(BLD-WBC=0.500,BLD-LYMPH=0.450), G-INFLAM(CRP=0.350)
H66.9:  G-CBC(BLD-WBC=0.400), G-INFLAM(CRP=0.300)
J35.0:  G-CBC(BLD-WBC=0.350), G-INFLAM(CRP=0.300,ESR=0.250)
L20.9:  G-CBC(BLD-EOS=0.450)
K40.9:  G-CBC(BLD-WBC=0.200)

妇科（department='妇科'；需手术的诊断挂 G-PREOP*）
N76.0:  G-CBC(BLD-WBC=0.350), G-URINE(URINE-WBC=0.600), G-USGYN*(US-GYN=0.450)
D25.9:  G-PREOP*, G-USGYN*(US-GYN=0.950)
N92.6:  G-CBC(BLD-HB=0.450), G-THYROID(TSH=0.250), G-USGYN(US-GYN=0.400), G-COAG
N72:    G-CBC(BLD-WBC=0.350), G-URINE(URINE-WBC=0.400), G-USGYN(US-GYN=0.300)
N73.9:  G-CBC(BLD-WBC=0.600), G-INFLAM(CRP=0.550,ESR=0.500), G-URINE(URINE-WBC=0.500), G-USGYN(US-GYN=0.550)
N83.2:  G-PREOP*, G-USGYN*(US-GYN=0.900)
N94.6:  G-CBC, G-USGYN(US-GYN=0.350)
N84.0:  G-PREOP*, G-USGYN*(US-GYN=0.850)
N80.0:  G-PREOP*, G-USGYN*(US-GYN=0.850)
N97.9:  G-CBC, G-THYROID(TSH=0.200), G-USGYN(US-GYN=0.600)
N85.6:  G-PREOP*, G-USGYN*(US-GYN=0.900)

五官科
J02.9:  G-CBC(BLD-WBC=0.450), G-INFLAM(CRP=0.400)
J30.4:  G-CBC(BLD-EOS=0.450), G-INFLAM(ESR=0.200)
H25.9:  G-PREOP*
J31.0:  G-CBC(BLD-WBC=0.300), G-INFLAM(CRP=0.250)
J35.0:  G-PREOP*, G-INFLAM(CRP=0.350,ESR=0.300)
H66.9:  G-CBC(BLD-WBC=0.450), G-INFLAM(CRP=0.400)
H10.9:  G-CBC(BLD-WBC=0.300), G-INFLAM(CRP=0.250)
J34.2:  G-PREOP*
H40.9:  G-CBC, G-GLU, G-ECG
J33.9:  G-PREOP*
H81.1:  G-CBC, G-ECG

皮肤科（全部不挂 G-PREOP——spec §9.2 规定皮肤科不挂手术）
L70.0:  G-CBC, G-INFLAM(CRP=0.200)
L30.9:  G-CBC(BLD-EOS=0.400), G-INFLAM(ESR=0.200,CRP=0.300)
L50.9:  G-CBC(BLD-EOS=0.350), G-INFLAM(CRP=0.300,ESR=0.250)
L20.9:  G-CBC(BLD-EOS=0.500)
B35.9:  G-CBC(BLD-EOS=0.250), G-LIVER(ALT=0.150)
L21.9:  G-CBC, G-INFLAM(CRP=0.200)
B02.9:  G-CBC(BLD-LYMPH=0.350), G-INFLAM(CRP=0.300)
L08.9:  G-CBC(BLD-WBC=0.600), G-INFLAM(CRP=0.550,PCT=0.300)
L60.0:  G-CBC(BLD-WBC=0.450), G-INFLAM(CRP=0.350), G-COAG
L40.9:  G-CBC, G-LIVER(ALT=0.300,AST=0.250), G-LIPID, G-ECG
L03.9:  G-CBC(BLD-WBC=0.750), G-INFLAM(CRP=0.650,PCT=0.350), G-GLU
```

- [ ] **Step 4: 入库并断言（含重复检测）**

```bash
cd backend
mysql -uroot -p123456 --default-character-set=utf8mb4 his_demo < sql/seed-ontology.sql
```

```bash
mysql -uroot -p123456 --default-character-set=utf8mb4 -e "
SELECT '总行数' AS chk, COUNT(*) AS v FROM his_demo.diagnosis_lab
UNION ALL SELECT '不同检验项目数(应 50)', COUNT(DISTINCT item_code) FROM his_demo.diagnosis_lab
UNION ALL SELECT '重复行(应 0)', COUNT(*) FROM (
  SELECT icd_code, department, item_code FROM his_demo.diagnosis_lab
  GROUP BY icd_code, department, item_code HAVING COUNT(*) > 1) z
UNION ALL SELECT '非法异常方向(应 0)', COUNT(*) FROM his_demo.diagnosis_lab
  WHERE abnormal_direction NOT IN ('HIGH','LOW','NONE')
UNION ALL SELECT '异常率越界(应 0)', COUNT(*) FROM his_demo.diagnosis_lab
  WHERE abnormal_prob < 0 OR abnormal_prob > 1
UNION ALL SELECT '部门为空(应 0)', COUNT(*) FROM his_demo.diagnosis_lab WHERE department IS NULL
UNION ALL SELECT '(科室,ICD) 不在 dept_diagnosis 中的组合数(应 0)', COUNT(*) FROM (
  SELECT DISTINCT department, icd_code FROM his_demo.diagnosis_lab) x
  WHERE NOT EXISTS (SELECT 1 FROM his_demo.dept_diagnosis dd
    WHERE dd.department = x.department AND dd.icd_code = x.icd_code)
UNION ALL SELECT 'dept_diagnosis 中未被挂接检验的诊断数(应 0)', COUNT(*)
  FROM his_demo.dept_diagnosis dd WHERE NOT EXISTS (
    SELECT 1 FROM his_demo.diagnosis_lab dl
    WHERE dl.icd_code = dd.icd_code AND dl.department = dd.department);
SELECT item_code, COUNT(*) c FROM his_demo.diagnosis_lab GROUP BY item_code ORDER BY c LIMIT 5;"
```

Expected: `总行数` 约 900（容差 ±20%，写入实际值即可，**不得填估算值**）、
`不同检验项目数 50`、其余四条 `0`、两条交叉检查 `0`。
最后一条列出被挂接最多的 5 个项目，用于人工确认没出现「某项目被挂了 700 次」这类爆炸。

**spec §9.2 的术前检查要求必须单独验一遍**（这条是阶段四手术域的前提）：

```bash
mysql -uroot -p123456 --default-character-set=utf8mb4 -e "
SELECT dl.department, dd.icd_code,
       SUM(dl.item_code IN ('BLD-WBC','BLD-HB','BLD-PLT')) AS 血常规,
       SUM(dl.item_code IN ('PT','APTT','INR'))             AS 凝血,
       SUM(dl.item_code IN ('HBSAG','HCVAB','TPAB','HIVAB')) AS 感染筛查,
       SUM(dl.item_code = 'ECG')                            AS 心电图
FROM his_demo.dept_diagnosis dd
JOIN his_demo.diagnosis_lab dl
  ON dl.icd_code = dd.icd_code AND dl.department = dd.department
WHERE dd.department = '外科'
GROUP BY dl.department, dd.icd_code
HAVING 血常规 = 0 OR 凝血 = 0 OR 感染筛查 = 0 OR 心电图 = 0;"
```

Expected: **零行**。任何一行输出都表示该外科诊断缺术前检查项，须补 `G-PREOP*`。

- [ ] **Step 5: Commit**

```bash
git add backend/sql/seed-ontology.sql
git commit -m "feat(sql): 检验本体 50 项，外科诊断全部挂术前检查组" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 6: `diagnosis_operation` 手术本体 20 术式（阶段一）

**Files:**
- Modify: `backend/sql/seed-ontology.sql`（追加第 5 节）
- Test: 无单元测试；本任务末尾是本表自洽性 SQL 断言，跨表一致性在 Task 9

> **本节开头必须加 `DELETE FROM diagnosis_operation;`**（与第 3 节同款纪律）。
> `diagnosis_operation` 在 schema.sql 里**没有唯一键**（只有 `idx_dop_icd` / `idx_dop_dept` /
> `idx_dop_code` 三个普通索引），重跑本节不报错而是**静默**把 22 行累加成 44/66… 行。
> 而 `probability` 是「该诊断需手术的概率」——同一诊断出现两行会**静默放大手术量**，
> 直接污染 Task 23~26 的手术域全部指标。本表无外键指向它，DELETE 永远安全。

**Interfaces:**
- Consumes: Task 4 的 `dept_diagnosis`（`(科室, ICD)` 必须与 Task 4 逐一对应）
- Produces: `diagnosis_operation` **22 行**、20 个不同术式；被 Task 23 的手术生成器读取
  （`probability` 决定是否手术、`operation_level` 决定术者与麻醉、`min_postop_days` 决定出院下限）

**这一列 `department` 在本表上不是锦上添花，是唯一能表达 spec 明文要求的手段。**
spec §9.2 要求「儿科（腹股沟疝）挂手术，且**概率远低于成人**」。
`K40.9` 在 外科 与 儿科 都有（Task 4 已入库），若按 spec §4.7 只以 `icd_code` 建键，
儿科与成人只能共用一行 `probability`——要么儿科被当成成人（错），
要么成人被当成儿科（更错）。下表中 外科 的 `K40.9` 概率 `0.900`、
儿科 的 `K40.9` 概率 `0.060`，**同一 ICD 两行、概率差 15 倍**，这就是该列存在的理由。

**编码同样必须逐条核对**：手术编码用 **ICD-9-CM-3**（国家临床版）。
核对办法与 ICD-10 相同（Task 4 Step 3）：打开官方版本逐条比对**编码 ↔ 术式名**，
不一致以官方为准，核不完的不入库。**下表术式名与编码的对应关系是我方起草的候选，不是权威结论。**

- [ ] **Step 1: 定 20 个术式与手术级别**

`min_postop_days` 按 spec §9.2 的级别映射取值：一级 1~2、二级 3~5、三级 5~7、四级 7~10。

```
一级手术（min_postop_days 1~2）
86.3     体表肿物切除术                          1
86.04    皮肤脓肿切开引流术                       1
86.22    开放性伤口清创缝合术                     2

二级手术（min_postop_days 3~5）
47.01    腹腔镜阑尾切除术                        3
47.09    阑尾切除术                             4
53.02    腹股沟疝无张力修补术                     3
53.41    脐疝修补术                             3
49.46    痔切除术                               3
45.42    内镜下结肠息肉切除术                     2

三级手术（min_postop_days 5~7）
51.23    腹腔镜胆囊切除术                        5
79.32    桡骨骨折切开复位内固定术                 6
68.29    子宫肌瘤剔除术                          5
65.29    卵巢囊肿剥除术                          5
13.41    白内障超声乳化+人工晶体植入术             2
28.2     扁桃体切除术                            4
21.5     鼻中隔矫正术                            3
21.31    鼻息肉切除术                            3

四级手术（min_postop_days 7~10）
06.39    甲状腺次全切除术                        7
06.4     甲状腺癌根治术                          9
81.54    人工膝关节置换术                        9
```

合计 **20 个术式**（一级 3 / 二级 6 / 三级 8 / 四级 3），落在 spec §9.2 的 15~25 区间。

> **一级/二级手术的 `min_postop_days` 小于所属级别的名义下限**是有意的：
> spec 给的是**区间**（一级 1~2、二级 3~5、三级 5~7），上表取区间内的具体值。
> 例：白内障手术取 2 天（三级区间 5~7 的下限之下）——这是**实际临床路径的常见值**，
> 但**与 spec 的级别映射不一致**。故本行在 SQL 注释里标 `-- 实际路径值，低于三级名义下限`，
> 并在论文中说明该偏离；**不要在论文里声称「严格按级别映射」**。
> 若作者希望严格一致，把 `13.41` 改为 `5` 即可（一个数字）。

- [ ] **Step 2: 写入 22 行挂接**

列序：**科室 / ICD / operation_code / 术式名 / 级别 / probability / min_postop_days / weight**。
`weight` 是同诊断多术式时的相对权重，本期一律 `1.000`（`K35.8` 除外，见下）。

```
外科
外科  K35.8  47.01  腹腔镜阑尾切除术              2  0.850  3  3.000
外科  K35.8  47.09  阑尾切除术                   2  0.150  4  1.000
外科  K80.1  51.23  腹腔镜胆囊切除术              3  0.950  5  1.000
外科  S52.5  79.32  桡骨骨折切开复位内固定术       3  0.900  6  1.000
外科  E04.1  06.39  甲状腺次全切除术              4  0.450  7  1.000
外科  C73    06.4   甲状腺癌根治术                4  0.950  9  1.000
外科  M17.9  81.54  人工膝关节置换术              4  0.350  9  1.000
外科  I84.2  49.46  痔切除术                     2  0.600  3  1.000
外科  S61.0  86.22  开放性伤口清创缝合术           1  0.950  2  1.000
外科  K40.9  53.02  腹股沟疝无张力修补术           2  0.900  3  1.000
外科  L02.9  86.04  皮肤脓肿切开引流术             1  0.850  1  1.000
外科  D23.9  86.3   体表肿物切除术                1  0.950  1  1.000
外科  K63.5  45.42  内镜下结肠息肉切除术           2  0.900  2  1.000
外科  K42.9  53.41  脐疝修补术                   2  0.850  3  1.000

儿科（与外科同 ICD、不同科室：这是 department 列的核心用例）
儿科  K40.9  53.02  腹股沟疝无张力修补术           2  0.060  3  1.000
儿科  J35.0  28.2   扁桃体切除术                  3  0.300  4  1.000

妇科
妇科  D25.9  68.29  子宫肌瘤剔除术                3  0.700  5  1.000
妇科  N83.2  65.29  卵巢囊肿剥除术                3  0.800  5  1.000

五官科
五官科 H25.9 13.41  白内障超声乳化+人工晶体植入术   3  0.950  2  1.000
五官科 J35.0 28.2   扁桃体切除术                  3  0.350  4  1.000
五官科 J34.2 21.5   鼻中隔矫正术                  3  0.750  3  1.000
五官科 J33.9 21.31  鼻息肉切除术                  3  0.850  3  1.000
```

**`probability` 的语义**：某次该诊断的住院**需要手术的概率**。
`K35.8` 的两行同为阑尾炎术式，故 **同一 (科室, ICD) 下所有行的 `probability` 之和必须 ≤ 1**，
生成器按 `weight` 在「需手术的那部分」里分配——`K35.8` 的 0.850 + 0.150 = 1.000 ✓。
其余诊断各只有一行，故其 `probability` 就是手术率本身。

**`probability` 的上表数值同样是起草值**：spec §9 的来源表里**没有手术率**这一项
（临床路径给的是「是否手术/术式」，不是概率）。故这些概率是**假设值**，
须在论文中标注为假设并做敏感性分析，写入时行尾加 `-- 假设值`。

- [ ] **Step 3: 逐条核对 ICD-9-CM-3 编码**

对 20 行**逐行**核对「编码 ↔ 术式名」。办法同 Task 4 Step 3。
**特别注意 `68.29`**：该编码在 ICD-9-CM-3 里是「子宫病损的其他切除术」，
子宫肌瘤剔除术归入此编码是否恰当需以官方版本确认；若官方版本另有更精确的编码，以官方为准。
核不完的不得入库，并在 Step 5 报出核对进度。

- [ ] **Step 4: 入库并断言**

```bash
cd backend
mysql -uroot -p123456 --default-character-set=utf8mb4 his_demo < sql/seed-ontology.sql
```

```bash
mysql -uroot -p123456 --default-character-set=utf8mb4 -e "
SELECT '总行数(应 22)' AS chk, COUNT(*) AS v FROM his_demo.diagnosis_operation
UNION ALL SELECT '不同术式数(应 20)', COUNT(DISTINCT operation_code) FROM his_demo.diagnosis_operation
UNION ALL SELECT '部门为空(应 0)', COUNT(*) FROM his_demo.diagnosis_operation WHERE department IS NULL
UNION ALL SELECT '级别越界(应 0)', COUNT(*) FROM his_demo.diagnosis_operation
  WHERE operation_level NOT BETWEEN 1 AND 4
UNION ALL SELECT '概率越界(应 0)', COUNT(*) FROM his_demo.diagnosis_operation
  WHERE probability < 0 OR probability > 1
UNION ALL SELECT '同组概率和 >1 的组合数(应 0)', COUNT(*) FROM (
  SELECT department, icd_code, SUM(probability) s FROM his_demo.diagnosis_operation
  GROUP BY department, icd_code HAVING s > 1.0000001) z
UNION ALL SELECT '内科挂了手术的诊断数(应 0)', COUNT(*) FROM his_demo.diagnosis_operation
  WHERE department = '内科'
UNION ALL SELECT '皮肤科挂了手术的诊断数(应 0)', COUNT(*) FROM his_demo.diagnosis_operation
  WHERE department = '皮肤科'
UNION ALL SELECT 'dept_diagnosis 中不存在的组合数(应 0)', COUNT(*) FROM (
  SELECT DISTINCT department, icd_code FROM his_demo.diagnosis_operation) x
  WHERE NOT EXISTS (SELECT 1 FROM his_demo.dept_diagnosis dd
    WHERE dd.department = x.department AND dd.icd_code = x.icd_code)
UNION ALL SELECT 'dept_diagnosis 里 admission_eligible=0 却挂了手术的组合数(应 0)', COUNT(*) FROM (
  SELECT DISTINCT department, icd_code FROM his_demo.diagnosis_operation) o
  JOIN his_demo.dept_diagnosis dd
    ON dd.department = o.department AND dd.icd_code = o.icd_code
  WHERE dd.admission_eligible = 0;
SELECT department, icd_code, operation_level, min_postop_days FROM his_demo.diagnosis_operation
ORDER BY department, icd_code;"
```

Expected: `总行数 22`、`不同术式数 20`；其余八条 `0`。
最后一条列出全部 22 行——人工扫一眼就能看出「内科/皮肤科混进来了」或「级别与术后天数错配」。

**单独验一次 spec §9.2 的儿科要求**（这条是 `department` 列的存在理由，必须能证明它生效了）：

```bash
mysql -uroot -p123456 --default-character-set=utf8mb4 -e "
SELECT department, icd_code, probability FROM his_demo.diagnosis_operation
WHERE icd_code = 'K40.9' AND operation_code = '53.02';"
```

Expected: **两行**，`外科 0.900` 与 `儿科 0.060`。
若只有一行，说明 `department` 列没生效、两科室被合并了——回 Step 2 检查是否漏填科室。

- [ ] **Step 5: Commit**

```bash
git add backend/sql/seed-ontology.sql
git commit -m "feat(sql): 手术本体 20 术式，按科室区分成人与儿科手术概率" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

提交信息附一行核对进度，如 `ICD-9-CM-3 核对：20/20` 或 `18/20（68.29、21.5 待作者确认）`。
**不得写「已核对」而实际未核。**

---

## Task 7: `diagnosis_drug` 用药本体（阶段一）

**Files:**
- Modify: `backend/sql/seed-ontology.sql`（追加第 6 节）
- Test: 无单元测试；本任务末尾是本表自洽性 SQL 断言，跨表一致性在 Task 9

> **本节开头必须加 `DELETE FROM diagnosis_drug;`**（与第 3 节同款纪律）。
> `diagnosis_drug` 在 schema.sql 里**没有唯一键**（只有 `idx_ddg_icd` / `idx_ddg_dept` /
> `idx_ddg_drug` 三个普通索引），重跑本节不报错而是**静默**把约 360 行累加成 720/1080… 行。
> 而本表是 Task 10 的 `RationalDrugValidator` 的**适应症允许集**、阶段二生成器的**处方药品池**——
> 同一 `(icd_code, drug_id)` 出现两行会静默把该药权重翻倍，进而扭曲处方分布。
> 本表无外键指向它，DELETE 永远安全。

**Interfaces:**
- Consumes: Task 3 的 196 种药（`drug_id` 必须存在）、Task 4 的 `(科室, ICD)` 组合
- Produces: `diagnosis_drug` 约 360 行；被 Task 10 的 `RationalDrugValidator` 当作**适应症允许集**，
  被阶段二生成器当作**处方药品池**

**这张表是「肠胃炎用布洛芬」的正解，而且它与旧代码的差别不是「更准」而是「结构不同」。**
旧代码里诊断与药品之间**没有边**——`DataGeneratorService` 把整张 `drug` 表 `selectList(null)`
读进来随机挑，`rule_config` 只在**校验**时把掺进来的药判为违规（这正是 `VIOLATION_RATE=0.03` 的来源）。
本表把「诊断 → 可用药」变成一条**存在的边**：生成器只在边内挑，
于是「开错药」从「事后被查出 3%」变成「**结构上不可能**」。
Task 8 会删掉注入机制，Task 12 会让生成器只读本表。

**`line` 的语义**：`1` 一线 / `2` 二线 / `3` 辅助用药。
生成器按「优先一线、必要时退二线/辅助」抽取，故**一线必须有**，
且**同一诊断同一线内至少一个候选**（Task 8 的测试会卡这条）。

**已有 8 个诊断的映射必须原样保留**（下表已含）：`rule_config` 里那 8 条是本项目既有的、
已被作者看过的映射，本表是它的**超集**。保留它们有两个作用：
① 语义上，旧的合理用药率口径能延续；② 机制上，Task 9 换数据源后，
这 8 个诊断的处方不会整批落进 `unchecked`。

- [ ] **Step 1: 写入 72 组用药映射**

列序：**科室 / ICD : 一线 / 二线 / 辅助**。每项写作 `id 药名(权重)`，
行尾 `P` 表示 `postop=1`（可作术后用药——只在外科类诊断的**术后**阶段使用）。
`weight` 是同线内相对权重。

```
内科
内科 I10:    4 硝苯地平控释片(1.0) 10 苯磺酸氨氯地平片(1.0) 37 缬沙坦胶囊(0.9) 39 厄贝沙坦片(0.8) 40 培哚普利叔丁胺片(0.8)
           / 41 卡托普利片(0.5) 43 美托洛尔缓释片(0.6) 47 呋塞米片(0.4) 48 氢氯噻嗪片(0.4) 50 吲达帕胺缓释片(0.4)
           / 9 阿司匹林肠溶片(0.6) 53 瑞舒伐他汀钙片(0.5)
内科 E11.9:  5 二甲双胍片(1.0) 111 阿卡波糖片(0.7) 116 达格列净片(0.6)
           / 11 格列美脲片(0.8) 113 瑞格列奈片(0.5) 115 西格列汀片(0.5) 118 吡格列酮片(0.3)
           / 112 伏格列波糖片(0.4)
内科 K29.5:  6 奥美拉唑肠溶胶囊(1.0) 64 雷贝拉唑钠肠溶片(0.8) 66 泮托拉唑钠肠溶片(0.7)
           / 63 铝碳酸镁咀嚼片(0.9) 67 法莫替丁片(0.5) 68 枸橼酸铋钾胶囊(0.6)
           / 73 双歧杆菌三联活菌胶囊(0.5) 69 多潘立酮片(0.4)
内科 E78.5:  54 阿托伐他汀钙片(1.0) 53 瑞舒伐他汀钙片(0.9) 55 辛伐他汀片(0.6)
           / 56 非诺贝特胶囊(0.4)
内科 I25.1:  9 阿司匹林肠溶片(1.0) 54 阿托伐他汀钙片(1.0) 43 美托洛尔缓释片(0.8)
           / 52 单硝酸异山梨酯缓释片(0.7) 58 氯吡格雷片(0.6) 44 比索洛尔片(0.5)
           / 51 硝酸甘油片(0.5) 182 复方丹参滴丸(0.4)
内科 K21.0:  65 兰索拉唑肠溶片(1.0) 64 雷贝拉唑钠肠溶片(0.9) 6 奥美拉唑肠溶胶囊(0.8)
           / 70 莫沙必利片(0.7) 69 多潘立酮片(0.6)
           / 63 铝碳酸镁咀嚼片(0.5)
内科 J44.9:  87 噻托溴铵粉雾剂(1.0) 86 布地奈德福莫特罗粉吸入剂(0.9) 81 氨溴索口服溶液(0.8)
           / 89 茶碱缓释片(0.5) 82 乙酰半胱氨酸泡腾片(0.6) 85 沙丁胺醇气雾剂(0.5)
           / 88 孟鲁司特钠片(0.4)
内科 N18.9:  143 尿毒清颗粒(1.0) 39 厄贝沙坦片(0.7) 47 呋塞米片(0.6)
           / 176 碳酸钙D3片(0.5) 144 碳酸氢钠片(0.5)
           / 181 血塞通片(0.3)
内科 I48:    57 华法林钠片(1.0) 60 利伐沙班片(0.9) 44 比索洛尔片(0.8)
           / 61 地高辛片(0.5) 62 胺碘酮片(0.4)
内科 E05.9:  121 甲巯咪唑片(1.0)
           / 122 丙硫氧嘧啶片(0.6)
           / 46 普萘洛尔片(0.7) 44 比索洛尔片(0.5)
内科 J06.9:  2 感冒灵颗粒(1.0) 1 阿莫西林胶囊(0.6)
           / 97 对乙酰氨基酚片(0.7) 185 连花清瘟胶囊(0.6)
           / 171 维生素C片(0.5) 94 复方甘草片(0.4)
内科 J20.9:  12 复方甘草口服液(1.0) 8 左氧氟沙星片(0.7)
           / 81 氨溴索口服溶液(0.8) 15 阿奇霉素分散片(0.6)
           / 84 复方甲氧那明胶囊(0.5) 96 苏黄止咳胶囊(0.4)
内科 A09.9:  7 蒙脱石散(1.0) 6 奥美拉唑肠溶胶囊(0.7)
           / 179 口服补液盐III(0.9) 73 双歧杆菌三联活菌胶囊(0.6)
           / 71 消旋山莨菪碱片(0.4) 184 藿香正气口服液(0.5)
内科 R50.9:  2 感冒灵颗粒(1.0) 3 布洛芬缓释胶囊(0.9)
           / 97 对乙酰氨基酚片(0.8)
           / 171 维生素C片(0.4)
内科 G43.9:  3 布洛芬缓释胶囊(1.0) 97 对乙酰氨基酚片(0.8)
           / 98 双氯芬酸钠缓释片(0.4)

外科（P = postop=1，仅术后使用）
外科 K35.8:  18 甲硝唑片(1.0) 32 阿莫西林克拉维酸钾片(0.9) 30 头孢氨苄胶囊(0.8)
           / 15 阿奇霉素分散片(0.4) 29 莫西沙星片(0.4)
           / 98 双氯芬酸钠缓释片(0.6)P 179 口服补液盐III(0.4)P
外科 K80.1:  32 阿莫西林克拉维酸钾片(1.0) 18 甲硝唑片(0.9) 78 熊去氧胆酸胶囊(0.8)
           / 17 克拉霉素片(0.4)
           / 101 洛索洛芬钠片(0.5)P 79 甘草酸二铵肠溶胶囊(0.4)P
外科 S52.5:  99 塞来昔布胶囊(1.0) 102 依托考昔片(0.8) 176 碳酸钙D3片(0.7)
           / 98 双氯芬酸钠缓释片(0.6) 104 艾瑞昔布片(0.5)
           / 177 葡萄糖酸钙口服溶液(0.5)
外科 E04.1:  120 左甲状腺素钠片(1.0)
           / 121 甲巯咪唑片(0.3)
外科 C73:    120 左甲状腺素钠片(1.0)
           / 176 碳酸钙D3片(0.5) 175 维生素D3滴剂(0.4)
外科 M17.9:  99 塞来昔布胶囊(1.0) 100 美洛昔康片(0.8) 101 洛索洛芬钠片(0.7)
           / 98 双氯芬酸钠缓释片(0.6) 104 艾瑞昔布片(0.5)
           / 176 碳酸钙D3片(0.6) 175 维生素D3滴剂(0.5)
外科 I84.2:  180 云南白药胶囊(0.9) 181 血塞通片(0.8)
           / 71 消旋山莨菪碱片(0.4)
           / 76 聚乙二醇4000散(0.7) 75 乳果糖口服溶液(0.6)
外科 S61.0:  1 阿莫西林胶囊(1.0) 30 头孢氨苄胶囊(0.8)
           / 153 莫匹罗星软膏(0.7) 154 夫西地酸乳膏(0.6)
           / 97 对乙酰氨基酚片(0.5)P
外科 K40.9:  30 头孢氨苄胶囊(0.7)P 99 塞来昔布胶囊(0.8)P
           / 76 聚乙二醇4000散(0.5)
外科 L02.9:  1 阿莫西林胶囊(1.0) 30 头孢氨苄胶囊(0.9) 153 莫匹罗星软膏(0.8)
           / 17 克拉霉素片(0.4) 36 米诺环素胶囊(0.3)
           / 97 对乙酰氨基酚片(0.5)P
外科 D23.9:  153 莫匹罗星软膏(0.8)P 97 对乙酰氨基酚片(0.6)P
外科 K63.5:  180 云南白药胶囊(0.6)P
           / 73 双歧杆菌三联活菌胶囊(0.5) 76 聚乙二醇4000散(0.4)
外科 K42.9:  30 头孢氨苄胶囊(0.7)P 99 塞来昔布胶囊(0.6)P

儿科（**严格禁忌：不得出现喹诺酮类 26/27/28/29 与阿司匹林 9**）
儿科 J06.9:  2 感冒灵颗粒(1.0) 1 阿莫西林胶囊(0.6)
           / 97 对乙酰氨基酚片(0.7) 186 蓝芩口服液(0.6)
           / 171 维生素C片(0.5)
儿科 R50.9:  97 对乙酰氨基酚片(1.0) 3 布洛芬缓释胶囊(0.7)
           / 179 口服补液盐III(0.6)
儿科 A09.9:  7 蒙脱石散(1.0) 179 口服补液盐III(0.9)
           / 73 双歧杆菌三联活菌胶囊(0.6) 74 地衣芽孢杆菌活菌胶囊(0.5)
           / 184 藿香正气口服液(0.4)
儿科 J18.0:  1 阿莫西林胶囊(1.0) 15 阿奇霉素分散片(0.9) 34 青霉素V钾片(0.6)
           / 14 头孢呋辛酯片(0.7) 31 头孢拉定胶囊(0.6)
           / 81 氨溴索口服溶液(0.7)
儿科 K52.9:  179 口服补液盐III(1.0) 7 蒙脱石散(0.9)
           / 73 双歧杆菌三联活菌胶囊(0.7) 74 地衣芽孢杆菌活菌胶囊(0.6)
儿科 J02.9:  1 阿莫西林胶囊(1.0) 186 蓝芩口服液(0.8)
           / 15 阿奇霉素分散片(0.5)
           / 164 西地碘含片(0.4)
儿科 B08.4:  186 蓝芩口服液(1.0) 184 藿香正气口服液(0.6)
           / 97 对乙酰氨基酚片(0.6) 171 维生素C片(0.5)
儿科 H66.9:  1 阿莫西林胶囊(1.0) 32 阿莫西林克拉维酸钾片(0.9)
           / 14 头孢呋辛酯片(0.6) 15 阿奇霉素分散片(0.5)
           / 157 氧氟沙星滴耳液(0.7)
儿科 J35.0:  1 阿莫西林胶囊(1.0)
           / 15 阿奇霉素分散片(0.5)
           / 186 蓝芩口服液(0.6)
儿科 L20.9:  146 丁酸氢化可的松乳膏(1.0) 90 氯雷他定片(0.8)
           / 155 他克莫司软膏(0.5)
           / 156 炉甘石洗剂(0.6)
儿科 K40.9:  1 阿莫西林胶囊(0.5)

妇科
妇科 N76.0:  165 克霉唑阴道片(1.0) 167 甲硝唑阴道泡腾片(0.9) 166 硝呋太尔制霉素阴道软胶囊(0.8)
           / 18 甲硝唑片(0.5)
           / 168 保妇康栓(0.6)
妇科 D25.9:  180 云南白药胶囊(0.8)
           / 169 屈螺酮炔雌醇片(0.5) 170 地屈孕酮片(0.5)
           / 176 碳酸钙D3片(0.4)
妇科 N92.6:  170 地屈孕酮片(1.0) 169 屈螺酮炔雌醇片(0.8)
           / 180 云南白药胶囊(0.5) 174 叶酸片(0.4)
妇科 N72:    167 甲硝唑阴道泡腾片(1.0) 166 硝呋太尔制霉素阴道软胶囊(0.8)
           / 18 甲硝唑片(0.5) 35 多西环素片(0.5)
妇科 N73.9:  18 甲硝唑片(1.0) 35 多西环素片(0.9) 32 阿莫西林克拉维酸钾片(0.7)
           / 29 莫西沙星片(0.5) 17 克拉霉素片(0.4)
           / 180 云南白药胶囊(0.5)
妇科 N83.2:  180 云南白药胶囊(0.6)
           / 169 屈螺酮炔雌醇片(0.5)
妇科 N94.6:  3 布洛芬缓释胶囊(1.0) 97 对乙酰氨基酚片(0.7)
           / 170 地屈孕酮片(0.5)
           / 182 复方丹参滴丸(0.3)
妇科 N84.0:  170 地屈孕酮片(0.8)
           / 180 云南白药胶囊(0.5)
妇科 N80.0:  3 布洛芬缓释胶囊(0.8) 170 地屈孕酮片(0.7)
           / 169 屈螺酮炔雌醇片(0.5)
           / 180 云南白药胶囊(0.5)
妇科 N97.9:  174 叶酸片(0.7) 120 左甲状腺素钠片(0.5)
           / 175 维生素D3滴剂(0.4)
妇科 N85.6:  169 屈螺酮炔雌醇片(0.7)
           / 180 云南白药胶囊(0.4)

五官科
五官科 J02.9:  1 阿莫西林胶囊(1.0) 186 蓝芩口服液(0.9)
             / 15 阿奇霉素分散片(0.6) 17 克拉霉素片(0.5)
             / 164 西地碘含片(0.6) 163 复方硼砂含漱液(0.5)
五官科 J30.4:  90 氯雷他定片(1.0) 91 西替利嗪片(0.9) 161 糠酸莫米松鼻喷雾剂(1.0)
             / 88 孟鲁司特钠片(0.7) 162 布地奈德鼻喷雾剂(0.8)
             / 92 依巴斯汀片(0.5)
五官科 H25.9:  158 左氧氟沙星滴眼液(0.9)P 160 玻璃酸钠滴眼液(0.8)P
             / 171 维生素C片(0.4)
五官科 J31.0:  161 糠酸莫米松鼻喷雾剂(1.0) 162 布地奈德鼻喷雾剂(0.9)
             / 90 氯雷他定片(0.6) 91 西替利嗪片(0.5)
             / 93 马来酸氯苯那敏片(0.4)
五官科 J35.0:  1 阿莫西林胶囊(1.0) 17 克拉霉素片(0.6)
             / 164 西地碘含片(0.7) 163 复方硼砂含漱液(0.6)
五官科 H66.9:  1 阿莫西林胶囊(1.0) 32 阿莫西林克拉维酸钾片(0.9)
             / 14 头孢呋辛酯片(0.6) 17 克拉霉素片(0.5)
             / 157 氧氟沙星滴耳液(0.8)
五官科 H10.9:  158 左氧氟沙星滴眼液(1.0) 159 妥布霉素滴眼液(0.9)
             / 160 玻璃酸钠滴眼液(0.6)
             / 171 维生素C片(0.3)
五官科 J34.2:  161 糠酸莫米松鼻喷雾剂(0.7)
             / 93 马来酸氯苯那敏片(0.4)
五官科 H40.9:  188 拉坦前列素滴眼液(1.0) 187 噻吗洛尔滴眼液(0.9)
             / 160 玻璃酸钠滴眼液(0.3)
五官科 J33.9:  161 糠酸莫米松鼻喷雾剂(0.9)
             / 90 氯雷他定片(0.5)
             / 93 马来酸氯苯那敏片(0.3)
五官科 H81.1:  189 甲磺酸倍他司汀片(1.0)
             / 182 复方丹参滴丸(0.4)

皮肤科
皮肤科 L70.0:  190 阿达帕林凝胶(1.0) 191 过氧苯甲酰凝胶(0.9) 192 克林霉素磷酸酯凝胶(0.8)
             / 193 异维A酸软胶囊(0.3) 194 维A酸乳膏(0.6)
皮肤科 L30.9:  145 糠酸莫米松乳膏(1.0) 146 丁酸氢化可的松乳膏(0.9) 90 氯雷他定片(0.7)
             / 155 他克莫司软膏(0.5)
             / 156 炉甘石洗剂(0.5)
皮肤科 L50.9:  90 氯雷他定片(1.0) 91 西替利嗪片(0.9) 92 依巴斯汀片(0.8)
             / 93 马来酸氯苯那敏片(0.4)
             / 156 炉甘石洗剂(0.5)
皮肤科 L20.9:  146 丁酸氢化可的松乳膏(1.0) 90 氯雷他定片(0.8)
             / 155 他克莫司软膏(0.7)
             / 156 炉甘石洗剂(0.5)
皮肤科 B35.9:  150 联苯苄唑乳膏(1.0) 151 特比萘芬乳膏(0.9)
             / 20 氟康唑胶囊(0.5) 21 伊曲康唑胶囊(0.4)
皮肤科 L21.9:  145 糠酸莫米松乳膏(0.8) 149 曲安奈德益康唑乳膏(0.7)
             / 150 联苯苄唑乳膏(0.5)
皮肤科 B02.9:  22 阿昔洛韦片(1.0) 23 伐昔洛韦片(0.9)
             / 152 阿昔洛韦乳膏(0.6)
             / 173 维生素B12片(0.7) 97 对乙酰氨基酚片(0.6)
皮肤科 L08.9:  153 莫匹罗星软膏(1.0) 154 夫西地酸乳膏(0.9) 1 阿莫西林胶囊(0.7)
             / 15 阿奇霉素分散片(0.4)
皮肤科 L60.0:  153 莫匹罗星软膏(1.0) 154 夫西地酸乳膏(0.8) 1 阿莫西林胶囊(0.7)
             / 30 头孢氨苄胶囊(0.5)
皮肤科 L40.9:  147 卤米松乳膏(0.9) 195 卡泊三醇软膏(0.8) 194 维A酸乳膏(0.7) 155 他克莫司软膏(0.6)
皮肤科 L03.9:  1 阿莫西林胶囊(1.0) 32 阿莫西林克拉维酸钾片(0.9)
             / 17 克拉霉素片(0.5) 36 米诺环素胶囊(0.4)
             / 97 对乙酰氨基酚片(0.5)
```

- [ ] **Step 2: 逐条核对用药合理性（本任务的核心审校步）**

按科室通读一遍，逐条问三个问题：

1. **有没有「药不对病」**——例如给 皮肤科 开 心血管药、给 妇科 开 呼吸药；
2. **有没有禁忌**——儿科不得有喹诺酮类（26 吡哌酸 / 27 诺氟沙星 / 28 环丙沙星 / 29 莫西沙星）
   与阿司匹林（9）；青光眼不得开 抗胆碱能类（如 71 消旋山莨菪碱、140 托特罗定、
   141 索利那新——**上表已避开，请复核**）；
3. **一线是否够用**——一线为空或只有一种药的诊断，生成器会退到二线，需确认可接受。

核出的问题**直接改上表**并在提交信息里列出改动。**核不完的写下未核条目**，不要沉默通过。

- [ ] **Step 3: 入库并断言（含禁忌的机器检查）**

```bash
cd backend
mysql -uroot -p123456 --default-character-set=utf8mb4 his_demo < sql/seed-ontology.sql
```

```bash
mysql -uroot -p123456 --default-character-set=utf8mb4 -e "
SELECT '总行数' AS chk, COUNT(*) AS v FROM his_demo.diagnosis_drug
UNION ALL SELECT '覆盖的(科室,ICD)组合数(应 72)', COUNT(*) FROM (
  SELECT DISTINCT department, icd_code FROM his_demo.diagnosis_drug) z
UNION ALL SELECT '非法 line(应 0)', COUNT(*) FROM his_demo.diagnosis_drug WHERE line NOT IN (1,2,3)
UNION ALL SELECT '部门为空(应 0)', COUNT(*) FROM his_demo.diagnosis_drug WHERE department IS NULL
UNION ALL SELECT '引用不存在的药(应 0)', COUNT(*) FROM his_demo.diagnosis_drug dg
  WHERE NOT EXISTS (SELECT 1 FROM his_demo.drug dr WHERE dr.id = dg.drug_id)
UNION ALL SELECT '(科室,ICD) 不在 dept_diagnosis 中(应 0)', COUNT(*) FROM (
  SELECT DISTINCT department, icd_code FROM his_demo.diagnosis_drug) x
  WHERE NOT EXISTS (SELECT 1 FROM his_demo.dept_diagnosis dd
    WHERE dd.department = x.department AND dd.icd_code = x.icd_code)
UNION ALL SELECT '无一线药的诊断数(应 0)', COUNT(*) FROM (
  SELECT DISTINCT department, icd_code FROM his_demo.diagnosis_drug) x
  WHERE NOT EXISTS (SELECT 1 FROM his_demo.diagnosis_drug d
    WHERE d.department = x.department AND d.icd_code = x.icd_code AND d.line = 1)
UNION ALL SELECT '【禁忌】儿科用了喹诺酮类(应 0)', COUNT(*) FROM his_demo.diagnosis_drug
  WHERE department = '儿科' AND drug_id IN (26,27,28,29)
UNION ALL SELECT '【禁忌】儿科用了阿司匹林(应 0)', COUNT(*) FROM his_demo.diagnosis_drug
  WHERE department = '儿科' AND drug_id = 9
UNION ALL SELECT '【禁忌】青光眼用了抗胆碱能药(应 0)', COUNT(*) FROM his_demo.diagnosis_drug
  WHERE icd_code = 'H40.9' AND drug_id IN (71,140,141,93)
UNION ALL SELECT 'rule_config 既有映射未保留的项数(应 0)', COUNT(*) FROM his_demo.rule_config rc
  JOIN his_demo.dept_diagnosis dd ON dd.icd_code = rc.diagnosis_code
  WHERE NOT EXISTS (
    SELECT 1 FROM his_demo.diagnosis_drug dg
    WHERE dg.icd_code = rc.diagnosis_code AND dg.department = dd.department
      AND FIND_IN_SET(dg.drug_id, rc.drug_ids) > 0);
SELECT department, COUNT(*) c FROM his_demo.diagnosis_drug GROUP BY department ORDER BY c DESC;"
```

Expected: `总行数` 约 360（填实测值）、`覆盖的(科室,ICD)组合数 72`；
其余应为 `0`，**尤其两条「【禁忌】」必须为 0**。
最后一条按科室列出映射条数，用于确认没有哪个科室被漏掉（应 6 行）。

> 倒数第二条断言是**跨版本的锚**：它要求既有 `rule_config` 的每个 `drug_ids` 成员，
> 都在新表中以对应 `(科室, ICD)` 的行出现。这是「换数据源但没丢语义」的机器证明。
> 注意 `J06.9`/`A09.9`/`R50.9` 这些**跨科室 ICD 会对多个科室各查一遍**——
> 断言里按 `dd.department` 逐一比对，正是为了覆盖这种情况。

- [ ] **Step 4: Commit**

```bash
git add backend/sql/seed-ontology.sql
git commit -m "feat(sql): 用药本体 —— 72 组诊断用药映射，儿科禁忌由断言卡住" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 8: `drug_dose` 剂量表 + 修正 `seed-extension.sql` 过松阈值（阶段一）

**Files:**
- Modify: `backend/sql/seed-ontology.sql`（追加第 7 节 `drug_dose`）
- Modify: `backend/sql/seed-extension.sql:10-12`（硝苯地平/二甲双胍日上限）
- Test: 无单元测试；本任务末尾是本表自洽性 SQL 断言，跨表一致性在 Task 9

**Interfaces:**
- Consumes: Task 3 的 196 种药（`drug_id` 必须存在）；原 12 种药的 id 由 `seed.sql` 自增决定，即 1~12
- Produces: `drug_dose` 142 行，`UNIQUE KEY uk_drug_dose (drug_id)`；被 Task 10 的 DOSE 检查
  与 Task 13 的生成器读取。**`frequency` 的取值域在此锁定**，
  Task 10 的 `OntologyRules.timesPerDay` 必须与之逐字一致（Task 16 的生成器调的是同一个方法）

### 剂量书写口径（三条，先定死再写数据）

1. **`single_dose` 的单位是「最小可分割给药单位」**：口服固体制剂按 片/粒/袋/支/丸，
   口服液与溶液按 ml，气雾剂按 揿，吸入粉雾剂按 吸/粒，**外用、滴眼、滴耳、鼻喷按「次」**。
2. **`frequency` 只用五个值**：`每日1次` `每日2次` `每日3次` `每日4次` `每晚1次`。
   对应的每日次数是 1/2/3/4/1。**每周给药的品种不在本模型内**（见下方注 ②）。
3. **`max_daily_dose` 的定义随剂型二分**：
   - 口服/注射：一日内 `single_dose × 每日次数` 的上限，**单位与 `dose_unit` 相同**；
   - 外用/滴眼/滴耳/鼻喷/吸入：**一日内给药次数的上限**（因为「适量涂抹」无法用最小可分割单位计数）。
   两种情形下 `single_dose × 每日次数 ≤ max_daily_dose` 都成立，所以 Task 10 的 DOSE 检查
   只需一条不等式，不必分支。

> 注 ①：口径 3 的后半段是个**已知的建模妥协**——外用制剂的「日上限」语义与口服不同，
> 本表用 `dose_unit='次'` 把它标出来，让读数据的人一眼能分辨。论文里要如实写这个妥协，
> 不要把它说成「统一剂量模型」。
>
> 注 ②：**`甲氨蝶呤片`（id 109）刻意不写 `drug_dose` 行，也不出现在任何 `diagnosis_drug` 映射里。**
> 它是银屑病与类风湿的锚定药，但标准用法是**每周 1 次**，本模型的 `frequency` 取值域
> （每日 N 次）无法表达周剂量；强行写成「每日1次」会造出比原问题更严重的剂量错误。
> 收录但不用，是这里唯一诚实的处理。Task 9 的覆盖集断言因此**不把 109 计入要求**。

- [ ] **Step 1: 修正 `seed-extension.sql` 里两条过松的日上限**

spec §4.4 明确指出硝苯地平的 8 片/日（240mg）宽于控释片的 30~60mg/日。
二甲双胍的 8 片/日（4g）同样超出常规上限 2g/日。两条都改：

```sql
INSERT INTO rational_rule (rule_type, drug_id, max_daily_dose, warning_level, message, enabled) VALUES
(2, 4, 2.00, 1, '硝苯地平控释片每日最大剂量 2 片（60mg）', 1),
(2, 5, 4.00, 1, '二甲双胍片每日最大剂量 4 片（2g）', 1);
```

**若运行库已经执行过旧版 `seed-extension.sql`**，编辑文件不会改到已入库的行，需补一条 UPDATE。
判断方法（先查，再决定要不要跑下面的 UPDATE）：

```bash
mysql -uroot -p123456 -D his_demo -e \
  "SELECT rule_type, drug_id, max_daily_dose FROM rational_rule WHERE rule_type = 2;"
```

若 `drug_id=4` 的 `max_daily_dose` 还是 `8.00`、`drug_id=5` 还是 `8.00`，
说明入库的是旧版（旧版见 `seed-extension.sql` 的当前内容，已被本步改过），补跑：

```sql
UPDATE rational_rule SET max_daily_dose = 2.00, message = '硝苯地平控释片每日最大剂量 2 片（60mg）'
  WHERE rule_type = 2 AND drug_id = 4;
UPDATE rational_rule SET max_daily_dose = 4.00, message = '二甲双胍片每日最大剂量 4 片（2g）'
  WHERE rule_type = 2 AND drug_id = 5;
```

- [ ] **Step 2: 在 `seed-ontology.sql` 追加 `drug_dose`（142 行）**

```sql
-- ============================================================
-- 第七节  drug_dose  药品剂量规则
-- 单位与频次口径见 docs/superpowers/plans/2026-09-21-medical-realism.md Task 8
-- 覆盖集 = diagnosis_drug 引用到的全部药品 ∪ seed.sql 原 12 种
-- 剂量为演示用常规成人剂量，非说明书逐字抄录；不构成用药建议
-- ============================================================
-- 幂等：drug_dose 有唯一键 uk_drug_dose(drug_id)，重跑本段会**报错**（Duplicate entry），
--       是响的而不是静默的——但仍加 DELETE 前置，理由是执行是**手工**的（见 D-4 部署批）：
--       报错会中止后续语句，留下「删了一半」的中间态，反而比静默更难收拾。
--       本表无外键指向它，DELETE 永远安全。
DELETE FROM drug_dose;
INSERT INTO drug_dose (drug_id, single_dose, dose_unit, frequency, max_daily_dose, days_min, days_max) VALUES
-- 抗感染
(1 ,1 ,'粒','每日3次',4 ,5 ,7 ),
(2 ,1 ,'袋','每日3次',3 ,3 ,7 ),
(8 ,1 ,'片','每日1次',1 ,5 ,7 ),
(14,1 ,'片','每日2次',4 ,5 ,7 ),
(15,1 ,'片','每日1次',1 ,3 ,5 ),
(17,1 ,'片','每日2次',2 ,7 ,14),
(18,1 ,'片','每日3次',3 ,5 ,7 ),
(20,1 ,'粒','每日1次',1 ,7 ,14),
(21,1 ,'粒','每日1次',2 ,7 ,14),
(22,2 ,'片','每日3次',6 ,7 ,10),
(23,1 ,'片','每日2次',2 ,7 ,10),
(29,1 ,'片','每日1次',1 ,5 ,7 ),
(30,1 ,'粒','每日3次',4 ,5 ,7 ),
(31,1 ,'粒','每日4次',4 ,5 ,7 ),
(32,1 ,'片','每日2次',2 ,5 ,7 ),
(34,2 ,'片','每日3次',6 ,5 ,7 ),
(35,1 ,'片','每日2次',2 ,7 ,10),
(36,1 ,'粒','每日2次',2 ,7 ,10),
-- 心血管
(4 ,1 ,'片','每日1次',2 ,14,30),
(9 ,1 ,'片','每日1次',1 ,30,90),
(10,1 ,'片','每日1次',1 ,14,30),
(37,1 ,'粒','每日1次',1 ,14,30),
(39,1 ,'片','每日1次',2 ,14,30),
(40,1 ,'片','每日1次',2 ,14,30),
(41,1 ,'片','每日3次',3 ,14,30),
(43,1 ,'片','每日1次',2 ,14,30),
(44,1 ,'片','每日1次',1 ,14,30),
(46,1 ,'片','每日3次',3 ,14,30),
(47,1 ,'片','每日1次',2 ,7 ,14),
(48,1 ,'片','每日1次',2 ,14,30),
(50,1 ,'片','每日1次',1 ,14,30),
(51,1 ,'片','每日3次',3 ,7 ,14),
(52,1 ,'片','每日1次',2 ,14,30),
(53,1 ,'片','每日1次',1 ,30,90),
(54,1 ,'片','每日1次',1 ,30,90),
(55,1 ,'片','每日1次',1 ,30,90),
(56,1 ,'粒','每日1次',1 ,30,90),
(57,1 ,'片','每日1次',2 ,30,90),
(58,1 ,'片','每日1次',1 ,30,90),
(60,1 ,'片','每日1次',1 ,30,90),
(61,1 ,'片','每日1次',1 ,14,30),
(62,1 ,'片','每日1次',3 ,14,30),
-- 消化
(6 ,1 ,'粒','每日1次',2 ,7 ,14),
(7 ,1 ,'袋','每日3次',3 ,3 ,7 ),
(63,2 ,'片','每日3次',6 ,7 ,14),
(64,1 ,'片','每日1次',2 ,14,30),
(65,1 ,'片','每日1次',1 ,14,30),
(66,1 ,'片','每日1次',1 ,14,30),
(67,1 ,'片','每日2次',2 ,7 ,14),
(68,2 ,'粒','每日2次',4 ,14,28),
(69,1 ,'片','每日3次',3 ,7 ,14),
(70,1 ,'片','每日3次',3 ,7 ,14),
(71,1 ,'片','每日3次',3 ,3 ,7 ),
(73,2 ,'粒','每日3次',6 ,7 ,14),
(74,2 ,'粒','每日3次',6 ,7 ,14),
(75,15,'ml','每日1次',30,7 ,14),
(76,1 ,'袋','每日2次',2 ,7 ,14),
(78,1 ,'粒','每日3次',3 ,30,90),
(79,2 ,'粒','每日3次',6 ,14,30),
-- 呼吸
(12,10,'ml','每日3次',30,3 ,7 ),
(81,10,'ml','每日3次',30,3 ,7 ),
(82,1 ,'片','每日2次',2 ,7 ,14),
(84,2 ,'粒','每日3次',6 ,3 ,7 ),
(85,2 ,'揿','每日3次',8 ,7 ,14),
(86,1 ,'吸','每日2次',2 ,30,90),
(87,1 ,'粒','每日1次',1 ,30,90),
(88,1 ,'片','每日1次',1 ,14,30),
(89,1 ,'片','每日2次',3 ,14,30),
(90,1 ,'片','每日1次',1 ,7 ,14),
(91,1 ,'片','每日1次',1 ,7 ,14),
(92,1 ,'片','每日1次',1 ,7 ,14),
(93,1 ,'片','每日3次',3 ,3 ,7 ),
(94,3 ,'片','每日3次',9 ,3 ,7 ),
(96,3 ,'粒','每日3次',9 ,7 ,14),
-- 解热镇痛与抗风湿
(3 ,1 ,'粒','每日2次',2 ,3 ,5 ),
(97,1 ,'片','每日3次',4 ,3 ,5 ),
(98,1 ,'片','每日1次',1 ,5 ,10),
(99,1 ,'粒','每日2次',2 ,7 ,14),
(100,1,'片','每日1次',2 ,7 ,14),
(101,1,'片','每日3次',3 ,5 ,10),
(102,1,'片','每日1次',1 ,5 ,10),
(104,1,'片','每日2次',2 ,5 ,10),
-- 降糖
(5 ,1 ,'片','每日3次',4 ,14,30),
(11,1 ,'片','每日1次',2 ,14,30),
(111,1,'片','每日3次',3 ,30,90),
(112,1,'片','每日3次',3 ,30,90),
(113,1,'片','每日3次',3 ,30,90),
(115,1,'片','每日1次',1 ,30,90),
(116,1,'片','每日1次',1 ,30,90),
(118,1,'片','每日1次',1 ,30,90),
-- 内分泌其他
(120,1,'片','每日1次',2 ,30,90),
(121,1,'片','每日2次',4 ,30,90),
(122,1,'片','每日3次',6 ,30,90),
-- 泌尿
(143,1,'袋','每日3次',3 ,30,90),
(144,2,'片','每日3次',6 ,14,30),
-- 皮肤外用（单位「次」）
(145,1,'次','每日1次',3 ,7 ,14),
(146,1,'次','每日2次',3 ,7 ,14),
(147,1,'次','每日1次',3 ,7 ,14),
(149,1,'次','每日2次',3 ,7 ,14),
(150,1,'次','每日1次',3 ,14,28),
(151,1,'次','每日2次',3 ,14,28),
(152,1,'次','每日3次',3 ,7 ,10),
(153,1,'次','每日3次',3 ,5 ,10),
(154,1,'次','每日3次',3 ,5 ,10),
(155,1,'次','每日2次',2 ,14,30),
(156,1,'次','每日3次',3 ,3 ,7 ),
(189,1,'次','每晚1次',1 ,30,90),
(190,1,'次','每日2次',2 ,30,90),
(191,1,'次','每日2次',2 ,14,30),
(194,1,'次','每晚1次',1 ,30,90),
(195,1,'次','每日2次',2 ,30,90),
-- 五官科（单位「次」）
(157,1,'次','每日2次',3 ,5 ,7 ),
(158,1,'次','每日4次',4 ,5 ,7 ),
(159,1,'次','每日4次',4 ,5 ,7 ),
(160,1,'次','每日4次',4 ,14,30),
(161,1,'次','每日1次',1 ,14,30),
(162,1,'次','每日2次',2 ,14,30),
(163,1,'次','每日3次',3 ,3 ,7 ),
(164,1,'片','每日3次',3 ,3 ,7 ),
(187,1,'次','每日2次',2 ,30,90),
(188,1,'片','每日3次',3 ,14,30),
(192,1,'次','每晚1次',1 ,30,90),
-- 妇科
(165,1,'片','每晚1次',1 ,1 ,1 ),
(166,1,'粒','每晚1次',1 ,6 ,6 ),
(167,1,'片','每晚1次',1 ,7 ,7 ),
(168,1,'枚','每晚1次',1 ,7 ,7 ),
(169,1,'片','每日1次',1 ,21,21),
(170,1,'片','每日2次',2 ,10,20),
-- 皮肤科口服
(193,1,'粒','每日2次',2 ,30,90),
-- 其他
(171,1,'片','每日3次',3 ,14,30),
(173,1,'片','每日3次',3 ,14,30),
(174,1,'片','每日1次',1 ,30,90),
(175,1,'粒','每日1次',1 ,30,90),
(176,1,'片','每日1次',2 ,30,90),
(177,1,'支','每日3次',3 ,14,30),
(179,1,'袋','每日3次',6 ,1 ,3 ),
(180,2,'粒','每日3次',6 ,5 ,10),
(181,1,'片','每日3次',3 ,14,30),
(182,10,'丸','每日3次',30,30,90),
(184,1,'支','每日2次',2 ,3 ,7 ),
(185,4,'粒','每日3次',12,3 ,7 ),
(186,1,'支','每日3次',3 ,3 ,7 );
```

- [ ] **Step 3: 本表自洽性断言**

```bash
mysql -uroot -p123456 --default-character-set=utf8mb4 his_demo -e "
SELECT '总行数(应 142)' AS item, COUNT(*) AS v FROM drug_dose
UNION ALL SELECT 'drug_id 重复(应 0)', COUNT(*) FROM (
  SELECT drug_id FROM drug_dose GROUP BY drug_id HAVING COUNT(*) > 1) z
UNION ALL SELECT 'drug_id 不存在(应 0)', COUNT(*) FROM drug_dose dd
  WHERE NOT EXISTS (SELECT 1 FROM drug dr WHERE dr.id = dd.drug_id)
UNION ALL SELECT 'frequency 不在取值域(应 0)', COUNT(*) FROM drug_dose dd
  LEFT JOIN (SELECT '每日1次' f UNION ALL SELECT '每日2次' UNION ALL
             SELECT '每日3次' UNION ALL SELECT '每日4次' UNION ALL
             SELECT '每晚1次') x ON x.f = dd.frequency
  WHERE x.f IS NULL
UNION ALL SELECT '单次*每日次数 > 日上限(应 0)', COUNT(*) FROM drug_dose dd
  JOIN (SELECT '每日1次' f,1 t UNION ALL SELECT '每日2次',2 UNION ALL
        SELECT '每日3次',3 UNION ALL SELECT '每日4次',4 UNION ALL
        SELECT '每晚1次',1) x ON x.f = dd.frequency
  WHERE dd.single_dose * x.t > dd.max_daily_dose
UNION ALL SELECT 'days_min < 1(应 0)', COUNT(*) FROM drug_dose WHERE days_min < 1
UNION ALL SELECT 'days_min > days_max(应 0)', COUNT(*) FROM drug_dose WHERE days_min > days_max
UNION ALL SELECT '单次剂量 <= 0(应 0)', COUNT(*) FROM drug_dose WHERE single_dose <= 0
UNION ALL SELECT '与 rational_rule 日上限不一致(应 0)', COUNT(*) FROM drug_dose dd
  JOIN rational_rule rr ON rr.rule_type = 2 AND rr.drug_id = dd.drug_id
  WHERE rr.max_daily_dose <> dd.max_daily_dose
UNION ALL SELECT '未覆盖的 diagnosis_drug 引用药(应 0)', COUNT(*) FROM (
  SELECT DISTINCT drug_id FROM diagnosis_drug) dg
  WHERE NOT EXISTS (SELECT 1 FROM drug_dose dd WHERE dd.drug_id = dg.drug_id)
UNION ALL SELECT '未覆盖的原 12 种药(应 0)', COUNT(*) FROM (
  SELECT 1 id UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL
  SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL
  SELECT 9 UNION ALL SELECT 10 UNION ALL SELECT 11 UNION ALL SELECT 12) o
  WHERE NOT EXISTS (SELECT 1 FROM drug_dose dd WHERE dd.drug_id = o.id);"
```

Expected: `总行数 142`，其余**全部为 0**。

> 倒数第二条是 Task 10 换数据源的前提：`RationalDrugValidator` 的 DOSE 检查会对
> 每一张被判定为「适应症合规」的处方去查 `drug_dose`，查不到就必须报 `unchecked`。
> 让未覆盖数为 0，是为了不把 `unchecked` 变成常态噪音——**那不是「没问题」，是「没检查」**。

- [ ] **Step 4: Commit**

```bash
git add backend/sql/seed-ontology.sql backend/sql/seed-extension.sql
git commit -m "feat(sql): 药品剂量表 142 行，并收紧硝苯地平/二甲双胍的日上限" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 9: `SeedSqlConsistencyTest` —— 把「我看过一遍」换成「机器每次跑」 + 作者审校门（阶段一）

**Files:**
- Create: `backend/his-generator/src/test/java/com/his/generator/SeedSqlConsistencyTest.java`
- Modify: `backend/sql/verify-final.sql`（追加本体断言段；替换第 43-44 行锚定 `rule_config` 的那条）
- Create: `backend/sql/AUTHOR-REVIEW-phase3.md`（作者逐表签收单）

**Interfaces:**
- Consumes: Task 1 的 7 张表、Task 3–8 写入 `seed.sql`/`seed-extension.sql`/`seed-ontology.sql` 的全部行
- Produces: 16 个断言方法；`RationalDrugValidator` 与生成器的**数据前置条件**由本测试守住

**为什么放在 his-generator：** `his-common` 没有 test 依赖（它只放实体与 Mapper），
`his-server` 的测试是全 Slice 的、要起 Spring 上下文。本测试只读文件、不需要数据库，
放在 `his-generator`（已有纯 Mockito 测试、且 surefire 工作目录就是模块目录）最省事。
surefire 的工作目录是 `backend/his-generator`，所以 `../sql` 正好是 `backend/sql`。

> **若日后要把本测试改成「连真库比对」，先读这两条**（Task 2 评审在真库上实测得出，
> 两者都会让断言**假失败**——红的是比法，不是数据，而假失败最容易被改成放宽断言来「修好」）：
>
> 1. **JSON 列会被 MySQL 规范化**：写入 `[0.6,0.7,0.8]`，读回是 `[0.6, 0.7, 0.8]`（逗号后带空格）。
>    所以 `DeptDiagnosis.getMonthWeights()` **永远不等于**种子文件里的字面量。
>    要比就 `ObjectMapper` 解析后比元素（这也是计划里 `parseMonthWeights` 的用法），
>    **不要**字符串 `equals`。
> 2. **`BigDecimal.equals` 比的是标度**：`new BigDecimal("1.000").equals(new BigDecimal("1.0"))` 是 `false`，
>    而 `weight DECIMAL(6,3)` 读回恒带 3 位小数。比 DECIMAL 一律用 `compareTo` 或 `doubleValue()`。
>
> 本测试**只读文件、不连库**，所以当前不受影响——写在这里是因为它是全仓最像「顺手加一次库比对」的地方。

**这个测试要解决什么：** 前六个任务产出的是**手写的中文医学数据 + 我在计划里逐条写下的断言**。
断言写在计划文档里，只在我手工粘进 mysql 客户端时跑过一次——**下次有人改一行种子，
没有任何东西会拦住他**。本任务把那批断言搬进 JUnit：改坏了，`mvn test` 就红。

- [ ] **Step 1: 写失败测试（骨架 + 解析器 + 前 4 个断言）**

```java
package com.his.generator;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.*;

/**
 * 种子 SQL 的跨表一致性校验。
 *
 * 只读 backend/sql/*.sql 文本，不连数据库——所以它在离线环境、在任何人的机器上
 * 跑出来的结论都一样，不需要先建库。
 *
 * 断言的医学含义见 docs/superpowers/plans/2026-09-21-medical-realism.md Task 3~8。
 * 这里守的是「数据本身不矛盾」；「生成出来的数据像不像真的」由本体校验器（Task 11）守。
 */
class SeedSqlConsistencyTest {

    /** surefire 的工作目录是 backend/his-generator。 */
    private static final Path SQL_DIR = Paths.get("..", "sql");

    private static final String[] FILES = {"seed.sql", "seed-extension.sql", "seed-ontology.sql"};

    /** 文件 → 表名 → 行（列名 → 已去引号的字面量文本）。 */
    private static final Map<String, Map<String, List<Map<String, String>>>> TABLES = new LinkedHashMap<>();

    @BeforeAll
    static void loadAll() throws IOException {
        for (String file : FILES) {
            Path p = SQL_DIR.resolve(file);
            assertTrue(Files.exists(p), "缺少种子文件: " + p.toAbsolutePath());
            Map<String, List<Map<String, String>>> tables = new LinkedHashMap<>();
            parseSql(Files.readString(p, StandardCharsets.UTF_8), tables);
            TABLES.put(file, tables);
        }
    }

    // ---------- 解析器 ----------

    /** 去掉 `--` 行尾注释后，逐条 INSERT 解析成行。 */
    static void parseSql(String sql, Map<String, List<Map<String, String>>> out) {
        String src = stripLineComments(sql);
        Matcher m = Pattern.compile("(?i)INSERT\\s+INTO\\s+(\\w+)\\s*\\(([^)]*)\\)\\s*VALUES").matcher(src);
        int from = 0;
        while (m.find(from)) {
            String table = m.group(1);
            List<String> cols = Arrays.stream(m.group(2).split(","))
                    .map(String::trim).collect(Collectors.toList());
            List<Map<String, String>> rows = out.computeIfAbsent(table, k -> new ArrayList<>());
            int i = m.end();
            while (true) {
                i = skipToNextTuple(src, i);
                if (i < 0) break;
                int[] cursor = {i};
                List<String> vals = readTuple(src, cursor);
                Map<String, String> row = new LinkedHashMap<>();
                for (int c = 0; c < cols.size(); c++) {
                    row.put(cols.get(c), c < vals.size() ? vals.get(c) : null);
                }
                rows.add(row);
                i = cursor[0];
                if (i < src.length() && src.charAt(i) == ';') break;
            }
            from = i;
        }
    }

    /** 跳到下一个元组的左括号；遇到分号返回 -1。 */
    static int skipToNextTuple(String src, int i) {
        while (i < src.length()) {
            char c = src.charAt(i);
            if (c == ';') return -1;
            if (c == '(') return i;
            i++;
        }
        return -1;
    }

    /**
     * 从 src[cursor[0]]=='(' 读出一个元组，返回去引号后的字段值，并把 cursor 推过右括号。
     *
     * 必须感知单引号：种子数据里有 JSON（clinical_pathway.steps）和
     * 形如 `组[G-CBC](WBC=0.200)` 的文本，其中都可能带逗号与括号。
     */
    static List<String> readTuple(String src, int[] cursor) {
        int i = cursor[0] + 1;
        List<String> vals = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        boolean inStr = false;
        while (i < src.length()) {
            char c = src.charAt(i);
            if (inStr) {
                if (c == '\'') {
                    // SQL 里 '' 是一个转义的单引号
                    if (i + 1 < src.length() && src.charAt(i + 1) == '\'') {
                        cur.append('\''); i += 2; continue;
                    }
                    inStr = false; i++; continue;
                }
                cur.append(c); i++; continue;
            }
            if (c == '\'') { inStr = true; i++; continue; }
            if (c == ',') { vals.add(cur.toString().trim()); cur.setLength(0); i++; continue; }
            if (c == ')') { vals.add(cur.toString().trim()); cursor[0] = i + 1; return vals; }
            cur.append(c); i++;
        }
        throw new IllegalStateException("SQL 元组未闭合（检查种子文件是否被截断）");
    }

    /** 只处理行尾 `--`；种子文件里没有字符串内含 `--` 的情况（中文字段不会有 ASCII 双连字符）。 */
    static String stripLineComments(String sql) {
        StringBuilder sb = new StringBuilder();
        for (String line : sql.split("\r?\n", -1)) {
            int p = line.indexOf("--");
            sb.append(p >= 0 ? line.substring(0, p) : line).append('\n');
        }
        return sb.toString();
    }

    // ---------- 取值辅助 ----------

    static List<Map<String, String>> rows(String file, String table) {
        return TABLES.getOrDefault(file, Map.of()).getOrDefault(table, List.of());
    }

    static long num(Map<String, String> row, String col) {
        String v = row.get(col);
        assertNotNull(v, "列 " + col + " 为空: " + row);
        return Long.parseLong(v.trim());
    }

    static BigDecimal dec(Map<String, String> row, String col) {
        return new BigDecimal(row.get(col).trim());
    }

    static boolean isNull(Map<String, String> row, String col) {
        String v = row.get(col);
        return v == null || "NULL".equalsIgnoreCase(v.trim());
    }

    /** 三份文件里某张表的全部行，按文件顺序拼接。 */
    static List<Map<String, String>> allRows(String table) {
        List<Map<String, String>> out = new ArrayList<>();
        for (String f : FILES) out.addAll(rows(f, table));
        return out;
    }

    /**
     * drug 表的 id → name。
     *
     * seed.sql 的 12 行**没有写 id 列**，靠 AUTO_INCREMENT 得到 1~12——这正是
     * Task 3 要求「seed.sql 必须先跑、且 seed-ontology.sql 的 id 从 13 起显式给出」的原因。
     * 本方法把这个约定显式编码，谁破坏了顺序，这里的断言会先炸。
     */
    static Map<Long, String> drugIdToName() {
        Map<Long, String> map = new LinkedHashMap<>();
        long auto = 1;
        for (Map<String, String> r : rows("seed.sql", "drug")) map.put(auto++, r.get("name"));
        for (Map<String, String> r : rows("seed-ontology.sql", "drug")) {
            assertFalse(isNull(r, "id"),
                    "seed-ontology.sql 的 drug 行必须显式给出 id，否则诊断映射会指错药: " + r.get("name"));
            map.put(num(r, "id"), r.get("name"));
        }
        return map;
    }

    // ---------- 断言（本步先写前 4 个，Step 3 再补齐） ----------

    @Test
    @DisplayName("药品字典达到 spec §9.2 要求的 150 种以上")
    void drugCatalogIsLargeEnough() {
        Map<Long, String> drugs = drugIdToName();
        assertTrue(drugs.size() >= 150, "药品数 " + drugs.size() + " 少于 150");
    }

    @Test
    @DisplayName("dept_diagnosis 共 72 条，六个科室各 8~15 条")
    void deptDiagnosisShape() {
        List<Map<String, String>> rows = allRows("dept_diagnosis");
        assertEquals(72, rows.size(), "dept_diagnosis 行数");
        Map<String, Long> byDept = rows.stream().collect(
                Collectors.groupingBy(r -> r.get("department"), LinkedHashMap::new, Collectors.counting()));
        assertEquals(6, byDept.size(), "科室数: " + byDept.keySet());
        byDept.forEach((dept, n) -> assertTrue(n >= 8 && n <= 15,
                dept + " 有 " + n + " 条，超出 spec §9.2 要求的 8~15"));
    }

    @Test
    @DisplayName("diagnosis_lab 的检验项目数落在 spec §9.2 要求的 30~50")
    void labItemCountInRange() {
        Set<String> items = allRows("diagnosis_lab").stream()
                .map(r -> r.get("item_code")).collect(Collectors.toSet());
        assertTrue(items.size() >= 30 && items.size() <= 50,
                "不同检验项目数 " + items.size() + " 不在 30~50");
    }

    @Test
    @DisplayName("diagnosis_operation 的术式数落在 spec §9.2 要求的 15~25")
    void operationCountInRange() {
        Set<String> ops = allRows("diagnosis_operation").stream()
                .map(r -> r.get("operation_code")).collect(Collectors.toSet());
        assertTrue(ops.size() >= 15 && ops.size() <= 25,
                "不同术式数 " + ops.size() + " 不在 15~25");
    }
}
```

- [ ] **Step 2: 跑一次，确认它真的能跑通解析器**

Run: `cd backend && mvn -o -pl his-generator -Dtest=SeedSqlConsistencyTest test`

Expected: 4 个测试 **PASS**。

**这一步现在就该通过**——种子数据在 Task 3~8 已经写完了。如果这里红了，
说明**解析器错了**（多半是元组切分没考虑引号），不是数据错了。
先看报错里打印的行数是否明显偏小，再回头查 `readTuple`。
*（这是一个诚实的说明：本步不是 TDD 的「先红后绿」，因为被测对象是已存在的数据文件；
先红后绿从 Step 3 的跨表断言开始——那批断言**在写的时候我并不知道会不会全过**。）*

- [ ] **Step 3: 补齐剩余 11 个跨表断言**

把这 11 个方法追加到上面的类里（**只加方法，不动已有内容**）：

```java
    @Test
    @DisplayName("diagnosis_drug 引用的每个 drug_id 都真实存在")
    void everyMappedDrugExists() {
        Set<Long> ids = drugIdToName().keySet();
        List<String> bad = allRows("diagnosis_drug").stream()
                .filter(r -> !ids.contains(num(r, "drug_id")))
                .map(r -> r.get("drug_id")).collect(Collectors.toList());
        assertTrue(bad.isEmpty(), "指向不存在的药: " + bad);
    }

    @Test
    @DisplayName("三张映射表的 (科室, ICD) 组合都出现在 dept_diagnosis 里")
    void mappingsAnchorOnKnownDiagnoses() {
        Set<String> known = allRows("dept_diagnosis").stream()
                .map(r -> r.get("department") + "|" + r.get("icd_code")).collect(Collectors.toSet());
        for (String table : List.of("diagnosis_drug", "diagnosis_lab", "diagnosis_operation")) {
            List<String> bad = allRows(table).stream()
                    .map(r -> r.get("department") + "|" + r.get("icd_code"))
                    .filter(k -> !known.contains(k)).distinct().collect(Collectors.toList());
            assertTrue(bad.isEmpty(), table + " 锚到了不存在的诊断: " + bad);
        }
    }

    @Test
    @DisplayName("drug_dose 覆盖 diagnosis_drug 的引用集与原 12 种药")
    void drugDoseCoversReferencedSet() {
        Set<Long> dosed = allRows("drug_dose").stream()
                .map(r -> num(r, "drug_id")).collect(Collectors.toSet());
        Set<Long> referenced = allRows("diagnosis_drug").stream()
                .map(r -> num(r, "drug_id")).collect(Collectors.toSet());
        Set<Long> missing = new TreeSet<>(referenced);
        missing.removeAll(dosed);
        assertTrue(missing.isEmpty(), "被处方却没剂量规则的药: " + missing);
        for (long id = 1; id <= 12; id++) {
            assertTrue(dosed.contains(id), "原 12 种药缺剂量规则: " + id);
        }
    }

    @Test
    @DisplayName("drug_dose 的 单次×每日次数 不超过日上限")
    void drugDoseRespectsDailyCap() {
        Map<String, Integer> times = Map.of(
                "每日1次", 1, "每日2次", 2, "每日3次", 3, "每日4次", 4, "每晚1次", 1);
        for (Map<String, String> r : allRows("drug_dose")) {
            String freq = r.get("frequency");
            assertTrue(times.containsKey(freq), "频次不在取值域: " + freq + " (" + r.get("drug_id") + ")");
            BigDecimal perDay = dec(r, "single_dose").multiply(BigDecimal.valueOf(times.get(freq)));
            assertTrue(perDay.compareTo(dec(r, "max_daily_dose")) <= 0,
                    "药 " + r.get("drug_id") + " 日剂量 " + perDay + " 超过上限 " + r.get("max_daily_dose"));
            assertTrue(num(r, "days_min") >= 1, "疗程下限小于 1 天: " + r.get("drug_id"));
            assertTrue(num(r, "days_min") <= num(r, "days_max"),
                    "疗程区间倒挂: " + r.get("drug_id"));
        }
    }

    @Test
    @DisplayName("drug_dose 与 rational_rule(rule_type=2) 的日上限一致")
    void drugDoseAgreesWithRationalRule() {
        Map<Long, BigDecimal> ruleCap = new LinkedHashMap<>();
        for (Map<String, String> r : allRows("rational_rule")) {
            if (num(r, "rule_type") == 2) ruleCap.put(num(r, "drug_id"), dec(r, "max_daily_dose"));
        }
        assertFalse(ruleCap.isEmpty(), "rational_rule 里没有 rule_type=2 的剂量规则");
        ruleCap.forEach((drugId, cap) -> {
            BigDecimal doseCap = allRows("drug_dose").stream()
                    .filter(r -> num(r, "drug_id") == drugId)
                    .map(r -> dec(r, "max_daily_dose")).findFirst().orElse(null);
            assertEquals(0, cap.compareTo(doseCap),
                    "药 " + drugId + " 两张表的日上限不一致: rational_rule=" + cap + ", drug_dose=" + doseCap);
        });
    }

    @Test
    @DisplayName("【禁忌】儿科用药不含喹诺酮类，也不含阿司匹林")
    void pediatricContraindications() {
        List<Long> forbidden = List.of(26L, 27L, 28L, 29L, 9L);
        List<String> bad = allRows("diagnosis_drug").stream()
                .filter(r -> "儿科".equals(r.get("department")))
                .filter(r -> forbidden.contains(num(r, "drug_id")))
                .map(r -> r.get("icd_code") + "→" + r.get("drug_id")).collect(Collectors.toList());
        assertTrue(bad.isEmpty(), "儿科禁忌药被映射: " + bad);
    }

    @Test
    @DisplayName("【禁忌】青光眼不含抗胆碱能药")
    void glaucomaContraindication() {
        List<Long> anticholinergic = List.of(71L, 140L, 141L, 93L);
        List<String> bad = allRows("diagnosis_drug").stream()
                .filter(r -> "H40.9".equals(r.get("icd_code")))
                .filter(r -> anticholinergic.contains(num(r, "drug_id")))
                .map(r -> r.get("drug_id")).collect(Collectors.toList());
        assertTrue(bad.isEmpty(), "青光眼映射了抗胆碱能药: " + bad);
    }

    @Test
    @DisplayName("rule_config 的既有诊断全部进了 dept_diagnosis")
    void ruleConfigDiagnosesAreCovered() {
        Set<String> covered = allRows("dept_diagnosis").stream()
                .map(r -> r.get("icd_code")).collect(Collectors.toSet());
        for (Map<String, String> r : allRows("rule_config")) {
            assertTrue(covered.contains(r.get("diagnosis_code")),
                    "rule_config 的诊断 " + r.get("diagnosis_code") + " 不在 dept_diagnosis 中，"
                            + "换数据源后它会掉进 unchecked");
        }
    }

    @Test
    @DisplayName("rule_config 的每个 drug_ids 成员都在新映射里保留了")
    void ruleConfigDrugsSurviveTheSwitch() {
        Set<String> mapped = allRows("diagnosis_drug").stream()
                .map(r -> r.get("department") + "|" + r.get("icd_code") + "|" + r.get("drug_id"))
                .collect(Collectors.toSet());
        for (Map<String, String> rc : allRows("rule_config")) {
            String icd = rc.get("diagnosis_code");
            for (String drugId : rc.get("drug_ids").split(",")) {
                String want = drugId.trim();
                boolean found = allRows("dept_diagnosis").stream()
                        .filter(dd -> icd.equals(dd.get("icd_code")))
                        .anyMatch(dd -> mapped.contains(dd.get("department") + "|" + icd + "|" + want));
                assertTrue(found, "rule_config " + icd + " 的药 " + want + " 在新映射里丢了");
            }
        }
    }

    @Test
    @DisplayName("diagnosis_operation 同一 (科室,ICD) 的概率之和不超过 1")
    void operationProbabilitiesSumWithinOne() {
        Map<String, BigDecimal> sum = new LinkedHashMap<>();
        for (Map<String, String> r : allRows("diagnosis_operation")) {
            sum.merge(r.get("department") + "|" + r.get("icd_code"), dec(r, "probability"), BigDecimal::add);
        }
        sum.forEach((key, s) -> assertTrue(s.compareTo(BigDecimal.ONE) <= 0,
                key + " 的术式概率之和 " + s + " 超过 1"));
    }

    @Test
    @DisplayName("非慢病的患病率为 0，慢病不少于 10 种")
    void prevalenceOnlyOnChronicDiseases() {
        List<Map<String, String>> rows = allRows("dept_diagnosis");
        List<String> bad = rows.stream()
                .filter(r -> num(r, "chronic") == 0)
                .filter(r -> dec(r, "prevalence").compareTo(BigDecimal.ZERO) != 0)
                .map(r -> r.get("icd_code")).collect(Collectors.toList());
        assertTrue(bad.isEmpty(), "非慢病却有患病率: " + bad);
        long chronic = rows.stream().filter(r -> num(r, "chronic") == 1).count();
        assertTrue(chronic >= 10, "慢病数 " + chronic + " 少于 spec §9.2 要求的 10");
    }
```

- [ ] **Step 4: 跑，确认 16 个断言全绿**

Run: `cd backend && mvn -o -pl his-generator -Dtest=SeedSqlConsistencyTest test`

Expected: `Tests run: 15, Failures: 0, Errors: 0`

**这一步的失败是真实的失败**——它意味着 Task 3~8 的某份手写数据自相矛盾。
按报错信息里的药品 id / ICD 回查到对应任务修数据，**不要改断言来迁就数据**。
若某条断言的确立不住（例如 `rule_config` 里某个历史映射在医学上本就该废掉），
在 `AUTHOR-REVIEW-phase3.md` 里写明「哪条、为什么废、替代映射是什么」，再改断言。

- [ ] **Step 5: 扩展 `verify-final.sql`**

先**删掉第 43-44 行**那条断言。它现在是这样的：

```sql
SELECT '处方用药超出该诊断允许范围(应 0)' AS item,
       SUM(FIND_IN_SET(i.drug_id, rc.drug_ids) = 0) AS violations
FROM ...
```

这条断言的判定依据是 `rule_config.drug_ids`，而三期之后**适应症允许集来自
`diagnosis_drug`**，不再是 `rule_config`——留着它会同时犯两个错：
对新增的 64 个诊断一律判「超出范围」（假阳性），
对真实违规又可能因为 `rule_config` 恰好覆盖而漏判（假阴性）。
**它是本任务里唯一一处「删除既有断言」的动作，删除的理由必须写进 commit message。**

然后在文件末尾追加本体断言段（与 Task 9 的 16 个 JUnit 断言一一对应），
供作者在不装 Maven 的机器上直接验库：

```sql
-- ============ 三期：本体一致性 ============
SELECT '药品数(>=150)' AS item, COUNT(*) AS v FROM drug
UNION ALL SELECT 'dept_diagnosis(72)', COUNT(*) FROM dept_diagnosis
UNION ALL SELECT '不同检验项目数(30~50)', COUNT(DISTINCT item_code) FROM diagnosis_lab
UNION ALL SELECT '不同术式数(15~25)', COUNT(DISTINCT operation_code) FROM diagnosis_operation
UNION ALL SELECT 'drug_dose(142)', COUNT(*) FROM drug_dose
UNION ALL SELECT '慢病数(>=10)', COUNT(*) FROM dept_diagnosis WHERE chronic = 1
UNION ALL SELECT '非慢病患病率非0(应 0)', COUNT(*) FROM dept_diagnosis
  WHERE chronic = 0 AND prevalence <> 0
UNION ALL SELECT '用药映射引用不存在的药(应 0)', COUNT(*) FROM diagnosis_drug dg
  WHERE NOT EXISTS (SELECT 1 FROM drug d WHERE d.id = dg.drug_id)
UNION ALL SELECT '映射锚到不存在的诊断(应 0)', COUNT(*) FROM (
  SELECT department, icd_code FROM diagnosis_drug
  UNION SELECT department, icd_code FROM diagnosis_lab
  UNION SELECT department, icd_code FROM diagnosis_operation) x
  WHERE NOT EXISTS (SELECT 1 FROM dept_diagnosis dd
    WHERE dd.department = x.department AND dd.icd_code = x.icd_code)
UNION ALL SELECT '被处方却无剂量规则(应 0)', COUNT(*) FROM (
  SELECT DISTINCT drug_id FROM diagnosis_drug) dg
  WHERE NOT EXISTS (SELECT 1 FROM drug_dose dd WHERE dd.drug_id = dg.drug_id)
UNION ALL SELECT '原12种药缺剂量规则(应 0)', COUNT(*) FROM (
  SELECT id FROM drug WHERE id <= 12) d
  WHERE NOT EXISTS (SELECT 1 FROM drug_dose dd WHERE dd.drug_id = d.id)
UNION ALL SELECT 'drug_dose 与 rational_rule 日上限不一致(应 0)', COUNT(*) FROM drug_dose dd
  JOIN rational_rule rr ON rr.rule_type = 2 AND rr.drug_id = dd.drug_id
  WHERE rr.max_daily_dose <> dd.max_daily_dose
UNION ALL SELECT '【禁忌】儿科喹诺酮/阿司匹林(应 0)', COUNT(*) FROM diagnosis_drug
  WHERE department = '儿科' AND drug_id IN (26,27,28,29,9)
UNION ALL SELECT '【禁忌】青光眼抗胆碱能(应 0)', COUNT(*) FROM diagnosis_drug
  WHERE icd_code = 'H40.9' AND drug_id IN (71,140,141,93)
UNION ALL SELECT 'rule_config 诊断未覆盖(应 0)', COUNT(*) FROM rule_config rc
  WHERE NOT EXISTS (SELECT 1 FROM dept_diagnosis dd WHERE dd.icd_code = rc.diagnosis_code)
UNION ALL SELECT '同一(科室,ICD)术式概率和>1(应 0)', COUNT(*) FROM (
  SELECT department, icd_code, SUM(probability) s FROM diagnosis_operation
  GROUP BY department, icd_code HAVING s > 1) z;
```

Run: `mysql -uroot -p123456 --default-character-set=utf8mb4 his_demo < backend/sql/verify-final.sql`

Expected: 前 6 行是计数（应等于括号里的值），**其后每一行都是 0**。

- [ ] **Step 6: 写作者审校单 `backend/sql/AUTHOR-REVIEW-phase3.md`**

这份单据是**给你的，不是给我的**。库里现在有 196 种药、72 条诊断、50 个检验项目、
20 个术式、约 360 条用药映射和 142 条剂量规则，**没有一条是你确认过的**。
spec §9.1 定的是「由我方起草、注明来源，提交作者通读审校后入库」——
本步就是把那个「审校」变成一份可签收的清单，而不是一句口头承诺。

```markdown
# 三期种子数据 作者审校单

> 用途：spec §9.1 规定本批医学数据「由我方按来源起草，提交作者通读审校后入库」。
> 本单是审校的凭据。**逐表签收，未签收的表在论文里不得声称为「已审校」**。
> 机器能查的部分（引用完整性、禁忌、概率和、剂量不等式）已由
> `SeedSqlConsistencyTest`（15 条）与 `verify-final.sql` 守住，**不必手工复核**。
> 本单只列**机器查不了的**——也就是「这条医学内容对不对」。

## 0. 先跑机器检查（不改数据，只读）

```bash
cd backend && mvn -o -pl his-generator -Dtest=SeedSqlConsistencyTest test   # 应 15 过 0 败
mysql -uroot -p123456 --default-character-set=utf8mb4 his_demo < backend/sql/verify-final.sql
```

## 1. 药品字典 196 种（Task 3）

- [ ] **科室够用**：确认六个科室都有可开的药（旧版只有 12 种，是「跨科室乱开药」的直接成因）
- [ ] **抽查 10 种**：通用名、剂型、规格与你熟悉的实际用药一致
- [ ] **价格**：确认接受「演示用模拟零售价」这个标注（**不是**中标价，论文中如实说明）
- [ ] **甲氨蝶呤（id 109）**：确认接受「收录进字典但不参与任何映射、无剂量规则」，
      理由是每周给药超出本模型频次域（计划 Task 8 注 ②）
- 已知缺口，**需你裁决**：目录未收录生物制剂、抗肿瘤药、麻醉与急救药品

## 2. 科室↔诊断本体 72 条（Task 4）

- [ ] **科室归属**：逐条看有没有「放错科室」的诊断（这是本次改造的核心目标，请重点看）
- [ ] **`admission_eligible`**：确认 46 条可住院诊断里没有该门诊化的（皮肤科现为 2：甲沟炎/蜂窝织炎。
  这个数与 Task 4 Step 4 的实测输出一致——**以那里的实际输出为准**，若实测不是 46/2，
  改这里的数字并在审校单上写明偏差）
- [ ] **ICD-10 编码本身（重要）**：72 行里 **62 行已按 ICD-10 国家临床版核对，余 10 行未经官方版核对**
      ——起草时执行环境里没有任何官方码表。Task 4 的实现者已把这个进度**如实**写进 commit message（`ICD 编码核对：62/72`），
      并把 10 条连同理由写进 `seed-ontology.sql` 的注释块，可就地裁定。
      其中 **2 条是明确错误**（不是「不够精确」，是编码指向了另一种病），请优先处理：
      - `L60.0 甲沟炎`：L60.0 在 ICD-10 里是「嵌甲」（ingrowing nail），且 L60 类目的 `Excludes2` 明确排除甲沟炎；
        国家临床版的甲沟炎应为 **L03.003**
      - `B35.9 体癣`：B35.9 是「未特指的皮肤癣菌病」；体癣（tinea corporis）应为 **B35.400**
      另有 4 条名称与亚目不精确（`K21.0` 胃食管反流病、`K52.9` 小儿腹泻病、`N92.6` 月经失调、`S61.0` 手指开放性伤口），
      4 条停在 3 位类目未展开（`E04.1` 甲状腺结节、`I48`、`N72`、`C73`）。
      **改码不是只改一行**：`icd_code` 同时是 Task 5 的检验组键、Task 7 的用药映射键和 Task 13 生成器的取样键，
      故必须「本计划文本 + `seed-ontology.sql` 一起改」，且**改在 Task 5/7 落地之前最省事**（之后就要同步改三处数据）。
- [ ] **`prevalence`**：**这是假设值，spec §9 无逐病患病率出处**。
      确认接受，或给出你找到的出处数据
- [ ] **`gender` 只有 0/1/2 三值带来的表达力上限（需你裁决）**：本列只能表达「不限 / 仅男 / 仅女」，
      表达不了「以女性为主」。故起草时把**内科 `E05.9` 甲亢、外科 `E04.1` 甲状腺结节、外科 `C73` 甲状腺恶性肿瘤
      硬编码为 `2 仅女`**，把**两行 `K40.9` 腹股沟疝硬编码为 `1 仅男`**。
      后果是可以预见的：生成数据里**不会出现男性甲亢/男性甲状腺结节，不会出现女性腹股沟疝**。
      这与流行病学不符（甲亢女性:男性约 5~10:1，腹股沟疝男性占绝大多数但女性并非零）。
      三个选择，请挑一个：
      1. **接受现状**（最简单：这三个病在演示数据里性别单一，目录里如实写着 `仅女`/`仅男`）；
      2. 把 `gender` 改为 `0`（不限）—— 代价是女性占比会掉到 50% 左右，比现状离现实更远；
      3. 给 `gender` 加一个「倾向」维度（如新增 `female_ratio` 列或把 `gender` 扩为 `0不限/1仅男/2仅女/3偏女/4偏男`）——
         代价最大：`OntologyRules.ageGenderMatches`、校验器、生成器抽样三处要同步改，且 `DeptDiagnosis.gender`
         的域变宽后，Task 2 已过评审的实体与 Task 11 的校验都要重开一轮。
      **默认按 1 处理**（不改），所以你不答复也不阻塞后续任务；但论文里若声称「性别分布符合流行病学」，
      必须先解决这一条。
- [ ] **`season`**：冬夏/春秋/全年的归类是否符合你的临床印象

## 3. 检验本体 50 项（Task 5）

- [ ] **外科类诊断的术前检查**：确认血常规、凝血、感染筛查（乙肝/梅毒/HIV）、心电图
      确实都是 `required=1`（机器已断言，请你确认这个组合本身正确）
- [ ] **`abnormal_prob`**：**假设值**。抽查 10 个诊断，确认异常率的量级符合直觉

## 4. 手术本体 20 术式（Task 6）

- [ ] **术式名称与编码**：逐条核对《手术操作分类代码》
- [ ] **`min_postop_days`**：确认与 1~4 级手术的分级对应；**白内障被定到 2 天
      （低于其 3 级对应的 3 天），这是有意偏离 spec 映射的一处**，需你确认或改回
- [ ] **`probability`**：**假设值**。重点看儿科腹股沟疝 0.060 与外科 K40.9 0.900 的对比是否合理

## 5. 用药映射 约 360 条（Task 7）

- [ ] **一线/二线/三线的划分**：抽查每个科室 2~3 个诊断
- [ ] **`postop=1` 的标记**：确认「术后才用」的药没有被标成术前
- [ ] **儿科**：确认除喹诺酮与阿司匹林外，没有其他你认得的儿童禁忌
- [ ] **青光眼（H40.9）**：确认除抗胆碱能外无其他禁忌

## 6. 剂量规则 142 条（Task 8）

- [ ] **抽查 15 种常用药**：单次剂量与频次是否与说明书一致
- [ ] **外用/滴眼/鼻喷（`dose_unit='次'`）**：确认接受「日上限 = 给药次数上限」
      这个建模妥协（计划 Task 8 注 ①），论文中如实披露
- [ ] **硝苯地平 2 片/日、二甲双胍 4 片/日**：确认收紧后的上限正确
      （旧值 8 片/日过松，是 spec §4.4 点名的缺陷）

## 7. 签收

| 表 | 审校人 | 日期 | 结论（通过 / 修改后通过 / 打回） | 备注 |
|---|---|---|---|---|
| drug (196) | | | | |
| dept_diagnosis (72) | | | | |
| diagnosis_lab | | | | |
| diagnosis_operation (22) | | | | |
| diagnosis_drug | | | | |
| drug_dose (142) | | | | |
| visit_rate (12) | | | | |

**未签收的表**：在论文 §「数据来源与校验」中标注「未经临床审校，演示用途」，
不得写成「已由临床人员审校」。
```

- [ ] **Step 7: Commit**

```bash
git add backend/his-generator/src/test/java/com/his/generator/SeedSqlConsistencyTest.java \
        backend/sql/verify-final.sql backend/sql/AUTHOR-REVIEW-phase3.md
git commit -m "test(sql): 15 条种子一致性断言入 JUnit，附作者审校单

verify-final.sql 删除锚定 rule_config.drug_ids 的适应症断言：
三期的适应症允许集改由 diagnosis_drug 提供，旧断言对新增的 64 个诊断
一律假阳性、对真实违规又可能假阴性。替代断言见同文件新增的本体一致性段。" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

> **作者的审校门（本任务结束后、Task 10 开始前）**
> Step 6 的清单要真的走一遍。spec §9.1 写的是「提交作者通读审校后入库」——
> 本阶段后面的校验器与生成器都建立在这批数据上，**数据错了，后面全错**。
> 但这不是「卡住不动」：Task 10~12（校验器与汇总）是纯代码，与数据内容无关，
> **审校与它们可以并行**；Task 13 起（患者疾病轨迹）才真正依赖数据被认可。

---

## Task 10: `RationalDrugValidator` 换数据源 + 增 `unchecked`（阶段一）

**Files:**
- Modify: `backend/his-common/src/main/java/com/his/common/dto/RationalDrugReport.java`
- Create: `backend/his-common/src/main/java/com/his/common/ontology/OntologyRules.java`
- Modify: `backend/his-server/src/main/java/com/his/server/service/validation/RationalDrugValidator.java`
- Test: `backend/his-server/src/test/java/com/his/server/service/validation/RationalDrugValidatorTest.java`
- Test: `backend/his-server/src/test/java/com/his/server/service/validation/OntologyRulesTest.java`
- Modify: `backend/his-generator/src/test/java/com/his/generator/SeedSqlConsistencyTest.java`
  （Step 7：把本地频次表换成 `OntologyRules.timesPerDay`；Task 9 早于本任务，那一步写不了这个引用）

**Interfaces:**
- Consumes: Task 2 的 `DiagnosisDrugMapper`、`DrugDoseMapper`；既有 `VisitMapper`
  （`diagnosis` 表没有 `department` 列，就诊科室只能从 `visit` 取）
- Produces: `RationalDrugReport.unchecked`（`long`）；`RationalDrugValidator` 新构造签名
  `(PrescriptionMapper, PrescriptionItemMapper, DiagnosisMapper, VisitMapper, DiagnosisDrugMapper, RationalRuleMapper, DrugDoseMapper)`；
  包级静态方法 `static BigDecimal firstDecimal(String)`。
  **新增 `com.his.common.ontology.OntologyRules`（`public`）**，含
  `select` / `ageGenderMatches` / `timesPerDay` 三个静态方法 ——
  前两个是本体匹配规则、`timesPerDay` 是频次取值域，**都是全项目的唯一实现**。
  Task 11/12/19 依赖 `unchecked` 字段；**Task 16 依赖 `timesPerDay`**。

  **本类只提供 `select`（取适用行），不提供「取适用行 + 判某值在不在里面」的合并方法。**
  合并版（曾拟名 `covers`）在**适用行为空**时返回 `false`，与「适用行里没有这个值」
  不可区分——而这两件事在语义上一个该报 `unchecked`、一个该报违规。
  本项目的调用方（Task 11 的检验判、Task 24 的手术判）都必须先 `select` 拿到列表、
  自己判 `isEmpty()`，再判含量。**合并版已在评审中删除，不要重新引入。**

**`OntologyRules` 为什么放在 `his-common` 而不是 `his-server`：**
科室优先级匹配与年龄性别判定**校验器与生成器都要用**——校验器拿它判违规，
生成器拿它挑「这个科室这个年龄能看的诊断」。而模块依赖是
`his-generator → his-common`、`his-server → his-common`，**两者互不依赖**：
放进 `his-server` 生成器就用不到，各写一份则必然在改规则时漏改。`his-common` 是唯一共同祖先，
且这个类只操作 `his-common` 自己的实体、不依赖 Spring，放这里没有任何代价。

**为什么必须做**（spec §8.1）：现有适应症检查只在诊断命中 `rule_config` 时才执行，
`rule_config` 只有 8 个诊断。本次扩到 60~80 个诊断后，若继续读 `rule_config`，
**绝大多数处方会被静默跳过**，「合理用药率」会变成没检查过的 100%。

**为什么构造器还要多一个 `VisitMapper`：** 适应症允许集是按 `(科室, 诊断)` 建的（Task 1 的
`department` 列），而 `diagnosis` 表**没有科室列**，就诊科室只在 `visit.department` 上。
不读 `visit` 就没法用 `department`，只能退回「按 ICD 匹配」——那样
「同一个 J06.9 在儿科与在内科允许的药不同」（Task 7 的核心诉求）就落不了地。

**`unchecked` 的两种含义，必须分清**（否则会把「未覆盖」误读成「没问题」）：

| 情况 | Task 10 的处理 | 谁来判违规 |
|---|---|---|
| 诊断编码完全不在 `diagnosis_drug` 中 | 计入 `unchecked` | 无人判——本体没收录，只能如实报「没检查」 |
| 诊断在体中，但**没有该科室的行**（如妇科开 J06.9） | 计入 `unchecked` | **Task 11 的 DEPT_DIAGNOSIS 检查** |

第二行是有意的职责划分：「妇科看肠胃炎」这件事**本身**就是错的，
该由「科室↔诊断」那类检查判违规，不该在用药校验里再判一次、也不该在这里静默通过。
两个校验器合起来看，这类就诊既被 `unchecked` 标记、又被 DEPT_DIAGNOSIS 判违规，不会漏。

**构造签名变化会打断既有测试**：`RationalDrugValidatorTest.validator()` 目前按位置传
`new RationalDrugValidator(pm, im, dm, rcm, rrm)`。第 4 个参数的类型由 `RuleConfigMapper` 换成
`VisitMapper`、第 5 个换成 `DiagnosisDrugMapper`，编译即失败——这正是我们要的，Step 1 一并改掉。

- [ ] **Step 1: 改测试（先写失败测试）**

把 `RationalDrugValidatorTest` 里 `setUp` 与 `validator()` 换成：

```java
    private PrescriptionMapper pm;
    private PrescriptionItemMapper im;
    private DiagnosisMapper dm;
    private VisitMapper vm;
    private DiagnosisDrugMapper ddgm;
    private RationalRuleMapper rrm;
    private DrugDoseMapper ddm;

    @BeforeEach
    void setUp() {
        pm = mock(PrescriptionMapper.class);
        im = mock(PrescriptionItemMapper.class);
        dm = mock(DiagnosisMapper.class);
        vm = mock(VisitMapper.class);
        ddgm = mock(DiagnosisDrugMapper.class);
        rrm = mock(RationalRuleMapper.class);
        ddm = mock(DrugDoseMapper.class);
    }

    private RationalDrugValidator validator() {
        return new RationalDrugValidator(pm, im, dm, vm, ddgm, rrm, ddm);
    }

    /** 造一条处方 + 一条明细 + 一条诊断 + 一次就诊（科室），减少各用例的样板。 */
    private void stub(Prescription p, PrescriptionItem item, String icdCode, String department) {
        when(pm.selectList(any())).thenReturn(List.of(p));
        when(im.selectList(any())).thenReturn(List.of(item));
        Diagnosis d = new Diagnosis();
        d.setVisitId(p.getVisitId());
        d.setIcdCode(icdCode);
        when(dm.selectList(any())).thenReturn(List.of(d));
        Visit v = new Visit();
        v.setId(p.getVisitId());
        v.setDepartment(department);
        when(vm.selectList(any())).thenReturn(List.of(v));
    }

    private Prescription prescription(long id, long visitId) {
        Prescription p = new Prescription();
        p.setId(id);
        p.setPatientId(1L);
        p.setVisitId(visitId);
        return p;
    }

    private PrescriptionItem item(long prescriptionId, long drugId, String dosage, String frequency) {
        PrescriptionItem it = new PrescriptionItem();
        it.setPrescriptionId(prescriptionId);
        it.setDrugId(drugId);
        it.setDrugName("药" + drugId);
        it.setDosage(dosage);
        it.setFrequency(frequency);
        return it;
    }

    /** department 传 null 表示「该诊断不限科室」的行。 */
    private DiagnosisDrug diagDrug(String icdCode, String department, long drugId) {
        DiagnosisDrug dd = new DiagnosisDrug();
        dd.setIcdCode(icdCode);
        dd.setDepartment(department);
        dd.setDrugId(drugId);
        dd.setLine(1);
        return dd;
    }

    private DrugDose drugDose(long drugId, String single, String maxDaily) {
        DrugDose d = new DrugDose();
        d.setDrugId(drugId);
        d.setSingleDose(new BigDecimal(single));
        d.setMaxDailyDose(new BigDecimal(maxDaily));
        d.setDoseUnit("片");
        d.setFrequency("每日1次");
        return d;
    }
```

`setUp` 里需要 `lenient()` 兜底（沿用本项目既有做法，Mockito 严格桩会报未使用）：

```java
        lenient().when(im.selectList(any())).thenReturn(List.of());
        lenient().when(dm.selectList(any())).thenReturn(List.of());
        lenient().when(vm.selectList(any())).thenReturn(List.of());
        lenient().when(ddgm.selectList(any())).thenReturn(List.of());
        lenient().when(rrm.selectList(any())).thenReturn(List.of());
        lenient().when(ddm.selectList(any())).thenReturn(List.of());
```

三个既有用例改写，并新增五个：

```java
    @Test
    void flags_indication_mismatch() {
        Prescription p = prescription(1L, 10L);
        stub(p, item(1L, 99L, "1片", "每日1次"), "J06.9", "内科");
        when(ddgm.selectList(any())).thenReturn(List.of(
                diagDrug("J06.9", "内科", 1L), diagDrug("J06.9", "内科", 2L)));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).hasSize(1);
        assertThat(report.getViolations().get(0).getRuleType()).isEqualTo("INDICATION");
        assertThat(report.getViolationPrescriptions()).isEqualTo(1);
        assertThat(report.getRationalRate()).isEqualTo(0.0);
        assertThat(report.getUnchecked()).isZero();
    }

    @Test
    void flags_dose_exceeding_limit() {
        Prescription p = prescription(1L, 10L);
        stub(p, item(1L, 4L, "2片", "每日3次"), "I10", "内科");
        when(ddgm.selectList(any())).thenReturn(List.of(diagDrug("I10", "内科", 4L)));
        // 日剂量 2×3 = 6 片 > 上限 2 片
        when(ddm.selectList(any())).thenReturn(List.of(drugDose(4L, "1", "2")));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).extracting(RationalViolation::getRuleType).contains("DOSE");
        assertThat(report.getUnchecked()).isZero();
    }

    @Test
    void clean_prescription_has_full_rational_rate() {
        Prescription p = prescription(1L, 10L);
        stub(p, item(1L, 1L, "1片", "每日1次"), "J06.9", "内科");
        when(ddgm.selectList(any())).thenReturn(List.of(
                diagDrug("J06.9", "内科", 1L), diagDrug("J06.9", "内科", 2L)));
        when(ddm.selectList(any())).thenReturn(List.of(drugDose(1L, "1", "4")));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).isEmpty();
        assertThat(report.getRationalRate()).isEqualTo(1.0);
        assertThat(report.getUnchecked()).isZero();
    }

    @Test
    void unchecked_counts_prescriptions_whose_diagnosis_is_absent_from_ontology() {
        Prescription p = prescription(1L, 10L);
        stub(p, item(1L, 1L, "1片", "每日1次"), "Z99.9", "内科");
        // 本体里没有 Z99.9 -> 不能伪装成「通过」，必须计入 unchecked
        when(ddgm.selectList(any())).thenReturn(List.of(diagDrug("J06.9", "内科", 1L)));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).isEmpty();
        assertThat(report.getUnchecked()).isEqualTo(1);
    }

    @Test
    void unrecognized_frequency_counts_as_unchecked_not_a_violation() {
        Prescription p = prescription(1L, 10L);
        stub(p, item(1L, 4L, "2片", "bid"), "I10", "内科");
        when(ddgm.selectList(any())).thenReturn(List.of(diagDrug("I10", "内科", 4L)));
        when(ddm.selectList(any())).thenReturn(List.of(drugDose(4L, "1", "2")));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).isEmpty();
        assertThat(report.getUnchecked()).isEqualTo(1);
    }

    @Test
    void exact_department_row_wins_over_the_null_row() {
        Prescription p = prescription(1L, 10L);
        // 儿科开 J06.9：儿科行只允许药 1，不限科室的行允许药 1 与药 2。
        // 精确匹配必须赢——用 2 应当判 INDICATION，证明走的是儿科那一行。
        stub(p, item(1L, 2L, "1片", "每日1次"), "J06.9", "儿科");
        when(ddgm.selectList(any())).thenReturn(List.of(
                diagDrug("J06.9", "儿科", 1L),
                diagDrug("J06.9", null, 1L),
                diagDrug("J06.9", null, 2L)));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).extracting(RationalViolation::getRuleType).contains("INDICATION");
        assertThat(report.getUnchecked()).isZero();
    }

    @Test
    void null_department_row_applies_when_no_exact_row_exists() {
        Prescription p = prescription(1L, 10L);
        // 该诊断在本体里只有「不限科室」的行 -> 落回退分支，药 1 合规
        stub(p, item(1L, 1L, "1片", "每日1次"), "J06.9", "内科");
        when(ddgm.selectList(any())).thenReturn(List.of(diagDrug("J06.9", null, 1L)));
        when(ddm.selectList(any())).thenReturn(List.of(drugDose(1L, "1", "4")));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).isEmpty();
        assertThat(report.getUnchecked()).isZero();
        assertThat(report.getRationalRate()).isEqualTo(1.0);
    }

    @Test
    void diagnosis_not_covering_this_department_is_unchecked_not_a_drug_violation() {
        Prescription p = prescription(1L, 10L);
        // J06.9 在本体里只有内科/儿科的行，妇科开它 -> 用药校验无从判定
        stub(p, item(1L, 1L, "1片", "每日1次"), "J06.9", "妇科");
        when(ddgm.selectList(any())).thenReturn(List.of(
                diagDrug("J06.9", "内科", 1L), diagDrug("J06.9", "儿科", 1L)));

        RationalDrugReport report = validator().validate();

        // 职责划分：「妇科看 J06.9」本身违规，由 Task 11 的 DEPT_DIAGNOSIS 判，
        // 本校验器只如实报「未覆盖」，不在这里重复判、更不静默通过
        assertThat(report.getViolations()).isEmpty();
        assertThat(report.getUnchecked()).isEqualTo(1);
    }

    @Test
    void firstDecimal_parses_the_leading_number() {
        assertThat(RationalDrugValidator.firstDecimal("1片")).isEqualByComparingTo("1");
        assertThat(RationalDrugValidator.firstDecimal("0.5片")).isEqualByComparingTo("0.5");
        assertThat(RationalDrugValidator.firstDecimal("10ml")).isEqualByComparingTo("10");
        assertThat(RationalDrugValidator.firstDecimal("每日3次")).isNull();
        assertThat(RationalDrugValidator.firstDecimal(null)).isNull();
    }
```

同一步再建第二个测试文件 `OntologyRulesTest.java`。它的目标是钉住一个**极易写错且错了不会报错**的点：
患者性别与本体性别是**两个不同的取值域**（患者 0=男/1=女，本体 0=不限/1=仅男/2=仅女）。
写成 `row.getGender() == gender` 会让「仅女」对女性为假、对男性为真，男女正好反了，
而生成器与校验器会犯同一个错、一起认为数据是对的——所以必须用测试钉住，不能靠注释。

> **测试为什么落在 `his-server` 而不是类所在的 `his-common`**：`his-common` 没有 `src/test`
> 目录、pom 里也没有任何测试依赖（纯实体/Mapper 模块）。为一个类给 `his-common` 拉一整套
> 测试基建属于超范围；`his-server` 依赖 `his-common` 且本任务的另一个测试就在同目录。

```java
package com.his.server.service.validation;

import com.his.common.entity.DeptDiagnosis;
import com.his.common.entity.DiagnosisLab;
import com.his.common.ontology.OntologyRules;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class OntologyRulesTest {

    private DeptDiagnosis row(Integer gender, Integer minAge, Integer maxAge) {
        DeptDiagnosis d = new DeptDiagnosis();
        d.setGender(gender);
        d.setMinAge(minAge);
        d.setMaxAge(maxAge);
        return d;
    }

    @Test
    @DisplayName("患者性别(0男1女)与本体性别(0不限1仅男2仅女)是两个域，必须映射后再比")
    void gender_domains_are_mapped_not_compared_directly() {
        // 仅女：女患者(1)适用、男患者(0)不适用
        assertThat(OntologyRules.ageGenderMatches(row(2, 0, 89), 30, 1)).isTrue();
        assertThat(OntologyRules.ageGenderMatches(row(2, 0, 89), 30, 0)).isFalse();
        // 仅男：男患者(0)适用、女患者(1)不适用
        assertThat(OntologyRules.ageGenderMatches(row(1, 0, 89), 30, 0)).isTrue();
        assertThat(OntologyRules.ageGenderMatches(row(1, 0, 89), 30, 1)).isFalse();
        // 不限：两者都适用
        assertThat(OntologyRules.ageGenderMatches(row(0, 0, 89), 30, 0)).isTrue();
        assertThat(OntologyRules.ageGenderMatches(row(0, 0, 89), 30, 1)).isTrue();
        // 本体行为 NULL 时按「不限」处理，不得 NPE
        assertThat(OntologyRules.ageGenderMatches(row(null, 0, 89), 30, 1)).isTrue();
    }

    @Test
    @DisplayName("原始形式与实体形式判定一致：生成器展平后的 Disease 走的就是这一条")
    void primitive_overload_agrees_with_the_entity_form() {
        // 生成器的 Disease record 不能再拼一个 DeptDiagnosis 出来复用规则，
        // 于是走这个重载；两种写法必须给出同一个答案，否则生成器与校验器又会分叉。
        int[][] cases = {{1, 30, 0}, {2, 30, 1}, {1, 30, 1}, {2, 30, 0}, {0, 30, 0}, {0, 30, 1}};
        for (int[] c : cases) {
            assertThat(OntologyRules.ageGenderMatches(c[0], 0, 89, c[1], c[2]))
                    .as("本体性别=%d 年龄=%d 患者性别=%d", c[0], c[1], c[2])
                    .isEqualTo(OntologyRules.ageGenderMatches(row(c[0], 0, 89), c[1], c[2]));
        }
    }

    @Test
    @DisplayName("年龄为闭区间；minAge/maxAge 为 null 表示不设限")
    void age_range_is_inclusive_and_null_means_unbounded() {
        assertThat(OntologyRules.ageGenderMatches(row(0, 18, 65), 18, 0)).isTrue();
        assertThat(OntologyRules.ageGenderMatches(row(0, 18, 65), 65, 0)).isTrue();
        assertThat(OntologyRules.ageGenderMatches(row(0, 18, 65), 17, 0)).isFalse();
        assertThat(OntologyRules.ageGenderMatches(row(0, 18, 65), 66, 0)).isFalse();
        assertThat(OntologyRules.ageGenderMatches(row(0, null, null), 0, 1)).isTrue();
        assertThat(OntologyRules.ageGenderMatches(row(0, null, null), 89, 0)).isTrue();
    }

    @Test
    @DisplayName("科室优先级：精确行胜过「不限科室」的行；无精确行时才回落；键不存在返回空")
    void select_prefers_the_exact_department_row() {
        DeptDiagnosis peds = row(0, 0, 89);
        peds.setDepartment("儿科");
        peds.setIcdCode("J06.9");
        DeptDiagnosis anyDept = row(0, 0, 89);
        anyDept.setDepartment(null);
        anyDept.setIcdCode("J06.9");
        List<DeptDiagnosis> rows = List.of(peds, anyDept);

        assertThat(OntologyRules.select(rows, DeptDiagnosis::getDepartment,
                DeptDiagnosis::getIcdCode, "儿科", "J06.9")).containsExactly(peds);
        // 内科没有精确行 -> 回落到「不限科室」那一行
        assertThat(OntologyRules.select(rows, DeptDiagnosis::getDepartment,
                DeptDiagnosis::getIcdCode, "内科", "J06.9")).containsExactly(anyDept);
        // 键根本不在本体里 -> 空列表，调用方必须计入 unchecked，不得当作通过
        assertThat(OntologyRules.select(rows, DeptDiagnosis::getDepartment,
                DeptDiagnosis::getIcdCode, "内科", "Z99.9")).isEmpty();
        // 科室为 null 的就诊（Task 11 会遇到）不能 NPE，同样返回空
        assertThat(OntologyRules.select(rows, DeptDiagnosis::getDepartment,
                DeptDiagnosis::getIcdCode, null, "J06.9")).isEmpty();
    }

    @Test
    @DisplayName("select 只在适用行里找，不跨科室误判")
    void select_looks_up_values_in_the_applicable_rows_only() {
        DiagnosisLab crp = new DiagnosisLab();
        crp.setIcdCode("J06.9");
        crp.setDepartment("儿科");
        crp.setItemCode("BLD-CRP");
        DiagnosisLab wbc = new DiagnosisLab();
        wbc.setIcdCode("J06.9");
        wbc.setDepartment("内科");
        wbc.setItemCode("BLD-WBC");
        List<DiagnosisLab> rows = List.of(crp, wbc);

        assertThat(OntologyRules.select(rows, DiagnosisLab::getDepartment, DiagnosisLab::getIcdCode,
                "儿科", "J06.9")).extracting(DiagnosisLab::getItemCode).containsExactly("BLD-CRP");
        // BLD-WBC 是内科那一行开的，儿科取适用行时不该看见它
        assertThat(OntologyRules.select(rows, DiagnosisLab::getDepartment, DiagnosisLab::getIcdCode,
                "儿科", "J06.9")).extracting(DiagnosisLab::getItemCode).doesNotContain("BLD-WBC");
    }
}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-server -am test \
  -Dtest=RationalDrugValidatorTest,OntologyRulesTest -Dsurefire.failIfNoSpecifiedTests=false
```

Expected: 编译失败——`OntologyRules` / `DiagnosisDrugMapper` 符号找不到、`RationalDrugValidator` 构造器参数不匹配。
三个信号都对，说明两个测试文件确实引用到了尚不存在的东西。

- [ ] **Step 3: 给 `RationalDrugReport` 加 `unchecked`**

在 `private List<RationalViolation> violations;` 之后追加：

```java
    /**
     * 无法判定的处方数。三种成因：诊断不在本体、诊断在体中但不含本科室的行、
     * 剂量或频次写法无法结构化识别。
     *
     * <p>**它不是「不通过」，也不是「通过」——它是「没检查」。** 因此
     * {@code rationalRate} 的分母仍是全部处方：未覆盖的处方不计入违规，也就默认算「合理」，
     * 这正是必须在看板上把 {@code unchecked} 和 {@code rationalRate} 一起显示的原因。
     * 阶段二重写生成器后，本值应趋近于 0；若长期不为 0，说明本体有洞，而不是数据变好了。
     */
    private long unchecked;
```

- [ ] **Step 4: 新建 `OntologyRules.java`（本体匹配规则的唯一实现）**

`diagnosis_drug`、`diagnosis_lab`、`diagnosis_operation` 三张表都带 `department` 列，
都要用同一条匹配规则；年龄性别判定校验器与生成器也都要用。写多份必然会在改规则时漏改，
所以先抽出这一个类，放在**两个模块的共同祖先** `his-common` 里。

```java
package com.his.common.ontology;

import com.his.common.entity.DeptDiagnosis;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Function;

/**
 * 医学本体匹配规则。**本类是这些规则在全项目的唯一实现**，
 * 校验器（his-server）与生成器（his-generator）都从这里取，不得各写一份。
 *
 * <p>放在 his-common 的原因：模块依赖是 generator → common、server → common，
 * 两者互不依赖；放 server 则 generator 用不到。本类只操作 his-common 自己的实体，
 * 不依赖 Spring，放在这里没有任何代价。
 */
public final class OntologyRules {

    private OntologyRules() {
    }

    /**
     * 科室优先级匹配：选出适用于 {@code (actualDept, actualKey)} 的本体行。
     *
     * <p>规则由 Task 1 定死：先取 {@code department} 精确匹配的行；无匹配时回落到
     * {@code department == null} 的行（null 表示「该诊断不限科室」）。
     * 返回空列表表示**本体覆盖不到这个 (科室, 键) 组合**，调用方必须如实计入
     * {@code unchecked} 或报「未覆盖」，**不得当作「通过」**。覆盖不到有两种成因：
     * <ol>
     *   <li>该键（ICD 编码）根本没进本体；</li>
     *   <li>键在体中，但既没有本科室的行、也没有 NULL 的行
     *       ——即「这个科室本就不该看这个病」。</li>
     * </ol>
     * 第 2 种由 {@code OntologyConsistencyValidator} 的对应检查判违规
     * （门诊诊断走 DEPT_DIAGNOSIS，手术走 OPERATION_DIAGNOSIS），本类只负责如实回报「判不了」。
     *
     * <p>当前种子数据里三张映射表的 {@code department} 全部非空，回落分支由单元测试用
     * 合成行覆盖。**不要因为数据里没有就删掉它**——它是「一个诊断不限科室」唯一的表达方式。
     *
     * @param deptOf 取一行所属科室（可能为 null，表示不限科室）
     * @param keyOf  取一行的键（如 ICD 编码）
     */
    public static <T> List<T> select(List<T> rows,
                                     Function<T, String> deptOf,
                                     Function<T, String> keyOf,
                                     String actualDept,
                                     String actualKey) {
        if (rows == null || rows.isEmpty() || actualDept == null || actualKey == null) {
            return List.of();
        }
        List<T> sameKey = new ArrayList<>();
        for (T row : rows) {
            if (actualKey.equals(keyOf.apply(row))) {
                sameKey.add(row);
            }
        }
        if (sameKey.isEmpty()) {
            return List.of();
        }
        List<T> exact = new ArrayList<>();
        for (T row : sameKey) {
            if (actualDept.equals(deptOf.apply(row))) {
                exact.add(row);
            }
        }
        if (!exact.isEmpty()) {
            return exact;
        }
        List<T> unrestricted = new ArrayList<>();
        for (T row : sameKey) {
            if (deptOf.apply(row) == null) {
                unrestricted.add(row);
            }
        }
        return unrestricted;
    }

    /**
     * 本体行是否适用于该年龄与性别。
     *
     * <p><b>两个独立的性别取值域，绝不可直接比较：</b>
     * <table border="1">
     *   <tr><th>来源</th><th>取值</th></tr>
     *   <tr><td>{@code Patient.gender}、{@code visit_rate.gender} 的 1/2 分支</td>
     *       <td>0=男 1=女</td></tr>
     *   <tr><td>{@code DeptDiagnosis.gender}</td><td>0=不限 1=仅男 2=仅女</td></tr>
     * </table>
     * 本方法的参数 {@code gender} 收的是**患者性别（0=男 1=女）**，在方法内部映射到本体域。
     * 若图省事写成 {@code row.getGender() == gender}，则「仅女」的行对女性为假、对男性为真，
     * 男女**正好反了**——而且生成器与校验器会犯同一个错，两边一起认为数据是对的，
     * 这正是把规则收进一个类要防的事，所以映射必须写在这里、只写一次。
     *
     * <p>{@code minAge}/{@code maxAge} 为闭区间，null 表示不设限。
     * 生成器用它筛「这个患者能得的诊断」，校验器用它判违规，**必须是同一个判定**——
     * 否则生成器造的数据会被校验器判违规。
     */
    public static boolean ageGenderMatches(DeptDiagnosis row, int age, int gender) {
        return ageGenderMatches(row.getGender(), row.getMinAge(), row.getMaxAge(), age, gender);
    }

    /**
     * 同上的原始形式。供**已经把本体行展平成自己的类型**的调用方使用
     * （生成器侧的 `Disease` record 就是这样，见 Task 13），
     * 免得它为了复用这条规则再拼一个 `DeptDiagnosis` 出来。
     */
    public static boolean ageGenderMatches(Integer rowGender, Integer minAge, Integer maxAge,
                                           int age, int gender) {
        int g = rowGender == null ? 0 : rowGender;
        // 患者域 0男/1女 → 本体域 1仅男/2仅女
        boolean genderOk = g == 0 || g == (gender == 0 ? 1 : 2);
        boolean ageOk = (minAge == null || age >= minAge) && (maxAge == null || age <= maxAge);
        return genderOk && ageOk;
    }

    /**
     * 频次文本 → 每日次数。
     *
     * <p><b>取值域</b>（逐字等于 {@code drug_dose.frequency}，Task 8 口径 2 锁定）：
     * {@code 每日1次} / {@code 每日2次} / {@code 每日3次} / {@code 每日4次} / {@code 每晚1次}
     * → 1 / 2 / 3 / 4 / 1。多一个键或少一个键都会让「校验器认得的频次」与
     * 「生成器写出的频次」错开——那正是 {@code parseInt("bid")} 时代错判的来源。
     *
     * <p><b>为什么收在这里而不是各写一份</b>：校验器要它算日剂量（判超量），
     * 生成器要它算疗程总量（Task 16 的 {@code packCount}）。两处各写一张 Map 的下场是
     * 生成器写得出 {@code 每日5次} 而校验器不认得它，整条处方落进 {@code unchecked}，
     * 看起来像「校验器覆盖不足」，实际是生成器越过了取值域。
     *
     * @throws IllegalStateException 取值域之外——**不返回默认值**，
     *         见三处调用的共同约定：判不了就说判不了，不猜
     */
    public static int timesPerDay(String frequency) {
        if (frequency == null) {
            throw new IllegalStateException("drug_dose.frequency 为空，无法确定每日次数");
        }
        return switch (frequency.trim()) {
            case "每日1次", "每晚1次" -> 1;
            case "每日2次" -> 2;
            case "每日3次" -> 3;
            case "每日4次" -> 4;
            default -> throw new IllegalStateException(
                    "drug_dose.frequency 取值域外（只允许 每日1~4次 / 每晚1次）: " + frequency);
        };
    }
}
```

- [ ] **Step 5: 重写 `RationalDrugValidator`**

整体替换为：

```java
package com.his.server.service.validation;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.dto.RationalDrugReport;
import com.his.common.dto.RationalViolation;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import com.his.common.ontology.OntologyRules;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.*;
import java.util.stream.Collectors;

/**
 * 合理用药校验：适应症 / 相互作用 / 剂量上限。
 *
 * <p>三类检查的数据源都来自医学本体表，不读自由文本、不依赖 rule_config。
 * <p>无法判定的记录一律计入 {@code unchecked} 显式报出，**不得静默跳过伪装成通过**。
 */
@Component
@RequiredArgsConstructor
public class RationalDrugValidator {

    private final PrescriptionMapper prescriptionMapper;
    private final PrescriptionItemMapper itemMapper;
    private final DiagnosisMapper diagnosisMapper;
    /** 就诊科室来源。diagnosis 表没有科室列，只能从 visit.department 取。 */
    private final VisitMapper visitMapper;
    /** 适应症数据源：诊断→药品本体（取代原先只有 8 条诊断的 rule_config）。 */
    private final DiagnosisDrugMapper diagnosisDrugMapper;
    private final RationalRuleMapper rationalRuleMapper;
    /** 剂量数据源：药品剂量规则；与 rational_rule.rule_type=2 的取值由一致性测试保持相等。 */
    private final DrugDoseMapper drugDoseMapper;

    public RationalDrugReport validate() {
        List<Prescription> prescriptions = prescriptionMapper.selectList(null);
        RationalDrugReport report = new RationalDrugReport();
        report.setTotalPrescriptions(prescriptions.size());
        report.setViolations(new ArrayList<>());
        if (prescriptions.isEmpty()) {
            report.setRationalRate(1.0);
            return report;
        }

        // 适应症本体：诊断编码 -> 该诊断的全部行（保留 department，供精确/回落匹配）
        Map<String, List<DiagnosisDrug>> ontologyByIcd = diagnosisDrugMapper.selectList(null).stream()
                .filter(dd -> dd.getIcdCode() != null && dd.getDrugId() != null)
                .collect(Collectors.groupingBy(DiagnosisDrug::getIcdCode));

        // 日剂量上限：drug_id -> drug_dose
        Map<Long, DrugDose> doseByDrug = new HashMap<>();
        for (DrugDose d : drugDoseMapper.selectList(null)) {
            if (d.getDrugId() != null) doseByDrug.put(d.getDrugId(), d);
        }

        // 相互作用：(min,max) 规范化后的药对 -> 规则
        Map<String, RationalRule> interactions = new HashMap<>();
        for (RationalRule r : rationalRuleMapper.selectList(
                new LambdaQueryWrapper<RationalRule>().eq(RationalRule::getEnabled, 1))) {
            if (r.getRuleType() != null && r.getRuleType() == 1
                    && r.getDrugAId() != null && r.getDrugBId() != null) {
                long a = Math.min(r.getDrugAId(), r.getDrugBId());
                long b = Math.max(r.getDrugAId(), r.getDrugBId());
                interactions.put(a + "-" + b, r);
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

        // 就诊科室：visit_id -> department。空串代表该次就诊没记科室，
        // 它既不会匹配到精确行、也不会匹配到 NULL 行，自然落入 unchecked。
        Map<Long, String> visitDepartment = new HashMap<>();
        for (Visit v : visitMapper.selectList(new LambdaQueryWrapper<Visit>()
                .in(Visit::getId, prescriptions.stream().map(Prescription::getVisitId)
                        .filter(Objects::nonNull).collect(Collectors.toSet())))) {
            visitDepartment.put(v.getId(), v.getDepartment() == null ? "" : v.getDepartment());
        }

        long unchecked = 0;
        Set<Long> violatingPrescriptions = new HashSet<>();
        for (Prescription p : prescriptions) {
            List<PrescriptionItem> its = itemsByPrescription.getOrDefault(p.getId(), List.of());
            if (its.isEmpty()) continue;
            List<Long> drugIds = its.stream().map(PrescriptionItem::getDrugId)
                    .filter(Objects::nonNull).distinct().toList();
            String diagCode = diagnosisCodeByVisit.get(p.getVisitId());
            String dept = visitDepartment.get(p.getVisitId());

            // 适应症：本体未覆盖该 (科室, 诊断) -> 计入 unchecked，不伪装成通过。
            // 「诊断在体中但没有该科室的行」也走这一支——那类错误由 Task 11 的
            // DEPT_DIAGNOSIS 判违规，本校验器不重复判、也不静默通过。
            List<DiagnosisDrug> applicable = OntologyRules.select(
                    ontologyByIcd.get(diagCode),
                    DiagnosisDrug::getDepartment, DiagnosisDrug::getIcdCode, dept, diagCode);
            if (applicable.isEmpty()) {
                unchecked++;
            } else {
                Set<Long> allowed = applicable.stream()
                        .map(DiagnosisDrug::getDrugId).collect(Collectors.toSet());
                for (PrescriptionItem it : its) {
                    if (it.getDrugId() != null && !allowed.contains(it.getDrugId())) {
                        report.getViolations().add(violation(p, "INDICATION", 2,
                                "药品与诊断不符：" + dept + " 的 " + diagCode
                                        + " 未关联药品 id=" + it.getDrugId()));
                        violatingPrescriptions.add(p.getId());
                    }
                }
            }

            // 相互作用：与诊断是否被本体覆盖无关，照常判定
            for (int i = 0; i < drugIds.size(); i++) {
                for (int j = i + 1; j < drugIds.size(); j++) {
                    long a = Math.min(drugIds.get(i), drugIds.get(j));
                    long b = Math.max(drugIds.get(i), drugIds.get(j));
                    RationalRule r = interactions.get(a + "-" + b);
                    if (r != null) {
                        report.getViolations().add(
                                violation(p, "INTERACTION", r.getWarningLevel(), r.getMessage()));
                        violatingPrescriptions.add(p.getId());
                    }
                }
            }

            // 剂量上限：按 drug_dose 的结构化字段判定，不再解析自由文本
            for (PrescriptionItem it : its) {
                if (it.getDrugId() == null) continue;
                DrugDose dd = doseByDrug.get(it.getDrugId());
                if (dd == null) continue;
                BigDecimal single = firstDecimal(it.getDosage());
                Integer times;
                try {
                    // 取值域由 OntologyRules 单点维护（生成器 Task 16 用的是同一个方法）
                    times = OntologyRules.timesPerDay(it.getFrequency());
                } catch (IllegalStateException e) {
                    times = null;
                }
                if (single == null || times == null) {
                    // 剂量或频次写法无法结构化识别 -> 无法判定，显式计入 unchecked
                    // （timesPerDay 对域外取值抛异常，这里接住并如实计入，不让一个坏行中断整轮校验）
                    unchecked++;
                    continue;
                }
                BigDecimal daily = single.multiply(BigDecimal.valueOf(times));
                if (dd.getMaxDailyDose() != null && daily.compareTo(dd.getMaxDailyDose()) > 0) {
                    report.getViolations().add(violation(p, "DOSE", 1,
                            "超日剂量上限：" + it.getDrugName()
                                    + " 日剂量 " + daily.stripTrailingZeros().toPlainString()
                                    + " > 上限 " + dd.getMaxDailyDose().stripTrailingZeros().toPlainString()));
                    violatingPrescriptions.add(p.getId());
                }
            }
        }

        report.setUnchecked(unchecked);
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

    /** 取字符串里第一段数字（允许小数点），如 "1片" -> 1、"0.5片" -> 0.5；没有数字返回 null。 */
    static BigDecimal firstDecimal(String s) {
        if (s == null) return null;
        StringBuilder sb = new StringBuilder();
        boolean dot = false;
        for (char c : s.toCharArray()) {
            if (Character.isDigit(c)) {
                sb.append(c);
            } else if (c == '.' && sb.length() > 0 && !dot) {
                sb.append(c);
                dot = true;
            } else if (sb.length() > 0) {
                break;
            }
        }
        return sb.length() == 0 ? null : new BigDecimal(sb.toString());
    }
}
```

> **原 `static int parseInt(String)` 整段删除**：它把剂量与频次都当纯整数解析
> （`parseInt("0.5片")` → 0、`parseInt("bid")` → 1），是错判来源。替换为
> `firstDecimal`（结构化取数）+ `OntologyRules.timesPerDay`（频次白名单），识别不了就报 `unchecked`。

- [ ] **Step 6: 跑测试确认通过**

```bash
cd backend && mvn -o -pl his-server -am test \
  -Dtest=RationalDrugValidatorTest,OntologyRulesTest -Dsurefire.failIfNoSpecifiedTests=false
```

Expected: `RationalDrugValidatorTest` Tests run: 9；`OntologyRulesTest` Tests run: 5；
两个类 Failures: 0, Errors: 0。

- [ ] **Step 7: 把 Task 9 那张本地频次表换成 `OntologyRules.timesPerDay`**

至此 `OntologyRules` 才存在（Task 9 在它之前，所以那一步不能引用它）。
现在把 `backend/his-generator/src/test/java/com/his/generator/SeedSqlConsistencyTest.java`
里 `drugDoseRespectsDailyCap` 开头的本地 Map 删掉，改用同一个方法——
该测试在 `his-generator`，`his-common` 就在它的依赖里，直接调即可：

```java
    @Test
    @DisplayName("drug_dose 的 单次×每日次数 不超过日上限")
    void drugDoseRespectsDailyCap() {
        for (Map<String, String> r : allRows("drug_dose")) {
            String freq = r.get("frequency");
            // 取值域只有 OntologyRules 一份（生成器 Task 16 与校验器 RationalDrugValidator
            // 调的都是它）；这里再写一张 Map 就等于让本测试校验的是副本，不是那份被用的。
            int times;
            try {
                times = OntologyRules.timesPerDay(freq);
            } catch (IllegalStateException e) {
                throw new AssertionError("频次不在取值域: " + freq + " (" + r.get("drug_id") + ")", e);
            }
            BigDecimal perDay = dec(r, "single_dose").multiply(BigDecimal.valueOf(times));
```

补 import：`import com.his.common.ontology.OntologyRules;`（放在 `java.util.stream.Collectors` 之后、
`import static` 之前；`his-generator` 的测试代码此时才第一次用到 `his-common` 的类）。

跑一次确认这条测试仍绿：

```bash
cd backend && mvn -o -pl his-generator -Dtest=SeedSqlConsistencyTest test
```

Expected: Tests run: 15, Failures: 0, Errors: 0。

- [ ] **Step 8: Commit**

```bash
git add backend/his-common/src/main/java/com/his/common/dto/RationalDrugReport.java \
        backend/his-common/src/main/java/com/his/common/ontology/OntologyRules.java \
        backend/his-server/src/main/java/com/his/server/service/validation/RationalDrugValidator.java \
        backend/his-server/src/test/java/com/his/server/service/validation/RationalDrugValidatorTest.java \
        backend/his-server/src/test/java/com/his/server/service/validation/OntologyRulesTest.java \
        backend/his-generator/src/test/java/com/his/generator/SeedSqlConsistencyTest.java
git commit -m "refactor(validation): 合理用药校验换用本体数据源，无法判定显式计入 unchecked" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 11: `OntologyConsistencyValidator` —— 门诊四类本体检查（阶段一）

**Files:**
- Create: `backend/his-common/src/main/java/com/his/common/dto/OntologyReport.java`
- Create: `backend/his-common/src/main/java/com/his/common/dto/OntologyViolation.java`
- Modify: `backend/his-common/src/main/java/com/his/common/dto/ValidationReport.java`
- Create: `backend/his-server/src/main/java/com/his/server/service/validation/OntologyConsistencyValidator.java`
- Test: `backend/his-server/src/test/java/com/his/server/service/validation/OntologyConsistencyValidatorTest.java`

**Interfaces:**
- Consumes: Task 2 的 `DeptDiagnosisMapper`、`DiagnosisLabMapper`；既有的 `VisitMapper`、`DiagnosisMapper`、`PatientMapper`、`LabTestMapper`、`AdmissionMapper`；**Task 10 的 `OntologyRules`**（检验清单同样要按科室取）
- Produces: `OntologyReport`（含 `checked`/`violations`/`unchecked`/`consistentRate`/`byType`/`details`）、`OntologyViolation`、
  `ValidationReport.ontology`；`OntologyConsistencyValidator` 构造签名
  `(VisitMapper, DiagnosisMapper, PatientMapper, LabTestMapper, AdmissionMapper, DeptDiagnosisMapper, DiagnosisLabMapper)`。
  **本类不再自带任何静态判定方法**——科室优先级用 `OntologyRules.select`，
  年龄性别用 `OntologyRules.ageGenderMatches`（都在 Task 10 建好）。
  Task 24 追加手术四类时同样复用它们，不再新增判定方法的第二份实现。

**注意 `dept_diagnosis` 本身不参与科室优先级匹配**：`department` 是它的主键组成部分
（「哪个科室能看哪个诊断」就是这张表的全部含义），不存在「不限科室」的行。
科室优先级只对 `diagnosis_drug`/`diagnosis_lab`/`diagnosis_operation` 这三张**映射表**成立。

**范围说明**：spec §8.3 的 8 类检查中，第 1~4 类（门诊侧）在本任务实现；
第 5~8 类（手术侧）依赖 `operation` 表有数据，放在 **Task 24（阶段四）** 追加到同一个类里。
`byType` 因此先有 4 个键，阶段四补齐为 8 个。

- [ ] **Step 1: 创建 `OntologyViolation.java`**

```java
package com.his.common.dto;

import lombok.Data;

/** 一条本体一致性违规。门诊类违规填 visitId，住院/手术类违规填 admissionId/operationId。 */
@Data
public class OntologyViolation {
    private Long patientId;
    private Long visitId;
    private Long admissionId;
    private Long operationId;
    /** DEPT_DIAGNOSIS / AGE_GENDER / LAB_DIAGNOSIS / ADMISSION / OPERATION_DIAGNOSIS / OPERATION_TIMESPAN / SURGEON_DEPT / POSTOP_DAYS */
    private String violationType;
    private String detail;
}
```

- [ ] **Step 2: 创建 `OntologyReport.java`**

```java
package com.his.common.dto;

import lombok.Data;

import java.util.List;
import java.util.Map;

/** 本体一致性校验报告。 */
@Data
public class OntologyReport {
    /** 实际判定了的本体检查条数。**不含未能判定的那些**（见 unchecked）。 */
    private long checked;
    private long violations;
    /**
     * 无法判定的条数：就诊没有科室、诊断名映射不到 ICD、本体里找不到适用该科室的行。
     *
     * <p>与 violations 分开报出，理由同 {@code RationalDrugReport.unchecked}——
     * 「判不了」既不是通过也不是违规，**不得静默跳过伪装成通过**。
     * 它也不进 {@code checked}，因此不会把一致率算成 100%。
     */
    private long unchecked;
    /** (checked - violations) / checked；checked 为 0 时记 1.0 */
    private double consistentRate;
    /** 违规类型 -> 条数，键集合固定，缺失的类型补 0 */
    private Map<String, Long> byType;
    /** 明细，供前端展示「具体哪条记录被检出」 */
    private List<OntologyViolation> details;
}
```

- [ ] **Step 3: 给 `ValidationReport` 加 `ontology`**

```java
@Data
public class ValidationReport {
    private RationalDrugReport rationalDrug;
    private PathwayReport pathway;
    private OntologyReport ontology;
}
```

- [ ] **Step 4: 写失败测试 `OntologyConsistencyValidatorTest`**

```java
package com.his.server.service.validation;

import com.his.common.dto.OntologyReport;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class OntologyConsistencyValidatorTest {

    private static final LocalDateTime VISIT_TIME = LocalDateTime.of(2026, 3, 10, 9, 0);

    private VisitMapper vm;
    private DiagnosisMapper dm;
    private PatientMapper pm;
    private LabTestMapper lm;
    private AdmissionMapper am;
    private DeptDiagnosisMapper ddm;
    private DiagnosisLabMapper dlm;

    @BeforeEach
    void setUp() {
        vm = mock(VisitMapper.class);
        dm = mock(DiagnosisMapper.class);
        pm = mock(PatientMapper.class);
        lm = mock(LabTestMapper.class);
        am = mock(AdmissionMapper.class);
        ddm = mock(DeptDiagnosisMapper.class);
        dlm = mock(DiagnosisLabMapper.class);
        lenient().when(vm.selectList(any())).thenReturn(List.of());
        lenient().when(dm.selectList(any())).thenReturn(List.of());
        lenient().when(pm.selectList(any())).thenReturn(List.of());
        lenient().when(lm.selectList(any())).thenReturn(List.of());
        lenient().when(am.selectList(any())).thenReturn(List.of());
        lenient().when(ddm.selectList(any())).thenReturn(List.of());
        lenient().when(dlm.selectList(any())).thenReturn(List.of());
    }

    private OntologyConsistencyValidator validator() {
        return new OntologyConsistencyValidator(vm, dm, pm, lm, am, ddm, dlm);
    }

    private DeptDiagnosis dept(String department, String icdCode, int gender, int minAge, int maxAge) {
        DeptDiagnosis d = new DeptDiagnosis();
        d.setDepartment(department);
        d.setIcdCode(icdCode);
        d.setDiagnosisName("诊断" + icdCode);
        d.setGender(gender);
        d.setMinAge(minAge);
        d.setMaxAge(maxAge);
        d.setAdmissionEligible(1);
        d.setWeight(new BigDecimal("1.000"));
        return d;
    }

    private void stubVisit(long visitId, String department, long patientId) {
        Visit v = new Visit();
        v.setId(visitId);
        v.setPatientId(patientId);
        v.setDepartment(department);
        v.setVisitTime(VISIT_TIME);
        when(vm.selectList(any())).thenReturn(List.of(v));
    }

    private void stubDiagnosis(long visitId, String icdCode) {
        Diagnosis d = new Diagnosis();
        d.setVisitId(visitId);
        d.setIcdCode(icdCode);
        d.setDiagnosisName("诊断" + icdCode);
        when(dm.selectList(any())).thenReturn(List.of(d));
    }

    private void stubPatient(long patientId, int gender, LocalDate birthDate) {
        Patient p = new Patient();
        p.setId(patientId);
        p.setGender(gender);
        p.setBirthDate(birthDate);
        when(pm.selectList(any())).thenReturn(List.of(p));
    }

    /** department 传 null 表示「该诊断不限科室」的行。 */
    private DiagnosisLab labLink(String icdCode, String department, String itemCode) {
        DiagnosisLab x = new DiagnosisLab();
        x.setIcdCode(icdCode);
        x.setDepartment(department);
        x.setItemCode(itemCode);
        x.setRequired(0);
        return x;
    }

    @Test
    void flags_diagnosis_outside_the_department() {
        stubVisit(1L, "内科", 1L);
        stubDiagnosis(1L, "N80.0");
        stubPatient(1L, 2, LocalDate.of(1990, 1, 1));
        when(ddm.selectList(any())).thenReturn(List.of(dept("妇科", "N80.0", 2, 15, 60)));

        OntologyReport report = validator().validate();

        assertThat(report.getByType().get("DEPT_DIAGNOSIS")).isEqualTo(1L);
        assertThat(report.getDetails()).extracting(OntologyViolation -> OntologyViolation.getViolationType())
                .contains("DEPT_DIAGNOSIS");
    }

    @Test
    void flags_gender_conflict() {
        stubVisit(1L, "妇科", 1L);
        stubDiagnosis(1L, "N80.0");
        // 患者性别 0=男 / 1=女（见 Task 10 的取值域）；本体的 2=仅女，故 0 才构成冲突。
        stubPatient(1L, 0, LocalDate.of(1990, 1, 1));   // 男患者
        when(ddm.selectList(any())).thenReturn(List.of(dept("妇科", "N80.0", 2, 15, 60)));

        OntologyReport report = validator().validate();

        assertThat(report.getByType().get("AGE_GENDER")).isEqualTo(1L);
    }

    @Test
    void flags_age_outside_the_range() {
        stubVisit(1L, "儿科", 1L);
        stubDiagnosis(1L, "J06.9");
        stubPatient(1L, 1, LocalDate.of(1980, 1, 1));   // 46 岁
        when(ddm.selectList(any())).thenReturn(List.of(dept("儿科", "J06.9", 0, 0, 14)));

        OntologyReport report = validator().validate();

        assertThat(report.getByType().get("AGE_GENDER")).isEqualTo(1L);
    }

    @Test
    void flags_lab_item_not_linked_to_the_diagnosis() {
        stubVisit(1L, "内科", 1L);
        stubDiagnosis(1L, "I10");
        stubPatient(1L, 1, LocalDate.of(1980, 1, 1));
        when(ddm.selectList(any())).thenReturn(List.of(dept("内科", "I10", 0, 15, 89)));

        LabTest lt = new LabTest();
        lt.setPatientId(1L);
        lt.setVisitId(1L);
        lt.setItemCode("TSH");
        when(lm.selectList(any())).thenReturn(List.of(lt));

        when(dlm.selectList(any())).thenReturn(List.of(labLink("I10", "内科", "BLD-GLU")));

        OntologyReport report = validator().validate();

        assertThat(report.getByType().get("LAB_DIAGNOSIS")).isEqualTo(1L);
    }

    @Test
    void lab_check_uses_the_exact_department_row() {
        stubVisit(1L, "儿科", 1L);
        stubDiagnosis(1L, "J06.9");
        stubPatient(1L, 1, LocalDate.of(2015, 1, 1));
        when(ddm.selectList(any())).thenReturn(List.of(dept("儿科", "J06.9", 0, 0, 14)));

        LabTest lt = new LabTest();
        lt.setPatientId(1L);
        lt.setVisitId(1L);
        lt.setItemCode("CRP");
        when(lm.selectList(any())).thenReturn(List.of(lt));

        // 儿科行不含 CRP，成人行含 CRP；精确匹配必须赢 -> 判违规
        when(dlm.selectList(any())).thenReturn(List.of(
                labLink("J06.9", "内科", "CRP"),
                labLink("J06.9", "儿科", "CBC")));

        OntologyReport report = validator().validate();

        assertThat(report.getByType().get("LAB_DIAGNOSIS")).isEqualTo(1L);
        assertThat(report.getUnchecked()).isZero();
    }

    @Test
    void lab_check_falls_back_to_the_null_department_row() {
        stubVisit(1L, "儿科", 1L);
        stubDiagnosis(1L, "J06.9");
        stubPatient(1L, 1, LocalDate.of(2015, 1, 1));
        when(ddm.selectList(any())).thenReturn(List.of(dept("儿科", "J06.9", 0, 0, 14)));

        LabTest lt = new LabTest();
        lt.setPatientId(1L);
        lt.setVisitId(1L);
        lt.setItemCode("CBC");
        when(lm.selectList(any())).thenReturn(List.of(lt));

        // 该诊断在本体里只有「不限科室」的行 -> 落回退分支，CBC 在清单中，合规
        when(dlm.selectList(any())).thenReturn(List.of(labLink("J06.9", null, "CBC")));

        OntologyReport report = validator().validate();

        assertThat(report.getViolations()).isZero();
        assertThat(report.getUnchecked()).isZero();
    }

    @Test
    void lab_check_without_any_applicable_row_counts_as_unchecked() {
        stubVisit(1L, "妇科", 1L);
        stubDiagnosis(1L, "J06.9");
        stubPatient(1L, 2, LocalDate.of(1990, 1, 1));
        // dept_diagnosis 里也没有妇科的 J06.9 -> 同时触发 DEPT_DIAGNOSIS 与检验未判定
        when(ddm.selectList(any())).thenReturn(List.of(dept("内科", "J06.9", 0, 15, 89)));

        LabTest lt = new LabTest();
        lt.setPatientId(1L);
        lt.setVisitId(1L);
        lt.setItemCode("CBC");
        when(lm.selectList(any())).thenReturn(List.of(lt));
        when(dlm.selectList(any())).thenReturn(List.of(labLink("J06.9", "内科", "CBC")));

        OntologyReport report = validator().validate();

        // 检验这一条判不了（没有妇科的行、也没有 NULL 行）-> 报未判定，不伪造成违规
        assertThat(report.getByType().get("LAB_DIAGNOSIS")).isZero();
        assertThat(report.getUnchecked()).isEqualTo(1);
        // 而「妇科看 J06.9」这件事本身由 DEPT_DIAGNOSIS 判
        assertThat(report.getByType().get("DEPT_DIAGNOSIS")).isEqualTo(1L);
    }

    @Test
    void flags_admission_whose_diagnosis_is_not_admission_eligible() {
        stubVisit(1L, "儿科", 1L);
        stubDiagnosis(1L, "J06.9");
        stubPatient(1L, 3, LocalDate.of(2020, 1, 1));
        DeptDiagnosis notEligible = dept("儿科", "J06.9", 0, 0, 14);
        notEligible.setAdmissionEligible(0);
        when(ddm.selectList(any())).thenReturn(List.of(notEligible));

        Admission a = new Admission();
        a.setPatientId(1L);
        a.setAdmissionDiagnosis("诊断J06.9");   // admission 存的是诊断名称，与 dept() 的 diagnosisName 对齐
        when(am.selectList(any())).thenReturn(List.of(a));

        OntologyReport report = validator().validate();

        assertThat(report.getByType().get("ADMISSION")).isEqualTo(1L);
        assertThat(report.getUnchecked()).isZero();
    }

    @Test
    void admission_with_a_name_absent_from_the_ontology_counts_as_unchecked() {
        stubVisit(1L, "内科", 1L);
        stubDiagnosis(1L, "I10");
        stubPatient(1L, 1, LocalDate.of(1980, 1, 1));
        when(ddm.selectList(any())).thenReturn(List.of(dept("内科", "I10", 0, 15, 89)));

        Admission a = new Admission();
        a.setPatientId(1L);
        a.setAdmissionDiagnosis("某个本体里没有的诊断名");
        when(am.selectList(any())).thenReturn(List.of(a));

        OntologyReport report = validator().validate();

        // 名称映射不到 ICD -> 无从判定，报未判定；不得伪造成「不可收住院」
        assertThat(report.getByType().get("ADMISSION")).isZero();
        assertThat(report.getUnchecked()).isEqualTo(1);
    }

    @Test
    void visit_without_department_counts_as_unchecked() {
        stubVisit(1L, null, 1L);
        stubDiagnosis(1L, "I10");
        stubPatient(1L, 1, LocalDate.of(1980, 1, 1));
        when(ddm.selectList(any())).thenReturn(List.of(dept("内科", "I10", 0, 15, 89)));

        OntologyReport report = validator().validate();

        // 没有科室 -> 既不能判科室归属，也不能按科室取本体行 -> 未判定，不是违规
        assertThat(report.getViolations()).isZero();
        assertThat(report.getUnchecked()).isEqualTo(1);
        assertThat(report.getChecked()).isZero();
    }

    @Test
    void clean_data_has_full_consistent_rate_and_zero_violations() {
        stubVisit(1L, "内科", 1L);
        stubDiagnosis(1L, "I10");
        stubPatient(1L, 1, LocalDate.of(1980, 1, 1));
        when(ddm.selectList(any())).thenReturn(List.of(dept("内科", "I10", 0, 15, 89)));

        OntologyReport report = validator().validate();

        assertThat(report.getViolations()).isZero();
        assertThat(report.getConsistentRate()).isEqualTo(1.0);
        assertThat(report.getByType()).containsKeys(
                "DEPT_DIAGNOSIS", "AGE_GENDER", "LAB_DIAGNOSIS", "ADMISSION");
    }

    @Test
    void empty_database_yields_full_rate_and_no_violations() {
        OntologyReport report = validator().validate();

        assertThat(report.getChecked()).isZero();
        assertThat(report.getViolations()).isZero();
        assertThat(report.getConsistentRate()).isEqualTo(1.0);
    }

    @Test
    void checks_are_scoped_by_patient_not_global() {
        // 三张表都按 patientId 过滤；用两个患者验证不会互相串诊断
        Visit v1 = new Visit();
        v1.setId(1L); v1.setPatientId(1L); v1.setDepartment("内科"); v1.setVisitTime(VISIT_TIME);
        Visit v2 = new Visit();
        v2.setId(2L); v2.setPatientId(2L); v2.setDepartment("内科"); v2.setVisitTime(VISIT_TIME);
        when(vm.selectList(any())).thenReturn(List.of(v1, v2));

        Diagnosis d1 = new Diagnosis(); d1.setVisitId(1L); d1.setIcdCode("I10");
        Diagnosis d2 = new Diagnosis(); d2.setVisitId(2L); d2.setIcdCode("I10");
        when(dm.selectList(any())).thenReturn(List.of(d1, d2));

        Patient p1 = new Patient(); p1.setId(1L); p1.setGender(1); p1.setBirthDate(LocalDate.of(1980, 1, 1));
        Patient p2 = new Patient(); p2.setId(2L); p2.setGender(1); p2.setBirthDate(LocalDate.of(1980, 1, 1));
        when(pm.selectList(any())).thenReturn(List.of(p1, p2));

        when(ddm.selectList(any())).thenReturn(List.of(dept("内科", "I10", 0, 15, 89)));

        OntologyReport report = validator().validate();

        assertThat(report.getChecked()).isEqualTo(2);
        assertThat(report.getViolations()).isZero();
    }
}
```

- [ ] **Step 5: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-server -am test \
  -Dtest=OntologyConsistencyValidatorTest -Dsurefire.failIfNoSpecifiedTests=false
```

Expected: 编译失败，`OntologyConsistencyValidator` 尚不存在。

- [ ] **Step 6: 实现 `OntologyConsistencyValidator`**

```java
package com.his.server.service.validation;

import com.his.common.dto.OntologyReport;
import com.his.common.dto.OntologyViolation;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.Period;
import java.util.*;
import java.util.stream.Collectors;

/**
 * 本体一致性校验：只判「这条记录是否违反医学本体」，**不重复用药安全**（那部分在
 * {@link RationalDrugValidator}），避免两类报告重复计数。
 *
 * <p>本任务实现门诊侧四类：科室↔诊断、年龄性别↔诊断、检验↔诊断、住院↔诊断。
 * 手术侧四类在阶段四追加（见计划 Task 24）。
 */
@Component
@RequiredArgsConstructor
public class OntologyConsistencyValidator {

    private static final List<String> CHECK_TYPES = List.of(
            "DEPT_DIAGNOSIS", "AGE_GENDER", "LAB_DIAGNOSIS", "ADMISSION");

    private final VisitMapper visitMapper;
    private final DiagnosisMapper diagnosisMapper;
    private final PatientMapper patientMapper;
    private final LabTestMapper labTestMapper;
    private final AdmissionMapper admissionMapper;
    private final DeptDiagnosisMapper deptDiagnosisMapper;
    private final DiagnosisLabMapper diagnosisLabMapper;

    public OntologyReport validate() {
        OntologyReport report = new OntologyReport();
        Map<String, Long> byType = new LinkedHashMap<>();
        CHECK_TYPES.forEach(t -> byType.put(t, 0L));
        report.setByType(byType);
        report.setDetails(new ArrayList<>());

        List<DeptDiagnosis> ontology = deptDiagnosisMapper.selectList(null);
        // icd_code -> 允许的科室集合（同 ICD 可跨科室，取并集）
        Map<String, Set<String>> deptsByIcd = new HashMap<>();
        // 适用行：icd_code -> 该 ICD 的全部本体行（供年龄/性别判定）
        Map<String, List<DeptDiagnosis>> rowsByIcd = new HashMap<>();
        for (DeptDiagnosis row : ontology) {
            if (row.getIcdCode() == null) continue;
            deptsByIcd.computeIfAbsent(row.getIcdCode(), k -> new HashSet<>()).add(row.getDepartment());
            rowsByIcd.computeIfAbsent(row.getIcdCode(), k -> new ArrayList<>()).add(row);
        }
        // 检验本体：保留 department，供 OntologyRules 按科室优先级匹配
        List<DiagnosisLab> labOntology = diagnosisLabMapper.selectList(null).stream()
                .filter(x -> x.getIcdCode() != null && x.getItemCode() != null)
                .toList();

        Map<Long, Patient> patientsById = patientMapper.selectList(null).stream()
                .collect(Collectors.toMap(Patient::getId, p -> p, (x, y) -> x));
        Map<Long, Visit> visitsById = visitMapper.selectList(null).stream()
                .collect(Collectors.toMap(Visit::getId, v -> v, (x, y) -> x));
        Map<Long, String> icdByVisit = diagnosisMapper.selectList(null).stream()
                .filter(d -> d.getVisitId() != null && d.getIcdCode() != null)
                .collect(Collectors.toMap(Diagnosis::getVisitId, Diagnosis::getIcdCode, (x, y) -> x));

        long checked = 0;
        long unchecked = 0;

        // 检查 1 + 2：逐条门诊诊断判科室归属与年龄性别
        for (Diagnosis d : diagnosisMapper.selectList(null)) {
            Visit v = d.getVisitId() == null ? null : visitsById.get(d.getVisitId());
            // 找不到对应就诊就无从判定：**不得静默跳过**（Global Constraints：无法判定的数据必须显式报出）
            if (v == null) {
                unchecked++;
                continue;
            }
            // 没有科室就没法判科室归属，也没法按科室取本体行 -> 如实记为未判定
            if (v.getDepartment() == null) {
                unchecked++;
                continue;
            }
            Patient p = patientsById.get(v.getPatientId());
            checked++;

            Set<String> depts = deptsByIcd.get(d.getIcdCode());
            if (depts == null || !depts.contains(v.getDepartment())) {
                byType.merge("DEPT_DIAGNOSIS", 1L, Long::sum);
                report.getDetails().add(violation(v.getPatientId(), v.getId(), null, null,
                        "DEPT_DIAGNOSIS",
                        "诊断 " + d.getIcdCode() + " 不属于科室 " + v.getDepartment()
                                + "（本体允许：" + (depts == null ? "无" : String.join("/", depts)) + "）"));
            }

            if (p != null && p.getBirthDate() != null) {
                int age = Period.between(p.getBirthDate(), v.getVisitTime().toLocalDate()).getYears();
                int gender = p.getGender() == null ? 0 : p.getGender();
                List<DeptDiagnosis> rows = rowsByIcd.get(d.getIcdCode());
                // 该 ICD 的任一行匹配即算通过（同 ICD 多年龄段/多性别是合法的）
                boolean matched = rows != null && rows.stream()
                        .anyMatch(row -> OntologyRules.ageGenderMatches(row, age, gender));
                if (rows != null && !rows.isEmpty() && !matched) {
                    byType.merge("AGE_GENDER", 1L, Long::sum);
                    report.getDetails().add(violation(v.getPatientId(), v.getId(), null, null,
                            "AGE_GENDER",
                            "诊断 " + d.getIcdCode() + " 不适用于 年龄=" + age + " 性别=" + gender));
                }
            }
        }

        // 检查 3：门诊检验（admissionId 为空）必须挂在该次就诊诊断的 diagnosis_lab 上，
        // 且该诊断的检验清单**按就诊科室**取（同 ICD 在不同科室的异常率不同，清单也可不同）
        for (LabTest lt : labTestMapper.selectList(null)) {
            // 这条是**范围切分**（住院检验交由阶段四的检查判定），不是静默跳过，保持原样。
            if (lt.getAdmissionId() != null) continue;
            // 下面两条是真正的「无从判定」：门诊检验必须挂在一次有诊断的就诊上，
            // 缺就诊或该就诊没有诊断编码，都如实计入 unchecked。
            if (lt.getVisitId() == null) {
                unchecked++;
                continue;
            }
            String icd = icdByVisit.get(lt.getVisitId());
            if (icd == null) {
                unchecked++;
                continue;
            }
            Visit v = visitsById.get(lt.getVisitId());
            String dept = v == null ? null : v.getDepartment();
            if (dept == null) {
                unchecked++;
                continue;
            }
            // **先取适用行，再判在不在清单里**——不能直接用 OntologyRules.covers：
            // covers 对「没有适用行」也返回 false，于是「本体里没有该科室看该诊断的行」
            // 会被算成违规。那不是「检验项目开错了」，是「本子没记这条」，
            // 必须计入 unchecked（见 lab_check_without_any_applicable_row_counts_as_unchecked）。
            List<DiagnosisLab> applicable = OntologyRules.select(labOntology,
                    DiagnosisLab::getDepartment, DiagnosisLab::getIcdCode, dept, icd);
            if (applicable.isEmpty()) {
                unchecked++;
                continue;
            }
            checked++;
            boolean onList = applicable.stream()
                    .anyMatch(row -> row.getItemCode().equals(lt.getItemCode()));
            if (!onList) {
                byType.merge("LAB_DIAGNOSIS", 1L, Long::sum);
                report.getDetails().add(violation(lt.getPatientId(), lt.getVisitId(), null, null,
                        "LAB_DIAGNOSIS",
                        "检验 " + lt.getItemCode() + " 不在 " + dept + " 的 " + icd + " 检验清单中"));
            }
        }

        // 检查 4：住院的入院诊断必须 admission_eligible=1
        // `allAdmissions` / `admissionsById` 提成局部变量：Task 24 的检查 5~8 要按 id 定位住院，
        // 提到这里之后整趟校验只全表扫一次 admission。
        List<Admission> allAdmissions = admissionMapper.selectList(null);
        Map<Long, Admission> admissionsById = new HashMap<>();
        for (Admission a : allAdmissions) {
            admissionsById.put(a.getId(), a);
        }
        for (Admission a : allAdmissions) {
            String diag = a.getAdmissionDiagnosis() != null ? a.getAdmissionDiagnosis() : a.getDiagnosis();
            // 新老两个诊断列都为空：无从判定，报 unchecked 而不是静默放过
            if (diag == null) {
                unchecked++;
                continue;
            }
            // admission 存的是诊断**名称**不是编码；名称对不上本体就无从判定，
            // 报 unchecked 而不是伪造成「不可收住院」
            String icd = icdOf(diag, ontology);
            if (icd == null) {
                unchecked++;
                continue;
            }
            checked++;
            boolean eligible = rowsByIcd.getOrDefault(icd, List.of()).stream()
                    .anyMatch(row -> row.getAdmissionEligible() != null && row.getAdmissionEligible() == 1);
            if (!eligible) {
                byType.merge("ADMISSION", 1L, Long::sum);
                report.getDetails().add(violation(a.getPatientId(), null, a.getId(), null,
                        "ADMISSION", "入院诊断「" + diag + "」不可收住院"));
            }
        }

        report.setChecked(checked);
        report.setUnchecked(unchecked);
        long violations = byType.values().stream().mapToLong(Long::longValue).sum();
        report.setViolations(violations);
        report.setConsistentRate(checked == 0 ? 1.0 : (double) (checked - violations) / checked);
        return report;
    }

    /** 诊断名称 → ICD 编码（`admission.diagnosis` 存的是名称，不是编码）。 */
    private static String icdOf(String diagnosisName, List<DeptDiagnosis> ontology) {
        return ontology.stream()
                .filter(row -> diagnosisName.equals(row.getDiagnosisName()))
                .map(DeptDiagnosis::getIcdCode)
                .findFirst().orElse(null);
    }

    private static OntologyViolation violation(Long patientId, Long visitId, Long admissionId,
                                               Long operationId, String type, String detail) {
        OntologyViolation v = new OntologyViolation();
        v.setPatientId(patientId);
        v.setVisitId(visitId);
        v.setAdmissionId(admissionId);
        v.setOperationId(operationId);
        v.setViolationType(type);
        v.setDetail(detail);
        return v;
    }
}
```

> `LocalDate` 的 import 只在 `Period.between` 处用不到（`p.getBirthDate()` 已是 `LocalDate`），
> 若编译器报 unused import，删掉 `java.time.LocalDate` 那一行即可。

- [ ] **Step 7: 跑测试确认通过**

```bash
cd backend && mvn -o -pl his-server -am test \
  -Dtest=OntologyConsistencyValidatorTest -Dsurefire.failIfNoSpecifiedTests=false
```

Expected: Tests run: 13, Failures: 0, Errors: 0。

- [ ] **Step 8: Commit**

```bash
git add backend/his-common/src/main/java/com/his/common/dto \
        backend/his-server/src/main/java/com/his/server/service/validation/OntologyConsistencyValidator.java \
        backend/his-server/src/test/java/com/his/server/service/validation/OntologyConsistencyValidatorTest.java
git commit -m "feat(validation): 新增本体一致性校验器（门诊四类）" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 12: `ValidationService` 汇总本体报告 + 删除违规注入（阶段一收尾）

**Files:**
- Modify: `backend/his-server/src/main/java/com/his/server/service/impl/ValidationServiceImpl.java`
- Create: `backend/his-server/src/test/java/com/his/server/service/impl/ValidationServiceImplTest.java`
- Modify: `backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java`（删注入）
- Modify: `backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java`

**Interfaces:**
- Consumes: Task 10 的 `RationalDrugValidator`、Task 11 的 `OntologyConsistencyValidator`、既有 `PathwayValidator`
- Produces: `ValidationReport{rationalDrug, pathway, ontology}` 三份报告全部就位；生成器不再有注入入口

**为什么「删注入」和「接汇总」是同一个任务**：这两件事合起来才完成一次语义切换——
**违规不再由生成器制造，而由校验器发现**。只做一半的话，看板上会同时出现
「注入的假违规」和「本体校验查出的真违规」，两个数字混在一起没法解释。

- [ ] **Step 1: 写失败测试 `ValidationServiceImplTest`**

```java
package com.his.server.service.impl;

import com.his.common.dto.OntologyReport;
import com.his.common.dto.PathwayReport;
import com.his.common.dto.RationalDrugReport;
import com.his.common.dto.ValidationReport;
import com.his.server.service.validation.OntologyConsistencyValidator;
import com.his.server.service.validation.PathwayValidator;
import com.his.server.service.validation.RationalDrugValidator;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ValidationServiceImplTest {

    private final RationalDrugValidator rationalDrug = mock(RationalDrugValidator.class);
    private final PathwayValidator pathway = mock(PathwayValidator.class);
    private final OntologyConsistencyValidator ontology = mock(OntologyConsistencyValidator.class);

    private final ValidationServiceImpl service =
            new ValidationServiceImpl(rationalDrug, pathway, ontology);

    @Test
    void run_fills_all_three_reports() {
        RationalDrugReport rd = new RationalDrugReport();
        rd.setTotalPrescriptions(10);
        PathwayReport pw = new PathwayReport();
        OntologyReport on = new OntologyReport();
        on.setChecked(20);
        when(rationalDrug.validate()).thenReturn(rd);
        when(pathway.validate()).thenReturn(pw);
        when(ontology.validate()).thenReturn(on);

        ValidationReport report = service.run();

        assertThat(report.getRationalDrug()).isSameAs(rd);
        assertThat(report.getPathway()).isSameAs(pw);
        // 本体报告是本任务新增的第三份；漏接它，看板上的「本体一致率」就永远是 null
        assertThat(report.getOntology()).isSameAs(on);
    }
}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-server -am test \
  -Dtest=ValidationServiceImplTest -Dsurefire.failIfNoSpecifiedTests=false
```

Expected: 编译失败——`ValidationServiceImpl` 的构造器只有两个参数，`getOntology()` 也还不存在
（`ontology` 字段在 Task 11 Step 3 已加到 `ValidationReport`，此处失败是因为服务层还没注入校验器）。

- [ ] **Step 3: 改 `ValidationServiceImpl`**

整体替换为：

```java
package com.his.server.service.impl;

import com.his.common.dto.ValidationReport;
import com.his.server.service.ValidationService;
import com.his.server.service.validation.OntologyConsistencyValidator;
import com.his.server.service.validation.PathwayValidator;
import com.his.server.service.validation.RationalDrugValidator;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class ValidationServiceImpl implements ValidationService {

    private final RationalDrugValidator rationalDrugValidator;
    private final PathwayValidator pathwayValidator;
    private final OntologyConsistencyValidator ontologyConsistencyValidator;

    @Override
    public ValidationReport run() {
        ValidationReport report = new ValidationReport();
        report.setRationalDrug(rationalDrugValidator.validate());
        report.setPathway(pathwayValidator.validate());
        report.setOntology(ontologyConsistencyValidator.validate());
        return report;
    }
}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd backend && mvn -o -pl his-server -am test \
  -Dtest=ValidationServiceImplTest -Dsurefire.failIfNoSpecifiedTests=false
```

Expected: Tests run: 1, Failures: 0, Errors: 0。

- [ ] **Step 5: 删掉生成器里的违规注入**

`DataGeneratorService` 里共有 5 处要删（行号按当前文件）：

**(1)** 删掉 `VIOLATION_RATE` 常量（第 57-58 行）：

```java
    /** 处方违规注入概率 */
    static final double VIOLATION_RATE = 0.03;
```

**(2)** 删掉两个只为注入而存在的字段（第 104-105 行）：

```java
    private List<RationalRule> interactionRules;
    private Map<Long, RationalRule> doseRulesByDrug;
```

**(3)** 删掉 `generate()` 里加载这两个字段的整段（第 121-130 行，从 `interactionRules = new ArrayList<>();`
到 `}` 结束的那个 for 循环）——它们**只被 `injectViolation` 读过**，删掉注入后就是死代码。
`rationalRuleMapper` 字段与它的构造器参数**保留**（阶段二要用它做「避免开相互作用药对」，
用途正好相反：注入违规 → 规避违规）。

**(4)** 删掉 `injectViolation` 方法全体（第 710-753 行，含它上面那行 `/** 以固定方式注入一类违规... */` 注释）。

**(5)** 删掉调用点（第 226-228 行）：

```java
            if (rnd.nextDouble() < VIOLATION_RATE) {
                injectViolation(its, pp.rule());
            }
```

改完后 `buildItems(...)` 的返回值直接进 `for (PrescriptionItem it : its)`，
上下的 `total` 重算逻辑**保持不动**（它仍是必需品：药价缺失要按 0 计，防 NPE）。

- [ ] **Step 6: 删掉对应的测试并跑生成器测试**

删除 `DataGeneratorServiceTest` 里的 `injectViolation_produces_a_known_violation` 方法全体
（第 420-435 行）。它断言的行为**已经不存在了**——留着它，要么编译不过，
要么（更糟）迫使实现保留一个「注入违规」的入口。同时删掉只为它准备的
`RULE_DRUG_1`/`RULE_DRUG_2` 常量**仅当**没有其他用例引用（先 grep 确认）。

```bash
cd backend && grep -rn "RULE_DRUG_1\|RULE_DRUG_2" his-generator/src/test/
cd backend && mvn -o -pl his-generator test
```

Expected: `Tests run: N, Failures: 0, Errors: 0`（N 为删除前的用例数减 1）。

- [ ] **Step 7: 全套测试**

```bash
cd backend && mvn -o test
```

Expected: 全模块通过。

- [ ] **Step 8: Commit**

```bash
git add backend/his-server/src/main/java/com/his/server/service/impl/ValidationServiceImpl.java \
        backend/his-server/src/test/java/com/his/server/service/impl/ValidationServiceImplTest.java \
        backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java \
        backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java
git commit -m "refactor: 校验报告汇总本体一致率；删除生成器的违规注入

违规不再由生成器制造，改由校验器发现。删除 VIOLATION_RATE、
injectViolation 及其只为它存在的 interactionRules/doseRulesByDrug。
rationalRuleMapper 保留：阶段二用它规避相互作用药对，用途与注入相反。" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### 阶段一出口判据（跑完 Task 12 才成立）

**此时生成器已不再制造违规，但「三项判据全为 0」还做不到。** 原因是清楚的：
生成器的诊断仍从 `rule_config` 的 8 条里随机挑，**与就诊科室无关**，
所以 Task 11 的 DEPT_DIAGNOSIS 会查出一大批「内科看妇科病」这类违规。

**这不是回归，是基线。** 现在这个数字第一次变得可测量、可归因：

```bash
# 1. 重新生成数据（需要已建库；建表见 Task 1 的 migrate-phase3.sql）
cd backend && mvn -q -pl his-generator -am install -DskipTests
java -jar his-generator/target/his-generator-1.0.0.jar

# 2. 跑校验
curl -s "http://localhost:8080/api/validation/run" | python -m json.tool
```

把 `ontology.byType` 里各类的条数与 `rationalDrug.unchecked` **如实抄进
`docs/test-report.md` 的「阶段一基线」小节**，标明这是**改造前**的数字。
阶段二的出口判据就是把这些数字压到 0（或压到本体覆盖不到而报 `unchecked` 的那部分）。

> **不要跳过这一步。** 论文里「改造前 42% 的就诊存在科室↔诊断不符，改造后 0%」
> 这样的句子，唯一的合法来源就是这一次实测。凭印象写数字，就是编造。

---

## Task 13: `OntologyRepository` —— 本体的一次性加载与按科室取样（阶段二）

**Files:**
- Create: `backend/his-generator/src/main/java/com/his/generator/Disease.java`
- Create: `backend/his-generator/src/main/java/com/his/generator/OntologyRepository.java`
- Modify: `backend/his-generator/src/main/resources/application.yml`（补一行 mybatis-plus 配置，见 Step 1）
- Modify: `backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java`（字段区 + `generate()` 开头）
- Test: `backend/his-generator/src/test/java/com/his/generator/OntologyRepositoryTest.java`
- Modify: `backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java`（构造器多一个参数）

**Interfaces:**
- Consumes: Task 2 的 6 个本体 Mapper（`DeptDiagnosisMapper` / `DiagnosisDrugMapper` / `DiagnosisLabMapper` / `DiagnosisOperationMapper` / `DrugDoseMapper` / `VisitRateMapper`）；Task 10 的 `OntologyRules`
- Produces（Task 14/15/16/17/23/24 全部依赖下列签名，**名字与类型不得改**）：
  - `com.his.generator.Disease` record：`icdCode()` / `name()` / `department()` / `gender()`(int) /
    `minAge()` / `maxAge()` / `chronic()` / `admissionEligible()` / `weight()`(double) /
    `monthWeights()`(double[]) / `prevalence()`(double) / `monthWeight(int month)`
  - `OntologyRepository`（`@Component`）：
    - `void load()` —— 全量重载，清空筛选缓存
    - `List<Disease> diseasesFor(int age, int gender)`
    - `List<Disease> chronicDiseasesFor(int age, int gender)`
    - `List<Disease> admissionEligibleDiseasesFor(int age, int gender)`
    - `List<DiagnosisDrug> drugsOf(Disease d)`
    - `List<DiagnosisLab> labsOf(Disease d)`
    - `List<DiagnosisOperation> operationsOf(Disease d)`
    - `DrugDose doseOf(Long drugId)` —— **无行返回 `null`**
    - `BigDecimal visitMultiplier(int age, int gender)`
    - `int diseaseCount()`
  - `static double[] parseMonthWeights(String json)`
  - `DataGeneratorService` 构造器在 `pathwayMapper` **之后**追加一个 `OntologyRepository`

**为什么要有这一层**：现在生成器的每一层抽样都是「从全局池随机取」——诊断从 `rule_config` 的 8 条里抽、
科室随机、检验从 5 项全局池抽。要改成 §6.1 的逐层条件抽样，第一层就是
「这个患者能得哪些病」，它要求把 `dept_diagnosis` 的 72 行按**年龄、性别**筛一遍，
再按**诊断**取药品 / 检验 / 手术行。这些查询会被调用上百万次，**不能每次都打数据库**，
所以改为 `load()` 时一次性全量载入内存、之后只读内存。

**为什么把 `DeptDiagnosis` 展平成 `Disease` record**：`month_weights` 是 JSON 文本，
而季节乘子在**加权抽样阶段**就要参与计算（§5.2 的 `weight × month_weights[当月] × acutePropensity`），
在热循环里每次 `readValue` 是纯浪费。展平后生成器全程只读 record，不再碰实体、不再解析 JSON。

**`month_weights` 的 `>0` 过滤是空操作，这里如实说明**：spec §5.2 写「从……且
`month_weights[当月] > 0` 的急性病池中抽」，但 Task 4 的三套季节模式（WINTER / SUMMER / SPRING_AUTUMN）
**每个月的乘子都 ≥0.7**，所以「> 0」恒真、没有任何筛选作用。**季节真正生效靠的是乘法而非成员资格**：
Task 15 用 `weight × monthWeight(month)` 加权抽取，7 月的流感权重自然降到冬季的一半。
因此本任务**不提供**按月过滤的方法——写了就是死代码。若日后要让某病在某月彻底不发生，
须同时改 Task 1 的 DDL 注释、Task 9 的正数断言与 Task 4 的种子，三处一起改，不要只改这里。

**慢病为什么也在 `diseasesFor` 的返回里**：`chronic=1` 只表示「可终身携带、走随访」，
不表示它不会急性发作。`diseasesFor` 返回**全部**适用诊断（含慢病），
于是「慢阻肺患者冬天急性加重来就诊」会自然出现，而 `chronicDiseasesFor` 只用于
§5.1 建档与 §5.2 随访。若把慢病排除在急性池之外，一个慢阻肺患者永远不会因为急性加重就诊——
那比现在更不真实。

**`doseOf` 找不到行时必须返回 `null`，绝不返回默认剂量**：`drug_dose` 缺行说明该药
「按每日次数给药」这个模型表达不了（如甲氨蝶呤是每周给药，见 Task 8 注②）。
生成器（Task 16）遇到 `null` 就**跳过该药并把计数报出来**，
不得自己编一个「每日 3 次、每次 1 片」——那正是要消灭的编造。

- [ ] **Step 1: 写失败的测试**

**先把一行配置补上**：`backend/his-generator/src/main/resources/application.yml` 里补

```yaml
mybatis-plus:
  configuration:
    map-underscore-to-camel-case: true
```

> 这一行**不改变任何行为**（MyBatis-Plus 的默认值就是 `true`，`his-server/application.yml:18` 也显式写着），
> 作用是把它从「吃框架默认值」变成「写下来的约定」。Task 2 评审实测：把这个开关置为 `false` 后，
> **连既有实体一起崩**（`Admission` 解析出 `patientId -> patientId`，真库 insert 报
> `Unknown column 'patientId' in 'field list'`）。也就是说全仓的列名映射都吊在这个默认值上，
> 而本任务起生成器开始读 `dept_diagnosis` / `diagnosis_drug` / `drug_dose` 等新表的
> `month_weights` / `max_daily_dose` / `min_postop_days` 这些多词列名——依赖面只增不减。
>
> **不要**用 `@TableField("列名")` 去「修」这类问题：本期硬约束明确禁止在实体上加 `@TableField`。
> 配置项是配置项，实体是实体。

新建 `backend/his-generator/src/test/java/com/his/generator/OntologyRepositoryTest.java`：

```java
package com.his.generator;

import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class OntologyRepositoryTest {

    private DeptDiagnosisMapper deptDiagnosisMapper;
    private DiagnosisDrugMapper diagnosisDrugMapper;
    private DiagnosisLabMapper diagnosisLabMapper;
    private DiagnosisOperationMapper diagnosisOperationMapper;
    private DrugDoseMapper drugDoseMapper;
    private VisitRateMapper visitRateMapper;
    private OntologyRepository repo;

    @BeforeEach
    void setUp() {
        deptDiagnosisMapper = mock(DeptDiagnosisMapper.class);
        diagnosisDrugMapper = mock(DiagnosisDrugMapper.class);
        diagnosisLabMapper = mock(DiagnosisLabMapper.class);
        diagnosisOperationMapper = mock(DiagnosisOperationMapper.class);
        drugDoseMapper = mock(DrugDoseMapper.class);
        visitRateMapper = mock(VisitRateMapper.class);
        // 默认全部为空，各测试按需覆盖
        when(deptDiagnosisMapper.selectList(any())).thenReturn(List.of());
        when(diagnosisDrugMapper.selectList(any())).thenReturn(List.of());
        when(diagnosisLabMapper.selectList(any())).thenReturn(List.of());
        when(diagnosisOperationMapper.selectList(any())).thenReturn(List.of());
        when(drugDoseMapper.selectList(any())).thenReturn(List.of());
        when(visitRateMapper.selectList(any())).thenReturn(List.of());
        repo = new OntologyRepository(deptDiagnosisMapper, diagnosisDrugMapper, diagnosisLabMapper,
                diagnosisOperationMapper, drugDoseMapper, visitRateMapper);
    }

    /** 构造一条 dept_diagnosis 行；参数顺序与 Task 4 的种子列序一致。 */
    private DeptDiagnosis dd(String department, String icd, String name, int gender,
                             int minAge, int maxAge, int chronic, int admissionEligible,
                             String weight, String monthWeights, String prevalence) {
        DeptDiagnosis d = new DeptDiagnosis();
        d.setDepartment(department);
        d.setIcdCode(icd);
        d.setDiagnosisName(name);
        d.setGender(gender);
        d.setMinAge(minAge);
        d.setMaxAge(maxAge);
        d.setChronic(chronic);
        d.setAdmissionEligible(admissionEligible);
        d.setWeight(new BigDecimal(weight));
        d.setMonthWeights(monthWeights);
        d.setPrevalence(new BigDecimal(prevalence));
        return d;
    }

    /** 三行样本：儿科急病、仅女的妇科慢病、内科慢病。 */
    private void stubThreeDiseases() {
        when(deptDiagnosisMapper.selectList(any())).thenReturn(List.of(
                dd("儿科", "J06.9", "上呼吸道感染", 0, 0, 14, 0, 0, "3.000", null, "0"),
                dd("妇科", "N80.0", "子宫内膜异位症", 2, 18, 55, 1, 1, "2.000", null, "0.0300"),
                dd("内科", "I10", "原发性高血压", 0, 18, 89, 1, 1, "4.000", null, "0.2500")));
    }

    @Test
    @DisplayName("按年龄与性别筛选：用的是与校验器同一个判定")
    void diseases_for_filters_by_age_and_gender() {
        stubThreeDiseases();
        repo.load();

        assertThat(repo.diseasesFor(30, 1)).extracting(Disease::icdCode)
                .containsExactlyInAnyOrder("N80.0", "I10");
        // 男性患者不该拿到「仅女」的 N80.0
        assertThat(repo.diseasesFor(30, 0)).extracting(Disease::icdCode)
                .containsExactly("I10");
        // 10 岁只落在儿科那一行
        assertThat(repo.diseasesFor(10, 1)).extracting(Disease::icdCode)
                .containsExactly("J06.9");
        // 56 岁超出 N80.0 的 18~55，只剩 I10
        assertThat(repo.diseasesFor(56, 1)).extracting(Disease::icdCode)
                .containsExactly("I10");
    }

    @Test
    @DisplayName("慢病池只含 chronic=1，可住院池只含 admission_eligible=1")
    void chronic_and_admission_pools_are_subsets() {
        stubThreeDiseases();
        repo.load();

        assertThat(repo.chronicDiseasesFor(30, 1)).extracting(Disease::icdCode)
                .containsExactlyInAnyOrder("N80.0", "I10");
        // J06.9 是急性病且不可住院，两个池都不该有它
        assertThat(repo.chronicDiseasesFor(10, 1)).isEmpty();
        assertThat(repo.admissionEligibleDiseasesFor(10, 1)).isEmpty();
        assertThat(repo.admissionEligibleDiseasesFor(30, 1)).extracting(Disease::icdCode)
                .containsExactlyInAnyOrder("N80.0", "I10");
    }

    @Test
    @DisplayName("药品/检验/手术行按科室优先级取：精确行胜过「不限科室」的行")
    void mapping_rows_use_the_shared_department_priority() {
        stubThreeDiseases();
        when(diagnosisDrugMapper.selectList(any())).thenReturn(List.of(
                drug("J06.9", "儿科", 1L), drug("J06.9", null, 1L), drug("J06.9", null, 2L)));
        when(diagnosisLabMapper.selectList(any())).thenReturn(List.of(
                lab("J06.9", "儿科", "BLD-CRP"), lab("J06.9", "内科", "BLD-WBC")));
        when(diagnosisOperationMapper.selectList(any())).thenReturn(List.of(
                op("N80.0", "妇科", "68.3101", 3)));
        repo.load();

        Disease peds = repo.diseasesFor(10, 1).get(0);
        // 儿科有精确行时必须只用它 -> 只剩药 1
        assertThat(repo.drugsOf(peds)).extracting(DiagnosisDrug::getDrugId).containsExactly(1L);
        // 检验同理：儿科的清单里没有 BLD-WBC
        assertThat(repo.labsOf(peds)).extracting(DiagnosisLab::getItemCode).containsExactly("BLD-CRP");

        Disease gyn = repo.diseasesFor(30, 1).stream()
                .filter(d -> d.icdCode().equals("N80.0")).findFirst().orElseThrow();
        assertThat(repo.operationsOf(gyn)).extracting(DiagnosisOperation::getOperationCode)
                .containsExactly("68.3101");
        // 没有手术本体的诊断返回空表，不是抛异常、也不是返回别科的术式
        assertThat(repo.operationsOf(peds)).isEmpty();
    }

    @Test
    @DisplayName("doseOf 没有行就返回 null，绝不返回默认剂量")
    void dose_of_returns_null_when_absent() {
        when(drugDoseMapper.selectList(any())).thenReturn(List.of(dose(1L, "0.5", "每日3次", "1.5")));
        repo.load();

        assertThat(repo.doseOf(1L).getFrequency()).isEqualTo("每日3次");
        assertThat(repo.doseOf(999L)).isNull();
        assertThat(repo.doseOf(null)).isNull();
    }

    @Test
    @DisplayName("就诊率乘子：性别专属行取代同段的 gender=0 行")
    void visit_multiplier_prefers_the_gender_specific_row() {
        VisitRate both = visitRate(15, 24, 0, "0.760");
        VisitRate male = visitRate(15, 24, 1, "0.560");
        VisitRate female = visitRate(15, 24, 2, "0.960");
        VisitRate elderly = visitRate(65, 89, 0, "2.000");
        when(visitRateMapper.selectList(any())).thenReturn(List.of(both, male, female, elderly));
        repo.load();

        // 患者性别 0=男 1=女 要映射到本表的 1=男 2=女
        assertThat(repo.visitMultiplier(20, 0)).isEqualByComparingTo("0.560");
        assertThat(repo.visitMultiplier(20, 1)).isEqualByComparingTo("0.960");
        assertThat(repo.visitMultiplier(70, 1)).isEqualByComparingTo("2.000");
        // 年龄段没覆盖到 -> 不缩放，不抛异常
        assertThat(repo.visitMultiplier(60, 0)).isEqualByComparingTo("1.000");
    }

    @Test
    @DisplayName("month_weights 只在加载时解析一次；写坏的 JSON 必须抛错而不是悄悄当成无季节性")
    void month_weights_are_validated_at_load_time() {
        assertThat(OntologyRepository.parseMonthWeights(null)).hasSize(12).containsOnly(1.0);
        assertThat(OntologyRepository.parseMonthWeights("[1.4,1.3,1.1,0.9,0.8,0.7,0.7,0.8,0.9,1.0,1.2,1.4]"))
                .hasSize(12);
        // 写坏的 JSON 若被当成「全 1」，季节分布会整体消失且没有任何报错——这是最该被发现的失败
        assertThatThrownBy(() -> OntologyRepository.parseMonthWeights("[1,2,3]"))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("长度 12");
        assertThatThrownBy(() -> OntologyRepository.parseMonthWeights("not json"))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("month_weights");
        assertThatThrownBy(() -> OntologyRepository.parseMonthWeights("[1,1,1,1,1,1,1,1,1,1,1,-1]"))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("正数");
    }

    @Test
    @DisplayName("load 可重复调用：第二次重载会清掉上一次的筛选缓存")
    void load_is_repeatable_and_clears_the_cache() {
        stubThreeDiseases();
        repo.load();
        assertThat(repo.diseasesFor(30, 1)).hasSize(2);

        // 库里的诊断被换掉后重载，缓存必须跟着换
        when(deptDiagnosisMapper.selectList(any())).thenReturn(List.of(
                dd("内科", "E11.9", "2型糖尿病", 0, 18, 89, 1, 1, "3.600", null, "0.1100")));
        repo.load();

        assertThat(repo.diseasesFor(30, 1)).extracting(Disease::icdCode).containsExactly("E11.9");
        assertThat(repo.diseaseCount()).isEqualTo(1);
    }

    @Test
    @DisplayName("未 load 就查询返回空表，不是 NPE")
    void queries_before_load_return_empty_not_npe() {
        assertThat(repo.diseasesFor(30, 0)).isEmpty();
        assertThat(repo.diseaseCount()).isZero();
        assertThat(repo.visitMultiplier(30, 0)).isEqualByComparingTo("1.000");
    }

    private DiagnosisDrug drug(String icd, String dept, Long drugId) {
        DiagnosisDrug d = new DiagnosisDrug();
        d.setIcdCode(icd);
        d.setDepartment(dept);
        d.setDrugId(drugId);
        d.setLine(1);
        d.setWeight(new BigDecimal("1.000"));
        d.setPostop(0);
        return d;
    }

    private DiagnosisLab lab(String icd, String dept, String itemCode) {
        DiagnosisLab l = new DiagnosisLab();
        l.setIcdCode(icd);
        l.setDepartment(dept);
        l.setItemCode(itemCode);
        l.setItemName(itemCode);
        l.setAbnormalDirection("NONE");
        l.setAbnormalProb(BigDecimal.ZERO);
        l.setWeight(new BigDecimal("1.000"));
        l.setRequired(0);
        return l;
    }

    private DiagnosisOperation op(String icd, String dept, String code, int level) {
        DiagnosisOperation o = new DiagnosisOperation();
        o.setIcdCode(icd);
        o.setDepartment(dept);
        o.setOperationCode(code);
        o.setOperationName("术式" + code);
        o.setOperationLevel(level);
        o.setProbability(new BigDecimal("0.800"));
        o.setMinPostopDays(3);
        o.setWeight(new BigDecimal("1.000"));
        return o;
    }

    private DrugDose dose(Long drugId, String single, String freq, String maxDaily) {
        DrugDose d = new DrugDose();
        d.setDrugId(drugId);
        d.setSingleDose(new BigDecimal(single));
        d.setDoseUnit("片");
        d.setFrequency(freq);
        d.setMaxDailyDose(new BigDecimal(maxDaily));
        d.setDaysMin(3);
        d.setDaysMax(7);
        return d;
    }

    private VisitRate visitRate(int ageMin, int ageMax, int gender, String multiplier) {
        VisitRate v = new VisitRate();
        v.setAgeMin(ageMin);
        v.setAgeMax(ageMax);
        v.setGender(gender);
        v.setMultiplier(new BigDecimal(multiplier));
        return v;
    }
}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-generator -Dtest=OntologyRepositoryTest test
```

Expected: 编译失败——`Disease`、`OntologyRepository` 符号找不到。

- [ ] **Step 3: 新建 `Disease.java`**

```java
package com.his.generator;

/**
 * 一条「患者可以得的病」——{@code dept_diagnosis} 一行的展开形式。
 *
 * <p><b>为什么不直接用 {@code DeptDiagnosis}：</b>{@code month_weights} 是 JSON 文本，
 * 而季节乘子在加权抽样阶段就要参与计算（§5.2 的 {@code weight × month_weights[当月]}），
 * 在热循环里每次解析是纯浪费。展平后生成器的每一层抽样都只读这个 record，
 * 不再碰实体、不再解析 JSON。
 *
 * <p><b>{@code gender} 是本体域（0不限/1仅男/2仅女），不是患者域</b>——
 * 判定一律走 {@code OntologyRules.ageGenderMatches}，不要在这里手写比较。
 *
 * @param department   该病所属科室。**诊断决定科室**（§6.1），生成器不再随机选科室。
 * @param monthWeights 长度 12 的季节乘子，索引 0=1月；来自 {@code month_weights}，NULL 时全 1。
 */
public record Disease(String icdCode, String name, String department,
                      int gender, Integer minAge, Integer maxAge,
                      boolean chronic, boolean admissionEligible,
                      double weight, double[] monthWeights, double prevalence) {

    /** 指定月份（1~12）的季节乘子。 */
    public double monthWeight(int month) {
        return monthWeights[month - 1];
    }
}
```

- [ ] **Step 4: 新建 `OntologyRepository.java`**

```java
package com.his.generator;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import com.his.common.ontology.OntologyRules;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.*;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 医学本体的一次性内存加载与取样（生成器侧）。
 *
 * <p>逐层条件抽样（§6.1）每生成一个事件都要问「这个患者能得哪些病」「这个诊断该开哪些药」，
 * 调用量在百万级，不能每次都打数据库。故 {@link #load()} 一次全量载入，
 * 之后所有查询只读内存。
 *
 * <p><b>它不是缓存，是快照</b>：本体表在生成过程中不会被写，所以没有失效问题；
 * 但生成开始前必须调用 {@link #load()}，否则查询返回空表。
 *
 * <p>本类只做「取数 + 按规则筛」，**不做加权抽样**——抽样在 Task 14/15 里做，
 * 因为权重还取决于患者个体（{@code acutePropensity}），不属于本体。
 */
@Component
@RequiredArgsConstructor
public class OntologyRepository {

    private static final ObjectMapper JSON = new ObjectMapper();
    /** {@code month_weights} 为 NULL 时使用：12 个月全 1，即无季节性。 */
    private static final double[] NO_SEASONALITY = {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1};

    private final DeptDiagnosisMapper deptDiagnosisMapper;
    private final DiagnosisDrugMapper diagnosisDrugMapper;
    private final DiagnosisLabMapper diagnosisLabMapper;
    private final DiagnosisOperationMapper diagnosisOperationMapper;
    private final DrugDoseMapper drugDoseMapper;
    private final VisitRateMapper visitRateMapper;

    private List<Disease> diseases = List.of();
    private Map<String, List<DiagnosisDrug>> drugsByIcd = Map.of();
    private Map<String, List<DiagnosisLab>> labsByIcd = Map.of();
    private Map<String, List<DiagnosisOperation>> operationsByIcd = Map.of();
    private Map<Long, DrugDose> doseByDrugId = Map.of();
    private List<VisitRate> visitRates = List.of();
    /** 按 (年龄, 性别) 缓存的适用诊断列表；load() 时清空。 */
    private final Map<Integer, List<Disease>> applicableCache = new HashMap<>();

    /** 全量重载。生成开始前调用一次；重复调用等价于重新取快照。 */
    public void load() {
        diseases = deptDiagnosisMapper.selectList(null).stream()
                .map(OntologyRepository::toDisease)
                .toList();
        drugsByIcd = groupByIcd(diagnosisDrugMapper.selectList(null), DiagnosisDrug::getIcdCode);
        labsByIcd = groupByIcd(diagnosisLabMapper.selectList(null), DiagnosisLab::getIcdCode);
        operationsByIcd = groupByIcd(diagnosisOperationMapper.selectList(null),
                DiagnosisOperation::getIcdCode);
        doseByDrugId = drugDoseMapper.selectList(null).stream()
                .collect(Collectors.toMap(DrugDose::getDrugId, d -> d, (a, b) -> a));
        visitRates = visitRateMapper.selectList(null);
        applicableCache.clear();
    }

    /** 本体收录的诊断总数（生成结束时的统计输出用）。 */
    public int diseaseCount() {
        return diseases.size();
    }

    /**
     * 该年龄性别适用的**全部**诊断，含慢病。
     *
     * <p>慢病也在这里，因为 {@code chronic=1} 只表示「可终身携带、走随访」，
     * 不表示它不会急性发作。若把慢病排除在外，「慢阻肺患者冬天急性加重来就诊」
     * 就永远不会出现——那比现在更不真实。
     *
     * <p>判定走 {@link OntologyRules#ageGenderMatches}，与校验器**同一个判定**：
     * 各写一份的话，生成器造出来的数据会被校验器判违规。
     */
    public List<Disease> diseasesFor(int age, int gender) {
        return applicableCache.computeIfAbsent(cacheKey(age, gender), k -> diseases.stream()
                .filter(d -> OntologyRules.ageGenderMatches(
                        d.gender(), d.minAge(), d.maxAge(), age, gender))
                .toList());
    }

    /** 该年龄性别适用的慢病池（§5.1 建档、§5.2 随访事件）。 */
    public List<Disease> chronicDiseasesFor(int age, int gender) {
        return diseasesFor(age, gender).stream().filter(Disease::chronic).toList();
    }

    /** 该年龄性别适用、且可收住院的诊断（§6.2：仅这些能产生 admission）。 */
    public List<Disease> admissionEligibleDiseasesFor(int age, int gender) {
        return diseasesFor(age, gender).stream().filter(Disease::admissionEligible).toList();
    }

    /** 该诊断适用的药品行；科室优先级由 {@link OntologyRules#select} 决定，与校验器同一规则。 */
    public List<DiagnosisDrug> drugsOf(Disease d) {
        return OntologyRules.select(drugsByIcd.getOrDefault(d.icdCode(), List.of()),
                DiagnosisDrug::getDepartment, DiagnosisDrug::getIcdCode, d.department(), d.icdCode());
    }

    /** 该诊断适用的检验行；规则同上。 */
    public List<DiagnosisLab> labsOf(Disease d) {
        return OntologyRules.select(labsByIcd.getOrDefault(d.icdCode(), List.of()),
                DiagnosisLab::getDepartment, DiagnosisLab::getIcdCode, d.department(), d.icdCode());
    }

    /** 该诊断适用的手术行；无行为空表（表示「这个诊断不做手术」，不是「查不到」）。 */
    public List<DiagnosisOperation> operationsOf(Disease d) {
        return OntologyRules.select(operationsByIcd.getOrDefault(d.icdCode(), List.of()),
                DiagnosisOperation::getDepartment, DiagnosisOperation::getIcdCode,
                d.department(), d.icdCode());
    }

    /**
     * 药品剂量规则。**没有行就返回 {@code null}，绝不返回默认剂量**——
     * {@code drug_dose} 缺行说明该药「按每日次数给药」这个模型表达不了
     * （如甲氨蝶呤是每周给药，见 Task 8 注②）。调用方必须据此跳过该药并计数报出，
     * 不得自己编一个「每日 3 次、每次 1 片」。
     */
    public DrugDose doseOf(Long drugId) {
        return drugId == null ? null : doseByDrugId.get(drugId);
    }

    /**
     * 就诊率乘子（§5.3 的年龄别 U 形曲线）。
     *
     * <p>性别语义由 Task 3 的种子写死：**{@code gender=1/2} 的行取代同段 {@code gender=0} 的行**，
     * 所以先找性别精确匹配、找不到才用 {@code gender=0} 的行。年龄用闭区间包含。
     *
     * @param gender 患者性别（0=男 1=女），需映射到本表的 1=男 2=女
     * @return 找不到任何匹配行时返回 {@code 1.000}（不缩放）。**不抛异常**——
     *         {@code visit_rate} 是调节项，缺一行不该让整个生成任务失败。
     */
    public BigDecimal visitMultiplier(int age, int gender) {
        int target = gender == 0 ? 1 : 2;
        BigDecimal fallback = null;
        for (VisitRate r : visitRates) {
            if (r.getAgeMin() == null || r.getAgeMax() == null
                    || age < r.getAgeMin() || age > r.getAgeMax()) {
                continue;
            }
            int g = r.getGender() == null ? 0 : r.getGender();
            if (g == target) {
                return r.getMultiplier();   // 性别精确行直接胜出，与遍历顺序无关
            }
            if (g == 0) {
                fallback = r.getMultiplier();
            }
        }
        return fallback == null ? BigDecimal.ONE : fallback;
    }

    /**
     * 解析 {@code month_weights}。
     *
     * <p><b>解析失败一律抛异常，不静默退回「无季节性」</b>：一份写坏的 JSON 若被当成全 1，
     * 季节分布会整体消失而没有任何报错——这正是最该被发现的失败。Task 9 的种子一致性
     * 测试已先卡一道，这里是运行时的第二道。
     */
    static double[] parseMonthWeights(String json) {
        if (json == null || json.isBlank()) {
            return NO_SEASONALITY.clone();
        }
        double[] w;
        try {
            w = JSON.readValue(json, double[].class);
        } catch (Exception e) {
            throw new IllegalStateException("month_weights 不是合法的 JSON 数值数组: " + json, e);
        }
        if (w.length != 12) {
            throw new IllegalStateException(
                    "month_weights 必须是长度 12 的数组，实为 " + w.length + ": " + json);
        }
        for (double v : w) {
            if (v <= 0) {
                throw new IllegalStateException("month_weights 必须全为正数: " + json);
            }
        }
        return w;
    }

    private static Disease toDisease(DeptDiagnosis r) {
        return new Disease(
                r.getIcdCode(),
                r.getDiagnosisName(),
                r.getDepartment(),
                r.getGender() == null ? 0 : r.getGender(),
                r.getMinAge(),
                r.getMaxAge(),
                r.getChronic() != null && r.getChronic() == 1,
                r.getAdmissionEligible() != null && r.getAdmissionEligible() == 1,
                r.getWeight() == null ? 0d : r.getWeight().doubleValue(),
                parseMonthWeights(r.getMonthWeights()),
                r.getPrevalence() == null ? 0d : r.getPrevalence().doubleValue());
    }

    private static <T> Map<String, List<T>> groupByIcd(List<T> rows, Function<T, String> icdOf) {
        Map<String, List<T>> out = new HashMap<>();
        for (T r : rows) {
            out.computeIfAbsent(icdOf.apply(r), k -> new ArrayList<>()).add(r);
        }
        return out;
    }

    private static int cacheKey(int age, int gender) {
        return age * 2 + gender;
    }
}
```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd backend && mvn -o -pl his-generator -Dtest=OntologyRepositoryTest test
```

Expected: Tests run: 8, Failures: 0, Errors: 0。

- [ ] **Step 6: 把仓库接进 `DataGeneratorService`（本任务只接线，不改变生成行为）**

在 `private final ClinicalPathwayMapper pathwayMapper;` **之后**追加一个字段，
使 `@RequiredArgsConstructor` 生成的构造器在末位多一个参数：

```java
    private final OntologyRepository ontologyRepository;
```

在 `generate(long patientCount)` 里 `pathways = pathwayMapper.selectList(...)` 那一行**之后**加：

```java
        ontologyRepository.load();
```

> **本步刻意不改变任何生成行为**：`load()` 的结果此任务内还没人读，
> 生成结果与改造前逐字节相同。这样 Task 13 的验收就只剩「既有测试仍全绿」，
> 而不会把「接线错了」和「抽样改错了」两类问题混在一次提交里。

- [ ] **Step 7: 修 `DataGeneratorServiceTest` 的构造器调用**

`buildService()` 里 `new DataGeneratorService(...)` 末位追加一个 mock：

```java
        OntologyRepository ontologyRepository = mock(OntologyRepository.class);

        return new DataGeneratorService(pm, vm, dm, prescriptionMapper, im, lm, drugM, ruleConfigMapper,
                doctorMapper, jdbcTemplate, chargeM, dispenseM, rationalRuleMapper,
                admissionMapper, medicalOrderMapper, vitalSignMapper, pathwayMapper,
                ontologyRepository);
```

> mock 的 `load()` 什么都不做，不影响既有断言——Task 13 内生成器还没读本体。

- [ ] **Step 8: 跑生成器模块全部测试**

```bash
cd backend && mvn -o -pl his-generator test
```

Expected: BUILD SUCCESS；`DataGeneratorServiceTest` 与 `OntologyRepositoryTest` 全绿。
**这一步是 Task 13 真正的验收**：它证明「接线 + 快照加载」没有惊动既有的 11 张表生成逻辑。
若 `DataGeneratorServiceTest` 红了，先看是不是构造器参数顺序错了——
`@RequiredArgsConstructor` 按**字段声明顺序**生成，所以新字段必须加在 `pathwayMapper` 之后。

- [ ] **Step 9: Commit**

```bash
git add backend/his-generator/src/main/java/com/his/generator/Disease.java \
        backend/his-generator/src/main/java/com/his/generator/OntologyRepository.java \
        backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java \
        backend/his-generator/src/test/java/com/his/generator/OntologyRepositoryTest.java \
        backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java
git commit -m "feat(generator): 本体一次性加载与按科室取样 —— 逐层条件抽样的数据源" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 14: `PatientProfile` —— 患者疾病档案（阶段二）

**Files:**
- Create: `backend/his-generator/src/main/java/com/his/generator/PatientProfile.java`
- Modify: `backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java`（`newPatient()` 的生日推导、新增 `buildProfile`、`flushPatients` 建档）
- Test: `backend/his-generator/src/test/java/com/his/generator/PatientProfileTest.java`
- Modify: `backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java:269-270`（年龄断言 18→0）

**Interfaces:**
- Consumes: Task 13 的 `Disease` / `OntologyRepository.chronicDiseasesFor` / `visitMultiplier`
- Produces:
  - `PatientProfile` record：`patientId()`(long) / `age()`(int) / `gender()`(int) /
    `chronicDiseases()`(`List<Disease>`，不可变) / `acutePropensity()`(double) /
    `visitMultiplier()`(BigDecimal) / `effectiveAcuteRate()`(double)
  - `static PatientProfile build(long patientId, int age, int gender, List<Disease> chronicPool, BigDecimal visitMultiplier, RandomGenerator rnd)`
  - `static final int MAX_CHRONIC_DISEASES = 2`
  - `DataGeneratorService`：私有 `PatientProfile buildProfile(Patient p)`；字段
    `Map<Long, PatientProfile> profilesByPatient`

**这一步补的是 §5.1，也就是「虚拟**患者**」里「患者」两个字的落点**：现在建档只抽年龄性别，
患者之间除了年龄性别没有任何差别，于是「同一位患者反复因同一慢病就诊」这种纵向特征
**不可能出现**——每次就诊的诊断都从全局池独立抽。档案一旦建起来，
「这个人有什么病」就成了后续所有就诊的约束条件。

**档案不落库**（spec §5.1 已定）：它是生成过程的中间状态，不是 HIS 业务数据。
落库会污染数据集、增加一张表与一套迁移成本，而且真实 HIS 里也没有「患者疾病档案」这张表。

**快照语义**：慢病**终身携带**，所以档案建好之后**不再修改**——
record + `List.copyOf` 保证这一点，调用方想改也改不了。若日后需要「新发慢病」，
那是往档案里追加的能力，必须显式设计，不能靠改这个不可变对象。

**为什么 `build` 接 `RandomGenerator` 而不是内部用 `ThreadLocalRandom`**：
加权抽样必须可复现才能测。接一个 `RandomGenerator`（`ThreadLocalRandom` 实现了这个接口，
所以生产代码原样传进去即可），测试传 `new Random(固定种子)` 就能精确断言。

- [ ] **Step 1: 写失败的测试**

新建 `backend/his-generator/src/test/java/com/his/generator/PatientProfileTest.java`：

```java
package com.his.generator;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import java.util.random.RandomGeneratorFactory;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class PatientProfileTest {

    /** 固定种子：结论可复现，不会偶发失败。 */
    private static final long SEED = 20260921L;

    private Disease chronic(String icd, String name, double prevalence) {
        return new Disease(icd, name, "内科", 0, 18, 89, true, true,
                1.0, new double[12], prevalence);
    }

    @Test
    @DisplayName("患病率 1.0 必被抽中，患病率 0 必不被抽中")
    void prevalence_one_is_always_carried_and_zero_never_is() {
        for (int i = 0; i < 50; i++) {
            PatientProfile p = PatientProfile.build(1L, 50, 0,
                    List.of(chronic("I10", "原发性高血压", 1.0),
                            chronic("X00", "零患病率", 0.0)),
                    BigDecimal.ONE, new Random(SEED + i));

            assertThat(p.chronicDiseases()).extracting(Disease::icdCode).containsExactly("I10");
        }
    }

    @Test
    @DisplayName("慢病最多携带 2 个：患病率都是 1.0 时也不能超")
    void chronic_diseases_are_capped_at_two() {
        List<Disease> pool = List.of(
                chronic("I10", "原发性高血压", 1.0),
                chronic("E11.9", "2型糖尿病", 1.0),
                chronic("K29.5", "慢性胃炎", 1.0),
                chronic("E78.5", "高脂血症", 1.0));
        for (int i = 0; i < 50; i++) {
            PatientProfile p = PatientProfile.build(1L, 50, 0, pool, BigDecimal.ONE,
                    new Random(SEED + i));
            assertThat(p.chronicDiseases()).hasSize(PatientProfile.MAX_CHRONIC_DISEASES);
        }
    }

    @Test
    @DisplayName("携带数量由患病率驱动，而不是固定抽 1~2 个")
    void carry_rate_follows_the_prevalence() {
        int carried = 0;
        int draws = 1000;
        for (int i = 0; i < draws; i++) {
            // **这里不能用 java.util.Random**：`new Random(seed).nextDouble()`（构造后取到的
            // 第一个 double）有已知偏置——实测 1000 个连续种子，首个值全部落在 [0.466, 0.597]，
            // 永远不小于 0.25。本用例的池只放 1 个病，Fisher-Yates 一次随机数都不消耗，
            // 于是「第一个 nextDouble」正好就是那次患病率判定，`carried` 会恒为 0（断言必红）。
            // 换成 L64X128MixRandom 后首个值均匀，250 上下。
            PatientProfile p = PatientProfile.build(1L, 50, 0,
                    List.of(chronic("I10", "原发性高血压", 0.25)),
                    BigDecimal.ONE, RandomGeneratorFactory.of("L64X128MixRandom").create(SEED + i));
            carried += p.chronicDiseases().size();
        }
        // 250 次上下；带宽取得宽松（0.20~0.30），只证明「按患病率抽」而不是「人人都得」
        assertThat(carried).isBetween(200, 300);
    }

    @Test
    @DisplayName("空慢病池给出零慢病档案，不是抛异常——0 岁的孩子本来就没有慢病")
    void an_empty_pool_yields_zero_chronic_diseases() {
        PatientProfile p = PatientProfile.build(1L, 0, 1, List.of(), BigDecimal.ONE, new Random(SEED));

        assertThat(p.chronicDiseases()).isEmpty();
        assertThat(p.age()).isZero();
    }

    @Test
    @DisplayName("急性病倾向落在 [0.5, 2.0)")
    void acute_propensity_is_within_range() {
        for (int i = 0; i < 1000; i++) {
            PatientProfile p = PatientProfile.build(1L, 50, 0, List.of(), BigDecimal.ONE,
                    new Random(SEED + i));
            assertThat(p.acutePropensity()).isGreaterThanOrEqualTo(0.5).isLessThan(2.0);
        }
    }

    @Test
    @DisplayName("有效就诊率 = 个体倾向 × 就诊率乘子（§5.3 的年龄别 U 形曲线从这里生效）")
    void effective_rate_multiplies_propensity_by_the_visit_multiplier() {
        PatientProfile p = PatientProfile.build(1L, 2, 0, List.of(),
                new BigDecimal("2.200"), new Random(SEED));

        assertThat(p.visitMultiplier()).isEqualByComparingTo("2.200");
        assertThat(p.effectiveAcuteRate()).isEqualTo(p.acutePropensity() * 2.200, within(1e-9));
    }

    @Test
    @DisplayName("档案不可变：慢病列表改不动（终身携带，建好即冻结）")
    void the_profile_is_immutable() {
        PatientProfile p = PatientProfile.build(1L, 50, 0,
                List.of(chronic("I10", "原发性高血压", 1.0)), BigDecimal.ONE, new Random(SEED));

        assertThatThrownBy(() -> p.chronicDiseases().add(chronic("E11.9", "2型糖尿病", 1.0)))
                .isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    @DisplayName("建档不改动传入的池子（调用方复用的是仓库里缓存的列表）")
    void build_does_not_mutate_the_incoming_pool() {
        List<Disease> pool = new ArrayList<>(List.of(
                chronic("I10", "原发性高血压", 1.0), chronic("E11.9", "2型糖尿病", 1.0)));
        PatientProfile.build(1L, 50, 0, pool, BigDecimal.ONE, new Random(SEED));

        assertThat(pool).hasSize(2).extracting(Disease::icdCode)
                .containsExactlyInAnyOrder("I10", "E11.9");
    }
}
```

> `within(1e-9)` 需要 `import static org.assertj.core.api.Assertions.within;`。
> 写实现时一并补上 import，否则编译失败。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-generator -Dtest=PatientProfileTest test
```

Expected: 编译失败——`PatientProfile` 符号找不到。

- [ ] **Step 3: 新建 `PatientProfile.java`**

```java
package com.his.generator;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.random.RandomGenerator;

/**
 * 患者疾病档案（§5.1）——「这个患者有什么病」，建档时抽定，之后作为所有就诊的约束。
 *
 * <p><b>不落库</b>（spec §5.1）：它是生成过程的中间状态，不是 HIS 业务数据。
 * 落库会污染数据集、多一张表与一套迁移成本，而真实 HIS 里也没有这张表。
 *
 * <p><b>建好即冻结</b>：慢病「终身携带」，所以本 record 不可变、慢病列表是
 * {@link List#copyOf} 的不可修改视图。日后若要支持「新发慢病」，那是往档案里追加的能力，
 * 必须显式设计，不能靠改这个对象。
 *
 * @param age             实际年龄，由 {@code birth_date} 算出——**与校验器同一算法**，
 *                        不能用另一套口径，否则校验器会算出不同的年龄、判定随之不同
 * @param chronicDiseases 终身携带的慢病，0~{@value #MAX_CHRONIC_DISEASES} 个
 * @param acutePropensity 个体急性就诊疏密倾向，[0.5, 2.0)
 * @param visitMultiplier 该年龄性别的就诊率乘子（§5.3 U 形曲线）
 */
public record PatientProfile(long patientId, int age, int gender,
                             List<Disease> chronicDiseases, double acutePropensity,
                             BigDecimal visitMultiplier) {

    /** 一名患者最多携带的慢病数（spec §5.1：抽 0~2 个）。 */
    public static final int MAX_CHRONIC_DISEASES = 2;

    /** 急性倾向下界。 */
    static final double MIN_ACUTE_PROPENSITY = 0.5;
    /** 急性倾向上界（不含）。 */
    static final double MAX_ACUTE_PROPENSITY = 2.0;

    public PatientProfile {
        chronicDiseases = List.copyOf(chronicDiseases);
    }

    /**
     * 事件疏密的最终乘子：个体倾向 × 该年龄性别的就诊率乘子。
     *
     * <p>§5.3 那张「年龄别 U 形曲线」的表格就是通过这里生效的——
     * 2 岁与 70 岁的患者事件更密，25~44 岁的男性最稀。没有这一步，
     * {@code visit_rate} 表就只是一张没人读的配置。
     */
    public double effectiveAcuteRate() {
        return acutePropensity * visitMultiplier.doubleValue();
    }

    /**
     * 建档：以各慢病**自身的基线患病率**独立判定是否携带，最多 {@value #MAX_CHRONIC_DISEASES} 个。
     *
     * <p><b>为什么不是「从慢病池里按权重抽 0~2 个」</b>：那样抽出来的数量分布与患病率无关——
     * 常见做法（均匀抽 0/1/2）会让 2/3 的人背上慢病，而真实的慢病患病率是
     * 「多数人没有、少数人有一到两种」。按各行自己的 {@code prevalence} 独立判定，
     * 抽出的数量分布自然服从患病率：{@code prevalence} 求和越小，无慢病的患者越多。
     *
     * <p><b>遍历顺序先打乱</b>：否则一旦命中数超过上限，被保留下来的永远是种子里靠前的几行
     * （Task 4 把内科排在最前），而「谁被留下」本该与行序无关。
     *
     * @param chronicPool      该年龄性别适用的慢病池，取自 {@code OntologyRepository.chronicDiseasesFor}
     * @param visitMultiplier  该年龄性别的就诊率乘子，取自 {@code OntologyRepository.visitMultiplier}
     * @param rnd              随机源。生产代码传 {@code ThreadLocalRandom.current()}，
     *                         测试传固定种子的 {@code Random} 以便精确断言
     */
    public static PatientProfile build(long patientId, int age, int gender,
                                       List<Disease> chronicPool, BigDecimal visitMultiplier,
                                       RandomGenerator rnd) {
        List<Disease> shuffled = new ArrayList<>(chronicPool);
        // Fisher-Yates，只用 rnd 的 nextInt，保证 RandomGenerator 接口足够
        for (int i = shuffled.size() - 1; i > 0; i--) {
            int j = rnd.nextInt(i + 1);
            Collections.swap(shuffled, i, j);
        }

        List<Disease> carried = new ArrayList<>(MAX_CHRONIC_DISEASES);
        for (Disease d : shuffled) {
            if (carried.size() >= MAX_CHRONIC_DISEASES) {
                break;
            }
            if (rnd.nextDouble() < d.prevalence()) {
                carried.add(d);
            }
        }

        double propensity = MIN_ACUTE_PROPENSITY
                + rnd.nextDouble() * (MAX_ACUTE_PROPENSITY - MIN_ACUTE_PROPENSITY);
        return new PatientProfile(patientId, age, gender, carried, propensity, visitMultiplier);
    }
}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd backend && mvn -o -pl his-generator -Dtest=PatientProfileTest test
```

Expected: Tests run: 8, Failures: 0, Errors: 0。

- [ ] **Step 5: 改 `newPatient()` —— 年龄改为 0~89 岁**

这是**儿科能有真实患者的前提**（spec §5.1 第 1 条）：现在最小 18 岁，
`dept_diagnosis` 里那些 `0~14` 岁的儿科诊断永远抽不到，儿科医生只能看成人病。

把 `newPatient()` 里从 `// 生日改为「随机年 + 随机月 + 1..28 日」` 到
`p.setBirthDate(birthDate);` 的整段替换为：

```java
        // 直接抽「实际年龄」而不是「出生年份」再往回推算：
        // 后者会因为「今年生日还没过」而漂移 ±1 岁，年龄下界收到 18 岁时靠一次补偿掩盖过去了，
        // 但下界一旦放到 0（新生儿），漂移就会算出 -1 岁——一个不可能存在的人。
        // 改为 today.minusYears(age).minusDays(0..364)：年龄精确等于 age，
        // 而月日仍是随机的（不再所有人共享生成日的月日）。
        LocalDate today = LocalDate.now();
        int age = rnd.nextInt(0, 90);
        LocalDate birthDate = today.minusYears(age).minusDays(rnd.nextLong(0, 365));
        p.setBirthDate(birthDate);
```

同时把该方法 javadoc 里的「年龄仍在 18–89 岁」改为：
`年龄覆盖 0–89 岁（含新生儿，使儿科诊断可被真实触达）`。

> **`age` 是局部变量，不要试图从 `newPatient` 返回它**：档案里的年龄另由
> `buildProfile` 从 {@code birth_date} 反算（下一步），全流程只有「由出生日期算年龄」这一个口径。

- [ ] **Step 6: 加 `buildProfile` 与档案表，并在 `flushPatients` 里建档**

在 `DataGeneratorService` 的字段区（`private long admissionCount, orderCount, vitalCount;` 之后）加：

```java
    /** 本次生成的患者疾病档案；按 patientId 索引。生成结束后即弃，不落库（spec §5.1）。 */
    private final Map<Long, PatientProfile> profilesByPatient = new HashMap<>();
```

在 `generate(long patientCount)` 开头、`clearDataTables()` 附近加清空：

```java
        profilesByPatient.clear();
```

> 若 `clearBeforeRun` 为 false 且服务被复用，上一轮的档案会串到这一轮——
> 档案按 patientId 索引，而新患者的 id 是自增的、不会碰撞，但清空仍然是对的：
> 不清就等于让内存随调用次数无界增长。

新增方法（放在 `newPatient()` 之后）：

```java
    /**
     * 建档（§5.1）：年龄性别 → 慢病池 → 抽 0~2 个慢病 + 急性倾向。
     *
     * <p>年龄**由 birth_date 反算**，与校验器（Task 11 的 `Period.between`）同一口径。
     * 若这里改成「建档时顺手记下抽的年龄」，两边一旦不一致，
     * 校验器就会拿另一个年龄去判「这个诊断适不适用」，凭空造出违规。
     */
    private PatientProfile buildProfile(Patient p) {
        int age = Period.between(p.getBirthDate(), LocalDate.now()).getYears();
        int gender = p.getGender() == null ? 0 : p.getGender();
        return PatientProfile.build(p.getId(), age, gender,
                ontologyRepository.chronicDiseasesFor(age, gender),
                ontologyRepository.visitMultiplier(age, gender),
                rnd);
    }
```

在 `flushPatients` 中 `patientMapper.insert(patients);`（回填 id 那一行）**之后**加：

```java
        for (Patient p : patients) {
            profilesByPatient.put(p.getId(), buildProfile(p));
        }
```

> 必须放在 `insert` 之后：`buildProfile` 用 `p.getId()` 作键，而 id 是 insert 时才回填的。

- [ ] **Step 7: 改 `DataGeneratorServiceTest` 的年龄断言**

`DataGeneratorServiceTest` 第 269~270 行现在是：

```java
            // 年龄仍落在 18-89 岁
            assertThat(Period.between(p.getBirthDate(), LocalDate.now()).getYears()).isBetween(18, 89);
```

改为：

```java
            // 年龄覆盖 0-89 岁（含新生儿，这是儿科诊断能被触达的前提）
            assertThat(Period.between(p.getBirthDate(), LocalDate.now()).getYears()).isBetween(0, 89);
```

- [ ] **Step 8: 跑生成器模块全部测试**

```bash
cd backend && mvn -o -pl his-generator test
```

Expected: BUILD SUCCESS。`PatientProfileTest` 8 个用例全绿；
`DataGeneratorServiceTest` 除年龄断言外**逐条不变**——本任务还没改任何抽样逻辑，
档案建了但没人读，诊断仍从 `rule_config` 随机抽。

若 `DataGeneratorServiceTest` 有别的用例红了，检查 `buildProfile` 是不是用了
`ontologyRepository` 的**未打桩方法**：mock 的 `chronicDiseasesFor` 返回 `null` 而非空表，
`PatientProfile.build` 会在 `new ArrayList<>(null)` 处 NPE。修法是在
`buildService()` 里补一行 `when(ontologyRepository.chronicDiseasesFor(anyInt(), anyInt())).thenReturn(List.of());`
以及 `when(ontologyRepository.visitMultiplier(anyInt(), anyInt())).thenReturn(BigDecimal.ONE);`。

- [ ] **Step 9: Commit**

```bash
git add backend/his-generator/src/main/java/com/his/generator/PatientProfile.java \
        backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java \
        backend/his-generator/src/test/java/com/his/generator/PatientProfileTest.java \
        backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java
git commit -m "feat(generator): 患者疾病档案 —— 慢病终身携带，年龄覆盖 0~89 岁" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 15: `EventCalendar` —— 事件日历与逐层条件抽样的时间层（阶段二）

**Files:**
- Create: `backend/his-generator/src/main/java/com/his/generator/PatientEvent.java`
- Create: `backend/his-generator/src/main/java/com/his/generator/EventCalendar.java`
- Test: `backend/his-generator/src/test/java/com/his/generator/EventCalendarTest.java`

**Interfaces:**
- Consumes: Task 13 的 `Disease`（`weight()` / `monthWeight(int)`）；Task 14 的 `PatientProfile`（`chronicDiseases()` / `effectiveAcuteRate()` / `age()`）
- Produces:
  - `PatientEvent` record：`time()`(LocalDateTime) / `disease()`(Disease) / `kind()`(Kind)；
    嵌套 `enum Kind { FOLLOW_UP, ACUTE, ROUTINE, RECHECK }`
  - `static List<PatientEvent> build(PatientProfile profile, List<Disease> acutePool, LocalDateTime birthFloor, LocalDateTime windowStart, LocalDateTime windowEnd, RandomGenerator rnd)`
    —— 返回**按时间升序**的事件列表
  - `static double lognormal(double median, double sigma, RandomGenerator rnd)`
  - `static double gaussian(RandomGenerator rnd)`
  - `public static final int MAX_EVENTS_PER_PATIENT`；包级可见的分布常数（见下）
- **本任务不接线**：`EventCalendar` 在这一步还没有调用方，接线在 Task 16。
  这是有意的——把「时间层算得对不对」和「生成的记录对不对」分成两次提交，
  出问题时能立刻定位是哪一层。纯函数 + 单元测试本身就是可独立验收的交付物。

**为什么必须有这一层**：现在的时间安排是 `visitOffsets(visitCount, windowMinutes)`——
把窗口等分后每槽位塞一次就诊。于是就诊间隔机械均匀，
而且「下一次什么时候来」与「这个人有什么病」**毫无关系**。
事件日历把这两件事绑在一起：下一位患者的下一件事，由他档案里的病决定。

### 分布参数：每一个都要标明出处或「假设」（spec §5.3 的硬要求）

```java
    // ===== 时间参数。带「有出处」的注明来源；标「假设」的必须在论文里标为假设并做敏感性分析 =====

    /**
     * 慢病随访间隔中位数（天）。**有出处**：《国家基本公共卫生服务规范（第三版）》≥4 次/年 → 91 天。
     *
     * <p>可用 {@code -Dhis.followUpMedianDays=...} 覆盖。**这是给 Task 18 的敏感性分析留的口子，
     * 不是生产旋钮**：spec §5.3 要求对随访间隔中位数做两档对照，而「改常量 → 重跑 → 改回来」
     * 的失败模式是**忘了改回来**——那样交付的生成器跑的是实验档位而非设计值，且从代码上看不出来。
     */
    static final double FOLLOW_UP_MEDIAN_DAYS =
            Double.parseDouble(System.getProperty("his.followUpMedianDays", "91.0"));
    /**
     * 随访间隔的对数正态 σ。**假设值**（spec §5.3 标注）。
     *
     * <p><b>注意 spec 自身的一处不一致，写论文时不要照抄那句话</b>：spec §5.3 的表格写
     * σ=0.5，同行括注却写「约 50% 的随访落在 61~136 天」——而 61~136 对应的是 σ≈0.4。
     * σ=0.5、中位数 91 天的四分位区间实际是 **65~128 天**。本实现取表格里明确写出的 σ=0.5，
     * 论文里的区间请**用本实现实测出来的数**，不要引用那句括注。
     */
    static final double FOLLOW_UP_SIGMA = 0.5;
    /**
     * 急性就诊间隔中位数（天）。**假设值**，无公开的分病种门诊间隔数据。
     *
     * <p>取值依据：人均年就诊 6.2 次（卫健委统计公报 2019）→ 平均间隔约 59 天。
     * 对数正态的**均值大于中位数**，故中位数取 48 天，使实际均值落在 59 天附近
     * （48 × exp(0.6²/2) ≈ 59.6）。
     */
    static final double ACUTE_MEDIAN_DAYS = 48.0;
    /** 急性间隔的对数正态 σ。**假设值**。 */
    static final double ACUTE_SIGMA = 0.6;
    /**
     * 慢病年度复查间隔中位数（天）。**假设值**，取 1 年。
     *
     * <p><b>与 spec §5.2 的一处偏离，如实记下</b>：spec 把第三类事件写成
     * 「其他就诊 —— 低频（体检复查等），从低权重池抽」。但本体里**没有「健康查体」这类诊断行**
     * （Task 4 的 72 行全是疾病），要照字面实现就得给 Task 4 加行、并连带改它的行数与断言。
     * 故这里把「低权重池」解释为**该患者自己的慢病池**，用更长的间隔（1 年 vs 随访的 91 天）
     * 表达「年度复查」与「季度随访」的区别。若日后 Task 4 补了 `Z00.0 健康查体`，
     * 改动点就在这里。
     */
    static final double ROUTINE_MEDIAN_DAYS = 365.0;
    /** 复查间隔的 σ。**假设值**。 */
    static final double ROUTINE_SIGMA = 0.3;
    /** 事件类型概率（有慢病时）：随访 / 复查 / 急性。**假设值**。 */
    static final double P_FOLLOW_UP = 0.35;
    static final double P_ROUTINE = 0.10;
    /**
     * 急性病复诊概率区间。**假设值**（spec §5.3 标注，要求做敏感性分析）。
     *
     * <p>两档对照靠 {@code -Dhis.recheckProbMin=... -Dhis.recheckProbMax=...}（Task 18 用），
     * 理由与 {@link #FOLLOW_UP_MEDIAN_DAYS} 的覆盖口子相同。
     */
    static final double RECHECK_PROB_MIN =
            Double.parseDouble(System.getProperty("his.recheckProbMin", "0.10"));
    static final double RECHECK_PROB_MAX =
            Double.parseDouble(System.getProperty("his.recheckProbMax", "0.20"));
    /** 急性病复诊间隔（天）。**假设值**（spec §5.3 标注）。 */
    static final int RECHECK_MIN_DAYS = 3;
    static final int RECHECK_MAX_DAYS = 7;
    /**
     * 首次事件的随机偏移上限（天）。**假设值**，但它解决的是一个真实缺陷：
     * 若首次事件钉在 `windowStart`，所有患者的首次就诊会落在同一天，
     * 在时间轴**左端**造出一根尖峰——与旧实现右端的 54 倍尖峰是同一个毛病（见 `TAIL_SPREAD_DAYS`）。
     */
    static final int FIRST_EVENT_SPREAD_DAYS = 90;
    /** 同一患者两次事件的最小间隔（分钟）。与原 `MIN_VISIT_GAP_MINUTES` 同值，保证时间严格递增。 */
    static final long MIN_GAP_MINUTES = 30;
```

- [ ] **Step 1: 写失败的测试**

新建 `backend/his-generator/src/test/java/com/his/generator/EventCalendarTest.java`：

```java
package com.his.generator;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Random;

import static org.assertj.core.api.Assertions.assertThat;

class EventCalendarTest {

    private static final long SEED = 20260921L;
    private static final LocalDateTime NOW = LocalDateTime.of(2026, 9, 21, 12, 0);
    private static final LocalDateTime BIRTH_FLOOR = LocalDateTime.of(1990, 1, 1, 0, 0);

    /** 无季节性、权重 1 的急性病。 */
    private Disease acute(String icd, String name, double weight) {
        return new Disease(icd, name, "内科", 0, 0, 89, false, true,
                weight, new double[12], 0);
    }

    /** 带季节乘子的急性病：winterHigh=true 时 1 月乘子 1.5、7 月 0.5，否则相反；其余月份中性。 */
    private Disease seasonal(String icd, String name, double weight, boolean winterHigh) {
        double[] w = new double[12];
        for (int i = 0; i < 12; i++) {
            w[i] = 1.0;
        }
        w[0] = winterHigh ? 1.5 : 0.5;   // 1 月
        w[6] = winterHigh ? 0.5 : 1.5;   // 7 月
        return new Disease(icd, name, "内科", 0, 0, 89, false, true, weight, w, 0);
    }

    private Disease chronicDisease(String icd, String name) {
        return new Disease(icd, name, "内科", 0, 18, 89, true, true,
                1.0, new double[12], 0.5);
    }

    private PatientProfile profile(int age, List<Disease> chronic, double propensity, String mult) {
        return new PatientProfile(1L, age, 0, chronic, propensity, new BigDecimal(mult));
    }

    @Test
    @DisplayName("对数正态的中位数就是传入的中位数，四分位区间与 σ 相符")
    void lognormal_is_centred_on_the_given_median() {
        List<Double> draws = new ArrayList<>();
        Random rnd = new Random(SEED);
        for (int i = 0; i < 20_000; i++) {
            draws.add(EventCalendar.lognormal(91.0, 0.5, rnd));
        }
        draws.sort(Comparator.naturalOrder());

        assertThat(draws.get(10_000)).isBetween(88.0, 94.0);
        // σ=0.5、中位数 91 的四分位区间是 65~128（不是 spec 括注里的 61~136，见 Task 15 的说明）
        assertThat(draws.get(5_000)).isBetween(62.0, 68.0);
        assertThat(draws.get(15_000)).isBetween(124.0, 132.0);
    }

    @Test
    @DisplayName("事件时间严格升序，同一患者两次事件至少隔 30 分钟")
    void event_times_are_strictly_increasing_with_a_minimum_gap() {
        List<Disease> pool = List.of(acute("J06.9", "上呼吸道感染", 3.0),
                acute("A09.9", "急性胃肠炎", 2.6));
        for (int i = 0; i < 200; i++) {
            List<PatientEvent> events = EventCalendar.build(
                    profile(40, List.of(chronicDisease("I10", "原发性高血压")), 1.5, "1.0"),
                    pool, BIRTH_FLOOR, NOW.minusYears(3), NOW, new Random(SEED + i));

            for (int j = 1; j < events.size(); j++) {
                long gap = java.time.Duration.between(
                        events.get(j - 1).time(), events.get(j).time()).toMinutes();
                assertThat(gap).as("第 %d 与第 %d 个事件", j - 1, j)
                        .isGreaterThanOrEqualTo(EventCalendar.MIN_GAP_MINUTES);
            }
        }
    }

    @Test
    @DisplayName("不会在出生之前产生事件——婴儿的窗口被出生日截断")
    void no_events_before_the_patients_birth() {
        LocalDateTime birth = NOW.minusMonths(6);          // 6 个月大的婴儿
        List<Disease> pool = List.of(acute("J06.9", "上呼吸道感染", 3.0));

        for (int i = 0; i < 50; i++) {
            List<PatientEvent> events = EventCalendar.build(
                    profile(0, List.of(), 1.2, "2.200"),
                    pool, birth, NOW.minusYears(3), NOW, new Random(SEED + i));

            assertThat(events).allSatisfy(e ->
                    assertThat(e.time()).isAfterOrEqualTo(birth).isBefore(NOW));
        }
    }

    @Test
    @DisplayName("慢病患者会反复因同一个慢病就诊——阶段二出口判据「重复就诊率 > 0」的来源")
    void chronic_patients_return_for_the_same_disease() {
        List<Disease> chronic = List.of(chronicDisease("J44.9", "慢性阻塞性肺疾病"));
        List<Disease> pool = List.of(acute("J06.9", "上呼吸道感染", 3.0));
        int patients = 100;
        int withRepeats = 0;
        for (int i = 0; i < patients; i++) {
            List<PatientEvent> events = EventCalendar.build(
                    profile(65, chronic, 1.5, "2.000"),
                    pool, BIRTH_FLOOR, NOW.minusYears(3), NOW, new Random(SEED + i));
            long visits = events.stream()
                    .filter(e -> e.disease().icdCode().equals("J44.9")).count();
            if (visits >= 2) {
                withRepeats++;
            }
        }
        assertThat(withRepeats).isEqualTo(patients);
    }

    @Test
    @DisplayName("无慢病的患者只产生急性事件，且事件密度由有效就诊率决定")
    void acute_only_patients_are_denser_when_the_rate_is_higher() {
        List<Disease> pool = List.of(acute("J06.9", "上呼吸道感染", 3.0));
        int childTotal = 0;
        int adultTotal = 0;
        int n = 100;
        for (int i = 0; i < n; i++) {
            childTotal += EventCalendar.build(profile(2, List.of(), 1.2, "2.200"),
                    pool, BIRTH_FLOOR, NOW.minusYears(2), NOW, new Random(SEED + i)).size();
            adultTotal += EventCalendar.build(profile(30, List.of(), 1.2, "0.650"),
                    pool, BIRTH_FLOOR, NOW.minusYears(2), NOW, new Random(SEED + i)).size();
        }
        // 2 岁段乘子 2.200 vs 30 岁段男性 0.650，幼儿必须明显更密
        assertThat(childTotal).isGreaterThan(adultTotal * 2);
    }

    @Test
    @DisplayName("季节乘子真的改变病种分布：1 月抽到冬季病、7 月抽到夏季病")
    void seasonality_shifts_the_chosen_diseases() {
        Disease winter = seasonal("J06.9", "上呼吸道感染", 3.0, true);
        Disease summer = seasonal("A09.9", "急性胃肠炎", 3.0, false);
        List<Disease> pool = List.of(winter, summer);

        // 窗口取整年：季节差异要靠「同一年里 1 月与 7 月选出的病不同」来体现。
        // 不能用只覆盖单月的短窗口——首次事件有 1~90 天的随机偏移
        // （FIRST_EVENT_SPREAD_DAYS），短窗口里多数患者一个事件都产生不出来。
        int winterInJan = 0, janTotal = 0, summerInJul = 0, julyTotal = 0;
        for (int i = 0; i < 300; i++) {
            List<PatientEvent> events = EventCalendar.build(profile(40, List.of(), 1.5, "1.0"),
                    pool, BIRTH_FLOOR,
                    LocalDateTime.of(2025, 1, 1, 0, 0), LocalDateTime.of(2025, 12, 30, 23, 0),
                    new Random(SEED + i));
            for (PatientEvent e : events) {
                if (e.time().getMonthValue() == 1) {
                    janTotal++;
                    if (e.disease().icdCode().equals("J06.9")) {
                        winterInJan++;
                    }
                } else if (e.time().getMonthValue() == 7) {
                    julyTotal++;
                    if (e.disease().icdCode().equals("A09.9")) {
                        summerInJul++;
                    }
                }
            }
        }

        assertThat(janTotal).isGreaterThan(100);
        assertThat(julyTotal).isGreaterThan(100);
        // 两个病的基础权重相同（3.0），唯一的差别就是季节乘子：
        // 1 月是 4.5:1.5 → 冬季病占 75%；7 月是 1.5:4.5 → 夏季病占 75%。
        // 若季节乘子没参与加权，两个比例都会落在 50% 附近。
        assertThat((double) winterInJan / janTotal).isGreaterThan(0.65);
        assertThat((double) summerInJul / julyTotal).isGreaterThan(0.65);
    }

    @Test
    @DisplayName("急性病复诊落在 3~7 天内，且复诊的诊断与急性事件相同")
    void recheck_follows_an_acute_event_within_three_to_seven_days() {
        List<Disease> pool = List.of(acute("J06.9", "上呼吸道感染", 3.0));
        int rechecks = 0;
        for (int i = 0; i < 300; i++) {
            List<PatientEvent> events = EventCalendar.build(profile(40, List.of(), 1.5, "1.0"),
                    pool, BIRTH_FLOOR, NOW.minusMonths(4), NOW, new Random(SEED + i));
            for (PatientEvent e : events) {
                if (e.kind() != PatientEvent.Kind.RECHECK) {
                    continue;
                }
                rechecks++;
                assertThat(e.disease().icdCode()).isEqualTo("J06.9");
                // 复诊之前必须有一次同病的急性事件，且间隔在 3~7 天
                boolean matched = events.stream().anyMatch(a ->
                        a.kind() == PatientEvent.Kind.ACUTE
                                && !a.time().isAfter(e.time())
                                && java.time.Duration.between(a.time(), e.time()).toDays() >= 3
                                && java.time.Duration.between(a.time(), e.time()).toDays() <= 7);
                assertThat(matched).isTrue();
            }
        }
        // 10%~20% 的复诊概率下，300 个患者总该出现一些
        assertThat(rechecks).isGreaterThan(0);
    }

    @Test
    @DisplayName("单患者事件数不超过上限，避免极端乘子造出病态长的链")
    void events_are_capped_per_patient() {
        List<Disease> pool = List.of(acute("J06.9", "上呼吸道感染", 3.0));
        for (int i = 0; i < 50; i++) {
            List<PatientEvent> events = EventCalendar.build(
                    profile(2, List.of(), 2.0, "2.200"),
                    pool, BIRTH_FLOOR, NOW.minusYears(5), NOW, new Random(SEED + i));
            assertThat(events).hasSizeLessThanOrEqualTo(EventCalendar.MAX_EVENTS_PER_PATIENT);
        }
    }

    @Test
    @DisplayName("空病池给出空事件表，不是 NPE")
    void an_empty_pool_yields_no_events() {
        List<PatientEvent> events = EventCalendar.build(profile(40, List.of(), 1.5, "1.0"),
                List.of(), BIRTH_FLOOR, NOW.minusYears(2), NOW, new Random(SEED));

        assertThat(events).isEmpty();
    }

    @Test
    @DisplayName("窗口跨度为 0 或负时给出空表")
    void a_degenerate_window_yields_no_events() {
        List<Disease> pool = List.of(acute("J06.9", "上呼吸道感染", 3.0));

        assertThat(EventCalendar.build(profile(40, List.of(), 1.5, "1.0"), pool,
                BIRTH_FLOOR, NOW, NOW, new Random(SEED))).isEmpty();
        assertThat(EventCalendar.build(profile(40, List.of(), 1.5, "1.0"), pool,
                BIRTH_FLOOR, NOW, NOW.minusDays(1), new Random(SEED))).isEmpty();
    }
}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-generator -Dtest=EventCalendarTest test
```

Expected: 编译失败——`EventCalendar`、`PatientEvent` 符号找不到。

- [ ] **Step 3: 新建 `PatientEvent.java`**

```java
package com.his.generator;

import java.time.LocalDateTime;

/**
 * 一次就诊事件：什么时间、因为什么病、属于哪一类。
 *
 * <p>事件日历（§5.2）的产出物，也是 Task 16 逐层条件抽样的**输入**——
 * 诊断不再是「从全局池随机抽一个」，而是这个事件里已经定好的 {@code disease}。
 *
 * @param time   就诊时间（挂号时刻）
 * @param disease 本次就诊的诊断。**它同时决定了科室**（§6.1）
 * @param kind   事件类型，见 {@link Kind}
 */
public record PatientEvent(LocalDateTime time, Disease disease, Kind kind) {

    public enum Kind {
        /** 慢病随访：间隔中位数 91 天（有出处） */
        FOLLOW_UP,
        /** 急性发作/急性病就诊：间隔中位数 48 天（假设值），并按有效就诊率缩放 */
        ACUTE,
        /** 慢病年度复查：间隔中位数 365 天（假设值，见 EventCalendar 的偏离说明） */
        ROUTINE,
        /** 急性事件后的复诊：3~7 天，概率 10%~20%（均为假设值） */
        RECHECK
    }
}
```

- [ ] **Step 4: 新建 `EventCalendar.java`**

```java
package com.his.generator;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.random.RandomGenerator;

/**
 * 患者事件日历（§5.2）——替代原来「把窗口等分、每槽位塞一次就诊」的时间安排。
 *
 * <p><b>它解决的是两个问题：</b>
 * <ol>
 *   <li>就诊间隔机械均匀。旧算法下同一患者的相邻就诊间隔几乎相等，一眼看出是程序生成的；</li>
 *   <li>「下一次什么时候来」与「这个人有什么病」无关。旧算法下，
 *       一位慢阻肺患者的随访节奏与一位健康青年的感冒节奏在统计上不可区分。</li>
 * </ol>
 *
 * <p>本类只决定 <b>什么时候、因为什么病</b>，不生产任何业务记录——
 * 挂号、诊断、处方、检验由 Task 16 按 {@link PatientEvent} 逐层生成。
 *
 * <p><b>纯函数</b>：所有随机性都来自传入的 {@link RandomGenerator}，
 * 生产代码传 {@code ThreadLocalRandom.current()}，测试传固定种子的 {@code Random}。
 */
public final class EventCalendar {

    // ===== 时间参数。带「有出处」的注明来源；标「假设」的必须在论文里标为假设并做敏感性分析 =====

    /**
     * 慢病随访间隔中位数（天）。**有出处**：《国家基本公共卫生服务规范（第三版）》≥4 次/年 → 91 天。
     *
     * <p>可用 {@code -Dhis.followUpMedianDays=...} 覆盖。**这是给 Task 18 的敏感性分析留的口子，
     * 不是生产旋钮**：spec §5.3 要求对随访间隔中位数做两档对照，而「改常量 → 重跑 → 改回来」
     * 的失败模式是**忘了改回来**——那样交付的生成器跑的是实验档位而非设计值，且从代码上看不出来。
     */
    static final double FOLLOW_UP_MEDIAN_DAYS =
            Double.parseDouble(System.getProperty("his.followUpMedianDays", "91.0"));

    /**
     * 随访间隔的对数正态 σ。**假设值**（spec §5.3 标注）。
     *
     * <p><b>注意 spec 自身的一处不一致，写论文时不要照抄那句话</b>：spec §5.3 的表格写 σ=0.5，
     * 同行括注却写「约 50% 的随访落在 61~136 天」——而 61~136 对应的是 σ≈0.4。
     * σ=0.5、中位数 91 天的四分位区间实际是 **65~128 天**。本实现取表格里明确写出的 σ=0.5，
     * 论文里的区间请**用本实现的实测值**，不要引用那句括注。
     */
    static final double FOLLOW_UP_SIGMA = 0.5;

    /**
     * 急性就诊间隔中位数（天）。**假设值**，无公开的分病种门诊间隔数据。
     *
     * <p>取值依据：人均年就诊 6.2 次（卫健委统计公报 2019）→ 平均间隔约 59 天。
     * 对数正态的**均值大于中位数**，故中位数取 48 天，使实际均值落在 59 天附近
     * （48 × exp(0.6²/2) ≈ 59.6）。
     */
    static final double ACUTE_MEDIAN_DAYS = 48.0;

    /** 急性间隔的对数正态 σ。**假设值**。 */
    static final double ACUTE_SIGMA = 0.6;

    /**
     * 慢病年度复查间隔中位数（天）。**假设值**，取 1 年。
     *
     * <p><b>与 spec §5.2 的一处偏离，如实记下</b>：spec 把第三类事件写成
     * 「其他就诊 —— 低频（体检复查等），从低权重池抽」。但本体里**没有「健康查体」这类诊断行**
     * （Task 4 的 72 行全是疾病），照字面实现就得给 Task 4 加行、并连带改它的行数与断言。
     * 故这里把「低权重池」解释为**该患者自己的慢病池**，用更长的间隔（365 天 vs 随访的 91 天）
     * 表达「年度复查」与「季度随访」的区别。若日后 Task 4 补了 {@code Z00.0 健康查体}，
     * 改动点就在这里。
     */
    static final double ROUTINE_MEDIAN_DAYS = 365.0;

    /** 复查间隔的 σ。**假设值**。 */
    static final double ROUTINE_SIGMA = 0.3;

    /** 有慢病时的随访概率。**假设值**。 */
    static final double P_FOLLOW_UP = 0.35;
    /** 有慢病时的年度复查概率。**假设值**。急性事件的概率是 1 − 前两者。 */
    static final double P_ROUTINE = 0.10;

    /**
     * 急性病复诊概率区间。**假设值**（spec §5.3 标注，要求做敏感性分析）。
     *
     * <p>两档对照靠 {@code -Dhis.recheckProbMin=... -Dhis.recheckProbMax=...}（Task 18 用），
     * 理由与 {@link #FOLLOW_UP_MEDIAN_DAYS} 的覆盖口子相同。
     */
    static final double RECHECK_PROB_MIN =
            Double.parseDouble(System.getProperty("his.recheckProbMin", "0.10"));
    static final double RECHECK_PROB_MAX =
            Double.parseDouble(System.getProperty("his.recheckProbMax", "0.20"));
    /** 急性病复诊间隔（天）。**假设值**（spec §5.3 标注）。 */
    static final int RECHECK_MIN_DAYS = 3;
    static final int RECHECK_MAX_DAYS = 7;

    /**
     * 首次事件的随机偏移上限（天）。**假设值**，但它解决的是一个真实缺陷：
     * 若首次事件钉在 {@code windowStart}，所有患者的首次就诊会落在同一天，
     * 在时间轴**左端**造出一根尖峰——与旧实现右端的 54 倍尖峰是同一个毛病
     * （见 {@code DataGeneratorService.TAIL_SPREAD_DAYS} 的注释）。
     */
    static final int FIRST_EVENT_SPREAD_DAYS = 90;

    /** 同一患者两次事件的最小间隔（分钟）。与原 {@code MIN_VISIT_GAP_MINUTES} 同值。 */
    static final long MIN_GAP_MINUTES = 30;

    /**
     * 单患者事件数上限。极端乘子（幼儿 + 高倾向）几年下来能算出上百次事件，
     * 那不是「更真实」，只是让数据集被少数患者主导。**触发上限时调用方会把它计入统计**
     * （{@code events_capped}），不静默截断。
     */
    public static final int MAX_EVENTS_PER_PATIENT = 60;

    private EventCalendar() {
    }

    /**
     * 为一名患者生成完整的事件日历。
     *
     * @param profile      患者疾病档案（Task 14）。慢病池来自它，急性事件的疏密也来自它
     * @param acutePool    该患者年龄性别适用的**全部**诊断，取自 {@code OntologyRepository.diseasesFor}
     * @param birthFloor   出生日 0 点。**窗口早于出生日时以出生日为准**（spec §5.2 的
     *                     {@code max(出生日, 起点)}）——3 个月大的婴儿不可能有 3 年前的就诊记录
     * @param windowStart  病史窗口起点
     * @param windowEnd    病史窗口终点（已扣掉下游缓冲，保证后续记录不越过 now）
     * @param rnd          随机源
     * @return 按时间升序的事件列表；窗口退化或病池为空时返回空表
     */
    public static List<PatientEvent> build(PatientProfile profile, List<Disease> acutePool,
                                           LocalDateTime birthFloor, LocalDateTime windowStart,
                                           LocalDateTime windowEnd, RandomGenerator rnd) {
        List<PatientEvent> events = new ArrayList<>();
        if (acutePool.isEmpty() && profile.chronicDiseases().isEmpty()) {
            return events;
        }
        LocalDateTime start = windowStart.isAfter(birthFloor) ? windowStart : birthFloor;
        if (!start.isBefore(windowEnd)) {
            return events;   // 窗口退化（含 start == end）：没有可放事件的空间
        }

        // 首次事件不钉在 start 上，见 FIRST_EVENT_SPREAD_DAYS 的说明
        LocalDateTime next = start.plusDays(1 + rnd.nextLong(0, FIRST_EVENT_SPREAD_DAYS));

        while (next.isBefore(windowEnd) && events.size() < MAX_EVENTS_PER_PATIENT) {
            PatientEvent.Kind kind = pickKind(profile, rnd);
            Disease disease = pickDisease(kind, profile, acutePool, next.getMonthValue(), rnd);
            if (disease == null) {
                break;   // 该类型无候选（如急性池为空），该患者的时间线到此结束
            }

            events.add(new PatientEvent(next, disease, kind));

            // 急性事件按假设概率追加一次复诊（同一诊断）
            if (kind == PatientEvent.Kind.ACUTE && rnd.nextDouble() < recheckProbability(rnd)) {
                int days = RECHECK_MIN_DAYS + rnd.nextInt(RECHECK_MAX_DAYS - RECHECK_MIN_DAYS + 1);
                LocalDateTime back = next.plusDays(days).plusMinutes(rnd.nextLong(0, 480));
                if (back.isBefore(windowEnd)) {
                    events.add(new PatientEvent(back, disease, PatientEvent.Kind.RECHECK));
                }
            }

            next = next.plusMinutes(nextIntervalMinutes(kind, profile, rnd));
        }

        // 复诊可能落到下一次主事件之后（复诊是 3~7 天，而主事件间隔可能只有 1 天），
        // 所以最后统一次序，并丢掉间距不足 30 分钟的（复诊本来就是可选的，丢掉不影响结构）
        events.sort(Comparator.comparing(PatientEvent::time));
        List<PatientEvent> result = new ArrayList<>(events.size());
        for (PatientEvent e : events) {
            if (result.isEmpty() || java.time.Duration.between(
                    result.get(result.size() - 1).time(), e.time()).toMinutes() >= MIN_GAP_MINUTES) {
                result.add(e);
            }
        }
        return result;
    }

    /** 事件类型：无慢病档案的患者只有急性事件。 */
    private static PatientEvent.Kind pickKind(PatientProfile profile, RandomGenerator rnd) {
        if (profile.chronicDiseases().isEmpty()) {
            return PatientEvent.Kind.ACUTE;
        }
        double u = rnd.nextDouble();
        if (u < P_FOLLOW_UP) {
            return PatientEvent.Kind.FOLLOW_UP;
        }
        if (u < P_FOLLOW_UP + P_ROUTINE) {
            return PatientEvent.Kind.ROUTINE;
        }
        return PatientEvent.Kind.ACUTE;
    }

    private static Disease pickDisease(PatientEvent.Kind kind, PatientProfile profile,
                                       List<Disease> acutePool, int month, RandomGenerator rnd) {
        if (kind == PatientEvent.Kind.FOLLOW_UP || kind == PatientEvent.Kind.ROUTINE) {
            List<Disease> chronic = profile.chronicDiseases();
            return chronic.isEmpty() ? null : chronic.get(rnd.nextInt(chronic.size()));
        }
        return pickWeightedBySeason(acutePool, month, rnd);
    }

    /**
     * 急性事件按 {@code weight × month_weights[当月]} 加权抽取（§5.2）。
     *
     * <p><b>季节真实生效就靠这里</b>：spec §5.2 还写了「且 {@code month_weights[当月] > 0}」，
     * 但 Task 4 的三套季节模式每月乘子都 ≥0.7，那个条件恒真、没有任何筛选作用。
     * 让 7 月的流感权重降到冬季一半的，是下面这个乘法，不是成员资格筛选。
     */
    private static Disease pickWeightedBySeason(List<Disease> pool, int month, RandomGenerator rnd) {
        double total = 0;
        for (Disease d : pool) {
            total += d.weight() * d.monthWeight(month);
        }
        if (total <= 0) {
            return pool.isEmpty() ? null : pool.get(rnd.nextInt(pool.size()));
        }
        double target = rnd.nextDouble() * total;
        double acc = 0;
        for (Disease d : pool) {
            acc += d.weight() * d.monthWeight(month);
            if (target < acc) {
                return d;
            }
        }
        return pool.get(pool.size() - 1);   // 浮点累加的兜底，不改变分布
    }

    private static double recheckProbability(RandomGenerator rnd) {
        return RECHECK_PROB_MIN + rnd.nextDouble() * (RECHECK_PROB_MAX - RECHECK_PROB_MIN);
    }

    /**
     * 到下一次事件的间隔（分钟）。
     *
     * <p>急性事件按 {@code effectiveAcuteRate} **缩放**：该患者越容易就诊，间隔越短。
     * 这是 {@code visit_rate} 的年龄别 U 形曲线（§5.3）真正生效的地方——
     * 2 岁段乘子 2.200，25~44 岁男性 0.650，两者的事件密度差三倍多。
     *
     * <p>下限 1 天：对数正态的左尾可以算出几小时，而「同一个人半天内因为两次不同急性病就诊」
     * 是噪声不是信号。
     */
    private static long nextIntervalMinutes(PatientEvent.Kind kind, PatientProfile profile,
                                            RandomGenerator rnd) {
        double days = switch (kind) {
            case FOLLOW_UP -> lognormal(FOLLOW_UP_MEDIAN_DAYS, FOLLOW_UP_SIGMA, rnd);
            case ROUTINE -> lognormal(ROUTINE_MEDIAN_DAYS, ROUTINE_SIGMA, rnd);
            case ACUTE, RECHECK -> lognormal(ACUTE_MEDIAN_DAYS, ACUTE_SIGMA, rnd)
                    / profile.effectiveAcuteRate();
        };
        return Math.max(1440L, Math.round(days * 1440));
    }

    /**
     * 对数正态抽样：中位数 {@code median}、对数尺度 {@code sigma}。
     *
     * <p>中位数**精确等于**传入的 {@code median}（因为标准正态关于 0 对称），
     * 这正是 spec 用「中位数 + σ」而不是「均值 + σ」描述随访间隔的原因。
     */
    static double lognormal(double median, double sigma, RandomGenerator rnd) {
        return median * Math.exp(sigma * gaussian(rnd));
    }

    /**
     * 标准正态抽样（Box-Muller）。
     *
     * <p>自己写而不用 {@code Random.nextGaussian()}：{@link RandomGenerator} 接口**没有**
     * {@code nextGaussian()}，而本类要求随机源是接口类型，测试才能传固定种子的 {@code Random}。
     */
    static double gaussian(RandomGenerator rnd) {
        double u1;
        do {
            u1 = rnd.nextDouble();
        } while (u1 == 0.0);        // log(0) = -Inf
        double u2 = rnd.nextDouble();
        return Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2);
    }
}
```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd backend && mvn -o -pl his-generator -Dtest=EventCalendarTest test
```

Expected: Tests run: 10, Failures: 0, Errors: 0。

若 `seasonality_shifts_the_chosen_diseases` 失败，先确认测试里那两个 `seasonal(...)`
的构造函数参数顺序与 record 一致（`icdCode, name, department, gender, minAge, maxAge,
chronic, admissionEligible, weight, monthWeights, prevalence`）——参数多、顺序容易看错，
而看错的表现是「季节没生效」这种误导性的症状。

- [ ] **Step 6: Commit**

```bash
git add backend/his-generator/src/main/java/com/his/generator/PatientEvent.java \
        backend/his-generator/src/main/java/com/his/generator/EventCalendar.java \
        backend/his-generator/src/test/java/com/his/generator/EventCalendarTest.java
git commit -m "feat(generator): 事件日历 —— 就诊时间与病种由患者档案驱动" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 16: 门诊链改造 —— 诊断决定科室、用药与检验（阶段二）

**Files:**
- Create: `backend/his-generator/src/main/java/com/his/generator/Weighted.java`
- Create: `backend/his-generator/src/main/java/com/his/generator/LabResults.java`
- Modify: `backend/his-generator/src/main/java/com/his/generator/EventCalendar.java`（`pickWeightedBySeason` 改为委托 `Weighted`）
- Modify: `backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java`
  （常量区 / 字段区 / `generate()` / `flushPatients` / `newVisits` / `buildItems` / `newLabTests`）
- Test: `backend/his-generator/src/test/java/com/his/generator/WeightedTest.java`（新建）
- Test: `backend/his-generator/src/test/java/com/his/generator/LabResultsTest.java`（新建）
- Test: `backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java`（桩 + 断言重写）

**Interfaces:**
- Consumes：Task 13 的 `OntologyRepository` 全部方法签名与 `Disease`；Task 14 的 `PatientProfile`
  与 `profilesByPatient`；Task 15 的 `EventCalendar.build` 与 `PatientEvent`；
  Task 10 的 `OntologyRules.timesPerDay`（本任务依赖它，见下方「频次取值域」一节）
- Produces（Task 17 依赖下列签名，**名字与类型不得改**）：
  - `DataGeneratorService` 私有 `record PendingVisit(Visit visit, Disease disease)`
  - `List<PendingVisit> newPendingVisits(Patient p, PatientProfile profile)`
  - `List<PrescriptionItem> buildItems(Disease disease)`
  - `List<LabTest> newLabTests(Disease disease, Prescription pr)`
  - `List<DiagnosisDrug> pickDrugRows(Disease disease, int max)`，以及
    `List<DiagnosisDrug> pickDrugRows(List<DiagnosisDrug> rows, int max)`
    —— **列表重载由 Task 23 的 Step 5(d) 加上**（术后用药要在一批已筛好的行里抽），
    本任务先写成一层、内部按 Disease 版实现即可，**不要**提前写两个重载
  - `generate()` 的返回 map 新增键 `dose_missing`
  - `Weighted.pickIndex(List<T>, ToDoubleFunction<T>, RandomGenerator)`（包级可见）
  - `LabResults.parseRefRange(String)` / `LabResults.draw(DiagnosisLab, RandomGenerator)`（包级可见）
- **Task 17 仍要用的、本任务不得删除**：`rules`、`loadRules()`、`randomDiagnosisName()`、
  `randomDepartment()`、`randomDrugName()`、`drugsById`、`newInpatientLabTest()`、`labTestForItem()`、
  `MedicalData.LAB_ITEMS`。住院链本任务一行不动。
  （`pickDrugIds()` 在本任务之后已无调用方——它是 Task 17 一并清理的死代码之一，
  本任务不删是因为删除它属于「住院链改造」那次提交，不是这次。）

**这一任务消灭的原始缺陷**：`DataGeneratorService.java:195` 的
`rules.get(rnd.nextInt(rules.size()))` —— 诊断与就诊科室完全无关。改造后诊断由事件日历按患者档案选出，
**科室再取自该诊断的 `dept_diagnosis.department`**，于是「妇科医生看肠胃炎」在结构上不可能出现，
而不是靠 3% 的抽检去发现。

**为什么先写两个纯工具类再接线**：`Weighted` 与 `LabResults` 都是「输入 → 输出」的纯函数，
它们的失败是**静默的**——加权抽样的下标算错一位只是分布略有偏差，参考范围解析错只是化验值略不合理，
两者都不会报错。所以先让它们各自带上单元测试，再接进 `DataGeneratorService`；
否则一旦出口数据不对，排查要在「三层数据源 + 加权抽样 + 值生成 + 接线」四处之间来回猜。

**为什么 `Weighted` 要单独存在而不是各写一份**：本任务与 Task 15 都需要「按 weight 不放回抽一个」，
两处的加权循环逐字相同、只是权重函数不同（一个乘 `month_weight`，一个用行自带的 `weight`）。
Task 15 已有一份私有实现，本任务把它收进 `Weighted` 并让 `EventCalendar` 改为委托——
**不是顺手重构**：留着两份的下场是日后只改一份（例如给 0 权重加保护），另一份悄悄留在旧行为上。

**频次取值域只用一份**：`quantity` 需要「每日几次」，而这个取值域（`每日1~4次`、`每晚1次`）
已经由 Task 8 锁定、由 Task 10 的 `OntologyRules.timesPerDay` 解析。
生成器**必须调它**，不得自己再写一张 5 个键的 Map —— 两份写法的典型后果是生成器写得出
`每日5次`，而校验器的白名单不认得它，于是整条处方落进 `unchecked`，看起来像「校验器覆盖不足」，
实际是生成器越过了取值域。

- [ ] **Step 1: 写失败的测试 —— `Weighted` 与 `LabResults`**

新建 `backend/his-generator/src/test/java/com/his/generator/WeightedTest.java`：

```java
package com.his.generator;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.random.RandomGenerator;
import java.util.random.RandomGeneratorFactory;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class WeightedTest {

    /** 固定种子：断言的是分布形状，不是某一次抽中的值。 */
    private static RandomGenerator rnd() {
        return RandomGeneratorFactory.of("L64X128MixRandom").create(20260921L);
    }

    private record Row(double w) {}

    @Test
    @DisplayName("权重为 9:1 时，低权重的行抽中比例约为 1/10")
    void weight_ratio_is_respected() {
        List<Row> rows = List.of(new Row(9.0), new Row(1.0));
        RandomGenerator r = rnd();
        int first = 0;
        for (int i = 0; i < 20000; i++) {
            if (Weighted.pickIndex(rows, Row::w, r) == 0) {
                first++;
            }
        }
        // 期望 18000，±3% 的余量足够宽松，也足以发现「权重被忽略」这类错误
        assertThat(first).isBetween(17400, 18600);
    }

    @Test
    @DisplayName("权重全为 0 / 全为 null 时退化为均匀，不抛异常也不越界")
    void all_zero_weights_fall_back_to_uniform() {
        List<Row> zeros = List.of(new Row(0), new Row(0), new Row(0));
        RandomGenerator r = rnd();
        for (int i = 0; i < 3000; i++) {
            assertThat(Weighted.pickIndex(zeros, Row::w, r)).isBetween(0, 2);
        }

        // BigDecimal 重载：null 视为 0，与「权重缺失」的实体字段对齐
        List<BigDecimal> nulls = java.util.Arrays.asList(null, null);
        for (int i = 0; i < 100; i++) {
            assertThat(Weighted.pickIndex(nulls, b -> b, r)).isBetween(0, 1);
        }
    }

    @Test
    @DisplayName("零权重行永不被抽中；负权重与 NaN 按 0 处理而不是把总权重拉低")
    void zero_and_negative_weights_are_never_picked() {
        List<Row> rows = List.of(new Row(0), new Row(1.0), new Row(-5.0));
        RandomGenerator r = rnd();
        for (int i = 0; i < 5000; i++) {
            assertThat(Weighted.pickIndex(rows, Row::w, r)).isEqualTo(1);
        }
    }

    @Test
    @DisplayName("空表抛异常：调用方必须先判空，不能把「空」当成「均匀抽一个」")
    void empty_table_throws() {
        assertThatThrownBy(() -> Weighted.pickIndex(List.of(), Row::w, rnd()))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("空表");
        assertThatThrownBy(() -> Weighted.pickIndex(new double[0], rnd()))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("空权重数组");
    }

    @Test
    @DisplayName("数组重载与列表重载走同一段逻辑")
    void array_overload_behaves_like_the_list_one() {
        double[] weights = {9.0, 1.0};
        RandomGenerator r = rnd();
        int first = 0;
        for (int i = 0; i < 20000; i++) {
            if (Weighted.pickIndex(weights, r) == 0) {
                first++;
            }
        }
        assertThat(first).isBetween(17400, 18600);

        // 全 0 退化均匀，不越界
        for (int i = 0; i < 1000; i++) {
            assertThat(Weighted.pickIndex(new double[]{0, 0, 0}, r)).isBetween(0, 2);
        }
    }
}
```

新建 `backend/his-generator/src/test/java/com/his/generator/LabResultsTest.java`：

```java
package com.his.generator;

import com.his.common.entity.DiagnosisLab;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.random.RandomGenerator;
import java.util.random.RandomGeneratorFactory;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class LabResultsTest {

    private static RandomGenerator rnd() {
        return RandomGeneratorFactory.of("L64X128MixRandom").create(20260921L);
    }

    private DiagnosisLab lab(String refRange, String direction, String prob) {
        DiagnosisLab r = new DiagnosisLab();
        r.setItemCode("X");
        r.setItemName("X");
        r.setUnit("u");
        r.setRefRange(refRange);
        r.setAbnormalDirection(direction);
        r.setAbnormalProb(new BigDecimal(prob));
        r.setWeight(BigDecimal.ONE);
        r.setRequired(0);
        return r;
    }

    @Test
    @DisplayName("参考范围的三种形态：a-b 数值区间、单个数、定性文本")
    void parse_ref_range_handles_three_shapes() {
        LabResults.RefRange range = LabResults.parseRefRange("4.0-10.0");
        assertThat(range.low()).isEqualTo(4.0);
        assertThat(range.high()).isEqualTo(10.0);
        assertThat(range.qualitative()).isNull();

        // URINE-GLU 的参考范围就是「0」：下界 0、上界 0，异常只能偏高
        LabResults.RefRange zero = LabResults.parseRefRange("0");
        assertThat(zero.low()).isEqualTo(0.0);
        assertThat(zero.high()).isEqualTo(0.0);

        LabResults.RefRange neg = LabResults.parseRefRange("成人 3.4-17.1");
        assertThat(neg.low()).isEqualTo(3.4);
        assertThat(neg.high()).isEqualTo(17.1);

        LabResults.RefRange text = LabResults.parseRefRange("阴性");
        assertThat(text.qualitative()).isEqualTo("阴性");
        assertThat(text.low()).isNull();
    }

    @Test
    @DisplayName("参考范围写坏了要抛异常：静默退回「无范围」等于放行所有值")
    void broken_ref_range_throws() {
        assertThatThrownBy(() -> LabResults.parseRefRange(null))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("参考范围");
        assertThatThrownBy(() -> LabResults.parseRefRange("—"))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("参考范围");
        assertThatThrownBy(() -> LabResults.parseRefRange("10.0-4.0"))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("上界");
        assertThatThrownBy(() -> LabResults.parseRefRange("见面再说"))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("参考范围");
    }

    @Test
    @DisplayName("正常值与异常值都落在参考范围之外/之内——is_abnormal 必须与值自洽")
    void value_agrees_with_the_abnormal_flag() {
        RandomGenerator r = rnd();
        LabResults.RefRange high = LabResults.parseRefRange("4.0-10.0");
        for (int i = 0; i < 3000; i++) {
            LabResults.Result n = LabResults.draw(high, "HIGH", false, r);
            double v = Double.parseDouble(n.value());
            assertThat(n.abnormal()).isZero();
            assertThat(v).isBetween(4.0, 10.0);

            LabResults.Result a = LabResults.draw(high, "HIGH", true, r);
            assertThat(a.abnormal()).isEqualTo(1);
            assertThat(Double.parseDouble(a.value())).isGreaterThan(10.0);
        }

        LabResults.RefRange low = LabResults.parseRefRange("40-55");
        for (int i = 0; i < 3000; i++) {
            LabResults.Result a = LabResults.draw(low, "LOW", true, r);
            assertThat(Double.parseDouble(a.value())).isLessThan(40.0).isGreaterThanOrEqualTo(0.0);
        }
    }

    @Test
    @DisplayName("direction=NONE 的行即使命中异常率也必须是正常值")
    void none_direction_never_produces_an_abnormal_value() {
        RandomGenerator r = rnd();
        LabResults.RefRange range = LabResults.parseRefRange("3.5-5.5");
        for (int i = 0; i < 2000; i++) {
            LabResults.Result x = LabResults.draw(range, "NONE", true, r);
            assertThat(x.abnormal()).isZero();
            assertThat(Double.parseDouble(x.value())).isBetween(3.5, 5.5);
        }
    }

    @Test
    @DisplayName("上界为 0 的项（尿糖 0）偏高时能造出 >0 的值")
    void zero_width_range_still_produces_a_high_value() {
        RandomGenerator r = rnd();
        LabResults.RefRange zero = LabResults.parseRefRange("0");
        for (int i = 0; i < 1000; i++) {
            LabResults.Result normal = LabResults.draw(zero, "HIGH", false, r);
            assertThat(Double.parseDouble(normal.value())).isZero();
            LabResults.Result abnormal = LabResults.draw(zero, "HIGH", true, r);
            assertThat(Double.parseDouble(abnormal.value())).isGreaterThan(0.0);
        }
    }

    @Test
    @DisplayName("定性项：正常取原文，异常取对立值")
    void qualitative_items_translate_the_abnormal_value() {
        RandomGenerator r = rnd();
        assertThat(LabResults.draw(LabResults.parseRefRange("阴性"), "HIGH", false, r).value())
                .isEqualTo("阴性");
        assertThat(LabResults.draw(LabResults.parseRefRange("阴性"), "HIGH", true, r).value())
                .isEqualTo("阳性");
        assertThat(LabResults.draw(LabResults.parseRefRange("正常"), "HIGH", false, r).value())
                .isEqualTo("正常");
        assertThat(LabResults.draw(LabResults.parseRefRange("正常"), "HIGH", true, r).value())
                .isEqualTo("异常");
    }

    @Test
    @DisplayName("下界为 0 却要求「偏低」是种子写错，必须抛异常而不是造一个 0")
    void low_direction_with_zero_floor_throws() {
        assertThatThrownBy(() ->
                LabResults.draw(LabResults.parseRefRange("0-5"), "LOW", true, rnd()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("下界为 0");
    }

    @Test
    @DisplayName("单参数重载按 abnormal_prob 命中异常")
    void row_overload_uses_the_abnormal_probability() {
        RandomGenerator r = rnd();
        int hits = 0;
        for (int i = 0; i < 20000; i++) {
            hits += LabResults.draw(lab("4.0-10.0", "HIGH", "0.500"), r).abnormal();
        }
        assertThat(hits).isBetween(9700, 10300);

        // prob=0 的项一次都不能异常
        for (int i = 0; i < 2000; i++) {
            assertThat(LabResults.draw(lab("4.0-10.0", "HIGH", "0"), r).abnormal()).isZero();
        }
    }
}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-generator -Dtest='WeightedTest,LabResultsTest' test
```

Expected: 编译失败——`Weighted`、`LabResults` 找不到符号。

- [ ] **Step 3: 新建 `Weighted.java`**

```java
package com.his.generator;

import java.math.BigDecimal;
import java.util.List;
import java.util.function.Function;
import java.util.function.ToDoubleFunction;
import java.util.random.RandomGenerator;

/**
 * 按权重不放回抽样的公共实现。
 *
 * <p>生成器有三处要「按 weight 加权抽一个」：急性病的季节加权（Task 15）、处方药（本任务）、
 * 可选检验项（本任务）。三处的加权循环逐字相同、只有权重函数不同，所以收在这里一份。
 * 留两份的下场是日后只改一份（例如给 0 权重加保护），另一份悄悄留在旧行为上。
 *
 * <p><b>纯函数</b>：随机性只来自传入的 {@link RandomGenerator}，不读时间、不读数据库，
 * 所以可以用固定种子做分布断言。
 */
final class Weighted {

    private Weighted() {
    }

    /**
     * 按权重抽一个，返回下标。
     *
     * <p>权重为 {@code null}、非有限值或 ≤ 0 时按 0 处理；**总权重为 0 时退化为均匀**——
     * 权重列没填不该让整个生成任务失败，但也不能让这些行永不被抽中（那会让
     * 「本体里有这条药，数据里却一次都没出现」变成一个无法解释的现象）。
     */
    static <T> int pickIndex(List<T> rows, ToDoubleFunction<T> weightOf, RandomGenerator rnd) {
        if (rows.isEmpty()) {
            throw new IllegalArgumentException("不能从空表里抽样：调用方必须先判空");
        }
        double[] w = new double[rows.size()];
        for (int i = 0; i < rows.size(); i++) {
            w[i] = weightOf.applyAsDouble(rows.get(i));
        }
        return pickIndex(w, rnd);
    }

    /**
     * 权重数组上的同一个抽样（Task 19 的日内到达分布用的是裸数组，不是实体列表）。
     *
     * <p>两种输入走同一段循环：分散成两份的下场是「给 0 权重加保护」这类修改只落在一处。
     */
    static int pickIndex(double[] weights, RandomGenerator rnd) {
        if (weights.length == 0) {
            throw new IllegalArgumentException("不能从空权重数组里抽样：调用方必须先判空");
        }
        double total = 0;
        for (double v : weights) {
            total += Double.isFinite(v) && v > 0 ? v : 0;
        }
        if (total <= 0) {
            return rnd.nextInt(weights.length);
        }
        double target = rnd.nextDouble() * total;
        double acc = 0;
        for (int i = 0; i < weights.length; i++) {
            double v = weights[i];
            acc += Double.isFinite(v) && v > 0 ? v : 0;
            if (target < acc) {
                return i;
            }
        }
        return weights.length - 1;   // 浮点累加的兜底，不改变分布
    }

    /**
     * {@link BigDecimal} 权重的便捷重载：本仓库的权重列（{@code weight}）都是 {@code DECIMAL}，
     * 实体字段即 {@code BigDecimal}，{@code null} 视为 0。
     *
     * <p>与上面的重载不会冲突：{@code BigDecimal} 不能隐式转成 {@code double}，
     * 所以返回 {@code BigDecimal} 的方法引用只会匹配这一个。
     */
    static <T> int pickIndex(List<T> rows, Function<T, BigDecimal> weightOf, RandomGenerator rnd) {
        return pickIndex(rows, t -> {
            BigDecimal b = weightOf.apply(t);
            return b == null ? 0 : b.doubleValue();
        }, rnd);
    }
}
```

- [ ] **Step 4: 新建 `LabResults.java`**

```java
package com.his.generator;

import com.his.common.entity.DiagnosisLab;

import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.random.RandomGenerator;

/**
 * 化验结果的取值：把 {@code diagnosis_lab} 一行变成一个 {@code result_value} 与 {@code is_abnormal}。
 *
 * <p><b>{@code is_abnormal} 与值必须自洽</b>：本类的唯一职责就是保证
 * 「标了异常的，值真的落在参考范围外；没标异常的，值真的落在参考范围内」。
 * 这条自洽性是校验器（Task 11 的 LAB_INDICATION）反测的对象，也是本类单独成类、
 * 单独测试的原因——把这段逻辑埋在 {@code newLabTests} 里，它出问题时的表现只是
 * 「化验值看着不太对」，没有任何报错。
 */
final class LabResults {

    /** {@code a-b} 或 {@code 前缀 a-b}：数值型参考范围。 */
    private static final Pattern RANGE = Pattern.compile(".*?(-?\\d+(?:\\.\\d+)?)\\s*-\\s*(-?\\d+(?:\\.\\d+)?)\\s*$");
    /** 单个数字：下界 0、上界即该数（如 URINE-GLU 的 {@code 0}）。 */
    private static final Pattern SINGLE = Pattern.compile("\\s*(-?\\d+(?:\\.\\d+)?)\\s*");

    private LabResults() {
    }

    /** 参考范围。数值型时 {@code low}/{@code high} 非空，定性型时 {@code qualitative} 非空。 */
    record RefRange(Double low, Double high, String qualitative) {
    }

    /** 一次取值的结果：值文本 + 异常标志（0/1）。 */
    record Result(String value, int abnormal) {
    }

    /**
     * 解析 {@code ref_range}。支持三种形态：{@code 4.0-10.0}、{@code 0}、{@code 阴性}/{@code 正常}。
     *
     * <p><b>解析不了就抛异常，不静默退回「无参考范围」</b>：放行所有值等于把
     * 「化验值与病相符」这条判据整体作废，而且作废得毫无痕迹。
     */
    static RefRange parseRefRange(String refRange) {
        if (refRange == null || refRange.isBlank() || "—".equals(refRange.trim()) || "-".equals(refRange.trim())) {
            throw new IllegalStateException("参考范围 diagnosis_lab.ref_range 缺失或为空占位符，无法生成化验值: " + refRange);
        }
        String text = refRange.trim();
        Matcher m = RANGE.matcher(text);
        if (m.matches()) {
            double low = Double.parseDouble(m.group(1));
            double high = Double.parseDouble(m.group(2));
            if (high < low) {
                throw new IllegalStateException("参考范围 ref_range 的上界小于下界: " + refRange);
            }
            return new RefRange(low, high, null);
        }
        Matcher s = SINGLE.matcher(text);
        if (s.matches()) {
            double high = Double.parseDouble(s.group(1));
            return new RefRange(Math.min(0.0, high), high, null);
        }
        if ("阴性".equals(text) || "正常".equals(text)) {
            return new RefRange(null, null, text);
        }
        throw new IllegalStateException(
                "参考范围 ref_range 既不是「a-b」、也不是单个数字、也不是「阴性」「正常」: " + refRange);
    }

    /**
     * 按本行的 {@code abnormal_prob} 抽一个结果。
     *
     * <p>异常判定与取值分开，是为了让「命中异常」这件事可单独测试（见 {@code LabResultsTest}
     * 的直接调用），不必靠上万次抽样去猜概率对不对。
     */
    static Result draw(DiagnosisLab row, RandomGenerator rnd) {
        double prob = row.getAbnormalProb() == null ? 0d : row.getAbnormalProb().doubleValue();
        return draw(parseRefRange(row.getRefRange()), row.getAbnormalDirection(),
                rnd.nextDouble() < prob, rnd);
    }

    /**
     * 取值。
     *
     * @param direction  {@code HIGH} / {@code LOW} / {@code NONE}；{@code NONE} 表示该项目的异常
     *                   没有方向（如血钾：可高可低），**此时永不产出异常值**——
     *                   因为「偏高还是偏低」这件事本体里没有说，编一个方向就是编造
     * @param abnormal   本次是否命中异常
     */
    static Result draw(RefRange range, String direction, boolean abnormal, RandomGenerator rnd) {
        String dir = direction == null || direction.isBlank() ? "NONE" : direction.trim();
        boolean hit = abnormal && !"NONE".equals(dir);

        if (range.qualitative() != null) {
            return new Result(hit ? qualitativeAbnormal(range.qualitative()) : range.qualitative(),
                    hit ? 1 : 0);
        }

        double low = range.low();
        double high = range.high();
        // 上界=下界（如「0」）时区间宽度为 0，异常值需要一个非零的跨度才能造出来
        double width = high - low > 0 ? high - low : Math.max(Math.abs(high), 1.0);
        double value;
        if (!hit) {
            value = low + rnd.nextDouble() * (high - low);
        } else if ("HIGH".equals(dir)) {
            value = high + width * (0.1 + 0.4 * rnd.nextDouble());
        } else if ("LOW".equals(dir)) {
            if (low <= 0) {
                throw new IllegalStateException("abnormal_direction=LOW 但参考范围下界为 0，"
                        + "不可能产出偏低的异常值，请修正 diagnosis_lab 的该行: " + range);
            }
            value = low - width * (0.1 + 0.4 * rnd.nextDouble());
        } else {
            throw new IllegalStateException(
                    "abnormal_direction 只能是 HIGH/LOW/NONE，实为: " + dir);
        }
        return new Result(String.format(Locale.ROOT, "%.2f", value), hit ? 1 : 0);
    }

    private static String qualitativeAbnormal(String normal) {
        return switch (normal) {
            case "阴性" -> "阳性";
            case "正常" -> "异常";
            default -> throw new IllegalStateException(
                    "定性检验项的参考范围只支持「阴性」「正常」，实为: " + normal);
        };
    }
}
```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd backend && mvn -o -pl his-generator -Dtest='WeightedTest,LabResultsTest' test
```

Expected: `WeightedTest` Tests run: 5；`LabResultsTest` Tests run: 8，全部通过。

> 若 `weight_ratio_is_respected` 越界，先确认 `Row::w` 取的是 `w()` 而不是 record 的另一个分量；
> 分布断言用固定种子，越界只可能是抽样逻辑错，不会是随机波动。

- [ ] **Step 6: `EventCalendar` 改为委托 `Weighted`，并卡住「窗口太短就一个事件都没有」**

改两处。

**其一**：把 `pickWeightedBySeason` 的整体循环换成委托（行为完全不变：
总权重 ≤0 时均匀、池空时返回 `null`）：

```java
    private static Disease pickWeightedBySeason(List<Disease> pool, int month, RandomGenerator rnd) {
        if (pool.isEmpty()) {
            return null;
        }
        return pool.get(Weighted.pickIndex(pool, d -> d.weight() * d.monthWeight(month), rnd));
    }
```

**其二**：把「首次事件」那一行（原 `start.plusDays(1 + rnd.nextLong(0, FIRST_EVENT_SPREAD_DAYS))`）
改成按分钟、且**按窗口长度封顶**：

```java
        // 首次事件不钉在 start 上（见 FIRST_EVENT_SPREAD_DAYS 的说明），
        // **但展开幅度不得超过窗口的一半**：婴儿的窗口可能只有几十天，
        // 「1 + [0,90) 天」会把首次事件直接推到 windowEnd 之外，该患者就一条记录都没有。
        // 「有病可得的患者一条记录都没有」比「首诊偏向窗口左端」严重得多——
        // 前者的下游表现是「同患者重复就诊率」的分母莫名其妙地小。
        long windowMinutes = Math.max(1, ChronoUnit.MINUTES.between(start, windowEnd));
        long spreadMinutes = Math.min(FIRST_EVENT_SPREAD_DAYS * 24L * 60L, windowMinutes / 2);
        LocalDateTime next = start.plusMinutes(1 + rnd.nextLong(0, Math.max(1, spreadMinutes)));
```

并在 `FIRST_EVENT_SPREAD_DAYS` 的常量注释末尾补一句：
`// 实际生效值 = min(本值, 窗口长度的一半)，见 build 的封顶逻辑`

跑 Task 15 的测试确认没有回归：

```bash
cd backend && mvn -o -pl his-generator -Dtest=EventCalendarTest test
```

Expected: Tests run: 10, Failures: 0, Errors: 0。

- [ ] **Step 7: 改造 `DataGeneratorService` 的门诊链**

按下面六处依次改。**住院相关的代码这一段一行都不要动。**

**(a) 删掉四个只服务于旧门诊链的常量**（`MIN_VISITS`、`MAX_VISITS`、`MIN_VISIT_GAP_MINUTES`、
`HOME_DEPT_RATIO`），并在常量区补两个新的：

```java
    /** 一次就诊最多开几种药（spec §6.1：抽 1~3 种）。 */
    private static final int MAX_PRESCRIBED_DRUGS = 3;
    /** 除 required=1 的必查项外，一次就诊最多再开几项可选检验。 */
    private static final int LAB_OPTIONAL_MAX = 3;
```

**(b) 字段区补一个计数**（与 `admissionCount, orderCount, vitalCount` 放一起）：

```java
    /** 因 drug_dose 缺行而被跳过的处方药次数。生成结束时报出，**不为它编造默认剂量**。 */
    private long doseMissing;
```

**(c) `generate()` 里重置并报出**：在 `admissionCount = 0; orderCount = 0; vitalCount = 0;` 之后加
`doseMissing = 0;`；在 `stat.put("vital_signs", vitalCount);` 之前加
`stat.put("dose_missing", doseMissing);`。

**(d) 记录类型**：把 `private record PendingPrescription(Prescription prescription, MedicalData.DiagnosisRule rule) {}`
改为 `Disease`，并新增一个：

```java
    /** 一次待写入的就诊：病种与科室已经定好，等待下游生成诊断 / 处方 / 检验。 */
    private record PendingVisit(Visit visit, Disease disease) {}

    private record PendingPrescription(Prescription prescription, Disease disease) {}
```

**(e) `flushPatients` 的门诊段**替换为（住院段另见 (c)；两段在同一个方法里，
**门诊段必须先执行**——住院段要读本段建好的 `anchorByPatient` 与裁过的 `pending`）：

```java
        List<PendingVisit> pending = new ArrayList<>();
        // 收治锚点：患者 id → 该患者最后一次「可住院」门诊。**在门诊段就填好**，
        // 供后面的住院段读取（收治判定与门诊链裁剪都在下面这个循环里做完，见相关注释）。
        Map<Long, PendingVisit> anchorByPatient = new HashMap<>();
        for (Patient p : patients) {
            List<PendingVisit> mine = newPendingVisits(p, profilesByPatient.get(p.getId()));

            // ── 找 anchor：只看病种的 admissionEligible 标记，PendingVisit 造出来时就已确定，
            //    **不必等住院构造完**。所以下面那刀能裁在「生成诊断 / 处方之前」，
            //    删掉的门诊不会留下孤儿记录（诊断 / 处方 / 检验 / 收费 / 发药都还没生成）。
            PendingVisit anchor = null;
            for (PendingVisit pv : mine) {
                if (pv.disease().admissionEligible()
                        && (anchor == null || pv.visit().getVisitTime().isAfter(anchor.visit().getVisitTime()))) {
                    anchor = pv;
                }
            }
            if (anchor != null && rnd.nextDouble() < INPATIENT_RATIO) {
                // ── 把 anchor 之后的门诊裁掉（推导见本节开头「死亡与『此后不再就诊』」）──
                // 「全部门诊记录都早于首次入院」**不是自动成立的**：anchor 只是最后一次*可住院*
                // 门诊，它之后完全可能还有若干次门诊（挂的是 admission_eligible=0 的诊断，如
                // J06.9 感冒）。不裁的话这些门诊会晚于首次入院，进而可能晚于 DEATH 时刻，
                // `death_only_on_the_last_admission_and_no_records_follow_it` 就会红。
                // 裁在 anchor、而不是裁到「首次入院时刻」：anchor 此刻已知，后者要等
                // newAdmissions 返回才有；裁得更早同样满足结论，且不必回头改已生成的记录。
                final LocalDateTime cut = anchor.visit().getVisitTime();
                mine.removeIf(pv -> pv.visit().getVisitTime().isAfter(cut));
                anchorByPatient.put(p.getId(), anchor);
            }
            pending.addAll(mine);
        }
        List<Visit> visitBuf = new ArrayList<>(pending.size());
        for (PendingVisit pv : pending) {
            visitBuf.add(pv.visit());
        }
        visitMapper.insert(visitBuf);                         // 回填 visit.id

        List<Diagnosis> diagBuf = new ArrayList<>(visitBuf.size());
        List<PendingPrescription> presBuf = new ArrayList<>(visitBuf.size());
        for (PendingVisit pv : pending) {
            Visit v = pv.visit();
            Disease disease = pv.disease();

            // 诊断 = 事件日历为该患者选出的病，不再从 rule_config 随机挑
            Diagnosis d = new Diagnosis();
            d.setPatientId(v.getPatientId()); d.setVisitId(v.getId());
            d.setIcdCode(disease.icdCode()); d.setDiagnosisName(disease.name());
            d.setDoctor(v.getDoctor()); d.setDiagnosedAt(v.getVisitTime().plusMinutes(rnd.nextInt(5, 20)));
            diagBuf.add(d);

            Prescription pr = new Prescription();
            pr.setPatientId(v.getPatientId()); pr.setVisitId(v.getId());
            pr.setDoctor(v.getDoctor()); pr.setPrescribedAt(d.getDiagnosedAt().plusMinutes(rnd.nextInt(2, 10)));
            pr.setTotalAmount(BigDecimal.ZERO);
            presBuf.add(new PendingPrescription(pr, disease));
        }
```

紧接着的明细+检验循环里，`buildItems(pp.rule())` 改为 `buildItems(pp.disease())`、
`newLabTests(pr)` 改为 `newLabTests(pp.disease(), pr)`。
（`if (rnd.nextDouble() < VIOLATION_RATE) { injectViolation(...); }` 与随后的
「total 重算」注释里提到注入的那半句，已在 Task 12 一并删除；若这里还看得到它们，说明 Task 12 没做完。）

住院段**不是「改一处」，是整体替换**——权威版本在后面的 **(c)**（`flushPatients` 的住院段）。
下面是**改动前**的旧代码，贴出来只为对照，**不要照着它改**（它用的是「末次门诊」`lastVisitByPatient`，
而改造后锚点是「最后一次**可住院**门诊」，且收治判定已移到门诊段）：

```java
        // 旧代码，仅作对照
        for (Patient p : patients) {
            LocalDateTime lastVisit = lastVisitByPatient.get(p.getId());
            if (lastVisit == null || rnd.nextDouble() >= INPATIENT_RATIO) {
                continue;
            }
            admissionBuf.addAll(newAdmissions(p, lastVisit));
        }
```

**(f) 删掉三个方法**：`newVisits(Patient)`、`pickOtherDept(...)`、`visitOffsets(...)`。
新增：

```java
    /**
     * 一位患者的门诊链：患者档案 + 事件日历 → 待写入的就诊列表（时间与病种都已定）。
     *
     * <p><b>科室由诊断决定</b>（spec §6.1）：{@code e.disease().department()} 就是本次就诊的科室，
     * 不再有「主科室」「随机换科室」这类与病种无关的抽样。
     */
    private List<PendingVisit> newPendingVisits(Patient p, PatientProfile profile) {
        List<Disease> acutePool = ontologyRepository.diseasesFor(profile.age(), profile.gender());
        if (acutePool.isEmpty() && profile.chronicDiseases().isEmpty()) {
            throw new IllegalStateException("患者（" + profile.age() + " 岁，"
                    + (profile.gender() == 0 ? "男" : "女") + "）在 dept_diagnosis 里没有任何适用的诊断，"
                    + "生成器无法为他安排任何就诊。请检查 Task 4 的种子是否覆盖了该年龄段/性别");
        }

        int years = rnd.nextInt(MIN_YEARS, MAX_YEARS + 1);
        LocalDateTime windowEnd = LocalDateTime.now()
                .minusMinutes(DOWNSTREAM_BUFFER_MINUTES)
                .minusDays(rnd.nextLong(0, TAIL_SPREAD_DAYS + 1))
                .minusMinutes(rnd.nextLong(0, 1440));
        LocalDateTime windowStart = windowEnd.minusYears(years);

        List<PatientEvent> events = EventCalendar.build(profile, acutePool,
                p.getBirthDate().atStartOfDay(), windowStart, windowEnd, rnd);

        List<PendingVisit> pending = new ArrayList<>(events.size());
        int seq = 0;
        for (PatientEvent e : events) {
            String department = e.disease().department();
            List<String> doctors = doctorsByDept.get(department);
            if (doctors == null || doctors.isEmpty()) {
                throw new IllegalStateException("诊断 " + e.disease().icdCode()
                        + "（" + e.disease().name() + "）挂在科室「" + department
                        + "」，而 doctor 表里没有这个科室的医生，无法挂号。"
                        + "请检查 dept_diagnosis.department 与 doctor.department 的取值域是否一致");
            }
            Visit v = new Visit();
            v.setPatientId(p.getId());
            v.setVisitNo("V" + p.getId() + "-" + (++seq));
            v.setDepartment(department);
            v.setDoctor(doctors.get(rnd.nextInt(doctors.size())));
            v.setVisitTime(e.time());
            v.setStatus(0);
            pending.add(new PendingVisit(v, e.disease()));
        }
        return pending;
    }

    /**
     * 该诊断要开的药：按 {@code line} 升序逐线取，同线内按 weight 加权不放回，
     * 取满 {@code max} 种或取尽为止。
     *
     * <p><b>「一线优先」就是靠这个顺序</b>：只有当一线的可选药全部取完后才会轮到二线，
     * 所以二线药（如联合用药里的第二种降压药）在数据里的出现频率天然低于一线。
     */
    private List<DiagnosisDrug> pickDrugRows(Disease disease, int max) {
        List<DiagnosisDrug> rows = new ArrayList<>(ontologyRepository.drugsOf(disease));
        rows.sort(Comparator.comparingInt(DataGeneratorService::lineOf));
        List<DiagnosisDrug> picked = new ArrayList<>(max);
        int i = 0;
        while (i < rows.size() && picked.size() < max) {
            int line = lineOf(rows.get(i));
            List<DiagnosisDrug> sameLine = new ArrayList<>();
            while (i < rows.size() && lineOf(rows.get(i)) == line) {
                sameLine.add(rows.get(i++));
            }
            while (!sameLine.isEmpty() && picked.size() < max) {
                picked.add(sameLine.remove(Weighted.pickIndex(sameLine, DiagnosisDrug::getWeight, rnd)));
            }
        }
        return picked;
    }

    private static int lineOf(DiagnosisDrug row) {
        return row.getLine() == null ? 1 : row.getLine();
    }

    /** 从该诊断的用药本体生成处方明细（不含 prescription_id 与金额，由调用方回填）。 */
    private List<PrescriptionItem> buildItems(Disease disease) {
        List<DiagnosisDrug> rows = ontologyRepository.drugsOf(disease);
        if (rows.isEmpty()) {
            return List.of();       // 本体没收录该诊断的用药：不开方，也不拿别的诊断的药顶上
        }
        int target = rnd.nextInt(1, Math.min(MAX_PRESCRIBED_DRUGS, rows.size()) + 1);
        List<PrescriptionItem> items = new ArrayList<>(target);
        for (DiagnosisDrug row : pickDrugRows(disease, target)) {
            Drug drug = drugsById.get(row.getDrugId());
            if (drug == null) {
                throw new IllegalStateException("diagnosis_drug 的诊断 " + disease.icdCode()
                        + " 引用了不存在的 drug_id=" + row.getDrugId()
                        + "，请修正 seed-ontology.sql 的用药映射");
            }
            DrugDose dose = ontologyRepository.doseOf(drug.getId());
            if (dose == null) {
                // drug_dose 缺行说明该药不是「每日 N 次」的给药模型（Task 8 注②：甲氨蝶呤每周 1 次）。
                // 编一个「每日 3 次、每次 1 片」比少开一种药糟得多——那正是要消灭的编造。
                doseMissing++;
                continue;
            }
            items.add(newPrescriptionItem(drug, dose));
        }
        return items;
    }

    private PrescriptionItem newPrescriptionItem(Drug drug, DrugDose dose) {
        int times = OntologyRules.timesPerDay(dose.getFrequency());
        int days = rnd.nextInt(dose.getDaysMin(), dose.getDaysMax() + 1);
        PrescriptionItem it = new PrescriptionItem();
        it.setDrugId(drug.getId());
        it.setDrugName(drug.getName());
        it.setDosage(dose.getSingleDose().stripTrailingZeros().toPlainString() + dose.getDoseUnit());
        it.setFrequency(dose.getFrequency());
        it.setDays(days);
        it.setQuantity(BigDecimal.valueOf(packCount(days, times, dose, drug)));
        it.setUnit(drug.getUnit());
        return it;
    }

    /**
     * 一次开几盒。口服固体制剂按「疗程天数 × 每日次数 ÷ 每包装最小单位数」向上取整；
     * 外用/滴眼/滴耳/鼻喷一律 1 个包装。
     *
     * <p><b>为什么要按包装算</b>：{@code drug.price} 是每**盒**的模拟零售价，
     * 而 {@code charge.amount} 与 {@code dispense.quantity} 都取 {@code price × quantity}，
     * 所以 quantity 的单位只能是包装。旧实现写的是随机 1~3，与疗程天数毫无关系——
     * 「开 30 天的降压药只给 1 盒」这种数据由此而来。
     */
    private static int packCount(int days, int timesPerDay, DrugDose dose, Drug drug) {
        if ("次".equals(dose.getDoseUnit())) {
            // 外用的包装容量（每支可涂几次）不在本模型内，与 Task 8 注① 的
            // dose_unit='次' 是同一处妥协：只声明「开了 1 支」，不假装知道它能涂几次
            return 1;
        }
        Integer perPack = packSize(drug.getSpec());
        if (perPack == null || perPack <= 0) {
            return 1;   // 规格不表达「每包装几片」（如 100ml、137.15g/袋）：按 1 个包装开
        }
        int units = days * timesPerDay;
        return Math.max(1, (units + perPack - 1) / perPack);
    }

    /**
     * 从 {@code drug.spec}（如 {@code 0.1g*12片}、{@code 30mg*7片}）取每包装的最小单位数。
     * 取不到返回 {@code null}（如 {@code 100ml}、{@code 137.15g/袋}）。
     */
    private static Integer packSize(String spec) {
        if (spec == null) {
            return null;
        }
        int star = spec.indexOf('*');
        if (star < 0) {
            return null;
        }
        StringBuilder digits = new StringBuilder();
        for (int i = star + 1; i < spec.length(); i++) {
            char c = spec.charAt(i);
            if (!Character.isDigit(c)) {
                break;
            }
            digits.append(c);
        }
        return digits.length() == 0 ? null : Integer.valueOf(digits.toString());
    }

    /**
     * 该诊断的检验单：{@code required=1} 的项必开，其余按 weight 加权不放回抽 0~{@link #LAB_OPTIONAL_MAX} 项。
     *
     * <p><b>required=1 无条件全开</b>：外科类诊断的 required 项就是术前检查组（血常规 / 凝血 /
     * 感染筛查 / 心电图，Task 5 的 G-PREOP），门诊开这套并不失真——外科门诊本来就是
     * 围着「要不要手术、能不能手术」转的。阶段四的术前检查还会再用它一次。
     */
    private List<LabTest> newLabTests(Disease disease, Prescription pr) {
        List<DiagnosisLab> rows = ontologyRepository.labsOf(disease);
        if (rows.isEmpty()) {
            return List.of();       // 本体没有该诊断的检验项：不开单，不拿别的诊断的项顶上
        }
        List<DiagnosisLab> optional = new ArrayList<>();
        List<LabTest> labs = new ArrayList<>();
        for (DiagnosisLab row : rows) {
            if (row.getRequired() != null && row.getRequired() == 1) {
                labs.add(newLabTest(row, pr));
            } else {
                optional.add(row);
            }
        }
        int k = rnd.nextInt(0, Math.min(LAB_OPTIONAL_MAX, optional.size()) + 1);
        for (int i = 0; i < k; i++) {
            labs.add(newLabTest(optional.remove(Weighted.pickIndex(optional, DiagnosisLab::getWeight, rnd)), pr));
        }
        return labs;
    }

    private LabTest newLabTest(DiagnosisLab row, Prescription pr) {
        LabResults.Result r = LabResults.draw(row, rnd);
        LabTest lt = new LabTest();
        lt.setPatientId(pr.getPatientId());
        lt.setVisitId(pr.getVisitId());
        lt.setItemCode(row.getItemCode());
        lt.setItemName(row.getItemName());
        lt.setResultValue(r.value());
        lt.setUnit(row.getUnit());
        lt.setReferenceRange(row.getRefRange());
        lt.setIsAbnormal(r.abnormal());
        lt.setTestedAt(pr.getPrescribedAt().plusMinutes(rnd.nextInt(20, 90)));
        return lt;
    }
```

补 import：`java.util.Comparator`、`com.his.common.ontology.OntologyRules`
（`java.time.Duration` 若因删掉 `visitOffsets` 而不再被使用，一并删掉 import——
**以编译器的 `unused import` 提示为准，不要凭印象删**）。

- [ ] **Step 8: 重写 `DataGeneratorServiceTest` 的桩与断言**

**(a) 字段与桩**：Task 13 的 Step 7 把 `ontologyRepository` 建成了 `buildService()` 的**局部变量**，
而本任务的测试方法要在**方法体内**改写它的桩（如 `fails_loudly_when_...`），
所以要提升为字段——同时新增化验结果收集器：

```java
    private OntologyRepository ontologyRepository;
    private final List<LabTest> insertedLabs = new ArrayList<>();
```

```java
        ontologyRepository = mock(OntologyRepository.class);          // 去掉行首的 OntologyRepository 类型声明
        stubBatchInsert(lm, insertedLabs, LabTest::setId);            // 原为 stubBatchInsert(lm, null, ...)

        Doctor doc3 = new Doctor(); doc3.setId(3L); doc3.setName("王丽华"); doc3.setDepartment("儿科");
        when(doctorMapper.selectList(any())).thenReturn(List.of(doc1, doc2, doc3));

        // 三条病覆盖 0~89 岁连续年龄段，且都落在有医生的科室里
        Disease uri = disease("J06.9", "上呼吸道感染", "儿科", 0, 0, 14, false, false, "4.000", 0);
        Disease hypertension = disease("I10", "原发性高血压", "内科", 0, 18, 89, true, true, "4.000", 0.25);
        Disease gallstone = disease("K80.1", "胆囊结石伴胆囊炎", "外科", 0, 18, 89, true, true, "3.000", 0.12);
        List<Disease> allDiseases = List.of(uri, hypertension, gallstone);
        when(ontologyRepository.diseasesFor(anyInt(), anyInt()))
                .thenAnswer(inv -> allDiseases.stream()
                        .filter(d -> OntologyRules.ageGenderMatches(d.gender(), d.minAge(), d.maxAge(),
                                inv.getArgument(0), inv.getArgument(1)))
                        .toList());

        // 每条病挂两味药：drug 1 有剂量行，drug 3 没有 → 必然触发「跳过并计数」那条路径
        when(ontologyRepository.drugsOf(any())).thenAnswer(inv -> {
            Disease d = inv.getArgument(0);
            return List.of(drugRow(d.icdCode(), d.department(), RULE_DRUG_1, 1, "3.000"),
                    drugRow(d.icdCode(), d.department(), 3L, 2, "1.000"));
        });
        when(ontologyRepository.doseOf(RULE_DRUG_1)).thenReturn(dose(RULE_DRUG_1, "1", "片", "每日3次", "4", 5, 7));
        when(ontologyRepository.doseOf(3L)).thenReturn(null);

        // 一条 required 的必查项 + 一条可选项
        when(ontologyRepository.labsOf(any())).thenAnswer(inv -> {
            Disease d = inv.getArgument(0);
            return List.of(lab(d.icdCode(), d.department(), "BLD-WBC", "4.0-10.0", "HIGH", "1.000", 1),
                    lab(d.icdCode(), d.department(), "K", "3.5-5.5", "NONE", "1.000", 0));
        });
```

**(b) 新增测试辅助方法**（与既有的 `item(...)` 放一起）：

```java
    private Disease disease(String icd, String name, String dept, int gender, int minAge, int maxAge,
                            boolean chronic, boolean admissionEligible, String weight, double prevalence) {
        return new Disease(icd, name, dept, gender, minAge, maxAge, chronic, admissionEligible,
                Double.parseDouble(weight), new double[]{1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, prevalence);
    }

    private DiagnosisDrug drugRow(String icd, String dept, Long drugId, int line, String weight) {
        DiagnosisDrug r = new DiagnosisDrug();
        r.setIcdCode(icd); r.setDepartment(dept); r.setDrugId(drugId);
        r.setLine(line); r.setWeight(new BigDecimal(weight)); r.setPostop(0);
        return r;
    }

    private DiagnosisLab lab(String icd, String dept, String itemCode, String refRange,
                             String direction, String weight, int required) {
        DiagnosisLab r = new DiagnosisLab();
        r.setIcdCode(icd); r.setDepartment(dept); r.setItemCode(itemCode); r.setItemName(itemCode);
        r.setUnit("10^9/L"); r.setRefRange(refRange); r.setAbnormalDirection(direction);
        r.setAbnormalProb(new BigDecimal("0.500")); r.setWeight(new BigDecimal(weight)); r.setRequired(required);
        return r;
    }

    private DrugDose dose(Long drugId, String single, String unit, String freq, String maxDaily,
                          int daysMin, int daysMax) {
        DrugDose d = new DrugDose();
        d.setDrugId(drugId); d.setSingleDose(new BigDecimal(single)); d.setDoseUnit(unit);
        d.setFrequency(freq); d.setMaxDailyDose(new BigDecimal(maxDaily));
        d.setDaysMin(daysMin); d.setDaysMax(daysMax);
        return d;
    }
```

补 import：`com.his.common.ontology.OntologyRules`。既有的 `import com.his.common.entity.*;` 已覆盖新实体。

**(c) 删掉这四个已失去意义的测试**（连同 `RULE_DRUG_2` 若不再被引用也一并删）：

| 被删的测试 | 为什么它没有意义了 |
|---|---|
| `diagnoses_come_from_rule_config` | 诊断已不从 `rule_config` 来，改为下面的 `diagnoses_come_from_the_patients_own_disease` |
| `visit_count_and_history_span_stay_within_spec` | 「每人 5-20 次」是等分窗口时代的产物，事件驱动后次数由档案决定 |
| `patients_are_concentrated_in_a_home_department` | `HOME_DEPT_RATIO` 已删除，科室集中度不再是「造」出来的 |
| `prescribed_drugs_are_subset_of_rule_drug_ids` | 改为从用药本体判定，见下面 |

`injectViolation_produces_a_known_violation` 已在 Task 12 删除；若这里还在，说明 Task 12 没做完。

**(d) 新增/替换的测试**：

```java
    /** 每一位诊断都必须来自该次就诊科室的本体——「妇科医生看肠胃炎」的直接反测。 */
    @Test
    void diagnoses_come_from_the_patients_own_disease() {
        DataGeneratorService svc = buildService();
        svc.generate(50);

        Map<String, String> deptByIcd = Map.of("J06.9", "儿科", "I10", "内科", "K80.1", "外科");
        Map<Long, Visit> visitById = new HashMap<>();
        for (Visit v : insertedVisits) {
            visitById.put(v.getId(), v);
        }
        assertThat(insertedDiagnoses).isNotEmpty();
        for (Diagnosis d : insertedDiagnoses) {
            Visit v = visitById.get(d.getVisitId());
            assertThat(v).isNotNull();
            // 诊断的科室 == 就诊科室 == 医生所在科室，三者同一个来源
            assertThat(v.getDepartment()).isEqualTo(deptByIcd.get(d.getIcdCode()));
        }
    }

    /** 就诊时间落在「出生之后、now 之前」的 1-3 年窗口内，且每位患者至少一次就诊。 */
    @Test
    void visits_stay_inside_the_patients_own_history_window() {
        DataGeneratorService svc = buildService();
        svc.generate(50);
        LocalDateTime now = LocalDateTime.now();

        Map<Long, List<Visit>> byPatient = visitsByPatient();
        assertThat(byPatient).hasSize(50);
        for (List<Visit> visits : byPatient.values()) {
            LocalDateTime first = visits.get(0).getVisitTime();
            LocalDateTime last = visits.get(visits.size() - 1).getVisitTime();
            assertThat(last).isBeforeOrEqualTo(now);
            // 事件日历按「间隔推进」，不再把窗口等分，所以跨度只保证不超过上限
            assertThat(ChronoUnit.DAYS.between(first, last)).isBetween(0L, 1096L);
        }
    }

    /** 处方药只能来自该诊断的 diagnosis_drug；缺 drug_dose 的药被跳过并计数，不被编造剂量。 */
    @Test
    void prescribed_drugs_come_from_the_ontology_and_skip_missing_doses() {
        DataGeneratorService svc = buildService();
        Map<String, Long> stat = svc.generate(5);

        assertThat(insertedItems).isNotEmpty();
        for (PrescriptionItem it : insertedItems) {
            assertThat(it.getDrugId()).isEqualTo(RULE_DRUG_1);          // drug 3 无剂量行，必被跳过
            assertThat(it.getFrequency()).isEqualTo("每日3次");           // 频次逐字来自 drug_dose
            assertThat(it.getDosage()).isEqualTo("1片");
            assertThat(it.getDays()).isBetween(5, 7);
        }
        assertThat(stat.get("dose_missing")).isGreaterThan(0L);
    }

    /** required=1 的检验项每次就诊必开；可选项目不得出现本体之外的项目。 */
    @Test
    void required_labs_are_always_ordered_and_no_others_are_invented() {
        DataGeneratorService svc = buildService();
        svc.generate(5);

        // **只看门诊检验**：住院链（newInpatientLabTest / labTestForItem）在同一批插入里
        // 产出 BLD-HB、CRP 等本体外的项目，混进来会让本用例间歇性变红（INPATIENT_RATIO=0.2 下
        // 5 个患者里至少一人住院的概率约 67%）。住院链的检验项由 Task 17 的用例单独断言。
        List<LabTest> outpatientLabs = insertedLabs.stream()
                .filter(lt -> lt.getAdmissionId() == null)
                .toList();

        assertThat(outpatientLabs).isNotEmpty();
        for (LabTest lt : outpatientLabs) {
            assertThat(lt.getItemCode()).isIn("BLD-WBC", "K");
        }
        Map<Long, Set<String>> codesByVisit = new HashMap<>();
        for (LabTest lt : outpatientLabs) {
            codesByVisit.computeIfAbsent(lt.getVisitId(), k -> new HashSet<>()).add(lt.getItemCode());
        }
        for (Visit v : insertedVisits) {
            assertThat(codesByVisit.getOrDefault(v.getId(), Set.of())).contains("BLD-WBC");
        }
    }

    /** 化验值与 is_abnormal 必须自洽——这条自洽性正是校验器要反测的。 */
    @Test
    void lab_values_are_consistent_with_their_reference_range() {
        DataGeneratorService svc = buildService();
        svc.generate(20);

        int abnormal = 0;
        int normal = 0;
        for (LabTest lt : insertedLabs) {
            if (!"BLD-WBC".equals(lt.getItemCode())) {
                continue;                       // 可选项 K 的 direction=NONE，恒为正常值
            }
            double v = Double.parseDouble(lt.getResultValue());
            boolean outside = v < 4.0 || v > 10.0;
            assertThat(lt.getIsAbnormal() == 1).isEqualTo(outside);
            if (outside) {
                abnormal++;
            } else {
                normal++;
            }
        }
        // abnormal_prob=0.5：两侧都该出现，否则说明方向或概率其中之一没生效
        assertThat(abnormal).isGreaterThan(0);
        assertThat(normal).isGreaterThan(0);
    }

    /** 本体里出现的科室必须在 doctor 表里有医生，否则显式失败而不是生成一个没有医生的挂号。 */
    @Test
    void fails_loudly_when_the_disease_department_has_no_doctor() {
        DataGeneratorService svc = buildService();
        when(ontologyRepository.diseasesFor(anyInt(), anyInt())).thenReturn(List.of(
                disease("N76.0", "急性阴道炎", "妇科", 2, 15, 60, false, false, "3.200", 0)));

        assertThatThrownBy(() -> svc.generate(1))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("妇科");
    }

    /** 患者年龄段在本体里没有任何适用诊断时显式失败——这是种子覆盖缺口，不能被静默跳过。 */
    @Test
    void fails_loudly_when_a_patient_has_no_applicable_disease() {
        DataGeneratorService svc = buildService();
        when(ontologyRepository.diseasesFor(anyInt(), anyInt())).thenReturn(List.of());

        assertThatThrownBy(() -> svc.generate(1))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("没有任何适用的诊断");
    }
```

**(e) 两处小改**：

1. `visit_doctor_matches_its_department` 的科室→医生映射补一行 `"儿科", List.of("王丽华")`。
2. `generation_chunks_across_batch_boundary` 里的 `assertThat(stat.get("visits")).isGreaterThanOrEqualTo(501L * 5)`
   **删掉**——「每人至少 5 次」已不再是本平台的性质。该测试要证明的是「跨批次不丢记录」，
   所以保留 `patients == 501`、`insertedVisits.size() == stat.get("visits")` 与
   `byPatient` 的患者数 == 501 三条即可（若第三条不在原测试里，补上：
   `assertThat(visitsByPatient()).hasSize(501);`）。

- [ ] **Step 9: 跑全部生成器测试**

```bash
cd backend && mvn -o -pl his-generator test
```

Expected: BUILD SUCCESS，`DataGeneratorServiceTest` 与其余测试类全绿。

若 `fails_fast_when_no_usable_rule_configured` / `fails_loudly_when_rule_references_missing_drug`
失败，**不要删它们**——住院链仍在用 `rule_config`，这两个测试此刻仍是有效的（Task 17 才删）。

- [ ] **Step 10: Commit**

```bash
git add backend/his-generator/src/main/java/com/his/generator/Weighted.java \
        backend/his-generator/src/main/java/com/his/generator/LabResults.java \
        backend/his-generator/src/main/java/com/his/generator/EventCalendar.java \
        backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java \
        backend/his-generator/src/test/java/com/his/generator/WeightedTest.java \
        backend/his-generator/src/test/java/com/his/generator/LabResultsTest.java \
        backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java
git commit -m "feat(generator): 门诊链改为诊断决定科室 / 用药 / 检验，事件日历驱动就诊时间" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 17: 住院链改造 —— 入院延续门诊诊断，医嘱与检验取自本体（阶段二）

**Files:**
- Modify: `backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java`
  （`DATA_TABLES` 增 `operation`；常量区 / 字段区 / `generate()` / `flushPatients` 的住院段 /
  `newAdmissions` / `generateInpatientEvents` / `newInpatientLabTest` / `labTestForItem`；删 8 处死代码）
- Modify: `backend/his-generator/src/main/java/com/his/generator/MedicalData.java`
  （删 `DiagnosisRule`、`LabItem`、`LAB_ITEMS`；`SURNAMES`/`GIVEN` 保留）
- Test: `backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java`
  （删 2 个 `fails_fast...rule...` 测试 + `DiagnosisRule` 桩；改截断测试；加住院链断言）

**Interfaces:**
- Consumes：Task 16 的 `PendingVisit` / `pickDrugRows` / `doseMissing`；
  Task 13 的 `OntologyRepository.admissionEligibleDiseasesFor` / `drugsOf` / `labsOf` / `doseOf`；
  Task 14 的 `PatientProfile`；Task 4 的 `admission_eligible` 列；
  Task 1 的 `admission` 四个新列。
- Produces（Task 18/23 依赖下列签名，**名字与类型不得改**）：
  - `private List<Admission> newAdmissions(Patient p, PatientProfile profile, PendingVisit anchor)`
  - `private void generateInpatientEvents(Admission a, Disease disease,
    List<MedicalOrder> orderBuf, List<VitalSign> vitalBuf, List<LabTest> inpatientLabBuf)`
  - `private String admissionRoute(Disease disease)`；
    `private String evolveDiagnosis(PatientProfile profile, Disease admissionDisease)`
  - `private MedicalOrder newInpatientOrder(Admission a, Drug drug, DrugDose dose, int orderType,
    LocalDateTime start, LocalDateTime discharge)` —— **`dose` 为 `null` 时返回 `null`**，
    调用方计入 `doseMissing`
  - `generate()` 的返回 map 新增三个键 `pathway_lab_unmapped` / `pathway_med_missing` /
    `pathway_order_skipped`（三种跳过成因分开计数，理由见 Step 3(b) 的说明）
- **阶段四（Task 23）会改的两处**，本任务先写成够用的样子并在注释里标出来：
  `dischargeDisposition(PatientProfile, boolean isLastAdmission)` 会加一个「是否手术」参数；
  `generateInpatientEvents` 里长期医嘱的时段会按 `postop=1` 拆成术前/术后两段。

**这一任务消灭的原始缺陷**：`newAdmissions` 现在这样定诊断与科室（`DataGeneratorService.java:535-537`）——

```java
            String diagnosis = pathway != null ? pathway.getDiagnosisName() : randomDiagnosisName();
            String department = randomDepartment();          // ← 与 diagnosis 毫无关系
```

`randomDepartment()` 从 `doctorsByDept.keySet()` 里均匀抽一个科室，`randomDiagnosisName()` 从
`rule_config` 的 8 条里均匀抽一个诊断名，两者各自独立。于是「内科住院、诊断写急性阑尾炎」
或者反过来「外科住院、诊断写上呼吸道感染」都是常态，而且**入院诊断与科室不一致这件事
连一个报警都没有**——它是每次生成都在发生的主路径，不是边角。

改造后：入院诊断**就是**该患者最后一次「可住院」门诊的诊断，科室取自该诊断的
`dept_diagnosis.department`，术者/主管医生取自该科室的 `doctor`。三者同一个来源，
不一致在结构上不可能。

**「最后一次可住院的门诊」而不是「最后一次门诊」**：spec §6.2 同时要求
「仅 `admission_eligible=1` 的诊断可产生 `admission`」与「入院诊断延续该患者本次门诊的诊断」。
若取「最后一次门诊」，一个上呼吸道感染后的患者会被收治住院（J06.9 的 `admission_eligible=0`，
Task 4 明确置 0）；若改为「按可住院病种重新抽一个」，那就不再是「延续门诊诊断」。
两条约束唯一的共同解是：**在门诊链里从后往前找第一条可住院的诊断**。
一条都没有的患者不住院——这是如实反应，不是丢弃数据：本就该有一部分人不住院。

### 死亡与「此后不再就诊」

`discharge_disposition=DEATH` 是**唯一**允许该患者此后再无记录的出口（spec §4.8）。
本任务把它**限制在最后一次住院**上，理由是一条能算出来的时间关系：

住院的首次入院时间落在 `(anchor, latestFirst]` 内（`anchor` = 该患者**最后一次可住院门诊**），
其后每次 +`ADMISSION_GAP_DAYS` 天，`latestFirst ≤ now`。

**但「全部门诊记录都早于首次入院」不是自动成立的**：`anchor` 只是最后一次*可住院*门诊，
它之后完全可能还有若干次门诊（挂的是 `admission_eligible=0` 的诊断，如 J06.9 感冒）。
这些门诊会晚于首次入院，进而可能晚于 DEATH 时刻，
`death_only_on_the_last_admission_and_no_records_follow_it` 就会红。

所以要**显式裁一刀**（见 (e) 的 `flushPatients` 门诊段）：对将被收治住院的患者，
把其门诊链的右端裁到 `anchor`——`anchor` 之后不再产生任何记录。
裁在 `anchor` 而不是裁到「首次入院时刻」，是因为 `anchor` 在门诊段就已知、
后者要等 `newAdmissions` 返回才有；裁得更早同样满足结论，且这一刀落在诊断/处方/检验
生成**之前**，不会留下孤儿记录。

裁完之后链条是：`全部门诊记录 ≤ anchor < 首次入院 ≤ … ≤ 末次出院`。
于是只要死亡只发生在 `i == count - 1` 那次住院上，该患者死亡之后就不存在任何记录。
Task 23 的术前检查、手术记录同理，都挂在住院下。
这条推理写成测试（`death_only_on_the_last_admission_and_no_records_follow_it`），
**不要只写在注释里**：日后有人把 `isLastAdmission` 的判断改掉，这条关系就断了，而症状是
「某个患者死后还有门诊记录」——从数据上很难一眼看出来。

- [ ] **Step 1: 改测试（先写失败测试）**

**(a) 删掉两个已失去前提的测试**：`fails_fast_when_no_usable_rule_configured`
与 `fails_loudly_when_rule_references_missing_drug`。它们断言的是
「`rule_config` 没有可用规则时快速失败」——本任务之后生成器不再读 `rule_config`，
`ruleConfigMapper` 连构造器依赖都可以去掉，这两个测试的对象不存在了。
连同 `buildService()` 里那个 `RuleConfig r1 = ...; when(ruleConfigMapper.selectList(any())).thenReturn(List.of(r1));`
桩一并删除（`ruleConfigMapper` 本身**保留**：它是构造器参数、前端「规则配置」页还在用）。

**(b) 改截断测试**（`clears_data_tables_before_generation`）：数据表清单加 `operation`：

```java
        for (String table : List.of("patient", "visit", "diagnosis", "prescription", "prescription_item",
                "lab_test", "charge", "dispense", "admission", "medical_order", "vital_sign", "operation")) {
            verify(jdbcTemplate).execute("TRUNCATE TABLE " + table);
        }
```

> `verify(jdbcTemplate, never()).execute(contains("rule_config"))` 保留不动。
> `operation` 不含 `drug`，所以「不得碰 `diagnosis_drug` / `drug_dose`」那条守卫仍然成立——
> 而 `diagnosis_operation` 是字典表，本就不在 `DATA_TABLES` 里，两者不要混。

**(c) 补住院链断言**（新增，放在 `newAdmissions` 相关测试附近）：

```java
    /** 入院诊断必须延续该患者的一条可住院门诊诊断，且科室与医生一致——旧实现的随机科室在此被钉死。 */
    @Test
    void admission_diagnosis_continues_an_admission_eligible_outpatient_diagnosis() {
        DataGeneratorService svc = buildService();
        svc.generate(60);

        Map<Long, List<Visit>> visitsByPatient = visitsByPatient();
        Map<Long, Diagnosis> diagnosisByVisit = new HashMap<>();
        for (Diagnosis d : insertedDiagnoses) {
            diagnosisByVisit.put(d.getVisitId(), d);
        }
        Map<String, Disease> byName = Map.of(
                "上呼吸道感染", uriDisease(), "原发性高血压", hypertensionDisease(), "胆囊结石伴胆囊炎", gallstoneDisease());

        assertThat(insertedAdmissions).isNotEmpty();
        for (Admission a : insertedAdmissions) {
            Disease expected = byName.get(a.getAdmissionDiagnosis());
            assertThat(expected).as("入院诊断必须来自本体: " + a.getAdmissionDiagnosis()).isNotNull();
            assertThat(expected.admissionEligible()).as("不可住院的诊断不得产生住院").isTrue();
            assertThat(a.getDepartment()).isEqualTo(expected.department());

            // 该患者确实在门诊看过这个病
            boolean seenInOutpatient = visitsByPatient.get(a.getPatientId()).stream()
                    .map(v -> diagnosisByVisit.get(v.getId()))
                    .anyMatch(d -> d != null && expected.name().equals(d.getDiagnosisName()));
            assertThat(seenInOutpatient).as("入院诊断必须是该患者门诊看过的病").isTrue();
        }
    }

    /** 旧列 diagnosis 必须与新列 admission_diagnosis 同值（前端与既有查询读的是旧列）。 */
    @Test
    void legacy_diagnosis_column_mirrors_admission_diagnosis() {
        DataGeneratorService svc = buildService();
        svc.generate(60);

        assertThat(insertedAdmissions).isNotEmpty();
        for (Admission a : insertedAdmissions) {
            assertThat(a.getDiagnosis()).isEqualTo(a.getAdmissionDiagnosis());
            assertThat(a.getAdmissionRoute()).isIn("OUTPATIENT", "EMERGENCY", "TRANSFER");
            assertThat(a.getDischargeDisposition())
                    .isIn("CURED", "IMPROVED", "TRANSFER", "DEATH", "AGAINST_ADVICE");
        }
    }

    /**
     * 出院诊断与入院诊断同科室（诊断演进不允许跨科室漂移）。
     *
     * <p><b>本用例自带一份「同科室两条诊断」的本体桩</b>，这是它有意义的前提：
     * `buildService()` 的共享桩里内科、外科**各只有一条**可住院诊断，于是
     * `evolveDiagnosis` 的 `sameDept` 恒为空、永远走「不演进」那一支——
     * 断言比较的两个字符串必然相等，测的是一个恒真式。
     * 换成两条内科诊断后，`sameDept` 里恰好有另一条，「演进」分支才真的会被走到。
     */
    @Test
    void discharge_diagnosis_evolves_within_the_same_department() {
        DataGeneratorService svc = buildService();
        // 只留内科两条可住院诊断：此刻「同科室的另一个」唯一确定，演进结果可预期
        when(ontologyRepository.diseasesFor(anyInt(), anyInt()))
                .thenReturn(List.of(hypertensionDisease(), diabetesDisease()));
        when(ontologyRepository.admissionEligibleDiseasesFor(anyInt(), anyInt()))
                .thenReturn(List.of(hypertensionDisease(), diabetesDisease()));

        svc.generate(200);

        Map<String, String> deptByName = Map.of(
                "原发性高血压", "内科", "2型糖尿病", "内科");
        int evolved = 0;
        for (Admission a : insertedAdmissions) {
            assertThat(a.getAdmissionDiagnosis()).isIn(deptByName.keySet());
            assertThat(a.getDischargeDiagnosis()).isNotNull();
            assertThat(deptByName.get(a.getDischargeDiagnosis()))
                    .as("出院诊断跨科室漂移: " + a.getAdmissionDiagnosis() + " → " + a.getDischargeDiagnosis())
                    .isEqualTo(deptByName.get(a.getAdmissionDiagnosis()));
            if (!a.getDischargeDiagnosis().equals(a.getAdmissionDiagnosis())) {
                evolved++;
            }
        }
        // 这一句才是「同科室」那片的守门人：把 `DIAGNOSIS_EVOLUTION_RATE` 改成 0、或把
        // `sameDept` 的过滤条件写反，上面那条断言会**全绿**——它比较的两个值永远相等。
        // 200 名患者、约 40 人住院、约 80 次住院，`DIAGNOSIS_EVOLUTION_RATE = 0.25` 下一次都不演进
        // 的概率约 1e-10；真落到 0 就说明分支死了，不该放过。
        assertThat(evolved).as("一次演进都没发生，说明上面的同科室断言退化成了恒真式")
                .isGreaterThan(0);
    }

    /**
     * DEATH 只出现在最后一次住院；且该患者没有任何记录晚于那次出院时间。
     *
     * <p>把「死后不再有记录」变成可执行断言，而不是注释里的一句承诺——
     * 日后若 `isLastAdmission` 的取反，这条会红；症状放在数据里是看不出来的。
     */
    @Test
    void death_only_on_the_last_admission_and_no_records_follow_it() {
        DataGeneratorService svc = buildService();
        svc.generate(200);

        Map<Long, List<Admission>> byPatient = new HashMap<>();
        for (Admission a : insertedAdmissions) {
            byPatient.computeIfAbsent(a.getPatientId(), k -> new ArrayList<>()).add(a);
        }

        int deaths = 0;
        for (Map.Entry<Long, List<Admission>> e : byPatient.entrySet()) {
            List<Admission> adm = new ArrayList<>(e.getValue());
            adm.sort(Comparator.comparing(Admission::getAdmissionTime));
            for (int i = 0; i < adm.size(); i++) {
                if (!"DEATH".equals(adm.get(i).getDischargeDisposition())) {
                    continue;
                }
                deaths++;
                assertThat(i).as("死亡只能发生在最后一次住院").isEqualTo(adm.size() - 1);
                LocalDateTime death = adm.get(i).getDischargeTime();
                for (Visit v : visitsByPatient().getOrDefault(e.getKey(), List.of())) {
                    assertThat(v.getVisitTime()).as("死亡之后仍有门诊记录").isBefore(death);
                }
            }
        }
        // **这里不能断言 `deaths > 0`**：N=200 时死亡数的期望只有约 0.2。
        // 要连过四关才有一次死亡——「该患者有住院」（INPATIENT_RATIO=0.2）、
        // 「患者年龄 ≥ DEATH_MIN_AGE=75」、「恰好在最后一次住院上」、「rnd.nextDouble() < DEATH_RATE=0.03」。
        // 相乘后的量级是 0.1~0.2，断言 `> 0` 等于掷硬币，而且是长年朝反面落的那种。
        // DEATH 分支的**正向覆盖**交给 Task 23：那里 `dischargeDisposition(...)` 是显式传参的方法，
        // 可以直接构造 `isLastAdmission=true` 并桩住随机数，断言它返回 "DEATH"，不必靠抽样去碰。
        // 本用例只负责守住上面的时间不变量——「死亡只在最后一次住院」「死亡之后没有门诊记录」。
    }

    /**
     * 「全部门诊记录都早于首次入院」——这是「死亡患者此后不再有记录」的地基。
     *
     * <p>原稿靠一段推理认为它自动成立，**实际不成立**（详见改造说明「死亡与『此后不再就诊』」）：
     * `anchor` 只是最后一次*可住院*门诊，它之后还可能有门诊。现在由 `flushPatients` 的门诊段
     * 把 `anchor` 之后的门诊裁掉来保证。本用例就是那一刀的守门人——裁剪被删掉、
     * `isAfter` 写反、或裁剪被挪到诊断生成之后，它都会红。
     */
    @Test
    void no_outpatient_record_follows_the_first_admission() {
        DataGeneratorService svc = buildService();
        svc.generate(200);

        Map<Long, LocalDateTime> firstAdmission = new HashMap<>();
        for (Admission a : insertedAdmissions) {
            firstAdmission.merge(a.getPatientId(), a.getAdmissionTime(),
                    (x, y) -> x.isBefore(y) ? x : y);
        }
        assertThat(firstAdmission).as("一个被收治的患者都没有，本断言会空转").isNotEmpty();

        for (Visit v : insertedVisits) {
            LocalDateTime first = firstAdmission.get(v.getPatientId());
            if (first == null) {
                continue;                       // 该患者未被收治，门诊链不受裁剪约束
            }
            assertThat(v.getVisitTime())
                    .as("门诊记录晚于首次入院：patient=" + v.getPatientId())
                    .isBefore(first);
        }
    }

    /** 医嘱药品必须来自该住院诊断的用药本体，剂量取自 drug_dose。 */
    @Test
    void inpatient_orders_come_from_the_diagnosis_drug_ontology() {
        DataGeneratorService svc = buildService();
        svc.generate(60);

        assertThat(insertedOrders).isNotEmpty();
        for (MedicalOrder o : insertedOrders) {
            // 测试桩里每条诊断只挂 drug 1（有剂量行）；drug 3 无剂量行，必被跳过
            assertThat(o.getItemName()).isEqualTo("感冒灵颗粒");
            assertThat(o.getDosage()).isEqualTo("1片");
            assertThat(o.getFrequency()).isEqualTo("每日3次");
        }
    }

    /** 住院检验必须来自该诊断的检验本体，值必须与 is_abnormal 自洽。 */
    @Test
    void inpatient_labs_come_from_the_diagnosis_lab_ontology() {
        DataGeneratorService svc = buildService();
        svc.generate(60);

        List<LabTest> inpatientLabs = insertedLabs.stream()
                .filter(lt -> lt.getAdmissionId() != null).toList();
        assertThat(inpatientLabs).isNotEmpty();
        for (LabTest lt : inpatientLabs) {
            assertThat(lt.getItemCode()).isIn("BLD-WBC", "K");
            if ("BLD-WBC".equals(lt.getItemCode())) {
                double v = Double.parseDouble(lt.getResultValue());
                assertThat(lt.getIsAbnormal() == 1).isEqualTo(v < 4.0 || v > 10.0);
            } else {
                assertThat(lt.getIsAbnormal()).isZero();      // direction=NONE 恒为正常
            }
        }
    }

    /** 不可住院的诊断（J06.9）无论抽中多少次都不产生住院。 */
    @Test
    void non_admission_eligible_diagnostics_never_produce_an_admission() {
        DataGeneratorService svc = buildService();
        // 只剩上呼吸道感染：所有患者的门诊只有它，且它 admission_eligible=false
        when(ontologyRepository.diseasesFor(anyInt(), anyInt())).thenReturn(List.of(uriDisease()));
        when(ontologyRepository.admissionEligibleDiseasesFor(anyInt(), anyInt())).thenReturn(List.of());

        svc.generate(50);

        assertThat(insertedVisits).isNotEmpty();
        assertThat(insertedAdmissions).isEmpty();
    }
```

**(d) 测试辅助**：把三条 `Disease` 的构造提成方法，供 `buildService()` 与上面几个测试共用
（否则同一条病要在两处各写一遍 11 个参数，参数顺序看错一次就是半小时）：

```java
    private static Disease uriDisease() {
        return new Disease("J06.9", "上呼吸道感染", "儿科", 0, 0, 14, false, false,
                4.0, new double[]{1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, 0);
    }

    private static Disease hypertensionDisease() {
        return new Disease("I10", "原发性高血压", "内科", 0, 18, 89, true, true,
                4.0, new double[]{1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, 0.25);
    }

    private static Disease gallstoneDisease() {
        return new Disease("K80.1", "胆囊结石伴胆囊炎", "外科", 0, 18, 89, true, true,
                3.0, new double[]{1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, 0.12);
    }

    /**
     * 内科的第二条可住院诊断。**不进 `allDiseases`**——它只为
     * `discharge_diagnosis_evolves_within_the_same_department` 服务：那条用例需要一个
     * 「同科室里还有另一条可住院诊断」的池子，否则诊断演进分支根本走不到（见该用例的说明）。
     * 放进共享桩会改变其它用例的抽样序列，得不偿失。
     */
    private static Disease diabetesDisease() {
        return new Disease("E11.9", "2型糖尿病", "内科", 0, 18, 89, true, true,
                4.0, new double[]{1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, 0.20);
    }
```

并把 Task 16 在 `buildService()` 里写的三个 `Disease` 局部变量换成这三个方法调用；
同时补两条 `ontologyRepository.admissionEligibleDiseasesFor` 桩（Task 16 没打桩，
mock 会返回 `null` 而不是空表）：

```java
        when(ontologyRepository.admissionEligibleDiseasesFor(anyInt(), anyInt()))
                .thenAnswer(inv -> allDiseases.stream()
                        .filter(Disease::admissionEligible)
                        .filter(d -> OntologyRules.ageGenderMatches(d.gender(), d.minAge(), d.maxAge(),
                                inv.getArgument(0), inv.getArgument(1)))
                        .toList());
```

> `allDiseases` 是 `buildService()` 里的局部变量；若它不在 `admissionEligibleDiseasesFor` 的
> λ 作用域内，把它提成 `buildService()` 上方的一个局部 `List<Disease> allDiseases = List.of(...)`，
> **不要**为此新建字段。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-generator -Dtest=DataGeneratorServiceTest test
```

Expected: 编译失败——`Admission.getAdmissionDiagnosis()` / `getAdmissionRoute()` 等访问器找不到
（Task 1/2 才刚建好列与字段，Task 16 的生成器还没写它们），
以及 `admission_diagnosis_continues_...` 在运行时失败（当前 `newAdmissions` 写的是随机科室）。

> 若 Step 2 是**编译通过但测试失败**，说明 Task 1/2 已执行而生成器未改，这正是 TDD 要的中间态。
> 若 `Admission` 上找不到 `getAdmissionRoute()`，先确认 Task 2 的实体改动已提交，
> **不要**在本任务里补实体字段。

- [ ] **Step 3: 改住院段的调用点**

**(a) `generate()` 里重置新计数器**：在 `doseMissing = 0;` 之后加

```java
        pathwayLabUnmapped = 0;
        pathwayMedMissing = 0;
        pathwayOrderSkipped = 0;
```

在 `stat.put("vital_signs", vitalCount);` 之后加

```java
        stat.put("pathway_lab_unmapped", pathwayLabUnmapped);
        stat.put("pathway_med_missing", pathwayMedMissing);
        stat.put("pathway_order_skipped", pathwayOrderSkipped);
```

**(b) 字段区补三个计数**（与 `doseMissing` 放一起）：

```java
    /** `clinical_pathway` 的 LAB 步骤引用了该诊断 `diagnosis_lab` 里没有的检验项，被跳过的次数。 */
    private long pathwayLabUnmapped;

    /** `clinical_pathway` 的 MED 步骤药名在 `drug` 字典里查不到，被跳过的次数。 */
    private long pathwayMedMissing;

    /** 路径步骤（LAB 或 MED）能映射到本体，但时间窗校验没过、未能落成记录的次数。 */
    private long pathwayOrderSkipped;
```

> **为什么是三个而不是一个**（原稿只有一个 `pathwayLabUnmapped`，把三种完全不同的
> 成因堆在同一个计数器里，还冠以 `lab` 之名）：这三者排障时指向的方向完全不同——
> `pathwayLabUnmapped` 说明**本体缺检查项**（补 `diagnosis_lab`），
> `pathwayMedMissing` 说明**字典缺药**（补 `seed-ontology.sql`），
> `pathwayOrderSkipped` 说明**时间窗算错了**（改 `newInpatientOrder`）。
> 合成一个数，看到 `pathway_lab_unmapped = 137` 的人第一反应必然是去翻检验本体，
> 而真实成因可能一个都不在那。三个数字各自都能直接对上一条修法，这才叫「如实计数」。

**(c) `flushPatients` 的住院段**整体替换（门诊段见 (e)，顺序在**前**——本段读它建好的
`anchorByPatient`，且收治判定已移到那里，本段不再消耗随机数）：

```java
        // 住院：先只构造 Admission，回填 id 之后再生成医嘱/体征/住院检验
        //
        // `anchorByPatient` 与收治判定（`rnd.nextDouble() < INPATIENT_RATIO`）都已在**门诊段**
        // 做完——门诊段同时把 anchor 之后的门诊裁掉了（推导见本节开头「死亡与『此后不再就诊』」）。
        // 本段只按 `patients` 的既有顺序把住院单造出来。**不要**在这里重新判定收治、
        // 也不要再消耗一次 `rnd.nextDouble()`：那会让收治名单与已裁剪的门诊链对不上。
        List<Admission> admissionBuf = new ArrayList<>();
        Map<Long, Disease> admissionDiseaseByPatient = new HashMap<>();
        for (Patient p : patients) {
            PendingVisit anchor = anchorByPatient.get(p.getId());
            if (anchor == null) {
                continue;                       // 未被收治（或一条可住院诊断都没有）
            }
            admissionDiseaseByPatient.put(p.getId(), anchor.disease());
            admissionBuf.addAll(newAdmissions(p, profilesByPatient.get(p.getId()), anchor));
        }

        List<MedicalOrder> orderBuf = new ArrayList<>();
        List<VitalSign> vitalBuf = new ArrayList<>();
        List<LabTest> inpatientLabBuf = new ArrayList<>();
        admissionMapper.insert(admissionBuf);      // 回填 admission.id
        for (Admission a : admissionBuf) {
            generateInpatientEvents(a, admissionDiseaseByPatient.get(a.getPatientId()),
                    orderBuf, vitalBuf, inpatientLabBuf);
        }
        medicalOrderMapper.insert(orderBuf);
        vitalSignMapper.insert(vitalBuf);
```

> 后面紧随的 `inpatientLabBuf` 落库、`admissionMapper.updateById` 之类**保持原样**，不要动。

**(d) 常量区补四个**（与 `INPATIENT_RATIO` 放一起），并注明它们是启发式：

```java
    /** 入院途径为「转院」的概率（其余按出院性质分流）。 */
    private static final double TRANSFER_RATE = 0.02;
    /** 慢病急性加重走门诊转入的比例——**启发式**，spec §6.2 未给出具体数值。 */
    private static final double OUTPATIENT_SHARE_CHRONIC = 0.80;
    /** 急症（非慢病）走门诊转入的比例——同上，启发式。 */
    private static final double OUTPATIENT_SHARE_ACUTE = 0.25;
    /** 出院诊断发生演进（与入院诊断不同）的概率。 */
    private static final double DIAGNOSIS_EVOLUTION_RATE = 0.25;
    /** 自动出院（AGAINST_ADVICE）的概率。 */
    private static final double AGAINST_ADVICE_RATE = 0.03;
    /** 保守治疗（无手术）出院为「治愈」的比例，其余为「好转」。 */
    private static final double CURED_RATE = 0.20;
    /** 出现 DEATH 的最低年龄。 */
    private static final int DEATH_MIN_AGE = 75;
    /** 高龄患者的 DEATH 概率——**极小**，它是「此后不再就诊」的唯一出口。 */
    private static final double DEATH_RATE = 0.03;
```

- [ ] **Step 4: 重写 `newAdmissions` 与 `generateInpatientEvents`**

**`newAdmissions`** 整体替换（住院次数/窗口/间隔的算法**逐字保留**，只换诊断与科室的来源，
并补 4 个新列与出院情况）：

```java
    /**
     * 构造一名患者的住院记录（1-3 次），只填 Admission 字段；医嘱/体征/检验在回填 id 之后生成。
     *
     * @param anchor 该患者最后一次「可住院」的门诊——入院诊断与科室都由它决定
     */
    private List<Admission> newAdmissions(Patient p, PatientProfile profile, PendingVisit anchor) {
        LocalDateTime now = LocalDateTime.now();
        LocalDateTime lastVisit = anchor.visit().getVisitTime();
        Disease disease = anchor.disease();
        int count = rnd.nextInt(MIN_ADMISSIONS, MAX_ADMISSIONS + 1);
        // 相邻住院固定隔 ADMISSION_GAP_DAYS 天（> 最大住院 15 天 + 出院最多 +23h），
        // 「严格升序 + 互不重叠 + 末次入院不晚于 now」由构造直接保证，无需再回退钳制。
        // 末次门诊距今太近、放不下 count 次时，逐级减少次数（末次门诊必早于 now，故至少保留 1 次）。
        LocalDateTime latestFirst = now;
        while (count > 0) {
            latestFirst = now.minusDays((count - 1) * (long) ADMISSION_GAP_DAYS);
            if (lastVisit.isBefore(latestFirst)) {
                break;
            }
            count--;
        }

        // 首次入院在 (lastVisit, latestFirst] 内随机；其后每次 = 前一次 + ADMISSION_GAP_DAYS 天。
        long windowMinutes = Duration.between(lastVisit, latestFirst).toMinutes();
        LocalDateTime first = windowMinutes <= 1
                ? latestFirst
                : lastVisit.plusMinutes(rnd.nextLong(1, windowMinutes + 1));

        String department = disease.department();
        List<String> doctors = doctorsByDept.get(department);
        // 科室有医生由 newPendingVisits 保证过（诊断选出来时就查了一遍），此处不再重复抛异常
        List<Admission> list = new ArrayList<>(count);
        for (int i = 0; i < count; i++) {
            boolean lastOne = i == count - 1;
            Admission a = new Admission();
            a.setPatientId(p.getId());
            a.setAdmissionNo("A" + p.getId() + "-" + (i + 1));
            a.setDepartment(department);
            a.setDoctor(doctors.get(rnd.nextInt(doctors.size())));
            a.setBedNo(rnd.nextInt(1, 60) + "床");
            // 入院诊断延续该患者这次门诊的诊断；旧列 diagnosis 继续写同一个值（前端与既有查询读它）
            a.setDiagnosis(disease.name());
            a.setAdmissionDiagnosis(disease.name());
            a.setDischargeDiagnosis(evolveDiagnosis(profile, disease));
            a.setAdmissionRoute(admissionRoute(disease));
            // DEATH 只允许出现在最后一次住院上，见本节「死亡与『此后不再就诊』」的推导
            a.setDischargeDisposition(dischargeDisposition(profile, lastOne));
            a.setAdmissionTime(first.plusDays((long) i * ADMISSION_GAP_DAYS));
            a.setDeposit(BigDecimal.valueOf(rnd.nextInt(5, 21) * 100));
            a.setStatus(0);
            list.add(a);
        }
        return list;
    }

    /**
     * 入院途径。本体表里没有「这是急症还是慢病急性加重」这一列，
     * 用 {@code chronic} 近似：慢病急性加重多为门诊转入，急症多为急诊。
     *
     * <p>这是本设计已知的**简化**（spec §6.2 只说「多为」，未给数值），
     * 论文中须按「启发式」陈述，不得写成「依据临床指南」。
     */
    private String admissionRoute(Disease disease) {
        double u = rnd.nextDouble();
        if (u < TRANSFER_RATE) {
            return "TRANSFER";
        }
        double outpatientShare = disease.chronic() ? OUTPATIENT_SHARE_CHRONIC : OUTPATIENT_SHARE_ACUTE;
        return u - TRANSFER_RATE < outpatientShare ? "OUTPATIENT" : "EMERGENCY";
    }

    /**
     * 出院诊断：允许与入院诊断不同（诊断演进），但**必须同科室**。
     * 跨科室漂移（内科入院、外科出院）不是演进，是错误，所以这里的候选池按科室过滤。
     *
     * <p>该科室没有第二个可住院诊断时**不演进**——不跨科室编一个出来。
     */
    private String evolveDiagnosis(PatientProfile profile, Disease admissionDisease) {
        if (rnd.nextDouble() >= DIAGNOSIS_EVOLUTION_RATE) {
            return admissionDisease.name();
        }
        List<Disease> sameDept = ontologyRepository
                .admissionEligibleDiseasesFor(profile.age(), profile.gender()).stream()
                .filter(d -> admissionDisease.department().equals(d.department()))
                .filter(d -> !admissionDisease.icdCode().equals(d.icdCode()))
                .toList();
        if (sameDept.isEmpty()) {
            return admissionDisease.name();
        }
        return sameDept.get(rnd.nextInt(sameDept.size())).name();
    }

    /**
     * 出院情况。**DEATH 只出现在最后一次住院上**（见本节开头的推导），
     * 且只出现在高龄患者上、概率极小。
     *
     * <p>手术联动（有手术以 CURED/IMPROVED 为主、高手术级别才出现 DEATH）
     * 在阶段四 Task 23 补上；本阶段全部住院都无手术，故只写保守治疗这一支。
     */
    private String dischargeDisposition(PatientProfile profile, boolean isLastAdmission) {
        if (isLastAdmission && profile.age() >= DEATH_MIN_AGE && rnd.nextDouble() < DEATH_RATE) {
            return "DEATH";
        }
        double u = rnd.nextDouble();
        if (u < AGAINST_ADVICE_RATE) {
            return "AGAINST_ADVICE";
        }
        return u < AGAINST_ADVICE_RATE + CURED_RATE ? "CURED" : "IMPROVED";
    }
```

**`generateInpatientEvents`** 整体替换（体征那段逐字保留；出院时间改为**先算**，
因为长期医嘱的 `stop_time` 要用它）：

```java
    /** 生成一次住院的医嘱/生命体征/住院检验，并回写出院字段（dischargeTime/totalCost/status）。 */
    private void generateInpatientEvents(Admission a, Disease disease,
                                         List<MedicalOrder> orderBuf,
                                         List<VitalSign> vitalBuf, List<LabTest> inpatientLabBuf) {
        ClinicalPathway pathway = pathwayFor(a.getAdmissionDiagnosis());
        int totalDays = rnd.nextInt(MIN_STAY_DAYS, MAX_STAY_DAYS + 1);
        boolean compliant = pathway != null && rnd.nextDouble() < PATHWAY_COMPLIANCE_RATE;

        // 出院时间先算出来：长期医嘱的 stop_time 就是它（阶段四的手术会把出院时间往后推，
        // 届时这里要改成「先手术、再定出院」，见 Task 23）
        LocalDateTime discharge = a.getAdmissionTime().plusDays(totalDays).plusHours(rnd.nextInt(0, 24));
        a.setDischargeTime(discharge);

        // 长期医嘱（首日开，执行至出院）；药品来自该诊断的 diagnosis_drug，剂量来自 drug_dose
        for (DiagnosisDrug row : pickDrugRows(disease, rnd.nextInt(1, 4))) {
            Drug drug = drugsById.get(row.getDrugId());
            if (drug == null) {
                throw new IllegalStateException("diagnosis_drug 的诊断 " + disease.icdCode()
                        + " 引用了不存在的 drug_id=" + row.getDrugId()
                        + "，请修正 seed-ontology.sql 的用药映射");
            }
            MedicalOrder o = newInpatientOrder(a, drug, ontologyRepository.doseOf(drug.getId()), 0,
                    a.getAdmissionTime().plusMinutes(rnd.nextInt(5, 60)), discharge);
            if (o != null) {
                orderBuf.add(o);
            }
        }

        // 每日生命体征（早中晚 3 次，带趋势；day 0 落在入院时间之前的记录跳过）
        for (int day = 0; day < totalDays; day++) {
            for (int slot = 0; slot < 3; slot++) {
                VitalSign vs = newVitalSign(a, day, slot);
                if (!vs.getRecordedAt().isBefore(a.getAdmissionTime())) {
                    vitalBuf.add(vs);
                }
            }
        }

        // 每日临时/检验医嘱
        for (int day = 0; day < totalDays; day++) {
            if (rnd.nextInt(3) == 0) {  // 临时医嘱：同一条诊断的用药，一次性执行
                List<DiagnosisDrug> rows = ontologyRepository.drugsOf(disease);
                if (!rows.isEmpty()) {
                    Drug drug = drugsById.get(rows.get(Weighted.pickIndex(rows, DiagnosisDrug::getWeight, rnd))
                            .getDrugId());
                    if (drug != null) {
                        MedicalOrder o = newInpatientOrder(a, drug, ontologyRepository.doseOf(drug.getId()), 1,
                                a.getAdmissionTime().plusDays(day).plusHours(9), discharge);
                        if (o != null) {
                            orderBuf.add(o);
                        }
                    }
                }
            }
            if (rnd.nextInt(2) == 0) {  // 检验医嘱 -> lab_test
                LabTest lt = newInpatientLabTest(a, disease, day);
                if (lt != null) {
                    inpatientLabBuf.add(lt);
                }
            }
        }

        // 路径：合规则按步骤补齐对应 LAB/MED 事件，不合规则跳过（自然变异）
        if (compliant && pathway != null) {
            for (PathwayStep step : parsePathwaySteps(pathway.getSteps())) {
                if (step.day() >= totalDays) continue;
                if ("LAB".equals(step.action())) {
                    LabTest lt = labTestForItem(a, disease, step);
                    if (lt == null) {
                        pathwayLabUnmapped++;   // 路径引用了本诊断没有的检验项：如实计数，不静默跳过
                    } else {
                        inpatientLabBuf.add(lt);
                    }
                } else if ("MED".equals(step.action())) {
                    // 路径里的药名不一定在 drug 字典里，查得到才开；查不到计入 pathwayMedMissing
                    Drug drug = drugsById.values().stream()
                            .filter(x -> x.getName().equals(step.item())).findFirst().orElse(null);
                    if (drug == null) {
                        pathwayMedMissing++;
                        continue;
                    }
                    MedicalOrder o = newInpatientOrder(a, drug, ontologyRepository.doseOf(drug.getId()), 1,
                            a.getAdmissionTime().plusDays(step.day()).plusHours(10), discharge);
                    if (o == null) {
                        pathwayOrderSkipped++;  // 映射得到，但时间窗没过：与「缺本体/缺药」不是一回事
                    } else {
                        orderBuf.add(o);
                    }
                }
            }
        }
        a.setStatus(1);
        a.setTotalCost(a.getDeposit().add(BigDecimal.valueOf(totalDays * 120L)));
    }

    /**
     * 一条来自本诊断用药本体的医嘱。
     *
     * <p><b>{@code dose} 为 {@code null} 时返回 {@code null}</b>，并把 {@code doseMissing} 加一
     * （与 Task 16 的处方明细同一条约定）：该药没有剂量规则说明它不是「每日 N 次」的给药模型，
     * 编一个「1 片、每日 3 次」比少开一条医嘱糟得多。
     */
    private MedicalOrder newInpatientOrder(Admission a, Drug drug, DrugDose dose, int orderType,
                                           LocalDateTime start, LocalDateTime discharge) {
        if (dose == null) {
            doseMissing++;
            return null;
        }
        MedicalOrder o = new MedicalOrder();
        o.setPatientId(a.getPatientId()); o.setAdmissionId(a.getId());
        o.setOrderType(orderType);
        o.setItemName(drug.getName());
        o.setDosage(dose.getSingleDose().stripTrailingZeros().toPlainString() + dose.getDoseUnit());
        o.setFrequency(dose.getFrequency());
        o.setStartTime(start);
        // 临时医嘱一次性执行完就停（当日 23:59），长期医嘱执行至出院
        o.setStopTime(orderType == 0 ? discharge : start.toLocalDate().atTime(23, 59));
        o.setStatus(orderType == 0 ? 0 : 2);
        o.setDoctor(a.getDoctor());
        return o;
    }
```

**`newInpatientLabTest` / `labTestForItem`** 替换（两者都改用本体 + `LabResults`）：

```java
    private LabTest newInpatientLabTest(Admission a, Disease disease, int day) {
        List<DiagnosisLab> rows = ontologyRepository.labsOf(disease);
        if (rows.isEmpty()) {
            return null;        // 该诊断本体里没有检验项：不开单，不拿别的诊断的项顶上
        }
        DiagnosisLab row = rows.get(Weighted.pickIndex(rows, DiagnosisLab::getWeight, rnd));
        return newInpatientLabTestFromRow(a, row, a.getAdmissionTime().plusDays(day).plusHours(8));
    }

    private LabTest labTestForItem(Admission a, Disease disease, PathwayStep step) {
        DiagnosisLab row = ontologyRepository.labsOf(disease).stream()
                .filter(x -> x.getItemName().equals(step.item())).findFirst().orElse(null);
        if (row == null) {
            return null;
        }
        return newInpatientLabTestFromRow(a, row,
                a.getAdmissionTime().plusDays(step.day())
                        .plusHours(rnd.nextInt(1, Math.max(2, step.window()))));
    }

    /** 用一根本体行生成一条住院检验；取值与异常标志由 {@link LabResults} 保证自洽。 */
    private LabTest newInpatientLabTestFromRow(Admission a, DiagnosisLab row, LocalDateTime testedAt) {
        LabResults.Result r = LabResults.draw(row, rnd);
        LabTest lt = new LabTest();
        lt.setPatientId(a.getPatientId()); lt.setAdmissionId(a.getId());
        lt.setItemCode(row.getItemCode()); lt.setItemName(row.getItemName());
        lt.setResultValue(r.value());
        lt.setUnit(row.getUnit()); lt.setReferenceRange(row.getRefRange());
        lt.setIsAbnormal(r.abnormal());
        lt.setTestedAt(testedAt);
        return lt;
    }
```

补 import：`java.time.LocalDate`（`newInpatientOrder` 用到 `start.toLocalDate()`）、
`com.his.common.entity.DiagnosisDrug` / `DiagnosisLab` / `DrugDose` / `Drug`
（若原文件用的是逐个 import 而非通配）。

- [ ] **Step 5: 删死代码**

| 删除对象 | 位置（改动前） | 为什么它现在是死的 |
|---|---|---|
| `randomDiagnosisName()` | 668-670 | 唯一调用方是旧 `newAdmissions` |
| `randomDepartment()` | 678-680 | 同上 |
| `randomDrugName()` | 669-673 | 唯一调用方是旧 `generateInpatientEvents` |
| `pickDrugIds(DiagnosisRule)` | 756-761 | 唯一调用方是 Task 16 已重写的 `buildItems` |
| `loadRules()` | 763-770 | 只给 `rules` 赋值 |
| 字段 `rules`、`loadRules()` 的调用 | 101、119 | 生成器不再读 `rule_config` |
| `MedicalData.DiagnosisRule` | `MedicalData.java:6` | 无引用 |
| `MedicalData.LabItem`、`MedicalData.LAB_ITEMS` | `MedicalData.java:7,12-18` | 已由 `diagnosis_lab` 本体取代 |

**`MedicalData.SURNAMES` / `GIVEN` / `randomPatientName()` 保留**——患者姓名仍从这里取。

> **`ruleConfigMapper` 字段与构造器参数保留**：前端「规则配置」页读的是同一张表，
> 表本身没废（spec §6.4），只是生成器不再拿它当数据源。
> 删掉构造器参数会让 `@RequiredArgsConstructor` 的 18 个参数变 17 个，
> 而 `DataGeneratorServiceTest` 是**按位置**传参的——白改一处、白红一次。

```bash
cd backend && mvn -o -pl his-generator test
```

Expected: BUILD SUCCESS。

- [ ] **Step 6: 全量回归**

```bash
cd backend && mvn -o test
```

Expected: 各模块全绿。`his-server` 的测试与本次无关，但 `mvn -o test` 会一并跑，
**若 `his-server` 红了，先确认不是 Task 10 留下的**（Task 10 的 `RationalDrugValidatorTest`
在本任务之前就应已全绿），不要在本任务里顺手改校验器。

- [ ] **Step 7: Commit**

```bash
git add backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java \
        backend/his-generator/src/main/java/com/his/generator/MedicalData.java \
        backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java
git commit -m "feat(generator): 住院链延续门诊诊断，医嘱与检验取自本体；清理 rule_config 死代码" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 18: 阶段二出口实测 —— 重复就诊率、随访间隔、敏感性分析（阶段二）

**Files:**
- Modify: `backend/sql/verify-final.sql`（四处被本次改造作废的断言）
- Create: `backend/sql/verify-phase2.sql`
- Modify: `docs/test-report.md`（新增一节，填**实测**数值）
- Modify: `docs/references/README.md`（把 §5.3 里「需逐条核实」的条目状态同步过去——
  已核实的标出处、没核实的保留「假设」）

**Interfaces:**
- Consumes：Task 16/17 的生成器；Task 4 的 `dept_diagnosis.chronic`；Task 15 的
  `-Dhis.followUpMedianDays` / `-Dhis.recheckProbMin` / `-Dhis.recheckProbMax` 三个覆盖口子
- Produces：`docs/test-report.md` 里一组**可被论文直接引用**的实测数字，
  以及它们的复现命令。**本任务不产出代码。**

**这个任务为什么必须单独存在**：阶段二的出口判据（「同患者重复就诊率 > 0；
慢病随访间隔中位数落在设计值附近」）**不是靠单元测试证明的**。
`EventCalendarTest` 证明的是「日历这个函数算得对」，它拿的是合成池与固定种子；
而出口判据问的是「生成出来的这张表在真实本体上长什么样」。
两者之间隔着 72 条种子、40 个科室医生、196 种药——只有跑一遍真库才看得见。
把这一步合并进 Task 17，就会出现「测试全绿但没人知道随访间隔实际是多少」的状态。

### 关于「实测」的硬要求

`docs/test-report.md` 里本节的所有数字**必须是 `verify-phase2.sql` 的输出**，
逐条粘贴，不许估算、不许沿用旧值、不许写「约」。跑不出来就写「未实测」并附命令。
这是 spec §13 与 CLAUDE.md 的共同要求（见「Global Constraints」的「报告数值必须实测」一条）。

同理，敏感性分析的两档结果**必须在同一台机器、同一份种子数据上跑**——
换了本体或改了生成器，两档之间就没有可比性。

- [ ] **Step 1: 修 `verify-final.sql` 里被本次改造作废的四处**

这四处断言的**前提**已经不存在了。留着它们比删掉更糟：脚本会照常输出一张表，
而表里的人会以为「有检查」——实际上它在验一条已经没人保证的性质。

| 位置 | 原断言 | 为什么作废 | 改成 |
|---|---|---|---|
| §1.2 | 每名患者就诊次数 min≥5、max≤20 | 次数由事件日历决定，不再是等分窗口的固定条数 | 只报 `min/avg/max` 与分布，**去掉 `out_of_range` 列与判据**；新增「0 次就诊的患者数（应为 0）」 |
| §1.4 | `FIND_IN_SET(i.drug_id, rc.drug_ids)` 应 0 | 处方药已改由 `diagnosis_drug` 决定，不再受 `rule_config` 约束 | 换成对 `diagnosis_drug` 的越界检查（见下方 SQL） |
| §2.4 | 病史跨度 365–1096 天 | 跨度只保证 ≤1096；事件稀疏时可能只有几十天 | 只报跨度分布；判据改为 `SUM(span > 1097) = 0` |
| §3.3.2 | 检验异常率（设计 15%） | 异常率已改为逐项 `diagnosis_lab.abnormal_prob`，全局单一值不存在了 | 改成**按项目**报异常率，并 JOIN `diagnosis_lab` 给出设计值做对照 |

§1.4 替换为：

```sql
SELECT '=== 1.4 处方药是否都在该诊断的 diagnosis_drug 里（越界应为 0）===' AS section;
SELECT COUNT(*) AS items_total,
       SUM(dd.drug_id IS NULL AND ddl.drug_id IS NULL
           AND EXISTS (SELECT 1 FROM diagnosis_drug x WHERE x.icd_code = d.icd_code)) AS violations,
       SUM(NOT EXISTS (SELECT 1 FROM diagnosis_drug x WHERE x.icd_code = d.icd_code)) AS uncovered
FROM prescription_item i
JOIN prescription pr ON i.prescription_id = pr.id
JOIN diagnosis d ON d.visit_id = pr.visit_id
JOIN visit v ON v.id = pr.visit_id
LEFT JOIN diagnosis_drug dd
       ON dd.icd_code = d.icd_code AND dd.department = v.department AND dd.drug_id = i.drug_id
LEFT JOIN diagnosis_drug ddl
       ON ddl.icd_code = d.icd_code AND ddl.department IS NULL AND ddl.drug_id = i.drug_id;
```

> 两个 LEFT JOIN 对应 `OntologyRules.select` 的两级匹配（先精确科室、再 NULL 回落），
> 任一命中即算合规。**`violations` 里那个 `EXISTS` 不能省**：没有它，这句 SQL 数的是
> 「该诊断在本体里一行都没有」的处方项——那正是「未覆盖」，不是「越界」。
> 两者混在一个数里会让这个指标失去意义：把一个本体没覆盖的诊断当成越界，
> 于是「越界 = 0」这个判据会在**补本体**时莫名其妙地变红，而越界本身早已清零。
> 所以拆成两列各报各的：`violations` 才是判据，`uncovered` 如实报出、单独看
> （本体覆盖不足是 Task 11 `OntologyConsistencyValidator` 的 `unchecked` 该管的事）。

§3.3.2 替换为：

```sql
SELECT '=== 3.3.2 检验异常率：设计值 vs 实测（按项目）===' AS section;
SELECT l.item_code,
       COUNT(*) AS tested,
       SUM(l.is_abnormal = 1) AS abnormal,
       ROUND(100.0 * SUM(l.is_abnormal = 1) / COUNT(*), 2) AS actual_pct
FROM lab_test l
GROUP BY l.item_code
ORDER BY l.item_code;
```

设计值不在 `lab_test` 里，而 `diagnosis_lab.abnormal_prob` 是**逐(诊断,项目)**的，
无法与「按项目聚合的实测值」一一对上——两者的口径差异**必须在报告里写明**，
不要拼成一张看似可比的表。正确的对照是「同项目在不同诊断下的实测异常率 vs 该诊断的设计值」：

```sql
SELECT '* 口径对照：同项目在不同诊断下的实测异常率 vs 该诊断的设计值' AS note;
SELECT d.icd_code, dl.item_code, dl.abnormal_prob AS designed_prob,
       COUNT(*) AS tested, ROUND(100.0 * SUM(l.is_abnormal = 1) / COUNT(*), 2) AS actual_pct
FROM lab_test l
JOIN visit v ON v.id = l.visit_id
JOIN diagnosis d ON d.visit_id = v.id
JOIN diagnosis_lab dl ON dl.icd_code = d.icd_code AND dl.item_code = l.item_code
GROUP BY d.icd_code, dl.item_code, dl.abnormal_prob
HAVING tested >= 20
ORDER BY d.icd_code, dl.item_code;
```

- [ ] **Step 2: 新建 `verify-phase2.sql`**

```sql
-- 阶段二出口实测：患者级疾病轨迹的可测指标
-- 用法：mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -t < verify-phase2.sql
--
-- 本脚本只读、只输出，不改任何数据。所有指标都定义在注释里——
-- 「重复就诊率」这类词先有定义才有数，否则两个人算出来不一样。

SELECT '=== 2.0 前提：本次生成是否用了本体（下列三项任一为 0 则阶段二未生效）===' AS section;
SELECT 'dept_diagnosis(字典)' AS t, COUNT(*) AS rows_ FROM dept_diagnosis
UNION ALL SELECT 'diagnosis_drug(字典)', COUNT(*) FROM diagnosis_drug
UNION ALL SELECT 'diagnosis_lab(字典)', COUNT(*) FROM diagnosis_lab
UNION ALL SELECT 'visit(数据)', COUNT(*) FROM visit
UNION ALL SELECT 'admission(数据)', COUNT(*) FROM admission;

-- 指标 1：同患者重复就诊率
--   定义：同一患者因**同一 ICD 编码**就诊 ≥2 次的，占全部有就诊患者数的百分比。
--   出口判据：> 0（旧实现里这个数恒为 0——每次就诊独立随机选诊断，同一人患同病两次几乎不可能）
SELECT '=== 2.1 同患者重复就诊率（判据：> 0）===' AS section;
SELECT COUNT(*) AS patients_with_visits,
       SUM(repeat_kinds > 0) AS patients_with_repeat,
       ROUND(100.0 * SUM(repeat_kinds > 0) / COUNT(*), 2) AS repeat_rate_pct,
       SUM(kinds) AS distinct_patient_diagnosis_pairs
FROM (SELECT patient_id, COUNT(*) AS kinds, SUM(c >= 2) AS repeat_kinds
      FROM (SELECT d.patient_id, d.icd_code, COUNT(*) c
            FROM diagnosis d WHERE d.icd_code IS NOT NULL
            GROUP BY d.patient_id, d.icd_code) x
      GROUP BY patient_id) y;

-- 指标 2：慢病随访间隔中位数
--   定义：同一患者、同一**慢病**诊断的相邻两次就诊之间的天数，取全部间隔的中位数。
--     · 「慢病」= dept_diagnosis.chronic = 1
--     · 只取相邻两次**同病**就诊，中间夹了别的病不算断（那正是随访的定义）
--     · 同一天内多次（复诊）不计：间隔为 0 的样本不参与，否则中位数会被拉到 0
--   出口判据：落在设计值 91 天的附近；「附近」的口径由本脚本的实测分布决定，
--             报告里同时给出 P25/P50/P75，不靠一句话描述
SELECT '=== 2.2 慢病随访间隔（天）中位数（设计值 91）===' AS section;
SELECT COUNT(*) AS intervals,
       MIN(gap) AS min_gap, MAX(gap) AS max_gap,
       ROUND(AVG(gap), 1) AS mean_gap,
       ROUND(AVG(CASE WHEN rn IN (FLOOR((n + 1) / 2), CEIL((n + 1) / 2)) THEN gap END), 1) AS median_gap,
       ROUND(AVG(CASE WHEN rn IN (FLOOR(n * 0.25), CEIL(n * 0.25)) THEN gap END), 1) AS p25_gap,
       ROUND(AVG(CASE WHEN rn IN (FLOOR(n * 0.75), CEIL(n * 0.75)) THEN gap END), 1) AS p75_gap
FROM (SELECT gap,
             ROW_NUMBER() OVER (ORDER BY gap) AS rn,
             COUNT(*) OVER () AS n
      FROM (SELECT d.patient_id, d.icd_code,
                   TIMESTAMPDIFF(DAY,
                       LAG(v.visit_time) OVER (PARTITION BY d.patient_id, d.icd_code
                                               ORDER BY v.visit_time),
                       v.visit_time) AS gap
            FROM diagnosis d
            JOIN visit v ON v.id = d.visit_id
            WHERE EXISTS (SELECT 1 FROM dept_diagnosis dg
                           WHERE dg.icd_code = d.icd_code AND dg.chronic = 1)) g
      WHERE gap IS NOT NULL AND gap > 0) t;

-- 指标 3：急性病就诊的复发（同一急性病的第二次就诊）
--   这是「重复就诊率」的分母之外的一个补充视角：慢病的重复本该出现，
--   急性病的重复才是「同一个人反复感冒」这类真实但低频的现象。
--   2.2/2.3 都写成 EXISTS 而不是 JOIN dept_diagnosis：同一个 ICD 在
--   dept_diagnosis 里可能有多个科室行（见 Task 4 的「部门归属」注），
--   JOIN 会按科室数扇出，把同一次就诊数成多次——2.2 的相邻间隔会被复制、
--   中位数失真，2.3 的 c >= 2 会在只去过一次时就成立。
SELECT '=== 2.3 急性病同病重复（非慢病）===' AS section;
SELECT COUNT(*) AS acute_pairs, SUM(c >= 2) AS repeated_pairs,
       ROUND(100.0 * SUM(c >= 2) / COUNT(*), 2) AS repeat_pct
FROM (SELECT d.patient_id, d.icd_code, COUNT(*) c
      FROM diagnosis d
      WHERE EXISTS (SELECT 1 FROM dept_diagnosis dg
                     WHERE dg.icd_code = d.icd_code AND dg.chronic = 0)
      GROUP BY d.patient_id, d.icd_code) x;

-- 指标 4：季节峰是否生效
--   挑一个月权重差异最大的病（Task 4 里呼吸道类冬季权重高），看按月分布是否成峰。
SELECT '=== 2.4 呼吸道类诊断的就诊月份分布（应冬季偏高）===' AS section;
SELECT MONTH(v.visit_time) AS month_, COUNT(*) AS cnt
FROM diagnosis d JOIN visit v ON v.id = d.visit_id
WHERE d.icd_code IN (SELECT icd_code FROM dept_diagnosis
                     WHERE JSON_EXTRACT(month_weights, '$[0]') > 1.0)
GROUP BY month_ ORDER BY month_;

-- 指标 5：死亡是唯一的时间线终止出口
--   判据：不存在「death 出院时间之后仍有该患者的就诊/住院/医嘱/体征/检验」的记录
SELECT '=== 2.5 死亡后仍有记录（全部应为 0）===' AS section;
SELECT 'visit' AS after_death, COUNT(*) AS bad
  FROM visit v JOIN admission a ON a.patient_id = v.patient_id
 WHERE a.discharge_disposition = 'DEATH' AND v.visit_time > a.discharge_time
UNION ALL SELECT 'admission(后续住院)', COUNT(*)
  FROM admission a2 JOIN admission a ON a.patient_id = a2.patient_id
 WHERE a.discharge_disposition = 'DEATH' AND a2.admission_time > a.discharge_time
UNION ALL SELECT 'medical_order', COUNT(*)
  FROM medical_order o JOIN admission a ON a.id = o.admission_id
 WHERE a.discharge_disposition = 'DEATH' AND o.start_time > a.discharge_time
UNION ALL SELECT 'vital_sign', COUNT(*)
  FROM vital_sign vs JOIN admission a ON a.id = vs.admission_id
 WHERE a.discharge_disposition = 'DEATH' AND vs.recorded_at > a.discharge_time
UNION ALL SELECT 'lab_test', COUNT(*)
  FROM lab_test l JOIN admission a ON a.id = l.admission_id
 WHERE a.discharge_disposition = 'DEATH' AND l.tested_at > a.discharge_time;

-- 指标 6：住院域的硬约束（阶段二的另一半出口判据）
SELECT '=== 2.6 住院诊断↔科室↔医生 一致性（应为 0）===' AS section;
SELECT '入院诊断不在 dept_diagnosis' AS chk, COUNT(*) AS bad
  FROM admission a
 WHERE NOT EXISTS (SELECT 1 FROM dept_diagnosis dg
                    WHERE dg.diagnosis_name = a.admission_diagnosis)
UNION ALL SELECT '入院诊断的科室 ≠ 住院科室', COUNT(*)
  FROM admission a
 WHERE NOT EXISTS (SELECT 1 FROM dept_diagnosis dg
                    WHERE dg.diagnosis_name = a.admission_diagnosis
                      AND dg.department = a.department)
UNION ALL SELECT '主管医生不属于该住院科室', COUNT(*)
  FROM admission a LEFT JOIN doctor doc ON doc.name = a.doctor AND doc.department = a.department
 WHERE doc.id IS NULL
UNION ALL SELECT '出院诊断与入院诊断跨科室', COUNT(*)
  FROM admission a
 WHERE NOT EXISTS (
   SELECT 1 FROM dept_diagnosis d1 JOIN dept_diagnosis d2 ON d1.department = d2.department
    WHERE d1.diagnosis_name = a.admission_diagnosis
      AND d2.diagnosis_name = a.discharge_diagnosis)
UNION ALL SELECT '旧列 diagnosis ≠ 新列 admission_diagnosis', COUNT(*)
  FROM admission WHERE diagnosis <> admission_diagnosis;

-- 指标 7：出院去向分布（DEATH 应为极小概率，且只在最高龄段）
SELECT '=== 2.7 出院去向分布 ===' AS section;
SELECT discharge_disposition, COUNT(*) AS cnt,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM admission), 2) AS pct
FROM admission GROUP BY discharge_disposition ORDER BY cnt DESC;

SELECT '=== 2.8 入院途径分布 ===' AS section;
SELECT admission_route, COUNT(*) AS cnt,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM admission), 2) AS pct
FROM admission GROUP BY admission_route ORDER BY cnt DESC;

-- 指标 9：人均年就诊次数（§7.3 敏感性分析的第三列）
--   定义：分母是**患者·年**，不是患者数。每名患者的病史跨度是 1~3 年（Task 15 的事件日历），
--   拿「总次数 / 患者数」得到的是「人均历史就诊次数」——它会随病史长短变化，
--   三档敏感性分析之间**没有可比性**（换一档参数、跨度分布就变了，量出来的差可能全来自分母）。
--   患者·年 = Σ max(365, 末次就诊 − 首次就诊 的天数) / 365。下限按 1 年计：
--   否则一个只来过一次的患者（跨度 0 天）会以近 0 的分母把人均值顶到天上。
--   两个口径都报出来，是为了让读报告的人一眼看出自己手上的是哪一个。
SELECT '=== 2.9 人均年就诊次数（分母为患者·年）===' AS section;
SELECT s.visits_total,
       (SELECT COUNT(*) FROM patient) AS patients_total,
       ROUND(s.patient_years, 1) AS patient_years,
       ROUND(s.visits_total / s.patient_years, 2) AS visits_per_patient_year,
       ROUND(s.visits_total / (SELECT COUNT(*) FROM patient), 2) AS visits_per_patient_history
FROM (SELECT SUM(n) AS visits_total,
             SUM(GREATEST(365, TIMESTAMPDIFF(DAY, lo, hi))) / 365.0 AS patient_years
      FROM (SELECT patient_id, MIN(visit_time) lo, MAX(visit_time) hi, COUNT(*) n
            FROM visit GROUP BY patient_id) p) s;
```

> **2.6 的四条断言必须按「诊断名称」关联，不得按 `icd_code`**：
> `admission` 存的是诊断**名称**（`上呼吸道感染`），不是编码——Task 17 的
> `admission_diagnosis_continues_an_admission_eligible_outpatient_diagnosis`
> 就是拿 `byName.get(a.getAdmissionDiagnosis())` 按名字查的。
> 若写成 `JOIN dept_diagnosis ON dg.icd_code = a.admission_diagnosis`，
> 中文病名永远匹配不上任何编码，于是第 1 条把**每一次住院**都算成违规（期望 0），
> 第 2、4 条则恒为 0（恒真通过）——一个假警报配两个假通过。
>
> **且不能用 `JOIN` + `WHERE dg.department <> a.department` 的写法**：
> 同一个病名在本体里可能对应多个科室（`J06.9 上呼吸道感染` 在 内科 与 儿科 各一行，
> 见 Task 4 的「部门归属」注），`JOIN` 会扇出成多行，
> 一个 内科 的 `上呼吸道感染` 住院会因为扇出的 儿科 那一行被判成「科室不符」。
> 所以四条一律写成 `NOT EXISTS`，第 4 条比较的是「两个病名**共同的**科室集合是否为空」。

> **`JSON_EXTRACT(month_weights, '$[0]')` 的写法对不上就用 `LIKE`**：
> MySQL 8.0 把 `month_weights` 当 JSON 存时 `JSON_EXTRACT` 可用，
> 若该列建成了 `VARCHAR`，改成 `WHERE month_weights LIKE '[1.%'` 之类。
> 以 `SHOW CREATE TABLE dept_diagnosis` 的实际列类型为准，**不要照抄**。

- [ ] **Step 3: 确认库已迁移，再重跑生成器**

按 `CLAUDE.md` 的部署顺序，**先确认 7 张新表与 `lab_test.admission_id`、`admission` 的 4 个新列都在**
（本机运行库可能还没应用二期增量）：

```bash
mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -e "
SELECT table_name FROM information_schema.tables
 WHERE table_schema='his_demo' AND table_name IN
   ('dept_diagnosis','diagnosis_drug','diagnosis_lab','drug_dose','visit_rate',
    'operation','diagnosis_operation');
SELECT column_name FROM information_schema.columns
 WHERE table_schema='his_demo' AND table_name='admission'
   AND column_name IN ('admission_route','discharge_disposition',
                       'admission_diagnosis','discharge_diagnosis');
SELECT column_name FROM information_schema.columns
 WHERE table_schema='his_demo' AND table_name='lab_test' AND column_name='admission_id';"
```

缺表/缺列就先执行 `backend/sql/schema.sql` 的增量段与 `seed-ontology.sql`，**不要跳过**——
缺表时 `OntologyRepository.load()` 会静默拿到空本体，生成器随后在
「患者没有任何适用的诊断」上抛异常；那个报错信息会把人往「种子覆盖不足」的方向带，
而真实原因是表根本没建。

重建并重跑生成器：

```bash
cd backend && mvn -q -pl his-generator -am install -DskipTests
java -jar his-generator/target/his-generator-1.0.0.jar
```

Expected: 生成结束打印各表行数，且 `dose_missing` / `pathway_lab_unmapped` / `pathway_med_missing` /
`pathway_order_skipped` 四个计数**都不为 0 也不大得离谱**（`dose_missing` 的合理解释见 Task 8 注②的
甲氨蝶呤；`pathway_lab_unmapped` 是 `clinical_pathway` 引用了本诊断没有的检验项；
`pathway_med_missing` 是路径药名不在 `drug` 字典里；`pathway_order_skipped` 是时间窗没过）。
四个数都要抄进报告——它们是「生成器如实报出了自己跳过了什么」的证据。

> `pathway_order_skipped` 若**明显偏大**（比如与 LAB 步骤数同量级），多半不是路径的问题，
> 而是 `newInpatientOrder` 的时间窗把落点算到出院之后了——先按 Task 17 Step 3(c) 的
> `newInpatientOrder` 定义核对 `start`/`discharge` 的先后，再去怀疑数据。
> 这正是把它与另外两个分开计数的价值：一个数就能把排查方向缩到一处。

- [ ] **Step 4: 跑出口脚本，把实测值抄进 `docs/test-report.md`**

```bash
mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -t < backend/sql/verify-phase2.sql
mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -t < backend/sql/verify-final.sql
```

在 `docs/test-report.md` 末尾新增一节：

```markdown
## 7. 三期 医学真实感改造测试（本体约束 + 患者疾病轨迹）

### 7.0 前提与复现命令

（填：库版本、生成器版本、患者数、`dose_missing` / `pathway_lab_unmapped` /
`pathway_med_missing` / `pathway_order_skipped` 四个计数、以下两条命令的原样输出位置）

### 7.1 本体约束（阶段一出口判据）

（填 `verify-final.sql` 中 §1.4 处方药越界数与未覆盖数、§2.6 住院一致性五项，
以及 Task 21 实测的 `POST /api/validation/run` 里的 `ontology.checked` / `ontology.unchecked` /
`ontology.consistentRate`）

> **接口名与字段名以 Task 12 的 `ValidationController` 为准：`POST /api/validation/run`，
> 字段是 `consistentRate`。** 本节早先写的 `GET /api/validation/ontology` + `consistencyRate`
> 两个名字都不存在——照抄会拿到 404 与一个空值。
> **覆盖率（`coverage`）不在这个响应里**：校验报告只算「已检查的项里合规的比例」，
> 而分母本身就受本体覆盖影响，一个接口同时报两者会自相矛盾。
> 覆盖率由前端 `ValidationReport.vue` 用 `dept_diagnosis` 的覆盖比例单独算出来展示
> （见 Task 21），要写进报告时从**页面**抄，不要从接口找。

### 7.2 患者疾病轨迹（阶段二出口判据）

| 指标 | 实测值 | 设计/判据 | 结论 |
|---|---|---|---|
| 同患者重复就诊率 | （实测） | > 0 | （填） |
| 慢病随访间隔中位数 | （实测） | 设计值 91 天 | （填） |
| 随访间隔 P25 / P75 | （实测） | — | — |
| 急性病同病重复率 | （实测） | — | — |
| 12 月 vs 7 月就诊量比 | （实测） | 冬季偏高 | （填） |
| 死亡后仍有记录 | （实测，应 0） | = 0 | （填） |

### 7.3 敏感性分析（spec §5.3 要求）

两档对照，**同一台机器、同一份种子**，只改命令行参数：

| 档位 | 命令参数 | 随访间隔中位数（实测） | 重复就诊率（实测） | 人均年就诊次数（实测） |
|---|---|---|---|---|
| 设计值 | （无参数） | | | |
| 低档 | `-Dhis.followUpMedianDays=60` | | | |
| 高档 | `-Dhis.followUpMedianDays=120` | | | |
| 复诊高频 | `-Dhis.recheckProbMin=0.25 -Dhis.recheckProbMax=0.35` | | | |

> 三列全部取自 `verify-phase2.sql`：随访间隔中位数 = §2.2 的 `median_gap`、
> 重复就诊率 = §2.1 的 `repeat_rate_pct`、人均年就诊次数 = §2.9 的 `visits_per_patient_year`。
> **第三列必须用 `visits_per_patient_year`，不是 `visits_per_patient_history`**——
> 后者的分母是患者数，会跟着病史跨度分布一起变，三档之间不可比（理由见 §2.9 的注释）。

（每档都要**删掉上一档的数据再重跑**——生成器会 TRUNCATE 数据表，所以直接重跑即可，
但同一档的 `verify-phase2.sql` 输出必须与生成器日志中的 `patients` / `visits` 数对上。）
```

**跑敏感性分析的三条命令**（注意它是 `java -D... -jar` 的**参数顺序**——`-D` 必须在 `-jar` 之前，
写在 `-jar` 之后就变成传给应用的参数，`System.getProperty` 取不到，静默用回设计值）：

```bash
cd backend
java -Dhis.followUpMedianDays=60 -jar his-generator/target/his-generator-1.0.0.jar
mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -t < sql/verify-phase2.sql

java -Dhis.followUpMedianDays=120 -jar his-generator/target/his-generator-1.0.0.jar
mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -t < sql/verify-phase2.sql

java -Dhis.recheckProbMin=0.25 -Dhis.recheckProbMax=0.35 -jar his-generator/target/his-generator-1.0.0.jar
mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -t < sql/verify-phase2.sql

# 收尾：把库恢复成设计值那一档的数据，否则交付的是实验档位的数据集
java -jar his-generator/target/his-generator-1.0.0.jar
mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -t < sql/verify-phase2.sql
```

**最后一条必须跑**：交付的数据集要是设计值那档。这一步在命令清单里显式写出，
是因为它紧接着三条实验命令、又长得和它们差不多，漏掉之后**从数据和代码上都看不出来**——
只有重新量一次随访间隔才会发现是 60 天。

- [ ] **Step 5: 同步 `docs/references/README.md` 的核实状态**

test-report 里若引用了「就诊星期分布」「日内到达分布」「环节耗时」这三项中的任何一项，
对应地在 `docs/references/README.md` §六 里把状态从「需逐条核实」改成实际状态：
找到了中文文献 → 补出处；没找到 → 保持「假设」，并在 test-report 里明确写成假设。
**不许在没找到出处的情况下按文献口吻陈述**（这是 spec §5.3 的硬规则）。

- [ ] **Step 6: Commit**

```bash
git add backend/sql/verify-final.sql backend/sql/verify-phase2.sql \
        docs/test-report.md docs/references/README.md
git commit -m "test: 阶段二出口实测脚本与敏感性分析，作废四处失效断言" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

**阶段二完成标志**：Task 16/17/18 三次提交都在，`mvn -o test` 全绿，
`docs/test-report.md` §7 的表格里每一格都是实测值（或明确写着「未实测」+ 命令）。

---

## Task 19: 时间分布模型 —— 星期、日内到达、环节耗时级联（阶段三）

**Files:**
- Create: `backend/his-generator/src/main/java/com/his/generator/TimeDistribution.java`
- Create: `backend/his-generator/src/main/java/com/his/generator/ClinicTimeline.java`
- Test: `backend/his-generator/src/test/java/com/his/generator/TimeDistributionTest.java`
- Test: `backend/his-generator/src/test/java/com/his/generator/ClinicTimelineTest.java`

**Interfaces:**
- Consumes：Task 16 的 `Weighted.pickIndex(double[], RandomGenerator)`
- Produces（Task 20 依赖下列签名，**名字与类型不得改**）：
  - `TimeDistribution`（包级可见，纯函数）：
    - `static double weekdayMultiplier(DayOfWeek dow)`（索引 0=周一）
    - `static LocalDateTime deferToWorkday(LocalDateTime t, RandomGenerator rnd)`
    - `static LocalDateTime placeAtArrivalHour(LocalDateTime day, RandomGenerator rnd)`
    - 包级常量 `WEEKDAY_MULTIPLIER`（`double[7]`）、`HOUR_WEIGHT`（`double[10]`）、`FIRST_HOUR`
  - `ClinicTimeline`（包级可见 record）：
    - `static ClinicTimeline of(LocalDateTime visitTime, RandomGenerator rnd)`
    - 访问器 `visitTime()` / `diagnosedAt()` / `prescribedAt()` / `testedAt()` / `chargedAt()` / `dispensedAt()`

**为什么这两件事在同一个任务里**：它们都是「把一个日期变成一串时刻」的纯计算，
合起来的产出是「一次门诊从进院到取药的全部时间戳」这一条完整的时间骨架。
拆开的话，Task 20 要接线两次、改两次生成器——而两次改动都在同一个 `flushPatients` 里。

**星期分布为什么用「推迟」而不是「重排」**：事件日历产出的是**间隔序列**，
若按星期乘子把事件日重新分布到偏好日，随访间隔就不再是对数正态——中位数那条出口判据会跟着漂。
改用**有界的礼拜推迟**（周末的非紧急就诊以一定概率推到下周一）：
间隔只被推后 1~2 天，分布形状不变，而「周一峰、周末谷」照样出现。
急性就诊**不推迟**——急症不会因为周末就不去。

**这三类参数全部是「假设值」**：spec §5.3 把「就诊星期分布」「日内到达分布」「环节耗时」
标为**需逐条核实**，而本项目**没有 CNKI / 万方的检索通道**（见 `docs/references/README.md` §六）。
所以：代码里按假设实现、注释里写明是假设、报告里按假设陈述并给出可复现的测量命令。
**不得**在没打开过原文的情况下写成「据某某文献」。

- [ ] **Step 1: 写失败的测试**

新建 `backend/his-generator/src/test/java/com/his/generator/TimeDistributionTest.java`：

```java
package com.his.generator;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.DayOfWeek;
import java.time.LocalDateTime;
import java.util.random.RandomGenerator;
import java.util.random.RandomGeneratorFactory;

import static org.assertj.core.api.Assertions.assertThat;

class TimeDistributionTest {

    private static RandomGenerator rnd() {
        return RandomGeneratorFactory.of("L64X128MixRandom").create(20260921L);
    }

    @Test
    @DisplayName("星期乘子：周一最高、周末最低，且索引 0 是周一")
    void weekday_multipliers_peak_on_monday_and_dip_at_the_weekend() {
        assertThat(TimeDistribution.weekdayMultiplier(DayOfWeek.MONDAY))
                .isEqualTo(TimeDistribution.WEEKDAY_MULTIPLIER[0]);
        assertThat(TimeDistribution.weekdayMultiplier(DayOfWeek.SUNDAY))
                .isEqualTo(TimeDistribution.WEEKDAY_MULTIPLIER[6]);
        assertThat(TimeDistribution.weekdayMultiplier(DayOfWeek.MONDAY))
                .isGreaterThan(TimeDistribution.weekdayMultiplier(DayOfWeek.FRIDAY));
        assertThat(TimeDistribution.weekdayMultiplier(DayOfWeek.SATURDAY))
                .isLessThan(TimeDistribution.weekdayMultiplier(DayOfWeek.FRIDAY));
        assertThat(TimeDistribution.weekdayMultiplier(DayOfWeek.SUNDAY))
                .isLessThan(TimeDistribution.weekdayMultiplier(DayOfWeek.SATURDAY));
        for (double m : TimeDistribution.WEEKDAY_MULTIPLIER) {
            assertThat(m).isPositive();
        }
    }

    @Test
    @DisplayName("工作日原样返回；周末以 (1-乘子) 的概率推到下周一")
    void defer_to_workday_only_moves_weekend_events() {
        RandomGenerator r = rnd();
        for (int i = 0; i < 500; i++) {
            LocalDateTime wed = LocalDateTime.of(2026, 3, 4, 9, 30);       // 周三
            assertThat(TimeDistribution.deferToWorkday(wed, r)).isEqualTo(wed);
        }

        int deferredSaturday = 0;
        int deferredSunday = 0;
        for (int i = 0; i < 20000; i++) {
            LocalDateTime sat = LocalDateTime.of(2026, 3, 7, 9, 30);        // 周六
            if (!TimeDistribution.deferToWorkday(sat, r).equals(sat)) {
                deferredSaturday++;
            }
            LocalDateTime sun = LocalDateTime.of(2026, 3, 8, 9, 30);        // 周日
            if (!TimeDistribution.deferToWorkday(sun, r).equals(sun)) {
                deferredSunday++;
            }
        }
        // 接受概率 = 该日乘子：周六 0.45 → 约 55% 被推迟；周日 0.35 → 约 65%
        assertThat(deferredSaturday).isBetween(10600, 11400);
        assertThat(deferredSunday).isBetween(12600, 13400);
    }

    @Test
    @DisplayName("推迟后的日期一定是周一，且时间部分不变")
    void deferred_events_land_on_monday_with_the_same_time_of_day() {
        RandomGenerator r = rnd();
        LocalDateTime sun = LocalDateTime.of(2026, 3, 8, 14, 25, 40);       // 周日
        LocalDateTime moved = TimeDistribution.deferToWorkday(sun, r);
        // 0.35 的接受率下总有被推迟的样本；loop 到出现为止，避免用固定种子赌一次
        int guard = 0;
        while (moved.equals(sun) && guard++ < 1000) {
            sun = LocalDateTime.of(2026, 3, 8, 14, 25, 40);
            moved = TimeDistribution.deferToWorkday(sun, r);
        }
        assertThat(guard).isLessThan(1000);
        assertThat(moved.getDayOfWeek()).isEqualTo(DayOfWeek.MONDAY);
        assertThat(moved.toLocalTime()).isEqualTo(sun.toLocalTime());
        assertThat(moved.toLocalDate()).isAfter(sun.toLocalDate());
    }

    @Test
    @DisplayName("日内到达时刻落在门诊时段内，且保留原日期")
    void arrival_hour_stays_inside_the_clinic_hours() {
        RandomGenerator r = rnd();
        LocalDateTime day = LocalDateTime.of(2026, 3, 4, 0, 0);
        for (int i = 0; i < 5000; i++) {
            LocalDateTime t = TimeDistribution.placeAtArrivalHour(day, r);
            assertThat(t.toLocalDate()).isEqualTo(day.toLocalDate());
            assertThat(t.getHour())
                    .isGreaterThanOrEqualTo(TimeDistribution.FIRST_HOUR)
                    .isLessThan(TimeDistribution.FIRST_HOUR + TimeDistribution.HOUR_WEIGHT.length);
        }
    }

    @Test
    @DisplayName("上午到达的比例明显高于下午（分段常数、上午峰）")
    void arrivals_peak_in_the_morning() {
        RandomGenerator r = rnd();
        LocalDateTime day = LocalDateTime.of(2026, 3, 4, 0, 0);
        int morning = 0;
        int total = 20000;
        for (int i = 0; i < total; i++) {
            if (TimeDistribution.placeAtArrivalHour(day, r).getHour() < 12) {
                morning++;
            }
        }
        // HOUR_WEIGHT 前 4 段（8~11 点）合计 6.0，全天合计 9.05 → 约 66%
        assertThat(morning).isBetween(12800, 13700);
    }

    @Test
    @DisplayName("到达时刻在分钟与秒上都有抖动，不会整天落在同一分钟")
    void arrival_minute_and_second_vary() {
        RandomGenerator r = rnd();
        LocalDateTime day = LocalDateTime.of(2026, 3, 4, 0, 0);
        long distinctMinutes = java.util.stream.IntStream.range(0, 2000)
                .map(i -> TimeDistribution.placeAtArrivalHour(day, r).getMinute())
                .distinct().count();
        assertThat(distinctMinutes).isGreaterThan(30);
    }
}
```

新建 `backend/his-generator/src/test/java/com/his/generator/ClinicTimelineTest.java`：

```java
package com.his.generator;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.random.RandomGenerator;
import java.util.random.RandomGeneratorFactory;

import static org.assertj.core.api.Assertions.assertThat;

class ClinicTimelineTest {

    private static RandomGenerator rnd() {
        return RandomGeneratorFactory.of("L64X128MixRandom").create(20260921L);
    }

    @Test
    @DisplayName("环节耗时级联：挂号 → 候诊 → 面诊 → 开方，严格递增")
    void the_cascade_is_strictly_increasing() {
        RandomGenerator r = rnd();
        for (int i = 0; i < 2000; i++) {
            ClinicTimeline t = ClinicTimeline.of(LocalDateTime.of(2026, 3, 4, 8, 0), r);
            assertThat(t.diagnosedAt()).isAfter(t.visitTime());
            assertThat(t.prescribedAt()).isAfter(t.diagnosedAt());
            assertThat(t.testedAt()).isAfter(t.prescribedAt());
            assertThat(t.chargedAt()).isAfter(t.prescribedAt());
            assertThat(t.dispensedAt()).isAfter(t.chargedAt());
        }
    }

    @Test
    @DisplayName("诊断时刻 = 挂号 10 + 候诊 30 + 面诊 5 = 第 45 分钟，逐次都一样")
    void diagnosis_happens_at_a_fixed_offset() {
        RandomGenerator r = rnd();
        LocalDateTime visit = LocalDateTime.of(2026, 3, 4, 8, 0);
        for (int i = 0; i < 100; i++) {
            assertThat(ClinicTimeline.of(visit, r).diagnosedAt()).isEqualTo(visit.plusMinutes(45));
        }
    }

    @Test
    @DisplayName("取药晚于收费、收费晚于开方——三者不会出现时间倒挂")
    void pharmacy_follows_payment_which_follows_prescription() {
        RandomGenerator r = rnd();
        for (int i = 0; i < 5000; i++) {
            ClinicTimeline t = ClinicTimeline.of(LocalDateTime.of(2026, 3, 4, 9, 15), r);
            assertThat(t.dispensedAt()).isAfter(t.chargedAt());
            assertThat(t.chargedAt()).isAfter(t.prescribedAt());
        }
    }

    @Test
    @DisplayName("整条链落在同一天内——半天的抖动不会把取药时刻推到次日")
    void the_whole_visit_fits_in_one_day() {
        RandomGenerator r = rnd();
        LocalDateTime visit = LocalDateTime.of(2026, 3, 4, 8, 0);
        for (int i = 0; i < 5000; i++) {
            ClinicTimeline t = ClinicTimeline.of(visit, r);
            assertThat(t.dispensedAt().toLocalDate()).isEqualTo(visit.toLocalDate());
        }
    }
}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-generator -Dtest='TimeDistributionTest,ClinicTimelineTest' test
```

Expected: 编译失败——`TimeDistribution` / `ClinicTimeline` 符号找不到。

- [ ] **Step 3: 新建 `TimeDistribution.java`**

```java
package com.his.generator;

import java.time.DayOfWeek;
import java.time.LocalDateTime;
import java.util.random.RandomGenerator;

/**
 * 就诊时间的星期与日内分布。
 *
 * <p><b>本类的每一个常数都是「假设值」</b>：spec §5.3 把「就诊星期分布」与「日内到达分布」
 * 标为需逐条核实，而本项目没有 CNKI / 万方的检索通道（禁止编造出处）。
 * 代码里按假设实现、论文里必须按假设陈述，并附本类测试给出的可复现统计。
 *
 * <p><b>纯函数</b>：随机性只来自传入的 {@link RandomGenerator}。
 */
final class TimeDistribution {

    /**
     * 星期乘子，索引 0=周一 … 6=周日。**假设值**。
     *
     * <p>形状依据只有一条常识：三级医院门诊量与工作日强相关，周一最高、周末最低。
     * 具体数值没有出处，故取一组单调递减、周末约降到工作日三分之一的数。
     */
    static final double[] WEEKDAY_MULTIPLIER = {1.35, 1.20, 1.10, 1.05, 0.95, 0.45, 0.35};

    /** 门诊开诊小时。 */
    static final int FIRST_HOUR = 8;

    /**
     * 日内到达分布（分段常数，索引 0 = 08:00-09:00 … 9 = 17:00-18:00）。**假设值**。
     *
     * <p>形状：上午两段最高（开诊后的集中到达），午休（12~13 点）最低，下午再起一个小峰。
     * 合计 9.05，前四段（8~11 点）占 6.00，即约 66% 的患者在上午到达。
     */
    static final double[] HOUR_WEIGHT = {1.60, 1.80, 1.50, 1.10, 0.45, 0.35, 0.60, 0.75, 0.55, 0.35};

    private TimeDistribution() {
    }

    static double weekdayMultiplier(DayOfWeek dow) {
        return WEEKDAY_MULTIPLIER[dow.getValue() - 1];
    }

    /**
     * 非紧急就诊的礼拜推迟：落在周末时，以 {@code weekdayMultiplier(该日)} 的概率**照常就诊**，
     * 否则推到**下一个周一**（时间部分不变）。
     *
     * <p><b>为什么是「推迟」而不是「按乘子重分布到偏好日」</b>：事件日历产出的是间隔序列，
     * 重分布会把随访间隔从对数正态改成别的形状，中位数那条出口判据跟着漂。
     * 推迟的位移有界（周六 +2 天、周日 +1 天），间隔分布形状不变，而「周一峰、周末谷」照样出现。
     *
     * <p><b>急性就诊不要调用本方法</b>——急症不会因为周末就不去。
     */
    static LocalDateTime deferToWorkday(LocalDateTime t, RandomGenerator rnd) {
        DayOfWeek dow = t.getDayOfWeek();
        // 推迟只针对周末：`WEEKDAY_MULTIPLIER` 同时充当「选日的权重」，周五那格略低于 1.0
        // 是为了保留「周五门诊量略降」的权重效果，**不是**「周五有 5% 概率被推到下周一」。
        // 所以这里先按星期几显式分流，再用乘子作「照常就诊」的概率。
        if (dow != DayOfWeek.SATURDAY && dow != DayOfWeek.SUNDAY) {
            return t;
        }
        if (rnd.nextDouble() < weekdayMultiplier(dow)) {
            return t;
        }
        // 周六 getValue()=6 → +2 天；周日 =7 → +1 天
        return t.plusDays(8 - dow.getValue());
    }

    /**
     * 把「哪一天」变成「几点几分」，**日期不变**。
     *
     * <p>分钟与秒独立取，避免整点堆积——旧数据里所有就诊都落在整分钟上，
     * 是按小时看分布时看不出来、按分钟看分布时一眼假的痕迹。
     */
    static LocalDateTime placeAtArrivalHour(LocalDateTime day, RandomGenerator rnd) {
        int hour = FIRST_HOUR + Weighted.pickIndex(HOUR_WEIGHT, rnd);
        return day.toLocalDate().atTime(hour, rnd.nextInt(0, 60), rnd.nextInt(0, 60));
    }
}
```

- [ ] **Step 4: 新建 `ClinicTimeline.java`**

```java
package com.his.generator;

import java.time.LocalDateTime;
import java.util.random.RandomGenerator;

/**
 * 一次门诊从进院到取药的时间骨架。
 *
 * <p>把「环节耗时」写成一条级联，而不是像旧实现那样给每个字段各配一个魔法偏移
 * （`charge = visitTime + 125~155min`、`dispense = visitTime + 165~205min`）：
 * 那些数字其实隐含了「挂号 10 + 候诊 30 + 面诊 5 + …」这条链，
 * 但隐含在四个互不相干的 `rnd.nextInt` 里，**改一处不改其余三处也不会报错**，
 * 只会让「收费早于开方」这类倒挂偶尔出现。
 *
 * <p>各环节耗时均来自 spec §5.3 的「环节耗时」行，该项标注为**需逐条核实**、
 * 本项目无检索通道，故按**假设值**使用，论文中按假设陈述。
 *
 * @param visitTime    挂号（患者到达）
 * @param diagnosedAt  面诊结束、下诊断
 * @param prescribedAt 开出处方
 * @param testedAt     检验出报告
 * @param chargedAt    缴费
 * @param dispensedAt  取药
 */
record ClinicTimeline(LocalDateTime visitTime, LocalDateTime diagnosedAt,
                      LocalDateTime prescribedAt, LocalDateTime testedAt,
                      LocalDateTime chargedAt, LocalDateTime dispensedAt) {

    /** 挂号耗时（分钟）。**假设值**。 */
    static final int REGISTRATION_MINUTES = 10;
    /** 候诊耗时（分钟）。**假设值**。 */
    static final int WAIT_MINUTES = 30;
    /** 面诊耗时（分钟）。**假设值**。 */
    static final int CONSULT_MINUTES = 5;

    /**
     * 级联一次门诊的全部时刻。
     *
     * <p>分支关系：诊断 → 开方之后，**检验与缴费是并行的两条支路**
     * （先缴费后采样、还是先采样后缴费，本模型不区分），取药**必须在缴费之后**。
     * 这几条先后关系由 {@code ClinicTimelineTest} 钉住，也是在「时间线按 time 升序」
     * 之外唯一需要保证的次序。
     */
    static ClinicTimeline of(LocalDateTime visitTime, RandomGenerator rnd) {
        LocalDateTime diagnosedAt = visitTime.plusMinutes(
                REGISTRATION_MINUTES + WAIT_MINUTES + CONSULT_MINUTES);
        LocalDateTime prescribedAt = diagnosedAt.plusMinutes(rnd.nextInt(2, 6));
        LocalDateTime testedAt = prescribedAt.plusMinutes(rnd.nextInt(20, 90));
        LocalDateTime chargedAt = prescribedAt.plusMinutes(rnd.nextInt(55, 86));
        LocalDateTime dispensedAt = chargedAt.plusMinutes(rnd.nextInt(10, 21));
        return new ClinicTimeline(visitTime, diagnosedAt, prescribedAt, testedAt, chargedAt, dispensedAt);
    }
}
```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd backend && mvn -o -pl his-generator -Dtest='TimeDistributionTest,ClinicTimelineTest' test
```

Expected: `TimeDistributionTest` Tests run: 6；`ClinicTimelineTest` Tests run: 4，全部通过。

> 若 `the_whole_visit_fits_in_one_day` 红了，说明级联的总跨度超过了「8:00 到次日 0:00」的
> 16 小时——先看是不是把某个 `rnd.nextInt(a, b)` 的上界写大了。**不要**改成
> `assertThat(...).isBefore(visit.plusDays(1))` 把断言放宽：那等于放弃这条保护。

- [ ] **Step 6: Commit**

```bash
git add backend/his-generator/src/main/java/com/his/generator/TimeDistribution.java \
        backend/his-generator/src/main/java/com/his/generator/ClinicTimeline.java \
        backend/his-generator/src/test/java/com/his/generator/TimeDistributionTest.java \
        backend/his-generator/src/test/java/com/his/generator/ClinicTimelineTest.java
git commit -m "feat(generator): 时间分布纯函数 —— 星期推迟、日内到达、环节耗时级联" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

> **本任务不接线**：`TimeDistribution` / `ClinicTimeline` 在这一步还没有调用方，接线在 Task 20。
> 与 Task 15 同理——把「时间层算得对不对」和「生成的记录对不对」分成两次提交。

---

## Task 20: 生成器接时间分布（阶段三）

**Files:**
- Modify: `backend/his-generator/src/main/java/com/his/generator/EventCalendar.java`
  （慢病随访与年度复查的日期过一遍 `deferToWorkday`）
- Modify: `backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java`
  （`newPendingVisits` 放置到达时刻；`flushPatients` 的处方/检验/收费/发药时刻改用 `ClinicTimeline`）
- Test: `backend/his-generator/src/test/java/com/his/generator/EventCalendarTest.java`
- Test: `backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java`

**Interfaces:**
- Consumes：Task 19 的 `TimeDistribution` / `ClinicTimeline`
- Produces：生成器产出的时间字段全部来自「日历定日 → 分布定时刻 → 级联定环节」这条链，
  不再有 `visitTime.plusMinutes(125, 155)` 这类散落的魔法偏移

**接线分两处，因为两处的输入不同**：
`EventCalendar` 拿到的是**事件的日期**，它决定「哪一天」——所以星期推迟在日历里做；
`DataGeneratorService` 拿到的是**那一天的 0 点**，它在 `newPendingVisits` 里定到达时刻，
再在 `flushPatients` 里用 `ClinicTimeline` 级联出其余时刻。

**急性事件不过 `deferToWorkday`**：`EventCalendar` 里按 `PatientEvent.Kind` 分流，
只有 `FOLLOW_UP` 与 `ROUTINE` 走推迟。这条分流写成测试，
因为「所有事件都推迟」和「都不推迟」两种错法在数据上都只是分布略有偏移，肉眼看不出来。

- [ ] **Step 1: 改 `EventCalendarTest`（写失败测试）**

在 `EventCalendarTest` 里新增：

```java
    @Test
    @DisplayName("急症不因周末而推迟：急性事件的星期分布与自然日一致")
    void acute_events_are_never_deferred_to_monday() {
        List<Disease> acute = List.of(seasonal("J06.9", "上呼吸道感染", 4.0, true));
        PatientProfile profile = new PatientProfile(1L, 30, 0, List.of(), 1.0, BigDecimal.ONE);
        RandomGenerator r = RandomGeneratorFactory.of("L64X128MixRandom").create(7L);

        List<PatientEvent> events = EventCalendar.build(profile, acute,
                LocalDateTime.of(1990, 1, 1, 0, 0),
                LocalDateTime.of(2024, 1, 1, 0, 0),
                LocalDateTime.of(2026, 1, 1, 0, 0), r);

        assertThat(events).isNotEmpty();
        long mondays = events.stream()
                .filter(e -> e.time().getDayOfWeek() == DayOfWeek.MONDAY).count();
        // 全部事件都是急性；若被无条件推迟，周一占比会接近 1.0
        assertThat((double) mondays / events.size()).isLessThan(0.5);
    }
```

> `seasonal(...)` 用文件里**已有**的那份（`EventCalendarTest` 顶部那个 4 参 helper：
> `seasonal(String icd, String name, double weight, boolean winterHigh)`）。
> 这份测试数据只用来验「急性事件不被推迟」，季节形状与科室取值都无所谓，
> 所以**不要**为此新写 helper，也**不要**引入 `JANUARY_HEAVY` 这类文件中不存在的常量。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-generator -Dtest=EventCalendarTest test
```

Expected: 编译失败（`deferToWorkday` 还没被调用，测试本身能编过）→ 实际为
**测试通过**（当前所有事件都不推迟，周一占比自然小于 0.5）。
这一步的正确预期是「测试先绿，但它测的还不是新行为」——因此下一步先接线，再回来确认它仍然绿。

> 这是 TDD 的一个例外，如实记下：这条测试是**回归保护**（保护「别把急性事件也推迟了」），
> 它无法先红——因为「不推迟」正是当前行为。真正的失败测试在 Task 19 已经写过。

- [ ] **Step 3: `EventCalendar` 接线**

在 `build` 的循环里，把「决定事件时间」之后加一步：

```java
            // 非紧急就诊才做礼拜推迟；急性就诊不去动它（急症不会因为周末就不去）
            LocalDateTime when = kind == PatientEvent.Kind.ACUTE
                    ? next
                    : TimeDistribution.deferToWorkday(next, rnd);
            if (when.isBefore(start)) {
                when = start;       // 推迟只会往后走，但起点在窗口之外时仍要守住下界
            }
            if (!when.isBefore(windowEnd)) {
                break;              // 推到窗口外就等于这一个事件不存在，终止而不是钳回窗口内
            }
            events.add(new PatientEvent(when, disease, kind));
```

并把 `next = next.plusMinutes(nextIntervalMinutes(kind, profile, rnd));` 改成
**以推迟前的 `next` 为基准**推进（不是 `when`）——用 `when` 会累积推迟量，
把随访间隔的中位数一步步推高：

```java
            next = next.plusMinutes(nextIntervalMinutes(kind, profile, rnd));
```

> 即：`next` 是日历的「计划时间」，`when` 是「实际发生时间」，两者分离。
> 这一点必须写进注释——把 `when` 当成新的基准是这一处最容易犯的错，
> 而症状（随访间隔中位数偏高）只会在 Task 22 的实测里才显形。

- [ ] **Step 4: `DataGeneratorService` 接线**

**(a) `newPendingVisits`**：`EventCalendar.build` 返回的事件里，
`e.time()` 现在是「那一天的 0 点」还是「已经带时刻」取决于 Task 15 的实现——
Task 15 的 `next` 由 `start.plusMinutes(...)` 推出，**本身带时刻**。
所以本步只需在**没有事件**的兜底路径上补一次到达时刻；否则直接用 `e.time()`。

`EventCalendar.build` 若返回空列表（患者一条事件都没有），当前代码会让该患者一次就诊都没有——
Task 16 已为此加了「池非空且窗口 ≥ 2 分钟 ⇒ 至少 1 个事件」的保证，故此处不另设兜底。

把 `v.setVisitTime(e.time());` 改为：

```java
            // 日历给出的是「计划时刻」，到达时刻再按日内分布重放一次
            //（保留日期、重取小时与分钟）——日历的间隔是「天」量级的，
            // 时刻部分由分布决定，两者互不干扰
            v.setVisitTime(TimeDistribution.placeAtArrivalHour(e.time(), rnd));
```

**(b) `flushPatients`**：`ClinicTimeline` 替代散落的偏移。在门诊段构造诊断/处方时插入：

```java
        List<ClinicTimeline> timelineBuf = new ArrayList<>(pending.size());
        for (PendingVisit pv : pending) {
            ClinicTimeline tl = ClinicTimeline.of(pv.visit().getVisitTime(), rnd);
            timelineBuf.add(tl);
            Visit v = pv.visit();
            Disease disease = pv.disease();
            ...（原样保留，只把两个 set 改掉）
            d.setDiagnosedAt(tl.diagnosedAt());
            ...
            pr.setPrescribedAt(tl.prescribedAt());
        }
```

`newLabTest` 里的 `lt.setTestedAt(pr.getPrescribedAt().plusMinutes(rnd.nextInt(20, 90)));`
改为直接取该次就诊的 `ClinicTimeline.testedAt()`——所以 `newLabTest` 要多收一个
`LocalDateTime testedAt` 参数（`newLabTests(Disease, Prescription)` →
`newLabTests(Disease, Prescription, ClinicTimeline)`）。

收费与发药同理：

```java
        c.setChargedAt(tl.chargedAt());
        disp.setDispensedAt(tl.dispensedAt());
```

> 需要用 `visitId -> ClinicTimeline` 的映射找出每次就诊的时间骨架，
> 与既有的 `visitTimeById` 同一处构建：把 `Map<Long, LocalDateTime> visitTimeById`
> 换成 `Map<Long, ClinicTimeline> timelineByVisitId`，`visitTimeById` 的其余用途
> 从 `tl.visitTime()` 取。**`visitTimeById` 若在替换后不再被引用，一并删除。**

- [ ] **Step 5: 跑全部生成器测试**

```bash
cd backend && mvn -o -pl his-generator test
```

Expected: BUILD SUCCESS。

若 `visit_doctor_matches_its_department` 或时间链断言红了，先确认
`timelineByVisitId` 是按 `visit.id` 取的（不是按 `patientId`）——
按错了会让不同就诊共用同一套时刻，症状是「同患者多次就诊的时刻一模一样」。

- [ ] **Step 6: Commit**

```bash
git add backend/his-generator/src/main/java/com/his/generator/EventCalendar.java \
        backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java \
        backend/his-generator/src/test/java/com/his/generator/EventCalendarTest.java \
        backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java
git commit -m "feat(generator): 就诊时刻按星期/日内分布与环节耗时级联生成" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 21: 看板显示本体一致率与覆盖率（阶段三）

**Files:**
- Modify: `frontend/src/views/ValidationReport.vue`
- Modify: `frontend/src/api/validation.js`（仅在需要新接口时）
- Test: `backend/his-server/src/test/java/com/his/server/controller/ValidationControllerTest.java`

**Interfaces:**
- Consumes：Task 11 的 `OntologyReport`（`checked` / `violations` / `unchecked` / `consistentRate` /
  `byType` / `details`）、Task 10 的 `RationalDrugReport.unchecked`、Task 12 的
  `ValidationReport.ontology`
- Produces：前端「数据质量校验报告」页新增两张指标卡与一张**按 `byType` 实际键渲染**的明细表
  （`TYPE_LABELS` 里写了八类的标签，那是**全集**；阶段一~三的 `CHECK_TYPES` 只有 4 类，
  故此刻响应里只有 4 个键。**不要为了凑满八行去造数据**——手术四类在 Task 24 由校验器自己补齐）

**为什么覆盖率必须单独占一张卡，而不是并进一致率**：`consistentRate` 是
`(checked - violations) / checked`，分母里**不含** `unchecked`。所以
「一致率 100%」与「覆盖率 0%」可以同时成立——那正是 spec §12.2 要防的事：
改造后的 0 必须是「检查过且没问题」，不是「没检查」。
两张卡并排放在第一行，谁看一眼都会同时看到。

**`byType` 在第一到三阶段只有 4 个键**（门诊四类；手术四类在 Task 24 补齐）。
前端**不要**写死 8 行——按 `byType` 的实际键渲染，缺失的补 0 在 Task 24 会自己出现。

- [ ] **Step 1: 写失败的测试**

`backend/his-server/src/test/java/com/his/server/controller/ValidationControllerTest.java`
（若已存在则在其上追加）：

```java
    @Test
    void run_returns_ontology_report_in_the_envelope() throws Exception {
        OntologyReport on = new OntologyReport();
        on.setChecked(100);
        on.setViolations(0);
        on.setUnchecked(3);
        on.setConsistentRate(1.0);
        on.setByType(Map.of("DEPT_DIAGNOSIS", 0L, "AGE_GENDER", 0L,
                "LAB_DIAGNOSIS", 0L, "ADMISSION", 0L));
        on.setDetails(List.of());
        ValidationReport report = new ValidationReport();
        report.setOntology(on);
        when(validationService.run()).thenReturn(report);

        mockMvc.perform(post("/api/validation/run"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.ontology.checked").value(100))
                .andExpect(jsonPath("$.data.ontology.unchecked").value(3))
                .andExpect(jsonPath("$.data.ontology.byType.DEPT_DIAGNOSIS").value(0));
    }
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-server -am test \
  -Dtest=ValidationControllerTest -Dsurefire.failIfNoSpecifiedTests=false
```

Expected: 若控制器返回的 `ValidationReport` 尚未序列化 `ontology`，断言失败；
否则先绿（说明控制器无需改动，本任务是纯前端改动）——两种结果都可接受，
**只要它确实断言了 `ontology` 的三个字段**。

- [ ] **Step 3: 改 `ValidationReport.vue`**

第一行指标卡改为四张（新增两张本体卡）：

```vue
    <el-row v-if="report" :gutter="16" class="metric-row">
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="never">
          <div class="metric">
            <div class="metric-value">{{ pct(report.ontology?.consistentRate) }}</div>
            <div class="metric-label">本体一致率</div>
            <div class="metric-sub muted">
              已判定 {{ report.ontology?.checked ?? 0 }} 条
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="never">
          <div class="metric">
            <div class="metric-value">{{ coveragePct }}</div>
            <div class="metric-label">本体覆盖率</div>
            <div class="metric-sub muted">
              未判定 {{ report.ontology?.unchecked ?? 0 }} 条
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="never">
          <div class="metric">
            <div class="metric-value">{{ pct(report.rationalDrug?.rationalRate) }}</div>
            <div class="metric-label">合理用药率</div>
            <div class="metric-sub muted">
              未判定 {{ report.rationalDrug?.unchecked ?? 0 }} 条
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="never">
          <div class="metric">
            <div class="metric-value">{{ report.rationalDrug?.violationPrescriptions ?? 0 }}</div>
            <div class="metric-label">违规处方数</div>
          </div>
        </el-card>
      </el-col>
    </el-row>
```

并在脚本里加：

```js
// 覆盖率 = 已判定 / (已判定 + 未判定)。分母为 0 时返回 '-' 而不是 100%——
// 「一条都没检查」不该显示成一个漂亮的满分。
const coveragePct = computed(() => {
  const checked = Number(report.value?.ontology?.checked ?? 0)
  const unchecked = Number(report.value?.ontology?.unchecked ?? 0)
  const total = checked + unchecked
  return total === 0 ? '-' : ((checked / total) * 100).toFixed(1) + '%'
})
```

补 `import { computed } from 'vue'`。

紧接着「合理用药违规明细」之前，插入八类明细表：

```vue
    <el-card v-if="report" shadow="never" style="margin-top: 16px">
      <template #header><span class="card-title">本体一致性违规（按类型）</span></template>
      <el-table :data="ontologyRows" size="small" border>
        <el-table-column prop="type" label="违规类型" width="200" />
        <el-table-column prop="count" label="条数" width="100" />
        <el-table-column prop="label" label="含义" min-width="240" />
      </el-table>
    </el-card>
```

```js
// 按 byType 的**实际键**渲染，不写死 8 行：手术四类在阶段四才出现，
// 写死会让前三阶段的两张表里挂着四行恒为 0 的行——看起来像「检查了但没问题」。
const TYPE_LABELS = {
  DEPT_DIAGNOSIS: '诊断与就诊科室不符',
  AGE_GENDER: '诊断不适用于该年龄/性别',
  LAB_DIAGNOSIS: '检验项目不在该诊断的清单里',
  ADMISSION: '住院诊断不可住院 / 不延续门诊诊断',
  OPERATION_DIAGNOSIS: '手术不在该诊断的手术清单里',
  OPERATION_TIMESPAN: '手术时间超出住院区间',
  SURGEON_DEPT: '术者不属于该住院科室',
  POSTOP_DAYS: '术后住院天数不足',
}
const ontologyRows = computed(() =>
  Object.entries(report.value?.ontology?.byType || {})
    .map(([type, count]) => ({ type, count, label: TYPE_LABELS[type] || '' }))
    .sort((a, b) => b.count - a.count || a.type.localeCompare(b.type))
)
```

- [ ] **Step 4: 构建前端确认无语法错误**

```bash
cd frontend && npm run build
```

Expected: 构建成功。

- [ ] **Step 5: 手工核对一次真实接口**

```bash
cd backend && mvn -pl his-server -am package -DskipTests
# 启动后端后：
curl -s -X POST http://localhost:8080/api/validation/run | head -c 400
```

Expected: `{"code":0,...,"data":{"ontology":{"checked":...,"unchecked":...,"consistentRate":...}}}`。
把 `checked` / `unchecked` / `consistentRate` 三个数抄进 `docs/test-report.md` §7.1
（**这就是阶段三判据「看板显示本体一致率与覆盖率」的实测证据**）。
若 `checked` 为 0，说明本体表是空的——回到 Task 18 Step 3 的迁移检查，**不要**只截图交差。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/ValidationReport.vue frontend/src/api/validation.js \
        backend/his-server/src/test/java/com/his/server/controller/ValidationControllerTest.java \
        docs/test-report.md
git commit -m "feat(frontend): 看板显示本体一致率与覆盖率，违规明细按 byType 渲染" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 22: 改造前后基线反测 + 测试报告与论文素材（阶段三）

**Files:**
- Create: `backend/sql/baseline-dataset.sql`（快照，`mysqldump` 产物，**纳入版本控制**）
- Create: `backend/sql/verify-baseline.sql`（对快照的统计查询）
- Modify: `docs/test-report.md`（新增 §8：改造前后对比）
- Modify: `docs/references/README.md`（论文素材清单的完成状态）

**Interfaces:**
- Consumes：Task 18 的 `verify-phase2.sql`；Task 11/10 的两个校验器；改造前的提交
- Produces：`docs/test-report.md` §8 的改造前后对比表——**这是论文的核心数字**

**为什么基线必须用「改造后的校验器」去反测**（spec §12.1 明确要求）：
旧校验器的适应症检查只覆盖 `rule_config` 里的 8 个诊断，
用它跑出来的违规率与改造后的口径不可比。用同一套判据测两个数据集，
「改造前 87% → 改造后 0%」才是可陈述的对比。

**基线数据集的来源**：改造前的代码已经在 git 里（Task 16 之前的那次提交），
所以基线**可以随时重建**，不需要提前留存。这是本计划把「留存快照」放在阶段三
而不是改造前的原因——只要提交还在，快照就重建得出来。**但前提是 `main` 历史不被改写**。

- [ ] **Step 1: 定位改造前的提交**

```bash
git log --oneline --all | head -30
# 找到 Task 15 完成、Task 16 尚未开始的那次提交，记为 BASE_COMMIT
git log --oneline -1 <BASE_COMMIT>
```

把 `BASE_COMMIT` 记进 `docs/test-report.md` §8——
**不写提交号的基线数字日后无法复现**，而没有提交号的数字在论文里是不可引用的。

- [ ] **Step 2: 在独立 worktree 里重建基线数据集**

**先备份当前库**（基线跑完要恢复）：

```bash
mysqldump -uhis_user -p'his@2026' --default-character-set=utf8mb4 his_demo > /tmp/his-after.sql
```

再检出基线代码并生成：

```bash
git worktree add ../his-baseline <BASE_COMMIT>
cd ../his-baseline/backend && mvn -q -pl his-generator -am install -DskipTests
java -jar his-generator/target/his-generator-1.0.0.jar
```

> **注意**：基线代码里没有 Task 1 的新表，`TRUNCATE` 也不会碰它们——
> 所以 `dept_diagnosis` 等字典表**保持现状**，而基线生成器**根本不读它们**。
> 这正是我们要的：基线数据 = 旧算法 + 旧字典，与改造后数据 = 新算法 + 新字典的对比，
> 差异全部来自算法。

立即导出快照（此刻库里就是基线数据）：

```bash
# 此刻 cwd 是 ../his-baseline/backend，主仓库根是它的 ../../his_demo。
# 用显式相对路径，别写 his_demo/backend/...（那会落到 ../his-baseline/backend/his_demo/... 上）
mysqldump -uhis_user -p'his@2026' --default-character-set=utf8mb4 \
  --no-create-info --complete-insert his_demo patient visit diagnosis prescription \
  prescription_item lab_test charge dispense admission medical_order vital_sign \
  > ../../his_demo/backend/sql/baseline-dataset.sql
```

导出后立刻核对落点与行数（**路径写错时 mysqldump 会「成功」地写到一个新目录里**，
`echo $?` 是 0，只有看文件才看得出来）：

```bash
ls -l ../../his_demo/backend/sql/baseline-dataset.sql
grep -c "^INSERT INTO \`visit\`" ../../his_demo/backend/sql/baseline-dataset.sql
```

- [ ] **Step 3: 用新校验器反测基线快照**

恢复「改造后」的代码但**不恢复数据**——库里的基线数据保持不动：

```bash
cd his_demo && git worktree remove ../his-baseline
cd backend && mvn -pl his-server -am package -DskipTests
```

启动后端（走的是**新**校验器、读的是**基线**数据），取报告：

```bash
curl -s -X POST http://localhost:8080/api/validation/run > /tmp/baseline-report.json
python -c "
import json;d=json.load(open('/tmp/baseline-report.json'))['data']
o=d['ontology'];r=d['rationalDrug']
print('ontology  checked=%s violations=%s unchecked=%s rate=%s'%(o['checked'],o['violations'],o['unchecked'],o['consistentRate']))
print('byType   ',o['byType'])
print('rational  total=%s violations=%s unchecked=%s rate=%s'%(r['totalPrescriptions'],r['violationPrescriptions'],r['unchecked'],r['rationalRate']))
"
```

**这些数字就是论文里的「改造前」一列。** 一个都不许估。

- [ ] **Step 4: 建 `verify-baseline.sql` 并在**数据仍是基线**时跑掉它**

`§8` 的表里有两行（同患者重复就诊率、慢病随访间隔中位数）**不是校验器能给的**——
它们出自 SQL 聚合，而 Step 5 就把库恢复成改造后的了。
所以这一步必须**排在恢复之前**，否则那两个格子只剩「未实测」可填。

新建 `backend/sql/verify-baseline.sql`，**查询体与 `verify-phase2.sql` 的 §2.1 / §2.2 逐字相同**
（口径必须一致，否则「改造前 vs 改造后」比的是两把尺子）：

```sql
-- 基线数据集（改造前）的轨迹指标，供 docs/test-report.md §8 的「改造前」一列使用。
-- 只在「库里是基线数据」的那段时间内执行一次。
-- 口径与 verify-phase2.sql §2.1 / §2.2 完全相同，以便与改造后逐格对比。
-- 用法：mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -t < verify-baseline.sql

SELECT '=== B.1 同患者重复就诊率（口径同 verify-phase2.sql §2.1）===' AS section;
SELECT COUNT(*) AS patients_with_visits,
       SUM(repeat_kinds > 0) AS patients_with_repeat,
       ROUND(100.0 * SUM(repeat_kinds > 0) / COUNT(*), 2) AS repeat_rate_pct,
       SUM(kinds) AS distinct_patient_diagnosis_pairs
FROM (SELECT patient_id, COUNT(*) AS kinds, SUM(c >= 2) AS repeat_kinds
      FROM (SELECT d.patient_id, d.icd_code, COUNT(*) c
            FROM diagnosis d WHERE d.icd_code IS NOT NULL
            GROUP BY d.patient_id, d.icd_code) x
      GROUP BY patient_id) y;

-- 基线可能一行都取不到：旧生成器的 ICD 来自 rule_config 的 8 条，
-- 它们未必都在 dept_diagnosis 里 —— 那样「慢病」这个筛选条件就无从谈起。
-- 取不到就**如实写「基线口径下不可计算」并把本查询的输出贴进报告**，不许跳过这一格。
SELECT '=== B.2 慢病随访间隔（天）中位数（口径同 verify-phase2.sql §2.2）===' AS section;
SELECT COUNT(*) AS intervals,
       MIN(gap) AS min_gap, MAX(gap) AS max_gap,
       ROUND(AVG(gap), 1) AS mean_gap,
       ROUND(AVG(CASE WHEN rn IN (FLOOR((n + 1) / 2), CEIL((n + 1) / 2)) THEN gap END), 1) AS median_gap,
       ROUND(AVG(CASE WHEN rn IN (FLOOR(n * 0.25), CEIL(n * 0.25)) THEN gap END), 1) AS p25_gap,
       ROUND(AVG(CASE WHEN rn IN (FLOOR(n * 0.75), CEIL(n * 0.75)) THEN gap END), 1) AS p75_gap
FROM (SELECT gap,
             ROW_NUMBER() OVER (ORDER BY gap) AS rn,
             COUNT(*) OVER () AS n
      FROM (SELECT d.patient_id, d.icd_code,
                   TIMESTAMPDIFF(DAY,
                       LAG(v.visit_time) OVER (PARTITION BY d.patient_id, d.icd_code
                                               ORDER BY v.visit_time),
                       v.visit_time) AS gap
            FROM diagnosis d
            JOIN visit v ON v.id = d.visit_id
            WHERE EXISTS (SELECT 1 FROM dept_diagnosis dg
                           WHERE dg.icd_code = d.icd_code AND dg.chronic = 1)) g
      WHERE gap IS NOT NULL AND gap > 0) t;
```

```bash
mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -t \
  < backend/sql/verify-baseline.sql
```

把这条命令的**原样输出**贴进 `docs/test-report.md` §8 下方，并把两个数填进「改造前」列。

- [ ] **Step 5: 恢复改造后的数据**

```bash
mysql -uhis_user -p'his@2026' --default-character-set=utf8mb4 his_demo < /tmp/his-after.sql
```

确认恢复成功（行数与备份前一致），否则后续所有实测都建立在基线数据上：

```bash
mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -e "
SELECT 'visit' t, COUNT(*) c FROM visit UNION ALL SELECT 'admission', COUNT(*) FROM admission;"
```

- [ ] **Step 6: 写 `docs/test-report.md` §8**

```markdown
## 8. 改造前后量化对比（论文核心数字）

**基线提交**：`<BASE_COMMIT>`（改造前最后一次提交）
**基线数据集**：`backend/sql/baseline-dataset.sql`（可由该提交重建）
**对比口径**：**同一套改造后的校验器**分别跑两个数据集。不用旧校验器——
它的适应症检查只覆盖 8 个诊断，口径不可比（spec §12.1）。

| 指标 | 改造前（基线） | 改造后 | 判据 |
|---|---|---|---|
| 本体一致性已判定条数 | （实测） | （实测） | — |
| 本体一致性违规数 | （实测） | （实测） | 改造后 = 0 |
| 本体一致性未判定数 | （实测） | （实测） | 改造后 = 0 |
| 本体一致率 | （实测） | （实测） | 改造后 = 100% |
| 本体覆盖率 = 已判定/(已判定+未判定) | （实测） | （实测） | 改造后 = 100% |
| 合理用药违规处方数 | （实测） | （实测） | 改造后 = 0 |
| 合理用药未判定条数 | （实测） | （实测） | 改造后 = 0 |
| 按类型违规明细（byType） | （实测） | （实测） | 改造后各项均为 0 |
| 同患者重复就诊率 | （实测） | （实测） | 改造后 > 0 |
| 慢病随访间隔中位数（天） | （实测） | （实测） | 改造后 ≈ 91 |

**复现命令**：
- **改造后**一列 = `verify-phase2.sql` + `POST /api/validation/run`，命令见 §7.0；
- 「改造前」一列 = 本任务 Step 1–5（worktree 重建 → 反测 → `verify-baseline.sql` → 恢复），
  三条命令按 Step 顺序原样记在本节上方，**连同 `BASE_COMMIT` 一起**；
- 下面这张表的每一格都要能追到**某一条具体命令的输出**。填不出处的格子写「未实测」+ 命令，不许填估算值。

（若某一格确实没测，写「未实测」并附命令，**不得填估算值**。）
```

- [ ] **Step 7: 整理论文素材清单**

在 `docs/references/README.md` 末尾新增一节，逐条列出 spec §12 的八项论文产出
及其**当前状态**（已完成 / 待实测 / 无法完成及原因）：

```markdown
## 九、论文产出清单（spec §12 的对应关系）

| spec §12 条目 | 素材位置 | 状态 |
|---|---|---|
| 1 改造前后量化对比 | `docs/test-report.md` §8 | （实测后填） |
| 2 本体覆盖率 | `docs/test-report.md` §7.1、§8 | （实测后填） |
| 3 校验器检出能力 | 正例测试用例名 + 人工录入演示记录 | 正例测试已随代码提交；演示记录待补 |
| 4 参数来源表 | spec §5.3 + 本文件 §五 | 已完成；其中三项标「需逐条核实」须保留为「假设」 |
| 5 本体规模统计 | `backend/sql/verify-phase2.sql` §2.0 | 实测 |
| 6 局限与未来工作 | spec §1.2 + §3.2 | 已完成 |
| 7 方法论参照章节 | 本文件 §一~§四 | 已完成；**3 篇付费墙文献未读，不得据以陈述机制** |
| 8 住院与手术域完整性论证 | `docs/test-report.md` §7.2、§9 | 阶段四完成后填 |
```

- [ ] **Step 8: Commit**

```bash
git add backend/sql/baseline-dataset.sql backend/sql/verify-baseline.sql \
        docs/test-report.md docs/references/README.md
git commit -m "docs: 改造前后基线反测数据落表，论文素材清单对齐 spec §12" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

**阶段三完成标志**：看板的两张本体指标卡显示实测值；`docs/test-report.md` §8 的
「改造前」「改造后」两列都是实测值（或明确写着「未实测」+ 命令）。

---

## Task 23: 手术域生成 —— 术式抽取、出院时间修正、术前检查与术后医嘱（阶段四）

**Files:**
- Modify: `backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java`
  （`generateInpatientEvents` 重排顺序并追加 `List<Operation> operationBuf` 形参；新增 `newOperation` /
  `operationMinutes` / `pickOther` / `preopLabs` / `postopOrders` / `addNursingOrders` /
  `totalProbability`；`pickDrugRows` 加列表重载；`dischargeDisposition` 加两个参数；
  字段区末尾追加 `OperationMapper operationMapper`；新增字段
  `Set<String> lastAdmissionNos`；`rnd` 的类型由 `ThreadLocalRandom` 改为 `RandomGenerator`
  并与 `dischargeDisposition` 一起放开到包级可见，作为 `DEATH` 分支的测试缝）
- Test: `backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java`

**Interfaces:**
- Consumes：Task 13 的 `OntologyRepository.operationsOf(Disease)`（**已存在，本任务不新增仓库方法**）、
  `drugsOf` / `labsOf` / `doseOf`；Task 16 的 `Weighted.pickIndex` / `pickDrugRows`；
  Task 17 的 `generateInpatientEvents` / `newInpatientOrder` / `newInpatientLabTestFromRow` /
  Task 1 的 `operation` 表与 `diagnosis_drug.postop` 列
- Produces（Task 24/26 依赖）：
  - `generate()` 的返回 map 新增键 `operations`（产出的手术条数）、`postop_missing`（挂了手术但术后用药本体为空的住院数）
  - `operationBuf` 在 `flushPatients` 里随 `visitBuf` 一起分块入库；`operationCount`
  - `OntologyRepository` **不改**——`operationsOf` 在 Task 13 已建

**为什么出院时间要重算，而不是「先定出院、手术塞进去」**：spec §6.3 的
`出院时间 = max(原出院时间, 手术结束 + min_postop_days)` 是一条**下界修正**。
若先定出院再选手术日，手术日就可能落在出院日之后——而手术日的下界（入院 + 1 天）
和上界（出院 - min_postop_days）在旧住院时长（3~15 天）下**可能无解**：
4 级手术 `min_postop_days=9`，若 totalDays 抽到 3，`[入院+1, 入院+3-9]` 是空区间。
所以只能反过来：**先手术、后出院**。

**抽术式是两步，不是一步**（Task 6 的表的语义）：
`同一 (科室, ICD) 下 probability 之和 = 该诊断这次住院的手术率`，
`weight = 需手术时各术式的相对份额`。
不要用 `probability` 当权重直接抽——那样 `K35.8` 的 0.850/0.150 会被当成 85:15 的份额，
而它本来就该是 85:15（`weight` 3.000/1.000 = 75:25），两个数会打架且没人知道该听谁的。
**分开用**：`sum(probability)` 掷「做不做」，`weight` 掷「做哪个」。

**麻醉医师只能取自同科室**：`doctor` 表没有麻醉科（`seed.sql` 只有 6 个临床科室）。
这不是本任务能修的字典缺口，所以如实处理：麻醉医师从**该住院科室**的医生里取一位非术者，
并在注释里写明这是字典局限。**不得**用 `randomName()` 编一个不在 `doctor` 表里的人——
校验器第 7 类只查术者，编出来的麻醉医师不会被检出，但它是个不存在的人。

- [ ] **Step 1: 写失败的测试**

在 `DataGeneratorServiceTest` 里新增（`op(...)` 等 helper 放在文件末尾的 helper 区）：

**(a) 字段区新增两个**

```java
    /** 本次生成落库的全部手术。取的路径与 `insertedAdmissions` 完全一致。 */
    private final List<Operation> insertedOperations = new ArrayList<>();

    /**
     * 本用例自己建的 service。
     *
     * <p>**它不是 `run()` 里 `buildService()` 出来的临时对象**：本体桩（`stubInpatientDisease`）
     * 必须在 `buildService()` **之后**打，因为 `ontologyRepository` 是那个方法里 `mock(...)`
     * 出来的字段。等桩全打完了，`run()` 才拿这个已经配好的 service 去生成。
     */
    private DataGeneratorService svc;
```

**(b) 测试与 helper**

```java
    /** 一条手术本体行。 */
    private DiagnosisOperation op(String icd, String dept, String code, String name,
                                  int level, double probability, int minPostop, double weight) {
        DiagnosisOperation o = new DiagnosisOperation();
        o.setIcdCode(icd);
        o.setDepartment(dept);
        o.setOperationCode(code);
        o.setOperationName(name);
        o.setOperationLevel(level);
        o.setProbability(BigDecimal.valueOf(probability));
        o.setMinPostopDays(minPostop);
        o.setWeight(BigDecimal.valueOf(weight));
        return o;
    }

    /** 本次生成落库的全部手术。 */
    private List<Operation> operations() {
        return insertedOperations;
    }

    /** 本次生成落库的全部住院。 */
    private List<Admission> admissions() {
        return insertedAdmissions;
    }

    /** 本次生成落库的全部住院医嘱。 */
    private List<MedicalOrder> orders() {
        return insertedOrders;
    }

    /** 本次生成落库的全部检验。 */
    private List<LabTest> labs() {
        return insertedLabs;
    }

    /** 住院 id → 住院单。id 由 `stubBatchInsert` 在插入时回填，所以这里取到的是真 id。 */
    private Map<Long, Admission> admissionsById() {
        return insertedAdmissions.stream()
                .collect(Collectors.toMap(Admission::getId, a -> a));
    }

    /**
     * 某科室的全部医生姓名。**从 `doctorMapper` 的桩读回**（与生成器的取数路径一致），
     * 而不是另抄一份名单——抄一份就等于测「名单没抄错」，不是测「术者来自本科室」。
     */
    private List<String> doctorsOf(String dept) {
        return doctorMapper.selectList(null).stream()
                .filter(d -> dept.equals(d.getDepartment()))
                .map(Doctor::getName)
                .toList();
    }

    /**
     * 把「该诊断可住院、挂给定的术式本体、用药本体为空」装进本体仓库，
     * **并顺带把 service 建好**（`svc` 字段）。
     *
     * <p>**必须先建 service 再打桩**：`ontologyRepository` 是 `buildService()` 里
     * `mock(...)` 出来的字段，service 没建之前它是 `null`，这里每一行 `when(...)` 都会 NPE。
     * 所以本方法内部先 `svc = buildService();`，后面的 `stubDrugs` / `stubRequiredLab`
     * 才有同一个 `ontologyRepository` 可以继续打桩，`run()` 也不再重建。
     *
     * <p>造一个**可住院**的诊断是这套测试的前提：不可住院的诊断根本走不到住院循环，
     * 手术断言会空转成「没有手术也算通过」。所以这里同时桩上 `diseasesFor`（门诊用）与
     * `admissionEligibleDiseasesFor`（住院用）——**只桩前者会让所有手术断言在零住院上通过**。
     */
    private void stubInpatientDisease(String icd, String name, String dept,
                                      List<DiagnosisOperation> ops) {
        stubInpatientDisease(icd, name, dept, ops, List.of());
    }

    private void stubInpatientDisease(String icd, String name, String dept,
                                      List<DiagnosisOperation> ops,
                                      List<DiagnosisDrug> drugs) {
        svc = buildService();
        Disease d = disease(icd, name, dept, 0, 18, 89, false, true, "4.000", 0);
        when(ontologyRepository.diseasesFor(anyInt(), anyInt())).thenReturn(List.of(d));
        when(ontologyRepository.admissionEligibleDiseasesFor(anyInt(), anyInt())).thenReturn(List.of(d));
        when(ontologyRepository.operationsOf(d)).thenReturn(ops);
        when(ontologyRepository.drugsOf(d)).thenReturn(drugs);
        when(ontologyRepository.labsOf(any())).thenReturn(List.of());
        // 剂量行必须给：拿不到剂量时生成器会跳过该条医嘱，术后用药断言会空转成「没有药」
        when(ontologyRepository.doseOf(anyLong())).thenReturn(dose(1L, "1", "片", "每日3次", "4", 5, 7));
    }

    /**
     * 覆盖掉 `stubInpatientDisease` 里那个空用药列表。
     *
     * <p>按 `icd` 过滤而不是整批返回——生产代码的 `drugsOf(disease)` 本来就只返回**该诊断**的行。
     * 整批返回会让「诊断 A 拿到了诊断 B 的药」这种错误在测试里看不出来。
     */
    private void stubDrugs(String icd, DiagnosisDrug... rows) {
        when(ontologyRepository.drugsOf(any())).thenAnswer(inv ->
                List.of(rows).stream().filter(r -> icd.equals(r.getIcdCode())).toList());
    }

    /** 6 参重载：多指定一个 `postop`（1=仅术后使用）。Task 16 的 5 参版本固定 `postop=0`。 */
    private DiagnosisDrug drugRow(String icd, String dept, Long drugId, int line,
                                  double weight, int postop) {
        DiagnosisDrug r = drugRow(icd, dept, drugId, line, String.valueOf(weight));
        r.setPostop(postop);
        return r;
    }

    /** 一条 `required=1` 的检验本体行，覆盖掉 `stubInpatientDisease` 里那个空列表。 */
    private void stubRequiredLab(String icd, String dept, String itemCode, String name) {
        when(ontologyRepository.labsOf(any())).thenAnswer(inv -> {
            Disease d = inv.getArgument(0);
            DiagnosisLab r = lab(d.icdCode(), d.department(), itemCode, "11.0-13.0", "NONE", "1.000", 1);
            r.setItemName(name);
            return List.of(r);
        });
    }

    /** 跑一次生成。200 名患者约 40 人住院，样本够断言用又跑得快。 */
    private void run() {
        svc.generate(200);
    }

    @Test
    @DisplayName("挂了手术本体的诊断必然产出手术；手术落在住院区间内且术后天数达标")
    void operations_stay_inside_the_admission_and_respect_min_postop_days() {
        // 一个可住院、挂 1 个术式（probability=1.0，必然命中）的诊断
        stubInpatientDisease("K35.8", "急性阑尾炎", "外科",
                List.of(op("K35.8", "外科", "47.01", "腹腔镜阑尾切除术", 2, 1.0, 3, 1.0)));
        run();

        assertThat(operations()).isNotEmpty();
        Map<Long, Admission> admissions = admissionsById();
        for (Operation o : operations()) {
            Admission a = admissions.get(o.getAdmissionId());
            assertThat(a).as("手术必须挂在一张存在且已落库的住院单上").isNotNull();
            assertThat(o.getStartTime()).isAfterOrEqualTo(a.getAdmissionTime());
            assertThat(o.getEndTime()).isAfter(o.getStartTime());
            assertThat(o.getEndTime()).isBeforeOrEqualTo(a.getDischargeTime());
            // 出院时间已被修正为 ≥ 手术结束 + min_postop_days
            assertThat(Duration.between(o.getEndTime(), a.getDischargeTime()).toDays())
                    .isGreaterThanOrEqualTo(3L);
        }
    }

    @Test
    @DisplayName("术者与麻醉医师都取自该住院科室的医生，助手不是术者本人")
    void surgeon_comes_from_the_admission_department() {
        stubInpatientDisease("K35.8", "急性阑尾炎", "外科",
                List.of(op("K35.8", "外科", "47.01", "腹腔镜阑尾切除术", 2, 1.0, 3, 1.0)));
        run();

        List<String> surgeons = doctorsOf("外科");
        assertThat(operations()).isNotEmpty();
        for (Operation o : operations()) {
            assertThat(surgeons).contains(o.getSurgeon());
            assertThat(o.getAssistant()).isNotEqualTo(o.getSurgeon());
            if (o.getAnesthesiologist() != null) {
                assertThat(surgeons).contains(o.getAnesthesiologist());
                assertThat(o.getAnesthesiologist()).isNotEqualTo(o.getSurgeon());
            }
        }
    }

    @Test
    @DisplayName("3~4 级手术必须全身麻醉且有麻醉医师；1~2 级不强制")
    void high_level_operations_require_general_anesthesia() {
        stubInpatientDisease("C73", "甲状腺癌", "外科",
                List.of(op("C73", "外科", "06.4", "甲状腺癌根治术", 4, 1.0, 9, 1.0)));
        run();

        assertThat(operations()).isNotEmpty();
        for (Operation o : operations()) {
            assertThat(o.getOperationLevel()).isGreaterThanOrEqualTo(3);
            assertThat(o.getAnesthesiaType()).isEqualTo("全身麻醉");
            assertThat(o.getAnesthesiologist()).isNotBlank();
        }
    }

    @Test
    @DisplayName("没有手术本体的诊断一行 operation 都不产生")
    void diagnoses_without_ontology_produce_no_operations() {
        stubInpatientDisease("I10", "高血压", "内科", List.of());
        run();

        // 这一句是守门的：没有住院时 operations() 必然为空，本用例会退化成恒真式。
        // 内科这条诊断本身可住院，所以确实会产生住院——若这里是空，说明桩打歪了。
        assertThat(admissions()).as("没有住院就不会有手术，本断言会空转").isNotEmpty();
        assertThat(operations()).isEmpty();
    }

    @Test
    @DisplayName("术后医嘱的药品来自 postop=1 的行，且时段在手术结束之后")
    void postop_orders_come_from_the_postop_flag_and_start_after_surgery() {
        stubInpatientDisease("K35.8", "急性阑尾炎", "外科",
                List.of(op("K35.8", "外科", "47.01", "腹腔镜阑尾切除术", 2, 1.0, 3, 1.0)));
        // 两条药：一条 postop=1（术后用），一条 postop=0；术后时段的医嘱只能出现前者
        stubDrugs("K35.8", drugRow("K35.8", "外科", 1L, 1, 1.0, 1),
                drugRow("K35.8", "外科", 2L, 1, 1.0, 0));
        run();

        assertThat(operations()).isNotEmpty();
        Map<Long, Operation> opByAdmission = operations().stream()
                .collect(Collectors.toMap(Operation::getAdmissionId, x -> x));
        List<String> postopDrugs = orders().stream()
                .filter(o -> opByAdmission.containsKey(o.getAdmissionId()))
                .filter(o -> !o.getStartTime().isBefore(opByAdmission.get(o.getAdmissionId()).getEndTime()))
                .filter(o -> o.getDoctor() != null)      // 护理医嘱没有药品，用 doctor 区分不了，故见下
                .map(MedicalOrder::getItemName).toList();
        assertThat(postopDrugs).contains("药品1").doesNotContain("药品2");
    }

    @Test
    @DisplayName("术后护理医嘱的条数多于术前")
    void nursing_orders_are_denser_after_surgery() {
        stubInpatientDisease("K35.8", "急性阑尾炎", "外科",
                List.of(op("K35.8", "外科", "47.01", "腹腔镜阑尾切除术", 2, 1.0, 3, 1.0)));
        run();

        assertThat(operations()).isNotEmpty();
        for (Operation o : operations()) {
            long pre = orders().stream()
                    .filter(x -> x.getAdmissionId().equals(o.getAdmissionId()))
                    .filter(x -> x.getOrderType() == 3)
                    .filter(x -> x.getStartTime().isBefore(o.getStartTime())).count();
            long post = orders().stream()
                    .filter(x -> x.getAdmissionId().equals(o.getAdmissionId()))
                    .filter(x -> x.getOrderType() == 3)
                    .filter(x -> !x.getStartTime().isBefore(o.getEndTime())).count();
            assertThat(post).as("术后护理医嘱应多于术前").isGreaterThan(pre);
        }
    }

    @Test
    @DisplayName("术前 1~2 天存在该诊断 required=1 的检验项")
    void required_preop_labs_exist_before_the_surgery() {
        stubInpatientDisease("K35.8", "急性阑尾炎", "外科",
                List.of(op("K35.8", "外科", "47.01", "腹腔镜阑尾切除术", 2, 1.0, 3, 1.0)));
        stubRequiredLab("K35.8", "外科", "COAG", "凝血功能");
        run();

        assertThat(operations()).isNotEmpty();
        for (Operation o : operations()) {
            boolean found = labs().stream()
                    .filter(l -> o.getAdmissionId().equals(l.getAdmissionId()))
                    .anyMatch(l -> "COAG".equals(l.getItemCode())
                            && l.getTestedAt().isBefore(o.getStartTime()));
            assertThat(found).as("手术 %s 之前必须有 required=1 的凝血功能", o.getOperationCode()).isTrue();
        }
    }

    @Test
    @DisplayName("无手术的住院完全不产生手术，也不因此改变出院时间的算法")
    void admissions_without_surgery_keep_the_plain_stay_length() {
        stubInpatientDisease("I10", "高血压", "内科", List.of());
        run();

        assertThat(operations()).isEmpty();
        assertThat(admissions()).isNotEmpty();
        for (Admission a : admissions()) {
            assertThat(a.getDischargeTime()).isAfter(a.getAdmissionTime());
            assertThat(Duration.between(a.getAdmissionTime(), a.getDischargeTime()).toDays())
                    .isBetween(3L, 16L);      // MIN_STAY_DAYS=3 → 最多 15 天 + 不足 1 天的余量
        }
    }

    /**
     * 只把 `nextDouble()` 钉成 0.0 的随机源，其余方法走 `RandomGenerator` 的默认实现。
     * `0.0 < DEATH_RATE` 恒成立，于是「高龄 + 末次住院」这条通路必然被走到。
     */
    private static final class ZeroDoubleGenerator implements RandomGenerator {
        @Override public long nextLong() { return 0L; }
        @Override public long nextLong(long bound) { return 0L; }
        @Override public double nextDouble() { return 0.0; }
    }

    /** 一位 90 岁患者。`PatientProfile` 的构造需要随机源，这里给个定种子的即可。 */
    private static PatientProfile agedProfile() {
        return PatientProfile.build(1L, 90, 0, List.of(), BigDecimal.ONE,
                RandomGeneratorFactory.of("L64X128MixRandom").create(1L));
    }

    @Test
    @DisplayName("末次住院 + 高龄 + 随机数落在 DEATH_RATE 内 → 出院情况必须是 DEATH")
    void last_admission_of_an_aged_patient_can_return_death() {
        svc = buildService();
        svc.rnd = new ZeroDoubleGenerator();

        assertThat(svc.dischargeDisposition(agedProfile(), true, false, 0)).isEqualTo("DEATH");
    }

    /**
     * 这一条是 {@code isLastAdmission} 那个开关的守门人。
     *
     * <p>把 `lastAdmissionNos` 换成 `lastAdmissionIds`（即 Task 23 的 F-27 那个坑）、
     * 或者干脆去掉 {@code if (isLastAdmission)} 这层，上面那条用例**依然全绿**——
     * 它只证明「高龄 + 末次能死」。只有这一条能证明「非末次不能死」。
     */
    @Test
    @DisplayName("非末次住院哪怕高龄、哪怕随机数落在 DEATH_RATE 内，也绝不写 DEATH")
    void non_last_admission_never_returns_death() {
        svc = buildService();
        svc.rnd = new ZeroDoubleGenerator();

        assertThat(svc.dischargeDisposition(agedProfile(), false, false, 0)).isNotEqualTo("DEATH");
    }
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-generator -Dtest=DataGeneratorServiceTest test
```

Expected: 编译失败——`Operation` / `RandomGenerator` / `RandomGeneratorFactory` 未 import、
`insertedOperations` 未声明、`svc.rnd` 与 `svc.dischargeDisposition(...)` 是 `private` 够不着、
`admissionsById()` / `doctorsOf(...)` / `stubInpatientDisease(...)` / `stubDrugs(...)` /
`stubRequiredLab(...)` 尚未定义（`operations()` / `admissions()` / `orders()` / `labs()` /
`ZeroDoubleGenerator` / `agedProfile()` 在本步一起建）。

> `svc.rnd` 与 `svc.dischargeDisposition(...)` 这两处够不着**不是笔误**：Step 5(e) 会把
> 它们从 `private` 放开到包级可见（测试类与生成器同在 `com.his.generator` 包）。
> 先看到这两条编译错误，才知道那道缝确实没开。

> **动手前先看一眼既有的 helper 区**：`disease(...)` / `drugRow(...)` / `lab(...)` / `dose(...)`
> 是 Task 16 建的，`uriDisease()` 等是 Task 17 建的，**都在同一个文件末尾**。
> 本步新增的 `drugRow(...)` 6 参重载与 `stubRequiredLab(...)` 是**建立在它们之上**的
> （6 参版委托给 5 参版，`stubRequiredLab` 复用 `lab(...)`），不要另写一份字段赋值序列。
> 若发现 `orders()` / `labs()` / `admissions()` 在 Task 16/17 已经建过，
> **直接复用，不要重复定义**——重名的 helper 会让编译报「already defined」，
> 而那正是提醒你「这里已经有一条取数路径了」。缺的才补。

> **`stubInpatientDisease` 内部先 `svc = buildService();` 再打桩，这件事不能调换。**
> `ontologyRepository` 是 `buildService()` 里的 `mock(...)`，service 没建它就是 `null`，
> 那一串 `when(...)` 会全部 NPE。反过来 `run()` **不要再 `buildService()`**——
> 重建会丢掉刚打的桩，而症状是「手术断言全空、但一个错误都不报」：
> 零住院时 `operations()` 就是空列表，`assertThat(operations()).isNotEmpty()` 才会红，
> 而 `isEmpty()` 那一类会静默通过。

- [ ] **Step 3: 改 `generateInpatientEvents` 的顺序**

把 Task 17 版本的 `generateInpatientEvents` 改为（**体征与路径两段逐字保留，
只有「先手术、再定出院」这一处与夹在其中的三处新增**）：

> **签名在本任务由 5 参变 6 参**：末尾追加 `List<Operation> operationBuf`。
>
> **为什么必须加这个参数**：按 Step 5(a)，`operationBuf` 是 `generate()` 的**局部变量**，
> 而本方法体里要写 `operationBuf.add(op);`——**在方法作用域里它不可达，编译不过**。
> （同段的 `operationCount` 是字段、可达；只有 `operationBuf` 会红，所以这个错很容易只修一半。）
>
> **为什么不在签名里再加一个 `PatientProfile profile`**：不需要。`profilesByPatient`
> **是本类的字段**（带初始化器的 `private final Map<Long, PatientProfile> = new HashMap<>()`，
> 所以 Lombok 不会把它算进构造器参数），方法体内直接
> `PatientProfile profile = profilesByPatient.get(a.getPatientId());` 就能拿到。
> 把一个现成的字段再当参数传一遍，等于给调用点新增一处「传错人」的机会，
> 而收益是零。
>
> **追加在末尾而不是插在开头**：插开头会让 Task 17 的调用点里
> `a` 与 `operationBuf` 两个实参互换位置——两者类型不同、换错编译不过，
> 但若有人「修」成 `(operationBuf, a, ...)` 与 `(a, operationBuf, ...)` 各改一半，
> 错误就落在两个相邻参数之间。追加在末尾时，调用点只需在行尾补一个实参，改法唯一。
>
> **调用点（Task 17 Step 3(c) 的循环）同步改为**：
>
> ```java
>         for (Admission a : admissionBuf) {
>             generateInpatientEvents(a, admissionDiseaseByPatient.get(a.getPatientId()),
>                     orderBuf, vitalBuf, inpatientLabBuf, operationBuf);
>         }
> ```

```java
    /**
     * 生成一次住院的医嘱/生命体征/住院检验/手术，并回写出院字段（dischargeTime/totalCost/status）。
     *
     * @param operationBuf 本次生成的手术缓冲区（`generate()` 的局部变量，必须显式传进来）
     */
    private void generateInpatientEvents(Admission a, Disease disease,
                                         List<MedicalOrder> orderBuf,
                                         List<VitalSign> vitalBuf, List<LabTest> inpatientLabBuf,
                                         List<Operation> operationBuf) {
        PatientProfile profile = profilesByPatient.get(a.getPatientId());
        ClinicalPathway pathway = pathwayFor(a.getAdmissionDiagnosis());
        int totalDays = rnd.nextInt(MIN_STAY_DAYS, MAX_STAY_DAYS + 1);
        boolean compliant = pathway != null && rnd.nextDouble() < PATHWAY_COMPLIANCE_RATE;
        LocalDateTime admissionTime = a.getAdmissionTime();
        LocalDateTime plannedDischarge = admissionTime.plusDays(totalDays).plusHours(rnd.nextInt(0, 24));

        // ── ① 先定手术。出院时间被手术结束时间往下推（spec §6.3），
        //       所以这一步必须在算出院之前，不能等医嘱都排完了再补手术。
        List<DiagnosisOperation> procedures = ontologyRepository.operationsOf(disease);
        Operation op = null;
        int minPostopDays = 0;
        if (!procedures.isEmpty() && rnd.nextDouble() < totalProbability(procedures)) {
            DiagnosisOperation chosen = procedures.get(
                    Weighted.pickIndex(procedures, DiagnosisOperation::getWeight, rnd));
            op = newOperation(a, chosen);
            minPostopDays = chosen.getMinPostopDays() == null ? 1 : chosen.getMinPostopDays();
        }

        // ── ② 出院 = max(原计划, 手术结束 + min_postop_days)。这是**下界修正**：
        //       无手术时 minPostopDays 分支不执行，出院时间与 Task 17 逐字一致。
        LocalDateTime discharge = plannedDischarge;
        if (op != null) {
            LocalDateTime floor = op.getEndTime().plusDays(minPostopDays);
            if (floor.isAfter(discharge)) {
                discharge = floor;
            }
        }
        a.setDischargeTime(discharge);

        // ── ③ 术前检查：手术前 1~2 天补齐该诊断 required=1 的检验项。
        //       放在出院确定之后，因为它要用 op.getStartTime()，与 discharge 无关。
        if (op != null) {
            preopLabs(a, disease, op, inpatientLabBuf);
        }

        // 长期医嘱（首日开，执行至出院）；药品来自该诊断的 diagnosis_drug，剂量来自 drug_dose
        for (DiagnosisDrug row : pickDrugRows(disease, rnd.nextInt(1, 4))) {
            Drug drug = drugsById.get(row.getDrugId());
            if (drug == null) {
                throw new IllegalStateException("diagnosis_drug 的诊断 " + disease.icdCode()
                        + " 引用了不存在的 drug_id=" + row.getDrugId()
                        + "，请修正 seed-ontology.sql 的用药映射");
            }
            MedicalOrder o = newInpatientOrder(a, drug, ontologyRepository.doseOf(drug.getId()), 0,
                    admissionTime.plusMinutes(rnd.nextInt(5, 60)), discharge);
            if (o != null) {
                orderBuf.add(o);
            }
        }

        // ── ④ 术后医嘱：药品只取 postop=1 的行，时段 [手术结束, 出院]
        if (op != null) {
            postopOrders(a, disease, op, discharge, orderBuf);
        }

        // ── ⑤ 护理医嘱：术前每日 1 条、术后每日 2 条（spec §6.3「术后频次高于术前」）
        addNursingOrders(a, op, discharge, orderBuf);

        // 每日生命体征（早中晚 3 次，带趋势；入院之前与出院之后的记录都跳过——
        // 出院时间现在会被手术推后，上界不再能省）
        long stayDays = ChronoUnit.DAYS.between(admissionTime, discharge) + 1;
        for (int day = 0; day < stayDays; day++) {
            for (int slot = 0; slot < 3; slot++) {
                VitalSign vs = newVitalSign(a, day, slot);
                if (!vs.getRecordedAt().isBefore(admissionTime)
                        && !vs.getRecordedAt().isAfter(discharge)) {
                    vitalBuf.add(vs);
                }
            }
        }

        // 每日临时/检验医嘱（上界同样改用 stayDays）
        for (int day = 0; day < stayDays; day++) {
            if (rnd.nextInt(3) == 0) {  // 临时医嘱：同一条诊断的用药，一次性执行
                List<DiagnosisDrug> rows = ontologyRepository.drugsOf(disease);
                if (!rows.isEmpty()) {
                    Drug drug = drugsById.get(rows.get(Weighted.pickIndex(rows, DiagnosisDrug::getWeight, rnd))
                            .getDrugId());
                    if (drug != null) {
                        MedicalOrder o = newInpatientOrder(a, drug, ontologyRepository.doseOf(drug.getId()), 1,
                                admissionTime.plusDays(day).plusHours(9), discharge);
                        if (o != null) {
                            orderBuf.add(o);
                        }
                    }
                }
            }
            if (rnd.nextInt(2) == 0) {  // 检验医嘱 -> lab_test
                LabTest lt = newInpatientLabTest(a, disease, day);
                if (lt != null) {
                    inpatientLabBuf.add(lt);
                }
            }
        }

        // 路径：合规则按步骤补齐对应 LAB/MED 事件，不合规则跳过（自然变异）
        if (compliant && pathway != null) {
            for (PathwayStep step : parsePathwaySteps(pathway.getSteps())) {
                if (step.day() >= stayDays) continue;
                if ("LAB".equals(step.action())) {
                    LabTest lt = labTestForItem(a, disease, step);
                    if (lt == null) {
                        pathwayLabUnmapped++;   // 路径引用了本诊断没有的检验项：如实计数，不静默跳过
                    } else {
                        inpatientLabBuf.add(lt);
                    }
                } else if ("MED".equals(step.action())) {
                    // 路径里的药名不一定在 drug 字典里，查得到才开；查不到计入 pathwayMedMissing
                    Drug drug = drugsById.values().stream()
                            .filter(x -> x.getName().equals(step.item())).findFirst().orElse(null);
                    if (drug == null) {
                        pathwayMedMissing++;
                        continue;
                    }
                    MedicalOrder o = newInpatientOrder(a, drug, ontologyRepository.doseOf(drug.getId()), 1,
                            admissionTime.plusDays(step.day()).plusHours(10), discharge);
                    if (o == null) {
                        pathwayOrderSkipped++;  // 映射得到，但时间窗没过：与「缺本体/缺药」不是一回事
                    } else {
                        orderBuf.add(o);
                    }
                }
            }
        }

        if (op != null) {
            operationBuf.add(op);
            operationCount++;
        }
        a.setStatus(1);
        a.setTotalCost(a.getDeposit().add(BigDecimal.valueOf(stayDays * 120L)));
    }
```

> **`totalDays` 与 `stayDays` 的分工**：`totalDays` 是**计划住院天数**（随机区间来源），
> `stayDays` 是**实际跨的天数**（被手术推长后的值）。体征与每日医嘱按 `stayDays` 排，
> 费用按 `stayDays` 算——用 `totalDays` 会让「住到第 13 天却只收 5 天的费」。

- [ ] **Step 4: 新增五个方法**

```java
    /**
     * 同一 (科室, ICD) 下各术式 probability 之和 = 该诊断本次住院的**手术率**。
     *
     * <p>上界钳到 1.0：种子若把两行写成 0.7 + 0.6，语义上仍然是「必然手术」，
     * 而不是「1.3 的概率」——超出部分无意义，但**不静默忽略**，见 Task 6 的 SQL 断言
     * （那里要求同组概率和 ≤ 1，超出会在入库时被断言拦下）。
     */
    private static double totalProbability(List<DiagnosisOperation> rows) {
        double sum = 0;
        for (DiagnosisOperation r : rows) {
            if (r.getProbability() != null) {
                sum += r.getProbability().doubleValue();
            }
        }
        return Math.min(1.0, sum);
    }

    /**
     * 一条手术记录。
     *
     * <p>日期取入院后 **1~3 天**（术前检查与准备期）；时长按手术级别
     * （1 级 30~60、2 级 60~120、3 级 120~240、4 级 240~360 分钟，spec §6.3）。
     * 开始时刻统一落在 8~15 点之间——**不在夜间安排择期手术**，
     * 这与「手术落在住院区间内」无关，是数据看起来像样的下限要求。
     *
     * <p>术者、助手、麻醉医师**全部取自该住院科室的医生名单**：
     * 术者必须是该科室（spec §4.6 第 3 条硬性约束，校验器第 7 类会查），
     * 助手取同科室的另一个人。麻醉医师也只能如此——`doctor` 表里**没有麻醉科**
     * （`seed.sql` 只有内科/外科/儿科/妇科/五官科/皮肤科六个科室），
     * 这是字典的局限。**不得**用 `randomName()` 编一个不在 `doctor` 表里的人顶上。
     */
    private Operation newOperation(Admission a, DiagnosisOperation chosen) {
        List<String> doctors = doctorsByDept.get(a.getDepartment());
        if (doctors == null || doctors.isEmpty()) {
            throw new IllegalStateException("科室 " + a.getDepartment()
                    + " 在 doctor 表里没有医生，无法指定术者；请先修正 seed.sql 的医生种子");
        }
        String surgeon = doctors.get(rnd.nextInt(doctors.size()));
        int level = chosen.getOperationLevel() == null ? 1 : chosen.getOperationLevel();

        LocalDateTime start = a.getAdmissionTime()
                .plusDays(rnd.nextInt(1, 4))
                .withHour(rnd.nextInt(8, 16)).withMinute(rnd.nextInt(0, 60)).withSecond(0).withNano(0);
        LocalDateTime end = start.plusMinutes(operationMinutes(level));

        Operation op = new Operation();
        op.setAdmissionId(a.getId());
        op.setPatientId(a.getPatientId());
        op.setOperationCode(chosen.getOperationCode());
        op.setOperationName(chosen.getOperationName());
        op.setOperationLevel(level);
        op.setSurgeon(surgeon);
        // 助手：同科室里另一个人；该科室只有一位医生时留空（不编名字）
        op.setAssistant(pickOther(doctors, surgeon));
        if (level >= 3) {
            // 3~4 级必须全身麻醉且有麻醉医师（spec §6.3）
            op.setAnesthesiaType("全身麻醉");
            op.setAnesthesiologist(pickOther(doctors, surgeon));
        } else {
            // 1~2 级可为椎管内/局部麻醉，麻醉医师可空——按级别在两者间分布
            boolean spinal = rnd.nextBoolean();
            op.setAnesthesiaType(spinal ? "椎管内麻醉" : "局部麻醉");
            op.setAnesthesiologist(spinal ? pickOther(doctors, surgeon) : null);
        }
        op.setStartTime(start);
        op.setEndTime(end);
        op.setStatus(1);        // 1=已完成：这批数据全部是历史记录，不存在「已安排」
        return op;
    }

    /** 手术时长（分钟）按级别。**假设值**（spec §6.3 给的区间）。 */
    private int operationMinutes(int level) {
        return switch (level) {
            case 1 -> rnd.nextInt(30, 60);
            case 2 -> rnd.nextInt(60, 120);
            case 3 -> rnd.nextInt(120, 240);
            default -> rnd.nextInt(240, 360);
        };
    }

    /** 从名单里取一个不是 {@code exclude} 的名字；名单只有一个人时返回 {@code null}。 */
    private String pickOther(List<String> names, String exclude) {
        List<String> others = names.stream().filter(n -> !n.equals(exclude)).toList();
        return others.isEmpty() ? null : others.get(rnd.nextInt(others.size()));
    }

    /**
     * 术前检查：手术前 1~2 天补齐该诊断 `required=1` 的检验项（spec §6.3）。
     *
     * <p>「必查」是本体上的标记而不是「随机多开几次」：{@code diagnosis_lab.required=1}
     * 表示该诊断走这条路就必须查这一项。所以这里**不掷骰子**——
     * 掷出一个 0.7 的概率意味着三成手术病人没做凝血功能，那不是真实感，那是缺陷。
     */
    private void preopLabs(Admission a, Disease disease, Operation op, List<LabTest> buf) {
        for (DiagnosisLab row : ontologyRepository.labsOf(disease)) {
            if (row.getRequired() == null || row.getRequired() != 1) {
                continue;
            }
            LocalDateTime at = op.getStartTime().minusDays(rnd.nextInt(1, 3))
                    .withHour(rnd.nextInt(6, 9)).withMinute(rnd.nextInt(0, 60)).withSecond(0).withNano(0);
            if (at.isBefore(a.getAdmissionTime())) {
                // 术前 2 天落在入院之前：钳到入院当天，**不新增一条住院期外的检验**
                at = a.getAdmissionTime().plusHours(1);
            }
            buf.add(newInpatientLabTestFromRow(a, row, at));
        }
    }

    /**
     * 术后医嘱：药品只取该诊断 `postop=1` 的行，时段 {@code [手术结束, 出院]}。
     *
     * <p>一条 `postop=1` 的行都没有时**计入 `postop_missing`**（统计里如实报出），
     * 而不是回落到 `postop=0` 的行——那等于把「术后本该怎么用药」这件本体上没写的事
     * 用一次静默回落盖过去，出口数字看起来还是 0 违规。
     */
    private void postopOrders(Admission a, Disease disease, Operation op,
                              LocalDateTime discharge, List<MedicalOrder> buf) {
        List<DiagnosisDrug> postopRows = ontologyRepository.drugsOf(disease).stream()
                .filter(r -> r.getPostop() != null && r.getPostop() == 1)
                .toList();
        if (postopRows.isEmpty()) {
            postopMissing++;
            return;
        }
        // 手术当天开始，执行至出院；长期医嘱（orderType=0）
        LocalDateTime start = op.getEndTime().plusMinutes(rnd.nextInt(10, 60));
        for (DiagnosisDrug row : pickDrugRows(postopRows, rnd.nextInt(1, POSTOP_MAX_DRUGS + 1))) {
            Drug drug = drugsById.get(row.getDrugId());
            if (drug == null) {
                throw new IllegalStateException("diagnosis_drug 的诊断 " + disease.icdCode()
                        + " 引用了不存在的 drug_id=" + row.getDrugId()
                        + "，请修正 seed-ontology.sql 的用药映射");
            }
            MedicalOrder o = newInpatientOrder(a, drug, ontologyRepository.doseOf(drug.getId()), 0,
                    start, discharge);
            if (o != null) {
                buf.add(o);
            }
        }
    }

    /**
     * 护理医嘱（{@code order_type=3}）：术前每日 1 条、术后每日 2 条。
     *
     * <p>护理医嘱**不挂药品**，所以不能走 {@link #newInpatientOrder}
     * （那个方法第一步就查 `drug_dose`，护理项在 `drug` 表里根本没有行）。
     */
    private void addNursingOrders(Admission a, Operation op, LocalDateTime discharge,
                                  List<MedicalOrder> buf) {
        List<String> doctors = doctorsByDept.get(a.getDepartment());
        String doctor = doctors.get(rnd.nextInt(doctors.size()));

        for (int day = 0; day <= ChronoUnit.DAYS.between(a.getAdmissionTime(), discharge); day++) {
            boolean postop = op != null
                    && !a.getAdmissionTime().plusDays(day).isBefore(op.getEndTime());
            int perDay = postop ? 2 : 1;
            for (int k = 0; k < perDay; k++) {
                MedicalOrder o = new MedicalOrder();
                o.setPatientId(a.getPatientId()); o.setAdmissionId(a.getId());
                o.setOrderType(3);
                o.setItemName(NURSING_ITEMS[rnd.nextInt(NURSING_ITEMS.length)]);
                o.setFrequency("每日" + perDay + "次");
                o.setStartTime(a.getAdmissionTime().plusDays(day).plusHours(7 + k * 8L));
                o.setStopTime(a.getAdmissionTime().plusDays(day).plusHours(23));
                o.setStatus(2);          // 护理医嘱一次性执行
                o.setDoctor(doctor);
                if (o.getStartTime().isAfter(discharge)) {
                    continue;            // 出院之后不再排护理
                }
                buf.add(o);
            }
        }
    }
```

> **上面 `postopOrders` 用到 `pickDrugRows(List<DiagnosisDrug>, int)` 这个重载，
> 它由 Task 23 的 Step 5(d) 新增**：Task 16 的 `pickDrugRows(Disease, int)` 内部本来就是「排序 + 按线抽取」，
> 术后用药只需要「在给定的一批行里抽」这后半段。改法是把 Task 16 的方法拆两层，
> **不要复制第二份加权不放回的循环**：
>
> ```java
>     private List<DiagnosisDrug> pickDrugRows(Disease disease, int max) {
>         return pickDrugRows(ontologyRepository.drugsOf(disease), max);
>     }
> ```
>
> 并把原方法体改成接收 `List<DiagnosisDrug> rows0`（内部 `new ArrayList<>(rows0)` 后排序）。
> 这一处改动**要回填 Task 16 的 Interfaces**，在同一张 `Produces` 列表里
> 把 `pickDrugRows(Disease, int)` 一行替换为两个重载。

> 上面最后那两行 `assert` 是**占位噪音，删掉**——`surgeryDay` 局部变量在最终实现里不需要，
> 留着只会让编译器/读者困惑。写实现时不要引入它。

**两处常量**（加到常量区）：

```java
    /** 护理医嘱的项目名。**假设值**：`medical_order.item_name` 无字典表，取值域由本数组定义。 */
    private static final String[] NURSING_ITEMS = {"一级护理", "二级护理", "生命体征监测", "伤口换药"};

    /** 术后药物的候选条数上界：术后用药不宜像门诊那样堆到 3 种。 */
    private static final int POSTOP_MAX_DRUGS = 2;
```

- [ ] **Step 5: 五处配套改动**

**(a) `operationBuf` / `operationCount` / `postopMissing` 的声明与入库**

字段区（紧邻既有的 `vitalCount` / `orderCount`）加：

```java
    private int operationCount = 0;
    private long postopMissing = 0;
```

**字段区末尾再加一个 Mapper**（`@RequiredArgsConstructor` 按**字段声明顺序**生成构造器，
所以它只能追加在最后一个字段之后，不能插在中间——插中间会让 Task 16/17 已经写好的
`new DataGeneratorService(...)` 调用点全部错位）：

```java
    private final OperationMapper operationMapper;
```

同时补 `import com.his.common.mapper.OperationMapper;`。

`generate()` 的局部缓冲区加 `List<Operation> operationBuf = new ArrayList<>();`，
在既有的分批入库序列里**紧接 `vitalSignMapper` 之后**加一段：

```java
        for (int i = 0; i < operationBuf.size(); i += BATCH_SIZE) {
            operationMapper.insert(operationBuf.subList(i, Math.min(i + BATCH_SIZE, operationBuf.size())));
        }
```

> **这里用 `insert(Collection<T>)`，不写 `insertBatch`。**
> MyBatis-Plus 3.5.7 的 `BaseMapper` **只有 `insert(T)` 与 `insert(Collection<T>)` 两个重载，
> 没有 `insertBatch`**（本项目用的版本实测如此）。写成 `insertBatch` 编译不过。
> 同文件既有的 `patientMapper` / `visitMapper` 等分块入库用的也是这一对重载，
> 照抄它们的形状即可。

`generate()` 的返回 map 加两个键：`stats.put("operations", operationCount);`、
`stats.put("postop_missing", postopMissing);`。

**测试侧同步两处**（`DataGeneratorServiceTest`）：

```java
    private final OperationMapper operationMapper;   // 加到 mock 字段区（与 doctorMapper 并列）
```
```java
        operationMapper = mock(OperationMapper.class);
        ...
        stubBatchInsert(operationMapper, insertedOperations, Operation::setId);
```

> `stubBatchInsert` 是既有的泛型 helper（`doAnswer(...).when(mapper).insert(anyList())`），
> 它挂在 **`insert(Collection)` 这个重载**上。`insertedOperations` 是 Step 1(a) 新增的字段——
> `operations()` 直接返回它，任务 24 起就不再需要别的取数路径。

> **`operation` 必须在 `admission` 之后入库**——它带 `admission_id` 外键。
> 上面「紧接 `vitalSignMapper` 之后」的位置已经满足，因为 `admission` 在更早的分块里。
> 若调整了顺序，`verify-final.sql` §1.1 的引用完整性会红。

**(b) `dischargeDisposition` 加两个参数**

Task 17 的签名 `(PatientProfile profile, boolean isLastAdmission)` 改为：

```java
    /**
     * 出院情况。
     *
     * <p><b>DEATH 只出现在最后一次住院上</b>（见 Task 17 的推导），且只出现在高龄患者、
     * 或有手术且手术级别高的患者上——spec §4.8 要求「DEATH 仅出现在高手术级别或高龄患者」。
     * 两条通路各自极小概率，且**都必须先满足 {@code isLastAdmission}**：
     * 非最后一次住院上写 DEATH 会让「死亡后仍有记录」直接成立（Task 26 的 SQL 会查出非 0）。
     */
    private String dischargeDisposition(PatientProfile profile, boolean isLastAdmission,
                                        boolean operated, int operationLevel) {
        if (isLastAdmission) {
            boolean agedRisk = profile.age() >= DEATH_MIN_AGE && rnd.nextDouble() < DEATH_RATE;
            boolean surgicalRisk = operated && operationLevel >= 4 && rnd.nextDouble() < SURGICAL_DEATH_RATE;
            if (agedRisk || surgicalRisk) {
                return "DEATH";
            }
        }
        double u = rnd.nextDouble();
        if (u < AGAINST_ADVICE_RATE) {
            return "AGAINST_ADVICE";
        }
        // 有手术的住院以「治愈」为主（手术是根治性治疗），保守治疗才偏向「好转」
        double curedRate = operated ? CURED_RATE_SURGICAL : CURED_RATE;
        return u < AGAINST_ADVICE_RATE + curedRate ? "CURED" : "IMPROVED";
    }
```

常量区加（都标 **启发式**，与 `DEATH_RATE` 的注释风格一致）：

```java
    /** 4 级手术后的死亡概率。**启发式**：与高龄通路叠加，但两条通路都在 isLastAdmission 内。 */
    private static final double SURGICAL_DEATH_RATE = 0.010;
    /** 有手术的住院以「治愈」为主的比例。**启发式**。 */
    private static final double CURED_RATE_SURGICAL = 0.55;
```

调用点：`newAdmissions` 里 `a.setDischargeDisposition(dischargeDisposition(profile, lastOne));`
**不能**原样保留——`dischargeDisposition` 现在需要「这次住院做没做手术」，
而手术是在 `generateInpatientEvents` 里决定的，那一刻 `discharge_disposition` 还没写。

**所以顺序要改**：`newAdmissions` 只 **不**写 `dischargeDisposition`，改由
`generateInpatientEvents` 在手术确定之后回写。具体：

1. `newAdmissions` 里删掉 `a.setDischargeDisposition(dischargeDisposition(profile, lastOne));` 一行，
   改为记录「本次住院是不是最后一次」——给 `Admission` 临时挂一个标记。**用
   `a.setDischargeDisposition(null)` 之外的载体**：新增一个字段
   `private final Set<String> lastAdmissionNos = new HashSet<>();`，
   `newAdmissions` 里对最后一次住院执行 `lastAdmissionNos.add(a.getAdmissionNo());`。

   > **必须用 `admissionNo` 而不是 `a.getId()`。** `newAdmissions` 里这一刻 `Admission` 的 id
   > **还是 `null`**——它由后面那次 `admissionMapper.insert(admissionBuf)` 回填
   > （见 Task 17 Step 3(c)），而**填充这行代码的执行顺序在回填之前**。
   > 于是 `add(a.getId())` 往集合里放的是 `null`；等到 `generateInpatientEvents` 里
   > 调 `contains(a.getId())` 时，`a.getId()` 已经是回填后的真 id，而集合里只有 `null`
   > ——**对每一次住院都返回 `false`**，`isLastAdmission` 恒假。
   >
   > 后果不是报错，是**静默消失**：`DEATH` 永远不会被写出（Task 17 的
   > `death_only_on_the_last_admission...` 与 Task 18 的 §2.5/§2.7 会一起红），
   > 而「死亡是时间线的唯一终止出口」这条设计也就没了落地方式。这类 bug 的排查成本
   > 极高——数据看起来完全正常，只是少了一个极低频的取值。
   >
   > `admissionNo` 在构造时就写成 `"A" + patientId + "-" + (i + 1)`，**插入前就非空且唯一**，
   > 是这里唯一可用的稳定键。（用 `Set<Long>` 也是这样错的——键的类型换了，错法一模一样，
   > 所以这里必须连类型一起改。）
2. `generateInpatientEvents` 在 `if (op != null) { ... }` 之后、`a.setStatus(1)` 之前插入：

```java
        a.setDischargeDisposition(dischargeDisposition(profile,
                lastAdmissionNos.contains(a.getAdmissionNo()),
                op != null, op == null ? 0 : op.getOperationLevel()));
```

3. `profile` 从**字段**取，不要加形参：在方法体开头（Step 3 给的签名下方）写一行

```java
        PatientProfile profile = profilesByPatient.get(a.getPatientId());
```

   `profilesByPatient` 带初始化器（`private final Map<...> = new HashMap<>()`），
   Lombok 不把它算进构造器参数，任何方法里都能直接读。
   **本任务给 `generateInpatientEvents` 加的唯一形参是 `List<Operation> operationBuf`**（见 Step 3）——
   那是编译必需的；`profile` 不是，多传一个就有了传错人的余地。

> **为什么用 `Set<String>` 而不是把「是否末次」直接传进 `generateInpatientEvents`**：
> 「是不是最后一次住院」这个信息只在 `newAdmissions` 里算得出来（它遍历整个住院列表），
> `generateInpatientEvents` 只看得见单张住院单。两种做法都行，本计划选 `Set` 是因为
> 它不改 `generateInpatientEvents` 的入参形状——`operationBuf` 那个形参是 Step 3 为了
> 写回手术缓冲区而加的，与「是否末次」无关。分开之后 Task 24 的校验器测试不受影响。
>
> 键必须用 `admissionNo`：`newAdmissions` 这一刻 `a.getId()` 还是 `null`（见上一条），
> 用 id 做键的集合会被填进一个 `null`，之后 `contains` 恒假。
> 集合里放的是「哪些住院号是末次」，而 `admissionNo` 在插入前就已唯一非空。

**(c) `DATA_TABLES` 与截断测试**

Task 17 已把 `operation` 加进 `DATA_TABLES` 与截断测试的列表，**本任务不动**。
若发现 `operation` 不在里面，说明 Task 17 漏了——补上，并在提交信息里写明。

**(d) 把 `pickDrugRows` 拆成两层**

Task 16 的 `pickDrugRows(Disease, int)` 内部是「取本体行 → 排序 → 按线加权不放回抽取」。
术后用药（Step 4 的 `postopOrders`）只有一批**已经筛好**的行（`postop=1` 的那些），
需要的是后半段。把该方法拆成：

```java
    private List<DiagnosisDrug> pickDrugRows(Disease disease, int max) {
        return pickDrugRows(ontologyRepository.drugsOf(disease), max);
    }

    /** 在给定的一批本体行里按 line 升序、同线按 weight 加权不放回地取最多 max 种。 */
    private List<DiagnosisDrug> pickDrugRows(List<DiagnosisDrug> rows0, int max) {
        List<DiagnosisDrug> rows = new ArrayList<>(rows0);
        rows.sort(Comparator.comparingInt(DataGeneratorService::lineOf));
        ...（其余逐字照搬 Task 16 的实现）
    }
```

**回填 Task 16 的 Interfaces**：把 `Produces` 里
`- \`List<DiagnosisDrug> pickDrugRows(Disease disease, int max)\`` 一行换成这两个重载，
并加一句「Task 23 用列表重载来抽术后用药」。不这么做的话，
Task 16 的完成报告里写着「只有一个 pickDrugRows」，而 Task 23 的代码里出现两个——
review 时会以为是 Task 23 私自复制了一份。

**(e) 给 `dischargeDisposition` 开一道测试缝**

Step 1 那两条 `DEATH` 用例要直接调 `dischargeDisposition` 并把随机数钉死，
而现在这两样都是 `private`。做两处放开：

```java
    /**
     * 随机源。生产路径是 {@code ThreadLocalRandom.current()}；
     * **测试可以替换成定种子实现**，用来钉住那些「概率极小、靠抽样等不到」的分支。
     * 类型是 {@code RandomGenerator} 而不是 {@code ThreadLocalRandom}——
     * 本项目的 {@code PatientProfile.build} / {@code EventCalendar.build} 收的也是它，
     * 换了类型反而与既有约定一致。
     */
    RandomGenerator rnd = ThreadLocalRandom.current();
```

```java
    /** 出院情况。**包级可见是为了让测试能直接钉住 DEATH 分支**，见本步说明。 */
    String dischargeDisposition(PatientProfile profile, boolean isLastAdmission,
                                boolean operated, int operationLevel) {
```

补 `import java.util.random.RandomGenerator;`（`java.util.concurrent.ThreadLocalRandom` 保留）。

> **为什么值得为它放开可见性**：`DEATH` 是「患者此后不再有任何记录」的唯一出口，
> 而它的发生率是 `DEATH_RATE = 0.03` 再叠「末次住院」和「年龄 ≥ 75」——
> 抽样观测下期望值约 0.1 条，**靠跑生成器断言它出现要么长期红、要么长期空转**
> （Task 17 的 `death_only_on_the_last_admission...` 里那句 `assertThat(deaths).isGreaterThan(0)`
> 已经因此被删掉了）。删掉之后，`DEATH` 这条通路就**一段测试都没有**：
> 把 `if (isLastAdmission)` 拿掉、或把 `lastAdmissionNos` 写成 id 集合，全套测试依旧全绿，
> 而线上数据里 `DEATH` 恒不出现——这正是 F-27 那个坑第二次发作的形态。
> 这两条用例把它变成确定性的：`ZeroDoubleGenerator` 让 `nextDouble()` 恒为 0.0。
>
> 代价只是同包内可见。**不要**为此把 `dischargeDisposition` 提成 `public`——
> 它不该出现在这个类的对外面上。

- [ ] **Step 6: 回填 Task 17 的 Interfaces**

`generateInpatientEvents` 的签名在本任务被扩展（末尾追加 `List<Operation> operationBuf`）。
到 Task 17 的 `Produces` 块里把那一行改为：

```
  - `private void generateInpatientEvents(Admission a, Disease disease,
    List<MedicalOrder> orderBuf, List<VitalSign> vitalBuf, List<LabTest> inpatientLabBuf,
    List<Operation> operationBuf)` ——
    **Task 23 会在不改形状的前提下用它回写出院情况**；`operationBuf` 是 `generate()`
    的局部缓冲区，方法体里要往里 `add` 手术，所以必须显式传进来。
    `profile` **没有**加成形参——`profilesByPatient` 本就是字段，方法体里直接读
    （Task 17 那一刻还没有手术信息，也没有这个需求）
```

**`operationBuf` 必须放在最后一个形参位。** Task 17 的实现者按 `Interfaces` 里给的顺序写，
本任务在其尾部追加，插在中间会让 Task 17 已写好的调用点里
`inpatientLabBuf` 与 `operationBuf` 两个实参错位——
那种错在编译期报的是「类型不匹配」（两个都是 `List`，但泛型不同），
看不出是参数顺序问题，会白烧一轮 review。

同一处再加两句：

- `dischargeDisposition(profile, isLastAdmission)` 在 Task 17 只写保守治疗那一支，
  **Task 23 会把它扩成四参数**——Task 17 的实现者看到这行就知道签名会变，不会以为写错了。
- Task 23 会新增字段 `private final Set<String> lastAdmissionNos = new HashSet<>();`，
  在 `newAdmissions` 里填、在 `generateInpatientEvents` 里读。**Task 17 不需要写它**，
  这个字段整个属于 Task 23；这里提一句只是让 Task 17 的实现者知道
  「末次住院」这件事有人在管，不要自己另造一套标记。

- [ ] **Step 7: 跑生成器测试**

```bash
cd backend && mvn -o -pl his-generator test
```

Expected: BUILD SUCCESS。

若 `operations_stay_inside_the_admission_and_respect_min_postop_days` 红在
`Duration.between(...).toDays() >= 3`，先确认 `minPostopDays` 取的是
`chosen.getMinPostopDays()`（术式自身的值）而不是级别名义值——
Task 6 的表里 `47.01` 的 `min_postop_days=3`，而 2 级的名义区间是 3~5，两者在本例恰好一致，
换个术式（如 `13.41` 的 2）就会露馅。

- [ ] **Step 8: Commit**

```bash
git add backend/his-generator/src/main/java/com/his/generator/DataGeneratorService.java \
        backend/his-generator/src/test/java/com/his/generator/DataGeneratorServiceTest.java
git commit -m "feat(generator): 手术域生成 —— 术式抽取、出院时间按术后天数修正、术前检查与术后医嘱" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 24: 校验器追加手术四类（阶段四）

**Files:**
- Modify: `backend/his-server/src/main/java/com/his/server/service/validation/OntologyConsistencyValidator.java`
- Test: `backend/his-server/src/test/java/com/his/server/service/validation/OntologyConsistencyValidatorTest.java`

**Interfaces:**
- Consumes：Task 11 的 `OntologyConsistencyValidator`；Task 2 的 `OperationMapper`、
  `DiagnosisOperationMapper`、`DoctorMapper`；Task 10 的 `OntologyRules.select`
- Produces：`byType` 补齐为 8 个键；`OntologyViolation.operationId` 从此有值
- **不改** `OntologyReport` / `OntologyViolation` / `ValidationReport` 的形状——
  Task 11 建的 DTO 已经预留了手术侧字段，本任务只填。

**四类都判在 `operation` 行上**（spec §8.3 明文）：一条越界手术计一次，
而不是把所属住院计一次。否则「一次住院做了 3 台手术、其中 2 台越界」会被数成 1，
口径不可解释。

**`unchecked` 的三个入口**（与门诊侧同一约定：判不了就说判不了）：
1. 手术的 `admission_id` 在 `admission` 表里找不到；
2. 该住院的入院诊断**名称映射不到 ICD**（`admission` 存的是名称，不是编码）；
3. 该 ICD 在 `dept_diagnosis` 里**完全没有行**（本体没覆盖这个诊断，无从判断它该不该手术）。

**第 3 条要与第 1 类检查区分开**：ICD 在本体里存在、但
`diagnosis_operation` 里一行都没有，是**确定的违规**（「这个诊断从不做手术」），
不是 unchecked。这两者一个是「本子没记这个病」、一个是「本子记了，写着不手术」。

- [ ] **Step 1: 写失败的测试**

在 `OntologyConsistencyValidatorTest` 里新增（`stubSurgery` 等 helper 放文件末尾）：

```java
    /** 一次住院 + 入院诊断名 + 该诊断的 ICD。 */
    private Admission stubAdmission(long admissionId, long patientId, String department,
                                    String diagnosisName, LocalDateTime admissionTime,
                                    LocalDateTime dischargeTime) {
        Admission a = new Admission();
        a.setId(admissionId);
        a.setPatientId(patientId);
        a.setDepartment(department);
        a.setAdmissionDiagnosis(diagnosisName);
        a.setDiagnosis(diagnosisName);
        a.setAdmissionTime(admissionTime);
        a.setDischargeTime(dischargeTime);
        when(am.selectList(any())).thenReturn(List.of(a));
        return a;
    }

    private Operation surgery(long id, long admissionId, long patientId, String code,
                              String surgeon, LocalDateTime start, LocalDateTime end) {
        Operation o = new Operation();
        o.setId(id);
        o.setAdmissionId(admissionId);
        o.setPatientId(patientId);
        o.setOperationCode(code);
        o.setOperationName("术式" + code);
        o.setOperationLevel(2);
        o.setSurgeon(surgeon);
        o.setStartTime(start);
        o.setEndTime(end);
        return o;
    }

    /** 一条手术本体行。 */
    private DiagnosisOperation opLink(String icd, String dept, String code, int minPostop) {
        DiagnosisOperation x = new DiagnosisOperation();
        x.setIcdCode(icd);
        x.setDepartment(dept);
        x.setOperationCode(code);
        x.setOperationName("术式" + code);
        x.setOperationLevel(2);
        x.setMinPostopDays(minPostop);
        x.setProbability(new BigDecimal("1.000"));
        x.setWeight(new BigDecimal("1.000"));
        return x;
    }

    private Doctor doctor(String name, String department) {
        Doctor d = new Doctor();
        d.setName(name);
        d.setDepartment(department);
        return d;
    }

    @Test
    void flags_operation_not_supported_by_the_admission_diagnosis() {
        stubAdmission(9L, 1L, "内科", "诊断I10",
                LocalDateTime.of(2026, 3, 1, 9, 0), LocalDateTime.of(2026, 3, 8, 9, 0));
        when(ddm.selectList(any())).thenReturn(List.of(dept("内科", "I10", 0, 15, 89)));
        when(om.selectList(any())).thenReturn(List.of(surgery(100L, 9L, 1L, "47.01", "杨文博",
                LocalDateTime.of(2026, 3, 2, 9, 0), LocalDateTime.of(2026, 3, 2, 10, 30))));
        // I10 在 dept_diagnosis 里，但 diagnosis_operation 里一行都没有 -> 确定的违规
        when(dom.selectList(any())).thenReturn(List.of());

        OntologyReport report = validator().validate();

        assertThat(report.getByType().get("OPERATION_DIAGNOSIS")).isEqualTo(1L);
        assertThat(report.getUnchecked()).isZero();
    }

    @Test
    void surgery_on_a_diagnosis_absent_from_the_ontology_counts_as_unchecked() {
        stubAdmission(9L, 1L, "内科", "本体里没有的诊断名",
                LocalDateTime.of(2026, 3, 1, 9, 0), LocalDateTime.of(2026, 3, 8, 9, 0));
        when(ddm.selectList(any())).thenReturn(List.of(dept("内科", "I10", 0, 15, 89)));
        when(om.selectList(any())).thenReturn(List.of(surgery(100L, 9L, 1L, "47.01", "杨文博",
                LocalDateTime.of(2026, 3, 2, 9, 0), LocalDateTime.of(2026, 3, 2, 10, 30))));

        OntologyReport report = validator().validate();

        // 诊断名映射不到 ICD -> 无从判断该不该手术，报未判定；不得伪造成违规
        assertThat(report.getByType().get("OPERATION_DIAGNOSIS")).isZero();
        assertThat(report.getUnchecked()).isPositive();
    }

    @Test
    void flags_operation_outside_the_admission_window() {
        stubAdmission(9L, 1L, "外科", "诊断K35.8",
                LocalDateTime.of(2026, 3, 1, 9, 0), LocalDateTime.of(2026, 3, 8, 9, 0));
        when(ddm.selectList(any())).thenReturn(List.of(dept("外科", "K35.8", 0, 15, 89)));
        when(dom.selectList(any())).thenReturn(List.of(opLink("K35.8", "外科", "47.01", 3)));
        when(dm2.selectList(any())).thenReturn(List.of(doctor("杨文博", "外科")));
        // 手术开始于出院之后
        when(om.selectList(any())).thenReturn(List.of(surgery(100L, 9L, 1L, "47.01", "杨文博",
                LocalDateTime.of(2026, 3, 9, 9, 0), LocalDateTime.of(2026, 3, 9, 10, 30))));

        OntologyReport report = validator().validate();

        assertThat(report.getByType().get("OPERATION_TIMESPAN")).isEqualTo(1L);
    }

    @Test
    void flags_end_time_not_after_start_time() {
        stubAdmission(9L, 1L, "外科", "诊断K35.8",
                LocalDateTime.of(2026, 3, 1, 9, 0), LocalDateTime.of(2026, 3, 8, 9, 0));
        when(ddm.selectList(any())).thenReturn(List.of(dept("外科", "K35.8", 0, 15, 89)));
        when(dom.selectList(any())).thenReturn(List.of(opLink("K35.8", "外科", "47.01", 3)));
        when(dm2.selectList(any())).thenReturn(List.of(doctor("杨文博", "外科")));
        LocalDateTime t = LocalDateTime.of(2026, 3, 2, 9, 0);
        when(om.selectList(any())).thenReturn(List.of(surgery(100L, 9L, 1L, "47.01", "杨文博", t, t)));

        OntologyReport report = validator().validate();

        assertThat(report.getByType().get("OPERATION_TIMESPAN")).isEqualTo(1L);
    }

    @Test
    void flags_surgeon_from_another_department() {
        stubAdmission(9L, 1L, "外科", "诊断K35.8",
                LocalDateTime.of(2026, 3, 1, 9, 0), LocalDateTime.of(2026, 3, 8, 9, 0));
        when(ddm.selectList(any())).thenReturn(List.of(dept("外科", "K35.8", 0, 15, 89)));
        when(dom.selectList(any())).thenReturn(List.of(opLink("K35.8", "外科", "47.01", 3)));
        // doctor 表里外科只有杨文博；术者却是内科的李建华
        when(dm2.selectList(any())).thenReturn(List.of(doctor("杨文博", "外科"),
                doctor("李建华", "内科")));
        when(om.selectList(any())).thenReturn(List.of(surgery(100L, 9L, 1L, "47.01", "李建华",
                LocalDateTime.of(2026, 3, 2, 9, 0), LocalDateTime.of(2026, 3, 2, 10, 30))));

        OntologyReport report = validator().validate();

        assertThat(report.getByType().get("SURGEON_DEPT")).isEqualTo(1L);
    }

    @Test
    void flags_postop_stay_shorter_than_min_postop_days() {
        // 2026-03-02 10:30 结束，min_postop_days=3 -> 出院至少要 2026-03-05 10:30；
        // 这里出院时间是 03-04，不足 3 天
        stubAdmission(9L, 1L, "外科", "诊断K35.8",
                LocalDateTime.of(2026, 3, 1, 9, 0), LocalDateTime.of(2026, 3, 4, 9, 0));
        when(ddm.selectList(any())).thenReturn(List.of(dept("外科", "K35.8", 0, 15, 89)));
        when(dom.selectList(any())).thenReturn(List.of(opLink("K35.8", "外科", "47.01", 3)));
        when(dm2.selectList(any())).thenReturn(List.of(doctor("杨文博", "外科")));
        when(om.selectList(any())).thenReturn(List.of(surgery(100L, 9L, 1L, "47.01", "杨文博",
                LocalDateTime.of(2026, 3, 2, 9, 0), LocalDateTime.of(2026, 3, 2, 10, 30))));

        OntologyReport report = validator().validate();

        assertThat(report.getByType().get("POSTOP_DAYS")).isEqualTo(1L);
    }

    @Test
    void clean_surgery_passes_all_four_checks() {
        stubAdmission(9L, 1L, "外科", "诊断K35.8",
                LocalDateTime.of(2026, 3, 1, 9, 0), LocalDateTime.of(2026, 3, 6, 9, 0));
        when(ddm.selectList(any())).thenReturn(List.of(dept("外科", "K35.8", 0, 15, 89)));
        when(dom.selectList(any())).thenReturn(List.of(opLink("K35.8", "外科", "47.01", 3)));
        when(dm2.selectList(any())).thenReturn(List.of(doctor("杨文博", "外科")));
        when(om.selectList(any())).thenReturn(List.of(surgery(100L, 9L, 1L, "47.01", "杨文博",
                LocalDateTime.of(2026, 3, 2, 9, 0), LocalDateTime.of(2026, 3, 2, 10, 30))));

        OntologyReport report = validator().validate();

        assertThat(report.getByType()).containsKeys(
                "OPERATION_DIAGNOSIS", "OPERATION_TIMESPAN", "SURGEON_DEPT", "POSTOP_DAYS");
        assertThat(report.getByType().get("OPERATION_DIAGNOSIS")).isZero();
        assertThat(report.getByType().get("OPERATION_TIMESPAN")).isZero();
        assertThat(report.getByType().get("SURGEON_DEPT")).isZero();
        assertThat(report.getByType().get("POSTOP_DAYS")).isZero();
    }

    @Test
    void surgery_whose_admission_is_missing_counts_as_unchecked() {
        when(am.selectList(any())).thenReturn(List.of());
        when(om.selectList(any())).thenReturn(List.of(surgery(100L, 9L, 1L, "47.01", "杨文博",
                LocalDateTime.of(2026, 3, 2, 9, 0), LocalDateTime.of(2026, 3, 2, 10, 30))));

        OntologyReport report = validator().validate();

        assertThat(report.getUnchecked()).isEqualTo(1);
        assertThat(report.getViolations()).isZero();
    }
```

> 测试类需要新增两个 mock 字段：`om`（`OperationMapper`）与 `dm2`（`DoctorMapper`；
> 名字用 `dm2` 是因为 `dm` 已被 `DiagnosisMapper` 占用）。`dom`（`DiagnosisOperationMapper`）。
> 三个都必须在 `setUp` 里 `mock(...)` 并 `lenient().when(...).thenReturn(List.of())`，
> 否则既有的 13 个测试会因为 `@BeforeEach` 里的严格打桩报 `UnnecessaryStubbingException`。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-server -am test \
  -Dtest=OntologyConsistencyValidatorTest -Dsurefire.failIfNoSpecifiedTests=false
```

Expected: 编译失败（三个 mock 字段与构造参数不存在）。

- [ ] **Step 3: 扩构造与 `CHECK_TYPES`**

```java
    private static final List<String> CHECK_TYPES = List.of(
            "DEPT_DIAGNOSIS", "AGE_GENDER", "LAB_DIAGNOSIS", "ADMISSION",
            "OPERATION_DIAGNOSIS", "OPERATION_TIMESPAN", "SURGEON_DEPT", "POSTOP_DAYS");
```

字段区**追加**（`@RequiredArgsConstructor` 按字段声明顺序生成构造器，
所以只能往后加，不能插在中间——插中间会让既有的所有 `new OntologyConsistencyValidator(...)`
调用点参数错位，而**错位的两个 mapper 都是同类型接口时编译仍然通过**）：

```java
    private final OperationMapper operationMapper;
    private final DiagnosisOperationMapper diagnosisOperationMapper;
    private final DoctorMapper doctorMapper;
```

测试类里那唯一的 `validator()` helper 同步改为
`new OntologyConsistencyValidator(vm, dm, pm, lm, am, ddm, dlm, om, dom, dm2)`。

- [ ] **Step 4: 在 `validate()` 末尾追加手术四类**

先做一处**前置改动**：把 Task 11 检查 4 的循环头改成遍历提到前面的局部变量，
并顺带建好 id → 住院的映射（见下方「前置改动」）。**这处不改的话本步编译不过**：
`admissionsById` 在 `validate()` 里根本不存在。

```java
        // 检查 4：住院的入院诊断必须 admission_eligible=1
        List<Admission> allAdmissions = admissionMapper.selectList(null);
        Map<Long, Admission> admissionsById = new HashMap<>();
        for (Admission a : allAdmissions) {
            admissionsById.put(a.getId(), a);
        }
        for (Admission a : allAdmissions) {
            ...（检查 4 原有循环体逐字不动）
        }
```

在检查 4（住院）之后、`report.setChecked(checked);` 之前插入：

```java
        // 检查 5~8：逐条手术判。**四类都判在 operation 行上**（spec §8.3）——
        // 一条越界手术计一次，而不是把所属住院计一次。
        List<Operation> allOps = operationMapper.selectList(null);   // 全表扫一次，循环里复用
        List<DiagnosisOperation> opOntology = diagnosisOperationMapper.selectList(null).stream()
                .filter(x -> x.getIcdCode() != null && x.getOperationCode() != null)
                .toList();
        // 住院科室 -> 该科室的医生名单（检查 7 的判据来源）
        Map<String, Set<String>> doctorsByDept = new HashMap<>();
        for (Doctor doc : doctorMapper.selectList(null)) {
            if (doc.getDepartment() != null && doc.getName() != null) {
                doctorsByDept.computeIfAbsent(doc.getDepartment(), k -> new HashSet<>()).add(doc.getName());
            }
        }

        for (Operation op : allOps) {
            Admission a = op.getAdmissionId() == null ? null : admissionsById.get(op.getAdmissionId());
            if (a == null) {
                unchecked++;        // 手术挂在一张查不到的住院单上：无法定位科室与诊断
                continue;
            }
            String diag = a.getAdmissionDiagnosis() != null ? a.getAdmissionDiagnosis() : a.getDiagnosis();
            String icd = diag == null ? null : icdOf(diag, ontology);
            if (icd == null || !rowsByIcd.containsKey(icd)) {
                // 诊断名映射不到 ICD，或该 ICD 根本不在 dept_diagnosis 里。
                // **区别于「ICD 在、diagnosis_operation 无行」**——那是确定的「该诊断不手术」，
                // 属于违规；这里是「本子没记这个病」，属未判定。
                unchecked++;
                continue;
            }
            checked++;
            String dept = a.getDepartment();
            List<DiagnosisOperation> applicable = OntologyRules.select(opOntology,
                    DiagnosisOperation::getDepartment, DiagnosisOperation::getIcdCode, dept, icd);

            // 检查 5：术式必须在该住院诊断的手术清单里
            boolean supported = applicable.stream()
                    .anyMatch(r -> r.getOperationCode().equals(op.getOperationCode()));
            if (!supported) {
                byType.merge("OPERATION_DIAGNOSIS", 1L, Long::sum);
                report.getDetails().add(violation(a.getPatientId(), null, a.getId(), op.getId(),
                        "OPERATION_DIAGNOSIS",
                        "术式 " + op.getOperationCode() + " 不在 " + dept + " 的 " + icd + " 手术清单中"
                                + "（可选：" + (applicable.isEmpty() ? "无" : applicable.stream()
                                        .map(DiagnosisOperation::getOperationCode)
                                        .distinct().collect(Collectors.joining("/"))) + "）"));
            }

            // 检查 6：时间区间
            boolean spanBad = op.getStartTime() == null || op.getEndTime() == null
                    || !op.getEndTime().isAfter(op.getStartTime())
                    || a.getAdmissionTime() == null || a.getDischargeTime() == null
                    || op.getStartTime().isBefore(a.getAdmissionTime())
                    || op.getEndTime().isAfter(a.getDischargeTime());
            if (spanBad) {
                byType.merge("OPERATION_TIMESPAN", 1L, Long::sum);
                report.getDetails().add(violation(a.getPatientId(), null, a.getId(), op.getId(),
                        "OPERATION_TIMESPAN",
                        "手术 [" + op.getStartTime() + ", " + op.getEndTime() + "] 不在住院区间 ["
                                + a.getAdmissionTime() + ", " + a.getDischargeTime() + "] 内"));
            }

            // 检查 7：术者必须属于该住院科室
            Set<String> deptDoctors = doctorsByDept.get(dept);
            if (deptDoctors == null || op.getSurgeon() == null || !deptDoctors.contains(op.getSurgeon())) {
                byType.merge("SURGEON_DEPT", 1L, Long::sum);
                report.getDetails().add(violation(a.getPatientId(), null, a.getId(), op.getId(),
                        "SURGEON_DEPT",
                        "术者「" + op.getSurgeon() + "」不属于 " + dept
                                + "（该科室：" + (deptDoctors == null ? "无医生" : String.join("/", deptDoctors)) + "）"));
            }

            // 检查 8：术后住院天数 >= 该术式的 min_postop_days
            // 用**该术式自身**的 min_postop_days，而不是手术级别对应的名义值——
            // Task 6 的表里 13.41（三级）取 2 天、06.4（四级）取 9 天，都是**逐术式**的值。
            if (op.getEndTime() != null && a.getDischargeTime() != null && supported) {
                Integer need = applicable.stream()
                        .filter(r -> r.getOperationCode().equals(op.getOperationCode()))
                        .map(DiagnosisOperation::getMinPostopDays)
                        .filter(Objects::nonNull)
                        .findFirst().orElse(null);
                if (need != null
                        && op.getEndTime().plusDays(need).isAfter(a.getDischargeTime())) {
                    byType.merge("POSTOP_DAYS", 1L, Long::sum);
                    report.getDetails().add(violation(a.getPatientId(), null, a.getId(), op.getId(),
                            "POSTOP_DAYS",
                            "术后住院 " + Duration.between(op.getEndTime(), a.getDischargeTime()).toDays()
                                    + " 天 < 该术式要求的 " + need + " 天"));
                }
            }
        }
```

补 import：`com.his.common.entity.Operation`、`com.his.common.entity.DiagnosisOperation`、
`com.his.common.entity.Doctor`、`java.time.Duration`、`java.util.Objects`。

> **检查 8 只在 `supported` 为真时判**：术式不在清单里时，`need` 查不到，
> 硬判会让同一条手术被计入两类违规，`byType` 的和就不再等于「有问题的记录数」。

**两处口径必须写清（否则日后回看会以为是漏判）**：

1. **ICD 在本体里存在、但该住院科室在这个 ICD 上没有行**（如内科的住院单上写「急性阑尾炎」）
   时，`applicable` 为空 → 计 `OPERATION_DIAGNOSIS`。这是**有意的**：
   该科室在 `diagnosis_operation` 里对这个诊断一行都没有，说明本子不承认这条路，
   此时做手术是确定的违规，不是「判不了」。这与 Task 11 检验判的
   「适用行为空 → `unchecked`」不同，因为检验本体的**行就是项目清单**
   （无行 = 没录这个病的检验），而手术本体的**语义是「这个诊断做不做手术」**。
   两者的差别源于 spec §4.7 明文给 `probability` 的定义（0 表示该诊断从不手术），
   检验本体没有对应语义。**不要为了「统一」把这条改成 unchecked**。
2. **本任务不新增「住院科室 ↔ 入院诊断」这一类检查。** spec §8.3 的 8 类里没有它，
   所以「内科收治急性阑尾炎」目前只能被第 1 类（门诊侧）间接挡住——
   而住院诊断不经门诊时就没有任何一类能判它。这是**已知覆盖缺口**，
   spec 范围之外，不在本计划内补。若作者要补，那就是第 9 类
   `ADMISSION_DEPT`，判法与第 1 类同构（`rowsByIcd` + 科室归属），
   但**必须同时改 spec §8.3 的表格与 §11 的测试矩阵**，否则论文章节与代码对不上。

- [ ] **Step 5: 跑测试确认通过**

```bash
cd backend && mvn -o -pl his-server -am test \
  -Dtest=OntologyConsistencyValidatorTest -Dsurefire.failIfNoSpecifiedTests=false
```

Expected: Tests run: 21, Failures: 0, Errors: 0（既有 13 + 新增 8）。

- [ ] **Step 6: Commit**

```bash
git add backend/his-server/src/main/java/com/his/server/service/validation/OntologyConsistencyValidator.java \
        backend/his-server/src/test/java/com/his/server/service/validation/OntologyConsistencyValidatorTest.java
git commit -m "feat(validation): 本体校验器补齐手术四类，8 类违规口径齐全" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 25: 手术进住院时间轴与前端展示（阶段四）

**Files:**
- Modify: `backend/his-server/src/main/java/com/his/server/service/impl/TimelineServiceImpl.java`
  （`buildAdmission` 加 `OPERATION` 事件）
- Create: `backend/his-server/src/main/java/com/his/server/service/OperationService.java`
- Create: `backend/his-server/src/main/java/com/his/server/service/impl/OperationServiceImpl.java`
- Create: `backend/his-server/src/main/java/com/his/server/controller/OperationController.java`
- Modify: `frontend/src/api/inpatient.js`（`listOperations` / `createOperation`）
- Modify: `frontend/src/components/Timeline.vue`（`TYPE_META` 加 `OPERATION`）
- Modify: `frontend/src/views/AdmissionDetail.vue`（手术信息区）
- Test: `backend/his-server/src/test/java/com/his/server/service/TimelineServiceTest.java`
- Test: `backend/his-server/src/test/java/com/his/server/service/OperationServiceImplTest.java`
- Test: `backend/his-server/src/test/java/com/his/server/controller/OperationControllerTest.java`

**Interfaces:**
- Consumes：Task 2 的 `OperationMapper`；既有的 `CacheInvalidator.evictAdmissionTimeline`
- Produces：`TimelineEvent("OPERATION", op.getStartTime(), op)`；
  `OperationService.save(Operation)`（写库 + 两次 evict）、`OperationService.listByAdmission(Long)`
- **为什么本任务要建一个 controller**：spec §6.3 要求「写入 `operation` 时必须 evict
  `timeline:admission:{admissionId}`」。生成器直连 mapper 写库，**它不经过任何 service，
  也没有 `CacheInvalidator` 可用**（`his-generator → his-common`，两者不依赖），
  所以那条要求对生成器无主体可施加。有主体的是**经 API 写入的那条路**——
  而本项目此前没有 `operation` 的写入接口。§8.4 的「人工录入路径」演示也需要它：
  人工录一条越界手术，看板上当场看到第 6 类违规被检出。

**`OPERATION` 事件的 `time` 取 `start_time`**（不是 `end_time`）：
时间轴是按事件**发生**时刻排序的，手术发生在开始那一刻。
用 `end_time` 会让「手术」排在术后医嘱之后，看起来像先用药后开刀。

- [ ] **Step 1: 写失败的测试**

`TimelineServiceTest` 追加：

```java
    @Test
    void admission_timeline_includes_operations_sorted_by_start_time() {
        Admission a = new Admission();
        a.setId(9L); a.setPatientId(1L);
        a.setAdmissionTime(LocalDateTime.of(2026, 3, 1, 9, 0));
        a.setDischargeTime(LocalDateTime.of(2026, 3, 8, 9, 0));
        when(admissionMapper.selectById(9L)).thenReturn(a);
        when(medicalOrderMapper.selectList(any())).thenReturn(List.of());
        when(vitalSignMapper.selectList(any())).thenReturn(List.of());
        when(labTestMapper.selectList(any())).thenReturn(List.of());

        Operation later = new Operation();
        later.setId(101L); later.setAdmissionId(9L); later.setPatientId(1L);
        later.setOperationCode("47.09");
        later.setStartTime(LocalDateTime.of(2026, 3, 4, 9, 0));
        later.setEndTime(LocalDateTime.of(2026, 3, 4, 10, 30));

        Operation earlier = new Operation();
        earlier.setId(100L); earlier.setAdmissionId(9L); earlier.setPatientId(1L);
        earlier.setOperationCode("47.01");
        earlier.setStartTime(LocalDateTime.of(2026, 3, 2, 9, 0));
        earlier.setEndTime(LocalDateTime.of(2026, 3, 2, 10, 30));

        // **故意「晚的在前」返回**：mapper 的返回顺序不影响时间轴顺序，排序是 service 的职责。
        // 只插 1 个事件的话，「时间线事件严格按 time 升序」这条全局约束实际没有被断言——
        // 用例名字里有 sorted，测的却只有 1 个元素。
        when(operationMapper.selectList(any())).thenReturn(List.of(later, earlier));

        TimelineVO vo = service.getAdmissionTimeline(9L);

        assertThat(vo.getEvents()).hasSize(2);
        assertThat(vo.getEvents()).extracting(TimelineEvent::getType).containsOnly("OPERATION");
        assertThat(vo.getEvents()).extracting(TimelineEvent::getTime).containsExactly(
                LocalDateTime.of(2026, 3, 2, 9, 0),      // time 取**开始**时刻，不是结束时刻
                LocalDateTime.of(2026, 3, 4, 9, 0));
        assertThat(vo.getEvents().get(0).getData()).isSameAs(earlier);
        assertThat(vo.getEvents().get(1).getData()).isSameAs(later);
    }
```

`OperationServiceImplTest`（新建，模仿 `LabTestServiceImplTest` 的写法）：

```java
class OperationServiceImplTest {

    @Test
    void save_evicts_both_timelines() {
        OperationMapper mapper = mock(OperationMapper.class);
        CacheInvalidator inv = mock(CacheInvalidator.class);
        OperationServiceImpl svc = new OperationServiceImpl(mapper, inv);

        Operation op = new Operation();
        op.setId(100L); op.setAdmissionId(9L); op.setPatientId(42L);
        svc.save(op);

        verify(mapper).insert(op);
        verify(inv).evictTimeline(42L);
        verify(inv).evictAdmissionTimeline(9L);
    }

    @Test
    void save_without_admission_only_evicts_the_patient_timeline() {
        OperationMapper mapper = mock(OperationMapper.class);
        CacheInvalidator inv = mock(CacheInvalidator.class);
        OperationServiceImpl svc = new OperationServiceImpl(mapper, inv);

        Operation op = new Operation();
        op.setId(100L); op.setPatientId(42L);       // admissionId 为空
        svc.save(op);

        verify(inv).evictTimeline(42L);
        verify(inv, never()).evictAdmissionTimeline(any());
    }
}
```

`OperationControllerTest`（`@WebMvcTest` + `@MockBean`，模仿 `MedicalOrderControllerTest`）：

```java
    @Test
    void create_returns_the_saved_operation_in_the_envelope() throws Exception {
        Operation op = new Operation();
        op.setId(100L); op.setAdmissionId(9L); op.setOperationCode("47.01");
        when(operationService.save(any())).thenReturn(op);

        mockMvc.perform(post("/api/operations")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"admissionId\":9,\"patientId\":1,\"operationCode\":\"47.01\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.operationCode").value("47.01"));
    }

    @Test
    void list_by_admission_is_ordered_by_start_time() throws Exception {
        when(operationService.listByAdmission(9L)).thenReturn(List.of());
        mockMvc.perform(get("/api/operations").param("admissionId", "9"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));
    }
```

> **`save` 返回实体的约定**：照着 `MedicalOrderServiceImpl.save` 的样子写
> （`save` 落库后把实体返回，便于 controller 回信封）。若既有实现的 `save` 返回 `void`，
> 以**既有实现为准**并同步改上面两个测试——**不要**为了测试好看给一个实体新造一套返回约定。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && mvn -o -pl his-server -am test \
  -Dtest='TimelineServiceTest,OperationServiceImplTest,OperationControllerTest' \
  -Dsurefire.failIfNoSpecifiedTests=false
```

Expected: 编译失败（`OperationService` 等尚不存在）。

- [ ] **Step 3: 建 `OperationService` / `OperationServiceImpl` / `OperationController`**

```java
package com.his.server.service;

import com.his.common.entity.Operation;
import java.util.List;

public interface OperationService {
    Operation save(Operation op);
    List<Operation> listByAdmission(Long admissionId);
}
```

```java
package com.his.server.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.entity.Operation;
import com.his.common.mapper.OperationMapper;
import com.his.server.service.CacheInvalidator;
import com.his.server.service.OperationService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 手术记录的写入与查询。
 *
 * <p><b>写入必须同时失效两个缓存</b>：手术既是患者时间轴上的事件，
 * 也是住院时间轴上的事件。少了后者，录入一条手术之后住院页要等缓存过期才看得到——
 * 而缓存 TTL 是 30 分钟，演示时看起来就是「没生效」。
 */
@Service
@RequiredArgsConstructor
public class OperationServiceImpl implements OperationService {

    private final OperationMapper operationMapper;
    private final CacheInvalidator cacheInvalidator;

    @Override
    public Operation save(Operation op) {
        operationMapper.insert(op);
        cacheInvalidator.evictTimeline(op.getPatientId());
        if (op.getAdmissionId() != null) {
            cacheInvalidator.evictAdmissionTimeline(op.getAdmissionId());
        }
        return op;
    }

    @Override
    public List<Operation> listByAdmission(Long admissionId) {
        return operationMapper.selectList(new LambdaQueryWrapper<Operation>()
                .eq(Operation::getAdmissionId, admissionId)
                .orderByAsc(Operation::getStartTime));
    }
}
```

```java
package com.his.server.controller;

import com.his.common.common.Result;
import com.his.common.entity.Operation;
import com.his.server.service.OperationService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/operations")
@RequiredArgsConstructor
public class OperationController {

    private final OperationService operationService;

    @PostMapping
    public Result<Operation> create(@RequestBody Operation op) {
        return Result.ok(operationService.save(op));
    }

    @GetMapping
    public Result<List<Operation>> list(@RequestParam Long admissionId) {
        return Result.ok(operationService.listByAdmission(admissionId));
    }
}
```

> `CacheInvalidator` 的**包路径要以项目里实际的为准**（当前是 `com.his.server.service`）。
> `MedicalOrderServiceImpl` 的 import 行就是现成的答案，照抄它。

- [ ] **Step 4: `TimelineServiceImpl` 加 `OPERATION` 事件**

`buildAdmission` 里，在 `labTestMapper` 那段之后加：

```java
        operationMapper.selectList(new LambdaQueryWrapper<Operation>()
                        .eq(Operation::getAdmissionId, admissionId))
                .forEach(o -> events.add(new TimelineEvent("OPERATION", o.getStartTime(), o)));
```

字段区追加 `private final OperationMapper operationMapper;`——
**必须加在最后一个字段之后**，即 `TimelineServiceImpl.java:43` 的
`private final ObjectMapper objectMapper;` 之下（`@RequiredArgsConstructor`
按**声明顺序**生成构造器，插在中间会让所有 `new TimelineServiceImpl(...)` 的参数错位）。
`TimelineServiceTest` 的构造调用点同步在末尾补一个 `operationMapper` mock。

- [ ] **Step 5: 跑服务端测试**

```bash
cd backend && mvn -o -pl his-server -am test \
  -Dtest='TimelineServiceTest,OperationServiceImplTest,OperationControllerTest' \
  -Dsurefire.failIfNoSpecifiedTests=false
```

Expected: 全部通过。

- [ ] **Step 6: 前端**

`frontend/src/api/inpatient.js` 追加：

```js
export const listOperations = (admissionId) =>
  request.get('/operations', { params: { admissionId } })
export const createOperation = (data) => request.post('/operations', data)
```

`AdmissionDetail.vue` 加一个手术信息区（放在住院基本信息卡之后）。
**`OPERATION` 事件在时间轴上的图标与颜色要和既有的 ORDER/VITAL_SIGN/LAB_TEST 区分开**——
`surgery` 用红色、`order` 用蓝色即可；具体写法照抄该文件里既有的
`v-if` 分支，**不新增样式类**：

```vue
      <el-table-column prop="type" label="类型" width="110">
        <template #default="{ row }">
          <el-tag :type="typeTag(row.type)" size="small">{{ typeLabel(row.type) }}</el-tag>
        </template>
      </el-table-column>
```

```js
// OPERATION 在阶段四才出现；未识别类型回落到原文，不隐藏——隐藏会让「时间轴少了一类事件」
// 看起来像「这类事件不存在」，而不是「前端还没支持」
const TYPE_LABELS = {
  ORDER: '医嘱',
  VITAL_SIGN: '生命体征',
  LAB_TEST: '检验',
  OPERATION: '手术',
}
const TYPE_TAGS = { ORDER: 'primary', VITAL_SIGN: 'success', LAB_TEST: 'warning', OPERATION: 'danger' }
const typeLabel = (t) => TYPE_LABELS[t] || t
const typeTag = (t) => TYPE_TAGS[t] || 'info'
```

**`frontend/src/components/Timeline.vue` 的 `TYPE_META` 也要加一项**（该文件第 148 行起，
病人时间轴与住院时间轴都由它渲染）：

```js
  OPERATION: { label: '手术', color: '#F56C6C' },
```

不加的后果不是报错，而是**静默降级**：`metaOf` 的兜底是
`TYPE_META[type] || { label: type || '其他', color: '#909399' }`，
于是手术事件会渲染成一个灰底标签、上面写着英文 `OPERATION`——
看起来像「这个系统压根没有手术这类事件」，而不是「前端漏配了一个 color」。
红色 `#F56C6C` 是 Element Plus 的 `danger` 色，与 `AdmissionDetail.vue` 里
`OPERATION: 'danger'` 那格一致，两处不要各挑一个色。

手术明细表放在住院信息之下：

```vue
    <el-card v-if="operations.length" shadow="never" style="margin-top: 16px">
      <template #header><span class="card-title">手术记录</span></template>
      <el-table :data="operations" size="small" border>
        <el-table-column prop="operationName" label="术式" min-width="200" />
        <el-table-column prop="operationCode" label="编码" width="100" />
        <el-table-column prop="operationLevel" label="级别" width="70" />
        <el-table-column label="时间" width="280">
          <template #default="{ row }">{{ fmt(row.startTime) }} ~ {{ fmt(row.endTime) }}</template>
        </el-table-column>
        <el-table-column prop="surgeon" label="术者" width="100" />
        <el-table-column prop="anesthesiaType" label="麻醉" width="110" />
      </el-table>
    </el-card>
```

```js
const operations = ref([])
...
const res = await listOperations(admissionId)
if (res.code === 0) operations.value = res.data
```

`fmt` 用该文件里**已有**的格式化函数；若没有，用
`(v) => (v ? String(v).replace('T', ' ').slice(0, 16) : '-')`。
**不要**为此引入 dayjs —— 前端依赖里没有它。

- [ ] **Step 7: 构建前端 + 手工核对**

```bash
cd frontend && npm run build
```

Expected: 构建成功。

启动后端后核对住院时间轴里出现了 `OPERATION`：

```bash
curl -s "http://localhost:8080/api/timeline/admission/1" | head -c 600
```

Expected: `"events":[...,{"type":"OPERATION",...},...]`。
**一个 OPERATION 都没有**说明要么该住院没做手术（正常）、要么生成器没跑（回 Task 23）。

- [ ] **Step 8: Commit**

```bash
git add backend/his-server/src/main/java/com/his/server/service/OperationService.java \
        backend/his-server/src/main/java/com/his/server/service/impl/OperationServiceImpl.java \
        backend/his-server/src/main/java/com/his/server/controller/OperationController.java \
        backend/his-server/src/main/java/com/his/server/service/impl/TimelineServiceImpl.java \
        backend/his-server/src/test/java/com/his/server/service/TimelineServiceTest.java \
        backend/his-server/src/test/java/com/his/server/service/OperationServiceImplTest.java \
        backend/his-server/src/test/java/com/his/server/controller/OperationControllerTest.java \
        frontend/src/api/inpatient.js frontend/src/views/AdmissionDetail.vue \
        frontend/src/components/Timeline.vue
git commit -m "feat(server,frontend): 手术进住院时间轴、新增手术写入接口并失效住院缓存" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 26: 阶段四出口实测与测试报告收尾

**Files:**
- Modify: `backend/sql/verify-phase2.sql`（追加第 3 节：手术域 3.1~3.11 共 11 项 + 手术级别分布）
- Modify: `backend/sql/verify-final.sql`（§1.2/§1.4/§2.4/§3.3.2 四处失效断言若 Task 18 未改，本任务补齐）
- Modify: `docs/test-report.md`（§7.2 住院与手术域实测、§9 阶段四出口）
- Modify: `docs/superpowers/specs/2026-09-21-medical-realism-design.md`（**仅在实测与 spec 不符时**改：把实测值写进 §5.3 的「需逐条核实」行列，或标注为假设）

**Interfaces:**
- Consumes：Task 18 的 `verify-phase2.sql`；Task 23 的统计键 `operations` / `postop_missing`；
  Task 24 的 8 类 `byType`
- Produces：spec §11「手术域行为」①~⑨ 与「生成器行为」⑧⑨ 的实测证据

**「实测」的硬要求（与 Task 18 同）**：本任务表格里的每一个数字都必须是
`verify-phase2.sql` 或 `POST /api/validation/run` 的输出**逐字粘贴**。
未测的写「未实测」并附命令，**不得填估算值**。

- [ ] **Step 1: 给 `verify-phase2.sql` 追加第 3 节**

```sql
-- ============================================================
-- 3. 手术域（阶段四出口判据，spec §11「手术域行为」①~⑨）
-- ============================================================
SELECT '3.1 手术条数(>0 表示手术域已产出)' AS chk, COUNT(*) AS v FROM operation
UNION ALL SELECT '3.2 时间越界或顺序颠倒(应 0)', COUNT(*) FROM operation o
  JOIN admission a ON a.id = o.admission_id
  WHERE o.start_time < a.admission_time OR o.end_time > a.discharge_time
     OR o.end_time <= o.start_time
UNION ALL SELECT '3.3 术者不属于该住院科室(应 0)', COUNT(*) FROM operation o
  JOIN admission a ON a.id = o.admission_id
  WHERE NOT EXISTS (SELECT 1 FROM doctor d
    WHERE d.department = a.department AND d.name = o.surgeon)
UNION ALL SELECT '3.4 术后住院天数不足(应 0)', COUNT(*) FROM operation o
  JOIN admission a ON a.id = o.admission_id
  JOIN dept_diagnosis dd ON dd.diagnosis_name = a.admission_diagnosis
  JOIN diagnosis_operation dop
    ON dop.icd_code = dd.icd_code AND dop.operation_code = o.operation_code
   AND (dop.department IS NULL OR dop.department = a.department)
  WHERE TIMESTAMPDIFF(DAY, o.end_time, a.discharge_time) < dop.min_postop_days
UNION ALL SELECT '3.5 术式不在该诊断手术清单中(应 0)', COUNT(*) FROM operation o
  JOIN admission a ON a.id = o.admission_id
  JOIN dept_diagnosis dd ON dd.diagnosis_name = a.admission_diagnosis
  WHERE NOT EXISTS (SELECT 1 FROM diagnosis_operation dop
    WHERE dop.icd_code = dd.icd_code AND dop.operation_code = o.operation_code
      AND (dop.department IS NULL OR dop.department = a.department))
UNION ALL SELECT '3.6 挂了手术本体的诊断却一行手术都没有(应 0)', COUNT(*) FROM (
  SELECT DISTINCT dd.icd_code FROM dept_diagnosis dd
  JOIN diagnosis_operation dop ON dop.icd_code = dd.icd_code
  WHERE dd.admission_eligible = 1) x
  WHERE NOT EXISTS (SELECT 1 FROM operation o
    JOIN admission a ON a.id = o.admission_id
    JOIN dept_diagnosis dd2 ON dd2.diagnosis_name = a.admission_diagnosis
    WHERE dd2.icd_code = x.icd_code)
UNION ALL SELECT '3.7 手术级别与麻醉不符(3~4 级须全麻且有麻醉医师,应 0)', COUNT(*)
  FROM operation WHERE operation_level >= 3
   AND (anesthesia_type <> '全身麻醉' OR anesthesiologist IS NULL)
UNION ALL SELECT '3.8 DEATH 患者此后仍有记录(应 0)', (
  SELECT COUNT(*) FROM admission a WHERE a.discharge_disposition = 'DEATH'
   AND (EXISTS (SELECT 1 FROM visit v WHERE v.patient_id = a.patient_id AND v.visit_time > a.discharge_time)
     OR EXISTS (SELECT 1 FROM admission a2 WHERE a2.patient_id = a.patient_id
                AND a2.id <> a.id AND a2.admission_time > a.discharge_time)
     OR EXISTS (SELECT 1 FROM lab_test l WHERE l.patient_id = a.patient_id AND l.tested_at > a.discharge_time)
     OR EXISTS (SELECT 1 FROM medical_order m WHERE m.patient_id = a.patient_id AND m.start_time > a.discharge_time)
     OR EXISTS (SELECT 1 FROM vital_sign s WHERE s.patient_id = a.patient_id AND s.recorded_at > a.discharge_time)))
UNION ALL SELECT '3.9 术后时段无医嘱的住院(应 0)', COUNT(*) FROM (
  SELECT o.admission_id, o.end_time FROM operation o) x
  WHERE NOT EXISTS (SELECT 1 FROM medical_order m
    WHERE m.admission_id = x.admission_id AND m.start_time > x.end_time)
UNION ALL SELECT '3.10 无手术本体的诊断却产出了手术(应 0)', COUNT(*) FROM operation o
  JOIN admission a ON a.id = o.admission_id
  JOIN dept_diagnosis dd ON dd.diagnosis_name = a.admission_diagnosis
  WHERE NOT EXISTS (SELECT 1 FROM diagnosis_operation dop
    WHERE dop.icd_code = dd.icd_code
      AND (dop.department IS NULL OR dop.department = a.department))
UNION ALL SELECT '3.11 术前 1~2 天缺 required=1 检验的手术(应 0)', COUNT(*) FROM operation o
  JOIN admission a ON a.id = o.admission_id
  JOIN dept_diagnosis dd ON dd.diagnosis_name = a.admission_diagnosis
  WHERE EXISTS (SELECT 1 FROM diagnosis_lab dl
      WHERE dl.icd_code = dd.icd_code AND dl.required = 1
        AND (dl.department IS NULL OR dl.department = a.department))
    AND NOT EXISTS (SELECT 1 FROM lab_test l
      WHERE l.admission_id = a.id
        AND l.tested_at < o.start_time
        AND l.tested_at >= o.start_time - INTERVAL 2 DAY
        AND EXISTS (SELECT 1 FROM diagnosis_lab dl2
          WHERE dl2.icd_code = dd.icd_code AND dl2.required = 1
            AND dl2.item_code = l.item_code
            AND (dl2.department IS NULL OR dl2.department = a.department)));
-- `operation_level` 必须写 `o.operation_level`：两张表都有这一列，
-- 不限定表名 MySQL 报 ERROR 1052 (Column 'operation_level' in field list is ambiguous)。
SELECT o.operation_level, COUNT(*) AS n, MIN(dop.min_postop_days) AS mn, MAX(dop.min_postop_days) AS mx
  FROM operation o JOIN diagnosis_operation dop ON dop.operation_code = o.operation_code
  GROUP BY o.operation_level ORDER BY o.operation_level;
```

> **3.4 / 3.5 / 3.10 / 3.11 的 `JOIN dept_diagnosis ON dd.diagnosis_name = a.admission_diagnosis`**：
> `admission` 存的是诊断**名称**。若同一名称在本体里对应两个 ICD（理论上不该发生，
> 但种子写重复就说不准），这四条会给出偏大的结果。**先跑 `SELECT diagnosis_name, COUNT(DISTINCT icd_code)
> FROM dept_diagnosis GROUP BY diagnosis_name HAVING COUNT(DISTINCT icd_code) > 1;`
> 确认返回空**，再把结果抄进报告。
>
> 这四条里凡是比对 `diagnosis_operation.department` / `diagnosis_lab.department` 的地方，
> **都写成 `department IS NULL OR department = a.department`**：这两列的含义是
> 「NULL=全部科室适用」（见 `diagnosis_lab` 的建表注释），直接写等号会把
> 全科室适用的行整批漏掉，而漏掉的方向恰好是「少判违规」——数字会好看，
> 但是假的。

- [ ] **Step 2: 重跑生成器并对全部断言**

```bash
cd backend && mvn -q -pl his-generator -am install -DskipTests
java -jar his-generator/target/his-generator-1.0.0.jar
```

Expected: 控制台输出的统计里 `operations` 非 0、`postop_missing` 为 0，
`pathway_lab_unmapped` / `pathway_med_missing` / `pathway_order_skipped` 与 `dose_missing`
四个计数都**非 0但不离谱**（与 Task 18 Step 3 同一判据）。

```bash
mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -t < backend/sql/verify-phase2.sql
```

把整个输出**逐字**贴进 `docs/test-report.md` §7.2。
**任何一格非 0 都不许改口径掩盖**——若 3.9 非 0，说明 `postopOrders` 的时段算错了；
若 3.7 非 0，说明 `newOperation` 的分级分支写反了。

- [ ] **Step 3: 取最新的本体校验报告**

```bash
curl -s -X POST http://localhost:8080/api/validation/run > /tmp/after-phase4.json
python -c "
import json;d=json.load(open('/tmp/after-phase4.json'))['data']
o=d['ontology'];r=d['rationalDrug']
print('ontology checked=%s violations=%s unchecked=%s rate=%s'%(o['checked'],o['violations'],o['unchecked'],o['consistentRate']))
for k,v in sorted(o['byType'].items()): print('  %-20s %s'%(k,v))
print('rational total=%s violations=%s unchecked=%s rate=%s'%(r['totalPrescriptions'],r['violationPrescriptions'],r['unchecked'],r['rationalRate']))
"
```

Expected: **8 个键全部存在**，`violations` 与 `unchecked` 全为 0，
`consistentRate` 与覆盖率都是 100%。

> **这一条是整份计划的总出口判据**（spec §8.3 的「判据」）：
> `ontology.violations == 0`、`rational.unchecked == 0`、`rational.violations == 0`。
> 三者任一非 0，**不要**去调校验器让它通过——那说明本体或生成器存在真实缺陷，
> 回查是哪一类，改生成器或补本体。

- [ ] **Step 4: 写 `docs/test-report.md` §9**

```markdown
## 9. 阶段四出口：手术域实测

**实测时间**：（填写）
**数据规模**：（patient / admission / operation 三个行数，实测）
**生成器统计**：`operations=`、`postop_missing=`、`dose_missing=`、`pathway_lab_unmapped=`、
`pathway_med_missing=`、`pathway_order_skipped=`（实测）

### 9.1 手术域九项断言（spec §11「手术域行为」①~⑨）

| # | 检查 | 实测值 | 判据 | 来源查询 |
|---|---|---|---|---|
| ① | 手术时间落在住院区间内且 `end > start` | （实测） | 0 | §3 的 3.2 |
| ② | 术者属于该住院科室 | （实测） | 0 | §3 的 3.3 |
| ③ | `discharge_time - end_time >= min_postop_days` | （实测） | 0 | §3 的 3.4 |
| ④ | 术式在该住院诊断的手术清单中 | （实测） | 0 | §3 的 3.5 |
| ⑤ | 无手术本体的诊断不产生手术 | （实测） | 0 | §3 的 3.10 |
| ⑥ | 3~4 级全身麻醉且麻醉医师非空 | （实测） | 0 | §3 的 3.7 |
| ⑦ | 术后时段存在医嘱 | （实测） | 0 | §3 的 3.9 |
| ⑧ | 术前 1~2 天存在 `required=1` 检验项 | （实测） | 0 | §3 的 3.11 |
| ⑨ | DEATH 患者此后无就诊记录 | （实测） | 0 | §3 的 3.8 |

> **不要把 ⑤ 填成 3.6 的数**：3.6 查的是**相反方向**的性质——「挂了手术本体的诊断却一行手术都没有」
> （本体有、数据缺，是**覆盖**问题）；⑤ 查的是「本体里根本没有术式的诊断却产出了手术」
> （本体没有、数据有，是**越界**问题）。两者都是 0 才算过关，但**是两个不同的 0**，
> 混填会把「覆盖数」当成「越界数」写进论文。
>
> 3.10 是 3.5 的**子集**（本体里一项术式都没有，显然是「术式不在清单中」的极端情形），
> 所以恒有 `3.10 ≤ 3.5`。留成两条是因为 spec §11 的 ①~⑨ 各要一个独立数字，
> 而 3.5 的计数里混着「清单里有 A 却做了 B」的情形，答不了「本体完全没写」这一问。
>
> 3.11 的判据与 Task 23 的 `required_preop_labs_exist_before_the_surgery` 一致，
> 但**下界更严**：测试只断言 `tested_at < start_time`，3.11 还要求
> `tested_at >= start_time - INTERVAL 2 DAY`（spec §6.3 的「术前 1~2 天」原文）。
> 生成器的 `preopLabs` 取 `start_time - 1~2 天`，术前 2 天落在入院之前时**钳到入院当天**，
> 那个钳位值仍落在本窗口内，所以这条应恒为 0。**若实测非 0**，说明钳位逻辑改了，
> 要回头核对 `preopLabs`，不要去放宽窗口。

### 9.2 生成器行为 ⑧⑨（spec §11）

| # | 检查 | 实测值 | 判据 |
|---|---|---|---|
| ⑧ | 偏低方向的异常确实出现（`isAbnormal=1` 且低于参考下界） | （实测） | > 0 |
| ⑨ | 临床路径产生的检验中存在 `isAbnormal=1` | （实测） | > 0 |

### 9.3 本体校验八类（`POST /api/validation/run` 实测）

| 违规类型 | 条数 |
|---|---|
| DEPT_DIAGNOSIS | （实测） |
| AGE_GENDER | （实测） |
| LAB_DIAGNOSIS | （实测） |
| ADMISSION | （实测） |
| OPERATION_DIAGNOSIS | （实测） |
| OPERATION_TIMESPAN | （实测） |
| SURGEON_DEPT | （实测） |
| POSTOP_DAYS | （实测） |

`checked=`、`unchecked=`、`consistentRate=`、`rational.unchecked=`、`rational.violations=`（全部实测）

（未测的格子写「未实测」+ 命令，**不得填估算值**。）
```

- [ ] **Step 5: 回填 spec 的假设标注**

打开 spec §5.3 的「时间参数的来源标注」表，对**实测后仍无出处的参数**，
把状态从「需逐条核实」改为「**假设值（已做敏感性分析，见 test-report §7.3）**」，
并写明本次实测取的值。**不要**改成「有出处」——没打开过原文就不是出处。

同时给 §9.2 的「手术」一行补一句实测规模：
`实测 N 个术式 / M 行挂接`（N、M 从 Task 6 的 SQL 断言输出取）。
**若实测与 §9.2 写的 15~25 不符，改 §9.2 而不是改数字。**

- [ ] **Step 6: 全量测试 + 提交**

```bash
cd backend && mvn -o test
cd frontend && npm run build
```

Expected: 两端都成功。

```bash
git add backend/sql/verify-phase2.sql backend/sql/verify-final.sql \
        docs/test-report.md docs/superpowers/specs/2026-09-21-medical-realism-design.md
git commit -m "docs: 手术域九项断言实测落表，spec 时间参数按实测回填为假设值" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

**阶段四完成标志**：§9.1 九项全 0、§9.2 两项全 >0、§9.3 八类全 0 且
`unchecked` 为 0，且**每一个数字都能在同一台机器上重跑出来**。

**整份计划完成标志**：四个阶段的完成标志同时成立；
`docs/test-report.md` §7~§9 无「待实测」残留（或残留项都附了命令）；
`docs/superpowers/specs/2026-09-21-medical-realism-design.md` 与实现对得上
（不一致时以实测为准改 spec，并在提交信息里写明改了哪一节）。

---



