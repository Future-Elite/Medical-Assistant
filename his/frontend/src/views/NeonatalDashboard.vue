<template>
  <div v-loading="loading">
    <div class="page-heading"><div><div class="eyebrow">NEONATAL ANALYTICS</div><h1>专科数据看板</h1><p>按当前导入批次汇总出院小结，统计单位为记录条数。</p></div><el-button @click="load">刷新统计</el-button></div>
    <div v-if="error" class="panel error-state">统计加载失败<el-button @click="load">重试</el-button></div>
    <template v-else-if="data">
      <div class="metric-grid">
        <div class="metric"><div class="label">出院小结总数</div><div class="value">{{ data.overview.records.toLocaleString() }}</div><div class="hint">不同住院号 {{ data.overview.hospitalNumbers.toLocaleString() }} 个，非去重患儿数</div></div>
        <div class="metric"><div class="label">孕周记录率</div><div class="value">{{ percent(data.overview.knownGestation, data.overview.records) }}</div><div class="hint">{{ data.overview.knownGestation.toLocaleString() }} / {{ data.overview.records.toLocaleString() }} 条</div></div>
        <div class="metric"><div class="label">小于 37 周占已知孕周</div><div class="value">{{ percent(data.overview.preterm, data.overview.knownGestation) }}</div><div class="hint">分母仅含孕周已知的记录</div></div>
        <div class="metric"><div class="label">平均住院天数</div><div class="value">{{ data.overview.averageStay ?? '—' }}<span v-if="data.overview.averageStay != null" style="font-size:12px; margin-left:6px">天</span></div><div class="hint">有效日期差样本 {{ data.overview.validStayRecords.toLocaleString() }} 条</div></div>
      </div>
      <div v-if="!data.overview.records" class="panel"><el-empty description="尚未导入新生儿出院小结"><el-button type="primary" @click="router.push('/import')">导入 Excel</el-button></el-empty></div>
      <template v-else>
        <div class="chart-grid">
          <div class="panel"><div class="panel-title">孕周分布 <small>未提及单独统计</small></div><Chart :option="gestationOption" /></div>
          <div class="panel"><div class="panel-title">每月入院小结数 <small>按可解析入院日期</small></div><Chart :option="monthlyOption"/><p class="muted">另有 {{ data.overview.unknownAdmissionDate }} 条入院日期缺失或无法解析。</p></div>
          <div class="panel"><div class="panel-title">治疗结果 <small>原文归一分组</small></div><Chart :option="outcomeOption"/></div>
          <div class="panel"><div class="panel-title">性别分布 <small>按源表记录</small></div><Chart :option="genderOption"/></div>
        </div>
        <div class="panel"><div class="panel-title">常见出院诊断原文 <small>前 10 种完整诊断组合</small></div><el-table :data="data.diagnoses"><el-table-column type="index" width="60" label="序号"/><el-table-column prop="label" label="出院诊断原文" min-width="300"/><el-table-column prop="count" label="记录数" width="120" align="right"/></el-table></div>
        <div class="note">诊断可能包含多个疾病且缺少分隔符，以上按完整原文组合计数。孕周分组直接使用源表字段；“治疗结果”中列举多种可能结局的文本列入“其他/待核对”，不视为已确认的转院或自动出院。</div>
      </template>
    </template>
  </div>
</template>
<script setup>
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import Chart from '../components/Chart.vue'
import { getStats, selectedBatch, batchParams } from '../api/neonatal'
const router = useRouter(), data = ref(null), error = ref(false), loading = ref(false)
const percent = (n,d) => d ? `${(n / d * 100).toFixed(1)}%` : '—'
const palette = ['#128c99','#5eb9b1','#7e9fc4','#c8a671','#91a7b4','#baa5c8','#d09b87','#c6d6df']
function bars(rows, color='#128c99', monthly=false) { return { color:[color], tooltip:{ trigger:'axis', confine:true }, grid:{ left:44,right:18,top:20,bottom:60 }, xAxis:{ type:'category',data:rows.map(r => r.label),axisLabel:{color:'#8495a2',fontSize:10,interval:0,rotate:monthly?45:35},axisTick:{show:false},axisLine:{lineStyle:{color:'#e5edf2'}} }, yAxis:{ type:'value',minInterval:1,axisLabel:{color:'#8495a2'},splitLine:{lineStyle:{color:'#f0f4f7'}} }, series:[{type:'bar',data:rows.map(r => r.count),barMaxWidth:23,itemStyle:{borderRadius:[4,4,0,0]}}] } }
function pie(rows) { return {color:palette,tooltip:{trigger:'item',confine:true},legend:{bottom:0,textStyle:{color:'#738796',fontSize:11}},series:[{type:'pie',radius:['43%','65%'],center:['50%','43%'],avoidLabelOverlap:true,itemStyle:{borderColor:'#fff',borderWidth:3},label:{formatter:'{b}: {c}',color:'#687e8e',fontSize:11},data:rows.map(r=>({name:r.label||'未记录',value:r.count}))}]} }
const gestationOption = computed(()=>bars(data.value?.gestation||[])), monthlyOption = computed(()=>bars(data.value?.monthly||[],'#128c99',true)), outcomeOption = computed(()=>pie(data.value?.outcomes||[])), genderOption = computed(()=>pie(data.value?.gender||[]))
let generation=0
async function load() { const current=++generation; loading.value=true; error.value=false
  try { const res=await getStats(batchParams()); if(current===generation)data.value=res.data }
  catch { if(current===generation){error.value=true;data.value=null} } finally {if(current===generation)loading.value=false}
}
watch(selectedBatch,load,{immediate:true})
</script>
