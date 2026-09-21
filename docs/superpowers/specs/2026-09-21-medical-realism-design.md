# 医学真实感改造 —— 虚拟患者时序数据生成与测试平台 · 设计文档

- 日期：2026-09-21
- 状态：待评审
- 题目：基于模拟 HIS 系统的虚拟患者时序数据生成与测试平台设计与实现
- 前置文档：[2026-09-19 设计文档](2026-09-19-his-simulation-design.md)、[2026-09-21 二期设计文档](2026-09-21-his-extension-design.md)

## 1. 背景与目标

一、二期已交付完整门诊 + 住院闭环，以及批量数据生成器、患者时间轴、数据看板、规则校验引擎。
但生成数据的**医学真实感**存在明显缺陷，典型现象：

> 妇科医生在诊治肠胃炎；肠胃炎用布洛芬治疗。

这类现象不是偶发瑕疵，而是**生成算法的结构性结果**。本设计的目标准确地定位并消除这些结构性成因。

### 1.1 与原始课题要求的关系

本设计不是新增功能，而是**补齐原课题的既定要求**。2026-09-19 设计文档已写明：

- §2 需求 4：「就诊时间顺序正确，用**配置规则表**约束诊断↔用药合理性」
- §2 需求 6：评估维度含「数据完整性、时间一致性、**统计分布合理性**」
- §8：「内置一批真实常见诊断……及对应常用药、检验项目」「**医学逻辑**……保证诊断↔用药基本合理」

一、二期把「用配置规则表约束诊断↔用药」实现成了 `rule_config`（8 个诊断 → 药品 ID 串），
但**从未建立科室↔诊断↔年龄性别↔检验的约束**，也**从未建立患者级疾病状态**。本设计补上这两块。

### 1.2 「100% 真实」的可达边界（论文表述必须遵守）

目标是「完全模拟医院中权威医生的诊断与化验」。必须把这句话拆成可验证与不可验证两部分，
否则答辩时会被问穿：

| | 内容 | 本设计能否保证 |
|---|---|---|
| ✅ **可保证** | 零规则违背：本体全覆盖下，四类本体检查 + 三类用药安全检查全部为 0 | 能 |
| ✅ **可保证** | 每个取值都有可追溯依据：科室/诊断/药品/剂量/检验来自本体表，时间参数有来源表（§5.3） | 能 |
| ✅ **可保证** | 分布符合公开统计：年龄性别就诊率、慢病随访频次、疾病季节峰 | 能 |
| ❌ **不能保证** | 与某家真实医院的经验分布**逐维一致** | 无真实数据可对标 |
| ❌ **不能保证** | 共病交互、并发症演进、罕见病、医生的个体化决策风格 | 本体规模与设计目标不支持 |
| ❌ **不能保证** | 化验值分布与真实实验室一致 | 参考范围用了，但分布族是假设（§5.3） |

因此论文里的准确表述是「**医学规则一致 + 分布有据**」，
**不能**写成「与真实医院数据不可区分」。后者的验证需要真实数据（如 MIMIC，需 credentialed 资质）
与正式统计检验（TSTR、判别器 AUC、专家盲评 ICC），属「局限与未来工作」。

## 2. 现状诊断（问题成因）

三条根因，逐条对应现有代码事实：

### 根因 ①：没有医学本体，各维度独立均匀抽取

`DataGeneratorService` 中，诊断、科室、医生、药品、检验项目分别从**全局池**独立抽取：

- 科室/医生：按 `HOME_DEPT_RATIO=0.8` 从 6 个科室里随机挑，**与本次诊断无关**
- 药品：按 `rule_config` 查诊断→药名串，但 `rule_config` 只有 8 条；未命中时回退到**药品全字典随机**（含 `VIOLATION_RATE=0.03` 的注入分支）
- 检验：从 `DATA_TABLES` 里的全局检验池抽，**与诊断无关**

因此「妇科医生看肠胃炎」是**必然**出现的，不是偶然：科室与诊断之间根本没有连边。

### 根因 ②：没有患者级疾病模型

患者只是「一串就诊记录的容器」。每次就诊的诊断重新随机，
所以**同一位患者不会反复因同一慢病就诊**——而这恰恰是「虚拟患者**时序数据**」课题的核心。
当前数据在时间维度上只有「单次门诊事件链」和「住院纵向数据」，
缺少「以人为单位的疾病轨迹」。

### 根因 ③：字典过窄且自相矛盾

- `seed.sql` 仅 **8 个诊断 / 12 种药品 / 6 个科室 / 24 位医生**，而目标是 ≥1 万患者、≥5 万就诊记录。
  平均每个诊断要重复数千次，分布上必然露馅。
- 8 个诊断**全部属于内科系**（上呼吸道感染、急性支气管炎、高血压、2 型糖尿病、慢性胃炎、急性胃肠炎、偏头痛、发热），
  外科/妇科/儿科/五官科/皮肤科**根本没有对应的诊断可开**——它们只能去看内科的 8 个病。
- `rule_config` 自相矛盾：`I10 原发性高血压 → '4,9,10'` 同时含硝苯地平(4) 与氨氯地平(10)，
  而 `seed-extension.sql` 的 `rational_rule` 明写 (4,10) 是重复用药相互作用——**生成器的映射表违反了校验器自己的规则**。

### 根因 ④（补充事实）

- `newPatient()` 用 `rnd.nextInt(18, 90)`：**全部患者 18~89 岁**，因此儿科只能看成年人。
- `上呼吸道感染` 在 `seed-extension.sql` 里配了 `{"day":3,"action":"DISCHARGE"}` 的住院路径：
  一个门诊疾病被建模成 3 天住院。
- 剂量完全忽略药品/年龄/体重：硝苯地平控释片 30mg「2 片 每日 2 次」= **120 mg/日，远超其常规日上限**。
- `VIOLATION_RATE=0.03` 注入的违规**不带任何标记**，因此「设计内的异常」与「真实 bug」在数据上无法区分。

