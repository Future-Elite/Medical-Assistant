-- Additive migration: legacy HIS tables are never deleted or rewritten.
CREATE TABLE IF NOT EXISTS neonatal_import_batch (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  file_name VARCHAR(255) NOT NULL,
  file_sha256 CHAR(64) NOT NULL,
  sheet_name VARCHAR(255) NOT NULL,
  record_count INT NOT NULL DEFAULT 0,
  issue_count INT NOT NULL DEFAULT 0,
  imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_neonatal_file (file_sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS neonatal_source_row (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  batch_id BIGINT NOT NULL,
  source_row_number INT NOT NULL,
  source_file VARCHAR(255),
  raw_data JSON NOT NULL,
  UNIQUE KEY uk_neonatal_source (batch_id, source_row_number),
  CONSTRAINT fk_neonatal_source_batch FOREIGN KEY (batch_id) REFERENCES neonatal_import_batch(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- One row is one source discharge document. A hospital number is NOT a unique infant identity.
CREATE TABLE IF NOT EXISTS neonatal_record (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  batch_id BIGINT NOT NULL,
  source_row_id BIGINT NOT NULL,
  hospital_no VARCHAR(100),
  gender VARCHAR(10),
  reported_age VARCHAR(100),
  reported_age_days INT,
  gestational_age_raw VARCHAR(100),
  gestational_weeks INT,
  gestational_days INT,
  admission_time DATETIME,
  discharge_time DATETIME,
  admission_time_raw VARCHAR(100),
  discharge_time_raw VARCHAR(100),
  length_of_stay INT,
  treatment_result VARCHAR(255),
  outcome_group VARCHAR(30) NOT NULL,
  issue_count INT NOT NULL DEFAULT 0,
  UNIQUE KEY uk_neonatal_record_source (source_row_id),
  KEY idx_neonatal_batch_date (batch_id, admission_time),
  KEY idx_neonatal_batch_hospital (batch_id, hospital_no),
  KEY idx_neonatal_batch_ga (batch_id, gestational_weeks),
  CONSTRAINT fk_neonatal_record_batch FOREIGN KEY (batch_id) REFERENCES neonatal_import_batch(id),
  CONSTRAINT fk_neonatal_record_source FOREIGN KEY (source_row_id) REFERENCES neonatal_source_row(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS neonatal_summary (
  record_id BIGINT PRIMARY KEY,
  presenting_symptoms MEDIUMTEXT,
  laboratory_findings MEDIUMTEXT,
  special_examinations MEDIUMTEXT,
  treatment_course MEDIUMTEXT,
  complications TEXT,
  discharge_condition MEDIUMTEXT,
  discharge_orders MEDIUMTEXT,
  discharge_guidance MEDIUMTEXT,
  CONSTRAINT fk_neonatal_summary_record FOREIGN KEY (record_id) REFERENCES neonatal_record(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- The workbook often concatenates diagnoses without separators. Preserve the whole text,
-- without guessing ICD codes, primary diagnoses or individual disease boundaries.
CREATE TABLE IF NOT EXISTS neonatal_diagnosis (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  record_id BIGINT NOT NULL,
  phase VARCHAR(20) NOT NULL,
  diagnosis_text TEXT NOT NULL,
  UNIQUE KEY uk_neonatal_diagnosis (record_id, phase),
  CONSTRAINT fk_neonatal_diagnosis_record FOREIGN KEY (record_id) REFERENCES neonatal_record(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS neonatal_quality_issue (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  record_id BIGINT NOT NULL,
  issue_code VARCHAR(50) NOT NULL,
  field_name VARCHAR(100) NOT NULL,
  message VARCHAR(500) NOT NULL,
  KEY idx_neonatal_issue (record_id, issue_code),
  CONSTRAINT fk_neonatal_issue_record FOREIGN KEY (record_id) REFERENCES neonatal_record(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
