<template>
  <div>
    <div class="page-heading"><div><div class="eyebrow">NEONATAL RECORDS</div><h1>新生儿病案</h1><p>检索出院小结，查看孕周、诊疗经过与出院记录。</p></div><el-button type="primary" @click="router.push('/import')">导入出院小结</el-button></div>
    <div class="metric-grid">
      <div class="metric"><div class="label">出院小结</div><div class="value">{{ overview?.records?.toLocaleString() ?? '—' }}</div><div class="hint">当前数据集的原始记录数</div></div>
      <div class="metric"><div class="label">孕周已记录</div><div class="value">{{ overview?.knownGestation?.toLocaleString() ?? '—' }}</div><div class="hint">按 Excel 孕周字段统计</div></div>
      <div class="metric"><div class="label">孕周小于 37 周</div><div class="value">{{ overview?.preterm?.toLocaleString() ?? '—' }}</div><div class="hint">未提及孕周的记录单独保留</div></div>
      <div class="metric"><div class="label">待核对病案</div><div class="value">{{ overview?.recordsWithIssues?.toLocaleString() ?? '—' }}</div><div class="hint"><router-link to="/validation">查看数据质量问题 →</router-link></div></div>
    </div>
    <form class="panel filter-form" @submit.prevent="search">
      <el-input v-model="filters.keyword" clearable placeholder="搜索住院号、入院或出院诊断" aria-label="搜索住院号或诊断" />
      <el-select v-model="filters.gestation" placeholder="全部孕周" clearable aria-label="孕周分组"><el-option label="小于 37 周" value="preterm"/><el-option label="37–41 周" value="term"/><el-option label="42 周及以上" value="postterm"/><el-option label="孕周未提及" value="unknown"/></el-select>
      <el-select v-model="filters.gender" placeholder="全部性别" clearable aria-label="性别"><el-option label="男" value="男"/><el-option label="女" value="女"/></el-select>
      <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="入院起始日期" end-placeholder="入院结束日期" style="width:260px; flex-grow:0" />
      <el-select v-model="filters.outcome" placeholder="全部治疗结果" clearable aria-label="治疗结果"><el-option v-for="o in outcomes" :key="o" :label="o" :value="o"/></el-select>
      <el-checkbox v-model="filters.issuesOnly">仅待核对</el-checkbox><el-button type="primary" native-type="submit">查询</el-button><el-button @click="reset">重置</el-button>
    </form>
    <div class="panel table-panel" v-loading="loading">
      <div class="table-top"><span>病案列表 <el-tag size="small" type="info" effect="plain">{{ total.toLocaleString() }} 条</el-tag></span><span class="muted">每条对应一份原始小结，重复住院号分别保留</span></div>
      <div v-if="error" class="error-state">病案加载失败<el-button @click="load">重试</el-button></div>
      <el-table v-else :data="records" row-key="id" @row-dblclick="open">
        <el-table-column label="住院号" min-width="145"><template #default="{ row }"><router-link :to="`/neonatal/${row.id}`" class="mono">{{ row.hospitalNo || '未记录' }}</router-link><div class="muted" style="font-size:10px">来源第 {{ row.rowNumber }} 行</div></template></el-table-column>
        <el-table-column prop="gender" label="性别" width="65"/><el-table-column prop="reportedAge" label="原文年龄" width="90"/>
        <el-table-column label="孕周" width="100"><template #default="{ row }"><el-tag v-if="row.gestationalWeeks != null" size="small" :type="row.gestationalWeeks < 37 ? 'warning' : 'success'">{{ row.gestationalWeeks }} 周{{ row.gestationalDays != null ? `+${row.gestationalDays}` : '' }}</el-tag><span v-else class="muted">未提及</span></template></el-table-column>
        <el-table-column prop="admissionTimeRaw" label="入院时间（原文）" min-width="160"/>
        <el-table-column prop="dischargeDiagnosis" label="出院诊断" min-width="240" show-overflow-tooltip/>
        <el-table-column prop="treatmentResult" label="治疗结果（原文）" min-width="115" show-overflow-tooltip/>
        <el-table-column label="数据核对" width="90"><template #default="{ row }"><el-tag v-if="row.issueCount" type="warning" effect="plain" size="small">{{ row.issueCount }} 项</el-tag><span v-else class="muted">无标记</span></template></el-table-column>
        <el-table-column label="操作" width="70" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="open(row)">查看</el-button></template></el-table-column>
        <template #empty><el-empty description="没有符合条件的病案"><el-button @click="router.push('/import')">导入 Excel</el-button></el-empty></template>
      </el-table>
      <div class="pagination"><el-pagination v-model:current-page="page" v-model:page-size="size" :page-sizes="[20,50,100]" :total="total" layout="total, sizes, prev, pager, next" @current-change="load" @size-change="search"/></div>
    </div>
    <p class="muted">年龄按原文展示，不代表入院日龄；孕周为源表记录值，未自动从病史提取或校正。</p>
  </div>
</template>
<script setup>
import { ref, reactive, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getRecords, getStats, selectedBatch, batchParams } from '../api/neonatal'
const router = useRouter(), filters = reactive({ keyword:'', gestation:'', gender:'', outcome:'', issuesOnly:false })
const outcomes = ['治愈','好转','未愈','转院','自动出院','死亡','其他/待核对','未记录']
const dateRange = ref([]), page = ref(1), size = ref(20), total = ref(0), records = ref([]), overview = ref(null), loading = ref(false), error = ref(false)
let generation = 0
async function load() {
  const current = ++generation; loading.value = true; error.value = false
  try { const res = await getRecords({ ...batchParams(), ...filters, page:page.value, size:size.value, dateFrom:dateRange.value?.[0], dateTo:dateRange.value?.[1] }); if (current === generation) { records.value = res.data.records; total.value = res.data.total } }
  catch { if (current === generation) { error.value = true; records.value = []; total.value = 0 } }
  finally { if (current === generation) loading.value = false }
}
function search() { page.value = 1; load() }
function reset() { Object.assign(filters, { keyword:'', gestation:'', gender:'', outcome:'', issuesOnly:false }); dateRange.value = []; search() }
function open(row) { router.push(`/neonatal/${row.id}`) }
watch(selectedBatch, async () => { search(); overview.value = null; const batch = selectedBatch.value
  try { const res = await getStats(batchParams()); if (batch === selectedBatch.value) overview.value = res.data.overview } catch { /* List remains usable if statistics fail. */ }
}, { immediate:true })
</script>
