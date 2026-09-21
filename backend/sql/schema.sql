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
