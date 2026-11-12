import { useEffect, useState, type ReactNode } from "react";
import { Icon } from "./Icon";
import type { BadgeTone } from "../types";
import { STEP_ORDER, useWorkbench } from "../state/workbench";

export function Badge({
  tone = "",
  children,
  className = "",
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}) {
  return <span className={`badge ${tone} ${className}`.trim()}>{children}</span>;
}

export function Field({
  label,
  hint,
  children,
  className = "",
}: {
  label?: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`field ${className}`.trim()}>
      {label ? <label>{label}</label> : null}
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
    </div>
  );
}

export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <b>{title}</b>
      {children ? <span>{children}</span> : null}
      {action}
    </div>
  );
}

export function Notice({
  tone = "info",
  icon = "info",
  children,
}: {
  tone?: "info" | "warn" | "error";
  icon?: string;
  children: ReactNode;
}) {
  return (
    <div className={`notice ${tone}`}>
      <Icon name={icon} size={15} />
      <div>{children}</div>
    </div>
  );
}

export function PageHeader({
  kicker,
  title,
  description,
  actions,
}: {
  kicker: string;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  const { step } = useWorkbench();
  const index = String(STEP_ORDER.indexOf(step) + 1).padStart(2, "0");
  const group = String(kicker || "").split("·")[0].trim();

  return (
    <div className="page-head">
      <div className="page-step">
        <span className="num">{index}</span>
        <span className="group">{group}</span>
      </div>
      <div>
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  );
}

export function Tabs({
  items,
  value,
  onChange,
}: {
  items: { key: string; label: string; count?: string | number }[];
  value: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          role="tab"
          aria-selected={value === item.key}
          className={`tab ${value === item.key ? "active" : ""}`}
          onClick={() => onChange(item.key)}
        >
          {item.label}
          {item.count !== undefined ? <span className="tab-count">{item.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

function useCountUp(value: ReactNode): ReactNode {
  const numeric = typeof value === "number" && Number.isFinite(value) ? value : null;
  const [display, setDisplay] = useState(numeric ?? 0);

  useEffect(() => {
    if (numeric === null) return;
    if (numeric === 0) {
      setDisplay(0);
      return;
    }
    let frame = 0;
    const started = performance.now();
    const duration = 480;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - started) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(Math.round(numeric * eased));
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [numeric]);

  return numeric === null ? value : display;
}

export function Metric({
  label,
  value,
  accent = false,
}: {
  label: string;
  value: ReactNode;
  accent?: boolean;
}) {
  const shown = useCountUp(value);
  return (
    <div className={`metric ${accent ? "accent" : ""}`.trim()}>
      <small>{label}</small>
      <strong>{shown}</strong>
    </div>
  );
}

export function CodeBlock({ value, maxHeight }: { value: unknown; maxHeight?: number }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <pre className="code-block" style={maxHeight ? { maxHeight } : undefined}>
      {text}
    </pre>
  );
}

export function StatusRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="status-row">
      <span>{label}</span>
      <span>{value}</span>
    </div>
  );
}
