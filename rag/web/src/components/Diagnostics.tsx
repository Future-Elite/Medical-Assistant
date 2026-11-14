import { Badge } from "./ui";
import { Icon } from "./Icon";
import { kindLabels, rankDelta, score } from "../lib/format";
import type { Dict } from "../types";

function DiagCard({
  title,
  badge,
  children,
  wide = false,
}: {
  title: string;
  badge?: React.ReactNode;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <section className={`diag-card ${wide ? "wide" : ""}`.trim()}>
      <div className="diag-head">
        <h4>{title}</h4>
        {badge}
      </div>
      <div className="diag-body">{children}</div>
    </section>
  );
}

function EmptyDiag({ text }: { text: string }) {
  return <div className="hint">{text}</div>;
}

export function MappingSourcesCard({ pkg }: { pkg: any }) {
  const qplan = pkg?.query_plan || {};
  const rows: Dict[] = qplan.mapping_trace || [];
  const expanded = rows.filter((row) => row.status === "expanded");
  const requestRows = expanded.filter((row) => (row.origin || "request") === "request");
  const libraryRows = expanded.filter((row) => row.origin === "library");
  const trace = pkg?.trace?.query_plan?.term_mapping || {};
  const badge = trace.library_rows ? (
    <Badge>
      库展开 {trace.library_expanded || 0}/{trace.library_rows}
    </Badge>
  ) : trace.expanded ? (
    <Badge>请求展开 {trace.expanded}</Badge>
  ) : null;

  return (
    <DiagCard title="术语映射来源" badge={badge}>
      {requestRows.length ? (
        <div className="hint">请求行展开：{requestRows.map((row) => row.concept).join("、")}</div>
      ) : null}
      {libraryRows.length ? (
        <div className="hint">库行展开：{libraryRows.map((row) => row.concept).join("、")}</div>
      ) : null}
      {!requestRows.length && !libraryRows.length ? <EmptyDiag text="本次无映射展开" /> : null}
      <div className="hint">
        共展开 {trace.expanded || 0} 行 · 未命中 {(trace.unmatched || []).length} 个概念 · needs_review
      </div>
    </DiagCard>
  );
}

export function RoutingCard({ pkg }: { pkg: any }) {
  const route = pkg?.trace?.route_decision;
  if (!route) return null;
  const selected = (route.selected_sources || []).join(" → ") || "—";
  return (
    <DiagCard
      title="来源路由"
      badge={<Badge tone={route.applied ? "done" : ""}>{route.mode}</Badge>}
    >
      <div className="hint">
        规则 <b>{route.rule}</b> · 意图 {route.clinical_intent || "—"} · 英文覆盖{" "}
        {route.english_coverage ? "有" : "无"}
      </div>
      <div className="hint">选中来源：{selected}</div>
      <div className="hint">
        实际来源顺序：{(route.source_order_after || []).join(" → ") || "保留原顺序"}
      </div>
      <div className="hint">
        {(route.rationale || []).join(" ")}
        {route.notes?.length ? ` ${route.notes.join(" ")}` : ""}
      </div>
    </DiagCard>
  );
}

export function QueryReviewCard({ pkg }: { pkg: any }) {
  const review = pkg?.trace?.query_review;
  if (!review) return null;
  return (
    <DiagCard
      title="检索式自检 · PRESS"
      badge={
        review.status === "ok" ? (
          <Badge tone="done">未发现需复核项</Badge>
        ) : (
          <Badge tone="warn">需人工复核</Badge>
        )
      }
    >
      <ul className="review-list">
        {(review.checks || []).map((check: Dict, index: number) => (
          <li className="review-item" key={index}>
            <span className={check.status === "ok" ? "ok" : "find"}>
              {check.status === "ok" ? "✓" : "!"}
            </span>
            <span>
              <b>{check.item}</b> · {check.detail || ""}
            </span>
          </li>
        ))}
      </ul>
      {(review.findings || []).map((finding: string, index: number) => (
        <div className="review-finding" key={index}>
          {finding}
        </div>
      ))}
      <div className="hint">{review.note || ""}</div>
    </DiagCard>
  );
}

