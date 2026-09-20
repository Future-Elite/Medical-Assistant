<template>
  <div class="admission-detail">
    <div class="page-head">
      <el-button link type="primary" @click="goBack">&lt; 返回</el-button>
      <h2 class="page-title">住院时间轴</h2>
      <span class="sub-title muted">
        住院号 {{ admissionNo }} · {{ diagnosis }}
      </span>
    </div>

    <el-card v-loading="loading" shadow="never">
      <el-row v-if="vitalRows.length" :gutter="16">
        <el-col :xs="24" :lg="12">
          <Chart :option="tempOption" height="280px" />
        </el-col>
        <el-col :xs="24" :lg="12">
          <Chart :option="bpOption" height="280px" />
        </el-col>
      </el-row>

      <el-table v-if="orders.length" :data="orders" size="small" border style="margin-top: 12px">
        <el-table-column prop="itemName" label="医嘱" min-width="140" />
        <el-table-column prop="orderTypeText" label="类型" width="80" />
        <el-table-column prop="dosage" label="用量" width="80" />
        <el-table-column prop="frequency" label="频次" width="100" />
        <el-table-column label="开始时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.startTime) }}</template>
        </el-table-column>
      </el-table>

      <el-table v-if="vitals.length" :data="vitals" size="small" border style="margin-top: 12px">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.recordedAt) }}</template>
        </el-table-column>
        <el-table-column prop="temperature" label="体温℃" width="80" />
        <el-table-column prop="pulse" label="脉搏" width="80" />
        <el-table-column prop="respiration" label="呼吸" width="80" />
        <el-table-column prop="systolicBp" label="收缩压" width="80" />
        <el-table-column prop="diastolicBp" label="舒张压" width="80" />
      </el-table>

      <el-table v-if="labs.length" :data="labs" size="small" border style="margin-top: 12px">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.testedAt) }}</template>
        </el-table-column>
        <el-table-column prop="itemName" label="检验项目" min-width="140" />
        <el-table-column prop="resultValue" label="结果" width="90" />
        <el-table-column prop="unit" label="单位" width="70" />
        <el-table-column prop="referenceRange" label="参考范围" min-width="120" />
        <el-table-column label="异常" width="70">
          <template #default="{ row }">
            <el-tag v-if="row.isAbnormal" type="danger" size="small">异常</el-tag>
            <span v-else class="muted">正常</span>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && !orders.length && !vitals.length && !labs.length" description="暂无住院记录" />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listOrders, listVitalSigns, getAdmissionTimeline } from '../api/inpatient'
import Chart from '../components/Chart.vue'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const orders = ref([])
const vitals = ref([])
const labs = ref([])

const admissionNo = computed(() => route.query.admissionNo || '-')
const diagnosis = computed(() => route.query.diagnosis || '-')

const ORDER_TYPE = { 0: '长期', 1: '临时', 2: '检验', 3: '护理' }
const formatDateTime = (t) => (t ? String(t).replace('T', ' ').slice(0, 19) : '')

const tempOption = computed(() => ({
  grid: { left: 40, right: 20, top: 32, bottom: 24, containLabel: true },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: vitals.value.map((v) => formatDateTime(v.recordedAt)) },
  yAxis: { type: 'value', scale: true },
  series: [{ type: 'line', name: '体温℃', data: vitals.value.map((v) => v.temperature) }]
}))
const bpOption = computed(() => ({
  grid: { left: 40, right: 20, top: 32, bottom: 24, containLabel: true },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: vitals.value.map((v) => formatDateTime(v.recordedAt)) },
  yAxis: { type: 'value', scale: true },
  series: [
    { type: 'line', name: '收缩压', data: vitals.value.map((v) => v.systolicBp) },
    { type: 'line', name: '舒张压', data: vitals.value.map((v) => v.diastolicBp) }
  ]
}))
const vitalRows = computed(() => vitals.value)

const goBack = () => router.back()

onMounted(async () => {
  loading.value = true
  const admissionId = route.params.id
  const [oRes, vRes, tRes] = await Promise.all([
    listOrders({ admissionId }).catch(() => null),
    listVitalSigns({ admissionId }).catch(() => null),
    getAdmissionTimeline(admissionId).catch(() => null)
  ])
  orders.value = (oRes?.data || []).map((o) => ({ ...o, orderTypeText: ORDER_TYPE[o.orderType] ?? o.orderType }))
  vitals.value = vRes?.data || []
  labs.value = (tRes?.data?.events || []).filter((e) => e.type === 'LAB_TEST').map((e) => e.data)
  loading.value = false
})
</script>

<style scoped>
.page-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.page-title { margin: 0; font-size: 18px; font-weight: 600; }
.sub-title { font-size: 13px; }
.muted { color: #909399; }
</style>
