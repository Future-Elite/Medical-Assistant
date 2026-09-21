-- 生成结果的医学一致性断言：三条都应为 0。
-- 这三条就是「妇科医生看肠胃炎」「肠胃炎用布洛芬」那类问题的机器判据。
-- 用法：MYSQL_PWD=<口令> mysql -uroot --default-character-set=utf8mb4 his_demo < backend/sql/verify-generation.sql

SELECT 'A1 诊断不属于该次就诊科室的本体' AS 断言, COUNT(*) AS 应为0 FROM diagnosis d
JOIN visit v ON d.visit_id = v.id
WHERE NOT EXISTS (SELECT 1 FROM dept_diagnosis dd
                  WHERE dd.department = v.department AND dd.icd_code = d.icd_code);

SELECT 'A2 处方药不在该(诊断,科室)的本体用药里' AS 断言, COUNT(*) AS 应为0
FROM prescription_item pi
JOIN prescription p ON pi.prescription_id = p.id
JOIN visit v ON p.visit_id = v.id
JOIN diagnosis d ON d.visit_id = v.id
WHERE NOT EXISTS (SELECT 1 FROM diagnosis_drug dg
                  WHERE dg.icd_code = d.icd_code AND dg.department = v.department
                    AND dg.drug_id = pi.drug_id);

SELECT 'A3 检验项不在该(诊断,科室)的本体检验里' AS 断言, COUNT(*) AS 应为0
FROM lab_test lt
JOIN visit v ON lt.visit_id = v.id
JOIN diagnosis d ON d.visit_id = v.id
WHERE NOT EXISTS (SELECT 1 FROM diagnosis_lab dl
                  WHERE dl.icd_code = d.icd_code AND dl.department = v.department
                    AND dl.item_code = lt.item_code);

-- A4 空处方：开了诊断却一条药都没有。
-- 起因：外科 D23.9（皮肤良性肿瘤）与 K42.9（脐疝）两组的药**整组都是 postop=1**，
-- 生成器「非术后场景排除 postop」的过滤会把池子清空。postop=1 表示「可作术后用药」，
-- 不是「仅限术后」，故池子空时必须回退全集。未回退时实测产生 2103 张空处方。
SELECT 'A4 一条明细都没有的处方' AS 断言, COUNT(*) AS 应为0
FROM prescription p
WHERE NOT EXISTS (SELECT 1 FROM prescription_item pi WHERE pi.prescription_id = p.id);

-- 分布证据：妇科到底在治什么
SELECT v.department AS 科室, d.diagnosis_name AS 诊断, COUNT(*) AS 次数
FROM diagnosis d JOIN visit v ON d.visit_id = v.id
WHERE v.department = '妇科'
GROUP BY v.department, d.diagnosis_name ORDER BY 次数 DESC LIMIT 5;

-- 分布证据：肠胃炎 A09.9 出现在哪些科室、用什么药
SELECT v.department AS 科室, COUNT(*) AS 次数
FROM diagnosis d JOIN visit v ON d.visit_id = v.id
WHERE d.icd_code = 'A09.9' GROUP BY v.department ORDER BY 次数 DESC;

SELECT dr.name AS 药名, COUNT(*) AS 次数
FROM diagnosis d JOIN visit v ON d.visit_id = v.id
JOIN prescription p ON p.visit_id = v.id
JOIN prescription_item pi ON pi.prescription_id = p.id
JOIN drug dr ON dr.id = pi.drug_id
WHERE d.icd_code = 'A09.9' GROUP BY dr.name ORDER BY 次数 DESC;

-- 一致性：admission 旧列 diagnosis 必须等于 admission_diagnosis
SELECT 'admission.diagnosis 与 admission_diagnosis 不符' AS 检查, COUNT(*) AS 应为0
FROM admission WHERE diagnosis IS NULL OR admission_diagnosis IS NULL OR diagnosis <> admission_diagnosis;

-- 总数
SELECT 'patient' AS 表, COUNT(*) AS 行数 FROM patient
UNION ALL SELECT 'visit', COUNT(*) FROM visit
UNION ALL SELECT 'diagnosis', COUNT(*) FROM diagnosis
UNION ALL SELECT 'prescription', COUNT(*) FROM prescription
UNION ALL SELECT 'prescription_item', COUNT(*) FROM prescription_item
UNION ALL SELECT 'lab_test', COUNT(*) FROM lab_test
UNION ALL SELECT 'charge', COUNT(*) FROM charge
UNION ALL SELECT 'dispense', COUNT(*) FROM dispense
UNION ALL SELECT 'admission', COUNT(*) FROM admission
UNION ALL SELECT 'medical_order', COUNT(*) FROM medical_order
UNION ALL SELECT 'vital_sign', COUNT(*) FROM vital_sign;
