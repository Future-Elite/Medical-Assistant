from __future__ import annotations

import html
import json
from pathlib import Path

from .io import output_path


def sparkline(series: list[tuple[str, list[float | None]]], *, width=760, height=200) -> str:
    values = [v for _, ys in series for v in ys if v is not None]
    if not values:
        return "<p>No numeric observations.</p>"
    lo, hi = min(values), max(values)
    hi = hi if hi > lo else lo + 1
    count = max(len(s) for _, s in series)
    colors = ["#047857", "#2563eb", "#d97706"]
    elements = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Trajectory chart">',
                f'<path d="M50 20 V{height-35} H{width-15}" fill="none" stroke="#94a3b8"/>']
    for index, (label, ys) in enumerate(series):
        points = []
        for i, value in enumerate(ys):
            if value is None:
                continue
            x = 50 + i * (width - 70) / max(count - 1, 1)
            y = height - 35 - (value - lo) / (hi - lo) * (height - 65)
            points.append(f"{x:.1f},{y:.1f}")
            elements.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{colors[index % 3]}"><title>{html.escape(label)}: {value:.4g}</title></circle>')
        elements.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{colors[index % 3]}" stroke-width="2"/>')
        elements.append(f'<text x="{60 + index*200}" y="16" fill="{colors[index % 3]}" font-size="13">{html.escape(label)}</text>')
    elements += [f'<text x="4" y="40" font-size="12">{hi:.3g}</text>',
                 f'<text x="4" y="{height-35}" font-size="12">{lo:.3g}</text>',
                 f'<text x="50" y="{height-8}" font-size="12">Chronological observations</text>', '</svg>']
    return "".join(elements)


def render_report(path: str | Path, full: dict, next_step: dict | None = None, *, episodes=(),
                  full_rollouts=(), next_rollouts=(), retention_result=None, efficiency=None) -> Path:
    def cell(value):
        return "N/A" if value is None else f"{value:.4f}"
    def row(name, report):
        values = [report["numerical"].get(k) for k in ("S@10", "S@15", "S@25", "SMAPE")]
        values += [report["clinical_status"]["abnormal_f1"], report["label"]["f1"], report["average_score"]]
        return f"<tr><th>{name}</th>" + "".join(f"<td>{cell(v)}</td>" for v in values) + "</tr>"
    title = "EHRWorld API reproduction report"
    parts = [f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{title}</title><style>body{{font:16px/1.65 system-ui,sans-serif;background:#f1f5f9;color:#172554;max-width:1050px;margin:30px auto;padding:20px}}section{{background:white;border-radius:12px;padding:22px;margin:18px 0}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:9px;text-align:left;border-bottom:1px solid #e2e8f0}}svg{{width:100%;max-height:260px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}small{{color:#475569}}</style>
<h1>{title}</h1><p>Backend: {html.escape(str(full['backend']))}. Episodes: {full['episode_count']}.</p>
<p>这是当前输入数据的实测结果。合成样例仅验证程序，API 替代版本不代表论文中经过微调的 EHRWorld 权重或其公布成绩。</p>
<section><h2>Prediction fidelity</h2><table><thead><tr><th>Mode</th><th>S@10</th><th>S@15</th><th>S@25</th><th>SMAPE</th><th>Status F1</th><th>Label F1</th><th>Avg Score</th></tr></thead><tbody>''', row("Full trajectory" if full['mode'] == 'full' else "Next step", full)]
    if next_step:
        parts.append(row("Next step", next_step))
    parts.append("</tbody></table><p>Label F1: macro. Status F1: abnormal-positive. N/A indicates an unavailable metric.</p></section>")
    parts.append(f"<section><h2>High-sensitivity events</h2><pre>{html.escape(json.dumps(full['high_sensitivity'], indent=2))}</pre></section>")
    if retention_result:
        parts.append(f"<section><h2>Retention (%)</h2><pre>{html.escape(json.dumps(retention_result, indent=2))}</pre></section>")
    full_map = {r.episode_id: r for r in full_rollouts}
    next_map = {r.episode_id: r for r in next_rollouts}
    for episode in episodes:
        concepts = sorted({e.action.measurement_key for s in episode.steps for e in s.events if e.action.value_type == "number"}, key=str)
        for concept, unit in concepts:
            ground = [(s.timestamp, e) for s in episode.steps for e in s.events if e.action.measurement_key == (concept, unit) and e.action.value_type == "number"]
            series = [("Ground truth", [float(e.value) for _, e in ground])]
            for label, mapping in [("Full", full_map), ("Next", next_map)]:
                pred = mapping.get(episode.episode_id)
                lookup = {(s.timestamp, e.action.action_id): e.value for s in pred.steps for e in s.events} if pred else {}
                series.append((label, [lookup.get((t, e.action.action_id)) for t, e in ground]))
            parts.append(f"<section><h2>{html.escape(episode.episode_id)}: {html.escape(concept)} ({html.escape(unit or 'unitless')})</h2>{sparkline(series)}</section>")
    if efficiency:
        parts.append(f"<section><h2>Measured API efficiency</h2><pre>{html.escape(json.dumps(efficiency, indent=2))}</pre><small>Network and gateway time included. Not comparable to the paper's local 8×H100 SGLang benchmark.</small></section>")
    parts.append(f"<section><h2>Metric policies</h2><pre>{html.escape(json.dumps(full['policies'], ensure_ascii=False, indent=2))}</pre></section></html>")
    target = output_path(path)
    target.write_text("".join(parts), encoding="utf-8")
    return target
