import { Badge } from "./ui";
import type { BadgeTone, Dict } from "../types";

const OUTCOME_LABEL: Record<string, string> = {
  resolved: "已解决",
  needs_information: "需补事实",
  needs_table_lookup: "需查表",
  external_evidence: "有外部候选",
  evidence_gap: "证据缺口",
  scope_mismatch: "条件矛盾",
};

const TARGET_STATUS_LABEL: Record<string, string> = {
  possible_match: "可能适用",
  mismatch: "对不上",
  unknown: "看不出来",
  not_assessed: "未评估",
};

const STOP_REASON_LABEL: Record<string, string> = {
  target_or_same_family_coverage: "目标指南或同一类病的指南已覆盖",
  missing_fact_can_change_target: "缺关键事实，补事实才可能改变结论",
  table_or_curve_required: "需要查指南里的表/曲线",
  external_candidates_found: "找到外部候选，但还没有判定为指南适用",
  no_candidate_or_backend: "本地和外部都没有候选，或没有外部检索后端",
  structured_scope_contradiction: "条件与病历记录矛盾，需要人工核对",
};

const TOPIC_LABEL: Record<string, string> = {
  hyperbilirubinemia: "高胆红素",
};

const NEXT_ACTION_LABEL: Record<string, string> = {
  supply_fact: "补事实后继续",
  table_lookup: "按原文表/曲线核对",
  review_external: "人工复核外部候选",
  report_gap: "报告证据缺口",
  scope_mismatch: "人工核对条件矛盾",
  review_selected: "按选中的指南复核",
};

function statusTone(status: string): BadgeTone {
  if (status === "possible_match" || status === "match") return "done";
  if (status === "mismatch" || status === "unknown") return "warn";
  return "";
}

function outcomeTone(outcome: string): BadgeTone {
  if (outcome === "resolved") return "done";
  if (outcome === "scope_mismatch" || outcome === "evidence_gap") return "error";
  return "warn";
}

function criteriaSummary(detail: Dict | undefined): string {
  const criteria: Dict[] = detail?.criteria || [];
  if (!criteria.length) return "无适用条件";
  const count = (status: string) => criteria.filter((item) => item.status === status).length;
  return `条件 ${count("match")} 对得上 · ${count("mismatch")} 对不上 · ${count("unknown")} 看不出来`;
}

function roundDetail(round: Dict, result: Dict): string {
  if (round.action === "assess") return criteriaSummary(result.target_detail);
  if (round.action === "request_information") {
    const labels = (round.requests || []).map((item: Dict) => item.label || item.field).filter(Boolean);
    return labels.length ? `需要补：${labels.join("、")}` : "缺关键事实";
  }
  if (round.action === "other_registered") {
    const titles = (result.selected_guideline_details || []).map((item: Dict) => item.title).filter(Boolean);
    return titles.length ? `改用：${titles.join("、")}` : "找到同一类病的另一份指南";
  }
  if (round.action === "external_search") {
    if (round.reason === "search_backend_not_provided") return "未执行：没有外部检索后端";
    const count = (round.new_candidates || []).length;
    return `查询：${round.query || "—"} · 候选 ${count} 个`;
  }
  return "";
}

interface ChainNode {
  key: string;
  index: string;
  label: string;
  state: "done" | "stopped" | "stop";
  badge: string;
  tone: BadgeTone;
  headline: string;
  detail?: string;
}

function buildNodes(result: Dict): ChainNode[] {
  const nodes: ChainNode[] = [];
  const rounds: Dict[] = Array.isArray(result.rounds) ? result.rounds : [];
  const target = result.target_detail;

  rounds.forEach((round, index) => {
    if (round.action === "assess") {
      const topic = String(round.target_topic || "");
      const topicOnly = Boolean(topic) && !target;
      nodes.push({
        key: `round-${index}`,
        index: String(round.round ?? 0),
        label: topicOnly ? "目标主题" : "目标指南",
        state: "done",
        badge: topicOnly
          ? "本地无对应指南"
          : TARGET_STATUS_LABEL[round.target_status] || round.target_status || "未评估",
        tone: topicOnly ? "warn" : statusTone(round.target_status),
        headline: topicOnly
          ? `主题：${TOPIC_LABEL[topic] || topic}`
          : target?.title || result.target?.title || result.target?.guideline_id || "未指定目标指南",
        detail: topicOnly ? "本地四份指南没有对应主题" : criteriaSummary(target),
      });
      return;
    }
    if (round.action === "request_information") {
      nodes.push({
        key: `round-${index}`,
        index: String(round.round ?? index),
        label: "补事实",
        state: "stopped",
        badge: "停在这里",
        tone: "warn",
        headline: "缺关键事实，先补事实再继续",
        detail: roundDetail(round, result),
      });
      return;
    }
    if (round.action === "other_registered") {
      nodes.push({
        key: `round-${index}`,
        index: String(round.round ?? index),
        label: "同一类病指南",
        state: "stopped",
        badge: "停在这里",
        tone: "done",
        headline: "找到同一类病的另一份指南",
        detail: roundDetail(round, result),
      });
      return;
    }
    if (round.action === "external_search") {
      const noBackend = round.reason === "search_backend_not_provided";
      nodes.push({
        key: `round-${index}`,
        index: String(round.round ?? index),
        label: "外部检索",
        state: round.stop_after ? "stopped" : "done",
        badge: round.stop_after ? "停在这里" : "完成",
        tone: noBackend ? "warn" : "done",
        headline: noBackend ? "没有可用的外部检索后端" : "已执行外部检索",
        detail: roundDetail(round, result),
      });
    }
  });

  if (result.lookup?.required) {
    const fields = (result.lookup.fields || []).join("、") || "—";
    const locators = (result.lookup.locators || []).join("、") || "—";
    nodes.push({
      key: "lookup",
      index: "1",
      label: "查表",
      state: "stopped",
      badge: "停在这里",
      tone: "warn",
      headline: "需要查指南里的表/曲线",
      detail: `字段：${fields} · 位置：${locators}`,
    });
  }

  const outcome = String(result.outcome || "");
  const stopReason = String(result.stop_reason || "");
  const nextAction = String(result.next_action?.type || "");
  nodes.push({
    key: "stop",
    index: "停",
    label: "停止",
    state: "stop",
    badge: OUTCOME_LABEL[outcome] || outcome || "未知",
    tone: outcomeTone(outcome),
    headline: STOP_REASON_LABEL[stopReason] || stopReason || "流程停止",
    detail: NEXT_ACTION_LABEL[nextAction] ? `下一步：${NEXT_ACTION_LABEL[nextAction]}` : "",
  });
  return nodes;
}

export function ApplicabilityChain({ result }: { result: Dict }) {
  const nodes = buildNodes(result);
  return (
    <div className="applicability-chain" aria-label="指南适用性链路">
      {nodes.map((node, index) => (
        <div className={`chain-step ${node.state}`} key={node.key}>
          <div className="chain-rail">
            <span className="chain-dot">{node.index}</span>
            {index < nodes.length - 1 ? <span className="chain-line" /> : null}
          </div>
          <div className="chain-card">
            <div className="chain-head">
              <b>{node.label}</b>
              <Badge tone={node.tone}>{node.badge}</Badge>
            </div>
            <div className="chain-headline">{node.headline}</div>
            {node.detail ? <div className="chain-detail">{node.detail}</div> : null}
          </div>
        </div>
      ))}
    </div>
  );
}
