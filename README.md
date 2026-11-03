# EHR Dreamer · 新生儿诊疗世界模型

Python + FastAPI 后端，Vue 3 前端。医生策略、患者动力学和结局文书分别实现，支持新生儿肺炎、RDS相关病程的单人模拟、十人批量生成、时间线展示和病历导出。保留原 EHRWorld 的数据处理、双模式预测、评估与命令行工具；不进行大模型微调。

本分支独立存放 EHR 项目。仓库仅发布源代码、依赖清单、配置模板及本 README；API Key、真实 API Base URL、患者数据、模型权重、生成结果、论文、指南原文和其他说明文档均不随代码上传。

## 环境与依赖

- Python 3.10+，Node.js 20.19+ 或 22.12+。
- 在本项目根目录执行命令。Windows 可使用 PowerShell。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

在本地 `.env` 中填写 `EHRWORLD_API_KEY` 和 `EHRWORLD_BASE_URL`，这两个值没有代码内默认配置。`.env.example` 只保留空值。`.env` 已被忽略，请勿提交。

可选模型：`DeepSeek-V4.1-Flash`、`GLM-5.3`、`GLM-5.3-Flash`、`MiMo-V2.6-Flash`、`Qwen3.8-27B`。模型选择作用于医生策略和结局文书；患者动态使用本地训练模型。请求按模型配置关闭思考或使用最低思考强度，实际支持取决于接入网关。

## 准备本地数据与模型

完整 EHR Dreamer 模拟需要自行准备有权使用的原始数据及指南。代码不会从 GitHub 下载这些文件，也不会用虚构训练数据替代缺失文件。

| 本地路径 | 用途 |
|---|---|
| `2024带孕周的出院小结汇总.xlsx` | 入院前资料提取及有时间依据的检验训练样本 |
| `test.json` | 可选的横断面训练补充；旧版批次功能需要此文件 |
| `guidelines/基层医疗卫生机构常见新生儿疾病诊疗指南：新生儿感染性肺炎（2025年）.pdf` | 肺炎指南来源校验 |
| `guidelines/《欧洲新生儿呼吸窘迫综合征管理共识指南2025版》解读.pdf` | RDS指南来源校验 |
| `outputs/dreamer_ml/model/` | 本地训练产生的模型及校验信息 |

指南文件由 `ehrworld/web/guidelines.py` 中的来源与哈希核验。更换来源时应审核对应规则与来源记录；缺失或不匹配时会明确报错。

准备好数据后训练：

```powershell
python -B scripts/train_dreamer.py
python -B scripts/evaluate_dreamer_rollout.py
```

训练脚本会提取数据、按患者分区评估、选择模型并输出流程参考。已有当前版本且校验通过的训练产物时无需重复训练。模型权重、提取样本、报告和数据库均保持本地，不在仓库中发布。

## 启动前后端

```powershell
.\run_web.ps1
```

脚本首次安装前端依赖并构建，由 FastAPI 同时提供前端与 API：

- 工作台：<http://127.0.0.1:8000/>
- API文档：<http://127.0.0.1:8000/docs>
- EHR Dreamer健康状态：`GET /api/dreamer/health`
- 旧版工作台：<http://127.0.0.1:8000/?legacy=1>

后台运行：`.\run_web.ps1 -Background`；已完成构建时可加 `-SkipBuild`。修改前端后应重新构建。

开发时也可在两个终端中分别运行：

```powershell
python -m uvicorn ehrworld.web.app:app --host 127.0.0.1 --port 8000
npm --prefix frontend ci
npm --prefix frontend run dev
```

Vue开发服务器位于 <http://127.0.0.1:5173/>，API通过Vite代理转发。缺少本地数据时可以启动页面并查看就绪状态；完整患者模拟需要上述数据、指南及训练产物。离线闭环演示使用同一患者模型和本地医生控制器，不调用外部API；API模式失败会明确报错。

## 三阶段生成流程

1. **治疗前**：仅以入院检查前可知信息作为起点，一次性下达入院检查组，发布汇总报告并形成工作诊断。
2. **治疗中**：先执行治疗或支持观察，再按计划复查相关指标。禁止重复申请整套入院检查；默认最多两轮针对性复查，可设置1–4轮。
3. **治疗后**：汇总实际执行的治疗、复查前后指标和逐诊断结局。流程完成不自动等于治愈或获准临床出院；证据不足保留待评估。

医生可见观测与隐藏患者状态分离。检查产生申请、采样及延迟报告；治疗操作返回 `null`，后续复查才提供新观测。批量功能一次生成10名独立患者，可逐人打开病历；操作预算只限制成本，不要求用完。

患者动力学使用真实记录训练的初始分布及日级转移模型，当前支持部分血气、炎症与胆红素指标。训练未识别完整生命体征动态、个体用药剂量反应或因果疗效；不以手写生理方程填补缺失能力。病历使用统一住院结构，尚未映射到特定医院HIS字段。

## 脚本与验证

`scripts/` 中保留训练、数据分析、流程参考、滚动评估、API探测与运行验证脚本；`run_web.ps1`、Python模块入口、Vue源代码、依赖锁文件和测试代码均纳入版本管理。

```powershell
python -B -m pytest --basetemp=tmp/pytest -o cache_dir=tmp/pytest-cache
npm --prefix frontend test
npm --prefix frontend run build
```

部分集成测试需要上述本地数据及模型；API交互单测使用模拟传输，不需要真实凭据。测试临时文件及生成结果不会提交。

保留的原 EHRWorld 离线示例可以在不提供患者数据或API配置时运行：

```powershell
python -m ehrworld demo --backend offline --output-dir outputs/offline_demo
python -m ehrworld --help
```

原论文离线模式是验证处理链路的保持值基线，不代表论文训练权重或医学预测效果。原论文API版本复现数据处理、状态更新、双模式响应和评估协议，不承诺论文的数值成绩。

## 忽略规则

本项目全部忽略规则集中在根目录 `.gitignore`：排除 spec及其他文档、私人环境配置、原始数据、模型权重、输出、缓存与依赖安装目录。README、`requirements.txt`、`pyproject.toml`、`package.json`、锁文件、运行脚本及源码保留。真实接口配置和本地原始文件只需在使用机器上准备。
