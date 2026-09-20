<template>
  <el-timeline class="event-timeline">
    <!-- 每次就诊一张卡：诊断/处方/检验归并到同一就诊下 -->
    <el-timeline-item
      v-for="(group, index) in grouped.groups"
      :key="groupKey(group, index)"
      :timestamp="formatDateTime(group.visit.time)"
      placement="top"
      size="large"
      color="#409EFF"
    >
      <el-card shadow="hover" class="visit-card">
        <!-- 就诊头 -->
        <div class="visit-head">
          <el-tag color="#409EFF" effect="dark" size="small" class="type-tag">就诊</el-tag>
          <span class="visit-meta">
            <span>{{ d(group.visit).department || '-' }}</span>
            <span class="meta-sep">·</span>
            <span>{{ d(group.visit).doctor || '-' }}</span>
            <span class="meta-sep">·</span>
            <span class="muted">{{ d(group.visit).visitNo || '-' }}</span>
          </span>
        </div>

        <!-- 诊断 -->
        <div v-if="group.diagnoses.length" class="section">
          <div class="section-label" style="--c: #F56C6C">
            <span class="dot"></span>诊断
          </div>
          <div v-for="(e, i) in group.diagnoses" :key="'d' + i" class="section-line">
            <span>{{ d(e).diagnosisName || '-' }}</span>
            <span v-if="d(e).icdCode" class="muted">（{{ d(e).icdCode }}）</span>
          </div>
        </div>

        <!-- 处方 -->
        <div v-for="(e, i) in group.prescriptions" :key="'p' + i" class="section">
          <div class="section-label" style="--c: #67C23A">
            <span class="dot"></span>处方
            <span v-if="d(e).totalAmount != null" class="amount">合计 ¥{{ d(e).totalAmount }}</span>
          </div>
          <el-table
            v-if="itemsOf(e).length"
            :data="itemsOf(e)"
            size="small"
            border
            class="item-table"
          >
            <el-table-column prop="drugName" label="药名" min-width="120" />
            <el-table-column prop="dosage" label="用量" min-width="80" />
            <el-table-column prop="frequency" label="频次" min-width="90" />
            <el-table-column prop="days" label="天数" width="70" />
            <el-table-column prop="quantity" label="数量" width="80" />
            <el-table-column prop="unit" label="单位" width="70" />
          </el-table>
          <div v-else class="v muted">无药品明细</div>
        </div>

        <!-- 检验 -->
        <div v-if="group.labTests.length" class="section">
          <div class="section-label" style="--c: #E6A23C">
            <span class="dot"></span>检验
          </div>
          <div v-for="(e, i) in group.labTests" :key="'l' + i" class="section-line lab-line">
            <span class="lab-name">{{ d(e).itemName || '-' }}</span>
            <span class="lab-val" :class="{ abnormal: isAbnormal(e) }">
              {{ d(e).resultValue ?? '-' }} {{ d(e).unit || '' }}
            </span>
            <el-tag v-if="isAbnormal(e)" type="danger" size="small" effect="dark">异常</el-tag>
            <span class="muted lab-ref">参考 {{ d(e).referenceRange || '-' }}</span>
          </div>
        </div>

        <!-- 收费 -->
        <div v-if="group.charges.length" class="section">
          <div class="section-label" style="--c: #8e44ad"><span class="dot"></span>收费</div>
          <div v-for="(e, i) in group.charges" :key="'c' + i" class="section-line">
            <span>{{ d(e).itemName || '-' }}</span>
            <span class="amount">¥{{ d(e).amount ?? '-' }}</span>
          </div>
        </div>

        <!-- 发药 -->
        <div v-if="group.dispenses.length" class="section">
          <div class="section-label" style="--c: #16a085"><span class="dot"></span>发药</div>
          <div v-for="(e, i) in group.dispenses" :key="'disp' + i" class="section-line">
            <span>{{ d(e).drugName || '-' }}</span>
            <span class="muted">{{ d(e).quantity ?? '-' }}</span>
          </div>
        </div>
      </el-card>
    </el-timeline-item>

    <!-- 住院卡：ADMISSION/DISCHARGE 按住院 id 归并为一张卡 -->
    <el-timeline-item
      v-for="(adm, index) in grouped.admissions"
      :key="'adm-' + index"
      :timestamp="formatDateTime(timeStr2(adm))"
      placement="top"
      size="large"
      color="#909399"
    >
      <el-card shadow="hover" class="visit-card">
        <el-tag color="#909399" effect="dark" size="small" class="type-tag">住院</el-tag>
        <div class="section-line">
          <span>{{ d(adm.admission).diagnosis || '-' }}</span>
          <span class="muted">· {{ d(adm.admission).department || '-' }} · {{ d(adm.admission).bedNo || '-' }}</span>
        </div>
        <div class="section-line muted">
          入院 {{ formatDateTime(adm.admission?.time) }}
          <template v-if="adm.discharge"> → 出院 {{ formatDateTime(adm.discharge.time) }}</template>
        </div>
        <el-button link type="primary" size="small"
          @click="openAdmission(d(adm.admission).id)">查看住院时间轴</el-button>
      </el-card>
    </el-timeline-item>

    <!-- 孤立事件兜底（无对应就诊的事件，正常数据不会出现） -->
    <el-timeline-item
      v-for="(event, index) in grouped.orphans"
      :key="eventKey(event, index)"
      :timestamp="formatDateTime(event.time)"
      placement="top"
      size="large"
      :color="metaOf(event.type).color"
    >
      <el-card shadow="hover" class="visit-card">
        <el-tag :color="metaOf(event.type).color" effect="dark" size="small" class="type-tag">
          {{ metaOf(event.type).label }}
        </el-tag>
        <div class="v muted orphan-text">{{ orphanSummary(event) }}</div>
      </el-card>
    </el-timeline-item>
  </el-timeline>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  events: { type: Array, default: () => [] }
})

