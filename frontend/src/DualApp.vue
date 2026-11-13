<script setup>
import {computed,onMounted,onBeforeUnmount,reactive,ref,watch,nextTick} from 'vue'
import {request,post,downloadDreamerJob,downloadDreamerBatch} from './api'
import DreamerTrend from './components/DreamerTrend.vue'
import GraphEvidence from './components/GraphEvidence.vue'
import {hours,statusLabels,phaseLabels} from './dreamer-utils'
import './workbench.css'

const health=ref(null),source=ref(null),models=ref([]),cases=ref([]),selected=ref(null),total=ref(0),offset=ref(0)
const page=ref('studio'),job=ref(null),error=ref(''),busy=ref(false),configOpen=ref(false),referenceText=ref('[]')
const history=ref([]),batch=ref(null),recentBatches=ref([]),preview=ref(null),knowledge=ref(null),knowledgeResult=ref(null),searchText=ref('')
const ehrTab=ref('timeline'),phaseFilter=ref('all'),newestFirst=ref(true),selectedStep=ref(''),followSteps=ref(true),filterMetric=ref(''),changedOnly=ref(false)
const agentScroller=ref(null)
const form=reactive({model:'Qwen3.8-27B',policy_model:'Qwen3.8-27B',dynamics_model:'Qwen3.8-27B',outcome_model:'Qwen3.8-27B',
 seed:42,max_cycles:3,decision_hours:12,forecast_horizon_hours:24,max_candidates:3,planning_samples:1,max_hours:72})
let timer,pollBusy=false
const record=computed(()=>job.value?.record),running=computed(()=>['queued','running'].includes(job.value?.status))
const legacy=computed(()=>job.value&&health.value&&job.value.engine_version!==health.value.engine_version)
const sourceChanged=computed(()=>job.value&&source.value&&job.value.source_sha256!==source.value.source_sha256)
const baseline=computed(()=>Object.values(selected.value?.baseline||{}))
const rules=computed(()=>record.value?.objective?.reference_rules||selected.value?.evaluation_preview?.reference_rules||{})
const agentSteps=computed(()=>job.value?.agent_steps||record.value?.agent_steps||[])
const currentScore=computed(()=>record.value?.objective?.current_visible)
const currentStage=computed(()=>({queued:'等待开始',initializing:'读取入院资料',baseline_ready:'入院资料已载入',graph_retrieval:'检索医学图谱',
 graph_query:'补充检索证据',policy_request:'策略模型评估中',clinical_assessment:'核对诊疗指征',proposal:'生成候选方案',
 candidate_evaluation:'校验候选与预测指标',proposing_and_forecasting:'准备本轮决策',forecasting:'联合预测候选方案',
 decision:'已确定本轮方案',executing:'执行并更新患者状态',review_published:'复查结果已发布',summarizing:'整理EHR',completed:'已完成',failed:'已停止'}[job.value?.progress?.stage]||'等待生成'))
const modeLabel=x=>({treatment_required:'需要治疗操作',maintain_current:'维持已执行治疗',observe_without_treatment:'暂不治疗并观察',needs_clinical_review:'需补充临床评估'}[x]||x)
const operation=x=>({start:'开始',adjust:'调整',stop:'停止',continue:'继续已执行治疗',wait:'暂不治疗并观察',order_test:'申请检查'}[x]||x)
const actionText=a=>['wait','continue'].includes(a.operation)?operation(a.operation):operation(a.operation)+' · '+(health.value?.treatments?.[a.target]?.name||health.value?.diagnostic_tests?.[a.target]||a.target)
const statusName=x=>({normal:'范围内',high:'偏高',low:'偏低',unknown:'未知',unassessed:'未设适用范围'}[x]||x)
const valueText=i=>i?.value==null?(i?.comparator?i.comparator+i.bound:'未知'):i.value
const metricName=k=>baseline.value.find(i=>i.key===k)?.name||k
const display={'ph':'pH','paco2':'PaCO2','sao2':'SaO2','spo2':'SpO2','crp':'CRP','pct':'PCT','wbc':'WBC','bilirubin':'总胆红素'}
const metricLabel=i=>{const name=display[i.key]||i.name;return ['', 'unitless'].includes(i.unit||'')?name:name+'('+i.unit+')'}
const baselineMap=computed(()=>Object.fromEntries(baseline.value.map(i=>[metricLabel(i),i.value??(i.comparator?i.comparator+i.bound:null)])))
const labelKeys=computed(()=>Object.fromEntries(baseline.value.map(i=>[metricLabel(i),i.key])))
const updates=computed(()=>job.value?(job.value.dynamics_monitor?.updates||[]):baseline.value.length?[{time_hours:0,state_version:0,labs:baselineMap.value,changes:{}}]:[])
const latestUpdate=computed(()=>updates.value.at(-1))
const monitorRows=computed(()=>Object.entries(latestUpdate.value?.labs||{}).map(([label,value])=>({label,value,delta:latestUpdate.value?.changes?.[label],key:labelKeys.value[label]}))
 .filter(i=>i.label.toLowerCase().includes(filterMetric.value.toLowerCase())&&(!changedOnly.value||typeof i.delta==='number'&&i.delta!==0)))