export function JointCard({ pkg }: { pkg: any }) {
  const joint = pkg?.trace?.joint_selection;
  if (!joint || !joint.participating_sources?.length) return null;
  const selected = joint.selected_per_source || {};
  const guaranteed = joint.guaranteed_minimums || {};
  const merit = joint.merit_filled_per_source || {};
  return (
    <DiagCard title="联合选择 · 分源配额" badge={<Badge>{joint.strategy || "per_source"}</Badge>}>
      <div className="quota-grid">
        {joint.participating_sources.map((kind: string) => {
          const preferred = joint.intent_preference === kind;
          return (
            <span className={`quota ${preferred ? "preferred" : ""}`.trim()} key={kind}>
              {kindLabels[kind] || kind} · 入选 <b>{selected[kind] ?? 0}</b> · 保底{" "}
              {guaranteed[kind] ?? 0}
              {merit[kind] ? ` · 择优 ${merit[kind]}` : ""}
              {preferred ? " · 意图优先" : ""}
            </span>
          );
        })}
      </div>
      <div className="hint" style={{ marginTop: 8 }}>
        {(joint.rationale || []).map((line: string, index: number) => (
          <div key={index}>{line}</div>
        ))}
      </div>
    </DiagCard>
  );
}

export function FidelityCard({ pkg }: { pkg: any }) {
  const fidelity = pkg?.trace?.query_fidelity;
  if (!fidelity || !fidelity.applied) return null;
  const report = fidelity.report || {};
  return (
    <DiagCard
      title="查询保真审计与门控"
      badge={<Badge tone="warn">已应用</Badge>}
    >
      <div className="hint">策略：{fidelity.policy}</div>
      <ul className="review-list" style={{ marginTop: 6 }}>
        {(fidelity.decisions || []).map((decision: Dict, index: number) => (
          <li className="review-item" key={index}>
            <span
              className={
                decision.action === "reject" || decision.action === "downweight" ? "find" : "ok"
              }
            >
              {decision.action}
            </span>
            <span>
              <b>{decision.route}</b> · {decision.level} · {decision.reason || ""}
            </span>
          </li>
        ))}
      </ul>
      {(report.routes || []).map((route: Dict, index: number) => {
        const findings = (route.findings || []).filter((item: Dict) => item.level !== "L0");
        return (
          <div key={index} style={{ marginTop: 6 }}>
            <div className="hint">
              <b>{route.route}</b> · status={route.status} · level={route.level}
            </div>
            {findings.length ? (
              findings.map((item: Dict, i: number) => (
                <div className="review-finding" key={i}>
                  [{item.level}/{item.verdict}] {item.evidence || ""}
                </div>
              ))
            ) : (
              <div className="hint">无分级问题 · status={route.status}</div>
            )}
          </div>
        );
      })}
      <div className="hint" style={{ marginTop: 6 }}>
        词面假设 · 非临床结论
      </div>
    </DiagCard>
  );
}

