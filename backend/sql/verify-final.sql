-- 最终修复验证：数据集规模、完整性、时间一致性、分布合理性
-- 用法：mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -t < verify-final.sql

SELECT '=== 0. 数据集规模 ===' AS section;
SELECT 'patient' AS t, COUNT(*) AS rows_ FROM patient
UNION ALL SELECT 'visit', COUNT(*) FROM visit
UNION ALL SELECT 'diagnosis', COUNT(*) FROM diagnosis
UNION ALL SELECT 'prescription', COUNT(*) FROM prescription
UNION ALL SELECT 'prescription_item', COUNT(*) FROM prescription_item
UNION ALL SELECT 'lab_test', COUNT(*) FROM lab_test
UNION ALL SELECT 'drug(字典)', COUNT(*) FROM drug
UNION ALL SELECT 'rule_config(字典)', COUNT(*) FROM rule_config;

SELECT '=== 1.1 引用完整性（孤儿行，全部应为 0）===' AS section;
SELECT 'visit.patient_id' AS fk, COUNT(*) AS orphans FROM visit v
  LEFT JOIN patient p ON v.patient_id = p.id WHERE p.id IS NULL
UNION ALL SELECT 'diagnosis.patient_id', COUNT(*) FROM diagnosis d
  LEFT JOIN patient p ON d.patient_id = p.id WHERE p.id IS NULL
UNION ALL SELECT 'diagnosis.visit_id', COUNT(*) FROM diagnosis d
  LEFT JOIN visit v ON d.visit_id = v.id WHERE v.id IS NULL
UNION ALL SELECT 'prescription.visit_id', COUNT(*) FROM prescription pr
  LEFT JOIN visit v ON pr.visit_id = v.id WHERE v.id IS NULL
UNION ALL SELECT 'prescription_item.prescription_id', COUNT(*) FROM prescription_item i
  LEFT JOIN prescription pr ON i.prescription_id = pr.id WHERE pr.id IS NULL
UNION ALL SELECT 'prescription_item.drug_id', COUNT(*) FROM prescription_item i
  LEFT JOIN drug dr ON i.drug_id = dr.id WHERE dr.id IS NULL
UNION ALL SELECT 'lab_test.visit_id', COUNT(*) FROM lab_test l
  LEFT JOIN visit v ON l.visit_id = v.id WHERE v.id IS NULL;

SELECT '=== 1.2 每名患者就诊次数（应 min>=5, max<=20）===' AS section;
SELECT MIN(c) AS min_visits, MAX(c) AS max_visits, ROUND(AVG(c),2) AS avg_visits,
       SUM(c < 5 OR c > 20) AS out_of_range
FROM (SELECT patient_id, COUNT(*) c FROM visit GROUP BY patient_id) x;

SELECT '=== 1.3 每张处方明细条数（应 1-3）与金额 ===' AS section;
SELECT MIN(c) AS min_items, MAX(c) AS max_items, ROUND(AVG(c),3) AS avg_items,
       SUM(c < 1 OR c > 3) AS out_of_range
FROM (SELECT prescription_id, COUNT(*) c FROM prescription_item GROUP BY prescription_id) y;
SELECT MIN(total_amount) AS min_amt, ROUND(AVG(total_amount),2) AS avg_amt, MAX(total_amount) AS max_amt,
       SUM(total_amount IS NULL OR total_amount <= 0) AS bad_amt FROM prescription;

SELECT '=== 1.4 诊断↔用药规则一致性（越界应为 0）===' AS section;
SELECT COUNT(*) AS items_total,
       SUM(FIND_IN_SET(i.drug_id, rc.drug_ids) = 0) AS violations
FROM prescription_item i
JOIN prescription pr ON i.prescription_id = pr.id
JOIN diagnosis d ON d.visit_id = pr.visit_id
JOIN rule_config rc ON rc.diagnosis_code = d.icd_code AND rc.enabled = 1;

SELECT '=== 2.1 未来时间戳（全部应为 0）===' AS section;
SELECT 'visit.visit_time' AS col, COUNT(*) AS future FROM visit WHERE visit_time > NOW()
UNION ALL SELECT 'diagnosis.diagnosed_at', COUNT(*) FROM diagnosis WHERE diagnosed_at > NOW()
UNION ALL SELECT 'prescription.prescribed_at', COUNT(*) FROM prescription WHERE prescribed_at > NOW()
UNION ALL SELECT 'lab_test.tested_at', COUNT(*) FROM lab_test WHERE tested_at > NOW()
UNION ALL SELECT 'patient.created_at', COUNT(*) FROM patient WHERE created_at > NOW();