const monitorReports=computed(()=>updates.value.map(u=>({collected_at_hours:u.time_hours,time_hours:u.time_hours,items:Object.entries(u.labs).map(([label,value])=>({key:label,name:label,unit:'',value:typeof value==='number'?value:null}))})))
const ehrEvents=computed(()=>{const rows=(record.value?.events||[]).filter(e=>!['planning','document'].includes(e.event_type)&&(phaseFilter.value==='all'||e.phase===phaseFilter.value));return newestFirst.value?rows.toReversed():rows})
const pretty=x=>JSON.stringify(x,null,2)
const formatDelta=x=>typeof x==='number'?(x>0?'+':'')+Number(x.toFixed(4)):'—'
const stepStatus=x=>({started:'请求中',completed:'完成',rejected:'未通过',failed:'失败'}[x]||x)

watch(()=>agentSteps.value.at(-1)?.id,async id=>{if(id&&(followSteps.value||!selectedStep.value)){selectedStep.value=id;await nextTick();const container=agentScroller.value,item=container?.lastElementChild;if(followSteps.value&&container&&item)container.scrollTop+=item.getBoundingClientRect().top-container.getBoundingClientRect().top-8}})
function payload(isBatch=false){
 let references;try{references=JSON.parse(referenceText.value)}catch{throw new Error('参考范围必须是JSON数组。')}
 if(!Array.isArray(references))throw new Error('参考范围必须是数组。')
 return {...form,backend:'api',reference_rules:references,case_id:isBatch?'':selected.value?.id,
  max_cycles:isBatch?10:form.max_cycles,max_hours:isBatch?Math.max(form.max_hours,form.decision_hours*10):form.max_hours,
  source_sha256:source.value?.source_sha256}
}
async function loadCases(){const r=await request('/dreamer/cases?offset='+offset.value+'&limit=12');cases.value=r.items;total.value=r.total;if(!selected.value)selected.value=r.items[0]||null}
async function choose(id){selected.value=await request('/dreamer/cases/'+encodeURIComponent(id));job.value=null;selectedStep.value='';localStorage.removeItem('dreamer_dual_last_job')}
async function changePage(delta){offset.value=Math.max(0,offset.value+delta);await loadCases()}
async function openJob(id){job.value=await request('/dreamer/generations/'+id);selected.value=job.value.case;page.value='studio';selectedStep.value=agentSteps.value.at(-1)?.id||'';localStorage.setItem('dreamer_dual_last_job',id)}
async function run(){busy.value=true;error.value='';selectedStep.value='';try{const r=await post('/dreamer/generations',payload());await openJob(r.id)}catch(e){error.value=e.message}finally{busy.value=false}}
async function retry(){busy.value=true;error.value='';try{const r=await post('/dreamer/generations/'+job.value.id+'/retry',{});await openJob(r.id)}catch(e){error.value=e.message}finally{busy.value=false}}
async function fresh(){const id=selected.value?.id;const previousHash=source.value?.source_sha256;job.value=null;selectedStep.value='';error.value='';localStorage.removeItem('dreamer_dual_last_job');try{source.value=await request('/dreamer/source');selected.value=null;await loadCases();if(id&&previousHash===source.value.source_sha256){try{await choose(id)}catch{selected.value=cases.value[0]||null}}}catch(e){error.value=e.message}}
async function nav(target){page.value=target;error.value='';try{if(target==='history')history.value=(await request('/dreamer/generations')).items;if(target==='batch')recentBatches.value=(await request('/dreamer/batches')).items;if(target==='evidence')knowledge.value=await request('/dreamer/knowledge')}catch(e){error.value=e.message}}
async function searchKnowledge(){busy.value=true;error.value='';try{knowledgeResult.value=await post('/dreamer/knowledge/search',{queries:searchText.value?[searchText.value]:[],...(job.value?.record?{generation_id:job.value.id}:selected.value?{case_id:selected.value.id}:{})})}catch(e){error.value=e.message}finally{busy.value=false}}
async function batchPreview(){busy.value=true;error.value='';try{preview.value=await post('/dreamer/batches/preview',payload(true))}catch(e){error.value=e.message}finally{busy.value=false}}
async function batchRun(){busy.value=true;error.value='';try{batch.value=await post('/dreamer/batches',payload(true));preview.value=null}catch(e){error.value=e.message}finally{busy.value=false}}
async function download(format){try{await downloadDreamerJob(job.value.id,format)}catch(e){error.value=e.message}}
async function poll(){if(pollBusy)return;pollBusy=true;try{if(running.value)job.value=await request('/dreamer/generations/'+job.value.id);if(batch.value?.status==='running')batch.value=await request('/dreamer/batches/'+batch.value.id)}catch(e){error.value=e.message}finally{pollBusy=false}}
onMounted(async()=>{timer=setInterval(poll,1200);try{const [h,m,s]=await Promise.all([request('/dreamer/health'),request('/models'),request('/dreamer/source')]);health.value=h;models.value=m.items;source.value=s;await loadCases();const last=localStorage.getItem('dreamer_dual_last_job');if(last){try{await openJob(last)}catch{localStorage.removeItem('dreamer_dual_last_job')}}}catch(e){error.value=e.message}})
onBeforeUnmount(()=>clearInterval(timer))
</script>

