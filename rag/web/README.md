# rag/web — 新生儿证据检索工作台（前端）

React 19 + TypeScript + Vite 单页应用，由 `rag/server.py` 直接托管构建产物。页面不依赖 CDN，运行时不需要 Node。

## 构建与开发

```bash
npm install
npm run build      # 产物写入 dist/，由 rag/server.py 在 / 与 /assets/* 提供
npm run dev        # 开发服务器 http://127.0.0.1:5173，/api 代理到 127.0.0.1:8798
```

`dist/` 随仓库提供，因此只跑 Python 服务时无需安装 Node。修改前端后必须重新 `npm run build`。

## 目录

| 路径 | 作用 |
|---|---|
| `src/App.tsx` | 应用外壳与步骤路由 |
| `src/state/workbench.tsx` | 全局状态、真实 API 动作、流程状态与启动加载 |
| `src/api/client.ts` | HTTP 封装、API 调用日志、错误字段（`error_id`/`exception_type`） |
| `src/panels/` | 核心阶段 + 技术步骤视图：快照/编排（`PreparePanels`）、执行（`ExecutePanels`）、证据包（`ResultsPanel`）、指南与审计（`GuidelinesPanel`，指南页内嵌 `GuidelineGraphCard`） |
| `src/components/` | 文件头、核心链路轨道（`FlowTrack`）、档案索引、底部运行记录、命令面板、诊断卡、证据详情、患者事实视图、患者图谱（`PatientGraph`）、指南图谱（`GuidelineGraph`）、知识图谱与双向检索（`KnowledgeAudit`） |
| `src/styles/` | 设计令牌（`tokens.css`）与布局/组件/面板样式 |
| `src/lib/format.ts` | 标签、分数、JSON 解析与展示辅助 |

## 视觉方向

工作台按“临床证据控制台”设计：冷灰底、白色工作区、深青蓝主色，风险只用红色；全部无衬线（IBM Plex Sans + 系统中文黑体），数据与标识符用 IBM Plex Mono。结构由细横线、步骤编号与表格承载，圆角 4–6px、阴影极轻。布局为深色文件头 + 左侧 6 阶段索引 + 中央工作区（顶部常驻 6 阶段核心链路 + 当前阶段技术步骤子轨）+ 底部可展开运行记录。核心链路对应患者快照 → 检索编排 → 执行检索 → 指南与适用性 → 证据包 → 审计，显示已完成 / 当前 / 未到达，点击可跳转；步骤切换、执行检索、结果数字与运行记录都有克制的过渡动画。正文按中文长文本排版：行宽约 62–68 字、行高 1.65–1.95、警告默认折叠为 2 条、长段落使用 `text-wrap: pretty`。

## 字体

拉丁字符使用 **IBM Plex Sans**（标题/正文）与 **IBM Plex Mono**（数据与标识符），来自 Fontsource 的 SIL OFL 开源字体，仅打包 latin `woff2` 子集（约 110 KB，无 CDN 依赖）。中文由系统字体回退：`Noto Sans CJK SC / PingFang SC / Microsoft YaHei`。

长文本样式参考了项目内 HIS 前端的临床文本处理：`line-height: 1.95`、`white-space: pre-wrap`、`overflow-wrap: anywhere`，用于引文、关键证据句与指南分块。

## 能力降级

工作台按 `/api/health` 的字段判断后端能力：缺少 `guideline_parsing` 时 08 指南与适用性步骤显示“未提供”，缺少 `entity_linking`/`guideline_lifecycle`/`claim_verifier` 时 05 的“校验与治理”自动隐藏。同一份构建可在 v6.7.0 发布线上运行。

## 约束

- 所有业务动作都落到真实 `/api/*`，前端不内置病例、快照或检索结果。
- 指南图谱卡片只读 `/api/knowledge/guideline-graphs`；节点是 `review_status=extracted` 的解析候选，不是临床审核指南。
- 08 指南与适用性页先展示本次检索得到的 Top-k 指南，再按“解析（按 Top-k）→ 建图 → 双向检索 → 适用性链路”推进；解析目标就是检索到的 Top-k，不手动挑指南，`查看原文 / 分块` 只在 Top-k 内切换。Top-k 按 `source_id` 去重，建图调用 `/api/knowledge/guideline-graphs/build`。解析设置、HIS 条件预览和 PDF/分块默认折叠。
- 07 执行检索页的“迭代检索（Agentic）”卡把来源勾选作为 `source_kinds` 传入 `/api/knowledge/iterative-retrieve`，显示轮次、来源命中、缺失来源与停止原因。
- 双向图检索调用 `/api/knowledge/guideline-fusion`：已有检索结果时直接传入 Top-k `guideline_ids`，否则按查询选 top-k；加载/现场建对应指南图与单个患者图融合。结果保存在 workbench，生成适用性链路时作为 `knowledge_fusion` 传入 `/api/applicability`，链路显示“双向图证据”和“指南图谱候选/其他指南图候选”节点。
- 图证据只作患者记录定位和复核提示，不改变 `match/mismatch/unknown`、`outcome` 或 `eligible_for_simulation`；跨指南候选明确标注“不同源，只作旁证”。
- 主题五档（浅色/深色/暖色/靛蓝/跟随系统），写入 localStorage，可用 `?theme=dark` 指定。
