import { useMemo, useState } from "react";
import { useWorkbench } from "../state/workbench";
import { Badge, Empty, Field, Notice, PageHeader } from "../components/primitives";
import { Icon } from "../components/Icon";
import type { Dict } from "../types";

export function GuidelinesPanel() {
  const {
    guidelines,
    guidelineDataDir,
    guidelineStatus,
    guidelineError,
    guidelineConfigured,
    selectedGuidelineId,
    selectGuideline,
    guidelineChunks,
    selectedChunkId,
    selectChunk,
    loadGuidelines,
    parseOptions,
    setParseOption,
    parseStatus,
    parseHint,
    parseGuidelines,
    pdfPages,
    pdfPage,
    showPdfPage,
    pdfMeta,
    pdfHint,
    applicabilityResult,
    applicabilityError,
    checkApplicability,
    currentCase,
    form,
    setForm,
    goStep,
    capabilities,
  } = useWorkbench();
  const [nonce] = useState(() => Date.now());

  const grouped = useMemo(() => {
    const map = new Map<string, Dict[]>();
    guidelineChunks.forEach((chunk) => {
      const key = (chunk.section_path || []).join(" > ") || "（无章节）";
      if (!map.has(key)) map.set(key, []);
      (map.get(key) as Dict[]).push(chunk);
    });
    return map;
  }, [guidelineChunks]);

  const stats = useMemo(() => {
    const pages = guidelineChunks.map((chunk) => Number(chunk.page_start) || 0).filter(Boolean);
    const types: Record<string, number> = {};
    guidelineChunks.forEach((chunk) => {
      const type = chunk.element_type || "text";
      types[type] = (types[type] || 0) + 1;
    });
    return { pages, types };
  }, [guidelineChunks]);

  const useChunksForRetrieval = () => {
    setForm("sourceMode", "custom");
    setForm("sourceKinds", ["guideline"]);
    goStep("scope");
  };

  const pdfUrl = selectedGuidelineId
    ? `/api/guidelines/pdf/page?document_id=${encodeURIComponent(selectedGuidelineId)}&page=${pdfPage}&dpi=110&t=${nonce}-${pdfPage}`
    : "";

  if (!capabilities.guidelines) {
    return (
      <>
        <PageHeader
          kicker="结果 · 指南解析"
          title="指南解析与 HIS 核对"
          description="当前后端未提供指南解析接口，本步骤不可用；其余检索与审计步骤不受影响。"
          actions={<Badge>未提供</Badge>}
        />
        <div className="page-body">
          <Notice tone="warn" icon="alert">
            该后端版本没有指南解析、PDF 分块与适用性核对接口。请使用支持该能力的后端，或跳过本步骤继续引用与审计。
          </Notice>
        </div>
        <div className="page-nav">
          <button type="button" className="btn" onClick={() => goStep("results")}>
            <Icon name="chevronLeft" size={14} />
            上一步
          </button>
          <button type="button" className="btn" onClick={() => goStep("audit")}>
            引用与审计
            <Icon name="chevronRight" size={14} />
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        kicker="结果 · 指南解析"
        title="指南解析与 HIS 核对"
        description="检索出指南后，用本地 guideline_parsing 解析其 PDF/Markdown，并与当前 HIS 快照逐条核对结构化适用条件。"
        actions={<Badge tone={guidelineConfigured ? "done" : "error"}>{guidelineStatus}</Badge>}
      />

      <div className="page-body">
        {!guidelineConfigured ? (
          <Notice tone="warn" icon="alert">
            未配置 RAG_GUIDELINE_DATA。启动服务时设置该环境变量（或 config.guideline_data_dir），并按
            <code className="code-inline">guidelines/manifest.json</code> 放入指南文件。
          </Notice>
        ) : null}
        {guidelineError ? <Notice tone="error">{guidelineError}</Notice> : null}

        <div className="card">
          <div className="card-head">
            <h3>指南来源</h3>
            <button type="button" className="btn btn-sm" onClick={loadGuidelines}>
              <Icon name="refresh" size={13} />
              刷新列表
            </button>
          </div>
          <div className="card-body stack">
            <div className="form-grid c3">
              <Field label="指南">
                <select
                  className="select"
                  value={selectedGuidelineId}
                  onChange={(event) => selectGuideline(event.target.value)}
                  disabled={!guidelines.length}
                >
                  {!guidelines.length ? <option value="">manifest 中没有指南</option> : null}
                  {guidelines.map((row) => (
                    <option key={row.document_id} value={row.document_id}>
                      {row.title}
                      {row.version_id ? ` · ${row.version_id}` : ""} · {row.chunk_count} 块 · 可检索{" "}
                      {row.retrievable_count ?? "—"}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="数据目录">
                <input className="input" readOnly value={guidelineDataDir} />
              </Field>
              <Field label="状态">
                <div className="row" style={{ height: 36 }}>
                  <Badge tone={guidelineChunks.length ? "done" : ""}>
                    {guidelineChunks.length ? `${guidelineChunks.length} 块` : parseStatus}
                  </Badge>
                  <span className="hint">{guidelines.length} 份指南</span>
                </div>
              </Field>
            </div>
            <div className="form-grid c4">
              <Field label="分块策略">
                <select
                  className="select"
                  value={parseOptions.chunkStrategy}
                  onChange={(event) => setParseOption("chunkStrategy", event.target.value)}
                >
                  <option value="recursive">recursive · 推荐</option>
                  <option value="structure">structure · 段落级</option>
                  <option value="fixed">fixed · 定长</option>
                </select>
              </Field>
              <Field label="表格表示">
                <select
                  className="select"
                  value={parseOptions.tableStrategy}
                  onChange={(event) => setParseOption("tableStrategy", event.target.value)}
                >
                  <option value="markdown">markdown</option>
                  <option value="row">row</option>
                  <option value="row_context">row_context</option>
                </select>
              </Field>
              <Field label="图注策略">
                <select
                  className="select"
                  value={parseOptions.figureStrategy}
                  onChange={(event) => setParseOption("figureStrategy", event.target.value)}
                >
                  <option value="reference">reference</option>
                  <option value="caption_context">caption_context</option>
                </select>
              </Field>
              <Field label="MinerU 质量档">
                <select
                  className="select"
                  value={parseOptions.mineruTier}
                  onChange={(event) => setParseOption("mineruTier", event.target.value)}
                >
                  <option value="advanced">advanced</option>
                  <option value="standard">standard</option>
                </select>
              </Field>
            </div>
            <div className="form-grid c4">
              <Field label="块大小 字符">
                <input
                  className="input"
                  type="number"
                  min={100}
                  max={4000}
                  step={50}
                  value={parseOptions.chunkSize}
                  onChange={(event) => setParseOption("chunkSize", Number(event.target.value) || 0)}
                />
              </Field>
              <Field label="重叠 字符">
                <input
                  className="input"
                  type="number"
                  min={0}
                  max={1000}
                  step={10}
                  value={parseOptions.chunkOverlap}
                  onChange={(event) =>
                    setParseOption("chunkOverlap", Number(event.target.value) || 0)
                  }
                />
              </Field>
              <Field label="选项">
                <label className="check" style={{ height: 36 }}>
                  <input
                    type="checkbox"
                    checked={parseOptions.forceReparse}
                    onChange={(event) => setParseOption("forceReparse", event.target.checked)}
                  />
                  强制重跑 MinerU
                </label>
              </Field>
              <Field label="操作">
                <div className="row">
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={() => parseGuidelines(false)}
                    disabled={!selectedGuidelineId}
                  >
                    <Icon name="play" size={13} />
                    解析所选指南
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => parseGuidelines(true)}>
                    解析全部
                  </button>
                </div>
              </Field>
            </div>
            <div className="row">
              <Badge tone={parseStatus.startsWith("完成") ? "done" : ""}>{parseStatus}</Badge>
              <span className="hint">{parseHint}</span>
              <span className="spacer" />
              <button type="button" className="btn btn-sm" onClick={useChunksForRetrieval}>
                用当前指南做 custom 检索
              </button>
            </div>
          </div>
        </div>

        <div className="guideline-viewer">
          <div className="pdf-panel">
            <div className="pdf-head">
              <b>PDF 原文</b>
              <span className="hint">{pdfMeta}</span>
            </div>
            <div className="pdf-controls">
              <button type="button" className="btn btn-sm" onClick={() => showPdfPage(pdfPage - 1)}>
                上一页
              </button>
              <input
                type="number"
                min={1}
                value={pdfPage}
                onChange={(event) => showPdfPage(Number(event.target.value))}
              />
              <span>/ {pdfPages || "—"}</span>
              <button type="button" className="btn btn-sm" onClick={() => showPdfPage(pdfPage + 1)}>
                下一页
              </button>
              <span className="hint">{pdfHint}</span>
            </div>
            <div className="pdf-canvas">
              {pdfUrl ? (
                <img src={pdfUrl} alt="指南 PDF 页面" />
              ) : (
                <Empty title="未选择指南">选择指南后由服务端实时渲染 PDF 页。</Empty>
              )}
            </div>
          </div>

          <div className="chunk-panel">
            <div className="chunk-panel-head">
              <div className="subcard-title" style={{ marginBottom: 0 }}>
                <span>内容分割</span>
                <span className="hint">点击分块跳转 PDF 页</span>
              </div>
            </div>
            {guidelineChunks.length ? (
              <>
                <div className="chunk-stats">
                  <Badge>{guidelineChunks.length} 块</Badge>
                  <Badge>{grouped.size} 个章节</Badge>
                  <Badge>
                    页码{" "}
                    {stats.pages.length
                      ? `${Math.min(...stats.pages)}–${Math.max(...stats.pages)}`
                      : "未知"}
                  </Badge>
                  {Object.entries(stats.types).map(([type, count]) => (
                    <Badge key={type}>
                      {type} {count}
                    </Badge>
                  ))}
                </div>
                <div className="chunk-list">
                  {[...grouped.entries()].map(([section, items]) => (
                    <div className="chunk-section" key={section}>
                      <div className="chunk-section-head">
                        <b>{section}</b>
                        <span className="hint">
                          {items.length} 块
                          {items[0]?.page_start ? ` · 第 ${items[0].page_start} 页起` : ""}
                        </span>
                      </div>
                      {items.map((chunk) => {
                        const pageText = chunk.page_start
                          ? `第 ${chunk.page_start}${
                              chunk.page_end && chunk.page_end !== chunk.page_start
                                ? `–${chunk.page_end}`
                                : ""
                            } 页`
                          : "页码未知";
                        return (
                          <div
                            key={chunk.chunk_id}
                            className={`chunk-card ${selectedChunkId === chunk.chunk_id ? "selected" : ""}`}
                            onClick={() => selectChunk(chunk.chunk_id)}
                            role="button"
                            tabIndex={0}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") selectChunk(chunk.chunk_id);
                            }}
                          >
                            <div className="chunk-meta">
                              <span>{chunk.element_type || "text"}</span>
                              <span>{pageText}</span>
                              <span>{(chunk.section_path || []).slice(1).join(" > ") || "（顶层）"}</span>
                              <span>{String(chunk.text || "").length} 字符</span>
                              <span>{chunk.chunk_id}</span>
                            </div>
                            <div className="chunk-text">{chunk.text}</div>
                          </div>
                        );
                      })}
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="chunk-list">
                <Empty title="尚未解析">
                  选择指南并解析后，这里显示章节路径、页码、元素类型、块 ID 与文本。
                </Empty>
              </div>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>HIS 条件适配预览</h3>
            <div className="row">
              <Badge tone={currentCase ? "ready" : ""}>
                {currentCase ? `病例 ${currentCase.snapshot.patient_ref}` : "未选病例"}
              </Badge>
              <button type="button" className="btn btn-sm" onClick={checkApplicability}>
                用当前病例核对
              </button>
            </div>
          </div>
          <div className="card-body">
            {applicabilityError ? <Notice tone="error">{applicabilityError}</Notice> : null}
            {applicabilityResult ? (
              <div className="stack">
                <div className="hint">
                  <b>{applicabilityResult.title || ""}</b> · {applicabilityResult.locator || ""} · 病例{" "}
                  {applicabilityResult.patient?.patient_ref || ""} · 可见事实{" "}
                  {applicabilityResult.patient?.visible_fact_count ?? "—"} 条
                </div>
                <div className="row">
                  <Badge
                    tone={
                      applicabilityResult.assessment?.status === "possible_match"
                        ? "done"
                        : "warn"
                    }
                  >
                    {{
                      possible_match: "可能匹配",
                      mismatch: "条件不符",
                      unknown: "信息不足",
                      not_assessed: "未评估",
                    }[applicabilityResult.assessment?.status as string] ||
                      applicabilityResult.assessment?.status ||
                      "未评估"}
                  </Badge>
                  <span className="hint">{applicabilityResult.assessment?.reason || ""}</span>
                </div>
                {(applicabilityResult.assessment?.criteria || []).map(
                  (criterion: Dict, index: number) => (
                    <div className="review-finding" key={index}>
                      <div>
                        <b>{criterion.label || criterion.criterion_id}</b>{" "}
                        <Badge tone={criterion.status === "match" ? "done" : "warn"}>
                          {{
                            match: "对得上",
                            mismatch: "对不上",
                            unknown: "看不出来",
                          }[criterion.status as string] || criterion.status}
                        </Badge>
                      </div>
                      <div className="hint">
                        条件：{criterion.field} {criterion.operator}{" "}
                        {(criterion.expected || []).join(", ") || "—"} · 来源{" "}
                        {criterion.source_locator || "—"}
                      </div>
                      <div className="hint">
                        病历实际：<b>{criterion.observed_value || "无可用记录"}</b>
                        {(criterion.patient_record_paths || []).length
                          ? ` · ${criterion.patient_record_paths.join("、")}`
                          : ""}
                      </div>
                      <div className="hint">{criterion.reason || ""}</div>
                    </div>
                  ),
                )}
                {!(applicabilityResult.assessment?.criteria || []).length ? (
                  <div className="hint">该指南没有结构化 applicability 条件。</div>
                ) : null}
                <div className="hint">
                  这是 09 检索后指南解析步骤的条件预览；完整检索适用性见 08 结果页“指南适用性”选项卡。
                </div>
              </div>
            ) : (
              <Empty title="尚未核对">
                在 01 步骤选择病例后点击“用当前病例核对”，按指南 applicability 条件与患者事实逐条比较。
              </Empty>
            )}
          </div>
          <div className="card-foot">
            <span>GuidelineParsingSource · {guidelineDataDir}</span>
            <span>只读解析 · 不写回 HIS</span>
          </div>
        </div>
      </div>

      <div className="page-nav">
        <button type="button" className="btn" onClick={() => goStep("results")}>
          <Icon name="chevronLeft" size={14} />
          上一步
        </button>
        <button type="button" className="btn" onClick={() => goStep("audit")}>
          引用与审计
          <Icon name="chevronRight" size={14} />
        </button>
      </div>
    </>
  );
}

export function AuditPanel() {
  const {
    pkg,
    auditAnswer,
    setAuditAnswer,
    auditResult,
    auditRunning,
    auditHint,
    runAudit,
    goStep,
  } = useWorkbench();

  const insertTemplate = () => {
    if (!pkg?.citations?.length) return;
    setAuditAnswer(`依据本次检索证据，该患儿…[${pkg.citations[0].citation_id}]。`);
  };

  const metrics = auditResult?.alce_like || {};
  const mode =
    { structural_only: "结构审计", claim_verification: "主张校验" }[
      auditResult?.verifier?.mode as string
    ] ||
    auditResult?.verifier?.mode ||
    "未校验";

  return (
    <>
      <PageHeader
        kicker="审计 · 引用与回答"
        title="引用与审计"
        description="对一次检索的回答做引用完整性检查与主张级校验；不可用时显式标记 not_checked，不用词面启发式冒充语义校验。"
        actions={<Badge tone={auditResult ? "done" : ""}>{auditResult ? "已审计" : "待审计"}</Badge>}
      />
      <div className="page-body">
        {!pkg ? (
          <Notice tone="warn" icon="alert">
            尚未检索。请先执行检索，审计需要绑定本次 <code className="code-inline">retrieval_id</code>。
          </Notice>
        ) : null}
        <div className="audit-layout">
          <div className="card">
            <div className="card-head">
              <h3>待审计回答</h3>
              <span className="hint">POST /api/citation · POST /api/audit</span>
            </div>
            <div className="card-body stack">
              <Field
                label="answer"
                hint="在句末用 [citation_id] 标注引用；审计只核对结构与主张支持，不评价临床正确性。"
              >
                <textarea
                  className="textarea"
                  style={{ minHeight: 170 }}
                  value={auditAnswer}
                  onChange={(event) => setAuditAnswer(event.target.value)}
                  placeholder="回答 [citation_id]"
                />
              </Field>
              <div className="row">
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={insertTemplate}
                  disabled={!pkg?.citations?.length}
                >
                  插入引用模板
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={runAudit}
                  disabled={auditRunning || !pkg}
                >
                  <Icon name="shield" size={14} />
                  {auditRunning ? "正在审计…" : "审计回答"}
                </button>
                <span className="hint">{auditHint}</span>
              </div>
              {pkg ? (
                <div className="hint">
                  本次检索 {pkg.retrieval_id} · 引文 {(pkg.citations || []).length} 条
                </div>
              ) : null}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <h3>审计结果</h3>
              <Badge>{mode}</Badge>
            </div>
            <div className="card-body">
              {auditResult ? (
                <div className="stack">
                  <div className="metrics" style={{ gridTemplateColumns: "1fr 1fr" }}>
                    <div className="metric">
                      <small>引用完整性</small>
                      <strong style={{ fontSize: 18 }}>
                        {metrics.citation_completeness == null
                          ? "未测量"
                          : `${(metrics.citation_completeness * 100).toFixed(1)}%`}
                      </strong>
                    </div>
                    <div className="metric">
                      <small>支持精确率</small>
                      <strong style={{ fontSize: 18 }}>
                        {metrics.citation_precision == null
                          ? "未测量"
                          : `${(metrics.citation_precision * 100).toFixed(1)}%`}
                      </strong>
                    </div>
                  </div>
                  {(auditResult.warnings || []).length ? (
                    <ul className="claim-list">
                      {auditResult.warnings.map((warning: string, index: number) => (
                        <li key={index}>{warning}</li>
                      ))}
                    </ul>
                  ) : null}
                  {(auditResult.claims || []).length ? (
                    <ul className="claim-list">
                      {auditResult.claims.map((claim: Dict, index: number) => (
                        <li key={index}>
                          <span className="claim-status">{claim.ragtruth_label}</span>
                          <span>{claim.claim}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <div className="hint">无主张级结果</div>
                  )}
                </div>
              ) : (
                <Empty title="尚未审计">填写回答并点击“审计回答”后显示引用完整性与主张标签。</Empty>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="page-nav">
        <button type="button" className="btn" onClick={() => goStep("guidelines")}>
          <Icon name="chevronLeft" size={14} />
          上一步
        </button>
        <button type="button" className="btn" onClick={() => goStep("case")}>
          回到开头
          <Icon name="chevronRight" size={14} />
        </button>
      </div>
    </>
  );
}
