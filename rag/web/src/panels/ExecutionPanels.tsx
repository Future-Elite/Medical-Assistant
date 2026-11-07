import { useWorkbench } from "../state/workbench";
import { Badge, Empty, Field, Metric, Notice, PageHeader } from "../components/primitives";
import { Icon } from "../components/Icon";

export function RequestPanel() {
  const { requestPreview, form, running, runRetrieval, goStep } = useWorkbench();
  const request = requestPreview.request;
  return (
    <>
      <PageHeader
        kicker="执行 · 请求组装"
        title="请求组装"
        description="工作台把 01–05 的输入组装为 RetrievalRequest；这里展示即将发送的真实请求体。"
        actions={
          <Badge tone={requestPreview.error ? "error" : request?.question ? "ready" : ""}>
            {requestPreview.error ? "JSON 错误" : request?.question ? "可执行" : "待组装"}
          </Badge>
        }
      />
      <div className="page-body">
        {requestPreview.error ? <Notice tone="error">{requestPreview.error}</Notice> : null}
        <div className="split-preview">
          <div className="stack">
            <div className="endpoint-card">
              <span className="endpoint-method">POST</span>
              <div className="endpoint-path">/api/retrieve</div>
              <div className="hint" style={{ marginTop: 8 }}>
                返回 EvidencePackage：ranked_documents、citations、patient_hits、trace 与 warnings。
              </div>
            </div>
            <div className="subcard">
              <div className="subcard-title">请求摘要</div>
              <div className="status-list">
                <div className="status-row">
                  <span>question</span>
                  <span>{request?.question ? `${request.question.slice(0, 80)}${request.question.length > 80 ? "…" : ""}` : "缺少"}</span>
                </div>
                <div className="status-row">
                  <span>source_mode</span>
                  <span>{form.sourceMode}</span>
                </div>
                <div className="status-row">
                  <span>source_kinds</span>
                  <span>{form.sourceKinds.join("、") || "—"}</span>
                </div>
                <div className="status-row">
                  <span>top_k</span>
                  <span>{form.topK}</span>
                </div>
                <div className="status-row">
                  <span>patient_snapshot</span>
                  <span>{request?.patient_snapshot ? "已附加" : "未附加"}</span>
                </div>
              </div>
            </div>
            <button
              type="button"
              className="btn btn-primary btn-block"
              onClick={runRetrieval}
              disabled={running || !request?.question}
            >
              <Icon name="play" size={14} />
              {running ? "正在检索…" : "执行检索"}
            </button>
          </div>
          <Field label="请求 JSON">
            <pre className="code-block" style={{ minHeight: 420 }}>
              {requestPreview.json || requestPreview.error || "尚未组装"}
            </pre>
          </Field>
        </div>
      </div>
      <div className="page-nav">
        <button type="button" className="btn" onClick={() => goStep("method")}>
          <Icon name="chevronLeft" size={14} />
          上一步
        </button>
        <button type="button" className="btn" onClick={() => goStep("run")}>
          执行检索
          <Icon name="chevronRight" size={14} />
        </button>
      </div>
    </>
  );
}

