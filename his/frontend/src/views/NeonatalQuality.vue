<template>
  <div v-loading="loading">
    <div class="page-heading"><div><div class="eyebrow">DATA QUALITY</div><h1>数据质量</h1><p>核对原始数据的缺失、重复与格式问题，保留每份小结的原始记录。</p></div><el-button @click="load">刷新</el-button></div>
    <div class="note">这里检查数据完整性与格式，不做临床合理用药或诊疗路径判断。重复住院号不代表重复患儿；孕周未知不按足月处理。问题数可能大于病案数。</div>
    <div v-if="error" class="panel error-state">数据质量报告加载失败<el-button @click="load">重试</el-button></div>
    <template v-else-if="data">
      <div class="panel"><div class="panel-title">问题分布 <small>点击筛选查看相关病案</small></div>
        <div class="issue-filters"><el-button :type="!issueCode?'primary':'default'" @click="choose('')">全部待核对 {{ data.totalIssueRecords }} 条</el-button><el-button v-for="item in groups" :key="item.code" :type="issueCode===item.code?'primary':'default'" @click="choose(item.code)">{{ labels[item.code] || item.code }} {{ item.count }} 项</el-button></div>
        <el-table :data="data.counts" style="margin-top:20px"><el-table-column prop="field" label="字段" width="150"/><el-table-column prop="message" label="核对说明" min-width="300"/><el-table-column prop="count" label="问题数" width="100" align="right"/></el-table>
      </div>
      <div class="panel table-panel"><div class="table-top"><span>相关病案</span><span class="muted">{{ data.page.total.toLocaleString() }} 条</span></div>
        <el-table :data="data.page.records"><el-table-column label="住院号" min-width="150"><template #default="{row}"><router-link :to="`/neonatal/${row.id}`" class="mono">{{row.hospitalNo||'未记录'}}</router-link></template></el-table-column><el-table-column prop="rowNumber" label="来源行" width="90"/><el-table-column prop="gestationalAgeRaw" label="孕周原文" width="110"/><el-table-column prop="admissionTimeRaw" label="入院时间原文" min-width="150"/><el-table-column prop="dischargeDiagnosis" label="出院诊断" min-width="240" show-overflow-tooltip/><el-table-column prop="issueCount" label="问题数" width="80"/><el-table-column label="操作" width="80"><template #default="{row}"><router-link :to="`/neonatal/${row.id}`">核对详情</router-link></template></el-table-column></el-table>
        <div class="pagination"><el-pagination v-model:current-page="page" :page-size="20" :total="data.page.total" layout="total, prev, pager, next" @current-change="load"/></div>
      </div>
    </template>
  </div>
</template>
<script setup>
import { ref, computed, watch } from 'vue'
import { getQuality,selectedBatch,batchParams } from '../api/neonatal'
const data=ref(null),loading=ref(false),error=ref(false),issueCode=ref(''),page=ref(1)
const labels={UNKNOWN_GESTATION:'孕周未知',REPEATED_HOSPITAL_NO:'住院号重复',INVALID_DATE:'日期缺失或异常',REVERSED_DATES:'日期倒置',INVALID_AGE:'年龄不明确',MISSING_FIELD:'关键字段缺失',MISSING_HOSPITAL_NO:'住院号缺失',INVALID_GENDER:'性别不明确',EXCEL_TEXT_LIMIT:'文本长度上限'}
const groups=computed(()=>{const counts=new Map();for(const r of data.value?.counts||[])counts.set(r.code,(counts.get(r.code)||0)+r.count);return Array.from(counts,([code,count])=>({code,count}))})
function choose(code){issueCode.value=code;page.value=1;load()}
let generation=0
async function load(){const current=++generation;loading.value=true;error.value=false;try{const res=await getQuality({...batchParams(),issueCode:issueCode.value,page:page.value});if(current===generation)data.value=res.data}catch{if(current===generation){error.value=true;data.value=null}}finally{if(current===generation)loading.value=false}}
watch(selectedBatch,()=>{page.value=1;load()},{immediate:true})
</script>
<style scoped>.issue-filters{display:flex;flex-wrap:wrap;gap:10px}.issue-filters .el-button{margin:0}</style>
