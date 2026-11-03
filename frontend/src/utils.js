export const statusLabels = { queued: '等待生成', running: '正在生成', completed: '生成完成', failed: '生成失败' }

export function formatTime(value, withDate = false) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = x => String(x).padStart(2, '0')
  return `${withDate ? `${pad(date.getMonth() + 1)}.${pad(date.getDate())} ` : ''}${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function numericSeries(steps, concept) {
  return steps.map((step, index) => {
    const event = step.events.find(e => e.action.concept === concept && e.action.value_type === 'number')
    return { step: index + 1, value: event?.value ?? null, timestamp: step.timestamp }
  })
}

export function chartGeometry(series, totalSteps = 10) {
  const values = series.map(p => p.value).filter(v => typeof v === 'number' && Number.isFinite(v))
  if (!values.length) return { points: [], ticks: [], path: '', area: '' }
  const min = Math.min(...values), max = Math.max(...values)
  const pad = Math.max((max - min) * 0.22, Math.abs(max) * 0.03, .3)
  const lo = min - pad, hi = max + pad
  const points = series.filter(p => typeof p.value === 'number' && Number.isFinite(p.value)).map(p => ({
    ...p, x: totalSteps === 1 ? 398 : 48 + (p.step - 1) / Math.max(totalSteps - 1, 1) * 700,
    y: 165 - (p.value - lo) / (hi - lo) * 140,
  }))
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${p.x},${p.y}`).join(' ')
  const area = points.length ? `${path} L${points.at(-1).x},175 L${points[0].x},175 Z` : ''
  return { points, path, area, ticks: Array.from({ length: 4 }, (_, i) => ({ y: 25 + i * 140 / 3, value: hi - i * (hi - lo) / 3 })) }
}
