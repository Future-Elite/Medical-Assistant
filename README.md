# EHR Dreamer · Graph-RAG 新生儿诊疗世界模型

## GSPO离线数据准备

最新精简版策略数据位于`data/gspo_compact_2024/`；四类动力学SFT数据位于`data/dynamics_sft_2024_v2/`。每份化验只使用一个“指标名(单位):值”的JSON，多步输出按天排列，不把溯源、解释或schema重复塞进模型对话。

```powershell
python -B scripts/compact_policy_dataset.py
python -B scripts/build_dynamics_sft.py
python -B scripts/validate_dynamics_sft.py
```

这些命令只生成和检查本地数据，不执行训练。

已增加按患者组织的CSV、verl嵌套Parquet、观察性动力学转移及72小时长程奖励接口。运行以下命令只做提取与校验，不训练、不调用模型API：

```powershell
python -m pip install -r requirements-gspo-data.txt
python -B scripts/build_gspo_dataset.py
python -B scripts/validate_gspo_dataset.py
```

数据写入本地忽略目录`data/gspo_2024/`。`policy_gspo.csv`每位患者一行，`output`在RL采样时生成；真实医生方案单独保存为参考，未冒称个体最优答案。长程奖励优先比较72小时终点异常数，再考虑过程异常负担和反弹；需要独立动力学与安全评价provider，当前未启用训练。详细数据字典与方法见本地`docs/EHR_DREAMER_GSPO_DATA.md`，配置说明为`examples/gspo_data_recipe.json`。

当前默认版本：**4.0，Graph-RAG策略Agent + 动力学LLM**。Python/FastAPI后端和Vue 3前端。策略先检索医学概念图并核对指征，动力学按治疗顺序和时间联合预测全部初始化指标，执行当前时间点的处置组合，复查后重新规划。

默认工作台分为三个区域：**策略Agent步骤、患者EHR、动力学指标变化**。Agent区域记录实际查询术语、证据路径、指征评估、候选及拒绝原因、最终方案；EHR支持时间病历和完整JSON；动力学区域显示逐次快照、变化量和全部指标的趋势。研究隐藏状态与医生可见报告分别展示，隐藏状态不进入策略输入。旧版本病历保留原样并提示重新生成，不补写历史日志。

本分支独立存放本项目。源代码中不包含API Key或真实Base URL。原始患者文件、数据库、模型产物、生成结果、论文、指南原文、spec及其他文档均保持本地；README和运行脚本纳入版本管理。

## 安装与启动

需要Python 3.10+、Node.js 20.19+或22.12+。在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

仅首次创建本地环境时复制模板。已有`.env`不要覆盖。在其中填写`EHRWORLD_API_KEY`和`EHRWORLD_BASE_URL`；这两个值没有代码内默认值。

```powershell
.\run_web.ps1 -Background
```

脚本安装/构建前端并由FastAPI统一提供服务。工作台：<http://127.0.0.1:8000/>；API文档：<http://127.0.0.1:8000/docs>。已构建时可用`-SkipBuild`。修改后端后需要停止旧进程并重新启动；脚本会复用已运行的服务。

开发模式：

```powershell
python -m uvicorn ehrworld.web.app:app --host 127.0.0.1 --port 8000
npm --prefix frontend ci
npm --prefix frontend run dev
```

Vue开发页面位于<http://127.0.0.1:5173/>，API由Vite代理转发。

## 本地数据

以下文件不随仓库发布，完整模拟需自行准备有权使用的来源：

| 路径 | 用途 |
|---|---|
| `2024带孕周的出院小结汇总.xlsx` | 入院背景与真实初始化验 |
| `guidelines/基层医疗卫生机构常见新生儿疾病诊疗指南：新生儿感染性肺炎（2025年）.pdf` | 肺炎指南来源校验 |
| `guidelines/《欧洲新生儿呼吸窘迫综合征管理共识指南2025版》解读.pdf` | RDS指南来源校验 |
| `test.json` | 仅旧版功能及历史ML对照使用 |

来源工作簿按既有18列结构读取，提取入院窗口实际记录的全部明确数值项目，不限于旧版7类指标，不将后续复查或出院结果补入初态。原始记录只读；比较符号、缺失、单位、允许负值的碱剩余和同日冲突保留。日级窗口不宣称严格同时或完全未经治疗，冲突不任取第一条或求均值。初始化、联合预测、评分及本轮复查是不同的指标集合。

**新引擎不依赖本地回归权重，也无需先运行训练脚本。** 本次是API驱动的LLM状态预测，没有进行大模型微调。缺少本地数据时页面可以启动，但无法生成完整病例。

## 工作流程

