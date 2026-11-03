import test from 'node:test'
import assert from 'node:assert/strict'
import { chartGeometry, numericSeries, formatTime } from '../src/utils.js'

test('trajectory chart uses the real step position and preserves missing observations', () => {
  const steps = [
    { timestamp: '2026-01-01T08:00:00', events: [{ action: { concept: 'web:glucose', value_type: 'number' }, value: 8 }] },
    { timestamp: '2026-01-01T14:00:00', events: [] },
    { timestamp: '2026-01-01T20:00:00', events: [{ action: { concept: 'web:glucose', value_type: 'number' }, value: 6 }] },
  ]
  const series = numericSeries(steps, 'web:glucose')
  assert.deepEqual(series.map(p => p.value), [8, null, 6])
  const geometry = chartGeometry(series)
  assert.equal(geometry.points.length, 2)
  assert.equal(geometry.points[1].step, 3)
  assert.equal(geometry.points[1].x, 48 + 2 / 9 * 700)
  assert.ok(geometry.points.every(p => Number.isFinite(p.y)))
})

test('empty and constant-value charts never produce invalid coordinates', () => {
  assert.equal(chartGeometry([]).path, '')
  const geometry = chartGeometry(Array.from({ length: 10 }, (_, i) => ({ step: i + 1, value: 0 })))
  assert.equal(geometry.points.length, 10)
  assert.equal(geometry.points[9].x, 748)
  assert.ok(!geometry.path.includes('NaN'))
  assert.ok(geometry.ticks.every(t => Number.isFinite(t.value)))
})

test('unknown values are never displayed as dates', () => {
  assert.equal(formatTime(null), '—')
  assert.equal(formatTime('invalid'), '—')
  assert.equal(formatTime('2026-01-02T08:05:00', true), '01.02 08:05')
})

test('variable length trajectories use the full axis including single-step records', () => {
  for (const n of [1,2,3,5,6,10]) {
    const geometry = chartGeometry(Array.from({length:n},(_,i)=>({step:i+1,value:90+i})), n)
    assert.equal(geometry.points[0].x, n===1 ? 398 : 48)
    assert.equal(geometry.points.at(-1).x, n===1 ? 398 : 748)
    assert.ok(geometry.points.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)))
    assert.ok(!/NaN|Infinity/.test(geometry.path))
  }
})
