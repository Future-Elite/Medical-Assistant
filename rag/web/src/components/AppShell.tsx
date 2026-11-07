import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./Icon";
import { Badge, StatusRow } from "./primitives";
import { STEP_ORDER, useWorkbench, type ThemeChoice } from "../state/workbench";
import { NAV_GROUPS, STEP_META, stepNumber } from "../lib/workflow-steps";
import { sourceLabels } from "../lib/formatting";
import type { StepKey } from "../types";

const THEME_LABELS: Record<ThemeChoice, string> = {
  light: "浅色",
  dark: "深色",
  warm: "暖色",
  indigo: "靛蓝",
  auto: "跟随系统",
};

export function Topbar({
  onToggleRunlog,
  onToggleSidebar,
}: {
  onToggleRunlog: () => void;
  onToggleSidebar: () => void;
}) {
  const { step, health, backendError, theme, setTheme, running, runRetrieval, setCmdOpen } =
    useWorkbench();
  const meta = STEP_META[step];
  const readyMethods = Object.entries(health?.retrieval_methods || {})
    .filter(([, value]) => value.available)
    .map(([key]) => key);
  const pillTone = backendError ? "bad" : health ? "" : "pending";

  return (
    <header className="topbar">
      {running ? <span className="exec-progress" aria-hidden="true" /> : null}
      <button
        type="button"
        className="btn btn-ghost btn-sm mobile-only"
        onClick={onToggleSidebar}
        aria-label="打开档案索引"
      >
        <Icon name="list" size={16} />
      </button>
      <div className="brand">
        <div className="brand-mark">CL</div>
        <div className="brand-text">
          <b>ChinaLLM</b>
          <span>新生儿证据检索</span>
        </div>
      </div>
      <div className="topbar-center">
        <div className="crumbs">
          <span className="step-num">{stepNumber(step)}</span>
          <span className="sep">/</span>
          <b>{meta.label}</b>
        </div>
      </div>
      <div className="topbar-actions">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => setCmdOpen(true)}
          title="命令面板 (Ctrl/⌘ + K)"
        >
          <Icon name="search" size={14} />
          <span className="btn-label">命令</span>
          <span className="badge">⌘K</span>
        </button>
        <span
          className={`health-pill ${pillTone}`}
          title={backendError || (health ? `后端 v${health.rag_version}` : "后端健康状态")}
        >
          <span className="dot" />
          {backendError
            ? "后端不可用"
            : health
              ? `${readyMethods.length} 项就绪`
              : "正在检查后端"}
        </span>
        <select
          className="theme-select"
          value={theme}
          onChange={(event) => setTheme(event.target.value as ThemeChoice)}
          aria-label="配色主题"
        >
          {Object.entries(THEME_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onToggleRunlog} title="运行记录与 API 日志">
          <Icon name="terminal" size={14} />
          <span className="btn-label">运行记录</span>
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={runRetrieval}
          disabled={running}
        >
          <Icon name="play" size={13} />
          {running ? "检索中…" : "执行检索"}
        </button>
      </div>
    </header>
  );
}

export function Sidebar({
  open,
  onNavigate,
}: {
  open: boolean;
  onNavigate: () => void;
}) {
  const { step, goStep, stepStatus, health } = useWorkbench();

  return (
    <aside className={`sidebar ${open ? "open" : ""}`} aria-label="档案索引">
      <div className="sidebar-scroll">
        {NAV_GROUPS.map((group) => (
          <div className="nav-group" key={group.title}>
            <div className="nav-group-title">{group.title}</div>
            {group.steps.map((key) => {
              const meta = STEP_META[key];
              const status = stepStatus[key];
              return (
                <button
                  key={key}
                  type="button"
                  className={`nav-item ${step === key ? "active" : ""}`}
                  onClick={() => {
                    goStep(key);
                    onNavigate();
                  }}
                >
                  <span className="nav-num">{stepNumber(key)}</span>
                  <span className="nav-label">{meta.label}</span>
                  <span className={`nav-status ${status.tone}`}>{status.text}</span>
                </button>
              );
            })}
          </div>
        ))}
      </div>
      <div className="sidebar-foot">
        <div>
          后端 <b>{health ? "在线" : "—"}</b> · 模式{" "}
          <b>{health?.active_mode || "—"}</b>
        </div>
        <div>合成演示 · 只读检索 · 非诊疗结论</div>
      </div>
    </aside>
  );
}

