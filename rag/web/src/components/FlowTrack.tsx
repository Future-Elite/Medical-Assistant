import { useWorkbench } from "../state/workbench";
import {
  CORE_STAGES,
  STEP_META,
  coreStageForStep,
  coreStageIndex,
  stepNumber,
} from "../lib/steps";
import { Icon } from "./Icon";

/**
 * 核心功能链路：6 个阶段，而不是 10 个技术步骤并排。
 * 当前阶段高亮，已走过阶段打勾；技术步骤收在本阶段下方。
 */
export function FlowTrack() {
  const { step, goStep, stepStatus } = useWorkbench();
  const stage = coreStageForStep(step);
  const current = coreStageIndex(step);

  return (
    <nav className="core-flow" aria-label="核心检索链路">
      <div className="core-flow-head">
        <span className="core-flow-title">核心链路</span>
        <span className="core-flow-hint">{CORE_STAGES.map((item) => item.title).join(" → ")}</span>
      </div>
      <div className="core-stages">
        {CORE_STAGES.map((item, index) => {
          const active = item.key === stage.key;
          const done = index < current;
          const status = stepStatus[item.primary];
          return (
            <div className="core-stage-wrap" key={item.key}>
              <button
                type="button"
                className={`core-stage ${active ? "active" : ""} ${done ? "done" : ""}`.trim()}
                aria-current={active ? "step" : undefined}
                title={`${String(index + 1).padStart(2, "0")} ${item.title} · ${status.text}`}
                onClick={() => goStep(item.primary)}
              >
                <span className="core-stage-top">
                  <span className="core-stage-num">{String(index + 1).padStart(2, "0")}</span>
                  <span className="core-stage-icon"><Icon name={item.icon} size={14} /></span>
                  {done ? <span className="core-stage-check"><Icon name="check" size={11} /></span> : null}
                </span>
                <b>{item.title}</b>
                <small>{item.description}</small>
              </button>
              {index < CORE_STAGES.length - 1 ? (
                <span className="core-stage-arrow" aria-hidden="true">
                  <Icon name="chevronRight" size={12} />
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
      {stage.steps.length > 1 ? <div className="core-substeps" aria-label="当前阶段技术步骤">
        <span className="core-substeps-label">本环节</span>
        {stage.steps.map((key) => {
          const meta = STEP_META[key];
          const active = key === step;
          return (
            <button
              key={key}
              type="button"
              className={`core-substep ${active ? "active" : ""}`}
              onClick={() => goStep(key)}
            >
              <span className="core-substep-num">{stepNumber(key)}</span>
              {meta.short}
              <span className={`core-substep-dot ${stepStatus[key].tone}`} />
            </button>
          );
        })}
      </div> : null}
    </nav>
  );
}
