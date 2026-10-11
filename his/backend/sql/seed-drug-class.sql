-- 药品药理分类（drug.drug_class）—— 同类药同方互斥
--
-- 执行：mysql --default-character-set=utf8mb4 his_demo < backend/sql/seed-drug-class.sql
--       （用户名/口令按本地部署自行补充，本脚本不固化凭据）
--
-- 前置：schema.sql（或 migrate-drug-class.sql，为已存在的库补列）+ seed.sql + seed-ontology.sql
--       都执行过——本脚本只 UPDATE 已存在的 196 行 drug，不 INSERT。
--
-- **本脚本可重复执行**（全是 UPDATE，无 INSERT/DELETE）；重复执行结果相同。
--
-- ## 这一列解决什么问题
--
-- 2026-09-22 实测：125537 条处方里有 168 条被判「两种钙通道阻滞剂重复用药」
-- （rational_rule id=8：硝苯地平 4 + 氨氯地平 10）。根因在数据——diagnosis_drug 里
-- I10 内科把这两种都列为 line=1、weight=1.000，生成器组合用药时会把两者一起选中。
-- 这不是注入的违规，是合法共处方涌现出来的，但两次 CCB 同方临床上确实不对。
--
-- 原有的 drug.category 太粗（这 29 种心血管药全归一个「心血管」），
-- 拿它去重会把「CCB + ARB」「二甲双胍 + 磺脲」这类**正确的联合用药**也误杀。
-- 故新增本列，口径取 ATC 第 4 层（同一层内的药互为同类、可替换，故不应同方并用）。
--
-- ## 口径与已知边界
--
-- · 「同类」= 同一 ATC 第 4 层。同类的药**同方互斥**；不同类可以并用。
-- · 阿司匹林与氯吡格雷/替格瑞洛**故意不同类**：双联抗血小板（DAPT）是标准治疗，
--   把它们归成一类会禁掉一个正确用法。ATC 上它们也确实分属 B01AC06 与 B01AC04/24。
-- · 对乙酰氨基酚与 NSAIDs 不同类：两者可以联用，这是常用做法。
-- · ARB 与 ACEI **归为不同类**（C09C / C09A），故缬沙坦 + 培哚普利仍可同方——
--   本表口径是「同类可替换」，不是「所有 RAAS 药互斥」；后者要靠 rational_rule 逐对声明。
-- · 中成药按功能主治归类，同一功能下算同类。
-- · 取值来源：ATC 分类（WHO Collaborating Centre）的**层次**，类名用中文习惯说法；
--   这是分类**层次**的引用，不是对具体药品 ATC 编码的逐条抄录。
--
-- 每行末尾的注释是该类的 ATC 层次参考。

-- ===== 心血管（29）=====
UPDATE drug SET drug_class = '钙通道阻滞剂' WHERE id IN (4, 10);                       -- C08
UPDATE drug SET drug_class = '血管紧张素受体拮抗剂' WHERE id IN (37, 38, 39);           -- C09C
UPDATE drug SET drug_class = '血管紧张素转换酶抑制剂' WHERE id IN (40, 41, 42);         -- C09A
UPDATE drug SET drug_class = 'β受体阻滞剂' WHERE id IN (43, 44, 45, 46);               -- C07
UPDATE drug SET drug_class = '袢利尿剂' WHERE id IN (47);                              -- C03C
UPDATE drug SET drug_class = '噻嗪类利尿剂' WHERE id IN (48, 50);                       -- C03A
UPDATE drug SET drug_class = '保钾利尿剂' WHERE id IN (49);                            -- C03D
UPDATE drug SET drug_class = '硝酸酯类' WHERE id IN (51, 52);                          -- C01D
UPDATE drug SET drug_class = '他汀类' WHERE id IN (53, 54, 55);                        -- C10A
UPDATE drug SET drug_class = '贝特类' WHERE id IN (56);                                -- C10A
UPDATE drug SET drug_class = '抗凝药' WHERE id IN (57, 60);                            -- B01A
UPDATE drug SET drug_class = '抗血小板药-环氧化酶抑制剂' WHERE id IN (9);               -- B01AC06
UPDATE drug SET drug_class = '抗血小板药-P2Y12抑制剂' WHERE id IN (58, 59);             -- B01AC04/24
UPDATE drug SET drug_class = '强心苷类' WHERE id IN (61);                              -- C01A
UPDATE drug SET drug_class = '抗心律失常药' WHERE id IN (62);                          -- C01B

