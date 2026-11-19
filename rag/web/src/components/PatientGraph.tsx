import { useMemo, useState } from "react";
import { collectionColors } from "../lib/format";
import type { Dict } from "../types";

export interface PatientGraphPayload {
  nodes?: PatientGraphNode[];
  edges?: PatientGraphEdge[];
  snapshot_sha256?: string;
  node_count?: number;
  edge_count?: number;
  [key: string]: any;
}

export interface PatientGraphNode {
  node_id: string;
  node_type: string;
  label: string;
  attributes?: Dict;
}

export interface PatientGraphEdge {
  source: string;
  target: string;
  relation: string;
  attributes?: Dict;
}

interface Point {
  x: number;
  y: number;
}

const WIDTH = 940;
const HEIGHT = 560;
const PADDING = 64;

const NODE_COLORS: Record<string, string> = {
  patient: "#0b4f6c",
  record: "#2563a8",
  concept: "#0e7490",
  subject: "#7c4dad",
  time: "#93a4b3",
  episode: "#9a6700",
  guideline: "#0b4f6c",
  recommendation: "#b42318",
  condition: "#9a6700",
  exception: "#7c4dad",
  timeframe: "#0e7490",
  chunk: "#64788a",
  topic: "#2f7d68",
};

const NODE_LABELS: Record<string, string> = {
  patient: "患者快照",
  record: "记录",
  concept: "事实概念",
  subject: "主体",
  time: "时间",
  episode: "决策时点",
  guideline: "指南",
  recommendation: "推荐",
  condition: "条件",
  exception: "例外",
  timeframe: "时间窗",
  chunk: "原文块",
  topic: "主题",
};

const EDGE_STYLES: Record<string, { color: string; dash?: string; arrow?: boolean }> = {
  HAS_RECORD: { color: "#9fb3c2" },
  HAS_SUBJECT: { color: "#9b7bc0" },
  MENTIONS: { color: "#7bb7c9", dash: "4 3" },
  DERIVED_FROM: { color: "#c08a2e", arrow: true },
  RECORDED_AT: { color: "#b6c4cf", dash: "2 3" },
  OCCURS_IN: { color: "#b6c4cf", dash: "2 3" },
  TEMPORALLY_BEFORE: { color: "#8798a6", dash: "1 4", arrow: true },
  has_recommendation: { color: "#b42318" },
  requires: { color: "#9a6700" },
  excludes: { color: "#7c4dad" },
  within_time: { color: "#0e7490" },
  supported_by: { color: "#64788a", dash: "4 3" },
  part_of_source: { color: "#9fb3c2" },
};

function nodeRadius(node: PatientGraphNode): number {
  if (node.node_type === "patient") return 22;
  if (node.node_type === "subject") return 13;
  if (node.node_type === "episode") return 11;
  if (node.node_type === "record") return 8;
  if (node.node_type === "recommendation") return 10;
  if (node.node_type === "condition") return 7;
  if (node.node_type === "guideline") return 16;
  if (node.node_type === "concept") return 6;
  return 5;
}

function nodeFill(node: PatientGraphNode): string {
  if (node.node_type === "record") {
    const collection = String(node.attributes?.collection || "");
    return collectionColors[collection] || NODE_COLORS.record;
  }
  return NODE_COLORS[node.node_type] || "#5b6b78";
}

function shortLabel(node: PatientGraphNode, selected: boolean): string {
  const raw = String(node.label || node.node_id);
  if (node.node_type === "patient") return raw;
  if (!selected && node.node_type === "concept") return raw.length > 8 ? `${raw.slice(0, 8)}…` : raw;
  return raw.length > 16 ? `${raw.slice(0, 16)}…` : raw;
}

