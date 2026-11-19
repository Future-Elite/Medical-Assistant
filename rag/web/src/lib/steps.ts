import { STEP_ORDER } from "../state/workbench";
import type { StepKey } from "../types";

export interface StepMeta {
  label: string;
  short: string;
  icon: string;
}

export const STEP_META: Record<StepKey, StepMeta> = {
  case: { label: "患者快照", short: "快照", icon: "stethoscope" },
  question: { label: "临床问题", short: "问题", icon: "info" },
  terms: { label: "术语映射", short: "术语", icon: "book" },
  scope: { label: "来源范围", short: "范围", icon: "grid" },
  method: { label: "检索方法", short: "方法", icon: "cpu" },
  request: { label: "请求编排", short: "编排", icon: "layers" },
  guidelines: { label: "指南与适用性", short: "指南", icon: "file" },
  run: { label: "执行检索", short: "检索", icon: "play" },
  results: { label: "证据包", short: "证据", icon: "list" },
  audit: { label: "引用审计", short: "审计", icon: "shield" },
};

export interface CoreStageMeta {
  key: string;
  title: string;
  description: string;
  icon: string;
  steps: StepKey[];
  primary: StepKey;
}

/**
 * 核心功能链路：把 10 个技术步骤收束为 6 个可讲清楚的阶段。
 * 顺序对应架构图：患者快照 → 检索编排 → 指南适配 → 适用性判断 → 证据包 → 审计。
 */
export const CORE_STAGES: CoreStageMeta[] = [
  {
    key: "snapshot",
    title: "患者快照",
    description: "HIS → Agent · as_of 可见",
    icon: "stethoscope",
    steps: ["case"],
    primary: "case",
  },
  {
    key: "plan",
    title: "检索编排",
    description: "问题 · 术语 · 来源 · 预算",
    icon: "grid",
    steps: ["question", "terms", "scope", "method", "request"],
    primary: "question",
  },
  {
    key: "retrieval",
    title: "执行检索",
    description: "来源 · 迭代 · 预算",
    icon: "activity",
    steps: ["run"],
    primary: "run",
  },
  {
    key: "guideline",
    title: "指南与适用性",
    description: "Top-k · 建图 · 双向 · 条件",
    icon: "file",
    steps: ["guidelines"],
    primary: "guidelines",
  },
  {
    key: "evidence",
    title: "证据包",
    description: "命中 · 引用 · 缺口 · 边界",
    icon: "list",
    steps: ["results"],
    primary: "results",
  },
  {
    key: "audit",
    title: "审计",
    description: "引用结构 · 主张 · 回放",
    icon: "shield",
    steps: ["audit"],
    primary: "audit",
  },
];

export function coreStageForStep(step: StepKey): CoreStageMeta {
  return CORE_STAGES.find((stage) => stage.steps.includes(step)) || CORE_STAGES[0];
}

export function coreStageIndex(step: StepKey): number {
  return Math.max(0, CORE_STAGES.findIndex((stage) => stage.steps.includes(step)));
}

export function coreStageNumber(step: StepKey): string {
  return String(coreStageIndex(step) + 1).padStart(2, "0");
}

export function stepNumber(step: StepKey): string {
  return String(STEP_ORDER.indexOf(step) + 1).padStart(2, "0");
}

export function stepIndex(step: StepKey): number {
  return STEP_ORDER.indexOf(step);
}
