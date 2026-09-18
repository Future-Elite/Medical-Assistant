<template>
  <el-timeline class="event-timeline">
    <!-- 后端已按 time 升序返回，这里按数组顺序渲染即可 -->
    <el-timeline-item
      v-for="(event, index) in events"
      :key="eventKey(event, index)"
      :color="metaOf(event.type).color"
      :timestamp="formatDateTime(event.time)"
      placement="top"
      size="large"
    >
      <el-card shadow="hover" class="event-card">
        <div class="card-head">
          <el-tag :color="metaOf(event.type).color" effect="dark" size="small" class="type-tag">
            {{ metaOf(event.type).label }}
          </el-tag>
          <span v-if="event.type === 'PRESCRIPTION' && d(event).totalAmount != null" class="amount">
            合计 ¥{{ d(event).totalAmount }}
          </span>
        </div>

        <!-- 就诊 -->
        <div v-if="event.type === 'VISIT'" class="fields">
          <div class="field">
            <span class="k">就诊科室</span>
            <span class="v">{{ d(event).department || '-' }}</span>
          </div>
          <div class="field">
            <span class="k">医生</span>
            <span class="v">{{ d(event).doctor || '-' }}</span>
          </div>
          <div class="field">
            <span class="k">就诊号</span>
            <span class="v">{{ d(event).visitNo || '-' }}</span>
          </div>
          <div v-if="d(event).remark" class="field field-wide">
            <span class="k">备注</span>
            <span class="v muted">{{ d(event).remark }}</span>
          </div>
        </div>

        <!-- 诊断 -->
        <div v-else-if="event.type === 'DIAGNOSIS'" class="fields">
          <div class="field">
            <span class="k">诊断名</span>
            <span class="v">
              {{ d(event).diagnosisName || '-' }}
              <span v-if="d(event).icdCode" class="muted">（{{ d(event).icdCode }}）</span>
            </span>
          </div>
          <div class="field">
            <span class="k">医生</span>
            <span class="v">{{ d(event).doctor || '-' }}</span>
          </div>
        </div>

        <!-- 处方 -->
        <div v-else-if="event.type === 'PRESCRIPTION'" class="prescription">
          <div class="field">
            <span class="k">开方医生</span>
            <span class="v">{{ d(event).doctor || '-' }}</span>
          </div>
          <el-table
            v-if="itemsOf(event).length"
            :data="itemsOf(event)"
            size="small"
            border
            class="item-table"
          >
            <el-table-column prop="drugName" label="药名" min-width="140" />
            <el-table-column prop="dosage" label="用量" min-width="90" />
            <el-table-column prop="frequency" label="频次" min-width="90" />
            <el-table-column prop="days" label="天数" width="70" />
            <el-table-column prop="quantity" label="数量" width="80" />
            <el-table-column prop="unit" label="单位" width="70" />
          </el-table>
          <div v-else class="v muted">无药品明细</div>
        </div>

        <!-- 检验 -->
        <div v-else-if="event.type === 'LAB_TEST'" class="fields">
          <div class="field">
            <span class="k">项目名</span>
            <span class="v">{{ d(event).itemName || '-' }}</span>
          </div>
          <div class="field">
            <span class="k">结果值</span>
            <span class="v" :class="{ abnormal: isAbnormal(event) }">
              {{ d(event).resultValue ?? '-' }}
            </span>
            <el-tag v-if="isAbnormal(event)" type="danger" size="small" effect="dark">异常</el-tag>
          </div>
          <div class="field">
            <span class="k">单位</span>
            <span class="v">{{ d(event).unit || '-' }}</span>
          </div>
          <div class="field">
            <span class="k">参考范围</span>
            <span class="v muted">{{ d(event).referenceRange || '-' }}</span>
          </div>
        </div>

        <!-- 未知类型兜底 -->
        <div v-else class="v muted">{{ d(event).visitNo || d(event).id || '-' }}</div>
      </el-card>
    </el-timeline-item>
  </el-timeline>
</template>

<script setup>
defineProps({
  events: { type: Array, default: () => [] }
})

// 事件类型 -> 颜色 / 标签
const TYPE_META = {
  VISIT: { label: '就诊', color: '#409EFF' },
  DIAGNOSIS: { label: '诊断', color: '#F56C6C' },
  PRESCRIPTION: { label: '处方', color: '#67C23A' },
  LAB_TEST: { label: '检验', color: '#E6A23C' }
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

const eventKey = (event, index) => `${event?.type || 'UNKNOWN'}-${d(event).id ?? index}`
</script>

<style scoped>
.event-card {
  --el-card-padding: 12px 16px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.type-tag {
  border: none;
  color: #fff;
  font-weight: 600;
}

.amount {
  color: #67c23a;
  font-weight: 600;
}

.fields {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 24px;
}

.field {
  display: flex;
  align-items: center;
  gap: 6px;
  line-height: 1.8;
}

.field-wide {
  flex-basis: 100%;
}

.k {
  color: #909399;
  font-size: 13px;
}

.v {
  color: #303133;
  font-size: 13px;
}

.v.abnormal {
  color: #f56c6c;
  font-weight: 600;
}

.muted {
  color: #909399;
}

.prescription .field {
  margin-bottom: 8px;
}

.item-table {
  width: 100%;
}
</style>
