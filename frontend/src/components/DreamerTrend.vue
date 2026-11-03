<script setup>
import {computed,ref} from 'vue'
import {metricSeries,metricGeometry,hours} from '../dreamer-utils'
const props=defineProps({reports:{type:Array,default:()=>[]}})
const metric=ref('crp')
const metrics={spo2:['血氧饱和度','%'],respiratory_rate:['呼吸频率','次/min'],heart_rate:['心率','bpm'],temperature:['体温','°C'],crp:['CRP','mg/L'],ph:['血气pH',''],paco2:['PaCO₂','mmHg'],wbc:['白细胞','10^9/L'],pct:['PCT','ng/mL'],sao2:['血气sO₂','%'],feeding:['喂养耐受','fraction'],bilirubin:['总胆红素','µmol/L']}
const geometry=computed(()=>metricGeometry(metricSeries(props.reports,metric.value)))
</script>
<template>
  <section class="ds-card ds-trend"><div class="ds-panel-head"><div><h2>已发布的患者观测</h2><p>按实际模拟时间绘制，待报检查不会提前出现在曲线中。</p></div><label class="ds-inline-field">指标<select v-model="metric"><option v-for="(m,k) in metrics" :key="k" :value="k">{{ m[0] }}</option></select></label></div>
    <svg viewBox="0 0 760 205" role="img" :aria-label="`${metrics[metric][0]}随模拟时间的变化`"><g v-for="y in [35,75,115,155]" :key="y"><line x1="48" x2="708" :y1="y" :y2="y" stroke="#e6edee" stroke-dasharray="3 5"/></g><text v-if="geometry.hi!=null" x="5" y="37" fill="#7b8e96" font-size="11">{{ geometry.hi.toFixed(1) }}</text><text v-if="geometry.lo!=null" x="5" y="158" fill="#7b8e96" font-size="11">{{ geometry.lo.toFixed(1) }}</text><path v-if="geometry.points.length" :d="geometry.path" fill="none" stroke="#14857e" stroke-width="2.5"/><g v-for="(p,i) in geometry.points" :key="i"><circle :cx="p.x" :cy="p.y" r="4" fill="white" stroke="#14857e" stroke-width="2"/><title>{{ hours(p.time) }} · {{ p.value }} {{ metrics[metric][1] }}</title></g><text v-for="(t,i) in geometry.ticks" :key="i" :x="t.x" y="190" text-anchor="middle" fill="#7b8e96" font-size="11">{{ hours(t.time) }}</text><text v-if="!geometry.points.length" x="380" y="95" text-anchor="middle" fill="#82989d" font-size="13">等待该项检查报告发布</text></svg>
  </section>
</template>