-- ===== 抗感染（26）=====
UPDATE drug SET drug_class = '青霉素类' WHERE id IN (1, 32, 33, 34);                    -- J01C
UPDATE drug SET drug_class = '头孢菌素类' WHERE id IN (13, 14, 30, 31);                 -- J01D
UPDATE drug SET drug_class = '大环内酯类' WHERE id IN (15, 16, 17);                     -- J01F
UPDATE drug SET drug_class = '喹诺酮类' WHERE id IN (8, 26, 27, 28, 29);                -- J01M
UPDATE drug SET drug_class = '硝基咪唑类' WHERE id IN (18, 19);                        -- J01XD/P01AB
UPDATE drug SET drug_class = '唑类抗真菌药' WHERE id IN (20, 21);                       -- J02A
UPDATE drug SET drug_class = '抗疱疹病毒药' WHERE id IN (22, 23);                       -- J05AB
UPDATE drug SET drug_class = '抗流感病毒药' WHERE id IN (24);                          -- J05AH
UPDATE drug SET drug_class = '广谱抗病毒药' WHERE id IN (25);                          -- J05AB
UPDATE drug SET drug_class = '四环素类' WHERE id IN (35, 36);                          -- J01A

-- ===== 消化（20）=====
UPDATE drug SET drug_class = '质子泵抑制剂' WHERE id IN (6, 64, 65, 66);                -- A02BC
UPDATE drug SET drug_class = 'H2受体拮抗剂' WHERE id IN (67);                          -- A02BA
UPDATE drug SET drug_class = '抗酸药' WHERE id IN (63);                                -- A02A
UPDATE drug SET drug_class = '胃黏膜保护剂' WHERE id IN (68);                          -- A02BX
UPDATE drug SET drug_class = '促胃动力药' WHERE id IN (69, 70);                         -- A03F
UPDATE drug SET drug_class = '解痉药' WHERE id IN (71, 72);                            -- A03A
UPDATE drug SET drug_class = '微生态制剂' WHERE id IN (73, 74);                        -- A07F
UPDATE drug SET drug_class = '渗透性泻药' WHERE id IN (75, 76, 77);                     -- A06A
UPDATE drug SET drug_class = '止泻药' WHERE id IN (7);                                 -- A07B
UPDATE drug SET drug_class = '利胆药' WHERE id IN (78);                                -- A05A
UPDATE drug SET drug_class = '保肝药' WHERE id IN (79, 80, 196);                        -- A05B

-- ===== 呼吸（17）=====
UPDATE drug SET drug_class = '镇咳祛痰中成药' WHERE id IN (12, 94, 95, 96);              -- R05 中成药
UPDATE drug SET drug_class = '祛痰药' WHERE id IN (81, 82);                            -- R05C
UPDATE drug SET drug_class = '镇咳药' WHERE id IN (83);                                -- R05D
UPDATE drug SET drug_class = '复方平喘止咳药' WHERE id IN (84);                         -- R03 复方
UPDATE drug SET drug_class = '短效β2受体激动剂' WHERE id IN (85);                       -- R03AC
UPDATE drug SET drug_class = '吸入激素复方制剂' WHERE id IN (86);                       -- R03AK
UPDATE drug SET drug_class = '长效抗胆碱能吸入剂' WHERE id IN (87);                     -- R03BB
UPDATE drug SET drug_class = '白三烯受体拮抗剂' WHERE id IN (88);                       -- R03DC
UPDATE drug SET drug_class = '茶碱类' WHERE id IN (89);                                -- R03DA
UPDATE drug SET drug_class = '抗组胺药' WHERE id IN (90, 91, 92, 93);                   -- R06

