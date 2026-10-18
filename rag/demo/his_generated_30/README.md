# HIS 风格新生儿批量数据（30 例）

本目录由研发侧工具 `generate_neonatal_his30.py` 确定性生成（该工具不随本分支发布），
是**可直接放入 demo 的 30 例档案**。它与 `../his_generated/` 使用同一个
`neonatal_demo_v1` profile，因此 `load_demo_cases(<本目录>)` 可直接读取。

全部记录均为合成数据：不是真实患者，不是指南，不构成诊断或治疗建议。

## 1. 与 `his_generated/` 的区别

| 目录 | 生成方式 | 用途 |
|---|---|---|
| `../his_generated/` | 11 个场景各 1 例 | 逐场景审阅、契约回归 |
| 本目录 | 按 `his` 生成器的加权诊断选择 + 分层抽样，生成 30 例 | 批量演示、工作台病例列表、检索压力测试 |

本目录的默认种子 `20261018` 下，11 个主题全部覆盖；分布为：
新生儿低血糖 6、重症 RDS 5、RDS 4、GBS 暴露 3、暂时性呼吸增快 3、
新生儿黄疸 3、RDS 回顾时点 2、早产儿呼吸暂停 1、感染性肺炎 1、PPHN 1、BPD 1。
换种子会改变分布，但分层抽样保证每个主题至少 1 例（`--patients >= 11` 时）。

## 2. 文件

| 文件 | 内容 | 用途 |
|---|---|---|
| `neonatal_cases.json` | 30 例（snapshot + preset），`profile=neonatal_demo_v1` | `load_demo_cases(<本目录>)`；可直接替换 demo 病例文件 |
| `snapshots.json` | 30 个裸快照 | `POST /api/his/snapshot` |
| `requests.json` | 30 个完整 `RetrievalRequest` | `POST /api/retrieve` |
| `case_index.csv` | case_id、patient_ref、as_of、主题、诊断、问题 | 人工快速审阅 |
| `tables/*.csv` | HIS 风格关系表（UTF-8 BOM，中文表头） | 对照 HIS 侧表结构；不是 RAG 输入 |
| `manifest.json` | 种子、权重、计数、文件 SHA-256、验证结果、边界 | 溯源与确定性复核 |

## 3. 生成

```bash
PYTHONPATH=. python3 tools/generate_neonatal_his30.py \
  --patients 30 --seed 20261018
```

参数：

| 参数 | 含义 |
|---|---|
| `--patients` | 生成条数（默认 30） |
| `--seed` | 确定性种子；同种子字节级一致 |
| `--output` | 输出目录；默认本目录 |
| `--no-validate` | 跳过 `rag` 契约与检索冒烟校验 |

## 4. 使用

```python
from rag.demo import load_demo_cases
from rag.models import RetrievalRequest

payload = load_demo_cases("rag/demo/his_generated_30")
case = payload["cases"][0]
request = RetrievalRequest.from_dict({**case["preset"], "patient_snapshot": case["snapshot"]})
```

默认 `GET /api/demo/cases` 与工作台已自动合并本目录的 30 例（精选 9 + 生成 30 = 39），
无需复制；响应中的 `case_sets` 字段区分两组。显式 `load_demo_cases(<本目录>)`
仍只读这 30 例。

## 5. 为什么不是直接运行 Java 生成器

`origin/his` 是 Spring Boot 3.3 / Java 21 项目，生成器直写 MySQL `his_demo`，且患者
年龄固定 18–89 岁，也没有新生儿本体表。
本脚本移植其核心规则：

- `dept_diagnosis` 式加权诊断选择（本目录的 11 个新生儿主题即本体行）；
- 诊断 → 用药 / 检验 / 剂量池；
- 就诊时间严格递增、下游事件不越过 `as_of`；
- GB 11643 身份与合成人口学字段。

输出**不是** Java 生成器的直接运行产物，也不是 Synthea/FHIR 产物。

## 6. 边界

- 合成数据，不是真实患者，不构成诊断或治疗建议。
- ICD-10 / LOINC / ATC 码值仅用于演示，逐条标 `待复核`。
- 剂量、FiO₂、PEEP 等是场景值，不是推荐阈值。
- `医嘱_药品.数量/单价/金额` 留空表示未建模，不填 0。
- 血培养 `pending` 与胎龄/GBS `not_recorded` 不得写成阴性；未来报告由 `as_of` 抑制。
