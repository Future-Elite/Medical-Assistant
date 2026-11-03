<script setup>
import { computed } from 'vue'
import Icon from './Icon.vue'
import { formatTime } from '../utils'
const props = defineProps({ record: Object, events: { type: Array, default: () => [] } })
const groups = computed(() => ['intervention', 'inquiry'].map(kind => ({ kind, orders: props.record?.orders?.filter(o => o.kind === kind) || [] })))
const labels = { pending: '待报告', negative: '当前报告阴性', positive: '当前报告阳性', unknown: '证据未知', absent: '未见', suspected: '仍疑似', confirmed: '已有证据', present: '存在', room_air: '空气下', oxygen: '氧疗', cpap: 'CPAP', nippv: 'NIPPV', invasive: '有创通气' }
function outcome(order) {
  const event = props.events.find(e => e.action.action_id === order.order_id)
  if (!event) return '尚无结果'
  if (order.kind === 'intervention') return '无即时观测值'
  if (event.action.concept === 'web:summary') return '见下方病程记录'
  return `${labels[event.value] || event.value} ${event.action.unit || ''}`.trim()
}
</script>
<template>
  <section v-if="record" class="physician-record">
    <div class="physician-heading"><Icon name="user" :size="17" /><strong>模拟医生 · 检查与干预医嘱</strong><span>{{ record.orders.length }} 项</span></div>
    <p class="decision-basis">{{ record.decision_basis }}<span v-if="record.observation_cutoff"> · 截至 {{ formatTime(record.observation_cutoff, true) }}</span><span v-else> · 使用初始已知状态</span></p>
    <p class="physician-assessment">{{ record.assessment }}</p>
    <p v-for="warning in record.warnings || []" :key="warning" class="physician-warning"><Icon name="warning" :size="13" />{{ warning }}</p>
    <div v-for="group in groups" :key="group.kind" class="physician-group">
      <template v-if="group.orders.length">
        <h4>{{ group.kind === 'intervention' ? '医生干预动作' : '医生检查 / 监测动作' }} <small>{{ group.orders.length }}</small></h4>
        <div v-for="order in group.orders" :key="order.order_id" class="physician-order" :class="group.kind">
          <div class="order-title"><span class="order-type">{{ group.kind === 'intervention' ? '干预' : '检查' }}</span><strong>{{ order.name }}</strong><span class="order-status">{{ order.status === 'planned' ? '计划' : '模拟已执行' }}</span></div>
          <div class="order-result">{{ group.kind === 'intervention' ? '记录' : '结果' }}：{{ outcome(order) }}</div>
          <details><summary>动作原因与指南依据</summary><p>{{ order.rationale }}</p><p v-if="order.parameters.dose_status || order.parameters.regimen_status">{{ order.parameters.dose_status || order.parameters.regimen_status }}</p><p v-if="order.parameters.spo2_target">SpO₂ 目标：{{ Array.isArray(order.parameters.spo2_target) ? `${order.parameters.spo2_target.join('–')}%` : order.parameters.spo2_target }}</p><a v-for="ref in order.guideline_refs" :key="ref.rule_id" :href="ref.url" target="_blank" rel="noreferrer">{{ ref.rule_id }} · 第 {{ ref.printed_page }} 页 <Icon name="chevron" :size="10" /></a></details>
        </div>
      </template>
    </div>
    <p class="physician-footnote">{{ record.note }}</p>
  </section>
</template>
