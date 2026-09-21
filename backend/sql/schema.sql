-- 模拟 HIS 系统 —— 建表脚本（库 his_demo 由 Step 1 创建，脚本作用于命令行指定的库）
-- 执行：mysql -uroot -p123456 --default-character-set=utf8mb4 his_demo < backend/sql/schema.sql
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
  KEY idx_visit_patient_time (patient_id, visit_time),
  KEY idx_visit_department (department)
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
  patient_id BIGINT NOT NULL, visit_id BIGINT NULL,
  item_code VARCHAR(20), item_name VARCHAR(100) NOT NULL,
  result_value VARCHAR(50), unit VARCHAR(20),
  reference_range VARCHAR(50), is_abnormal TINYINT DEFAULT 0,
  tested_at DATETIME NOT NULL,
  admission_id BIGINT NULL,
  KEY idx_lab_patient_time (patient_id, tested_at),
  KEY idx_lab_admission (admission_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='检验结果表';

CREATE TABLE rule_config (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  diagnosis_code VARCHAR(20), diagnosis_name VARCHAR(100) NOT NULL,
  drug_ids VARCHAR(500), enabled TINYINT DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='诊断用药配置规则表';

CREATE TABLE doctor (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(50) NOT NULL,
  department VARCHAR(50) NOT NULL,
  KEY idx_doctor_department (department)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='医生表';

CREATE TABLE admission (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  patient_id BIGINT NOT NULL,
  admission_no VARCHAR(32) NOT NULL,
  department VARCHAR(50), doctor VARCHAR(50),
  bed_no VARCHAR(20), diagnosis VARCHAR(100),
  admission_route VARCHAR(16) COMMENT 'OUTPATIENT门诊转入/EMERGENCY急诊/TRANSFER转院',
  discharge_disposition VARCHAR(16) COMMENT 'CURED医嘱离院/IMPROVED好转/TRANSFER转院/DEATH死亡/AGAINST_ADVICE自动出院',
  admission_diagnosis VARCHAR(100) COMMENT '入院诊断',
  discharge_diagnosis VARCHAR(100) COMMENT '出院诊断，可与入院诊断不同（诊断演进）',
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