-- ===== 解热镇痛（9）=====
UPDATE drug SET drug_class = '非甾体抗炎药' WHERE id IN (3, 98, 100, 101, 103);          -- M01A
UPDATE drug SET drug_class = '对乙酰氨基酚' WHERE id IN (97);                            -- N02BE
UPDATE drug SET drug_class = '选择性COX-2抑制剂' WHERE id IN (99, 102, 104);              -- M01AH

-- ===== 降糖（11）=====
UPDATE drug SET drug_class = '双胍类' WHERE id IN (5);                                 -- A10BA
UPDATE drug SET drug_class = '磺脲类' WHERE id IN (11);                                -- A10BB
UPDATE drug SET drug_class = 'α-糖苷酶抑制剂' WHERE id IN (111, 112);                   -- A10BF
UPDATE drug SET drug_class = '格列奈类' WHERE id IN (113, 114);                         -- A10BX
UPDATE drug SET drug_class = 'DPP-4抑制剂' WHERE id IN (115);                          -- A10BH
UPDATE drug SET drug_class = 'SGLT-2抑制剂' WHERE id IN (116, 117);                    -- A10BK
UPDATE drug SET drug_class = '噻唑烷二酮类' WHERE id IN (118, 119);                     -- A10BG

-- ===== 神经精神（14）=====
UPDATE drug SET drug_class = '苯二氮䓬类' WHERE id IN (123, 124);                       -- N05BA
UPDATE drug SET drug_class = '非苯二氮䓬催眠药' WHERE id IN (125, 126);                  -- N05CF
UPDATE drug SET drug_class = 'SSRI' WHERE id IN (127, 128, 129);                      -- N06AB
UPDATE drug SET drug_class = 'SNRI' WHERE id IN (130);                                -- N06AX
UPDATE drug SET drug_class = 'NaSSA' WHERE id IN (131);                               -- N06AX
UPDATE drug SET drug_class = '非典型抗精神病药' WHERE id IN (132, 133);                  -- N05AH
UPDATE drug SET drug_class = '抗癫痫药' WHERE id IN (134, 135, 136);                    -- N03A

-- ===== 泌尿（8）=====
UPDATE drug SET drug_class = 'α1受体阻滞剂' WHERE id IN (137, 139);                     -- G04CA
UPDATE drug SET drug_class = '5α还原酶抑制剂' WHERE id IN (138);                        -- G04CB
UPDATE drug SET drug_class = 'M受体拮抗剂' WHERE id IN (140, 141);                      -- G04BD
UPDATE drug SET drug_class = 'PDE5抑制剂' WHERE id IN (142);                           -- G04BE
UPDATE drug SET drug_class = '中成药-泌尿' WHERE id IN (143);
UPDATE drug SET drug_class = '碱化剂' WHERE id IN (144);                               -- G04B

-- ===== 妇科（6）=====
UPDATE drug SET drug_class = '阴道用抗真菌药' WHERE id IN (165);                         -- G01A
UPDATE drug SET drug_class = '阴道用复方抗感染药' WHERE id IN (166);                     -- G01A 复方
UPDATE drug SET drug_class = '阴道用抗滴虫药' WHERE id IN (167);                         -- G01A
UPDATE drug SET drug_class = '中成药-妇科栓剂' WHERE id IN (168);
UPDATE drug SET drug_class = '复方避孕药' WHERE id IN (169);                            -- G03A
UPDATE drug SET drug_class = '孕激素' WHERE id IN (170);                               -- G03D

-- ===== 皮肤外用（17）=====
UPDATE drug SET drug_class = '外用糖皮质激素' WHERE id IN (145, 146, 147, 148);          -- D07A
UPDATE drug SET drug_class = '外用激素抗真菌复方' WHERE id IN (149);                     -- D07C
UPDATE drug SET drug_class = '外用抗真菌药' WHERE id IN (150, 151);                     -- D01A
UPDATE drug SET drug_class = '外用抗病毒药' WHERE id IN (152);                          -- D06B
UPDATE drug SET drug_class = '外用抗菌药' WHERE id IN (153, 154, 192);                  -- D06A
UPDATE drug SET drug_class = '外用钙调神经磷酸酶抑制剂' WHERE id IN (155);               -- D11A
UPDATE drug SET drug_class = '外用止痒剂' WHERE id IN (156);                            -- D04A
UPDATE drug SET drug_class = '外用维A酸类' WHERE id IN (190, 194);                      -- D10A
UPDATE drug SET drug_class = '外用抗痤疮药' WHERE id IN (191);                          -- D10A
UPDATE drug SET drug_class = '外用维生素D衍生物' WHERE id IN (195);                      -- D05A

