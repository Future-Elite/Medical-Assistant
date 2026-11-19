import { useState } from "react";
import { useWorkbench } from "../state/workbench";
import { Badge, Empty, Field, Notice, StatusRow } from "../components/ui";
import { Icon } from "../components/Icon";

/**
 * 双向图检索：患者图 → 指南图（正向）和指南条件 → 患者图（反向）。
 * 结果保存在 workbench，生成适用性链路时自动作为 knowledge_fusion 传入。
 * 只展示重点；release/术语/排除/冲突收在折叠区。
 */
export function KnowledgeAudit() {
  const {
    snapshot,
    currentCase,
    knowledgeFusion,
    knowledgeFusionError,
    knowledgeFusionRunning,
    runKnowledgeFusion,
    selectedGuidelineId,
    retrievalGuidelines,
  } = useWorkbench();
  const [query, setQuery] = useState("CPAP");
  const [topK, setTopK] = useState(3);
  const hasSnapshot = Boolean(snapshot || currentCase?.snapshot);
  const retrievalIds = (retrievalGuidelines || []).map((row) => row.guideline_id);
  const usingRetrieval = retrievalIds.length > 0;
  const recommendations = knowledgeFusion?.recommendations || [];
  const withEvidence = recommendations.filter(
    (row: any) => (row.patient_graph_evidence || []).length,
  );
  const stats = knowledgeFusion?.fusion?.stats || {};

  return (
    <div className="card" id="knowledge-fusion" data-knowledge-audit="true">
      <div className="card-head">
        <h3>知识图谱与双向检索</h3>
        <Badge tone={knowledgeFusion ? "done" : ""}>
          {knowledgeFusion?.status || "尚未检索"}
        </Badge>
      </div>
      <div className="card-body stack">
        <div className="notice info">
          <Icon name="shield" size={15} />
          <div>
            本地指南 PDF 先解析分块并建图；查询时检索 top-k 指南，再与 1 个患者图做双向检索：
            患者图命中作为指南图种子（正向），指南推荐条件再回查患者图（反向）。
            指南图节点是 <code>review_status=extracted</code> 候选，不是临床指南。
          </div>
        </div>
        <div className="row">
          <Field label="双向图检索词" hint="POST /api/knowledge/guideline-fusion · 本地指南 PDF">
            <input
              className="input"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="例如：CPAP、固尔苏、PS"
            />
          </Field>
          {usingRetrieval ? (
            <Field label="检索 Top-k 指南" hint="来自 07 执行检索的指南证据">
              <input className="input" readOnly value={`${retrievalIds.length} 本`} />
            </Field>
          ) : (
            <Field label="k 本指南" hint="尚未检索，先按查询选 top-k">
              <input
                className="input"
                type="number"
                min={1}
                max={6}
                value={topK}
                onChange={(event) => setTopK(Number(event.target.value) || 1)}
              />
            </Field>
          )}
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => runKnowledgeFusion(
              [query],
              usingRetrieval ? retrievalIds.length : topK,
              usingRetrieval ? retrievalIds : undefined,
            )}
            disabled={knowledgeFusionRunning || !hasSnapshot}
          >
            <Icon name="search" size={14} />
            {knowledgeFusionRunning ? "检索中…" : usingRetrieval ? "与检索 Top-k 指南双向检索" : "运行 k 指南双向检索"}
          </button>
        </div>
        {!hasSnapshot ? <div className="hint">先在 01 患者快照载入病例。</div> : null}
        {usingRetrieval ? (
          <div className="chips">
            {retrievalGuidelines.map((row, index) => (
              <span className="chip" key={row.guideline_id}>
                Top{index + 1} · {row.title} · {row.score.toFixed(3)}
              </span>
            ))}
          </div>
        ) : (
          <div className="hint">尚未执行检索；先在 07 执行检索，再回来对 Top-k 指南建图/双向检索。</div>
        )}
        {knowledgeFusionError ? <Notice tone="error">{knowledgeFusionError}</Notice> : null}
        {knowledgeFusion?.diagnostics?.library_warning ? (
          <Notice tone="warn">{knowledgeFusion.diagnostics.library_warning}</Notice>
        ) : null}
        {knowledgeFusion ? (
          <div className="stack">
            <div className="status-list" data-graph-fusion="true">
              <StatusRow
                label="选中指南"
                value={`${(knowledgeFusion.guidelines || []).length} 本 · ${(knowledgeFusion.recommendations || []).length} 条推荐`}
              />
              <StatusRow
                label="正向 患者图→指南图"
                value={`${knowledgeFusion.fusion?.forward?.hits ?? 0} hits · ${(knowledgeFusion.fusion?.forward?.seeds || []).length} seeds`}
              />
              <StatusRow
                label="反向 指南条件→患者图"
                value={`${knowledgeFusion.fusion?.backward?.hits ?? 0} hits · ${(knowledgeFusion.fusion?.backward?.queries || []).length} queries`}
              />
              <StatusRow label="条件引用覆盖" value={String(stats.reference_coverage ?? "—")} />
              <StatusRow
                label="患者证据"
                value={`${stats.recommendations_with_patient_evidence ?? 0} / ${stats.recommendations ?? recommendations.length} 条推荐`}
              />
            </div>
            <div className="chips">
              {(knowledgeFusion.guidelines || []).map((row: any) => (
                <span className="chip" key={row.guideline_id}>
                  {row.guideline_id === selectedGuidelineId ? "目标 · " : ""}
                  {row.title || row.guideline_id} · {row.score}
                </span>
              ))}
            </div>
            <div className="chips">
              {(knowledgeFusion.fusion?.backward?.queries || []).slice(0, 6).map((item: string, index: number) => (
                <span className="chip" key={`${item}-${index}`}>反向：{item}</span>
              ))}
            </div>
            <div className="fusion-bridge-note">
              生成适用性链路时会自动带入本次双向图证据；链路中只作“图证据/候选”，不改变 criteria status。
            </div>
            <div className="section-title">带患者图证据的推荐</div>
            {withEvidence.length ? (
              withEvidence.map((row: any) => (
                <div className="result-row" data-knowledge-recommendation={row.recommendation_id} key={row.recommendation_id}>
                  <div className="result-title">
                    <strong>{row.recommendation_id}</strong>
                    <div className="result-badges">
                      <Badge>{row.direction}</Badge>
                      <Badge tone={row.eligible_for_simulation ? "done" : "warn"}>
                        {row.eligible_for_simulation ? "可模拟" : "需复核"}
                      </Badge>
                      <Badge>{row.review_status || "未审核"}</Badge>
                      {row.fusion?.coverage != null ? <Badge>条件覆盖 {row.fusion.coverage}</Badge> : null}
                    </div>
                  </div>
                  <div className="result-snippet">{row.summary}</div>
                  <div className="result-meta" data-patient-evidence="true">
                    患者证据：{(row.patient_graph_evidence || []).map((item: any) =>
                      `${item.record_path} (${item.origin})`).join("；")}
                  </div>
                  <div className="result-meta">
                    指南：{row.guideline_title || (row.source_ids || []).join("、") || "—"} · {
                      row.same_target || row.guideline_id === selectedGuidelineId
                        ? "当前目标指南，同源"
                        : "其他指南，只作旁证"
                    }
                  </div>
                  {row.missing_information?.length ? (
                    <div className="result-meta">未知项：{row.missing_information.join("；")}</div>
                  ) : null}
                </div>
              ))
            ) : (
              <Empty title="没有带患者图证据的推荐">
                这不等于患者没有相关事实；检查检索词与快照可见记录。
              </Empty>
            )}
            <details className="disclosure">
              <summary>检索与 release 审计（{recommendations.length} 条推荐）</summary>
              <div className="stack" style={{ marginTop: 10 }}>
                <div className="status-list">
                  <StatusRow label="knowledge_context_id" value={String(knowledgeFusion.knowledge_context_id || "").slice(0, 24)} />
                  <StatusRow label="release" value={knowledgeFusion.knowledge_release_id || "—"} />
                  <StatusRow label="release_sha256" value={String(knowledgeFusion.release_sha256 || "").slice(0, 16)} />
                  <StatusRow label="snapshot_sha256" value={String(knowledgeFusion.snapshot_sha256 || "").slice(0, 16)} />
                  <StatusRow label="retrieval_id" value={String(knowledgeFusion.retrieval_id || "").slice(0, 24)} />
                  <StatusRow label="intent" value={String(knowledgeFusion.diagnostics?.intent || "—")} />
                  <StatusRow label="effective_queries" value={(knowledgeFusion.effective_queries || []).join(" / ") || "—"} />
                </div>
                <div className="section-title">推荐与审核状态</div>
                {recommendations.map((row: any) => (
                  <div className="result-row" data-knowledge-recommendation={row.recommendation_id} key={`all-${row.recommendation_id}`}>
                    <div className="result-title">
                      <strong>{row.recommendation_id}</strong>
                      <div className="result-badges">
                        <Badge>{row.direction}</Badge>
                        <Badge>{row.review_status || "未审核"}</Badge>
                      </div>
                    </div>
                    <div className="result-snippet">{row.summary}</div>
                    <div className="result-meta">
                      证据：{(row.evidence_span_ids || []).join("、") || "—"} · 来源：
                      {(row.source_ids || []).join("、") || "—"}
                    </div>
                  </div>
                ))}
                <div className="section-title">术语归一</div>
                <div className="chips">
                  {(knowledgeFusion.term_resolutions || []).map((term: any, index: number) => (
                    <span className="chip" key={`${term.input}-${index}`}>
                      {term.input} → {term.status}
                      {term.candidates?.length ? ` (${term.candidates.map((item: any) => item.id).join(" / ")})` : ""}
                    </span>
                  ))}
                </div>
                <div className="section-title">排除的推荐</div>
                {(knowledgeFusion.excluded_recommendations || []).length ? (
                  (knowledgeFusion.excluded_recommendations || []).map((row: any) => (
                    <div className="result-row" data-knowledge-excluded={row.id} key={row.id}>
                      <strong>{row.id}</strong>
                      <div className="result-meta">排除原因：{row.reason}</div>
                    </div>
                  ))
                ) : (
                  <div className="hint">本轮没有排除项。</div>
                )}
                <div className="section-title">冲突与监测面板</div>
                {(knowledgeFusion.conflicts || []).length ? (
                  (knowledgeFusion.conflicts || []).map((row: any, index: number) => (
                    <div className="notice warn" data-knowledge-conflict={index} key={index}>
                      冲突：{(row.recommendations || []).join(" ↔ ")} —— {row.reason}
                    </div>
                  ))
                ) : (
                  <div className="hint">本轮没有未解决冲突。</div>
                )}
              </div>
            </details>
          </div>
        ) : (
          <Empty title="尚未运行双向检索">
            载入快照后输入检索词；结果会在生成适用性链路时自动传入。
          </Empty>
        )}
      </div>
    </div>
  );
}