export function RunPanel() {
  const {
    running,
    runError,
    pkg,
    runRetrieval,
    goStep,
    controllerPolicy,
    setControllerPolicy,
    controllerResult,
    controllerRunning,
    runController,
  } = useWorkbench();

  return (
    <>
      <PageHeader
        kicker="执行 · 检索"
        title="执行检索"
        description="一次检索依次完成分源收集、来源内排序、联合选择与引用生成；也可运行逐轮控制器。"
        actions={
          <Badge tone={running ? "running" : runError ? "error" : pkg ? "done" : ""}>
            {running ? "执行中" : runError ? "失败" : pkg ? "完成" : "待执行"}
          </Badge>
        }
      />
      <div className="page-body">
        <div className="card">
          <div className="card-head">
            <h3>检索流水线</h3>
            <span className="hint">只读 · 不生成临床结论</span>
          </div>
          <div className="card-body stack">
            <div className={`pipeline ${running ? "running" : ""}`.trim()}>
              {["分源收集", "来源内排序", "联合选择", "引用生成"].map((label, index) => (
                <span key={label} className="pipeline-item">
                  <span
                    className="pipeline-step"
                    style={running ? { animationDelay: `${index * 0.16}s` } : undefined}
                  >
                    <i>{index + 1}</i>
                    {label}
                  </span>
                  {index < 3 ? <span className="pipeline-arrow">→</span> : null}
                </span>
              ))}
            </div>
            {runError ? <Notice tone="error">{runError}</Notice> : null}
            <div className="row">
              <button
                type="button"
                className="btn btn-primary"
                onClick={runRetrieval}
                disabled={running}
              >
                <Icon name="play" size={14} />
                {running ? "正在检索…" : "执行检索"}
              </button>
              {pkg ? (
                <button type="button" className="btn" onClick={() => goStep("results")}>
                  查看结果
                  <Icon name="arrowRight" size={14} />
                </button>
              ) : null}
            </div>
            {pkg ? (
              <div className="metrics">
                <Metric label="候选证据" value={pkg.trace?.candidate_count ?? 0} />
                <Metric label="最终返回" value={pkg.trace?.selected_count ?? 0} accent />
                <Metric label="指南条目" value={(pkg.ranked_documents || []).filter((row: any) => row.document.kind === "guideline").length} />
                <Metric label="成功来源" value={(pkg.trace?.connector_trace || []).filter((row: any) => row.status === "ok").length} />
              </div>
            ) : (
              <Empty title="尚未执行">执行后在这里显示本次输出摘要。</Empty>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>逐轮检索控制器</h3>
            <Badge tone={controllerResult ? "done" : ""}>controller-v1-trial</Badge>
          </div>
          <div className="card-body stack">
            <div className="form-grid c4">
              <Field label="max_rounds">
                <input
                  className="input"
                  type="number"
                  min={0}
                  max={8}
                  value={controllerPolicy.max_rounds}
                  onChange={(event) =>
                    setControllerPolicy({ max_rounds: Number(event.target.value) || 0 })
                  }
                />
              </Field>
              <Field label="max_cost">
                <input
                  className="input"
                  type="number"
                  min={0}
                  step={0.5}
                  value={controllerPolicy.max_cost}
                  onChange={(event) =>
                    setControllerPolicy({ max_cost: Number(event.target.value) || 0 })
                  }
                />
              </Field>
              <Field label="min_evidence">
                <input
                  className="input"
                  type="number"
                  min={0}
                  value={controllerPolicy.min_evidence}
                  onChange={(event) =>
                    setControllerPolicy({ min_evidence: Number(event.target.value) || 0 })
                  }
                />
              </Field>
              <Field label="min_coverage">
                <input
                  className="input"
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={controllerPolicy.min_coverage}
                  onChange={(event) =>
                    setControllerPolicy({ min_coverage: Number(event.target.value) || 0 })
                  }
                />
              </Field>
            </div>
            <div className="row">
              <button
                type="button"
                className="btn"
                onClick={runController}
                disabled={controllerRunning}
              >
                <Icon name="refresh" size={14} />
                {controllerRunning ? "控制器运行中…" : "运行逐轮控制器"}
              </button>
              <span className="hint">阈值与停止原因是检索层启发式，不是临床充分性。</span>
            </div>
            {controllerResult ? (
              controllerResult.error ? (
                <Notice tone="error">{controllerResult.error}</Notice>
              ) : (
                <div className="notice info">
                  <Icon name="activity" size={15} />
                  <div>
                    停止原因 <b>{controllerResult.stop?.reason || "—"}</b> · 轮次{" "}
                    {controllerResult.final?.rounds ?? 0} · 证据{" "}
                    {controllerResult.final?.evidence_count ?? 0} · 预算{" "}
                    {controllerResult.final?.budget_spent ?? 0}
                    <div className="hint">详细轮次见 08 结果页“逐轮控制”选项卡。</div>
                  </div>
                </div>
              )
            ) : null}
          </div>
        </div>
      </div>
      <div className="page-nav">
        <button type="button" className="btn" onClick={() => goStep("request")}>
          <Icon name="chevronLeft" size={14} />
          上一步
        </button>
        <button type="button" className="btn" onClick={() => goStep("results")}>
          证据结果
          <Icon name="chevronRight" size={14} />
        </button>
      </div>
    </>
  );
}