export function RunLog({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const {
    health,
    tools,
    backendError,
    apiLog,
    clearApiLog,
    demoGuidelines,
    loadDemoSources,
    clearSources,
    registeredCount,
    backendCheck,
  } = useWorkbench();

  const readyMethods = Object.entries(health?.retrieval_methods || {})
    .filter(([, value]) => value.available)
    .map(([key]) => key);
  const notReady = Object.entries(health?.retrieval_methods || {})
    .filter(([, value]) => !value.available)
    .map(([key]) => key);
  const library = health?.term_mapping_library || {};

  return (
    <section className={`runlog ${open ? "open" : ""}`} aria-label="运行记录">
      <button type="button" className="runlog-bar" onClick={onToggle} aria-expanded={open}>
        <span className="runlog-title">运行记录</span>
        <span className="runlog-summary">
          <span>
            后端 <b>{health ? "在线" : "—"}</b>
          </span>
          <span>
            已注册来源 <b>{registeredCount}</b>
          </span>
          <span>
            API 调用 <b>{apiLog.length}</b>
          </span>
          <span>
            工具 <b>{tools?.tools?.length ?? "—"}</b>
          </span>
        </span>
        <span className="runlog-toggle">
          {open ? "收起" : "展开"}
          <Icon name="chevronDown" size={13} />
        </span>
      </button>
      <div className="runlog-body">
        <div className="runlog-col">
          <h4>后端与契约</h4>
          {backendError ? (
            <div className="hint" style={{ color: "var(--cinnabar)" }}>{backendError}</div>
          ) : (
            <dl className="kv">
              <dt>版本</dt>
              <dd>{health ? `v${health.rag_version}` : "—"}</dd>
              <dt>模式</dt>
              <dd>{health?.active_mode || "—"}</dd>
              <dt>HIS</dt>
              <dd>调用方快照</dd>
              <dt>生成</dt>
              <dd>不生成</dd>
              <dt>临床决策</dt>
              <dd>不执行</dd>
            </dl>
          )}
          <div className="row" style={{ marginTop: 10 }}>
            <button type="button" className="btn btn-sm" onClick={backendCheck}>
              <Icon name="refresh" size={12} />
              重新自检
            </button>
          </div>
        </div>

        <div className="runlog-col">
          <h4>检索方法就绪</h4>
          <div className="status-list">
            <StatusRow label="就绪" value={readyMethods.join("、") || "bm25"} />
            <StatusRow label="未就绪" value={notReady.join("、") || "无"} />
            <StatusRow label="PubMed" value={`${health?.pubmed?.max_results ?? "—"} 条`} />
            <StatusRow
              label="映射库"
              value={library.configured ? `${library.rows ?? 0} 概念` : "未配置"}
            />
          </div>
        </div>

        <div className="runlog-col">
          <h4>来源库</h4>
          {demoGuidelines.length ? (
            demoGuidelines.map((row) => (
              <div className="source-item" key={row.source_id}>
                <b>{sourceLabels[row.source_id] || row.source_id}</b>
                <span>
                  {row.document_count} 条 · {row.synthetic ? "合成来源" : "含真实书目元数据"}
                </span>
              </div>
            ))
          ) : (
            <div className="hint">暂无演示来源</div>
          )}
          <div className="row" style={{ marginTop: 8 }}>
            <button type="button" className="btn btn-sm" onClick={loadDemoSources}>
              加载来源库
            </button>
            <button type="button" className="btn btn-sm btn-danger" onClick={clearSources}>
              清空
            </button>
          </div>
        </div>

        <div className="runlog-col">
          <h4>API 调用日志</h4>
          <div className="api-log">
            {apiLog.length ? (
              apiLog.map((row) => (
                <div className="api-row" key={row.id} data-id={row.id} title={`${row.at} · ${row.url}`}>
                  <span>
                    {row.method} {row.url}
                  </span>
                  <span className={row.status === 200 ? "ok" : "err"}>
                    {row.status} · {row.ms}ms
                  </span>
                </div>
              ))
            ) : (
              <div className="hint">尚无调用</div>
            )}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button type="button" className="btn btn-sm" onClick={clearApiLog}>
              清空日志
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

export function ToastStack() {
  const { toasts, dismissToast } = useWorkbench();
  return (
    <div className="toast-stack" aria-live="polite">
      {toasts.map((toast) => (
        <div className={`toast ${toast.tone}`} key={toast.id}>
          <Icon name={toast.tone === "error" ? "alert" : toast.tone === "success" ? "check" : "info"} size={15} />
          <div style={{ flex: 1 }}>{toast.message}</div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            style={{ height: 20, padding: 0 }}
            onClick={() => dismissToast(toast.id)}
          >
            <Icon name="x" size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}

export function CommandPalette() {
  const {
    cmdOpen,
    setCmdOpen,
    goStep,
    stepStatus,
    runRetrieval,
    backendCheck,
    loadDemoSources,
    setTheme,
  } = useWorkbench();
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const actions = useMemo(() => {
    const stepActions = STEP_ORDER.map((key) => ({
      id: `step-${key}`,
      label: `跳转到 ${stepNumber(key)} · ${STEP_META[key].label}`,
      hint: stepStatus[key].text,
      run: () => goStep(key),
    }));
    return [
      ...stepActions,
      { id: "run", label: "执行检索", hint: "POST /api/retrieve", run: runRetrieval },
      { id: "health", label: "后端自检", hint: "GET /api/health", run: backendCheck },
      { id: "sources", label: "加载演示来源库", hint: "POST /api/demo/guidelines/load", run: loadDemoSources },
      { id: "theme-light", label: "切换主题：浅色", hint: "light", run: () => setTheme("light") },
      { id: "theme-dark", label: "切换主题：深色", hint: "dark", run: () => setTheme("dark") },
    ];
  }, [goStep, stepStatus, runRetrieval, backendCheck, loadDemoSources, setTheme]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return actions;
    return actions.filter(
      (action) =>
        action.label.toLowerCase().includes(q) || action.hint.toLowerCase().includes(q),
    );
  }, [actions, query]);

  useEffect(() => {
    if (cmdOpen) {
      setQuery("");
      setCursor(0);
      window.setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [cmdOpen]);

  if (!cmdOpen) return null;

  const close = () => setCmdOpen(false);
  const execute = (index: number) => {
    const action = filtered[index];
    if (!action) return;
    close();
    action.run();
  };

  return (
    <div className="cmd-backdrop" onClick={close} role="presentation">
      <div className="cmd-panel" onClick={(event) => event.stopPropagation()}>
        <input
          ref={inputRef}
          className="cmd-input"
          placeholder="搜索步骤或动作…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setCursor(0);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") close();
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setCursor((prev) => Math.min(prev + 1, filtered.length - 1));
            }
            if (event.key === "ArrowUp") {
              event.preventDefault();
              setCursor((prev) => Math.max(prev - 1, 0));
            }
            if (event.key === "Enter") {
              event.preventDefault();
              execute(cursor);
            }
          }}
        />
        <div className="cmd-list">
          {filtered.length ? (
            filtered.map((action, index) => (
              <button
                key={action.id}
                type="button"
                className={`cmd-item ${index === cursor ? "active" : ""}`}
                onMouseEnter={() => setCursor(index)}
                onClick={() => execute(index)}
              >
                <Icon name="chevronRight" size={14} />
                <span>{action.label}</span>
                <span className="kbd">{action.hint}</span>
              </button>
            ))
          ) : (
            <div className="cmd-empty">没有匹配的步骤或动作</div>
          )}
        </div>
      </div>
    </div>
  );
}