**根因 ④ 直接解释了「肠胃炎→布洛芬」的现象**：它并不是映射表写错了
（`rule_config` 里 `A09.9 急性胃肠炎 → '7,6'`，即蒙脱石散 + 奥美拉唑，是正确的），
而是 3% 注入分支的产物。该机制本次**整体删除**（§7），不是加标记。

### 根因 ⑤：化验项目与诊断完全无关，且异常方向单一

`DataGeneratorService.java:450-468`（门诊）、`641-654`（住院）、`656-665`（路径）三处化验生成：

| 缺陷 | 代码事实 | 后果 |
|---|---|---|
| 与诊断**零关联** | 第 454 行 `MedicalData.LAB_ITEMS.get(rnd.nextInt(size()))`——全局均匀随机抽 | `MedicalData.LAB_ITEMS` **总共只有 5 项**（红细胞计数/白细胞计数/空腹血糖/血红蛋白/血小板计数）。2 型糖尿病有 80% 概率查不到血糖；急性胃肠炎几乎查不到白细胞 |
| 异常方向**恒为偏高** | 第 460 行 `val = li.high() + rnd.nextDouble() * li.high() * 0.3` | 只向上加、从不向下减。**贫血（血红蛋白偏低）、低血糖、低钾这类结果物理上不可能出现** |
| 异常概率**与疾病无关** | 第 459 行 `rnd.nextDouble() < 0.15` 写死 | 糖尿病人的空腹血糖与健康人的血小板，异常率都是 15% |
| 路径检验**恒为正常** | 第 665 行 `lt.setIsAbnormal(0)` | 2 型糖尿病临床路径 day 2 复查空腹血糖，结果永远「正常」——路径数据自相矛盾 |

**外部依据**：多篇综述一致指出该领域「对**时序依赖**与**临床真实性**关注严重不足」
（见 §9.3），本根因与根因 ② 共同构成该缺口在本项目中的具体体现。

## 3. 范围

### 3.1 本设计做什么

1. **医学知识本体**（新增 5 张表）：科室↔诊断↔药品↔检验↔年龄性别 的约束与权重。
2. **患者级疾病轨迹**：患者疾病档案（慢病终身携带 + 急性病倾向）+ 每患者事件日历。
3. **生成链改造**：从「各维独立均匀抽取」改为「按本体逐层条件抽样」。
4. **删除违规注入**：生成数据在医学规则上 100% 合规。
5. **升级并扩充校验器**：既有合理用药校验换用新本体作数据源，另新增 4 类本体一致性检查，产出量化一致率。

### 3.2 本设计不做什么，以及为什么

| 不做 | 原因 |
|---|---|
| **用大模型直接生成数据** | ① 一次生成 5 万条记录的成本与耗时不可接受；② LLM 输出的分布不可控、不可复现，与「可复现的测试平台」矛盾；③ 无 ground truth 可验证其正确性。**LLM 只在离线阶段辅助起草知识库条目**（见 §9） |
| **训练 ML 生成模型（GAN/VAE/扩散）** | 没有真实数据作为训练目标。用自己模拟器生成的数据训练，学到的只是自己已有的偏差——论文里这叫 **model collapse / 自我蒸馏**，无法提升真实感 |
| **训练强化学习** | RL 需要一个外部奖励信号。这里唯一的奖励只能来自「是否符合医学常识」——而那正是本设计的**规则层**本身。用规则当奖励再学一个策略，等于把确定性的规则近似成一个不可解释、可能失真的模型，**纯属绕远路**。此结论纳入论文「局限与未来工作」 |
| **引入受限医学数据源** | SNOMED CT、UMLS、DrugBank、MIMIC 均需授权/资质，不可用于公开毕设。见 §9 |
| **接入真实排班/预约池** | 与二期结论一致：对时序加成小（原设计文档 §2 已排除） |

**结论**：走 **本体/指南约束抽样** 路线（即 Synthea 的做法），并显式输出违规率作为诚实性证据。

## 4. 数据模型（5 张新表）

统一约定沿用一、二期：时间字段 `DATETIME`、主键 `BIGINT AUTO_INCREMENT`、
外键列与查询列建索引、字符集 `utf8mb4`。

**关键设计决定**：同一 `icd_code` 允许出现在多行（不同科室 + 不同年龄段）。
例如 `J06.9 上呼吸道感染` 同时有「儿科 / 0~14 岁 / weight 高」与「内科 / 15~89 岁 / weight 中」两行。
生成器先按患者年龄性别筛出**适用行**，再按 `weight` 加权抽科室。这解决了「儿童上感去内科」的问题，
且不需要为儿童单独造一套 ICD 编码。

### 4.1 `dept_diagnosis` 科室↔诊断本体（核心表）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT PK AI | |
| `department` | VARCHAR(32) | 科室名，与 `doctor.department` / `visit.department` 取值域一致 |
| `icd_code` | VARCHAR(16) | ICD-10 国家临床版编码 |
| `diagnosis_name` | VARCHAR(64) | 诊断名 |
| `gender` | TINYINT | 0=不限 1=仅男 2=仅女。产科/妇科诊断置 2，男患者永不抽到 |
| `min_age` | INT | 适用年龄下限（含） |
| `max_age` | INT | 适用年龄上限（含） |
| `chronic` | TINYINT | 1=慢病（可被患者终身携带） |
| `admission_eligible` | TINYINT | 1=可收住院。`J06.9` 置 0 |
| `weight` | DECIMAL(6,3) | 同科室同年龄段内的相对常见度，>0 |
| `month_weights` | JSON | 长度 12 的正数数组，季节乘子；全 1 表示无季节性 |
| `prevalence` | DECIMAL(6,4) | 慢病的年龄/性别基线患病率，建档时按此抽样；非慢病置 0 |

索引：`idx_dd_dept(department)`、`idx_dd_icd(icd_code)`、`idx_dd_chronic(chronic)`

### 4.2 `diagnosis_drug` 诊断→药品

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT PK AI | |
| `icd_code` | VARCHAR(16) | |
| `drug_id` | BIGINT | 外键 → `drug.id` |
| `line` | TINYINT | 1=一线 2=二线 3=辅助用药 |
| `weight` | DECIMAL(6,3) | 同一线内相对权重 |

