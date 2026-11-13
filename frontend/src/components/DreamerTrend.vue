<script setup>
import {computed,ref,watch} from 'vue'
import {metricSeries,metricGeometry,hours} from '../dreamer-utils'
const props=defineProps({reports:{type:Array,default:()=>[]},metricKey:String})
const metric=ref('crp')
const metrics=computed(()=>Object.fromEntries(props.reports.flatMap(r=>r.items.map(i=>[i.key,[i.name||i.key,i.unit||'']]))))
const available=computed(()=>Object.keys(metrics.value))
watch(available,keys=>{if(!keys.includes(metric.value)||!metricSeries(props.reports,metric.value).length)metric.value=keys.find(k=>metricSeries(props.reports,k).length)||keys[0]||'crp'},{immediate:true})
const activeMetric=computed(()=>props.metricKey&&metrics.value[props.metricKey]?props.metricKey:metric.value)
const activeMeta=computed(()=>metrics.value[activeMetric.value]||['指标',''])
const geometry=computed(()=>metricGeometry(metricSeries(props.reports,activeMetric.value)))
</script>
<template>
  <section class="ds-card ds-trend"><div class="ds-panel-head"><div><h2>已发布的患者观测</h2><p>按采样时间绘制，仅显示已发布报告。</p></div><label class="ds-inline-field">指标<select v-model="metric"><option v-for="k in available" :key="k" :value="k">{{ metrics[k][0] }}</option></select></label></div>
    <svg viewBox="0 0 760 205" role="img" :aria-label="`${activeMeta[0]}随模拟时间的变化`"><g v-for="y in [35,75,115,155]" :key="y"><line x1="48" x2="708" :y1="y" :y2="y" stroke="#e6edee" stroke-dasharray="3 5"/></g><text v-if="geometry.hi!=null" x="5" y="37" fill="#7b8e96" font-size="11">{{ geometry.hi.toFixed(1) }}</text><text v-if="geometry.lo!=null" x="5" y="158" fill="#7b8e96" font-size="11">{{ geometry.lo.toFixed(1) }}</text><path v-if="geometry.points.length" :d="geometry.path" fill="none" stroke="#14857e" stroke-width="2.5"/><g v-for="(p,i) in geometry.points" :key="i"><circle :cx="p.x" :cy="p.y" r="4" fill="white" stroke="#14857e" stroke-width="2"/><title>{{ hours(p.time) }} · {{ p.value }} {{ activeMeta[1] }}</title></g><text v-for="(t,i) in geometry.ticks" :key="i" :x="t.x" y="190" text-anchor="middle" fill="#7b8e96" font-size="11">{{ hours(t.time) }}</text><text v-if="!geometry.points.length" x="380" y="95" text-anchor="middle" fill="#82989d" font-size="13">{{ reports.some(r=>r.items.some(i=>i.key===activeMetric)) ? '已有非数值或检出限结果，无可绘制数值' : '等待该项检查报告发布' }}</text></svg>
  </section>
</template>
