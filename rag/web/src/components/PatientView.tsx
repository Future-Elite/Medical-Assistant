import { collectionColors, collectionLabels } from "../lib/format";
import { Badge, Empty } from "./ui";
import type { Dict, PatientSnapshot } from "../types";

function formatTime(value: unknown): string {
  const text = String(value ?? "").trim();
  if (!text) return "—";
  return text.replace("T", " ").slice(0, 16);
}

export function SnapshotView({ snapshot }: { snapshot: PatientSnapshot | null }) {
  if (!snapshot) {
    return <Empty title="尚未载入快照" />;
  }
  const records = snapshot.records || [];
  const groups: Record<string, Dict[]> = {};
  records.forEach((record) => {
    const key = record.record_type || record.collection || "other";
    (groups[key] ||= []).push(record);
  });
  const initials = String(snapshot.patient_ref || "?")
    .replace(/^neo-/, "")
    .slice(0, 4)
    .toUpperCase();

  return (
    <div className="stack">
      <div className="case-summary">
        <div className="case-avatar">{initials}</div>
        <div className="case-meta" style={{ flex: 1 }}>
          <b>{snapshot.patient_ref}</b>
          <span>只读快照 · {snapshot.as_of}</span>
        </div>
        <div className="chips">
          <span className="chip">可见事实 {records.length} 条</span>
          {snapshot.suppressed_future ? (
            <span className="chip suppressed">已抑制未来记录 {snapshot.suppressed_future} 条</span>
          ) : null}
          <span className="chip">
            来源 {String(snapshot.source || "").includes("synthetic") ? "合成数据" : snapshot.source || "快照"}
          </span>
        </div>
      </div>
      {Object.keys(groups).length ? (
        Object.entries(groups).map(([key, rows]) => (
          <div className="fact-group" key={key}>
            <div className="fact-group-head">
              <span
                className="fact-dot"
                style={{ background: collectionColors[key] || "#5b6b78" }}
              />
              {collectionLabels[key] || key}
              <span className="fact-count">{rows.length} 条</span>
            </div>
            {rows.map((record, index) => {
              const fields = record.fields || {};
              const provenance = record.provenance || {};
              const main =
                Object.entries(fields)
                  .filter(
                    ([name]) =>
                      !["text", "free_text", "narrative", "note", "history"].includes(name),
                  )
                  .map(([name, value]) => `${name}=${String(value)}`)
                  .join(" · ") || String(record.event_type || "记录");
              return (
                <div className="fact-row" key={`${record.record_id || record.record_path}-${index}`}>
                  <span className="fact-time">{formatTime(record.event_time || record.time || record.start)}</span>
                  <div className="fact-main">
                    {main}
                    {fields.subject_role ? (
                      <span className="chip role" style={{ marginLeft: 6 }}>
                        {String(fields.subject_role)}
                      </span>
                    ) : null}
                    <div className="fact-path">
                      {record.record_id || record.record_path}
                      {provenance.field_path && provenance.field_path !== (record.record_id || record.record_path)
                        ? ` · ${provenance.field_path}`
                        : ""}
                      {record.end ? ` · 至 ${record.end}` : ""}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ))
      ) : (
        <Empty title="快照中没有可见事实" />
      )}
    </div>
  );
}

export function PatientHitsView({ pkg }: { pkg: any }) {
  const hits: Dict[] = pkg?.patient_hits || [];
  const graphTrace = pkg?.trace?.patient_graph || {};
  const query = graphTrace.query || {};
  const queryText = Array.isArray(query.input) ? query.input.join(" · ") : String(query.input || "");
  const intents = Array.isArray(query.intents) ? query.intents.join("、") : String(query.intents || "");
  const concepts = Array.isArray(query.concepts) ? query.concepts.join("、") : String(query.concepts || "");
  const statusCounts = hits.reduce<Record<string, number>>((counts, hit) => {
    const key = String(hit.status || "unknown");
    counts[key] = (counts[key] || 0) + 1;
    return counts;
  }, {});
  if (!hits.length) {
    return (
      <div className="stack">
        <div className="patient-hit-summary">
          患者检索：{queryText || "—"} · 状态 {graphTrace.status || "no_results"}；当前快照没有可返回的患者事实记录。
        </div>
        <Empty title="无患者事实命中" />
      </div>
    );
  }
  return (
    <div className="stack">
      <div className="patient-hit-summary">
        <div>
          <b>患者检索</b> · {queryText || "—"}
        </div>
        <div className="chips">
          {intents ? <span className="chip">意图 {intents}</span> : null}
          {concepts ? <span className="chip">概念 {concepts}</span> : null}
          {Object.entries(statusCounts).map(([status, count]) => (
            <span className="chip" key={status}>{status} {count}</span>
          ))}
          <span className="chip">{graphTrace.engine || "patient_graph"} · 命中 {hits.length}</span>
        </div>
        <div className="hint">图谱结构与命中路径可在 01 步骤“患者知识图谱”中查看。</div>
        {pkg?.trace?.patient_fact_expansion ? (
          <div className="hint">
            患者事实已回流本地指南检索：{(pkg.trace.patient_fact_expansion.terms || []).join("、") || "—"}
            （概念标签/匹配别名，不含原始记录；外部来源不加入）。
          </div>
        ) : null}
      </div>
      <div className="fact-group">
        <div className="fact-group-head">
          <span className="fact-dot" style={{ background: "var(--brand)" }} />
          本次命中
          <span className="fact-count">{hits.length} 条</span>
        </div>
        {hits.map((hit, index) => {
          const status = String(hit.status || "unknown");
          const positive = status === "observed_positive";
          return (
            <div className="fact-row patient-hit-row" key={`${hit.record_path}-${index}`}>
              <span className="fact-time">{formatTime(hit.time || hit.start)}</span>
              <div className="fact-main">
                <div className="patient-hit-line">
                  <span>{hit.evidence_text}</span>
                  <Badge tone={positive ? "done" : "warn"}>{status}</Badge>
                </div>
                {(hit.concept_ids || []).length ? (
                  <div className="chips">
                    {(hit.concept_ids || []).map((concept: string) => (
                      <span className="chip" key={concept}>{concept}</span>
                    ))}
                  </div>
                ) : null}
                <div className="fact-path">
                  {hit.record_path} · {hit.field_path}
                  {hit.match_method ? ` · 方法 ${hit.match_method}` : ""}
                </div>
                {(hit.graph_path || []).length ? (
                  <div className="fact-path graph-path">
                    {(hit.graph_path || []).join(" → ")}
                  </div>
                ) : null}
              </div>
              <span className="fact-score">{Number(hit.score).toFixed(3)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