-- ===== 内分泌（3）=====
UPDATE drug SET drug_class = '甲状腺激素' WHERE id IN (120);                           -- H03A
UPDATE drug SET drug_class = '抗甲状腺药' WHERE id IN (121, 122);                       -- H03B

-- ===== 抗痛风（4）=====
UPDATE drug SET drug_class = '秋水仙碱' WHERE id IN (105);                             -- M04AC
UPDATE drug SET drug_class = '抑制尿酸生成药' WHERE id IN (106, 107);                   -- M04AA
UPDATE drug SET drug_class = '促尿酸排泄药' WHERE id IN (108);                          -- M04AB

-- ===== 抗风湿（2）=====
UPDATE drug SET drug_class = '抗风湿改善病情药' WHERE id IN (109, 110);                  -- L04A

-- ===== 其他（19）=====
UPDATE drug SET drug_class = '维生素C' WHERE id IN (171);                              -- A11G
UPDATE drug SET drug_class = '维生素B族' WHERE id IN (172, 173);                        -- A11E
UPDATE drug SET drug_class = '叶酸' WHERE id IN (174);                                 -- B03BB
UPDATE drug SET drug_class = '维生素D' WHERE id IN (175);                              -- A11CC
UPDATE drug SET drug_class = '钙剂' WHERE id IN (176, 177);                            -- A12A
UPDATE drug SET drug_class = '电解质补充剂' WHERE id IN (178, 179);                     -- B05B
UPDATE drug SET drug_class = '中成药-止血' WHERE id IN (180);
UPDATE drug SET drug_class = '中成药-活血化瘀' WHERE id IN (181, 182, 183);
UPDATE drug SET drug_class = '中成药-解表化湿' WHERE id IN (184);
UPDATE drug SET drug_class = '中成药-清热解毒' WHERE id IN (185, 186);
UPDATE drug SET drug_class = '前庭功能调节药' WHERE id IN (189);                        -- N07C
UPDATE drug SET drug_class = '维A酸类' WHERE id IN (193);                              -- D10BA
-- 196 复方甘草酸苷片 已随「保肝药」归入消化段

-- ===== 感冒（1）=====
UPDATE drug SET drug_class = '中成药-感冒复方' WHERE id IN (2);

-- ===== 五官科（10）=====
UPDATE drug SET drug_class = '喹诺酮滴耳剂' WHERE id IN (157);                          -- S02A
UPDATE drug SET drug_class = '喹诺酮滴眼剂' WHERE id IN (158);                          -- S01A
UPDATE drug SET drug_class = '氨基糖苷滴眼剂' WHERE id IN (159);                        -- S01A
UPDATE drug SET drug_class = '人工泪液' WHERE id IN (160);                              -- S01X
UPDATE drug SET drug_class = '鼻用糖皮质激素' WHERE id IN (161, 162);                   -- R01A
UPDATE drug SET drug_class = '口腔含漱剂' WHERE id IN (163);                            -- A01A
UPDATE drug SET drug_class = '口腔含片' WHERE id IN (164);                              -- A01A
UPDATE drug SET drug_class = '降眼压β受体阻滞剂' WHERE id IN (187);                     -- S01E
UPDATE drug SET drug_class = '前列腺素类降眼压药' WHERE id IN (188);                     -- S01E

-- 自检：应为 196（= drug 表行数），且 drug_class 不应有空白或空串。
-- 若本行报出的数字小于 196，说明上面漏了某个 id。
SELECT COUNT(*) AS 已分类药数,
       SUM(drug_class IS NULL OR TRIM(drug_class) = '') AS 未分类
FROM drug;
