<script setup>
import {computed,ref,watch} from 'vue'
import Icon from './Icon.vue'
import DreamerTrend from './DreamerTrend.vue'
import {phaseLabels,terminalLabels,statusLabels,outcomeLabels,eventLabels,actorLabels,hours,examNames,metricNames,workflowProgress} from '../dreamer-utils'
import {request,post} from '../api'
const props=defineProps({job:Object})
const emit=defineEmits(['export','retry'])
const phase=ref('assessment'),selected=ref(null),research=ref(null),inverse=ref(null),busy=ref(false),error=ref('')
const record=computed(()=>props.job?.record)
const events=computed(()=>record.value?.events||[])
const clinicalEvents=computed(()=>{
  const grouped=new Set((record.value?.orders||[]).filter(o=>o.session_type).map(o=>o.order_id))
  return events.value.filter(e=>e.phase===phase.value&&!['decision','specimen'].includes(e.event_type)&&!(e.event_type==='report'&&grouped.has(e.parent_order_id))&&!(e.event_type==='wait'&&e.phase==='assessment'))
})
const current=computed(()=>clinicalEvents.value.find(e=>e.event_id===selected.value)||clinicalEvents.value.at(-1))
const decision=computed(()=>{
  let event=current.value
  for(let i=0;i<4&&event;i++){
    if(event.event_type==='decision')return event
    event=events.value.find(e=>e.event_id===event.parent_order_id)
  }
  return null
})
const final=computed(()=>record.value?.discharge_summary)
const intake=computed(()=>props.job.case.intake)
const learnedTrace=computed(()=>research.value?.trace.some(t=>t.state.values))
const fmt=value=>value==null?'未估计':Number(value).toFixed(2)
watch(()=>props.job.id,()=>{phase.value='assessment';selected.value=null;research.value=null;inverse.value=null;error.value=''})
async function inspect(){
  if(research.value){research.value=null;return}
  const id=props.job.id;busy.value=true;error.value=''
  try{const result=await request(`/dreamer/generations/${id}/research`);if(props.job.id===id)research.value=result}
  catch(e){if(props.job.id===id)error.value=e.message}finally{busy.value=false}
}
async function inverseAnalysis(){
  const all=record.value?.observations||[]
  const target=['infection_panel','blood_gas','bilirubin','vitals'].find(k=>all.filter(r=>r.target===k).length>=2)
  const reports=all.filter(r=>r.target===target)
  if(reports.length<2){error.value='至少需要两个不同时间的同类检查报告。';return}
  const id=props.job.id;busy.value=true;error.value=''
  try{const result=await post(`/dreamer/generations/${id}/inverse`,{before_event_id:reports[0].event_id,after_event_id:reports.at(-1).event_id});if(props.job.id===id)inverse.value=result}
  catch(e){if(props.job.id===id)error.value=e.message}finally{busy.value=false}
}
</script>
<template>
  <div class="ds-patient-detail">
    <section class="ds-card ds-overview"><div class="ds-panel-head"><div><span class="ds-kicker">PATIENT EPISODE</span><h2>{{ job.case.label }}</h2><p>{{ intake.chief_complaint }}</p></div><span class="ds-status" :class="job.status">{{ terminalLabels[job.terminal_status]||statusLabels[job.status] }}</span></div>
      <div class="ds-four-stats"><div><span>出生胎龄</span><strong>{{ intake.gestational_weeks==null?'待核实':intake.gestational_weeks+'周'+(intake.gestational_days==null?'':`+${intake.gestational_days}天`) }}</strong></div><div><span>初始日龄 / 性别</span><strong>{{ intake.postnatal_age_hours==null?'时点待核实':intake.postnatal_age_hours+'小时' }} · {{ intake.sex }}</strong></div><div><span>已执行操作 / 预算</span><strong>{{ job.operations }} / {{ job.request.max_operations }}</strong></div><div><span>模拟时间</span><strong>{{ hours(job.time_hours) }}</strong></div></div>
      <div class="ds-progress"><i :style="{width:`${workflowProgress(job)}%`}"></i></div><div class="ds-detail-meta"><span>{{ job.learning?(job.request.backend==='demo'?'离线医生与本地机器学习动力学':'API医生与本地机器学习动力学'):'旧版生成结果' }}</span><span>策略 {{ job.models.policy.name }} · 动力学 {{ job.models.dynamics.name }} · 结局 {{ job.models.outcome.name }}</span></div>
    </section>
    <div v-if="job.error" class="ds-alert" role="alert"><Icon name="warning" :size="19"/><div><strong>{{ job.error.message }}</strong><p>已完成事件保留。API失败不会自动替换成演示数据。</p></div><button class="ds-button" @click="emit('retry')">按原配置重试</button></div>
    <div v-if="job.request.backend==='demo'" class="ds-note">{{ job.learning?'医生和结局使用离线控制器，患者状态由本地已训练模型预测；未调用外部大模型。':'此条历史记录使用旧版离线规则，不能作为机器学习模型生成结果。' }}</div>
    <div v-if="job.learning" class="ds-note"><strong>训练产物 {{ job.learning.model_id }} · {{ {train:'训练分区病例，不能作为独立验证',validation:'验证分区病例',test:'独立测试分区病例',not_in_training_sources:'未参与本次训练'}[job.learning.source_partition] }}</strong><p>日级动态支持：{{ job.learning.transition_metrics.join('、') }}。生命体征的初始预测不会被当成已学会的治疗响应。</p></div>
    <div v-else class="ds-note">此记录来自重构前版本；旧版人工公式结果不参与新模型训练和验证。</div>
    <div v-if="job.learning&&!job.workflow" class="ds-note">这条历史记录来自流程修复前版本。新建模拟将使用一次入院检查、治疗计划、有限针对性复查和结果汇总；旧病历不会被追溯改写。</div>
    <section v-if="job.workflow" class="ds-card ds-care-summary"><div class="ds-panel-head"><div><h2>检查、治疗与结果</h2><p>一次入院评估，治疗中按计划复查特定指标，结束后汇总治疗结果。</p></div></div><div class="ds-four-stats"><div><span>入院检查</span><strong>{{ job.workflow.assessment_order_id?'1组':'待执行' }}</strong></div><div><span>治疗动作</span><strong>{{ events.filter(e=>e.event_type==='treatment').length }}项</strong></div><div><span>针对性复查</span><strong>{{ job.workflow.review_count }} / {{ job.workflow.max_followups }}轮</strong></div><div><span>治疗结果</span><strong>{{ job.workflow.trajectory_complete?'已生成':'待汇总' }}</strong></div></div><p class="ds-note" v-if="job.workflow.next_due_hours!=null&&!job.workflow.trajectory_complete">下一次计划复评 {{ hours(job.workflow.next_due_hours) }}；不重复申请完整入院检查。</p></section>
    <DreamerTrend :reports="record?.observations||[]"/>
    <section class="ds-card ds-phase-panel"><div class="ds-panel-head"><div><h2>三阶段住院病历</h2><p>检查、治疗、复评和结局共享同一条患者时间线。</p></div><div class="ds-toolbar"><button class="ds-button" :disabled="job.status!=='completed'" @click="emit('export','json')"><Icon name="download" :size="15"/>JSON</button><button class="ds-button" :disabled="job.status!=='completed'" @click="emit('export','csv')">CSV</button></div></div>
      <div class="ds-phase-tabs" role="tablist" aria-label="病历阶段"><button v-for="(label,key) in phaseLabels" :key="key" role="tab" :aria-selected="phase===key" :class="{active:phase===key}" @click="phase=key;selected=null">{{ label }}<small>{{ job.workflow?(key==='assessment'?'1组入院检查':key==='treatment'?job.workflow.review_count+'轮针对性复查':job.workflow.trajectory_complete?'已汇总':'待汇总'):events.filter(e=>e.phase===key&&!['decision','specimen'].includes(e.event_type)).length }}</small></button></div>
      <template v-if="phase==='discharge'">
        <div v-if="final" class="ds-discharge"><div class="ds-outcome-heading"><Icon name="file" :size="23"/><div><h3>{{ final.title }}</h3><p>{{ final.discharge_status }}</p></div><span class="ds-status">{{ outcomeLabels[final.overall_outcome] }}</span></div><div class="ds-note" v-if="final.disposition==='ongoing'&&final.document_type!=='treatment_outcome_summary'">这是阶段结局记录。任务运行完成不表示患者已经出院或治愈。</div><p v-if="final.document_type==='treatment_outcome_summary'" class="ds-note">本次治疗观察与复评流程已经完成，结果按实际证据记录；不自动等于治愈出院。</p><table class="ds-table"><thead><tr><th>工作诊断</th><th>对应结局</th><th>证据</th></tr></thead><tbody><tr v-for="c in final.per_condition_outcomes" :key="c.condition_id"><td>{{ c.name }}</td><td>{{ outcomeLabels[c.status] }}</td><td>{{ c.evidence_ids.length }}份已发布报告</td></tr><tr v-if="!final.per_condition_outcomes.length"><td colspan="3">当前尚未形成工作诊断，保留未知状态。</td></tr></tbody></table><p v-if="final.treatment_result_text">{{ final.treatment_result_text }}</p><div v-if="final.monitored_indicator_changes?.length" class="ds-table-scroll"><h4>治疗前后复查指标对照</h4><table class="ds-table"><thead><tr><th>指标</th><th>入院基线</th><th>治疗后复查</th><th>单位</th></tr></thead><tbody><tr v-for="item in final.monitored_indicator_changes" :key="item.key"><td>{{ item.name }}</td><td>{{ item.before }}</td><td>{{ item.after }}</td><td>{{ item.unit }}</td></tr></tbody></table></div><div class="ds-discharge-columns"><div><h4>未解决问题</h4><p v-for="t in final.unresolved_issues" :key="t">{{ t }}</p></div><div><h4>后续安排</h4><p v-for="t in final.follow_up_plan" :key="t">{{ t }}</p></div></div><details v-if="final.hospital_course?.length"><summary>检查后采取的治疗与复评经过</summary><p v-for="e in final.hospital_course" :key="e.event_id">{{ hours(e.time_hours) }} · {{ e.name }}</p></details><p class="ds-muted">{{ final.prescription_note }}</p></div>
        <div v-else class="ds-empty"><Icon name="file" :size="27"/><h3>结局尚未生成</h3><p>等待病程结束或明确记录预算停止，不提前填入出院情况。</p></div>
      </template>
      <div v-else-if="clinicalEvents.length" class="ds-event-layout"><div class="ds-event-list" aria-label="阶段事件"><button v-for="event in clinicalEvents" :key="event.event_id" :class="{active:current?.event_id===event.event_id}" @click="selected=event.event_id"><span>{{ hours(event.time_hours) }} <small>{{ eventLabels[event.event_type] }}</small></span><strong>{{ event.name }}</strong><small>{{ actorLabels[event.actor]||event.actor }}</small></button></div><article v-if="current" class="ds-event-record"><div class="ds-event-top"><span class="ds-kicker">EVENT {{ current.sequence }}</span><span class="ds-status">{{ current.status }}</span></div><h3>{{ current.name }}</h3><p class="ds-muted">{{ current.occurred_at }} · {{ actorLabels[current.actor]||current.actor }}</p><div v-if="Array.isArray(current.value)" class="ds-observation-grid"><div v-for="item in current.value" :key="item.key"><span>{{ item.name }}</span><strong>{{ item.value }}<small>{{ item.unit }}</small></strong><small v-if="item.interval90" class="ds-muted">一步校准区间 {{ item.interval90.map(v=>Number(v).toFixed(2)).join('–') }}</small><small v-if="item.prediction_support==='initial_distribution_only'" class="ds-muted">仅支持初始分布</small></div></div><div v-else-if="current.event_type==='admission'" class="ds-intake-summary"><p>主诉：{{ intake.chief_complaint }}</p><p>出生体重：{{ intake.birth_weight_g==null?'待核实':intake.birth_weight_g+'g' }}</p><p>已知围产背景：{{ intake.perinatal_risks.join('、')||'来源未可靠提取' }}</p><p>来源工作表 {{ job.case.sheet }}，第{{ job.case.row }}行。后续检查、治疗和出院字段未作为起点输入。</p></div><div v-else-if="current.event_type==='order'&&current.parameters.tests" class="ds-note"><p>{{ current.parameters.session_type==='admission'?'本次入院检查组一次性下达，报告分别返回。':'本轮只复查治疗计划需要的指标。' }}</p><p v-for="test in current.parameters.tests" :key="test.target"><strong>{{ examNames[test.target]||test.target }}</strong>：{{ test.metrics?.length?test.metrics.map(m=>metricNames[m]||m).join('、'):'入院基线评估' }}</p></div><p v-else class="ds-event-explanation">{{ current.event_type==='treatment'?'治疗操作已记录；只进入有数据支持的日级预测，尚未识别剂量响应和因果疗效。':current.event_type==='order'?'检查申请已记录，结果将按采样和报告时间发布。':current.name }}</p><div v-if="decision" class="ds-note"><strong>医生决策依据</strong><p>{{ decision.name }}</p></div><details v-if="current.parent_order_id||current.evidence_ids.length||Object.keys(current.parameters||{}).length" class="ds-event-audit"><summary>依据与操作记录</summary><p v-if="current.parent_order_id">关联操作：{{ current.parent_order_id }}</p><p v-if="current.evidence_ids.length">证据：{{ current.evidence_ids.join('、') }}</p><pre>{{ JSON.stringify(current.parameters,null,2) }}</pre></details></article></div>
      <div v-else class="ds-empty"><Icon name="clock" :size="27"/><h3>等待这一阶段的记录</h3><p>后台会按医生操作和患者反馈逐步生成。</p></div>
    </section>
    <section v-if="record?.conditions.length" class="ds-card ds-condition-panel"><h2>工作诊断与证据</h2><div v-for="c in record.conditions" :key="c.condition_id" class="ds-condition"><strong>{{ c.name }}</strong><span>{{ c.evidence_ids.length }}项已有证据</span></div></section>
    <section class="ds-card ds-source-detail"><details><summary>初始来源质量与使用边界</summary><p v-for="flag in job.case.quality_flags" :key="flag">{{ flag }}</p><p>18列原文保存在本地来源库。当前模型仅接收白名单初始资料，目标病例的出院诊断、真实治疗过程和治疗结果被隔离。</p><p v-for="note in job.limitations" :key="note">{{ note }}</p></details></section>
    <section class="ds-card ds-research"><div class="ds-panel-head"><div><h2>研究视图</h2><p>内部状态不会传给医生策略；逆动力学候选不写回在线病历。</p></div><div class="ds-toolbar"><button class="ds-button" :disabled="busy" @click="inspect">{{ research?'收起内部演化':'查看内部演化' }}</button><button class="ds-button" :disabled="busy||job.status!=='completed'" @click="inverseAnalysis">逆动力学离线分析</button></div></div><p v-if="error" class="ds-error-text" role="alert">{{ error }}</p><div v-if="research" class="ds-research-body"><div class="ds-table-scroll"><table class="ds-table"><thead><tr><th>模拟时间</th><th>触发操作</th><th>{{ learnedTrace?'预测CRP':'旧肺部负荷' }}</th><th>{{ learnedTrace?'预测PaCO₂':'旧感染负荷' }}</th><th>{{ learnedTrace?'预测总胆红素':'旧喂养状态' }}</th></tr></thead><tbody><tr v-for="(item,i) in research.trace" :key="i"><td>{{ hours(item.time_hours) }}</td><td>{{ item.reason }}</td><td>{{ fmt(item.state.values?item.state.values.crp:item.state.lung) }}</td><td>{{ fmt(item.state.values?item.state.values.paco2:item.state.infection) }}</td><td>{{ fmt(item.state.values?item.state.values.bilirubin:item.state.feeding) }}</td></tr></tbody></table></div><p class="ds-muted">这些是内部预测状态，检查申请后才发布对应快照；一步校准区间不是临床参考范围，也不保证长程覆盖率。策略访问权限：{{ research.policy_has_access?'有':'无' }}。</p></div><div v-if="inverse" class="ds-research-body"><strong>推断候选 · 非真实医嘱</strong><p>{{ inverse.uncertainty }}</p><p v-for="(candidate,i) in inverse.candidates" :key="i">{{ candidate.target }}：{{ candidate.explanation }}</p></div></section>
  </div>
</template>
