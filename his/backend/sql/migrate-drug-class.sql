-- 为已存在的 his_demo 库补 drug.drug_class（同类药同方互斥用的药理分类）。
--
-- 执行：mysql --default-character-set=utf8mb4 his_demo < backend/sql/migrate-drug-class.sql
--       （用户名/口令按本地部署自行补充，本脚本不固化凭据）
--       schema.sql 已含本列，全新安装不需要本文件。
--
-- **只能执行一次**：MySQL 8 不支持 ADD COLUMN IF NOT EXISTS，重复执行会报
-- "Duplicate column name 'drug_class'" 并中止。这是预期行为，不是需要绕过的缺陷。
--
-- 本文件只加列，**不写分类值**——分类值在 seed-drug-class.sql 里（那份是幂等的 UPDATE）。
-- 分开的理由：加列是一次性结构变更，分类值是可能反复修订的字典内容，两者更新节奏不同。
--
-- 为什么需要这一列：2026-09-22 实测 125537 条处方里有 168 条被判「两种钙通道阻滞剂
-- 重复用药」（硝苯地平 + 氨氯地平）。根因是 diagnosis_drug 里 I10 内科把这两种都列为
-- line=1，生成器组合用药时两者都被选中。原有的 drug.category 太粗（29 种心血管药
-- 同属一个「心血管」），拿它去重会连「CCB + ARB」这类正确的联合用药一起误杀。
-- 口径与已知边界的完整说明见 seed-drug-class.sql 头部注释。

USE his_demo;

ALTER TABLE drug
  ADD COLUMN drug_class VARCHAR(32) NULL
    COMMENT '药理分类（ATC第4层口径）：同类药同方互斥，生成器每个分类最多取一种。取值见 seed-drug-class.sql',
  ADD KEY idx_drug_class (drug_class);