function buildLayout(nodes: PatientGraphNode[], edges: PatientGraphEdge[]) {
  const positions = new Map<string, Point>();
  const ordered = [...nodes].sort((left, right) => {
    const order = ["patient", "episode", "subject", "record", "time", "concept"];
    const typeDelta = order.indexOf(left.node_type) - order.indexOf(right.node_type);
    return typeDelta !== 0 ? typeDelta : left.node_id.localeCompare(right.node_id);
  });
  const counters: Record<string, number> = {};
  const totals: Record<string, number> = {};
  ordered.forEach((node) => {
    totals[node.node_type] = (totals[node.node_type] || 0) + 1;
  });
  ordered.forEach((node) => {
    const index = counters[node.node_type] || 0;
    counters[node.node_type] = index + 1;
    if (node.node_type === "patient") {
      positions.set(node.node_id, { x: 0, y: 0 });
      return;
    }
    const total = Math.max(1, totals[node.node_type] || 1);
    const angle = (index / total) * Math.PI * 2 + (node.node_type === "concept" ? Math.PI / 5 : 0);
    const radius =
      node.node_type === "episode" ? 96 :
      node.node_type === "subject" ? 120 :
      node.node_type === "record" ? 190 :
      node.node_type === "time" ? 255 : 320;
    positions.set(node.node_id, {
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius * 0.82,
    });
  });

  const points = ordered.map((node) => ({ node, point: positions.get(node.node_id)! }));
  const indexOf = new Map(ordered.map((node, index) => [node.node_id, index]));
  for (let iteration = 0; iteration < 150; iteration += 1) {
    const deltas = points.map(() => ({ x: 0, y: 0 }));
    for (let left = 0; left < points.length; left += 1) {
      for (let right = left + 1; right < points.length; right += 1) {
        const a = points[left].point;
        const b = points[right].point;
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let distanceSquared = dx * dx + dy * dy;
        if (distanceSquared < 1) {
          dx = (left + 1) * 0.01;
          dy = (right + 1) * 0.01;
          distanceSquared = dx * dx + dy * dy;
        }
        const distance = Math.sqrt(distanceSquared);
        const force = Math.min(14, 900 / distanceSquared);
        const fx = (dx / distance) * force;
        const fy = (dy / distance) * force;
        deltas[left].x += fx;
        deltas[left].y += fy;
        deltas[right].x -= fx;
        deltas[right].y -= fy;
      }
    }
    edges.forEach((edge) => {
      const left = indexOf.get(edge.source);
      const right = indexOf.get(edge.target);
      if (left === undefined || right === undefined) return;
      const a = points[left].point;
      const b = points[right].point;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const distance = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const force = (distance - 110) * 0.018;
      const fx = (dx / distance) * force;
      const fy = (dy / distance) * force;
      deltas[left].x += fx;
      deltas[left].y += fy;
      deltas[right].x -= fx;
      deltas[right].y -= fy;
    });
    points.forEach(({ node, point }, index) => {
      if (node.node_type === "patient") return;
      point.x += Math.max(-8, Math.min(8, deltas[index].x * 0.02 - point.x * 0.004));
      point.y += Math.max(-8, Math.min(8, deltas[index].y * 0.02 - point.y * 0.004));
    });
  }

  const xs = points.map(({ point }) => point.x);
  const ys = points.map(({ point }) => point.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = Math.max(1, maxX - minX);
  const spanY = Math.max(1, maxY - minY);
  const scale = Math.min(
    (WIDTH - PADDING * 2) / spanX,
    (HEIGHT - PADDING * 2) / spanY,
  );
  const offsetX = (WIDTH - spanX * scale) / 2 - minX * scale;
  const offsetY = (HEIGHT - spanY * scale) / 2 - minY * scale;
  const laidOut = new Map<string, Point>();
  points.forEach(({ node, point }) => {
    laidOut.set(node.node_id, {
      x: point.x * scale + offsetX,
      y: point.y * scale + offsetY,
    });
  });
  return laidOut;
}

function attributeRows(node: PatientGraphNode): Array<[string, string]> {
  const attributes = node.attributes || {};
  return Object.entries(attributes)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => [
      key,
      typeof value === "object" ? JSON.stringify(value) : String(value),
    ]);
}

