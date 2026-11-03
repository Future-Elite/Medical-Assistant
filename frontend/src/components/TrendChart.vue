<script setup>
import { computed, ref } from 'vue'
import { chartGeometry, formatTime, numericSeries } from '../utils'
const props = defineProps({ steps: { type: Array, default: () => [] }, measurement: Object, selected: Number, totalSteps: {type:Number,default:10} })
const emit = defineEmits(['select'])
const hovered = ref(null)
const geometry = computed(() => chartGeometry(numericSeries(props.steps, `web:${props.measurement?.key}`), props.totalSteps))
const active = computed(() => geometry.value.points.find(p => p.step === (hovered.value ?? props.selected + 1)))
</script>
<template>
  <div class="trend-chart" :class="{ 'is-empty': !steps.length }">
    <svg viewBox="0 0 780 218" role="img" :aria-label="`${measurement?.name || '指标'}的 ${totalSteps} 步变化趋势`">
      <defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#0e8a83" stop-opacity=".13"/><stop offset="100%" stop-color="#0e8a83" stop-opacity="0"/></linearGradient></defs>
      <g v-for="(y, i) in [25,72,119,165]" :key="y">
        <line x1="48" :y1="y" x2="748" :y2="y" stroke="#e8edee" stroke-dasharray="4 5" />
        <text x="32" :y="y + 4" text-anchor="end" fill="#94a1a6" font-size="11">{{ geometry.ticks[i]?.value.toFixed(1) ?? '—' }}</text>
      </g>
      <g v-for="n in totalSteps" :key="n"><text :x="totalSteps === 1 ? 398 : 48 + (n-1) / (totalSteps-1) * 700" y="202" text-anchor="middle" fill="#94a1a6" font-size="11">T{{ String(n).padStart(2, '0') }}</text></g>
      <template v-if="geometry.points.length">
        <path :d="geometry.area" fill="url(#area)" />
        <path :d="geometry.path" fill="none" stroke="#0e8a83" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
        <line v-if="active" :x1="active.x" :x2="active.x" y1="20" y2="175" stroke="#0e8a83" stroke-dasharray="3 4" opacity=".35" />
        <g v-for="point in geometry.points" :key="point.step" @mouseenter="hovered = point.step" @mouseleave="hovered = null" @click="emit('select', point.step - 1)" class="chart-point">
          <circle :cx="point.x" :cy="point.y" r="15" fill="transparent" />
          <circle :cx="point.x" :cy="point.y" :r="point.step === selected + 1 ? 5 : 3.5" fill="white" stroke="#0e8a83" stroke-width="2" />
          <title>T{{ point.step }} · {{ formatTime(point.timestamp, true) }} · {{ point.value }} {{ measurement?.unit }}</title>
        </g>
      </template>
      <text v-else x="398" y="100" text-anchor="middle" fill="#a2aeb2" font-size="13">生成后，查看指标随时间的变化</text>
    </svg>
    <div v-if="active" class="chart-readout">T{{ String(active.step).padStart(2, '0') }} <strong>{{ active.value }}</strong> {{ measurement?.unit }}</div>
  </div>
</template>
