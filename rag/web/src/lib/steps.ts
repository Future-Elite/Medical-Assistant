import { STEP_ORDER } from "../state/workbench";
import type { StepKey } from "../types";

export interface StepMeta {
  label: string;
  short: string;
  icon: string;
}

export const STEP_META: Record<StepKey, StepMeta> = {
  case: { label: "病例与快照", short: "病例", icon: "stethoscope" },
  question: { label: "问题与意图", short: "问题", icon: "info" },
  terms: { label: "术语与映射", short: "术语", icon: "book" },
  scope: { label: "范围与过滤", short: "范围", icon: "grid" },
  method: { label: "检索方法与门控", short: "方法", icon: "cpu" },
  request: { label: "请求组装", short: "请求", icon: "layers" },
  run: { label: "执行检索", short: "执行", icon: "play" },
  results: { label: "证据结果", short: "结果", icon: "list" },
  guidelines: { label: "指南解析与 HIS 核对", short: "指南", icon: "file" },
  audit: { label: "引用与审计", short: "审计", icon: "shield" },
};

export const NAV_GROUPS: { title: string; steps: StepKey[] }[] = [
  { title: "准备", steps: ["case", "question", "terms", "scope", "method"] },
  { title: "执行", steps: ["request", "run"] },
  { title: "结果", steps: ["results", "guidelines"] },
  { title: "审计", steps: ["audit"] },
];

export function stepNumber(step: StepKey): string {
  return String(STEP_ORDER.indexOf(step) + 1).padStart(2, "0");
}

export function stepIndex(step: StepKey): number {
  return STEP_ORDER.indexOf(step);
}
