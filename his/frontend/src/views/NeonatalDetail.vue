<template>
  <div v-loading="loading">
    <div class="page-heading"><div><div class="eyebrow">DISCHARGE SUMMARY</div><h1>新生儿病案详情</h1><p>原始出院记录与结构化信息</p></div><el-button @click="router.push('/')">返回病案列表</el-button></div>
    <div v-if="error" class="panel error-state">病案不存在或加载失败<el-button @click="load">重试</el-button></div>
    <template v-else-if="record">
      <div class="panel"><div class="panel-title"><span class="mono" style="font-size:20px">{{ record.hospitalNo || '住院号未记录' }}</span><el-tag effect="plain">{{ record.gender }} · {{ record.reportedAge || '年龄未记录' }}</el-tag></div>
        <div class="detail-meta">
          <div><div class="label">孕周（源表）</div><div class="value">{{ record.gestationalAgeRaw || '未记录' }}</div></div>
          <div><div class="label">入院时间（原文）</div><div class="value">{{ record.admissionTimeRaw || '未记录' }}</div></div>
          <div><div class="label">出院时间（原文）</div><div class="value">{{ record.dischargeTimeRaw || '未记录' }}</div></div>
          <div><div class="label">住院天数（日期差）</div><div class="value">{{ record.lengthOfStay == null ? '无法计算' : `${record.lengthOfStay} 天` }}</div></div>
          <div><div class="label">治疗结果（原文）</div><div class="value">{{ record.treatmentResult || '未记录' }}</div></div>
          <div><div class="label">来源工作表 / 行</div><div class="value">{{ record.batch.sheetName }} / 第 {{ record.rowNumber }} 行</div></div>
          <div><div class="label">来源文档</div><div class="value">{{ record.sourceFile || '未记录' }}</div></div>
          <div><div class="label">导入批次</div><div class="value">#{{ record.batchId }}</div></div>
        </div>
      </div>
      <div v-if="record.issues.length" class="panel"><div class="panel-title">数据核对 <el-tag type="warning" size="small">{{ record.issues.length }} 项</el-tag></div><div v-for="(issue,i) in record.issues" :key="i" class="muted" style="margin-bottom:7px"><strong>{{ issue.field }}</strong>：{{ issue.message }}</div></div>
      <div class="panel"><el-tabs v-model="activeTab">
        <el-tab-pane label="出院小结" name="summary"><section v-for="section in sections" :key="section.key" class="clinical-section"><h3>{{ section.label }}</h3><div class="clinical-text">{{ textFor(section) || '原表未记录' }}</div></section></el-tab-pane>
        <el-tab-pane label="原始 Excel 行" name="source"><p class="muted" style="margin:12px 0">{{ record.batch.fileName }} · {{ record.batch.sheetName }} · 第 {{ record.rowNumber }} 行</p><section v-for="(value,key) in record.rawData" :key="key" class="clinical-section"><h3>{{ key }}</h3><div class="clinical-text">{{ value || '（空单元格）' }}</div></section></el-tab-pane>
        <el-tab-pane label="来源与统计口径" name="provenance">
          <div class="clinical-section"><h3>文件校验值（SHA-256）</h3><div class="clinical-text mono">{{ record.batch.fileSha256 }}</div></div>
          <div class="clinical-section"><h3>入库规则</h3><div class="clinical-text">一份小结对应一条独立病案，不按重复住院号合并患儿。孕周仅使用源表“孕周”列，未记录的天数保留为空。年龄按原文保存，不推算出生日期。住院天数为出院日期减入院日期，不含无法解析或先后颠倒的日期。仅日期值归一为当日 00:00 便于检索，不代表实际入出院时刻。</div></div>
          <div class="clinical-section"><h3>诊疗信息</h3><div class="clinical-text">检查、诊疗经过及出院医嘱按原文展示。原表没有独立、带时间戳的处方、收费或逐日医嘱明细，因此未将叙述文本自动拆为执行记录，也未自动赋予 ICD 编码。</div></div>
        </el-tab-pane>
      </el-tabs></div>
    </template>
  </div>
</template>
<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getRecord } from '../api/neonatal'
const route = useRoute(), router = useRouter(), record = ref(null), loading = ref(false), error = ref(false), activeTab = ref('summary')
const sections = [{key:'admissionDiagnosis',label:'入院诊断',root:true},{key:'dischargeDiagnosis',label:'出院诊断',root:true},{key:'presentingSymptoms',label:'入院时主要症状及体征'},{key:'laboratoryFindings',label:'实验室检查及主要会诊'},{key:'specialExaminations',label:'特殊检验及重要会诊'},{key:'treatmentCourse',label:'诊疗过程'},{key:'complications',label:'合并症'},{key:'dischargeCondition',label:'出院时情况'},{key:'dischargeOrders',label:'出院医嘱'},{key:'dischargeGuidance',label:'出院指导'}]
const textFor = s => s.root ? record.value[s.key] : record.value.summary[s.key]
let generation = 0
async function load() { const current = ++generation; loading.value = true; error.value = false; record.value = null
  try { const res = await getRecord(route.params.id); if (current === generation) record.value = res.data }
  catch { if (current === generation) error.value = true }
  finally { if (current === generation) loading.value = false }
}
watch(() => route.params.id, load, { immediate:true })
</script>