索引：`idx_ddg_icd(icd_code)`、`idx_ddg_drug(drug_id)`

**约束**：同一 `icd_code` 下的一线药物组合，**不得**违反 `rational_rule` 中的相互作用/重复用药规则。
这是必须用测试卡住的点（见 §11）。`I10 → '4,9,10'` 的现状即违反此项。

### 4.3 `diagnosis_lab` 诊断→检验

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT PK AI | |
| `icd_code` | VARCHAR(16) | |
| `item_code` | VARCHAR(32) | 检验项目编码（LOINC 中文版风格，如 `WBC`、`CRP`、`GLU`） |
| `item_name` | VARCHAR(64) | 项目名 |
| `unit` | VARCHAR(16) | 单位 |
| `ref_range` | VARCHAR(32) | 参考范围文本 |
| `abnormal_direction` | VARCHAR(8) | `HIGH` / `LOW` / `NONE` |
| `abnormal_prob` | DECIMAL(4,3) | 该病该项目的异常概率，0~1 |
| `weight` | DECIMAL(6,3) | 该项目被开立的相对权重 |

索引：`idx_ddl_icd(icd_code)`

**本表是修掉根因 ⑤ 的关键**，四条硬性要求：

1. 项目必须**按诊断挂接**，不再存在全局随机抽取的检验池。
2. `abnormal_direction` 必须支持 `LOW`，且**每个诊断至少有一条 `LOW` 或 `NONE` 的可选项目**，
   杜绝「只会偏高」——贫血、低血糖、低钾类结果必须可能生成。
3. `ref_range` 与 `abnormal_direction` 必须自洽：方向为 `HIGH` 时参考范围上界须低于生成值域下界
   （即异常值确实越界），由 §11 的一致性测试卡住。
4. **临床路径里的检验同样受本表约束**：`labTestForItem` 必须按该诊断的 `abnormal_direction`/`abnormal_prob`
   生成结果，**不得再写死 `isAbnormal(0)`**。糖尿病路径复查空腹血糖必须可以是异常。

**规模要求**：项目池扩到 **30~50 项**（覆盖血常规、CRP、肝肾功能、电解质、血糖、尿常规、心肌酶、
凝血、甲功等常用组套），其中每个诊断挂 2~8 项。

### 4.4 `drug_dose` 药品剂量规则

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT PK AI | |
| `drug_id` | BIGINT | 唯一，外键 → `drug.id` |
| `single_dose` | DECIMAL(8,2) | 单次剂量 |
| `dose_unit` | VARCHAR(16) | 剂量单位 |
| `frequency` | VARCHAR(16) | 频次，如「每日2次」 |
| `max_daily_dose` | DECIMAL(8,2) | 日上限，生成器**必须**保证不超过 |
| `days_min` / `days_max` | INT | 疗程天数区间 |

索引：`uk_drug_dose(drug_id)` 唯一

**约束**：`single_dose × 每日次数 ≤ max_daily_dose`，在种子数据层面即校验，且生成器再次兜底。

**与既有 `rational_rule` 剂量规则的口径对齐（必须遵守）**：
`rational_rule` 已有 `rule_type=2` 的剂量规则，字段为 `(drug_id, max_daily_dose)`，
**单位是「片/日」**（现有两行：硝苯地平 8.00、二甲双胍 8.00）。
因此 `drug_dose` 的口径固定为：

- `single_dose` 以**最小可分割给药单位**计（片 / 粒 / 袋 / 支），与 `dose_unit` 一致；
- `max_daily_dose` 同样以该单位计，**与 `rational_rule.rule_type=2` 的 `max_daily_dose` 直接可比**；
- 二者对同一 `drug_id` 的取值**必须相等**，由 §11 的一致性测试卡住。

**顺带修正的既有缺陷**：现有 `rational_rule` 对硝苯地平设 8 片/日（=240 mg，控释片常规上限 30~60 mg/日）
**过松**，导致「2 片 每日 2 次」（4 片/日）在规则上「合法」而在临床上不合理。
本次起草种子时按**公开药品说明书**收紧该阈值，并同步更新 `rational_rule` 对应行。

### 4.5 `visit_rate` 就诊率配置

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT PK AI | |
| `age_min` / `age_max` | INT | 年龄段 |
| `gender` | TINYINT | 0=不限 1=男 2=女 |
| `multiplier` | DECIMAL(6,3) | 该段就诊率乘子（相对基准） |

索引：`idx_vr_age(age_min, age_max)`

## 5. 患者级疾病轨迹模型

这是本设计的核心，直接补上课题「虚拟患者**时序数据**」的空白。

### 5.1 建档（patient 生成时）

现在的建档只抽年龄性别。改造后：

1. **年龄**：改为覆盖 `0~89`（`rnd.nextInt(0, 90)`）。这是儿科能有真实患者的前提。
2. **疾病档案**：按 `dept_diagnosis` 中 `chronic=1` 且年龄性别适用的行，
   以 `prevalence` 为权重抽 **0~2 个**慢病。慢病一旦抽中，**终身携带**，写入患者档案。
3. **急性病倾向** `acutePropensity`：抽一个 `[0.5, 2.0]` 的乘子，决定该患者急性就诊的疏密。

> 患者档案在生成器内存中维护（`Map<Long, PatientProfile>`），不落库。
> **理由**：它是生成过程的中间状态，不是 HIS 业务数据；落库会污染数据集、增加表与迁移成本。

### 5.2 事件日历（替代窗口等分槽位）

当前算法把时间窗口等分后每槽位塞一次就诊。这导致就诊间隔机械均匀、
且「下一次什么时候来」与「这个人有什么病」无关。改为**每患者一份事件日历**：

```
nextEvent ← max(出生日, 首个事件抽样的起点)
while nextEvent < now:
    以 nextEvent 为锚点抽事件类型：
      · 慢病随访  —— 从该患者的慢病档案里选一个
                    间隔 ~ Lognormal(中位数 91 天, σ=0.5)
      · 急性发作  —— 从「该患者年龄性别适用、且 month_weights[当月] > 0」的急性病池中
                    按 weight × month_weights[当月] × acutePropensity 加权抽
      · 其他就诊  —— 低频（体检复查等），从低权重池抽
    生成一次就诊记录（见 §6）
    nextEvent ← 就诊时间 + 本次事件的「到下次事件」间隔
    若本次是急性病：以 10~20% 的概率在 3~7 天后追加一次复诊事件
```

