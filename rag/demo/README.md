# 新生儿演示档案（neonatal_demo_v1）

本目录是**演示资产**，不是产品语料，也不是第三种来源模式。顶层演示文件为 JSON，可审阅、可 diff、可整体替换；`his_generated/` 另含生成的关系表 CSV 与说明。

## 1. 文件与职责

| 文件 | 内容 | 谁在用 |
|---|---|---|
| `neonatal_cases.json` | 9 个精选合成新生儿病例：`snapshot`（PatientSnapshot 载荷）+ `preset`（RetrievalRequest 字段） | `rag/demo.py`、`GET /api/demo/cases`、工作台步骤 01–05 |
| `neonatal_guidelines.json` | 三个调用方来源：`neonatal-guideline-metadata`（真实书目元数据，`synthetic=false`）、`neonatal-guideline-synthetic`（合成指南块，`synthetic=true`）、`neonatal-literature-synthetic`（合成文献条目，`synthetic=true`） | `rag/demo.py`、`GET /api/demo/guidelines`、`POST /api/demo/guidelines/load` |
| `his_generated/` | **独立档案**：11 例逐场景合成病例（同 profile）、裸快照、完整请求与 HIS 关系表 | `load_demo_cases(".../his_generated")`；不进入默认 `GET /api/demo/cases` |
| `his_generated_30/` | **独立档案**：30 例 HIS 风格批量病例（加权诊断选择 + 分层抽样）、裸快照、完整请求、病例索引与 HIS 关系表 | 默认 `load_demo_cases()` 自动并入（精选 9 + 生成 30 = 39，工作台可见）；显式 `load_demo_cases(".../his_generated_30")` 只读 30 例 |

模块只做**校验与读取**（`rag/demo.py`），注册动作必须显式发起：

```text
POST /api/demo/guidelines/load     # 工作台按钮“加载新生儿演示指南库”
config.demo_autoload_guidelines=True  # 运维在启动阶段预载（仍记一条 register 事件）
```

服务仍不内置语料：病例默认 `source_mode=live`（真实联网指南/全文）；只有显式加载演示库并切到 `custom` 时才使用演示块，未加载时 `custom` 检索会显式报错。

## 2. 病例 schema（`neonatal_cases.json`）

```json
{
  "profile": "neonatal_demo_v1",
  "synthetic": true,
  "age_rules": { "gestational_age": "...", "postnatal_age": "...", "postmenstrual_age": "...", "corrected_age": "..." },
  "data_status_vocabulary": ["observed_positive", "observed_negative", "not_recorded", "pending", "unavailable", "hypothesis"],
  "subject_roles": ["patient", "mother"],
  "cases": [
    {
      "case_id": "neo-rds",
      "label": "RDS · 30周+2天 · 生后6小时 · CPAP FiO₂ 0.30（as_of 2026-03-10）",
      "synthetic": true,
      "snapshot": { "patient_ref": "neo-rds-001", "as_of": "2026-03-10", "facts": [ ... ] },
      "preset": {
        "question": "...",
        "term_mapping": [
          {"concept": "新生儿呼吸窘迫综合征", "aliases": ["NRDS"],
           "english": ["respiratory distress syndrome"],
           "mesh": ["Respiratory Distress Syndrome, Newborn"]}
        ],
        "top_k": 6, "source_mode": "live", "...": "..."
      }
    }
  ]
}
```

校验规则（`rag/demo.py::load_demo_cases`，由 `tests/test_demo.py` 守护）：

1. `profile` 必须为 `neonatal_demo_v1`，`synthetic` 必须为 `true`；
2. 每个 `case_id` 唯一，`synthetic` 必须为 `true`；
3. `snapshot` 必须能通过 `snapshot_from_dict()`（即真实 `PatientSnapshot` 契约）；
4. `preset` 只能出现 `RetrievalRequest` 字段（`PRESET_FIELDS`），未知字段直接报错；
5. `preset + snapshot` 必须能通过 `RetrievalRequest.from_dict()`（保证 GUI 演示输入与 RAG 输入完全一致）。

`preset` 中**不得**出现 `request_id`（执行时生成）与 `patient_snapshot`（来自步骤 01 的显式载入）。

**术语映射（v6.6.5）**：`preset.term_mapping` 是推荐输入，每行把中文概念与 `english`/`mesh`/`aliases` 绑定；只有概念或别名出现在问题/患者术语/facets 时才展开。演示档案仍保留旧 `english_terms`/`mesh_terms`/`user_terms` 作为兼容字段与历史对照，但工作台只发送 `term_mapping`。新增或修改病例时应优先维护 `term_mapping`，并保证每个映射行至少有一个英文或 MeSH 产物。

