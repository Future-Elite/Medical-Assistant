# Evidence Retrieval

这是 ChinaLLM 的独立、只读证据检索模块，面向 Agent 工作流提供可追溯的外部证据包。模块不读取真实病历，不写回 HIS，不生成诊断或治疗结论；上层应用只能据此组织带有来源和不确定性的回答。

模块当前通过 PubMed E-utilities 获取实时文献，并支持可选的标准 HTTP OpenAI-compatible LLM 摘要和 MiniCheck 主张校验。检索失败、模型未配置或校验器不可用时，系统会明确返回状态，不使用合成内容冒充外部证据或模型结果。

## 核心能力

- 受控中文查询计划：由原始问题、人工审核的最小化术语和中英文术语映射组成，不把原始患者病历发送给外部服务。
- 证据检索与排序：支持词面相关度、来源内 RRF、权威性与时效性重排、DOI/PMID/标题去重和结果多样化。
- 时间与状态控制：排除晚于查询截止时间的文献，并处理撤回、未知日期等状态。
- 可追溯引用：每条证据保留来源标识、原文摘录、定位信息和 `open_evidence` 动作；引用 ID 绑定单次 `retrieval_id`，不能跨检索复用。
- 关键证据句定位：在文档过滤和排序完成后，提取与查询词正向匹配的原文句，记录句子 ID、句序、字符偏移、匹配词和透明的词面分数。
- 回答审计：检索后检查引用完整性、引用有效性和主张支持状态；只有真实 MiniCheck 完成推理时才报告句子级支持结果。
- 只读本地工作台：提供检索、引文打开、回答审计和健康检查接口，不提供写回或诊疗操作。

关键证据句只是定位辅助，不是临床正确性、指南符合性或证据质量判断，也不能替代 MiniCheck、人工审核或完整原文核对。

## 模块结构

- `models.py`：JSON 优先的数据契约，未知外部字段保存在 `metadata`。
- `connectors.py`：知识源接入面，当前服务注册实时 PubMed 连接器；其他只读来源可实现相同连接器契约后接入。
- `query.py`：查询计划和术语映射，限制外部服务只能收到完成检索所需的最小信息。
- `ranking.py`：相关度、来源权威性、时效性、去重和多样化排序。
- `evidence.py`：中英文混合文本的句子切分、匹配和字符定位。
- `pipeline.py`：检索、过滤、排序、引用包生成和关键证据句附加。
- `agent.py`：`retrieve_evidence`、`open_citation` 和 `audit_grounded_answer` 工具。
- `audit.py`：引用审计和可选 MiniCheck 适配器。
- `pubmed.py`：通过 NCBI E-utilities 实时获取 PMID、标题、摘要、DOI、期刊、日期和 PubMed URL。
- `llm.py`：受本轮证据包约束的标准 HTTP LLM 摘要适配器。
- `server.py`：本地 HTTP 工作台和接口入口。

## Agent 接入

模块对外提供证据检索、引用打开和回答审计三个只读工具。调用方提交查询问题、截止时间、人工审核的最小化术语和来源范围，得到带 `retrieval_id`、来源定位、引用和关键证据句的证据包；后续引用和审计请求必须携带同一轮检索标识。

连接器返回 `SourceDocument` 或包含 `document_id`、`kind`、`title`、`text` 的字典。PubMed 条目应提供 `pmid`、`doi` 和 `published_at` 等来源字段。连接器不得把原始患者病历、患者标识或自由文本病史发送到外部服务。

## 本地工作台

从当前模块目录启动：

```bash
python3 server.py
# open http://127.0.0.1:8788/
```

页面支持中文问题、证据截止日期、最小化患者术语和来源范围输入。结果区展示检索状态、时间过滤提示、逐条排序依据、完整摘录、关键证据句、句序、字符偏移、匹配度和命中词。生成摘要后，系统会在同一 `retrieval_id` 内自动执行回答审计。

当前工作台只注册实时 PubMed 连接器，不使用本地或合成证据回退。主要接口如下：

- `GET /api/health`：返回服务、实时来源、LLM 和 MiniCheck 的脱敏状态。
- `GET /api/tools`：返回 Agent 工具契约。
- `POST /api/retrieve`：执行检索，可选择生成受证据约束的摘要。
- `POST /api/citation`：打开本轮检索中的引用。
- `POST /api/audit`：审计带有本轮 `retrieval_id` 的回答。

## 配置

统一配置入口为 `config.py`。配置文件只保留本地值，不要提交真实 API key、患者资料或其他凭据；修改后需重启服务。

常用字段包括：

```python
openai_api_key = ""
openai_model = ""
openai_base_url = ""
openai_api_mode = "auto"
ncbi_email = ""
ncbi_api_key = ""
minicheck_model = ""
minicheck_cache_dir = ""
server_host = "127.0.0.1"
server_port = 8788
```

LLM 只接收用户问题和本轮检索证据。缺少配置时返回“未生成”，不会输出模板答案；请求失败时保留失败状态。MiniCheck 只有在依赖、模型和配置均可用时才执行真实推理，否则主张支持状态为 `not_checked`。服务日志和健康检查不得泄露凭据。

## 测试

在当前模块目录运行：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m compileall -q .
```

测试覆盖数据契约、查询与排序、关键证据句定位、Agent 工具、检索管线、回答审计、GUI、LLM 和 PubMed 录制响应。测试使用合成或录制数据，不调用实时 PubMed，不请求 LLM，也不下载 MiniCheck 模型；实时服务和外部模型应另行进行带凭据的集成验收。

## 使用边界

- 实时 PubMed 结果具有网络、排序和服务状态的不确定性，不应直接作为可复现实验数据集。
- 排序分数、关键词匹配和引用存在性不等于临床证据质量、指南推荐等级或医学事实正确性。
- LLM 摘要和审计结果不是诊疗结论；使用者必须核对完整原文、来源定位和适用人群。
- 当前模块未接入真实 HIS/PACS、正式指南库、药品数据库或临床试验库，也不支持病历写回。
- 合成测试数据只用于机制和接口验证，不支持临床有效性或医院部署结论。
