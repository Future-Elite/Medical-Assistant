import test from 'node:test'
import assert from 'node:assert/strict'
import {metricSeries,metricGeometry} from '../src/dreamer-utils.js'

test('Dreamer trend follows actual released report times and retains missing values',()=>{
  const reports=[{event_id:'a',time_hours:0,items:[{key:'spo2',value:89}]},{event_id:'b',time_hours:2,items:[{key:'ph',value:7.3}]},{event_id:'c',time_hours:12,items:[{key:'spo2',value:95}]}]
  const series=metricSeries(reports,'spo2')
  assert.deepEqual(series.map(p=>p.time),[0,12])
  const g=metricGeometry(series)
  assert.equal(g.points[0].x,48);assert.equal(g.points[1].x,708)
})

test('Dreamer trend single observation and empty reports have finite coordinates',()=>{
  assert.deepEqual(metricGeometry([]).points,[])
  const g=metricGeometry([{time:0,value:0}])
  assert.equal(g.points[0].x,380)
  assert.ok(Number.isFinite(g.points[0].y));assert.ok(!/NaN|Infinity/.test(g.path))
})
