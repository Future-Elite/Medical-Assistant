-- 模拟 HIS 系统二期 —— 增量种子（合理用药规则 + 临床路径）
-- 执行：mysql -uroot -p123456 --default-character-set=utf8mb4 his_demo < backend/sql/seed-extension.sql
-- 药品 id 对齐 backend/sql/seed.sql：1阿莫西林 3布洛芬 4硝苯地平 5二甲双胍 8左氧氟沙星 9阿司匹林 10氨氯地平

INSERT INTO rational_rule (rule_type, drug_a_id, drug_b_id, warning_level, message, enabled) VALUES
(1, 1, 8, 2, '阿莫西林与左氧氟沙星抗菌机制重叠，不宜联用', 1),
(1, 3, 9, 2, '布洛芬与阿司匹林合用增加消化道出血风险', 1),
(1, 4, 10, 1, '两种钙通道阻滞剂重复用药', 1);

INSERT INTO rational_rule (rule_type, drug_id, max_daily_dose, warning_level, message, enabled) VALUES
(2, 4, 2.00, 1, '硝苯地平控释片每日最大剂量 2 片（60mg）', 1),
(2, 5, 4.00, 1, '二甲双胍片每日最大剂量 4 片（2g）', 1);

INSERT INTO clinical_pathway (diagnosis_code, diagnosis_name, steps, enabled) VALUES
('J06.9','上呼吸道感染','[{"day":0,"action":"LAB","item":"白细胞计数","window":24},{"day":0,"action":"MED","item":"感冒灵颗粒","window":48},{"day":3,"action":"DISCHARGE","window":24}]',1),
('E11.9','2型糖尿病','[{"day":0,"action":"LAB","item":"空腹血糖","window":24},{"day":0,"action":"MED","item":"二甲双胍片","window":48},{"day":2,"action":"LAB","item":"空腹血糖","window":24},{"day":5,"action":"DISCHARGE","window":24}]',1);