export function EntityLinkingCard({ pkg }: { pkg: any }) {
  const entity = pkg?.trace?.entity_linking;
  if (!entity) return null;
  const links: Dict[] = entity.links || [];
  const count = (status: string) => links.filter((row) => row.status === status).length;
  const verifications: Dict[] = entity.mapping_verifications || [];
  const bad = verifications.filter(
    (row) => row.status === "conflict" || row.status === "ambiguous",
  );
  const added = (entity.added_terms || []).flatMap((row: Dict) =>
    (row.terms || []).map((term: string) => `${row.kind}：${term}`),
  );
  const semantic = entity.linker?.semantic || {};
  const semanticText = semantic.configured
    ? semantic.loaded
      ? "已加载"
      : semantic.reason
        ? `不可用：${semantic.reason}`
        : "待首次加载"
    : "off";
  return (
    <DiagCard
      title="实体链接 · MeSH"
      badge={
        <Badge tone={entity.applied ? "done" : ""}>
          {entity.mode}
          {entity.applied ? " · 已应用" : ""}
        </Badge>
      }
    >
      <div className="hint">
        链接 {count("linked")} · 歧义 {count("ambiguous")} · 未链接 {count("unlinked")} · 无英文表面{" "}
        {count("skipped_no_english_surface")} · 语义候选 {semanticText}
        {entity.semantic_error ? ` · ${entity.semantic_error}` : ""}
      </div>
      {added.length ? <div className="hint">新增术语：{added.join("、")}</div> : null}
      <ul className="review-list" style={{ marginTop: 6 }}>
        {links
          .filter((row) => row.status !== "skipped_no_english_surface")
          .slice(0, 10)
          .map((row, index) => {
            const candidate = (row.candidates || [])[0];
            return (
              <li className="review-item" key={index}>
                <span className={row.status === "linked" ? "ok" : "find"}>{row.status}</span>
                <span>
                  <b>{row.surface}</b>
                  {row.expanded_from ? ` → ${row.expanded_from}` : ""}
                  {candidate
                    ? ` · ${candidate.label} · ${Number(candidate.score).toFixed(2)} · ${candidate.method}`
                    : ""}
                </span>
              </li>
            );
          })}
      </ul>
      {bad.length ? (
        <>
          <div className="hint">映射核验异常</div>
          <ul className="review-list">
            {bad.map((row, index) => (
              <li className="review-item" key={index}>
                <span className="find">{row.status}</span>
                <span>
                  <b>{row.concept}</b> · {row.reason || ""}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <div className="hint">概念身份不是字符串保留；歧义/冲突不进入 active 扩展。</div>
    </DiagCard>
  );
}

export function LifecycleCard({ pkg }: { pkg: any }) {
  const lifecycle = pkg?.trace?.guideline_lifecycle;
  if (!lifecycle) return null;
  const statuses = lifecycle.statuses || {};
  const counts: Record<string, number> = {};
  Object.values(statuses).forEach((value: any) => {
    const key = value.status || "unknown";
    counts[key] = (counts[key] || 0) + 1;
  });
  return (
    <DiagCard
      title="指南生命周期 · PubMed"
      badge={
        <Badge tone={lifecycle.applied ? "done" : ""}>
          {lifecycle.mode}
          {lifecycle.applied ? " · 已应用" : ""}
        </Badge>
      }
    >
      <div className="hint">
        状态：
        {Object.keys(counts).length
          ? Object.entries(counts)
              .map(([key, value]) => `${key} ${value}`)
              .join(" · ")
          : "无"}{" "}
        · as_of {lifecycle.as_of || "—"}
      </div>
      <ul className="review-list" style={{ marginTop: 6 }}>
        {(lifecycle.documents || []).slice(0, 10).map((row: Dict, index: number) => {
          const status = statuses[row.pmid] || {};
          const events = [...(status.applied_events || []), ...(status.future_events || []), ...(status.unknown_date_events || [])]
            .map((event: Dict) => `${event.kind}@${event.date || "日期未知"}`)
            .join("、");
          return (
            <li className="review-item" key={index}>
              <span className={row.lifecycle_status === "retracted" ? "find" : "ok"}>
                {row.lifecycle_status || "unknown"}
              </span>
              <span>
                <b>{row.document_id}</b> · PMID {row.pmid} · {events || "无事件"}
                {row.applied ? " · 已应用" : ""}
              </span>
            </li>
          );
        })}
      </ul>
      {(lifecycle.unresolved || []).length ? (
        <div className="hint">未取得记录：{lifecycle.unresolved.join("、")}</div>
      ) : null}
      {(lifecycle.synthetic_excluded || []).length ? (
        <div className="hint">合成 PMID 未外发：{lifecycle.synthetic_excluded.join("、")}</div>
      ) : null}
      {(lifecycle.warnings || []).map((warning: string, index: number) => (
        <div className="review-finding" key={index}>
          {warning}
        </div>
      ))}
      {lifecycle.error ? <div className="review-finding">{lifecycle.error}</div> : null}
      <div className="hint">只读 PubMed；日期未知不过滤，当前状态与历史 as_of 分开。</div>
    </DiagCard>
  );
}

export function RankingCard({ pkg }: { pkg: any }) {
  const rows: Dict[] = pkg?.ranked_documents || [];
  if (!rows.length) return null;
  const rerank = pkg?.trace?.rerank_trace || {};
  const rerankState =
    rerank.status === "ok"
      ? `已重排 · ${rerank.reranker_id || "reranker"}`
      : rerank.status === "not_configured"
        ? "重排未配置"
        : "未重排";
  return (
    <DiagCard
      title="排序与分数构成"
      badge={<Badge tone={rerank.status === "ok" ? "done" : ""}>{rerankState}</Badge>}
      wide
    >
      <div className="hint" style={{ marginBottom: 8 }}>
        base = 0.55×词面归一×variant_factor + 0.20×RRF归一 + 0.15×权威 + 0.10×新鲜度；有神经分数时总分 =
        0.70×base + 0.30×best_neural；纯神经时总分 = 0.70×neural + 0.20×RRF归一 + 0.10×新鲜度；RRF=Σ1/(60+rank)。
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="data-table" style={{ minWidth: 980 }}>
          <thead>
            <tr>
              <th>最终</th>
              <th>重排前</th>
              <th>变化</th>
              <th>文档</th>
              <th>总分</th>
              <th>base</th>
              <th>词面归一</th>
              <th>RRF归一</th>
              <th>词面原分</th>
              <th>语义</th>
              <th>RRF原分</th>
              <th>权威</th>
              <th>新鲜</th>
              <th>重排分</th>
              <th>检索器 / 重排器</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const finalRank = index + 1;
              const delta = row.fusion_rank ? row.fusion_rank - finalRank : 0;
              return (
                <tr key={index}>
                  <td className="num">#{finalRank}</td>
                  <td className="num">{row.fusion_rank ? `#${row.fusion_rank}` : "—"}</td>
                  <td>
                    <Badge tone={delta > 0 ? "done" : delta < 0 ? "warn" : ""}>
                      {rankDelta(row.fusion_rank, finalRank)}
                    </Badge>
                  </td>
                  <td style={{ minWidth: 180 }}>{String(row.document.title).slice(0, 80)}</td>
                  <td className="num">{score(row.score)}</td>
                  <td className="num">{score(row.base_score)}</td>
                  <td className="num">{score(row.lexical_norm_score)}</td>
                  <td className="num">{score(row.fusion_norm_score, 4)}</td>
                  <td className="num">{score(row.lexical_score)}</td>
                  <td className="num">{score(row.semantic_score)}</td>
                  <td className="num">{score(row.fusion_score, 4)}</td>
                  <td className="num">{score(row.authority_score)}</td>
                  <td className="num">{score(row.freshness_score)}</td>
                  <td className="num">{score(row.reranker_score)}</td>
                  <td className="mono" style={{ fontSize: 10.5 }}>
                    {row.retriever_id || "—"} / {row.reranker_id || "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="hint" style={{ marginTop: 8 }}>
        重排前列 = joint selection 的 fusion_rank；最终列为重排后顺序。未请求/未配置重排时两者相同。
      </div>
    </DiagCard>
  );
}

export function DiagnosticsGrid({ pkg }: { pkg: any }) {
  return (
    <div className="bento">
      <MappingSourcesCard pkg={pkg} />
      <RoutingCard pkg={pkg} />
      <QueryReviewCard pkg={pkg} />
      <JointCard pkg={pkg} />
      <FidelityCard pkg={pkg} />
      <EntityLinkingCard pkg={pkg} />
      <LifecycleCard pkg={pkg} />
      <RankingCard pkg={pkg} />
    </div>
  );
}