**效果**：每次就诊的诊断**来自该患者自己的疾病池**。
「同一位患者反复因同一慢病就诊」自然出现——这正是时序数据课题要的纵向特征，当前完全缺失。

### 5.3 时间参数的来源标注

设计文档与论文必须区分「有出处」与「假设」：

| 参数 | 值 | 来源 |
|---|---|---|
| 慢病随访间隔中位数 | 91 天（≥4 次/年） | 《国家基本公共卫生服务规范（第三版）》 |
| 人均年就诊次数 | 6.2 次/年 | 卫健委统计公报（2019） |
| 年龄就诊率形状 | U 形（幼儿与老年高） | 卫健委统计公报年龄别数据 |
| 女性就医率偏高 | 30%~46% | 卫健委统计公报性别别数据 |
| 就诊星期分布 | 周一峰、周末谷 | 中文文献/技术博客（**需逐条核实**） |
| 疾病季节峰 | 各病种 `month_weights` | 临床路径/文献，逐病种标注 |
| 随访间隔分布族与 σ | Lognormal，σ=0.5（约 50% 的随访落在 61~136 天） | **假设**，需做敏感性分析 |
| 急性病复诊概率（10~20%）与间隔（3~7 天） | 区间值 | **假设** |
| 日内到达分布 | 分段常数（上午峰） | **假设** |
| 环节耗时 | 挂号 10min / 候诊 30min / 面诊 5min / 取药 10min | 中文三级医院测量（**需逐条核实**） |

**规则**：标「假设」的参数必须在论文里明确标注为假设，并做**敏感性分析**（至少对随访间隔中位数与复诊概率做两档对照），
不得以文献口吻陈述。标「需逐条核实」的条目，引用前必须打开原文确认。

## 6. 生成链改造

### 6.1 逐层条件抽样

```
患者
  → 本次事件的疾病（来自该患者疾病档案 / 事件日历）
  → 诊断 = 该疾病
  → 科室 = dept_diagnosis.department（由诊断决定，不再随机）
  → 医生 = 该科室的医生（从 doctor 表按 department 过滤）
  → 药品 = diagnosis_drug(诊断) 按 line 优先、weight 加权抽 1~3 种
  → 剂量 = drug_dose(药) → single_dose / frequency / 疗程天数
  → 检验 = diagnosis_lab(诊断) 按 weight 抽 0~n 项；
           结果值 = 参考范围 ∩ (abnormal_prob 命中 ? 异常方向 : 正常)
  → 时间 = 事件日历决定「哪天」，再叠加：
           星期乘子 × 日内到达分布 → 挂号时刻
           环节耗时级联 → 各环节时刻
```

### 6.2 住院域的约束

- 仅 `admission_eligible=1` 的诊断可产生 `admission`。`J06.9 上呼吸道感染` 置 0。
- **入院诊断延续该患者本次门诊的诊断**，不再独立抽取。
- 医嘱药品来自该诊断的 `diagnosis_drug`，不再从全字典随机。
- 出院时间受该诊断对应的 `clinical_pathway` 约束（若存在路径）。

### 6.3 保留但收敛的行为

- `HOME_DEPT_RATIO` 的存在意义消失（科室由诊断决定），移除。
- `rule_config` 表：**保留**（前端「规则配置」页在用），但**生成器不再以它为主**——
  改为优先查 `diagnosis_drug`。二者的一致性由 §11 的一致性测试卡住。

## 7. 删除违规注入

现状：`DataGeneratorService` 以 `VIOLATION_RATE=0.03` 的概率调用 `injectViolation(...)`，
向处方里塞入相互作用 / 超剂量 / 适应症不符的药品（正是「肠胃炎→布洛芬」的来源）。

**本次改造删除该机制。** 生成数据在医学规则上**100% 合规**，不再有「设计内的异常」。

删除范围（已核对代码）：

| 位置 | 内容 |
|---|---|
| `DataGeneratorService.java:57-58` | 常量 `VIOLATION_RATE` 及其注释 |
| `DataGeneratorService.java:226-228` | 调用点 `if (rnd.nextDouble() < VIOLATION_RATE) injectViolation(...)` |
| `DataGeneratorService.java:709-745` | 方法 `injectViolation(...)` |
| `DataGeneratorServiceTest.java:421` | 测试 `injectViolation_produces_a_known_violation` |
| `DataGeneratorServiceTest.java:107` | 仅为违规注入测试而存在的辅助方法注释（若无其他使用则一并删除） |
| `DataGeneratorService.java:232` | 「违规注入后 drugId 可能指向规则外药品」的注释改为如实描述 |

**连带简化**：删除后处方明细的 `drugId` 恒来自本体的 `diagnosis_drug`，
因此 `prescription.totalAmount` 的计算不再需要「缺药/缺价按 0 计」的防御分支——
但**该防御保留**（数据质量兜底仍需要，只是理由变了）。

## 8. 校验器：升级数据源 + 新增本体一致性检查

### 8.1 必须先修的一处隐患（否则论文章节的核心数字是空的）

现有 `RationalDrugValidator.java:84-93` 的**适应症检查只在诊断命中 `rule_config` 时才执行**：

```java
Set<Long> allowed = diagCode == null ? null : allowedByDiagnosis.get(diagCode);
if (allowed != null) { ... }   // 未命中则整段跳过
```

`rule_config` 只有 8 个诊断。本次要扩到 60~80 个诊断，若校验器仍读 `rule_config`，
**绝大多数处方会被静默跳过**，「合理用药率」将是一个没有检查过的 100%——
这在答辩时是致命的。

因此校验器必须同步换成读新本体。

### 8.2 `RationalDrugValidator`（既有，升级数据源）

