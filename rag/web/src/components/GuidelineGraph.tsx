import { useEffect, useMemo, useState } from "react";
import { requestJson } from "../api/client";
import { Badge, Empty, Notice, StatusRow } from "../components/ui";
import { Icon } from "../components/Icon";
import { PatientGraphView, type PatientGraphPayload } from "../components/PatientGraph";

interface GuidelineSummary {
  guideline_id: string;
  title: string;
  version?: string;
  stats?: Record<string, number>;
  warnings?: number;
}

interface GuidelineLibrary {
  stats?: Record<string, number>;
  guidelines?: GuidelineSummary[];
  publishable?: boolean;
  review_status?: string;
}

export function GuidelineGraphCard({ guidelineId }: { guidelineId?: string }) {
  const [library, setLibrary] = useState<GuidelineLibrary | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const payload = await requestJson<GuidelineLibrary>("/api/knowledge/guideline-graphs");
        if (cancelled) return;
        setLibrary(payload);
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const selected = useMemo(() => {
    const ids = (library?.guidelines || []).map((row) => row.guideline_id);
    if (guidelineId && ids.includes(guidelineId)) return guidelineId;
    return ids[0] || "";
  }, [library, guidelineId]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    (async () => {
      try {
        const payload = await requestJson(`/api/knowledge/guideline-graphs/${encodeURIComponent(selected)}`);
        if (!cancelled) setDetail(payload);
      } catch (err) {
        if (!cancelled) { setDetail(null); setError((err as Error).message); }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [selected]);

  const graph: PatientGraphPayload | null = useMemo(() => {
    if (!detail?.nodes) return null;
    return {
      patient_ref: detail.guideline_id,
      as_of: detail.version || "",
      snapshot_sha256: detail.source?.content_sha256 || "",
      nodes: (detail.nodes || []).map((node: any) => ({
        node_id: String(node.id),
        node_type: String(node.type || "unknown"),
        label: String(node.title || node.summary || node.raw_text || node.id),
        attributes: node,
      })),
      edges: (detail.edges || []).map((edge: any) => ({
        source: String(edge.source),
        target: String(edge.target),
        relation: String(edge.type || edge.relation || "RELATED"),
        attributes: edge,
      })),
    };
  }, [detail]);

  const summary = library?.guidelines?.find((row) => row.guideline_id === selected);
  const stats = summary?.stats || detail?.stats || {};

  return (
    <div className="card" data-guideline-graph="true">
      <div className="card-head">
        <h3>指南图谱</h3>
        <Badge tone={library?.publishable ? "done" : "warn"}>
          {library?.publishable ? "已发布" : "提取候选"}
        </Badge>
      </div>
      <div className="card-body stack">
        <div className="notice info">
          <Icon name="file" size={15} />
          <div>
            本地 PDF 解析得到的推荐候选图；节点 <code>review_status=extracted</code>，不是临床审核指南。重新解析指南后需重建图谱；缺失图会按该指南 chunk 现场建图。
          </div>
        </div>
        {error ? <Notice tone="error">{error}</Notice> : null}
        {!library ? (
          <Empty title="尚未载入指南图库">需要先解析本地指南 PDF 并运行 build_guideline_graph。</Empty>
        ) : (
          <>
            <div className="row" style={{ alignItems: "baseline", flexWrap: "wrap" }}>
              <Badge>跟随当前指南</Badge>
              <span className="hint">
                {summary?.title || selected}
                {summary?.version ? ` · ${summary.version}` : ""}
              </span>
              <span className="spacer" />
              <span className="chip">推荐 {stats.recommendations ?? 0}</span>
              <span className="chip">条件 {stats.conditions ?? 0}</span>
              <span className="chip">例外 {stats.exceptions ?? 0}</span>
              <span className="chip">时间窗 {stats.timeframes ?? 0}</span>
              <span className="chip">原文块 {stats.chunks ?? 0}</span>
            </div>
            {loading ? <div className="hint">正在读取指南图…</div> : null}
            <div className="guideline-graph-canvas">
              <PatientGraphView
                graph={graph}
                title="指南结构图"
                emptyText="该指南没有可展示的图谱节点。"
              />
            </div>
            <div className="status-list">
              <StatusRow label="guideline_id" value={detail?.guideline_id || selected || "—"} />
              <StatusRow label="review_status" value={detail?.review_status || "extracted"} />
              <StatusRow
                label="校验"
                value={`${(detail?.validation?.errors || []).length} error / ${(detail?.validation?.warnings || []).length} warning`}
              />
              <StatusRow label="节点 / 边" value={`${graph?.nodes?.length || 0} / ${graph?.edges?.length || 0}`} />
            </div>
            {(detail?.warnings || []).length ? (
              <div className="hint">提取提示：{(detail.warnings || []).slice(0, 3).join("；")}</div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
