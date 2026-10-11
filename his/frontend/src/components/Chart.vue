<template>
  <!-- 显式高度是必须的：ECharts 需要有尺寸的容器才能渲染 -->
  <div ref="el" class="chart" :style="{ height }"></div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  option: { type: Object, default: () => ({}) },
  // 横向长图（如用药分布）可传入更大的高度，避免类目过多时标签互相重叠
  height: { type: String, default: '320px' }
})

const el = ref(null)
let chart = null

const render = () => {
  if (!chart || !props.option) return
  // notMerge：切换数据集（如重新抽患者）时丢弃旧配置，避免残留上一次的系列
  chart.setOption(props.option, true)
}

const handleResize = () => chart?.resize()

onMounted(() => {
  chart = echarts.init(el.value)
  render()
  window.addEventListener('resize', handleResize)
})

watch(() => props.option, render, { deep: true })

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.chart {
  width: 100%;
}
</style>