// 事件类型 -> 颜色 / 标签
const TYPE_META = {
  VISIT: { label: '就诊', color: '#409EFF' },
  DIAGNOSIS: { label: '诊断', color: '#F56C6C' },
  PRESCRIPTION: { label: '处方', color: '#67C23A' },
  LAB_TEST: { label: '检验', color: '#E6A23C' },
  ADMISSION: { label: '入院', color: '#909399' },
  DISCHARGE: { label: '出院', color: '#909399' },
  CHARGE: { label: '收费', color: '#8e44ad' },
  DISPENSE: { label: '发药', color: '#16a085' }
}

const metaOf = (type) => TYPE_META[type] || { label: type || '其他', color: '#909399' }

// data 理论上必有，做一次兜底避免模板里重复判空
const d = (event) => event?.data || {}

const itemsOf = (event) => {
  const items = d(event).items
  return Array.isArray(items) ? items : []
}

const isAbnormal = (event) => Number(d(event).isAbnormal) === 1

// "2024-01-01T09:00:00" -> "2024-01-01 09:00:00"
const formatDateTime = (t) => (t ? String(t).replace('T', ' ').slice(0, 19) : '')

// ISO 时间字符串长度固定，字典序即时间序
const timeStr = (event) => (event?.time ? String(event.time) : '')

// 住院卡：入院/出院事件二选一取时间
const timeStr2 = (adm) => (adm.admission || adm.discharge)?.time || ''

// 用 hash 跳转，Timeline 为展示组件，不引入路由上下文依赖
const openAdmission = (id) => {
  if (id != null) window.location.hash = `#/admission/${id}`
}

/**
 * 归并：以 VISIT 事件的 data.id 为键，把 DIAGNOSIS / PRESCRIPTION / LAB_TEST /
 * CHARGE / DISPENSE 按 data.visitId 挂到对应就诊下；ADMISSION / DISCHARGE 按
 * 住院 data.id 归并为一张住院卡。后端已按时间升序返回，归并后再按时间排一次保证组内有序。
 */