SELECT '=== 2.2 时间链倒序（全部应为 0）===' AS section;
SELECT 'diagnosed_at < visit_time' AS chk, COUNT(*) AS bad
  FROM diagnosis d JOIN visit v ON d.visit_id = v.id WHERE d.diagnosed_at < v.visit_time
UNION ALL SELECT 'prescribed_at < diagnosed_at', COUNT(*)
  FROM prescription pr JOIN diagnosis d ON d.visit_id = pr.visit_id WHERE pr.prescribed_at < d.diagnosed_at
UNION ALL SELECT 'tested_at < prescribed_at', COUNT(*)
  FROM lab_test l JOIN prescription pr ON pr.visit_id = l.visit_id WHERE l.tested_at < pr.prescribed_at;

SELECT '=== 2.3 同患者就诊时间非严格递增（应为 0）===' AS section;
SELECT COUNT(*) AS non_increasing FROM (
  SELECT patient_id, visit_time,
         LAG(visit_time) OVER (PARTITION BY patient_id ORDER BY visit_time) AS prev
  FROM visit) z
WHERE prev IS NOT NULL AND visit_time <= prev;

SELECT '=== 2.4 病史跨度（天，应 365-1096）===' AS section;
SELECT MIN(span) AS min_days, ROUND(AVG(span),1) AS avg_days, MAX(span) AS max_days,
       SUM(span < 365 OR span > 1097) AS out_of_range
FROM (SELECT patient_id, TIMESTAMPDIFF(DAY, MIN(visit_time), MAX(visit_time)) AS span
      FROM visit GROUP BY patient_id) s;
SELECT MIN(visit_time) AS earliest_visit, MAX(visit_time) AS latest_visit,
       TIMESTAMPDIFF(MONTH, MIN(visit_time), MAX(visit_time)) + 1 AS month_count
FROM visit;

SELECT '=== 3.3.1 用药分布（按明细数降序）===' AS section;
SELECT d.name AS drug_name, COUNT(*) AS cnt
FROM prescription_item i JOIN drug d ON i.drug_id = d.id
GROUP BY d.id, d.name ORDER BY cnt DESC, d.name ASC;

SELECT '=== 3.3.2 检验异常率（设计 15%）===' AS section;
SELECT COUNT(*) AS total, SUM(is_abnormal = 1) AS abnormal,
       ROUND(100.0 * SUM(is_abnormal = 1) / COUNT(*), 2) AS abnormal_pct,
       COUNT(DISTINCT item_name) AS item_kinds FROM lab_test;

SELECT '=== 3.3.3 性别 / 科室 / 医生分布 ===' AS section;
SELECT IF(gender = 0, '男(0)', '女(1)') AS gender, COUNT(*) AS cnt,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM patient), 2) AS pct
FROM patient GROUP BY gender;
SELECT COUNT(DISTINCT department) AS depts, COUNT(DISTINCT doctor) AS doctors FROM visit;
SELECT department, COUNT(*) AS cnt FROM visit GROUP BY department ORDER BY cnt DESC;
SELECT doctor, COUNT(*) AS cnt FROM visit GROUP BY doctor ORDER BY cnt DESC;

SELECT '=== 3.3.4 就诊频率：按月 ===' AS section;
SELECT DATE_FORMAT(visit_time, '%Y-%m') AS ym, COUNT(*) AS cnt
FROM visit GROUP BY ym ORDER BY ym;

SELECT '=== 3.3.4 就诊频率：按年 ===' AS section;
SELECT YEAR(visit_time) AS yr, COUNT(*) AS visits, COUNT(DISTINCT DATE(visit_time)) AS active_days,
       ROUND(COUNT(*) / COUNT(DISTINCT DATE(visit_time)), 1) AS per_day
FROM visit GROUP BY yr ORDER BY yr;

SELECT '=== 3.3.4 单日峰值 Top 5（整日）===' AS section;
SELECT DATE(visit_time) AS day, COUNT(*) AS cnt
FROM visit GROUP BY day ORDER BY cnt DESC LIMIT 5;

