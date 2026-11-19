# 患者图谱接入说明（RAG 内部与外部 EHR）

本文件面向两类调用方：

1. **RAG 内部**：在同一个 Python 进程里直接调用 `rag.patient_graph`。
2. **外部 EHR**：通过只读 HTTP 接口提交 `PatientEnvironment.view()`，不需要了解 RAG 内部的图谱、词表或快照类。

接口只做记录定位、来源/时间/主体保留与路径审计，不输出诊断、治疗建议或动作授权。所有患者数据由调用方在请求中提供，服务不落盘、不回写病历、不访问 HIS。

机器可读契约：[patient_snapshot.schema.json](patient_snapshot.schema.json) 与 [patient_graph.openapi.yaml](patient_graph.openapi.yaml)。

## 1. 数据契约

患者快照的 `schema_version` 固定为 `patient-snapshot`。顶层只接受 `records`；旧的顶层 `facts` 会被拒绝。

```json
{
  "patient_ref": "pseudo-patient-001",
  "as_of": "2026-01-09T07:00:00",
  "schema_version": "patient-snapshot",
  "source": "ehr_view",
  "records": [
    {
      "record_id": "ehr:intake:birth",
      "record_type": "derived_birth",
      "event_type": "EHR_INTAKE",
      "event_time": "2026-01-09T07:00:00",
      "status": "final",
      "source_system": "ehr",
      "fields": {
        "ga_days": 251,
        "ga_text": "35周6天",
        "birth_weight_g": 2328,
        "data_status": "observed_positive"
      },
      "provenance": {
        "record_path": "ehr:intake:birth",
        "source_type": "derived_from_ehr_input"
      }
    }
  ],
  "reviewed_external_terms": []
}
```

`PatientRecord` 字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `record_id` | 是 | 调用方内稳定记录 ID，用于证据回溯 |
| `record_type` | 是 | `patient`、`derived_birth`、`labs`、`respiratory_support`、`ehr_condition`、`ehr_action` 等 |
| `event_type` | 否 | 事件类型；缺省取 `record_type` |
| `fields` | 否 | 结构化字段；字段名即检索词表使用的语义名 |
| `event_time` | 否 | 事件发生/采样时间，ISO 日期或日期时间 |
| `reported_time` | 否 | 报告发布时间；晚于 `as_of` 时记录不可见 |
| `start` / `end` | 否 | 区间记录（如呼吸支持）；不能补默认日期 |
| `status` | 否 | `final`、`preliminary`、`amended`、`pending`、`not_recorded`、`entered_in_error`、`unknown` |
| `source_system` | 否 | 来源系统，例如 `his`、`ehr` |
| `subject` | 否 | `patient`、`mother` 等主体 |
| `code` / `value` / `unit` / `reference_range` | 否 | EHR 观测的标准编码与值；参与检索 token 与哈希 |
| `provenance` / `extra` | 否 | 来源路径与调用方附加信息；不进入图响应正文 |

可见性规则：

- `as_of` 为日期时，按记录自身时区的本地日期比较；`as_of` 为日期时间时，按绝对时刻比较。
- `event_time` 或 `reported_time` 晚于 `as_of` 的记录不进入图谱。
- 缺时间记录保留并产生 warning，不补默认时间。
- `not_recorded` 记录仍返回，状态标为 `not_recorded`；`no_results` 表示“没有查到”，不是阴性。

## 2. RAG 内部调用

```python
from rag.adapters import snapshot_from_ehr_view
from rag.patient_graph import build_patient_graph, retrieve_patient_facts

# 方式 A：EHR 运行时视图
snapshot = snapshot_from_ehr_view(
    ehr_view,                      # PatientEnvironment.view() 的 dict
    patient_ref="pseudo-patient-001",
    as_of="2026-01-09T07:00:00",   # 或 start_time + view["time_hours"]
    source="ehr_view",
)

# 方式 B：调用方已经构造好 patient-snapshot
from rag.core.his import snapshot_from_dict
snapshot = snapshot_from_dict({"patient_snapshot": snapshot_dict})

graph = build_patient_graph(snapshot)
hits, trace = retrieve_patient_facts(snapshot, ["该患儿出生胎龄是多少周？"], top_k=10)
```

