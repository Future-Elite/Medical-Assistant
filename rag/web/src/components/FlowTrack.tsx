import { STEP_ORDER, useWorkbench } from "../state/workbench";
import { STEP_META, stepNumber } from "../lib/steps";
import { Icon } from "./Icon";

/**
 * 顶部流程轨道：10 个真实步骤的进度概览。
 * 已走过、当前、未到达三种状态由位置决定；点击可直接跳转。
 */
export function FlowTrack() {
  const { step, goStep, stepStatus } = useWorkbench();
  const current = STEP_ORDER.indexOf(step);
  const progress = current <= 0 ? 0 : (current / (STEP_ORDER.length - 1)) * 100;

  return (
    <nav className="flow-track" aria-label="检索流程进度">
      <div className="flow-track-line" aria-hidden="true">
        <span className="flow-track-fill" style={{ width: `${progress}%` }} />
      </div>
      {STEP_ORDER.map((key, index) => {
        const meta = STEP_META[key];
        const done = index < current;
        const active = index === current;
        const status = stepStatus[key];
        return (
          <button
            key={key}
            type="button"
            className={`flow-node ${done ? "done" : ""} ${active ? "active" : ""}`.trim()}
            aria-current={active ? "step" : undefined}
            title={`${stepNumber(key)} ${meta.label} · ${status.text}`}
            onClick={() => goStep(key)}
          >
            <span className="flow-dot">
              {done ? <Icon name="check" size={11} /> : stepNumber(key)}
            </span>
            <span className="flow-name">{meta.short}</span>
          </button>
        );
      })}
    </nav>
  );
}
