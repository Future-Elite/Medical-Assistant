export const phaseLabels = { assessment:'治疗前 · 诊断评估', treatment:'治疗中 · 治疗与监测', discharge:'治疗后 · 治疗结果' }
export const statusLabels = {queued:'排队中',running:'模拟进行中',completed:'模拟已完成',failed:'生成失败',completed_with_errors:'部分任务失败'}
export const terminalLabels = {treatment_completed:'已生成治疗结果',discharged:'已出院',transferred:'已转院',truncated:'病程未完成'}
export const outcomeLabels = {resolved:'已解决',improved:'改善',unresolved:'未解决',unknown:'证据不足',home:'居家',transfer:'转院',ongoing:'继续治疗'}
export const eventLabels = {admission:'初始资料',decision:'医生决策',order:'检查申请',specimen:'采样 / 观察',report:'检查报告',assessment_summary:'入院检查汇总',followup_summary:'针对性复查结果',diagnosis:'工作诊断',treatment:'治疗执行',treatment_review:'治疗复评',treatment_end:'治疗结果汇总',wait:'等待与复评',consult:'会诊',termination:'病程结束',budget_stop:'预算停止',document:'结局文书',rejected:'操作待纠正'}
export const actorLabels = {simulated_doctor:'模拟医生',environment:'患者环境',intake:'来源导入',validator:'执行校验',outcome_model:'结局模型'}
export function hours(value){return `T+${Number((value||0).toFixed(2))}h`}
export function metricSeries(reports,key){return reports.flatMap(r=>{const i=r.items.find(i=>i.key===key&&typeof i.value==='number');return i?[{time:r.collected_at_hours??r.time_hours,value:i.value,eventId:r.event_id}]:[]})}
export function metricGeometry(series){
  if(!series.length)return {points:[],path:'',ticks:[]}
  const times=series.map(s=>s.time),values=series.map(s=>s.value)
  const start=Math.min(...times),end=Math.max(...times),lo=Math.min(...values),hi=Math.max(...values),pad=Math.max((hi-lo)*.2,Math.abs(hi)*.02,.2)
  const points=series.map(p=>({...p,x:start===end?380:48+(p.time-start)/(end-start)*660,y:155-(p.value-lo+pad)/(hi-lo+2*pad)*120}))
  return {points,path:points.map((p,i)=>`${i?'L':'M'}${p.x},${p.y}`).join(' '),ticks:Array.from({length:4},(_,i)=>({x:48+i*220,time:start+(end-start)*i/3})),lo:lo-pad,hi:hi+pad}
}

export const examNames={vitals:'入院生命体征',blood_gas:'血气',infection_panel:'感染指标',nutrition:'营养与体重',bilirubin:'胆红素',chest_xray:'胸部影像',lung_ultrasound:'肺部超声'}
export const metricNames={spo2:'SpO₂',ph:'pH',paco2:'PaCO₂',sao2:'血气sO₂',crp:'CRP',wbc:'白细胞',pct:'PCT',bilirubin:'总胆红素',weight_g:'体重'}
export function workflowProgress(job){
 if(['treatment_completed','discharged','transferred'].includes(job.terminal_status))return 100
 if(!job.workflow)return job.operations/job.request.max_operations*100
 const f=job.workflow
 if(f.care_started_at!=null)return Math.min(95,45+45*f.review_count/f.max_followups)
 return f.assessment_complete?35:f.assessment_order_id?20:5
}