`retrieve_patient_facts()` 是患者事实检索的唯一入口；返回的 `hits[]` 包含 `record_path`、`status`、`concept_ids`、`graph_path`、`evidence_text`，`trace` 包含解析、种子、扩展、预算、截断与 `snapshot_sha256`。

## 3. 外部 EHR HTTP 调用

启动 RAG 服务后，EHR 只需调用两个只读端点：

可先用 `GET /api/health` 探测：`patient_graph.contract == "patient-snapshot"`、`patient_graph.accepts_ehr_view == true`、`patient_graph.search_endpoint == "/api/patient-graph/search"`。

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/patient-graph` | 由 EHR 视图或患者快照构建图，返回节点/边/哈希/计数 |
| `POST` | `/api/patient-graph/search` | 构建图并检索患者事实，返回命中、路径与 trace |

两个端点都接受两种请求体之一：

### 3.1 直接提交 EHR 视图（推荐）

EHR 不需要构造 `PatientRecord`，直接提交 `PatientEnvironment.view()`：

```json
{
  "patient_ref": "pseudo-patient-001",
  "as_of": "2026-01-09T07:00:00",
  "ehr_view": {
    "intake": {
      "gestational_weeks": 35,
      "gestational_days": 6,
      "birth_weight_g": 2328,
      "age_hours": 21,
      "symptoms": ["气促"]
    },
    "phase": "treatment",
    "time_hours": 23,
    "observations": [
      {
        "event_id": "obs-1",
        "session_type": "admission",
        "collected_at_hours": 0,
        "items": [
          {"key": "pH", "value": 7.36},
          {"key": "SpO2(%)", "value": 91},
          {"key": "CRP(mg/L)", "value": 24}
        ]
      }
    ],
    "active_treatments": [{"target": "cpap"}],
    "pending_tests": [{"target": "culture"}],
    "conditions": [{"condition": "rds", "status": "active"}],
    "executed_actions": [{"action": "start_cpap", "at_hours": 0}]
  },
  "queries": ["CPAP", "呼吸窘迫"],
  "top_k": 10,
  "max_hops": 2,
  "max_nodes": 240
}
```

`as_of` 可省略，此时用 `start_time`（ISO 日期时间）加 `view["time_hours"]` 计算；两者都没有会返回 400，不会编造日期。

### 3.2 提交已规范化的患者快照

```json
{
  "patient_snapshot": {
    "patient_ref": "pseudo-patient-001",
    "as_of": "2026-01-09T07:00:00",
    "schema_version": "patient-snapshot",
    "records": [
      {
        "record_id": "ehr:observation:obs-1",
        "record_type": "labs",
        "event_type": "EHR_OBSERVATION",
        "event_time": "2026-01-09T07:00:00",
        "fields": {"ph": 7.36, "spo2": 91, "crp": 24, "data_status": "observed_positive"}
      }
    ]
  },
  "queries": ["CPAP", "呼吸窘迫"]
}
```

### 3.3 响应

`POST /api/patient-graph/search` 返回：

```json
{
  "status": "ok",
  "data": {
    "graph": {
      "patient_ref": "pseudo-patient-001",
      "as_of": "2026-01-09T07:00:00",
      "snapshot_sha256": "…",
      "node_count": 70,
      "edge_count": 300,
      "fact_count": 60
    },
    "hits": [
      {
        "record_path": "derived:ehr:birth:2001",
        "collection": "derived_birth",
        "status": "observed_positive",
        "concept_ids": ["ga_weeks"],
        "graph_path": ["patient:…", "record:…", "concept:…"],
        "evidence_text": "derived_birth/BIRTH …",
        "score": 0.92
      }
    ],
    "trace": {
      "engine": "patient_graph",
      "query": {"raw": ["CPAP", "呼吸窘迫"], "intent": "assess"},
      "budget": {"max_hops": 2, "max_nodes": 240},
      "graph": {"snapshot_sha256": "…", "nodes": 70, "edges": 300}
    }
  }
}
```

`graph` 不回显原始 `records` 或叙述文本；`hits[].evidence_text` 只拼接结构化字段，不包含自由文本病史。返回的分数是检索相关性，不是概率、证据质量或临床适用性。

## 4. EHR 视图字段映射

| EHR 视图 | 生成的记录 | 说明 |
|---|---|---|
| `intake` | `derived_birth`、`ehr_intake`、`patient` | 孕周/体重生成派生出生记录；`age_hours` 生成日龄 |
| `observations[]` | `labs` | 指标按 `_EHR_METRIC_KEYS` 归一；原始项目保存在 `extra.ehr_items` |
| `conditions[]` | `ehr_condition` | 原样保留，不自动解释为诊断 |
| `active_treatments[]` | `respiratory_support` | `target` 作为 `mode` |
| `executed_actions[]` | `ehr_action` | 原样保留动作与时间 |
| `pending_tests[]` | `labs`（`pending`） | 培养/影像/其他待检分别标记，不造阴性结果 |

适配器不导入 EHR 实现包；EHR 侧只需传入普通 dict。若 EHR 的字段名变化，先改适配器并补测试，不要让 RAG 直接读取 EHR 内部对象。

已验证 EHR 运行时的 `PatientEnvironment.view()` 可直接投影：同一进程内构建 3 条记录、患者图 9 节点/16 边，胎龄查询命中 `ehr:intake:birth`。EHR 运行时的 pydantic `PatientEnvironment.view()` 返回的 pydantic 模型也支持（适配器自动 `model_dump()`）。

## 5. 错误与边界

| 情况 | 返回 |
|---|---|
| 缺 `patient_ref` | 400 |
| 缺 `as_of` 且缺 `start_time` | 400 |
| `queries` 为空、超过 20 条或单条超过 200 字 | 400 |
| 旧顶层 `facts` 快照 | 400，要求 `records` |
| 未知记录类型/字段 | 保留原值，不猜测；可能只产生 `not_recorded` 或 `no_results` |
| 未来记录/未发布报告 | 不进入图谱，计入 `suppressed_future` 或 warning |

安全要求：

- `patient_ref` 使用调用方生成的伪匿名 ID，不要传姓名、住院号、身份证号。
- 不要把自由文本病史塞进 `fields`；图谱响应不返回叙述字段。
- 服务只读、不落盘、不写回 HIS/EHR，不调用外部文献服务。
- 外部 EHR 只在已授权上下文内调用；接口没有内置认证，部署时由网关/内网访问控制负责。

## 6. 最小客户端示例

如果调用方与 RAG 包在同一 Python 环境，可直接使用带类型的客户端：

```python
from rag.patient_graph import PatientGraphClient

