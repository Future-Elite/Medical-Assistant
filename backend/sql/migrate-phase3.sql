-- 三期迁移：为已存在的 his_demo 库补列。**只能执行一次**（MySQL 8 不支持 ADD COLUMN IF NOT EXISTS）。
-- 全新安装不需要本文件——schema.sql 已含全部列。
USE his_demo;

ALTER TABLE admission
  ADD COLUMN admission_route VARCHAR(16) COMMENT 'OUTPATIENT门诊转入/EMERGENCY急诊/TRANSFER转院',
  ADD COLUMN discharge_disposition VARCHAR(16) COMMENT 'CURED医嘱离院/IMPROVED好转/TRANSFER转院/DEATH死亡/AGAINST_ADVICE自动出院',
  ADD COLUMN admission_diagnosis VARCHAR(100) COMMENT '入院诊断',
  ADD COLUMN discharge_diagnosis VARCHAR(100) COMMENT '出院诊断，可与入院诊断不同（诊断演进）';

-- ---------------------------------------------------------------------------
-- 以下 7 张表是「已有库」路径不可或缺的一部分：
-- Step 5 的断言 1（应为 7）作用于已存在的 his_demo 库，而 CREATE TABLE 只写在
-- schema.sql 里（仅全新安装会执行）。若迁移脚本不含建表语句，已部署库跑完迁移后
-- 只会补上 4 个列，7 张新表仍然缺失，断言 1 恒为 0，且 Task 2 起的实体映射与
-- Task 3–9 的种子装载都会因表不存在而失败。
-- 故此处按 brief Step 1 / Step 2 的 DDL 逐字补齐。DDL 与 schema.sql 完全一致。
-- 本文件同为一次性执行。实测重复执行的行为（见 task-1-report.md）：
--   · 不加 --force：因 ALTER 在最前，第二次执行在第 5 行即报
--     "Duplicate column name 'admission_route'" 并中止（exit 1），本建表段不会被执行；
--   · 加 --force：上述错误与本段 7 条 "Table already exists" 一并报出，脚本继续跑完。
--   两种情况都属预期，不是需要绕过的缺陷。
-- ---------------------------------------------------------------------------

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
  month_weights JSON NULL COMMENT '长度12的正数数组，季节乘子；全1表示无季节性',
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