SELECT '=== 3.3.4 单小时峰值 Top 5 ===' AS section;
SELECT DATE(visit_time) AS day, HOUR(visit_time) AS hour_, COUNT(*) AS cnt
FROM visit GROUP BY day, hour_ ORDER BY cnt DESC LIMIT 5;

SELECT '=== 3.3.4 日历天数 / 有就诊天数 ===' AS section;
SELECT DATEDIFF(MAX(DATE(visit_time)), MIN(DATE(visit_time))) + 1 AS calendar_days,
       COUNT(DISTINCT DATE(visit_time)) AS active_days,
       DATEDIFF(MAX(DATE(visit_time)), MIN(DATE(visit_time))) + 1
         - COUNT(DISTINCT DATE(visit_time)) AS zero_days
FROM visit;

SELECT '=== 3.3.5 身份证合规性 ===' AS section;
SELECT LENGTH(id_card) AS len, COUNT(*) AS cnt FROM patient GROUP BY len;
SELECT COUNT(*) AS total,
       SUM(id_card LIKE '110101%') AS region_ok,
       SUM(CHAR_LENGTH(id_card) = 18) AS len18,
       SUM(DATE_FORMAT(birth_date, '%Y%m%d') = SUBSTRING(id_card, 7, 8)) AS birthdate_match,
       SUM((CAST(SUBSTRING(id_card, 17, 1) AS UNSIGNED) % 2 = 1 AND gender = 0)
        OR (CAST(SUBSTRING(id_card, 17, 1) AS UNSIGNED) % 2 = 0 AND gender = 1)) AS parity_gender_ok,
       -- GB 11643 / ISO 7064 mod-11-2 校验位
       SUM(SUBSTRING(id_card, 18, 1) = ELT(
             (CAST(SUBSTRING(id_card,1,1) AS UNSIGNED)*7 + CAST(SUBSTRING(id_card,2,1) AS UNSIGNED)*9
            + CAST(SUBSTRING(id_card,3,1) AS UNSIGNED)*10 + CAST(SUBSTRING(id_card,4,1) AS UNSIGNED)*5
            + CAST(SUBSTRING(id_card,5,1) AS UNSIGNED)*8 + CAST(SUBSTRING(id_card,6,1) AS UNSIGNED)*4
            + CAST(SUBSTRING(id_card,7,1) AS UNSIGNED)*2 + CAST(SUBSTRING(id_card,8,1) AS UNSIGNED)*1
            + CAST(SUBSTRING(id_card,9,1) AS UNSIGNED)*6 + CAST(SUBSTRING(id_card,10,1) AS UNSIGNED)*3
            + CAST(SUBSTRING(id_card,11,1) AS UNSIGNED)*7 + CAST(SUBSTRING(id_card,12,1) AS UNSIGNED)*9
            + CAST(SUBSTRING(id_card,13,1) AS UNSIGNED)*10 + CAST(SUBSTRING(id_card,14,1) AS UNSIGNED)*5
            + CAST(SUBSTRING(id_card,15,1) AS UNSIGNED)*8 + CAST(SUBSTRING(id_card,16,1) AS UNSIGNED)*4
            + CAST(SUBSTRING(id_card,17,1) AS UNSIGNED)*2) % 11 + 1,
            '1','0','X','9','8','7','6','5','4','3','2')) AS checksum_ok
FROM patient;

SELECT '=== 3.3.5 出生日期分布 ===' AS section;
SELECT MIN(birth_date) AS min_birth, MAX(birth_date) AS max_birth,
       COUNT(DISTINCT birth_date) AS distinct_birthdates,
       COUNT(DISTINCT DATE_FORMAT(birth_date, '%m-%d')) AS distinct_month_days,
       MIN(TIMESTAMPDIFF(YEAR, birth_date, CURDATE())) AS min_age,
       MAX(TIMESTAMPDIFF(YEAR, birth_date, CURDATE())) AS max_age
FROM patient;

SELECT '=== 4. 时间线样例（患者 1）===' AS section;
SELECT 'VISIT' AS type, COUNT(*) AS c FROM visit WHERE patient_id = 1
UNION ALL SELECT 'DIAGNOSIS', COUNT(*) FROM diagnosis WHERE patient_id = 1
UNION ALL SELECT 'PRESCRIPTION', COUNT(*) FROM prescription WHERE patient_id = 1
UNION ALL SELECT 'LAB_TEST', COUNT(*) FROM lab_test WHERE patient_id = 1;