| 检查 | 现状数据源 | 改为 |
|---|---|---|
| `INDICATION` 适应症 | `rule_config`（8 条，未命中则跳过） | `diagnosis_drug`（全量），且**未命中即视为无法判定**并计入 `unchecked` 单独报出，不得伪装成通过 |
| `DOSE` 剂量上限 | `rational_rule.rule_type=2` | `drug_dose`（与前者由 §11 一致性测试保持相等） |
| `INTERACTION` 相互作用 | `rational_rule.rule_type=1`（3 条） | 保留该表，但**必须扩充规则行数**——现有 3 条针对旧 12 种药，扩到 150~250 种药后若不加规则，相互作用检查等于不存在 |

`DOSE` 检查另有一处粗糙实现需修：`parseInt(it.getDosage()) * parseInt(it.getFrequency())`
（`RationalDrugValidator.java:110`）把剂量与频次当纯数字解析，遇到「4片」「每日3次」这类文本会失败。
改为按 `drug_dose` 的结构化字段判定，而非解析自由文本。

### 8.3 新增 `OntologyConsistencyValidator`（4 类本体侧检查）

与 `RationalDrugValidator` 同包同级，**只做本体一致性、不重复用药安全**（避免与 8.2 重复计数）：

| # | 检查 | 违规判定 |
|---|---|---|
| 1 | 科室 ↔ 诊断 | 该诊断不属于该医生的科室（按 `dept_diagnosis` 判定；同 ICD 多科室时取并集） |
| 2 | 年龄/性别 ↔ 诊断 | 患者年龄不在 `[min_age, max_age]`，或性别与 `gender` 冲突 |
| 3 | 检验 ↔ 诊断 | 该项目不在该诊断的 `diagnosis_lab` 中 |
| 4 | 住院 ↔ 诊断 | 入院诊断为 `admission_eligible=0` |

输出结构（新增进 `ValidationReport`）：

```json
"ontology": {
  "checked": 0, "violations": 0, "consistentRate": 0.0,
  "byType": { "DEPT_DIAGNOSIS": 0, "AGE_GENDER": 0, "LAB_DIAGNOSIS": 0, "ADMISSION": 0 }
}
```

在 `RationalDrugReport` 中增补被跳过的口径：

```json
"unchecked": 0   // 诊断不在本体中、无法判定的处方数。必须为 0，否则说明本体没覆盖全
```

**判据**：改造完成后，在本体覆盖率为 100% 的前提下，
`ontology.violations == 0`、`rational.unchecked == 0`、`rational.violations == 0`。
三者任一非 0，都说明本体或生成器存在**真实缺陷**——这正是该局面下校验器的价值。

### 8.4 校验器的演示路径（删掉注入后如何演示「能检出问题」）

生成数据恒合规，意味着校验器在校验生成数据时恒返回 0。答辩时可能被问「那它到底有用吗」。
三条实证路径，都不依赖注入：

1. **单元测试正例**：对每一类检查构造一条**已知违规**记录，断言被正确分类到对应 `byType`（见 §11）。
2. **人工录入路径**：`EntryForm.vue` 是人工写入入口。演示时人工录入一条错误处方（如给孕妇开禁忌药），
   在 `ValidationReport.vue` 上实时看到被检出。这是最直观的活演示。
3. **外部数据接入**：平台定位是「测试平台」——校验器的服务对象是**任何**接入的数据，
   不限于自己生成的数据。生成数据校验为 0 只说明生成器可信，不说明校验器无用。

看板新增「医学合理性」指标卡：本体一致率 + 四类违规明细 + 合理用药率 + 未判定数。

## 9. 知识库数据来源与授权

全部为**公开可引用**来源，不涉及任何受限数据。

| 来源 | 用途 | 许可/性质 |
|---|---|---|
| 卫健委**临床路径**（224 病种） | 科室归属、检查项、用药 | 政府公开 |
| **ICD-10 国家临床版** | 诊断编码与名称 | 政府公开 |
| **国家医保药品目录（2024）** | 药品通用名与剂型 | 政府公开 |
| 《国家基本公共卫生服务规范（第三版）》 | 慢病随访频次 | 政府公开 |
| 卫健委**统计公报** | 就诊率、年龄/性别分布 | 政府公开 |
| **Synthea** 疾病模块（Apache-2.0） | 关系结构参考（不直接复制数据） | Apache-2.0 |
| **中国药品说明书**（公开） | 单次剂量、频次、日上限 | 公开 |
| **大模型离线辅助起草** | 60~80 诊断的候选条目填写 | **必须标注**，人工审校后入库 |

**不可用**：SNOMED CT（需成员国授权）、UMLS（需许可）、DrugBank（商业许可）、
MIMIC（需 credentialed 资质）、CMeKG（授权不明）。

### 9.1 录入方式（已定）

由我方按上表来源**直接起草**全部 60~80 诊断的 SQL 种子，**每条注明来源**，
提交作者**通读审校**后入库。作者保留最终判定权。
大模型只用于「按来源整理条目」这一确定性工作，**不用于产生医学事实**。

### 9.2 规模与分布

- **诊断**：60~80 个，覆盖 6 个科室，每科 8~15 个
  - 内科（含慢病：高血压、2 型糖尿病、冠心病、慢阻肺、慢性胃炎……）
  - 外科（阑尾炎、胆囊炎、腹股沟疝、骨折……）
  - 儿科（上呼吸道感染、手足口病、支气管肺炎、腹泻病……）
  - 妇科（阴道炎、盆腔炎、月经失调、子宫肌瘤……）
  - 五官科（急性咽炎、中耳炎、过敏性鼻炎、结膜炎……）
  - 皮肤科（湿疹、荨麻疹、痤疮、带状疱疹……）
- **药品**：150~250 种，扩充 `drug` 字典
- **慢病**：≥10 个，带 `prevalence`
- **检验项目**：**30~50 项**，按诊断挂接（要求见 §4.3；现状仅 5 项且与诊断无关）

### 9.3 算法参考的公开实现与文献（含核实状态）

**必须先说清的现状**：在本设计提出之前，`DataGeneratorService` 的生成算法
**没有参考任何论文或公开实现**。全部参数（`HOME_DEPT_RATIO=0.8`、`VIOLATION_RATE=0.03`、
每人 5~20 次就诊、住院 3~15 天、化验异常率 15%）均为手拍常数，无出处。
这是本项目**方法论章节的既有缺口**，本设计通过引入下述参照来补。

