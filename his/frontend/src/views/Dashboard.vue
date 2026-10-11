<template>
  <div class="dashboard">
    <div class="page-head">
      <h2 class="page-title">数据看板</h2>
      <el-button
        type="primary"
        :loading="patientLoading"
        @click="drawPatient"
      >
        随机抽取患者
      </el-button>
    </div>

    <el-alert
      v-if="patientError"
      class="block-alert"
      type="error"
      show-icon
      :closable="false"
      :title="patientError"
    />

    <!-- 随机患者 + 诊疗时间轴 -->
    <el-card v-loading="patientLoading" shadow="never" class="panel">
      <template #header>
        <span class="card-title">随机患者诊疗时间轴</span>
        <span v-if="patient" class="sub-title">
          {{ patient.name || '未命名' }}（ID: {{ patient.id }}）
          <span class="muted">
            · {{ genderText(patient.gender) }} · {{ ageText(patient.birthDate) }}岁
          </span>
        </span>
        <span v-if="events.length" class="sub-title muted">共 {{ events.length }} 条记录</span>
      </template>

      <Timeline v-if="events.length" :events="events" />
      <el-empty
        v-else-if="patient"
        description="该患者暂无诊疗记录"
      />
      <el-empty
        v-else-if="!patientLoading"
        description="点击「随机抽取患者」查看其诊疗时间轴"
      />
    </el-card>

    <el-row :gutter="16" class="metric-row">
      <el-col :xs="24" :md="8">
        <el-card shadow="never">
          <div class="metric">
            <div class="metric-value">{{ pct(admission?.ratio) }}</div>
            <div class="metric-label">住院占比</div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="never">
          <div class="metric">
            <div class="metric-value">{{ pct(rational?.rationalRate) }}</div>
            <div class="metric-label">合理用药率</div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="never">
          <div class="metric">
            <div class="metric-value">{{ pct(pathway?.executionRate) }}</div>
            <div class="metric-label">路径执行率</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="charts-row">
      <!-- 就诊频率：趋势，单序列折线 -->
      <el-col :xs="24" :lg="12">
        <el-card v-loading="freqLoading" shadow="never" class="panel">
          <template #header>
            <span class="card-title">就诊频率（按月）</span>
          </template>
          <el-alert
            v-if="freqError"
            type="error"
            show-icon
            :closable="false"
            :title="freqError"
          />
          <Chart v-if="freqRows.length" :option="freqOption" />
          <el-empty v-else-if="!freqLoading && !freqError" description="暂无就诊数据" />
        </el-card>
      </el-col>

      <!-- 用药分布：类目量值比较，单序列横向柱状 -->
      <el-col :xs="24" :lg="12">
        <el-card v-loading="drugLoading" shadow="never" class="panel">
          <template #header>
            <span class="card-title">用药分布</span>
          </template>
          <el-alert
            v-if="drugError"
            type="error"
            show-icon
            :closable="false"
            :title="drugError"
          />
          <Chart v-if="drugRows.length" :option="drugOption" :height="drugHeight" />
          <el-empty v-else-if="!drugLoading && !drugError" description="暂无用药数据" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  admissionRatio,
  drugDistribution,
  pathwayRate,
  randomPatient,
  rationalDrugRate,
  visitFrequency
} from '../api/dashboard'
import { getTimeline } from '../api/patient'
import Timeline from '../components/Timeline.vue'
import Chart from '../components/Chart.vue'

/* ---------- 图表配色 ----------
   单序列图表不需要图例，用「一个序列一个颜色」即可。
   槽位 1（蓝）给就诊频率，槽位 2（橙）给用药分布 —— 两个顺序型图表同屏时
   第二个取下一槽位色。两色已对白色卡片背景校验通过（对比度 >= 3:1）。 */
const SERIES_BLUE = '#2a78d6'
const SERIES_ORANGE = '#eb6834'
const INK_SECONDARY = '#52514e'
const INK_MUTED = '#898781'
const GRID_LINE = '#e1e0d9'
const AXIS_LINE = '#c3c2b7'
const SURFACE = '#ffffff'

/* ---------- 随机患者 ---------- */
const patient = ref(null)
const events = ref([])
const patientLoading = ref(false)
const patientError = ref('')

const genderText = (gender) => (gender === 0 ? '男' : gender === 1 ? '女' : '-')

const ageText = (birthDate) => {
  if (!birthDate) return '-'
  // 用 / 分隔避免 UTC 解析导致的日期偏移
  const birth = new Date(String(birthDate).replace(/-/g, '/'))
  if (Number.isNaN(birth.getTime())) return '-'
  const today = new Date()
  let age = today.getFullYear() - birth.getFullYear()
  const monthDiff = today.getMonth() - birth.getMonth()
  if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birth.getDate())) age -= 1
  return age >= 0 ? String(age) : '-'
}

const drawPatient = async () => {
  patientLoading.value = true
  patientError.value = ''
  // 先清空上一次的结果，避免新患者加载中仍显示旧时间轴
  patient.value = null
  events.value = []

  const patientRes = await randomPatient().catch(() => null)
  if (!patientRes?.data) {
    patientError.value = '随机抽取患者失败'
    patientLoading.value = false
    return
  }
  patient.value = patientRes.data

  const timelineRes = await getTimeline(patientRes.data.id).catch(() => null)
  events.value = timelineRes?.data?.events || []
  if (!timelineRes) patientError.value = '该患者诊疗时间轴加载失败'
  patientLoading.value = false
}

/* ---------- 就诊频率 ---------- */
const freqRows = ref([])
const freqLoading = ref(false)
const freqError = ref('')