export function PatientGraphView({
  graph,
  highlightRecordPaths = [],
  title = "患者知识图谱",
  emptyText = "尚未构建图谱。载入快照后点击“构建患者图谱”。",
}: {
  graph: PatientGraphPayload | null;
  highlightRecordPaths?: string[];
  title?: string;
  emptyText?: string;
}) {
  const [selectedId, setSelectedId] = useState<string>("");
  const [zoom, setZoom] = useState(1);
  const [hitOnly, setHitOnly] = useState(false);
  const nodes = useMemo(() => graph?.nodes || [], [graph]);
  const edges = useMemo(() => graph?.edges || [], [graph]);
  const selected = nodes.find((node) => node.node_id === selectedId) || null;
  const hitPaths = useMemo(() => new Set(highlightRecordPaths.filter(Boolean)), [highlightRecordPaths]);
  const hitNodeIds = useMemo(() => new Set(nodes
    .filter((node) => node.node_type === "record" && hitPaths.has(String(node.attributes?.record_path || "")))
    .map((node) => node.node_id)), [nodes, hitPaths]);
  const visibleNodeIds = useMemo(() => {
    if (!hitOnly) return null;
    const ids = new Set<string>(hitNodeIds);
    nodes.forEach((node) => { if (node.node_type === "patient") ids.add(node.node_id); });
    edges.forEach((edge) => {
      if (hitNodeIds.has(edge.source)) ids.add(edge.target);
      if (hitNodeIds.has(edge.target)) ids.add(edge.source);
    });
    return ids;
  }, [hitOnly, hitNodeIds, nodes, edges]);
  const visibleNodes = useMemo(
    () => (visibleNodeIds ? nodes.filter((node) => visibleNodeIds.has(node.node_id)) : nodes),
    [visibleNodeIds, nodes],
  );
  const visibleEdges = useMemo(
    () => (visibleNodeIds ? edges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)) : edges),
    [visibleNodeIds, edges],
  );
  const layout = useMemo(() => buildLayout(visibleNodes, visibleEdges), [visibleNodes, visibleEdges]);
  const neighborIds = useMemo(() => {
    if (!selectedId) return new Set<string>();
    const ids = new Set<string>([selectedId]);
    edges.forEach((edge) => {
      if (edge.source === selectedId) ids.add(edge.target);
      if (edge.target === selectedId) ids.add(edge.source);
    });
    return ids;
  }, [edges, selectedId]);

  if (!nodes.length) {
    return (
      <div className="patient-graph-empty">
        {emptyText}
      </div>
    );
  }

  const viewWidth = WIDTH / zoom;
  const viewHeight = HEIGHT / zoom;
  const viewX = (WIDTH - viewWidth) / 2;
  const viewY = (HEIGHT - viewHeight) / 2;

  return (
    <div className="patient-graph" data-graph-nodes={nodes.length} data-graph-edges={edges.length}>
      <div className="patient-graph-toolbar">
        <div className="patient-graph-title">
          <b>{title}</b>
          <span>
            {nodes.length} 节点 · {edges.length} 边 · 命中记录 {hitPaths.size} 条
          </span>
        </div>
        <div className="patient-graph-actions">
          <label className="check patient-graph-toggle">
            <input type="checkbox" checked={hitOnly} onChange={(event) => setHitOnly(event.target.checked)} />
            仅看命中邻域
          </label>
          <button type="button" className="btn btn-sm" onClick={() => setZoom((value) => Math.min(1.8, Number((value + 0.2).toFixed(2))))}>
            +
          </button>
          <button type="button" className="btn btn-sm" onClick={() => setZoom((value) => Math.max(0.7, Number((value - 0.2).toFixed(2))))}>
            −
          </button>
          <button type="button" className="btn btn-sm" onClick={() => setZoom(1)}>
            重置
          </button>
        </div>
      </div>
      <div className="patient-graph-canvas">
        <svg
          viewBox={`${viewX} ${viewY} ${viewWidth} ${viewHeight}`}
          role="img"
          aria-label="患者知识图谱节点与关系"
          className="patient-graph-svg"
        >
          <defs>
            <marker id="patient-graph-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#8798a6" />
            </marker>
            <marker id="patient-graph-arrow-derived" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#c08a2e" />
            </marker>
          </defs>
          <g className="patient-graph-edges">
            {visibleEdges.filter((edge) => !selectedId || edge.source === selectedId || edge.target === selectedId).map((edge, index) => {
              const source = layout.get(edge.source);
              const target = layout.get(edge.target);
              if (!source || !target) return null;
              const style = EDGE_STYLES[edge.relation] || { color: "#b6c4cf" };
              const active = selectedId
                ? edge.source === selectedId || edge.target === selectedId
                : true;
              return (
                <line
                  key={`${edge.source}-${edge.relation}-${edge.target}-${index}`}
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  stroke={style.color}
                  strokeWidth={active ? 1.4 : 0.6}
                  strokeDasharray={style.dash}
                  opacity={selectedId ? (active ? 0.95 : 0.1) : (edge.relation === "MENTIONS" ? 0.28 : edge.relation === "HAS_RECORD" ? 0.5 : 0.72)}
                  markerEnd={style.arrow ? `url(#${edge.relation === "DERIVED_FROM" ? "patient-graph-arrow-derived" : "patient-graph-arrow"})` : undefined}
                >
                  <title>{`${edge.source} -[${edge.relation}]-> ${edge.target}`}</title>
                </line>
              );
            })}
          </g>
          <g className="patient-graph-nodes">
            {visibleNodes.map((node) => {
              const point = layout.get(node.node_id);
              if (!point) return null;
              const recordPath = String(node.attributes?.record_path || "");
              const isHit = node.node_type === "record" && hitPaths.has(recordPath);
              const dim = selectedId && !neighborIds.has(node.node_id);
              const radius = nodeRadius(node);
              return (
                <g
                  key={node.node_id}
                  className={`patient-graph-node ${isHit ? "is-hit" : ""} ${selectedId === node.node_id ? "is-selected" : ""}`}
                  transform={`translate(${point.x}, ${point.y})`}
                  opacity={dim ? 0.22 : 1}
                  onClick={() => setSelectedId((current) => (current === node.node_id ? "" : node.node_id))}
                  data-node-id={node.node_id}
                  data-node-type={node.node_type}
                >
                  <title>{`${NODE_LABELS[node.node_type] || node.node_type}：${node.label}`}</title>
                  {isHit ? <circle r={radius + 5} fill="none" stroke="var(--cinnabar, #b42318)" strokeWidth={2} opacity={0.9} /> : null}
                  <circle
                    r={radius}
                    fill={nodeFill(node)}
                    stroke={selectedId === node.node_id ? "var(--ink, #10212f)" : "rgba(255,255,255,0.9)"}
                    strokeWidth={selectedId === node.node_id ? 2 : 1}
                  />
                  {(
                    node.node_type === "patient" ||
                    node.node_type === "concept" ||
                    node.node_type === "subject" ||
                    selectedId === node.node_id ||
                    isHit
                  ) ? (
                    <text
                      y={radius + 11}
                      textAnchor="middle"
                      className="patient-graph-label"
                    >
                      {shortLabel(node, selectedId === node.node_id)}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </g>
        </svg>
      </div>
      <div className="patient-graph-footer">
        <div className="patient-graph-legend">
          {Object.entries(NODE_LABELS).map(([type, label]) => (
            <span key={type} className="patient-graph-legend-item">
              <i style={{ background: NODE_COLORS[type] }} />
              {label}
            </span>
          ))}
          <span className="patient-graph-legend-item"><i className="line-solid" />包含</span>
          <span className="patient-graph-legend-item"><i className="line-dashed" />概念/来源</span>
          <span className="patient-graph-legend-item"><i className="line-hit" />命中记录</span>
        </div>
        {selected ? (
          <div className="patient-graph-detail" data-selected-node={selected.node_id}>
            <div className="patient-graph-detail-head">
              <b>{selected.label}</b>
              <span>{NODE_LABELS[selected.node_type] || selected.node_type} · {selected.node_id}</span>
            </div>
            <dl>
              {attributeRows(selected).map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          </div>
        ) : (
          <div className="hint">
            {hitOnly ? `仅显示命中邻域：${visibleNodes.length} 节点 / ${visibleEdges.length} 边。` : "点击节点查看属性与相邻边；红色外圈为本次患者检索命中的记录。"}
          </div>
        )}
      </div>
    </div>
  );
}