#### 算法参照的公开实现

| 项目 | 许可 | 参照的具体内容 | 核实状态 |
|---|---|---|---|
| **Synthea**（MITRE） | Apache-2.0 | 疾病模块 = 状态机；`Delay` 状态是唯一耗时原语；每患者事件日历；Encounter 记录；模块内临床逻辑。本设计 §5 的事件日历与慢病终身携带在结构上即此模型 | 架构与许可是**凭已有知识陈述，仓库未打开**。使用前须确认许可条款 |

> **许可注意**：若从 Synthea 的模块文件中**直接提取关系数据**（如某病的用药/检查清单），
> 须保留其 Apache-2.0 署名。本设计要求**只参照方法、不复制数据**——关系数据一律取自 §9 的公开政府来源，
> 以规避署名与合规争议。

#### 文献

核实手段：arXiv 官方 API + Crossref CSL 元数据 + PubMed E-utilities（本机 `curl` 可用；
`WebFetch` 被企业网络策略拦截）。已下载全文的位于 `docs/references/papers/`，
完整清单与说明见 [`docs/references/README.md`](../../references/README.md)。

| 文献 | 用途 | 核实状态 |
|---|---|---|
| Walonoski J, Kramer M, Nichols J, et al. Synthea: An approach, method, and software mechanism for generating synthetic patients and the synthetic electronic health care record. *JAMIA*. 2018;25(3):230-238. doi:10.1093/jamia/ocx079. PMID:29025144 | §5 患者级疾病轨迹的方法论出处 | **元数据已核实**（PubMed：标题/期刊/2018-03-01/卷25/期3/页230-238/10 位作者逐字一致）。**但 `isOpenAccess: N`，原文未读到**——架构描述来自既有知识，引用具体机制前须取原文确认 |
| Esteban C, Hyland SL, Rätsch G. Real-valued (Medical) Time Series Generation with Recurrent Conditional GANs. arXiv:1706.02633, 2017 | **TSTR 评估范式的原始出处** | 全文已下载并核对（13 页，标题/作者/摘要一致） |
| Kartoun U. A Methodology to Generate Virtual Patient Repositories. arXiv:1608.00570, 2016 | 虚拟患者库的早期方法 | 全文已下载并核对（10 页） |
| Chen X, Wu Z, Shi X, Cho H, Mukherjee B. Generating Synthetic EHR Data: a Methodological Scoping Review with Benchmarking on Phenotype Data and Open-Source Software. arXiv:2411.04281, 2024 | 方法五分类（规则驱动 / GAN / VAE / Transformer / 扩散） | 全文已下载并核对（80 页） |
| Miletic M, Sariyar M. Synthetic data generation methods for longitudinal and time series health data: a systematic review. *BMC Med Inform Decis Mak*. 2026;26:30. doi:10.1186/s12911-025-03326-8 | **§1.1 立论缺口** | 全文已下载并核对（开放获取，24 页）。**可直接引用** |
| Snoke J, Raab GM, Nowok B, Dibben C, Slavkovic A. General and specific utility measures for synthetic data. *JRSS-A*. 2018;181(3):663-688. doi:10.1111/rssa.12358 | pMSE 用于合成数据及其零假设分布（§12 量化口径） | 期刊版付费墙；已下载 **arXiv 预印本 1604.06651** 并核对五位作者一致 |
| Woo MJ, Reiter JP, Oganian A, Karr AF. Global Measures of Data Utility for Microdata Masked for Disclosure Limitation. *J Privacy and Confidentiality*. 2009;1(1). doi:10.29012/jpc.v1i1.568 | **pMSE 最早出处** | 全文已下载并核对（开放获取，14 页） |
| Raab GM, Nowok B, Dibben C. Guidelines for Producing Useful Synthetic Data. arXiv:1712.04078, 2017 | 合成数据效用评估的实操建议 | 全文已下载并核对（第一作者是 Raab，非 Snoke） |
| Uppalapati K, Abdulkareem S, Yimenicioglu B, et al. RareGraph-Synth: Knowledge-Guided Diffusion Models for Generating Privacy-Preserving Synthetic Patient Trajectories in Ultra-Rare Diseases. arXiv:2510.06267, 2025 | **同类工作**（KG 约束生成）——§3.2 与相关工作定位 | 全文已下载并核对（PDF 元数据未改 IEEE 模板，正文标题作者一致） |
| Wang H, Zhao Y, Zheng Y, Wu X. RareSyn: Health Record Synthesis for Rare Disease Diagnosis. EMNLP 2025 | 同类工作（KG + LLM 合成 EHR） | 全文已下载并核对（17 页） |
| Ahmed MS, Dusanic M, Kirschner MN, et al. The Provenance Gap in Clinical AI: Evidence-Traceable Temporal Knowledge Graphs for Rare Disease Reasoning. arXiv:2604.17114, 2026 | 同类工作（时序知识图谱） | 全文已下载并核对（66 页） |
| Sherafatmandjoo H, Akbari M, Rahmati Z. Semantic Reasoning in Medicine: The Role of Knowledge Graphs Across Five Key Domains. arXiv:2606.15155, 2026 | 背景：医学知识图谱综述 | 全文已下载并核对（25 页） |
| Budu, Etminani, Soliman, et al. Evaluation of synthetic electronic health records: A systematic review and experimental assessment. *Neurocomputing*. 2024;603:128253. doi:10.1016/j.neucom.2024.128253 | 评估指标分类（resemblance / utility / privacy / cost） | 元数据已核实（Crossref：2024-10/卷603/页128253）。**付费墙，原文未读到** |
| Ghosheh, Li, Zhu, et al. A Survey of Generative Adversarial Networks for Synthesizing Structured Electronic Health Records. *ACM Computing Surveys*. 2024;56:1-34. doi:10.1145/3636424 | GAN 路线代表综述 | 元数据已核实（Crossref：2024/卷56/页1-34）。**付费墙，原文未读到** |

