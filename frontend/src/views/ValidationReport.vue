<template>
  <div class="validation">
    <div class="page-head">
      <h2 class="page-title">数据质量校验报告</h2>
      <el-button type="primary" :loading="loading" @click="load">重新校验</el-button>
    </div>

    <el-row v-if="report" :gutter="16" class="metric-row">
      <el-col :xs="24" :md="8">
        <el-card shadow="never">
          <div class="metric">
            <div class="metric-value">{{ pct(report.rationalDrug?.rationalRate) }}</div>
            <div class="metric-label">合理用药率</div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="never">
          <div class="metric">
            <div class="metric-value">{{ pct(report.pathway?.executionRate) }}</div>
            <div class="metric-label">临床路径执行率</div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="never">
          <div class="metric">
            <div class="metric-value">{{ report.rationalDrug?.violationPrescriptions ?? 0 }}</div>
            <div class="metric-label">违规处方数</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card v-if="report" shadow="never">
      <template #header><span class="card-title">合理用药违规明细</span></template>
      <el-table :data="report.rationalDrug?.violations || []" size="small" border>
        <el-table-column prop="prescriptionId" label="处方ID" width="90" />
        <el-table-column prop="patientId" label="患者ID" width="90" />
        <el-table-column prop="ruleType" label="违规类型" width="120" />
        <el-table-column prop="warningLevel" label="级别" width="70" />
        <el-table-column prop="message" label="说明" min-width="240" />
      </el-table>
    </el-card>

    <el-card v-if="report" shadow="never" style="margin-top: 16px">
      <template #header><span class="card-title">临床路径变异明细</span></template>
      <el-table :data="report.pathway?.variances || []" size="small" border>
        <el-table-column prop="admissionId" label="住院ID" width="90" />
        <el-table-column prop="patientId" label="患者ID" width="90" />
        <el-table-column prop="diagnosisName" label="诊断" width="140" />
        <el-table-column prop="varianceType" label="变异类型" width="100" />
        <el-table-column prop="detail" label="说明" min-width="240" />
      </el-table>
    </el-card>

    <el-empty v-if="!loading && !report" description="点击「重新校验」生成报告" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { runValidation } from '../api/validation'

const report = ref(null)
const loading = ref(false)

const pct = (v) => (v == null ? '-' : (Number(v) * 100).toFixed(1) + '%')

const load = async () => {
  loading.value = true
  const res = await runValidation().catch(() => null)
  report.value = res?.data || null
  loading.value = false
}

onMounted(load)
</script>

<style scoped>
.page-head { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
.page-title { margin: 0; font-size: 18px; font-weight: 600; }
.metric-row { margin-bottom: 16px; }
.metric { text-align: center; padding: 8px 0; }
.metric-value { font-size: 30px; font-weight: 700; color: #409EFF; }
.metric-label { font-size: 13px; color: #909399; margin-top: 4px; }
.card-title { font-weight: 600; }
</style>
