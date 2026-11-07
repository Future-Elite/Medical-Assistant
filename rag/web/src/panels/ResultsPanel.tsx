import { useMemo } from "react";
import { useWorkbench } from "../state/workbench";
import { Badge, CodeBlock, Empty, Metric, Notice, PageHeader, Tabs } from "../components/primitives";
import { Icon } from "../components/Icon";
import { EvidenceDetail } from "../components/EvidenceDetail";
import { PatientHitsView, SnapshotView } from "../components/PatientSnapshotView";
import { DiagnosticsGrid } from "../components/RetrievalDiagnostics";
import { kindLabels, rankDelta, score } from "../lib/formatting";
import type { Dict } from "../types";

function EvidenceList() {
  const { pkg, selectedIndex, selectResult, sourceFilter, setSourceFilter } = useWorkbench();
  const groups: Record<string, Dict[]> = pkg?.evidence_groups || {};
  const keys = Object.keys(groups).sort((a, b) =>
    a === "guideline" ? -1 : b === "guideline" ? 1 : 0,
  );
  const indexByCitation = useMemo(() => {
    const map = new Map<string, number>();
    (pkg?.ranked_documents || []).forEach((row: Dict, index: number) => {
      if (row.citation_id) map.set(row.citation_id, index);
    });
    return map;
  }, [pkg]);

  const visibleKeys = sourceFilter === "all" ? keys : keys.filter((key) => key === sourceFilter);

  return (
    <div className="stack">
      {keys.length > 1 ? (
        <div className="results-toolbar">
          <button
            type="button"
            className={`filter-chip ${sourceFilter === "all" ? "active" : ""}`}
            onClick={() => setSourceFilter("all")}
          >
            全部 <span className="n">{(pkg?.ranked_documents || []).length}</span>
          </button>
          {keys.map((key) => (
            <button
              key={key}
              type="button"
              className={`filter-chip ${sourceFilter === key ? "active" : ""}`}
              onClick={() => setSourceFilter(key)}
            >
              {kindLabels[key] || key} <span className="n">{groups[key].length}</span>
            </button>
          ))}
        </div>
      ) : null}
      <div className="results-layout">
        <div className="result-list">
          {visibleKeys.map((kind) => (
            <div key={kind} className="stack" style={{ gap: 8 }}>
              {keys.length > 1 ? (
                <div className="section-title" style={{ marginTop: 4 }}>
                  {kindLabels[kind] || kind} · {groups[kind].length} 条
                </div>
              ) : null}
              {groups[kind].map((item, innerIndex) => {
                const globalIndex =
                  item.citation_id && indexByCitation.has(item.citation_id)
                    ? (indexByCitation.get(item.citation_id) as number)
                    : -1;
                const doc = item.document;
                const finalRank = globalIndex >= 0 ? globalIndex + 1 : null;
                return (
                  <button
                    type="button"
                    key={`${kind}-${innerIndex}`}
                    className={`result-row ${globalIndex === selectedIndex ? "active" : ""} ${
                      finalRank && finalRank <= 3 ? "rank-top" : ""
                    }`}
                    onClick={() => selectResult(globalIndex)}
                  >
                    <div className="result-title">
                      <span className="result-rank">{innerIndex + 1}</span>
                      <h3>{doc.title}</h3>
                      <div className="result-badges">
                        <Badge>{kindLabels[doc.kind] || doc.kind}</Badge>
                      </div>
                    </div>
                    <div className="result-snippet">{doc.text}</div>
                    <div className="result-meta">
                      <span>总排名 {finalRank ?? "—"}</span>
                      <span>来源内分数 {Number(item.score).toFixed(3)}</span>
                      <span>{item.evidence_status}</span>
                      {doc.pmid ? <span>PMID {doc.pmid}</span> : null}
                      {doc.version_id ? <span>版本 {doc.version_id}</span> : null}
                      {doc.recommendation_id ? <span>推荐 {doc.recommendation_id}</span> : null}
                      <span>引用 {item.citation_id || "—"}</span>
                    </div>
                    <div className="result-scoreline">
                      <span>
                        总分 <b>{score(item.score)}</b>
                      </span>
                      <span>词面 {score(item.lexical_score)}</span>
                      <span>语义 {score(item.semantic_score)}</span>
                      <span>RRF {score(item.fusion_score, 4)}</span>
                      <span>权威 {score(item.authority_score)}</span>
                      <span>新鲜 {score(item.freshness_score)}</span>
                      <span>重排 {score(item.reranker_score)}</span>
                      <span>
                        重排前 {item.fusion_rank ? `#${item.fusion_rank}` : "—"} → 最终{" "}
                        {finalRank ? `#${finalRank}` : "—"} · {rankDelta(item.fusion_rank, finalRank)}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          ))}
          {!visibleKeys.length ? <Empty title="当前筛选下无匹配证据" /> : null}
        </div>
        <EvidenceDetail index={selectedIndex} />
      </div>
    </div>
  );
}

function ApplicabilityTab() {
  const { pkg, openGuidelineChunk } = useWorkbench();
  const rows: Dict[] = pkg?.applicability || [];
  if (!rows.length) {
    return <Empty title="无结构化适用条件">本次返回的指南没有可核对的结构化 applicability 条件。</Empty>;
  }
  const labels: Record<string, string> = {
    possible_match: "可能匹配",
    mismatch: "条件不符",
    unknown: "信息不足",
    not_assessed: "未评估",
  };
  const tones: Record<string, string> = {
    possible_match: "done",
    mismatch: "warn",
    unknown: "warn",
    not_assessed: "",
  };
  const statusText: Record<string, string> = {
    match: "对得上",
    mismatch: "对不上",
    unknown: "看不出来",
  };

  return (
    <div className="stack">
      <div className="hint">本次核对 {rows.length} 份指南的结构化条件。</div>
      {rows.map((row, index) => {
        const ranked = (pkg.ranked_documents || []).find(
          (item: Dict) => item.document.document_id === row.document_id,
        );
        const sourceId = ranked?.document?.source_id;
        return (
          <section className="diag-card" key={index}>
            <div className="diag-head">
              <h4>{row.document_id}</h4>
              <div className="row">
                {sourceId ? (
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => openGuidelineChunk(sourceId, row.document_id)}
                  >
                    <Icon name="file" size={13} />
                    在 PDF 分块中查看
                  </button>
                ) : null}
                <Badge tone={(tones[row.status] as any) || ""}>
                  {labels[row.status] || row.status}
                </Badge>
              </div>
            </div>
            <div className="diag-body">
              <div className="hint">{row.reason || ""}</div>
              {(row.criteria || []).map((criterion: Dict, i: number) => {
                const preview = criterion.preview;
                const observed = preview?.observed_value || criterion.observed_value;
                const paths =
                  preview?.patient_record_paths?.length
                    ? preview.patient_record_paths
                    : criterion.patient_record_paths || [];
                return (
                  <div className="review-finding" key={i}>
                    <div>
                      <b>{criterion.label || criterion.criterion_id}</b>{" "}
                      <Badge tone={criterion.status === "match" ? "done" : "warn"}>
                        {statusText[criterion.status] || criterion.status}
                      </Badge>
                      {preview ? (
                        <Badge>
                          试算预览：{statusText[preview.status] || preview.status} · 未审核
                        </Badge>
                      ) : null}
                    </div>
                    <div className="hint">
                      条件：{criterion.field} {criterion.operator}{" "}
                      {(criterion.expected || []).join(", ") || "—"}
                      {preview?.unit ? ` ${preview.unit}` : ""} · 来源{" "}
                      {criterion.source_locator || "—"}
                    </div>
                    <div className="hint">
                      病历实际：<b>{observed || "无可用记录"}</b>
                      {paths.length ? ` · ${paths.join("、")}` : ""}
                    </div>
                    <div className="hint">
                      {criterion.reason || ""}
                      {preview ? ` ${preview.note || ""}` : ""}
                    </div>
                  </div>
                );
              })}
              {!(row.criteria || []).length ? (
                <div className="hint">指南未提供结构化适用条件。</div>
              ) : null}
              <div className="hint" style={{ marginTop: 6 }}>
                字段核对 · 非临床判定
              </div>
            </div>
          </section>
        );
      })}
    </div>
  );
}

function ControllerTab() {
  const { controllerResult } = useWorkbench();
  if (!controllerResult) {
    return <Empty title="尚未运行">在 07 执行检索页运行逐轮控制器后显示轮次与停止原因。</Empty>;
  }
  if (controllerResult.error) {
    return <Notice tone="error">{controllerResult.error}</Notice>;
  }
  const labels: Record<string, string> = {
    patient_facts: "患者事实 · 本地",
    guideline_registry: "指南注册表",
    europe_pmc: "Europe PMC",
    authorized_pdf: "授权 PDF",
    pubmed: "PubMed",
    other: "其它",
  };
  const stop = controllerResult.stop || {};
  const final = controllerResult.final || {};
  const coverage = final.coverage || {};
  return (
    <div className="stack">
      <div className="notice info">
        <Icon name="activity" size={15} />
        <div>
          最终：证据 {final.evidence_count ?? 0} · 来源{" "}
          {(final.source_roles || []).join(" → ") || "—"} · 词面覆盖 {coverage.covered ?? 0}/
          {coverage.total ?? 0} · 比例 {coverage.ratio ?? 0} · 预算 {final.budget_spent ?? 0}
        </div>
      </div>
      {(controllerResult.rounds || []).map((round: Dict, index: number) => {
        const considered = (round.considered || [])
          .map((row: Dict) => `${labels[row.role] || row.role}:${row.decision}`)
          .join(" · ");
        const stopText = round.stop_after
          ? `${round.stop_after.decision}/${round.stop_after.reason}`
          : "—";
        return (
          <div className="review-finding" key={index}>
            <div>
              <b>
                round {round.round} · {labels[round.role] || round.role || "—"}
              </b>{" "}
              <Badge>{round.action}</Badge>
            </div>
            <div className="hint">{round.reason || ""}</div>
            <div className="hint">
              来源：{(round.source_ids || []).join("、") || "—"} · 新增证据：
              {round.result?.new_documents ?? "—"} · 停止判断：{stopText}
            </div>
            <div className="hint">候选：{considered || "—"}</div>
          </div>
        );
      })}
      <div className="hint">
        回放序列：
        {(controllerResult.replay?.decision_sequence || [])
          .map(
            (row: Dict) =>
              `r${row.round}:${row.role || row.action}${row.stop_after ? `→${row.stop_after}` : ""}`,
          )
          .join(" · ") || "—"}
      </div>
      <div className="hint">启发式停止 · 非临床充分性 · 停止原因 {stop.reason || "—"}</div>
    </div>
  );
}

function RawTab() {
  const { pkg } = useWorkbench();
  const trace = pkg?.trace || {};
  return (
    <div className="stack">
      <div className="hint">
        evidence_groups：{Object.keys(pkg?.evidence_groups || {}).join("、") || "无"} · source
        separation：{pkg?.source_separation?.status || "unknown"}
      </div>
      <div className="grid grid-2">
        <div>
          <div className="section-title">包摘要</div>
          <CodeBlock
            value={{
              retrieval_id: pkg?.retrieval_id,
              request_id: pkg?.request_id,
              candidate_count: trace.candidate_count,
              selected_count: trace.selected_count,
              warnings: (pkg?.warnings || []).length,
            }}
          />
        </div>
        <div>
          <div className="section-title">原始响应 JSON</div>
          <CodeBlock value={pkg} maxHeight={480} />
        </div>
      </div>
    </div>
  );
}

export function ResultsPanel() {
  const {
    pkg,
    resultTab,
    setResultTab,
    patientTab,
    setPatientTab,
    snapshot,
    runRetrieval,
    running,
    goStep,
  } = useWorkbench();

  if (!pkg) {
    return (
      <>
        <PageHeader
          kicker="结果 · 证据"
          title="证据结果"
          description="检索返回的证据包按来源分组，并附引文、患者事实、适用性与诊断轨迹。"
          actions={<Badge>未检索</Badge>}
        />
        <Empty
          title="尚未检索"
          action={
            <button type="button" className="btn btn-primary" onClick={runRetrieval} disabled={running}>
              <Icon name="play" size={14} />
              执行检索
            </button>
          }
        >
          完成 01–06 的输入后执行检索，结果会显示在这里。
        </Empty>
      </>
    );
  }

  const trace = pkg.trace || {};
  const warnings: string[] = pkg.warnings || [];
  const ranked = pkg.ranked_documents || [];
  const guidelineCount = ranked.filter((row: Dict) => row.document.kind === "guideline").length;
  const okSources = (trace.connector_trace || []).filter((row: Dict) => row.status === "ok").length;
  const rerank = trace.rerank_trace || {};
  const rerankLabel =
    rerank.status === "ok"
      ? ` · 重排 ${rerank.reranker_id}${rerank.pool_enabled ? ` · 池 ${rerank.pool_size}→${rerank.reranked_count}` : ""}`
      : "";

  return (
    <>
      <PageHeader
        kicker="结果 · 证据"
        title="证据结果"
        description={
          <span>
            {pkg.request_id} · 方法 {trace.retrieval_method || "bm25"}
            {rerankLabel} · source_mode {trace.source_mode || "—"}
          </span>
        }
        actions={
          <div className="row">
            <button type="button" className="btn btn-sm" onClick={() => goStep("run")}>
              重新检索
            </button>
            <Badge tone="done">{ranked.length} 条</Badge>
          </div>
        }
      />

      <div className="page-body">
        <div className="metrics">
          <Metric label="候选证据" value={trace.candidate_count ?? 0} />
          <Metric label="最终返回" value={trace.selected_count ?? 0} accent />
          <Metric label="指南条目" value={guidelineCount} />
          <Metric label="成功来源" value={okSources} />
        </div>

        {warnings.length ? (
          <Notice tone="warn" icon="alert">
            {warnings.slice(0, 2).map((warning, index) => (
              <div key={index}>{warning}</div>
            ))}
            {warnings.length > 4 ? (
              <details>
                <summary>其余 {warnings.length - 2} 条</summary>
                {warnings.slice(2).map((warning, index) => (
                  <div key={index}>{warning}</div>
                ))}
              </details>
            ) : null}
          </Notice>
        ) : null}

        <div className="card">
          <div className="card-body" style={{ padding: 14 }}>
            <Tabs
              value={resultTab}
              onChange={setResultTab}
              items={[
                { key: "evidence", label: "证据列表", count: ranked.length },
                { key: "patient", label: "患者事实", count: (pkg.patient_hits || []).length },
                { key: "applicability", label: "指南适用性", count: (pkg.applicability || []).length },
                { key: "controller", label: "逐轮控制" },
                { key: "diagnostics", label: "检索诊断" },
                { key: "raw", label: "原始响应" },
              ]}
            />
            <div style={{ marginTop: 14 }}>
              {resultTab === "evidence" ? <EvidenceList /> : null}
              {resultTab === "patient" ? (
                <div className="stack">
                  <Tabs
                    value={patientTab}
                    onChange={setPatientTab}
                    items={[
                      { key: "hits", label: "本次命中", count: (pkg.patient_hits || []).length },
                      {
                        key: "snapshot",
                        label: "快照事实",
                        count: (snapshot?.facts || []).length || "—",
                      },
                    ]}
                  />
                  {patientTab === "hits" ? <PatientHitsView pkg={pkg} /> : <SnapshotView snapshot={snapshot} />}
                </div>
              ) : null}
              {resultTab === "applicability" ? <ApplicabilityTab /> : null}
              {resultTab === "controller" ? <ControllerTab /> : null}
              {resultTab === "diagnostics" ? <DiagnosticsGrid pkg={pkg} /> : null}
              {resultTab === "raw" ? <RawTab /> : null}
            </div>
          </div>
        </div>
      </div>

      <div className="page-nav">
        <button type="button" className="btn" onClick={() => goStep("run")}>
          <Icon name="chevronLeft" size={14} />
          上一步
        </button>
        <button type="button" className="btn" onClick={() => goStep("guidelines")}>
          指南解析与 HIS 核对
          <Icon name="chevronRight" size={14} />
        </button>
      </div>
    </>
  );
}