#### 已剔除的检索错配（不得引用）

搜索引擎给出的两条关联是**错的**，最初转述时也是错的，现作废并留档警示：

| 被误传为 | 实际是 | 判定 |
|---|---|---|
| doi:10.1186/s13040-026-00590-w「用 SNOMED/UMLS 校验生成序列的框架」 | 解析到**完全不同的论文**：*Explainable apprenticeship learning for hierarchical clinical decision modeling with multimodal alignment and safety-aware feedback*，*BioData Mining* | **不得引用** |
| arXiv:2406.09235「TSTR 相关」 | *Variational Mode Decomposition as Trusted Data Augmentation in ML-based **Power System** Stability Assessment*——电力系统论文 | **不得引用**；TSTR 用 Esteban 2017 |

**未核实 1 条**：中国专利 CN121938538A（医学知识图谱约束的患者数据生成方法）。专利库未能检索，若要用请自行到国家知识产权局确认。

#### 三条硬规则

1. **元数据已核实 ≠ 读过原文。** 上表中标注「付费墙，原文未读到」的三条
   （Synthea / Budu / Ghosheh），**不得**据以陈述其具体机制或结论。
   尤其 Synthea：本设计对状态机、`Delay` 状态的描述来自既有知识，
   **论文里写「Synthea 采用 X 机制」之前必须先取得原文**。
2. **不得从搜索摘要直接引文献。** 上节两条错配即为反例。
3. **本设计不依赖任何未读到原文的文献。** §3.2 排除 ML/RL 的论证是**自洽推理**
   （无真实数据 → 无奖励信号 → 只能拟合自己的模拟器），
   即使上表文献全部不成立，该论证依然成立。

#### 可直接支撑立论的原文摘录

出自 Miletic & Sariyar（*BMC Med Inform Decis Mak* 2026;26:30，已下载核对）：

> Utility evaluations vary widely, with strong emphasis on descriptive statistics and predictive
> performance, but **limited attention to inferential validity and clinical realism**.
> Privacy assessments are sparse and inconsistently reported: only 30% of studies included any
> metric, and just around 6% implemented differential privacy (DP), often without parameter disclosure.

> Event-based EHR data are most commonly targeted, while **continuous and irregular time series
> remain underexplored**.

根因 ② 与根因 ⑤ 正是这一缺口在本项目中的具体体现——这构成本设计的问题陈述与贡献定位。

#### 中文文献（本项目无法代查）

就诊时间分布、候诊时间实测、居民就诊率的时间序列分析，**必须从 CNKI / 万方 / 维普检索**；
本机检索通道为 US-only，不覆盖中文数据库。建议检索词：

```
门诊就诊时间分布    候诊时间 纵向研究    分级诊疗 就诊人次 时间序列
居民两周就诊率      医院门诊量 季节性     门诊人次 年龄 性别 差异
```

§5.3 中标为「需逐条核实」的参数，要么找到中文文献支撑后改为「有出处」，
要么保留为「假设」并做敏感性分析。**不得**在无出处时按文献口吻陈述。

## 10. 分期交付

每一期结束后系统都保持可运行、可测试。

| 阶段 | 内容 | 完成判据 |
|---|---|---|
| **一** | 5 张表 + 种子；删除违规注入（§7）；生成器接本体（科室/医生/药品/剂量/检验全部条件抽样）；升级 `RationalDrugValidator` 数据源 + 新增 `OntologyConsistencyValidator`（§8） | §8.3 三项判据全为 0（`ontology.violations`、`rational.violations`、`rational.unchecked`）；「妇科看肠胃炎」在万级数据上不再出现 |
| **二** | 患者疾病档案 + 事件日历 + 慢病随访 + 年龄改 0~89 | 同患者重复就诊率 > 0；慢病随访间隔分布中位数落在设计值附近（实测） |
| **三** | 时间分布模型（月/星期/日内/环节耗时）+ 校验器接看板 + 改造前后基线反测 + 测试报告与论文素材 | 看板显示本体一致率与覆盖率；§12.1 的基线反测数据实测落表 |

**交付粒度**：三阶段**共用同一份实现计划**，作为有序任务组排在一个 plan 里。
理由：三者都改 `DataGeneratorService` 与同一批本体表，拆成三个 plan 会产生三次重复的本体加载改造；
且阶段二是阶段三的必要前提（没有事件日历，「哪天就诊」就无从叠加星期/季节乘子）。
阶段三为纯增量，若周期紧张可整体裁剪而不影响一、二的交付完整性。

## 11. 测试策略

沿用项目既有风格：service/生成器用 Mockito，controller 用 `@WebMvcTest` + `@MockBean`。

| 层 | 测试 |
|---|---|
| **种子数据一致性** | ① `diagnosis_drug` 的每个诊断一线药组合不违反 `rational_rule.rule_type=1` 的相互作用规则（卡住现行 `I10` 缺陷）；② `drug_dose` 自身满足 `single_dose × 每日次数 ≤ max_daily_dose`；③ `drug_dose.max_daily_dose` 与 `rational_rule.rule_type=2` 对同一 `drug_id` 取值相等；④ 对 `rule_config` 已覆盖的 8 个诊断，`rule_config` 与 `diagnosis_drug` 的药品集合**完全相等**（过渡期约束；种子起草时把这 8 条一并修正，含 `I10` 去掉重复的 CCB） |
| **本体完整性** | 每个 `dept_diagnosis.department` 在 `doctor` 表里都有医生；每个 `diagnosis_drug.drug_id` 在 `drug` 表里存在；同一 `item_code` 在 `diagnosis_lab` 各行的 `item_name`/`unit`/`ref_range` 一致（`diagnosis_lab` 自身即检验项目池，不另建表）；`month_weights` 长度为 12 且全为正 |
| **化验本体自洽（修根因 ⑤）** | ① 每个诊断至少挂 2 项检验，且**至少有一项 `abnormal_direction` 为 `LOW` 或 `NONE`**（杜绝「只会偏高」）；② `ref_range` 与 `abnormal_direction` 自洽：方向为 `HIGH` 时参考范围上界须低于异常值域下界，为 `LOW` 时反之 |
| **生成器行为** | ① **核心回归测试**：生成数据上本体一致性与合理用药检查全部为 0，且 `unchecked == 0`；② 处方明细的每个 `drugId` 必属于该诊断的 `diagnosis_drug`（这是删除注入后的**等价保证**，直接卡住注入复活）；③ 患者年龄覆盖 <14；④ 儿科就诊的患者年龄 <14；⑤ 男性患者不出现妇科诊断；⑥ `admission` 的诊断全部 `admission_eligible=1`；⑦ 慢病随访间隔中位数落在设计区间；⑧ **偏低方向的异常确实出现**（`isAbnormal=1` 且结果值低于参考下界的记录数 > 0）；⑨ **临床路径检验不再恒为正常**（路径产生的检验中存在 `isAbnormal=1` 的记录） |
| **校验器（正例，必须有）** | 对**每一类**检查构造一条**已知违规**记录，断言被正确分类到对应 `byType`。这是「删除注入后校验器仍有效」的唯一代码级证据，**不得省略** |
| **校验器（负例）** | 生成数据上断言全 0；另构造一条「诊断不在本体中」的处方，断言计入 `unchecked` 而非被静默跳过（卡住 §8.1 的隐患回潮） |
| **时间一致性** | 沿用二期已有的时间线升序断言；新增环节耗时级联的先后顺序断言 |