<template>
<div class="wm-app">
 <header class="wm-header">
  <a href="/" class="wm-brand">EHR Dreamer</a>
  <nav><button v-for="[id,label] in [['studio','诊疗工作台'],['batch','批量任务'],['evidence','医学证据库'],['history','历史病历']]" :key="id" :class="{active:page===id}" @click="nav(id)">{{ label }}</button></nav>
  <div class="wm-header-right"><span><i class="wm-dot" :class="{online:health}"/>{{ health?'服务已连接':'连接中' }}</span><a href="/docs" target="_blank">API</a></div>
 </header>
 <div v-if="health?.validation_fixture" class="wm-validation">合成界面验证：使用人工病例和模拟模型响应。</div>
 <div v-if="error" class="wm-alert" role="alert">{{ error }}<button @click="error=''">×</button></div>
 <div v-if="health&&!health.api_configured" class="wm-alert">服务端尚未配置模型API，请检查项目本地配置。</div>

 <div v-if="['studio','batch'].includes(page)" class="wm-toolbar">
  <div class="wm-patient-select"><span>患者</span><select :value="selected?.id" :disabled="running||page==='batch'" @change="choose($event.target.value)"><option v-for="c in cases" :key="c.id" :value="c.id">{{ c.label }} · {{ c.phenotype }}</option></select><button class="wm-page-button" :disabled="offset===0||running" @click="changePage(-12)">‹</button><button class="wm-page-button" :disabled="offset+12>=total||running" @click="changePage(12)">›</button></div>
  <div v-if="selected" class="wm-patient-summary"><strong>{{ selected.intake.chief_complaint }}</strong><span>{{ selected.intake.gestational_weeks??'未知' }}周<template v-if="selected.intake.gestational_days!=null">{{ selected.intake.gestational_days }}天</template></span><span>{{ selected.intake.birth_weight_g??'—' }} g</span><span>初始 {{ baseline.length }} 项 · 评分 {{ Object.keys(rules).length }} 项</span></div>
  <div class="wm-toolbar-actions"><button @click="configOpen=!configOpen">{{ configOpen?'收起配置':'运行配置' }}</button><button v-if="job&&!running" @click="fresh">新建</button><button v-if="job?.status==='failed'&&!legacy&&!sourceChanged" @click="retry" :disabled="busy">重试</button><button class="wm-primary" :disabled="busy||running||!selected||!health?.api_configured||legacy||sourceChanged" @click="page==='batch'?batchPreview():run()">{{ busy?'提交中…':running?'生成中…':page==='batch'?'预览10人批次':'开始生成' }}</button></div>
 </div>
 <div v-if="configOpen&&['studio','batch'].includes(page)" class="wm-config">
  <label v-for="[key,label] in [['policy_model','策略模型'],['dynamics_model','动力学模型'],['outcome_model','结局模型']]" :key="key" class="wm-field wm-model-field">{{ label }}<select v-model="form[key]" :disabled="running"><option v-for="m in models" :key="m.id" :value="m.id">{{ m.name }}</option></select><small>{{ models.find(m=>m.id===form[key])?.thinking_policy?.label }}</small></label>
  <label class="wm-field">决策轮次<input v-model.number="form.max_cycles" type="number" min="1" max="10" :disabled="running"/></label>
  <label class="wm-field">决策间隔 / 小时<input v-model.number="form.decision_hours" type="number" min="1" max="48" :disabled="running"/></label>
  <label class="wm-field">预测跨度 / 小时<input v-model.number="form.forecast_horizon_hours" type="number" min="1" max="168" :disabled="running"/></label>
  <label class="wm-field">候选数量<input v-model.number="form.max_candidates" type="number" min="2" max="4" :disabled="running"/></label>
  <label class="wm-field">每方案预测样本<input v-model.number="form.planning_samples" type="number" min="1" max="3" :disabled="running"/></label>
  <label class="wm-field">模拟时长上限 / 小时<input v-model.number="form.max_hours" type="number" min="1" max="720" :disabled="running"/></label>
  <label class="wm-field">随机种子<input v-model.number="form.seed" type="number" min="0" :disabled="running"/></label>
  <label class="wm-field wide">带来源的自定义参考范围 / JSON<textarea v-model="referenceText" rows="2" :disabled="running" spellcheck="false"/></label>
 </div>

 <template v-if="page==='studio'">
  <div v-if="legacy||sourceChanged" class="wm-validation">当前病历的版本或来源文件已更新。历史记录保持原样；点击“新建”载入当前患者资料后生成。</div>
  <div v-if="job?.error" class="wm-alert" role="alert">{{ job.error.message }}</div>
  <div class="wm-grid">
   <section class="wm-panel wm-agent" data-testid="agent-panel">
    <div class="wm-panel-head"><div><h2>策略 Agent 步骤</h2><p>{{ job?.models?.policy?.name||form.policy_model }}</p></div><label class="wm-step-follow"><input type="checkbox" v-model="followSteps"/>跟随最新</label></div>
    <div class="wm-stage-bar"><i v-if="running" class="wm-spin"/>{{ job?currentStage:'尚未开始' }}<span v-if="job" class="wm-muted">· 已完成 {{ job.progress?.cycle||0 }} 轮 · {{ hours(job.time_hours) }}</span></div>
    <div v-if="!agentSteps.length" class="wm-empty">生成后显示实际执行的术语检索、<br/>证据引用、方案校验和决策记录。</div>
    <div v-else class="wm-agent-list" ref="agentScroller">
     <article v-for="step in agentSteps" :key="step.id" class="wm-step" :class="step.status">
      <button class="wm-step-button" :class="{selected:selectedStep===step.id}" @click="selectedStep=selectedStep===step.id?'':step.id;followSteps=false"><span class="wm-step-number">{{ step.sequence }}</span><span><strong>{{ step.title }}</strong><small>{{ hours(step.time_hours) }} · 第{{ step.cycle }}轮</small></span><span class="wm-step-state">{{ stepStatus(step.status) }}</span></button>
      <div v-if="selectedStep===step.id" class="wm-step-body">
       <p v-if="step.summary" class="wm-reason">{{ step.summary }}</p>
       <template v-if="['graph_retrieval','graph_query'].includes(step.stage)">
        <p class="wm-muted">查询：{{ (step.data.effective_queries||step.data.queries||[]).join('、')||'入院综合评估' }}</p><GraphEvidence :context="step.data" compact/>
       </template>
       <template v-else-if="step.stage==='clinical_assessment'"><span class="wm-badge">{{ modeLabel(step.data.mode) }}</span><p>病例依据：{{ step.data.evidence_ids?.join('、') }}</p><div class="wm-tags"><span v-for="id in step.data.guideline_rule_ids" :key="id" class="wm-tag">{{ id }}</span></div><p v-if="step.data.missing_information?.length" class="wm-rejected">待核实：{{ step.data.missing_information.join('、') }}</p></template>
       <template v-else-if="step.stage==='proposal'"><div v-for="plan in step.data.plans" :key="plan.candidate_id" class="wm-plan-card"><strong>{{ plan.candidate_id }}</strong><p v-for="(a,i) in plan.actions" :key="i">{{ hours(step.time_hours+a.offset_hours) }} · {{ actionText(a) }}</p><p>{{ plan.rationale }}</p><small class="wm-muted">拟复查：{{ plan.review_metrics.map(metricName).join('、') }}</small></div></template>
       <template v-else-if="step.stage==='candidate_evaluation'"><p><strong>{{ step.data.candidate_id }}</strong> · {{ step.data.validation_status==='valid'?'符合候选条件':'已排除' }}</p><p v-if="step.data.validation_status==='valid'">终点预计异常 {{ step.data.expected_abnormal_count }} 项<br/>联合预测 {{ step.data.predicted_metric_count }} 项，其中评分 {{ step.data.scored_metric_count }} 项</p><p v-else class="wm-rejected">{{ step.data.error }}</p></template>
       <template v-else-if="step.stage==='decision'"><span class="wm-badge">{{ modeLabel(step.data.clinical_assessment?.mode) }}</span><div class="wm-plan-card"><strong>{{ step.data.selected_plan?.candidate_id }}</strong><p v-for="(a,i) in step.data.selected_plan?.actions" :key="i">{{ hours(step.time_hours+a.offset_hours) }} · {{ actionText(a) }}</p></div><details class="wm-details"><summary>策略最终输出 JSON</summary><pre class="wm-json">{{ pretty(step.data) }}</pre></details></template>
       <template v-else-if="step.stage==='execution'"><p v-for="(a,i) in step.data.actions" :key="i">{{ actionText(a) }}</p></template>
       <template v-else-if="step.stage==='review'"><pre class="wm-json">{{ pretty(step.data.report) }}</pre></template>
       <details v-if="!['decision','review'].includes(step.stage)" class="wm-details"><summary>步骤原始记录</summary><pre class="wm-json">{{ pretty(step.data) }}</pre></details>
      </div>
     </article>
    </div>
   </section>

   <section class="wm-panel wm-ehr" data-testid="ehr-panel">
    <div class="wm-panel-head"><div><h2>患者 EHR</h2><p>{{ job?.case?.label||'检查、治疗与结局记录' }}</p></div><span v-if="job" class="wm-badge" :class="job.status">{{ statusLabels[job.status]||job.status }}</span><div class="wm-head-actions"><button :disabled="!record" @click="download('json')">导出JSON</button><button :disabled="!record" @click="download('csv')">CSV</button></div></div>
    <div class="wm-subtabs"><button :class="{active:ehrTab==='timeline'}" @click="ehrTab='timeline'">时间病历</button><button :class="{active:ehrTab==='json'}" @click="ehrTab='json'">完整JSON</button><template v-if="ehrTab==='timeline'"><button @click="newestFirst=!newestFirst">{{ newestFirst?'最新在前':'最早在前' }}</button><button v-for="[id,label] in [['all','全部'],['assessment','治疗前'],['treatment','治疗中'],['discharge','治疗后']]" :key="id" :class="{active:phaseFilter===id}" @click="phaseFilter=id">{{ label }}</button></template></div>
    <div class="wm-ehr-scroll">
     <div v-if="!record" class="wm-empty">尚无生成病历。</div>
     <pre v-else-if="ehrTab==='json'" class="wm-json" style="max-height:none;margin:0">{{ pretty(record) }}</pre>
     <template v-else>
      <article v-for="e in ehrEvents" :key="e.event_id" class="wm-event"><time>{{ hours(e.time_hours) }} · {{ phaseLabels[e.phase]||e.phase }}</time><h3>{{ e.name }}</h3>
       <p v-if="e.event_type==='admission'">{{ selected?.intake.chief_complaint }} · {{ selected?.intake.gestational_weeks??'未知' }}周 · {{ selected?.intake.birth_weight_g??'—' }} g</p>
       <p v-if="e.value?.rationale">{{ e.value.rationale }}</p>
       <div v-if="e.parameters?.actions" class="wm-event-actions"><span v-for="(a,i) in e.parameters.actions" :key="i">{{ actionText(a) }}</span></div>
       <p v-else-if="e.parameters?.operation">{{ operation(e.parameters.operation) }} · {{ health?.treatments?.[e.target]?.name||e.target }}<span v-if="e.parameters.fio2!=null"> · FiO₂ {{ e.parameters.fio2 }}</span><span v-if="e.parameters.pressure_cm_h2o!=null"> · {{ e.parameters.pressure_cm_h2o }} cmH₂O</span></p>
       <p v-if="e.value?.metric_keys">复查：{{ e.value.metric_keys.map(metricName).join('、') }}</p>
       <div v-if="Array.isArray(e.value)" class="wm-table-wrap"><table class="wm-table"><thead><tr><th>指标</th><th>结果</th><th>单位</th><th>研究评价</th></tr></thead><tbody><tr v-for="i in e.value" :key="i.key"><td>{{ i.name }}</td><td>{{ valueText(i) }}</td><td>{{ i.unit==='unitless'?'—':i.unit }}</td><td><span class="wm-badge" :class="i.status">{{ statusName(i.status) }}</span></td></tr></tbody></table></div>
       <p v-if="e.value?.reason">{{ e.value.reason }}</p><p v-if="e.evidence_ids?.length" class="wm-muted">依据：{{ e.evidence_ids.join('、') }}</p>
       <p v-if="e.value?.result_status==='not_available'" class="wm-muted">待完成 · {{ e.value.note }}</p>
      </article>
      <div v-if="record.discharge_summary&&['all','discharge'].includes(phaseFilter)" class="wm-plan-card"><strong>{{ record.discharge_summary.title }}</strong><p>{{ record.discharge_summary.treatment_result_text }}</p><p class="wm-muted">{{ record.discharge_summary.discharge_status }}</p></div>
     </template>
    </div>
   </section>

   <section class="wm-panel wm-monitor" data-testid="dynamics-panel">
    <div class="wm-panel-head"><div><h2>动力学指标变化</h2><p>{{ job?.models?.dynamics?.name||form.dynamics_model }} · 研究状态视图</p></div><span class="wm-count">{{ Object.keys(latestUpdate?.labs||{}).length }} 项</span></div>
    <div class="wm-monitor-body">
     <div><div class="wm-monitor-summary"><span>{{ job?'最近更新':'入院基线' }} · {{ hours(latestUpdate?.time_hours||0) }}</span><span>{{ updates.length }} 个状态快照</span></div><DreamerTrend v-if="updates.length" :reports="monitorReports"/><div v-else class="wm-empty">当前病历没有动力学状态记录。</div><p class="wm-muted">本区显示模型状态；医生只读取 EHR 中已发布的检查结果。</p><p v-if="currentScore" class="wm-muted">已观测评分 {{ currentScore.evaluated_count }} 项，异常 {{ currentScore.abnormal_count??'未知' }} 项；其余指标仍参与联合预测。<template v-if="currentScore.stale_metric_keys?.length">{{ currentScore.stale_metric_keys.length }}项评分沿用既往测量。</template></p></div>
     <div><div class="wm-monitor-filters"><input type="search" v-model="filterMetric" placeholder="搜索全部指标" aria-label="搜索动力学指标"/><label><input type="checkbox" v-model="changedOnly"/>仅看变化</label></div><div class="wm-monitor-table"><table class="wm-table"><thead><tr><th>指标 / 单位</th><th>当前值</th><th>较上次</th></tr></thead><tbody><tr v-for="i in monitorRows" :key="i.label"><td>{{ i.label }}</td><td>{{ i.value??'未知' }}</td><td class="wm-delta" :class="i.delta>0?'wm-up':i.delta<0?'wm-down':''">{{ formatDelta(i.delta) }}</td></tr></tbody></table></div></div>
    </div>
   </section>
  </div>
 </template>

 <main v-else-if="page==='evidence'" class="wm-main-page">
  <section class="wm-page-card"><div class="wm-page-top"><h2>医学术语与证据检索</h2><span class="wm-muted">{{ knowledge?.node_count||0 }}节点 · {{ knowledge?.edge_count||0 }}关系 · {{ knowledge?.recommendations||0 }}推荐 · PubMed目录 {{ knowledge?.pubmed_discovery?.metadata_records||0 }}条</span></div><form class="wm-search" @submit.prevent="searchKnowledge"><input v-model="searchText" placeholder="输入术语，例如 CPAP、肺表面活性物质、抗感染" aria-label="医学术语查询"/><button class="wm-primary" :disabled="busy">{{ busy?'查询中…':'查询图谱' }}</button></form><p class="wm-muted">按当前病例的已发布资料筛选人群条件。目录中的未核对文献不会自动成为诊疗规则。</p></section>
  <div class="wm-evidence-grid"><section class="wm-page-card"><h2>检索结果与关系路径</h2><GraphEvidence v-if="knowledgeResult" :context="knowledgeResult"/><div v-else class="wm-empty">查询后显示规范术语、推荐、监测项目与来源之间的路径。</div></section><section class="wm-page-card"><h2>指南与重点文献</h2><article v-for="s in knowledge?.sources||[]" :key="s.url" class="wm-source-row"><a :href="s.url" target="_blank" rel="noreferrer">{{ s.title }} ↗</a><small>{{ s.year }} · {{ s.kind }}<span v-if="s.pmid"> · PMID {{ s.pmid }}</span><br/>{{ s.scope }}</small></article></section></div>
 </main>

 <main v-else-if="page==='history'" class="wm-main-page"><section class="wm-page-card"><div class="wm-page-top"><h2>历史病历</h2><button @click="nav('history')">刷新</button></div><button v-for="j in history" :key="j.id" class="wm-history-row" @click="openJob(j.id)"><strong>{{ j.case.label }}</strong><span class="wm-badge" :class="j.status">{{ statusLabels[j.status]||j.status }}</span><span>{{ hours(j.time_hours) }}</span><span>{{ j.created_at?.slice(0,19).replace('T',' ') }} · {{ j.engine_version }}</span></button><div v-if="!history.length" class="wm-empty">暂无保存记录。</div></section></main>

 <main v-else-if="page==='batch'" class="wm-main-page">
  <section v-if="preview" class="wm-page-card"><div class="wm-page-top"><h2>10人批次预览</h2><button class="wm-primary" :disabled="busy" @click="batchRun">确认生成</button></div><div class="wm-batch-grid"><article v-for="p in preview.patients" :key="p.index" class="wm-batch-patient"><h3>{{ p.case.label }}</h3><p>{{ p.case.phenotype }}</p><p>{{ Object.keys(p.case.baseline).length }}项初始指标 · 最多{{ p.max_cycles }}轮</p></article></div></section>
  <section v-if="batch" class="wm-page-card"><div class="wm-page-top"><h2>批次进度 · {{ batch.completed_patients }}/10</h2><button @click="downloadDreamerBatch(batch.id).catch(e=>error=e.message)">导出整批JSON</button></div><div class="wm-batch-grid"><button v-for="p in batch.patients" :key="p.id" class="wm-batch-patient" @click="openJob(p.id)"><span class="wm-badge" :class="p.status">{{ statusLabels[p.status]||p.status }}</span><h3>{{ p.case.label }}</h3><p>{{ p.progress?.cycle||0 }}/{{ p.request.max_cycles }}轮 · {{ hours(p.time_hours) }}</p></button></div></section>
  <section class="wm-page-card"><div class="wm-page-top"><h2>最近批次</h2><button @click="nav('batch')">刷新</button></div><button v-for="b in recentBatches" :key="b.id" class="wm-history-row" @click="request('/dreamer/batches/'+b.id).then(r=>batch=r).catch(e=>error=e.message)"><strong>{{ b.id.slice(0,8) }}</strong><span>{{ b.completed_patients }}/10完成</span><span>{{ statusLabels[b.status]||b.status }}</span></button><div v-if="!recentBatches.length&&!preview" class="wm-empty">点击“预览10人批次”选择患者。</div></section>
 </main>
 <footer class="wm-footer">来源 {{ source?.records??'—' }} 条 · 当前可选 {{ total }} 例<span v-if="job"> · 任务 {{ job.id.slice(0,8) }}</span></footer>
</div>
</template>