client = PatientGraphClient("http://127.0.0.1:8798", timeout=10)
data = client.search(
    patient_ref="pseudo-patient-001",
    ehr_view=ehr_view,
    as_of="2026-01-09T07:00:00",
    queries=["CPAP", "呼吸窘迫"],
    top_k=10,
)
```

`build()` 返回图构造结果，`search()` 返回 `graph/hits/trace`；HTTP 错误抛 `PatientGraphClientError`，带 `status` 与响应体。

```python
import json
import urllib.request

payload = {
    "patient_ref": "pseudo-patient-001",
    "as_of": "2026-01-09T07:00:00",
    "ehr_view": ehr_view,
    "queries": ["CPAP", "呼吸窘迫"],
    "top_k": 10,
}
request = urllib.request.Request(
    "http://127.0.0.1:8798/api/patient-graph/search",
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=10) as response:
    result = json.loads(response.read().decode("utf-8"))
```

`curl`：

```bash
curl -s http://127.0.0.1:8798/api/patient-graph/search \
  -H 'Content-Type: application/json' \
  -d @patient-graph-request.json
```

服务默认监听地址与端口由启动配置决定；本机开发常用 `8798`。请求体上限 1 MB，超时由调用方设置；不要用浏览器直接上传未授权的“已可见快照”绕过后端取数。

## 7. 验证

```bash
cd rag
PYTHONPATH=.. python3 -m unittest tests.patient_graph.test_patient_graph_api
PYTHONPATH=.. python3 -m unittest tests.core.test_ehr_view_adapter
```

`tests/patient_graph/test_patient_graph_api.py` 覆盖 `ehr_view` 与 `patient_snapshot` 两种 HTTP 请求；`tests/core/test_ehr_view_adapter.py` 覆盖视图映射、未来报告过滤与知识图/患者图联动。
