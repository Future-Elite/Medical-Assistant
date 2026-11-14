import { collectionColors, collectionLabels } from "../lib/format";
import { Empty } from "./ui";
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
  const facts = snapshot.facts || [];
  const groups: Record<string, Dict[]> = {};
  facts.forEach((fact) => {
    const key = fact.collection || "other";
    (groups[key] ||= []).push(fact);
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
          <span className="chip">可见事实 {facts.length} 条</span>
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
            {rows.map((fact, index) => {
              const fields = fact.fields || {};
              const main =
                Object.entries(fields)
                  .filter(
                    ([name]) =>
                      !["text", "free_text", "narrative", "note", "history"].includes(name),
                  )
                  .map(([name, value]) => `${name}=${String(value)}`)
                  .join(" · ") || String(fact.event_type || "记录");
              return (
                <div className="fact-row" key={`${fact.record_path}-${index}`}>
                  <span className="fact-time">{formatTime(fact.time || fact.start)}</span>
                  <div className="fact-main">
                    {main}
                    {fields.subject_role ? (
                      <span className="chip role" style={{ marginLeft: 6 }}>
                        {String(fields.subject_role)}
                      </span>
                    ) : null}
                    <div className="fact-path">
                      {fact.record_path} · {fact.field_path}
                      {fact.end ? ` · 至 ${fact.end}` : ""}
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
  if (!hits.length) return <Empty title="无患者事实命中" />;
  return (
    <div className="fact-group">
      <div className="fact-group-head">
        <span className="fact-dot" style={{ background: "var(--brand)" }} />
        本次命中
        <span className="fact-count">{hits.length} 条</span>
      </div>
      {hits.map((hit, index) => (
        <div className="fact-row" key={index}>
          <span className="fact-time">{formatTime(hit.time || hit.start)}</span>
          <div className="fact-main">
            {hit.evidence_text}
            <div className="fact-path">
              {hit.record_path} · {hit.field_path} · 命中：{(hit.matched_terms || []).join("、")}
            </div>
          </div>
          <span className="fact-score">{Number(hit.score).toFixed(3)}</span>
        </div>
      ))}
    </div>
  );
}
