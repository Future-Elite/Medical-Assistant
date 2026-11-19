import { useState } from "react";
import { postJson } from "../api/client";
import { Badge, Empty, Notice } from "../components/ui";
import { Icon } from "../components/Icon";
import { useWorkbench } from "../state/workbench";

/**
 * The visible retrieval → top-k → graph → bidirectional flow.
 * The top-k list comes from the executed retrieval package, not a second
 * chunk search; graph construction is an explicit user action.
 */
export function TopKGuidelinesCard() {
  const {
    pkg,
    retrievalGuidelines,
    knowledgeFusion,
    selectGuideline,
    goStep,
    notify,
  } = useWorkbench();
  const [buildResult, setBuildResult] = useState<any>(null);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState("");

  const ids = (retrievalGuidelines || []).map((row) => row.guideline_id);
  const statusById = new Map<string, string>();
  (buildResult?.guidelines || []).forEach((row: any) => statusById.set(row.guideline_id, row.status));

  async function buildGraphs() {
    if (!ids.length) {
      setError("先执行检索，得到 Top-k 指南");
      return;
    }
    setBuilding(true);
    setError("");
    try {
      const data = await postJson("/api/knowledge/guideline-graphs/build", { guideline_ids: ids });
      setBuildResult(data);
      notify(
        `Top-k 建图完成：新建 ${data.built?.length ?? 0}，缓存 ${data.cached?.length ?? 0}`,
        "success",
      );
    } catch (err) {
      setError((err as Error).message);
      notify((err as Error).message, "error");
    } finally {
      setBuilding(false);
    }
  }

  const steps = [
    { label: "检索", state: pkg ? "done" : "pending" },
    { label: "Top-k 指南", state: ids.length ? "done" : "pending" },
    { label: "建指南图谱", state: buildResult ? "done" : ids.length ? "current" : "pending" },
    { label: "双向检索", state: knowledgeFusion ? "done" : buildResult ? "current" : "pending" },
  ];

  return (
    <div className="card" data-topk-guidelines="true">
      <div className="card-head">
        <h3>检索 → Top-k 指南 → 建图 → 双向检索</h3>
        <Badge tone={ids.length ? "done" : ""}>{ids.length ? `${ids.length} 本` : "待检索"}</Badge>
      </div>
      <div className="card-body stack">
        <div className="pipeline">
          {steps.map((step, index) => (
            <span key={step.label} className="pipeline-item">
              <span className={`pipeline-step ${step.state}`}>
                <i>{index + 1}</i>
                {step.label}
              </span>
              {index < steps.length - 1 ? <span className="pipeline-arrow">→</span> : null}
            </span>
          ))}
        </div>
        {error ? <Notice tone="error">{error}</Notice> : null}
        {!pkg ? (
          <Empty
            title="还没有检索结果"
            action={
              <button type="button" className="btn btn-sm" onClick={() => goStep("run")}>
                去执行检索
                <Icon name="arrowRight" size={13} />
              </button>
            }
          >
            先在 07 执行检索；指南 Top-k 来自本次检索的指南证据。
          </Empty>
        ) : !ids.length ? (
          <Empty title="本次检索没有指南结果">
            检查来源是否勾选“指南（本地 PDF）”，或调整问题后重试；这不等于本地没有指南。
          </Empty>
        ) : (
          <>
            {retrievalGuidelines.map((row, index) => {
              const status = statusById.get(row.guideline_id);
              return (
                <div className="result-row" key={`${row.guideline_id}-${index}`} data-topk-guideline={row.guideline_id}>
                  <div className="result-title">
                    <span className="result-rank">{index + 1}</span>
                    <strong>{row.title}</strong>
                    <div className="result-badges">
                      <Badge>{row.score.toFixed(3)}</Badge>
                      <Badge tone={status === "built" || status === "cached" ? "done" : status === "missing" ? "error" : ""}>
                        {status === "built" ? "已建图" : status === "cached" ? "已有图谱" : status === "missing" ? "缺块/缺图" : "待建图"}
                      </Badge>
                    </div>
                  </div>
                  <div className="result-meta">
                    {row.guideline_id} · 命中块 {row.chunk_id || "—"}
                    <button
                      type="button"
                      className="btn btn-sm"
                      style={{ marginLeft: 8 }}
                      onClick={() => selectGuideline(row.guideline_id)}
                    >
                      查看原文 / 分块
                    </button>
                  </div>
                </div>
              );
            })}
            <div className="row">
              <button type="button" className="btn btn-primary btn-sm" onClick={buildGraphs} disabled={building}>
                <Icon name="grid" size={13} />
                {building ? "建图中…" : buildResult ? "重建 / 检查图谱" : "建立 Top-k 指南图谱"}
              </button>
              {buildResult ? (
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => document.getElementById("knowledge-fusion")?.scrollIntoView({ behavior: "smooth", block: "start" })}
                >
                  下一步：双向检索
                  <Icon name="arrowRight" size={13} />
                </button>
              ) : null}
              {(buildResult?.warnings || []).length ? (
                <span className="hint">{(buildResult.warnings || []).slice(0, 2).join("；")}</span>
              ) : null}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