**禁止**：写只断言 `notNull` 的空洞测试；写断言真值但从不失败的测试。

## 12. 论文产出

1. **改造前后量化对比（论文核心数字）**：
   - **改造前基线**：在改动代码**之前**先生成并留存一份数据集快照，
     改造完成后用**新的校验器**去反测这份快照，得到改造前的各类违规率。
     这样基线与新数字出自同一套判据，可比。**不要**用旧校验器的结果做基线（它的适应症检查只覆盖 8 个诊断，口径不可比）。
   - **改造后**：同规模数据集，全部检查为 0。
   - 数值**必须实测**（跑生成器 + MySQL 断言），未测则标「待实测」并附命令，不得编造。
2. **本体覆盖率**：`unchecked / total` 比例。这是「改造后的 0 不是空检查」的证据——
   必须证明所有处方都被判定过，而不是被跳过。
3. **校验器检出能力**：§11 正例测试的逐类结果 + 人工录入路径的活演示记录。
4. **参数来源表**：§5.3 的完整表格，区分「有出处」与「假设」，含敏感性分析。
5. **本体规模统计**：诊断数、药品数、关系边数、慢病数（实测）。
6. **局限与未来工作**：§1.2 的可达边界表 + §3.2 的 ML/RL 论证，以及若要突破需具备什么条件
   （真实数据访问权 + 合规审查 + 可验证的评估协议）。
7. **方法论参照章节**（现状完全缺失的一章）：§9.3 的公开实现与文献表 + `docs/references/README.md`。
   含「本设计提出前的算法无任何出处」的**如实说明**，以及逐条的核实状态。
   **交付前提**：11 篇已下载全文的可直接引用；3 篇付费墙文献须先经学校图书馆取得原文，
   且**不得**据以陈述其具体机制。

## 13. 全局约束（后续计划必须遵守）

- 不引入账号密码 / session / JWT
- 任何模拟数据（医生/患者/药师/护士）**不得使用「王冠杰」**
- 统一信封 `Result{code,message,data}`，`code=0` 成功
- 时间线事件严格按 `time` 升序；所有时间字段 `DATETIME`
- Redis 纯优化，失败降级查库，绝不让 GET 变 500
- controller 用 `@WebMvcTest` + `@MockBean`；service/生成器用 Mockito
- **数据字典表（`drug`/`rule_config`/`doctor`/`rational_rule`/`clinical_pathway`）在生成器清空数据时不得触碰**；
  本次新增的 5 张本体表同属字典表，同样不得清空
- **生成数据不得包含任何故意违规**：不引入注入机制，不放宽本体约束来「制造」待检出的异常
- 校验器对**无法判定**的数据必须显式报出（`unchecked`），不得静默跳过伪装成通过
- 报告数值必须实测，不得编造；未测就标「待实测」并给命令
- 每个 commit 结尾附 `Co-Authored-By: Claude Code <noreply@anthropic.com>`
- 全程中文交流；代码标识符英文，注释跟随现有中文风格

## 14. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 60~80 诊断的种子数据录入量大、易出错 | 本体错误 → 生成数据荒谬 | 每条注明来源；作者审校；§11 的一致性测试自动兜底 |
| 时间参数（季节/星期/间隔）多为假设 | 论文被质疑 | §5.3 显式标注 + 敏感性分析；不以文献口吻陈述假设 |
| `rule_config` 与 `diagnosis_drug` 双真相源 | 前端改了一处、生成器读另一处 | 过渡期用一致性测试卡住；后续可让前端编辑 `diagnosis_drug` 并废弃 `rule_config` |
| **本体覆盖不全** | `unchecked > 0`，「违规率为 0」变成「没检查所以为 0」，论文核心数字失真 | 覆盖率作为一等指标报出（§12.2）；§11 断言 `unchecked == 0`；覆盖率 100% 才对外报数字 |
| **删除注入后校验器恒返回 0** | 答辩被问「校验器到底有没有用」 | §8.4 三条路径：正例单测（代码级证据）+ `EntryForm` 人工录入活演示 + 平台定位是校验**外部**数据 |
| **3 篇付费墙文献原文未读到**（Synthea / Budu / Ghosheh） | 据以陈述其具体机制即为学术风险 | 元数据已逐字核实并留档；`docs/references/README.md` 列有获取途径（学校图书馆）；§9.3 硬规则明确禁止据以陈述机制。**Synthea 原文建议优先获取**——它是 §5 的骨架 |
| 数据生成耗时因本体查询上升 | 万级患者生成变慢 | 本体表启动时全量加载进内存（与现有加载方式一致）；生成过程零 DB 往返 |
| 三期工作量超出毕设周期 | 交付不完整 | 分期交付，每期独立可运行；阶段三是纯增量，可裁剪 |