### 2.1 年龄字段约定

| 字段 | 含义 | 放置位置 | 约束 |
|---|---|---|---|
| `ga_days` | 出生胎龄总天数 | `birth[0].fields` | 整数；不得由体重反推 |
| `ga_text` | 原始“周+天”文本 | `birth[0].fields` | 与 `ga_days` 同时保留 |
| `pna_hours` | 生后小时数 | `postnatal[0].fields` | 急性呼吸场景至少到小时 |
| PMA | `ga_days + floor(pna_hours/24)` | **不写入快照** | 由 Agent/调用方派生；RAG 不推导 |
| 矫正年龄 | 随访场景 | 不写入快照 | 不与 PMA 混用 |

### 2.2 数据状态与主体

- `fields.data_status` 取 `observed_positive / observed_negative / not_recorded / pending / unavailable / hypothesis` 之一；
- `pending`（如血培养待回报）与 `not_recorded`（未记载）**不得**写成阴性；
- 母体事实固定为 `collection=maternal` 且 `fields.subject_role=mother`；患儿事实 `subject_role=patient`。RAG 本地检索可见两者，但外发术语只来自 `reviewed_external_terms` / 审核术语。

## 3. 指南库 schema（`neonatal_guidelines.json`）

沿用 custom-source 载荷（`contracts/README.md` §8.1）：

```json
{ "sources": [ { "source_id": "...", "kinds": ["guideline"], "version": "...", "documents": [ ... ] } ] }
```

v6.6.5 实例：

| source_id | 内容 | `synthetic` | 许可/边界 |
|---|---|---|---|
| `neonatal-guideline-metadata` | 3 条经书目核验的元数据：新生儿感染性肺炎（2025，中文）、RDS 2025 更新版（Neonatology 2026）、RDS 中文解读 | `false` | `content_mode=metadata_only`、`full_text_allowed=false`、`rights_basis=source_page_requires_verification`；只作为来源定位 |
| `neonatal-guideline-synthetic` | 10 个合成指南块（RDS×2、肺炎×2、黄疸、TTN、PPHN、BPD、低血糖、GBS） | `true` | 仅流程测试；`metadata.disclaimer` 写明不是真实推荐 |
| `neonatal-literature-synthetic` | 9 条合成文献条目（按主题各 1 条，`kind=pubmed`，PMID 前缀 9000） | `true` | 让离线演示也能看到 PubMed 来源分组；不是真实研究 |

### 3.1 元数据条目硬约束

1. 标题、DOI、PMID、PMCID、团体作者、版本与出版日期必须一致（`tests/test_demo.py` 校验 DOI/PMID/发布日期）；
2. **版本标签与发表时间分开**：RDS 的 `version_id="2025"`、`published_at="2026-03-09"`；`as_of` 早于发表日时该记录被 `EvidenceStatus.FUTURE_AT_QUERY_TIME` 排除；
3. 中文解读 `metadata.source_role=secondary_interpretation`、`official=false`，不得与原版指南互相替代或合并；
4. 任何成人指南（高血压/糖尿病/氨氯地平）不得出现在演示库（测试断言）；
5. `metadata.applicability_criteria` 只声明适用范围级条件（如胎龄 < 37 周、新生儿期、诊断主题），**不得写治疗阈值**；每条条件需带 `criterion_id`、`field`、`operator`、`value`、`review_status` 与 `source`。真实指南元数据使用 `review_status="scope_only"`（不参与判定）；只有经人工审核的条件才可标 `reviewed`。真实指南元数据的条件只来自标题/适用人群，不来自正文推荐。

### 3.2 合成块硬约束

1. `metadata.synthetic=true`、`content_mode=synthetic_fixture`、`rights_basis=synthetic-fixture-not-clinical`；
2. 正文必须写明“合成样例（非真实指南）”与“不是真实推荐”；
3. 数值只用于演示“数值与条件绑定”，不得写成可执行的临床阈值；
4. `metadata.applicability_criteria` 可以声明演示条件（`required`/`subject_role`/`operator`），但必须与正文显式字段绑定，并标 `review_status="synthetic_fixture"` 与 `source`；`mismatch`/`unknown` 是流程演示，不是临床结论。`draft`/`scope_only` 条件只展示、不参与判定。

