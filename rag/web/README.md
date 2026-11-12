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
| `src/panels/` | 01–10 步骤视图：准备（`PreparePanels`）、执行（`ExecutePanels`）、结果（`ResultsPanel`）、指南与审计（`GuidelinesPanel`） |
| `src/components/` | 文件头、档案索引、底部运行记录、命令面板、诊断卡、证据详情、患者事实视图 |
| `src/styles/` | 设计令牌（`tokens.css`）与布局/组件/面板样式 |
| `src/lib/format.ts` | 标签、分数、JSON 解析与展示辅助 |

## 视觉方向

工作台按“临床证据控制台”设计：冷灰底、白色工作区、深青蓝主色，风险只用红色；全部无衬线（IBM Plex Sans + 系统中文黑体），数据与标识符用 IBM Plex Mono。结构由细横线、步骤编号与表格承载，圆角 4–6px、阴影极轻。布局为深色文件头 + 左侧索引 + 中央工作区（顶部常驻 10 步流程轨道）+ 底部可展开运行记录。流程轨道显示已完成 / 当前 / 未到达，点击可跳转；步骤切换、执行检索、结果数字与运行记录都有克制的过渡动画。正文按中文长文本排版：行宽约 62–68 字、行高 1.65–1.95、警告默认折叠为 2 条、长段落使用 `text-wrap: pretty`。

## 字体

拉丁字符使用 **IBM Plex Sans**（标题/正文）与 **IBM Plex Mono**（数据与标识符），来自 Fontsource 的 SIL OFL 开源字体，仅打包 latin `woff2` 子集（约 110 KB，无 CDN 依赖）。中文由系统字体回退：`Noto Sans CJK SC / PingFang SC / Microsoft YaHei`。

长文本样式参考了项目内 HIS 前端的临床文本处理：`line-height: 1.95`、`white-space: pre-wrap`、`overflow-wrap: anywhere`，用于引文、关键证据句与指南分块。

## 能力降级

工作台按 `/api/health` 的字段判断后端能力：缺少 `guideline_parsing` 时 09 步骤显示“未提供”，缺少 `entity_linking`/`guideline_lifecycle`/`claim_verifier` 时 05 的“校验与治理”自动隐藏。同一份构建可在 v6.7.0 发布线上运行。

## 约束

- 所有业务动作都落到真实 `/api/*`，前端不内置病例、快照或检索结果。
- 主题五档（浅色/深色/暖色/靛蓝/跟随系统），写入 localStorage，可用 `?theme=dark` 指定。
