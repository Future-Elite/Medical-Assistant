<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import Icon from './Icon.vue'
import ModelSelect from './ModelSelect.vue'
import { post, request, downloadBatch } from '../api'
import { formatTime, statusLabels } from '../utils'
const props = defineProps({ models:{type:Array,default:()=>[]}, initialBatchId:String, defaultModel:String })
const emit = defineEmits(['open-patient','selected-batch','updated'])
const source=ref(null), preview=ref(null), batch=ref(null), recent=ref([]), error=ref(''), busy=ref(false), exporting=ref(false), loading=ref(true)
const resultsPanel=ref(null)
const config=reactive({model:props.defaultModel||'Qwen3.8-27B',backend:'api',seed:42,interval_hours:3,care_setting:'nicu',start_time:new Date().toLocaleDateString('sv-SE')+'T08:00'})
const running=computed(()=>['queued','running'].includes(batch.value?.status))
const groupLabels={short:'短轨迹 · 1–2 步',medium:'中轨迹 · 3–5 步',long:'长轨迹 · 6–10 步'}
const labels={...statusLabels,completed_with_errors:'部分患者生成失败'}
let timer, controller, token=0, disposed=false
function stop(){ clearTimeout(timer); controller?.abort(); token++ }
async function refreshRecent(){ const result=await request('/batches'); if(!disposed) recent.value=result.items }
async function poll(id, generation){
  controller=new AbortController()
  try {
    const result=await request(`/batches/${id}`,{signal:controller.signal})
    if(disposed||generation!==token)return
    batch.value=result
    error.value=''
    if(['queued','running'].includes(result.status))timer=setTimeout(()=>poll(id,generation),1200)
    else { refreshRecent().catch(()=>{}); emit('updated') }
  } catch(e){
    if(e.name==='AbortError'||disposed||generation!==token)return
    error.value='批次进度暂时无法读取，正在重新连接。'
    timer=setTimeout(()=>poll(id,generation),2500)
  }
}
async function openBatch(id){
  stop(); batch.value=null; preview.value=null; busy.value=true; error.value=''
  const generation=token
  try {
    const result=await request(`/batches/${id}`)
    if(disposed||generation!==token)return
    batch.value=result; Object.assign(config,result.request); emit('selected-batch',id)
    await nextTick(); resultsPanel.value?.scrollIntoView({block:'start',behavior:'auto'})
    if(['queued','running'].includes(result.status))poll(id,generation)
  }catch(e){if(!disposed&&generation===token)error.value=e.message}
  finally{if(!disposed&&generation===token)busy.value=false}
}
function body(){return {model:config.model,backend:config.backend,seed:config.seed,interval_hours:config.interval_hours,care_setting:config.care_setting,start_time:config.start_time}}
watch(config,()=>{preview.value=null})
async function plan(){
  busy.value=true; error.value=''
  try{ const result=await post('/batches/preview',body()); if(!disposed)preview.value=result }
  catch(e){error.value=e.message}
  finally{busy.value=false}
}
async function generate(){
  if(!preview.value)return
  busy.value=true;error.value=''
  try{
    const result=await post('/batches',{...body(),source_sha256:preview.value.source.source_sha256})
    if(disposed)return
    stop();batch.value=result;preview.value=null;emit('selected-batch',result.id);emit('updated')
    await nextTick(); resultsPanel.value?.scrollIntoView({block:'start',behavior:'auto'})
    poll(result.id,token);await refreshRecent()
  }catch(e){error.value=e.message}
  finally{busy.value=false}
}
function newBatch(){stop();batch.value=null;preview.value=null;error.value='';emit('selected-batch',null)}
async function exportAll(){
  exporting.value=true;error.value=''
  try{await downloadBatch(batch.value.id)}catch(e){error.value=e.message}finally{exporting.value=false}
}
onMounted(async()=>{
  try{const [summary]=await Promise.all([request('/seed-data'),refreshRecent()]);if(disposed)return;source.value=summary;if(props.initialBatchId)await openBatch(props.initialBatchId)}
  catch(e){if(!disposed)error.value=e.message}finally{if(!disposed)loading.value=false}
})
onBeforeUnmount(()=>{disposed=true;stop()})
</script>
<template>
  <div class="batch-workspace">
    <div v-if="error" class="alert" role="alert"><Icon name="warning" :size="18"/><span>{{ error }}</span><button @click="error=''" aria-label="关闭批次提示">×</button></div>
    <section class="card batch-source">
      <div class="panel-heading"><div><h2><Icon name="file" :size="18"/>初始患者来源 · test.json</h2><p>每名患者使用最早的 Single 单时点记录，生成后的时间轨迹为合成数据。</p></div><span class="research-badge">READ ONLY</span></div>
      <p v-if="loading">正在读取来源数据…</p>
      <template v-if="source"><div class="batch-source-stats"><div><strong>{{ source.record_count.toLocaleString() }}</strong><span>原始记录</span></div><div><strong>{{ source.unique_patients }}</strong><span>不同患者</span></div><div><strong>{{ source.eligible_patients }}</strong><span>可用初始患者</span></div><div><strong>{{ source.scenario_counts.neonatal_pneumonia || 0 }} / {{ source.scenario_counts.neonatal_rds || 0 }}</strong><span>肺炎 / RDS 初始患者</span></div></div><details class="batch-import-notes"><summary>导入规则与缺失字段</summary><p v-for="note in source.import_notes" :key="note">{{ note }}</p><p>默认尽量选取肺炎、RDS 各5名；同一批次患者不重复。原始影像仅保留引用，不读取影像像素。</p><small>来源指纹 {{ source.source_sha256 }}</small></details></template>
    </section>

    <section v-if="!batch" class="card batch-settings">
      <div class="panel-heading"><div><h2><Icon name="layers" :size="18"/>生成一个10人批次</h2><p>3名短轨迹 + 5名中轨迹 + 2名长轨迹。每一步均包含医生动作和患者观测。</p></div></div>
      <fieldset :disabled="busy || loading || !source"><div class="batch-form-grid"><ModelSelect v-model="config.model" :models="models"/><label class="field">生成模式<select v-model="config.backend"><option value="api">API 模型</option><option value="demo">离线演示</option></select></label><label class="field">随机种子<input v-model.number="config.seed" type="number" min="0" max="2147483647" step="1"/></label><label class="field">步长间隔<select v-model.number="config.interval_hours"><option v-for="n in [1,3,6,12,24]" :key="n" :value="n">{{ n }} 小时</option></select></label><label class="field">轨迹起始时间<input v-model="config.start_time" type="datetime-local"/></label></div><div class="batch-actions"><button class="button secondary" @click="plan"><Icon name="refresh" :size="16"/>{{ busy ? '正在准备…' : '预览10名患者' }}</button><span>同一来源、种子与设置可复现患者名单和步数。</span></div></fieldset>
      <template v-if="preview"><div class="batch-preview-title"><strong>本次计划 · 10名患者 / {{ preview.total_steps }}步</strong><small>预览只准备初始数据，不调用模型。</small></div><div class="batch-table-scroll"><table class="batch-preview-table"><thead><tr><th>患者 / 来源编号</th><th>初始诊疗路径</th><th>胎龄 / 出生体重</th><th>轨迹分组</th><th>目标步数</th></tr></thead><tbody><tr v-for="p in preview.patients" :key="p.index"><td><b>{{ String(p.index).padStart(2,'0') }}</b> {{ p.request.source_seed.source_patient_id }}<small>原始记录 {{ p.request.source_seed.source_record_id }}</small></td><td>{{ p.request.scenario_id==='neonatal_rds'?'早产儿 RDS':'新生儿肺炎' }}</td><td>{{ p.request.patient.gestational_weeks }}周 / {{ p.request.patient.birth_weight_g }}g</td><td>{{ groupLabels[p.length_group] }}</td><td><strong>{{ p.target_steps }} 步</strong></td></tr></tbody></table></div><div class="batch-submit"><p>{{ config.backend==='demo'?'离线规则演示：不调用大模型。':'API模式：使用所选模型逐步生成，最多同时处理2名患者。' }}</p><button class="button primary" :disabled="busy" @click="generate"><span v-if="busy" class="spinner"></span><Icon v-else name="spark" :size="17"/>开始生成10名患者</button></div></template>
    </section>

    <section v-else ref="resultsPanel" class="card batch-results">
      <div class="panel-heading"><div><h2><Icon name="layers" :size="18"/>批次患者轨迹 <span class="status-pill" :class="batch.status">{{ labels[batch.status] }}</span></h2><p>{{ batch.request.model }} · {{ batch.request.backend==='demo'?'离线规则演示':'API 模型' }} · 随机种子 {{ batch.request.seed }} · {{ formatTime(batch.created_at,true) }}</p></div><div class="batch-toolbar"><button class="button secondary" :disabled="batch.status!=='completed'||exporting" @click="exportAll"><Icon name="download" :size="16"/>导出整批 JSON</button><button v-if="!running" class="button secondary" @click="newBatch">新建批次</button></div></div>
      <div class="batch-progress-summary"><div><strong>{{ batch.completed_patients }}<small> / 10名完成</small></strong><span v-if="batch.failed_patients" class="batch-failed-count">{{ batch.failed_patients }}名失败，可进入病历重试</span></div><span>{{ batch.completed_steps }} / {{ batch.total_steps }} 步</span></div><div class="overview-progress"><span :style="{width:`${batch.completed_steps / batch.total_steps * 100}%`}"></span></div>
      <div class="batch-patient-grid"><button v-for="p in batch.patients" :key="p.id" class="batch-patient-card" @click="emit('open-patient',p.id,batch.id)"><div class="batch-card-top"><span class="batch-patient-index">{{ String(p.batch_index).padStart(2,'0') }}</span><span class="status-pill" :class="p.status"><span v-if="p.status==='running'" class="spinner"></span>{{ labels[p.status] }}</span></div><h3>{{ p.request.source_seed.source_patient_id }}</h3><p>{{ p.request.scenario_id==='neonatal_rds'?'早产儿 RDS':'新生儿肺炎' }} · {{ p.request.patient.gestational_weeks }}周 · {{ p.request.patient.birth_weight_g }}g</p><div class="batch-card-length"><span>{{ groupLabels[p.length_group] }}</span><strong>{{ p.completed_steps }} / {{ p.total_steps }} 步</strong></div><div class="mini-progress"><span :style="{width:`${p.completed_steps / p.total_steps * 100}%`}"></span></div><p v-if="p.error" class="batch-card-error">{{ p.error.message }}</p><div class="batch-card-link">查看患者病历 <Icon name="arrow" :size="15"/></div></button></div>
      <p class="batch-footnote">点击任一患者，查看完整指标趋势、时间节点、医生检查 / 干预、病程记录及单人导出。任务保存在本地，离开页面仍会继续生成。</p>
    </section>

    <section class="card batch-recent"><div class="panel-heading"><h2>最近批次</h2><button class="button secondary" @click="refreshRecent().catch(e=>error=e.message)"><Icon name="refresh" :size="15"/>刷新</button></div><p v-if="!recent.length" class="batch-footnote">还没有批次。先预览患者，再开始生成。</p><button v-for="item in recent" :key="item.id" class="batch-recent-row" :disabled="busy" @click="openBatch(item.id)"><span><strong>{{ item.id.slice(0,8) }}</strong><small>{{ item.request.model }} · {{ item.request.backend==='demo'?'离线演示':'API' }}</small></span><span>{{ formatTime(item.created_at,true) }}</span><span class="status-pill" :class="item.status">{{ labels[item.status] }}</span><span>{{ item.completed_patients }}/10名</span><Icon name="chevron" :size="16"/></button></section>
  </div>
</template>