## 4. 病例清单

| case_id | 主题 | as_of | 演示要点 |
|---|---|---|---|
| `neo-rds` | RDS，30周+2天，CPAP FiO₂ 0.30 | 2026-03-10 | 条件化推荐；RDS 2025 已发表 |
| `neo-rds-retro` | 同结构病例的回顾时点 | 2026-02-25 | 早于 RDS 2025 发表日 → 未来文献被排除 |
| `neo-pneumonia` | 感染性肺炎，血培养 pending | 2026-09-12 | `pending≠阴性`、CRP、影像 |
| `neo-ttn` | 暂时性呼吸增快 | 2026-08-20 | 鉴别与观察 |
| `neo-jaundice` | 黄疸，总胆红素 285 μmol/L | 2026-07-18 | 数值与胎龄/时点绑定 |
| `neo-pphn` | PPHN，iNO | 2026-06-12 | 导管前后 SpO₂ |
| `neo-bpd` | BPD，经后年龄 36 周 | 2026-05-20 | 定义与评估时点 |
| `neo-hypoglycemia` | 低血糖，胎龄未记录 | 2026-04-05 | `not_recorded` 不得反推 |
| `neo-gbs` | 母体 GBS 阳性、无症状 | 2026-10-02 | 母婴主体分离 |

## 5. 修改规则

1. 改病例或指南库后必须跑 `tests/test_demo.py` 与 `tests/test_gui.py`；
2. 新增字段先加进 `PRESET_FIELDS` 与 `RetrievalRequest` 契约三处（models / contracts JSON / agent 工具描述），再改 GUI；
3. 新增病例/文献条目必须标 `synthetic=true`，不得放入真实患者数据；
4. 修改元数据条目必须重新核对 DOI/PMID/发布日期；不确定就保留 `status=unknown`，不猜 `current`；
5. 合成块标题统一以“合成指南 · ”开头，正文以“合成指南（非临床推荐）”开头，避免被误读为指南原文；
6. 合成块之间不得共享与病种无关的高频词（如通用“新生儿”前缀），否则相关度门会把无关主题带进结果；
7. 新增或修改 `term_mapping` 时必须走 `RetrievalRequest.from_dict()` 校验；概念/别名要与病例问题或 `patient_terms` 实际出现的写法一致，未命中概念会在 `query_review` 中提示；
8. 新增或修改 `applicability_criteria` 后，用 `tests/test_query_mapping_applicability.py` 的核对语义检查 `match`/`mismatch`/`unknown`；`not_recorded`/`pending` 不得因“没找到”被写成阴性。

## 6. 生成的 HIS 风格病例（`his_generated/`、`his_generated_30/`）

两个子目录都是**独立档案**，与上面的 9 例精选病例分开维护：

- `his_generated/`：11 个场景各 1 例，用于逐场景审阅与契约回归。
- `his_generated_30/`：30 例批量档案，按 HIS 生成器的加权诊断选择 + 分层抽样生成；
  默认种子下 11 个主题全部覆盖，附 `case_index.csv` 便于人工审阅。
- 默认 `load_demo_cases()`（以及 `GET /api/demo/cases`）会把 `his_generated_30/` 的
  30 例并入精选 9 例，工作台下拉框显示 39 例；显式传入目录时只读该目录。

共同规则：

- `neonatal_cases.json` 使用同一个 `neonatal_demo_v1` profile，可直接
  `load_demo_cases(".../his_generated")`；默认 `GET /api/demo/cases` 仍只读精选档案。
- `snapshots.json` 可直接作为 `POST /api/his/snapshot` 的 `patient_snapshot`；
  `requests.json` 是含快照的完整 `RetrievalRequest`，可直接 `POST /api/retrieve`。
- `tables/*.csv` 是 HIS 风格关系表，仅供对照，不是 RAG 输入；数量/单价/金额留空
  表示未建模，不填 0。
- 生成器：研发侧 `generate_neonatal_his.py`（11 例）与 `generate_neonatal_his30.py`（不随本分支发布）
  （30 例）；同种子重跑字节级一致。
- 重新生成后必须跑 `tests/test_neonatal_his_generated.py` 与
  `tests/test_neonatal_his_generated_30.py`，并同步各自 `README.md` 与 `manifest.json`。
- 该档案不是 Synthea/FHIR 产物，不声称来自 `origin/his` 或 `origin/hisdata`
  的原生运行；ICD/LOINC/ATC 码值与剂量只用于演示。
