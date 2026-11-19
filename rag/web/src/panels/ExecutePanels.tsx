import { useState } from "react";
import { useWorkbench } from "../state/workbench";
import { Badge, Empty, Field, Metric, Notice, PageHeader, StatusRow } from "../components/ui";
import { Icon } from "../components/Icon";

export function RequestPanel() {
  const { requestPreview, form, goStep } = useWorkbench();
  const request = requestPreview.request;
  return (
    <>
      <PageHeader
        kicker="编排 · 请求编排"
        title="请求组装"
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
            </div>
            <div className="subcard">
              <div className="subcard-title">请求摘要</div>
              <div className="status-list">
                <div className="status-row">
                  <span>question</span>
                  <span>{request?.question ? `${request.question.slice(0, 80)}${request.question.length > 80 ? "…" : ""}` : "缺少"}</span>
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
        <button type="button" className="btn btn-primary" onClick={() => goStep("run")}>
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
    agenticResult,
    agenticError,
    agenticRunning,
    runAgenticRetrieval,
    form,
  } = useWorkbench();
  const [maxRounds, setMaxRounds] = useState(3);
  const [minCoverage, setMinCoverage] = useState(0.8);
  const sourceLabels: Record<string, string> = { pubmed: "PubMed（实时）", guideline: "指南（本地 PDF）", drug: "药品库（在线）" };

  return (
    <>
      <PageHeader
        kicker="核心 · 执行检索"
        title="执行检索"
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
              <Empty title="尚未执行" />
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
                  </div>
                </div>
              )
            ) : null}
          </div>
        </div>

        <div className="card" data-agentic-retrieval="true">
          <div className="card-head">
            <h3>迭代检索（Agentic）</h3>
            <Badge tone={agenticResult?.status === "sufficient" ? "done" : agenticResult ? "warn" : ""}>
              {agenticResult?.status || "未运行"}
            </Badge>
          </div>
          <div className="card-body stack">
            <div className="notice info">
              <Icon name="refresh" size={15} />
              <div>
                Interpreter 解析问题与意图 → Explorer 按当前勾选的来源逐轮查证 → Arbiter 汇总证据并给出停止原因。
                来源选择直接决定每轮允许访问的 PubMed、指南、药品库；患者事实始终本地。
              </div>
            </div>
            <div className="chips">
              {(form.sourceKinds || []).map((kind) => (
                <span className="chip" key={kind}>{sourceLabels[kind] || kind}</span>
              ))}
              {!(form.sourceKinds || []).length ? <span className="chip">未选来源</span> : null}
            </div>
            <div className="form-grid c2">
              <Field label="max_rounds">
                <input className="input" type="number" min={1} max={5} value={maxRounds}
                  onChange={(event) => setMaxRounds(Number(event.target.value) || 1)} />
              </Field>
              <Field label="min_evidence_coverage">
                <input className="input" type="number" min={0} max={1} step={0.05} value={minCoverage}
                  onChange={(event) => setMinCoverage(Number(event.target.value) || 0)} />
              </Field>
            </div>
            <div className="row">
              <button type="button" className="btn btn-primary" onClick={() => runAgenticRetrieval(maxRounds, minCoverage)} disabled={agenticRunning}>
                <Icon name="activity" size={14} />
                {agenticRunning ? "迭代检索中…" : "运行迭代检索"}
              </button>
            </div>
            {agenticError ? <Notice tone="error">{agenticError}</Notice> : null}
            {agenticResult ? (
              <div className="stack">
                <div className="status-list">
                  <StatusRow label="status" value={agenticResult.status || "—"} />
                  <StatusRow label="stop_reason" value={agenticResult.stop_reason || "—"} />
                  <StatusRow label="轮次" value={String((agenticResult.rounds || []).length)} />
                  <StatusRow label="外部来源" value={Object.entries(agenticResult.external_source_counts || {}).map(([kind, count]) => `${sourceLabels[kind] || kind} ${count}`).join(" · ") || "—"} />
                </div>
                {(agenticResult.gap_analysis?.missing_sources || []).length ? (
                  <Notice tone="warn">本轮没有命中的来源：{(agenticResult.gap_analysis.missing_sources || []).map((kind: string) => sourceLabels[kind] || kind).join("、")}；空结果不是阴性。</Notice>
                ) : null}
                <div className="section-title">轮次</div>
                {(agenticResult.rounds || []).map((round: any, index: number) => (
                  <div className="result-row" key={index}>
                    <div className="result-title">
                      <strong>round {round.round}</strong>
                      <div className="result-badges">
                        <Badge>{round.status || "—"}</Badge>
                        <Badge>新推荐 {round.new_recommendations?.length ?? 0}</Badge>
                        <Badge>外部文档 {round.external?.documents ?? 0}</Badge>
                      </div>
                    </div>
                    <div className="result-meta">查询：{(round.queries || []).join(" / ")}</div>
                    {round.external ? (
                      <div className="result-meta">
                        来源命中：{Object.entries(round.external.source_counts || {}).map(([kind, count]) => `${sourceLabels[kind] || kind} ${count}`).join(" · ") || "—"}
                      </div>
                    ) : null}
                  </div>
                ))}
                {(agenticResult.external_documents || []).length ? (
                  <>
                    <div className="section-title">外部证据</div>
                    {(agenticResult.external_documents || []).slice(0, 8).map((doc: any) => (
                      <div className="result-row" key={`${doc.source_id}:${doc.document_id}`}>
                        <div className="result-title">
                          <strong>{doc.title}</strong>
                          <Badge>{sourceLabels[doc.kind] || doc.kind || doc.source_id}</Badge>
                        </div>
                        <div className="result-meta">{String(doc.text || "").slice(0, 220)}</div>
                      </div>
                    ))}
                  </>
                ) : null}
                <div className="hint">
                  迭代检索只汇总证据与缺口，不生成诊断或处方；指南图为 extracted 候选，药品库为美国 FDA 标签。
                </div>
              </div>
            ) : (
              <Empty title="尚未运行迭代检索" />
            )}
          </div>
        </div>
      </div>
      <div className="page-nav">
        <button type="button" className="btn" onClick={() => goStep("request")}>
          <Icon name="chevronLeft" size={14} />
          请求编排
        </button>
        <button type="button" className="btn btn-primary" onClick={() => goStep("guidelines")}>
          指南与适用性
          <Icon name="chevronRight" size={14} />
        </button>
      </div>
    </>
  );
}