const grouped = computed(() => {
  const visitMap = new Map()
  const admissionMap = new Map()
  const orphans = []
  for (const e of props.events) {
    const data = d(e)
    if (e.type === 'VISIT') {
      if (data.id == null) {
        orphans.push(e)
        continue
      }
      let g = visitMap.get(data.id)
      if (!g) {
        g = { visit: e, diagnoses: [], prescriptions: [], labTests: [], charges: [], dispenses: [] }
        visitMap.set(data.id, g)
      } else {
        g.visit = e
      }
    } else if (e.type === 'DIAGNOSIS' || e.type === 'PRESCRIPTION' || e.type === 'LAB_TEST') {
      const g = data.visitId != null ? visitMap.get(data.visitId) : null
      if (g) {
        if (e.type === 'DIAGNOSIS') g.diagnoses.push(e)
        else if (e.type === 'PRESCRIPTION') g.prescriptions.push(e)
        else g.labTests.push(e)
      } else {
        orphans.push(e)
      }
    } else if (e.type === 'CHARGE' || e.type === 'DISPENSE') {
      const g = data.visitId != null ? visitMap.get(data.visitId) : null
      if (g) {
        if (e.type === 'CHARGE') (g.charges ||= []).push(e)
        else (g.dispenses ||= []).push(e)
      } else {
        orphans.push(e)
      }
    } else if (e.type === 'ADMISSION' || e.type === 'DISCHARGE') {
      const key = data.id
      if (key == null) {
        orphans.push(e)
        continue
      }
      let a = admissionMap.get(key)
      if (!a) { a = { admission: null, discharge: null }; admissionMap.set(key, a) }
      if (e.type === 'ADMISSION') a.admission = e
      else a.discharge = e
    } else {
      orphans.push(e)
    }
  }
  const groups = [...visitMap.values()].sort((a, b) =>
    timeStr(a.visit).localeCompare(timeStr(b.visit))
  )
  for (const g of groups) {
    g.diagnoses.sort((a, b) => timeStr(a).localeCompare(timeStr(b)))
    g.prescriptions.sort((a, b) => timeStr(a).localeCompare(timeStr(b)))
    g.labTests.sort((a, b) => timeStr(a).localeCompare(timeStr(b)))
    g.charges.sort((a, b) => timeStr(a).localeCompare(timeStr(b)))
    g.dispenses.sort((a, b) => timeStr(a).localeCompare(timeStr(b)))
  }
  const admissions = [...admissionMap.values()].sort((x, y) =>
    timeStr2(x).localeCompare(timeStr2(y))
  )
  return { groups, orphans, admissions }
})

const orphanSummary = (event) => {
  const data = d(event)
  switch (event.type) {
    case 'DIAGNOSIS':
      return `${data.diagnosisName || '-'}${data.icdCode ? '（' + data.icdCode + '）' : ''}`
    case 'PRESCRIPTION':
      return `处方${data.totalAmount != null ? '，合计 ¥' + data.totalAmount : ''}`
    case 'LAB_TEST':
      return `${data.itemName || '-'} ${data.resultValue ?? ''} ${data.unit || ''}`
    default:
      return data.visitNo || data.id || '-'
  }
}

const groupKey = (group, index) => `${d(group.visit).id ?? 'visit'}-${index}`
const eventKey = (event, index) => `${event?.type || 'UNKNOWN'}-${d(event).id ?? index}`
</script>

<style scoped>
.visit-card {
  --el-card-padding: 12px 16px;
}

.visit-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 4px;
}

.type-tag {
  border: none;
  color: #fff;
  font-weight: 600;
}

.visit-meta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: #303133;
}

.meta-sep {
  color: #c0c4cc;
}

.section {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed #ebeef5;
}

.section-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--c);
  margin-bottom: 4px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--c);
}

.amount {
  margin-left: auto;
  color: #67c23a;
  font-weight: 600;
}

.section-line {
  padding-left: 14px;
  line-height: 1.9;
  font-size: 13px;
  color: #303133;
}

.lab-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.lab-name {
  font-weight: 500;
}

.lab-val.abnormal {
  color: #f56c6c;
  font-weight: 600;
}

.lab-ref {
  font-size: 12px;
}

.item-table {
  width: 100%;
  margin-top: 2px;
}

.orphan-text {
  margin-top: 8px;
}

.muted {
  color: #909399;
}
</style>