1. 选择具有真实初始化验及适用评价规则的病例。
2. 独立选择策略、动力学、结局模型。五种可选模型为`DeepSeek-V4.1-Flash`、`GLM-5.3`、`GLM-5.3-Flash`、`MiMo-V2.6-Flash`和`Qwen3.8-27B`。按模型配置请求关闭思考或使用最低强度；网关实际支持情况以返回为准。
3. 控制器解析症状、风险和治疗术语，遍历医学图谱并筛选人群条件；策略可申请最多2轮额外图谱查询。
4. 策略先输出带病例证据及推荐ID的指征评估，再提出候选。没有活动治疗禁止“维持”；有明确观察依据时才可选择暂不治疗观察。删除泛化的“supportive_care”占位动作。
5. 候选通过临床条件、术语、引用和时间校验后，动力学从医生可见资料建立的假设状态预测全部初始化指标，程序在共同终点计算研究异常数。拒绝原因写入Agent步骤，不凭少做动作赢得同分比较。
6. 执行所选方案所有`offset_hours=0`的当前处置组合，不执行未来操作；隐藏状态按实际事件和时间推进。PS等事件型操作与持续治疗分开记录。
7. 按计划复查；可用`review_panels`选择血常规、感染检验、电解质等该患者已有初态的面板。查询发布采样快照，不重生成指标。研究评分归零不再自动结束轨迹，也不等于疾病治愈或临床出院。

检查信息价值采用有界、针对性的复评安排，目前不是完整的POMDP求解。规划得到的是当前模型和候选预算下的近似最优，不是已验证的个体治疗因果最优。

## 医学图谱与证据

知识图谱包含疾病/临床问题、诊疗术语及别名、检查面板、人群条件、推荐、指南及论文来源；检索保留概念到推荐、动作、监测及来源的真实关系路径。使用版本化的有限来源摘要，不把所有PubMed检索命中自动变成临床规则。推荐是核对来源后的工程实现摘要，尚未经临床专家验证。

新增权威来源包括中国新生儿肺炎与败血症指南/共识、欧洲RDS 2025原版、NICE NG195、AAP按胎龄分层的EOS报告、WHO严重细菌感染及早产照护、CPS表面活性物质、AAP/NICE黄疸和相关配套指南。重点试验包括SUPPORT、COIN、OPTIMIST-A、NeoPInS、CAP及NeOProM。适用人群不同的推荐不能拼接为通用处方，群体疗效不转写为个体确定药效。

公开文献检索及缓存命令（不读取患者数据，不调用生成模型）：

```powershell
python -B scripts/update_neonatal_evidence.py --through 2026/11/12
python -B scripts/cache_neonatal_guidelines.py
```

需要使用Windows已启用的系统代理时，可加`--proxy`；脚本只读取该设置，不修改系统网络。检索目录和完整查询记录位于`outputs/neonatal_evidence/`，可访问指南页面/全文与校验记录位于`guidelines/evidence_2026/`。访问受限的文档记录状态，不绕过限制。检索具有明确范围，不声称穷尽全部医学文献。详细记录见本地`docs/graph_rag_evidence_2026-11-12.md`。

## 模型化验格式

运行时使用 `compact-labs-1.0` 协议：策略输入的 `visible_patient.history` 和 `current` 每份报告只包含一个 `labs` 字典，例如 `{"id":"L1","at_hours":0,"labs":{"pH":7.2,"PaCO2(mmHg)":62,"CRP(mg/L)":"<0.5"}}`。`current` 是最新一份报告，历史中不重复该报告；没有复查的项目保留在原报告中，不冒充当前测量。采样时间与延迟发布时间分别保留。

动力学输入使用相同的化验字典。`actions` 按入院后小时给出开始、调整和停止；`time_hours` 是模拟时钟，`predict_at_hours` 是预测终点。锚点时间不同时用 `current.sampled_at_hours` 保留各指标的采样时间。全部初始化指标在一次请求中联合预测，回复直接为 `{"pH":7.31,"PaCO2(mmHg)":48,"CRP(mg/L)":0.4}`，不再使用逐指标对象或 `predictions` 数组。示例数值仅用于说明格式。

键必须覆盖 `output_keys` 的全部项目且单位一致；未知用 `null`，检出限保留字符串。不接受漏项、额外字段、布尔值、普通数字字符串、非有限值或重复JSON键。程序在本地管理请求、分支、版本与时钟，验证成功后才提交隐藏状态；检验查询读取冻结的采样快照。这里的“全部”指当前患者已初始化的指标集合，不补造源文件中缺失的项目。