const freqOption = computed(() => {
  const rows = freqRows.value
  const counts = rows.map((r) => r.count)
  return {
    grid: { left: 8, right: 24, top: 32, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      // 十字准线帮读者对准某个月，而不是去命中的 2px 折线
      axisPointer: { type: 'line', lineStyle: { color: AXIS_LINE, width: 1 } },
      valueFormatter: (value) => `${value} 次`
    },
    xAxis: {
      type: 'category',
      data: rows.map((r) => r.date),
      boundaryGap: false,
      axisLine: { lineStyle: { color: AXIS_LINE } },
      axisTick: { show: false },
      axisLabel: { color: INK_MUTED, fontSize: 12 }
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: GRID_LINE, type: 'solid', width: 1 } },
      axisLabel: { color: INK_MUTED, fontSize: 12 }
    },
    series: [
      {
        type: 'line',
        name: '就诊次数',
        data: counts,
        symbol: 'circle',
        symbolSize: 8,
        lineStyle: { width: 2, color: SERIES_BLUE, cap: 'round', join: 'round' },
        // 2px 表面色描边，让数据点压在线上时依然清晰
        itemStyle: { color: SERIES_BLUE, borderColor: SURFACE, borderWidth: 2 },
        areaStyle: { color: 'rgba(42, 120, 214, 0.10)' },
        emphasis: { focus: 'series' },
        // 只直接标注峰值，其余交给坐标轴与 tooltip（不给每个点都标数字）
        markPoint: {
          symbol: 'circle',
          symbolSize: 1,
          itemStyle: { color: 'transparent' },
          label: {
            show: true,
            position: 'top',
            color: INK_SECONDARY,
            fontSize: 12,
            formatter: (p) => String(p.value)
          },
          data: [{ type: 'max', name: '峰值' }]
        }
      }
    ]
  }
})

/* ---------- 用药分布 ---------- */
const drugRows = ref([])
const drugLoading = ref(false)
const drugError = ref('')

// 最多 20 个类目，高度随数据增长，避免类目标签被挤在一起或出现内部滚动条
const drugHeight = computed(() => `${Math.max(320, drugRows.value.length * 28 + 72)}px`)

const drugOption = computed(() => {
  const rows = drugRows.value
  return {
    grid: { left: 8, right: 56, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      // 柱状图以柱子本身为命中目标，不用十字准线
      trigger: 'item',
      formatter: (p) => `${p.name}<br/>${p.value} 次`
    },
    xAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: GRID_LINE, type: 'solid', width: 1 } },
      axisLabel: { color: INK_MUTED, fontSize: 12 }
    },
    yAxis: {
      type: 'category',
      data: rows.map((r) => r.drugName),
      // 后端已按 count 降序返回，inverse 让第一名显示在最上方
      inverse: true,
      axisLine: { lineStyle: { color: AXIS_LINE } },
      axisTick: { show: false },
      axisLabel: { color: INK_SECONDARY, fontSize: 12, width: 160, overflow: 'truncate' }
    },
    series: [
      {
        type: 'bar',
        name: '处方次数',
        data: rows.map((r) => r.count),
        barMaxWidth: 24,
        // 数据端 4px 圆角，基线端保持方角
        itemStyle: { color: SERIES_ORANGE, borderRadius: [0, 4, 4, 0] },
        // 数值标在柱端外侧（不塞进柱内，避免被截断），悬停时柱子变浅提示响应
        label: { show: true, position: 'right', color: INK_SECONDARY, fontSize: 12 },
        emphasis: { itemStyle: { opacity: 0.85 } }
      }
    ]
  }
})

/* ---------- 汇总指标（住院占比 / 合理用药率 / 路径执行率） ---------- */
const admission = ref(null)
const rational = ref(null)
const pathway = ref(null)

// 后端三个接口都返回 0~1 的比例；缺失时显示 '-' 而不是误导性的 0.0%
const pct = (v) => (v == null ? '-' : (Number(v) * 100).toFixed(1) + '%')

/* ---------- 加载 ---------- */
const loadStats = async () => {
  freqLoading.value = true
  drugLoading.value = true
  freqError.value = ''
  drugError.value = ''

  const [freqRes, drugRes, admissionRes, rationalRes, pathwayRes] = await Promise.all([
    visitFrequency().catch(() => null),
    drugDistribution().catch(() => null),
    admissionRatio().catch(() => null),
    rationalDrugRate().catch(() => null),
    pathwayRate().catch(() => null)
  ])

  freqRows.value = Array.isArray(freqRes?.data) ? freqRes.data : []
  drugRows.value = Array.isArray(drugRes?.data) ? drugRes.data : []
  if (!freqRes) freqError.value = '就诊频率统计加载失败'
  if (!drugRes) drugError.value = '用药分布统计加载失败'

  // 三个指标卡没有独立的错误位：任一接口失败都只让对应卡片显示 '-'，不阻断其余内容
  admission.value = admissionRes?.data ?? null
  rational.value = rationalRes?.data ?? null
  pathway.value = pathwayRes?.data ?? null

  freqLoading.value = false
  drugLoading.value = false
}

onMounted(loadStats)
</script>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 12px;
}

.page-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}

.block-alert {
  margin-bottom: 12px;
}

.panel {
  margin-bottom: 16px;
}

.charts-row .panel {
  margin-bottom: 0;
}

.card-title {
  font-weight: 600;
  color: #303133;
}

.sub-title {
  margin-left: 12px;
  font-size: 13px;
  color: #303133;
}

.muted {
  color: #909399;
}

/* 汇总指标卡：与校验报告页（ValidationReport.vue）保持同一套样式 */
.metric-row {
  margin-bottom: 16px;
}

.metric {
  text-align: center;
  padding: 8px 0;
}

.metric-value {
  font-size: 30px;
  font-weight: 700;
  color: #409EFF;
}

.metric-label {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}
</style>
