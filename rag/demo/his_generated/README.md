# HIS 风格新生儿合成数据（his_generated）

本目录由研发侧工具 `generate_neonatal_his.py` 确定性生成（该工具不随本分支发布），
是**独立于 `rag/demo/neonatal_cases.json` 的第二个演示档案**。它复用 v6.6.4 的真实
`PatientSnapshot` / `RetrievalRequest` 契约，但不改动精选的 9 例病例，也不注册任何来源。

全部记录均为合成数据：不是真实患者，不是指南，不构成诊断或治疗建议。

> 需要批量 30 例时见 [`../his_generated_30/`](../his_generated_30/README.md)（HIS 加权诊断选择 + 分层抽样）。

## 1. 文件

| 文件 | 内容 | 用途 |
|---|---|---|
| `neonatal_cases.json` | `profile=neonatal_demo_v1`，11 例（snapshot + preset） | `rag.demo.load_demo_cases(<本目录>)`、工作台/测试 |
| `snapshots.json` | 11 个裸快照 | `POST /api/his/snapshot` 的 `patient_snapshot` |
| `requests.json` | 11 个完整 `RetrievalRequest`（含快照） | `POST /api/retrieve`，或 Python `RetrievalRequest.from_dict` |
| `tables/*.csv` | HIS 风格关系表，UTF-8 BOM，中文表头 | 对照 `origin/hisdata` 的 8 表口径；不是 RAG 输入 |
| `manifest.json` | 种子、计数、文件 SHA-256、边界 | 溯源与确定性复核 |

## 2. 生成

```bash
# 在含研发侧 tools/ 的工作区根目录执行；PYTHONPATH 让脚本导入本分支的 rag 包做契约校验
PYTHONPATH=. python3 tools/generate_neonatal_his.py \
  --seed 20261018
```

常用参数：

| 参数 | 含义 |
|---|---|
| `--seed` | 确定性种子；同种子 + 同脚本 = 字节级相同输出 |
| `--variants N` | 每个场景生成 N 例（默认 1）；`case_id` 追加 `-v02` 等后缀 |
| `--output DIR` | 输出目录；默认本目录 |
| `--no-validate` | 跳过 `rag` 契约与检索冒烟校验（只在没有 v6.6.4 环境时使用） |

生成后脚本会自动做三层校验：

1. `load_demo_cases()` 读取并校验 profile/synthetic/preset 字段；
2. 每个病例走 `RetrievalRequest.from_dict()` 与 `snapshot_from_dict()`；
3. 注册精选演示指南库，对 RDS 病例跑一次真实 BM25 检索（要求返回引用）。

## 3. 病例清单

| case_id | 主题 | 演示要点 |
|---|---|---|
| `neo-his-rds` | RDS，30 周左右，CPAP | 条件化呼吸支持；血培养 pending |
| `neo-his-rds-severe` | 重症 RDS，27 周左右，IMV | 表面活性物质已给药；机械通气 |
| `neo-his-rds-retro` | RDS 回顾时点（as_of 2026-02-25） | 早于 RDS 2025 更新版发表日 |
| `neo-his-pneumonia` | 感染性肺炎，发热，CRP 升高 | `pending≠阴性`；胸片 |
| `neo-his-ttn` | 暂时性呼吸增快 | 鉴别与观察；胸片叶间裂积液 |
| `neo-his-jaundice` | 新生儿黄疸，光疗 | 数值与胎龄/时点绑定 |
| `neo-his-pphn` | PPHN + 胎粪吸入，iNO | 导管前后 SpO₂ 差异；超声 |
| `neo-his-bpd` | BPD，经后年龄约 36 周 | 定义与评估时点；PMA 由调用方派生 |
| `neo-his-hypoglycemia` | 低血糖，胎龄未记录 | `not_recorded` 不反推 |
| `neo-his-gbs` | 母体 GBS 阳性、无症状 | 母婴主体分离；血培养 pending |
| `neo-his-apnea` | 早产儿呼吸暂停 | 咖啡因与呼吸监测 |

每个病例的 `preset` 只含 `RetrievalRequest` 字段；`request_id` 与 `patient_snapshot`
由调用方在请求时生成/注入，不写进 preset。

## 4. 在 v6.6.4 中使用

### 4.1 Python

```python
from rag.demo import load_demo_cases
from rag.models import RetrievalRequest

payload = load_demo_cases("rag/demo/his_generated")
case = payload["cases"][0]
request = RetrievalRequest.from_dict({**case["preset"], "patient_snapshot": case["snapshot"]})
```

### 4.2 HTTP

```bash
# 1) 载入快照（服务按 as_of 过滤未来记录后回显）
curl -s http://127.0.0.1:8798/api/his/snapshot \
  -H 'Content-Type: application/json' \
  -d "$(python3 - <<'PY'
import json
print(json.dumps({"patient_snapshot": json.load(open(
  "rag/demo/his_generated/snapshots.json"))["patients"][0]},
  ensure_ascii=False))
PY
)"

# 2) 直接发送完整请求
curl -s http://127.0.0.1:8798/api/retrieve \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c 'import json;print(json.dumps(json.load(open("rag/demo/his_generated/requests.json"))["requests"][0]["request"],ensure_ascii=False))')"
```

`source_mode=custom`，因此检索前需要显式加载演示指南库：
`POST /api/demo/guidelines/load`（或启动时 `config.demo_autoload_guidelines=True`）。

## 5. 与上游生成分支的关系

| 上游 | 实际能力 | 本目录的处理 |
|---|---|---|
| `origin/his@0e6a2d1` | Java/Spring Boot；写 MySQL `his_demo`；患者年龄 18–89；诊断/用药/检验/剂量来自 `dept_diagnosis` / `diagnosis_drug` / `diagnosis_lab` / `drug_dose` 本体表 | 借其「科室-诊断-用药-检验一致、时间严格递增、身份证 GB 11643」纪律；不直接运行（该生成器依赖 MySQL 本体库，且无新生儿年龄档） |
| `origin/hisdata@e85f469` | Python 3.11+ + Synthea 源码 + JDK 21 + 5 个 Java 补丁；IR → GMF → FHIR → 8 张 CSV；当前 IR 只有成人 T2DM | 借其「IR 每个数字带 source、稳定行序、空值不填 0、`【待核实】` 标记」纪律；不直接运行（当前无新生儿 IR） |

本目录**不是** Synthea/FHIR 产物，不声称 Synthea 溯源；也没有运行上述两条上游生成器。

## 6. 表口径与边界

- `tables/` 是给 HIS 侧对照用的关系表，**不是 v6.6.4 的输入**；v6.6.4 只读
  `neonatal_cases.json` / `snapshots.json` / `requests.json`。
- `医嘱_药品.数量/单价/金额` 留空而不是填 0：这批数据没有建模包装数量与费用，
  空值表示「未测量/未建模」，不是「免费」。
- ICD-10 / LOINC / ATC 码值仅用于演示，逐条标注 `待复核`；不得当作国标或官方编码。
- 剂量、FiO₂、PEEP 等数值是合成场景值，不是推荐阈值，不得写成临床建议。
- 母体事实固定 `collection=maternal` 且 `fields.subject_role=mother`；患儿事实
  `subject_role=patient`。`pending`（如血培养）与 `not_recorded`（如胎龄/GBS）
  不得写成阴性。
- 同一种子重跑必须字节级一致；改脚本或种子后重新生成，并同步 `manifest.json`。