页面右上角“模型输入输出示例”及 [GET /api/dreamer/model-protocol](http://127.0.0.1:8000/api/dreamer/model-protocol) 提供完整 system/user messages 和动力学回复示例，由实际运行时组装函数生成，使用纯人工病例，不读取工作簿、不调用API。公开病历仍保留来源和审计元数据，精简只发生在模型交互层。本次没有重新制作数据集或执行训练。

## 评价与配置

前端支持决策间隔、预测跨度、候选数量、每方案预测样本数及决策轮次。默认小预算用于控制API耗时。模拟时钟与实际API耗时分开。

目标是在相同未来时点，让固定指标集合中的异常数量最少。集合包含起始正常项目，不会因少检查、丢指标或缺失值而取得低分。公开参考范围只作为有来源的研究评价口径，未匹配本院检测方法；适用性不明确的项目标记未评估。

默认血气与部分年龄适用规则来源见：

- [Safer Care Victoria 新生儿参考](https://www.safercare.vic.gov.au/best-practice-improvement/clinical-guidance/neonatal/normal-laboratory-values-for-neonates)：页面提示复审暂停，应优先使用本院范围。
- [University of Iowa WBC参考](https://www.healthcare.uiowa.edu/path_handbook/handbook/test1977.html)：足月儿且满足日龄条件时使用。

前端高级设置可以提交带`key`、`unit`、`lower`/`upper`和`source`的参考规则数组。PCT、胆红素等没有适用规则时不按成人范围评分。可通过API文档查看完整请求契约。

十人批次保持3名短、5名中、2名长预算，对应1–2、3–5、6–10轮上限，允许目标达到后提前结束。每名患者可以单独查看规划、病历及JSON/CSV，支持整批JSON导出。

## API与代码

| 位置 | 职责 |
|---|---|
| `ehrdreamer/dual/source.py` | 真实基线与来源隔离 |
| `ehrdreamer/dual/admission.py` | 全量入院数值提取、检出限与同日冲突 |
| `ehrdreamer/dual/knowledge.py` | 医学概念图、规范术语、关系遍历与人群筛选 |
| `ehrdreamer/dual/evidence.py` | 版本化的来源和诊疗推荐摘要 |
| `ehrdreamer/prompts.py` | 策略提案、选择及动力学提示词 |
| `ehrdreamer/dual/protocol.py` | 模型用的紧凑化验字典、时间锚点与输入投影 |
| `ehrdreamer/dual/planner.py` | 候选校验、独立预测与程序评分 |
| `ehrdreamer/dual/dynamics.py` | LLM转移、时间账本、请求去重与输出校验 |
| `ehrdreamer/dual/environment.py` | 实际隐藏态、采样快照、事务执行 |
| `ehrdreamer/dual/service.py` | 单人/批次、进度、结局与存储 |
| `frontend/src/DualApp.vue` | 默认双LLM工作台 |

主要接口位于`/api/dreamer`：`GET /health`、`GET /source`、`GET /cases`、`POST /generations`、`GET /generations/{id}`、病历导出及批次预览/创建/查询。`GET /knowledge`返回图谱及来源统计，`GET /knowledge/graph`返回节点和关系，`POST /knowledge/search`接受术语和可选病例/任务ID并返回实际检索路径。`GET /learning`返回架构及参考规则信息，不声称已微调或使用旧权重。

健康检查只说明本地服务状态；模型连通性与患者预测有效性需要另行验证。网关失败会记录明确错误，不自动切换成离线或旧回归模型。

## 验证与历史对照

```powershell
python -B -m pytest --basetemp=tmp/pytest -o cache_dir=tmp/pytest-cache
npm --prefix frontend test
npm --prefix frontend run build
```

双LLM测试使用人工病例和模拟SDK传输，验证协议、状态隔离、时间顺序、事务、批次和导出，不作为模型药效证据。历史ML回归测试需要本地模型产物，使用显式旧引擎测试入口；默认服务不加载这些权重。

完整测试集还包含依赖本地患者来源、指南原文和环境配置的集成用例。这些文件不会随Git仓库上传；新克隆的源码副本未准备相应输入时，相关用例会因缺少来源或配置而失败。运行完整回归前，请按上面的“本地数据”说明准备有权使用的文件，并单独配置本地`.env`，不要将这些内容提交到仓库。

旧`ehrdreamer/learning`及`scripts/train_dreamer.py`等脚本保留作实验对照。原EHRWorld前端位于`/?legacy=1`；无需API和患者数据的命令行处理示例仍可运行：

```powershell
python -m ehrworld demo --backend offline --output-dir outputs/offline_demo
python -m ehrworld --help
```

该离线示例验证原论文处理链路，不是双LLM患者环境的替代模型。
