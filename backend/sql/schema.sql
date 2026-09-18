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
