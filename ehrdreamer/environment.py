from __future__ import annotations

import copy
import hashlib
import json
import random
from datetime import timedelta, datetime, timezone

from .catalog import CONDITIONS, EXAMS, TREATMENTS, VERSION
from .contracts import AgentView, Decision, Intake
from .dynamics import get_engine, snapshot
from .observation import sample, report_text
from .workflow import BASELINE, initial_state, public_state, refresh_plan, due_tests, interval_for


class ActionRejected(ValueError):
    pass


class PatientEnvironment:
    def __init__(self,intake: Intake,request,episode_id,priors,guidance):
        self.intake=intake;self.request=request;self.episode_id=episode_id;self.priors=priors;self.guidance=guidance
        self.rng=random.Random(request.seed);self.engine=get_engine();self.hidden=self.engine.initial(intake,self.rng)
        self.capabilities=self.engine.capabilities();self.guidance={**guidance,'learned_capabilities':self.capabilities}
        self.time=0.;self.phase='assessment';self.operations=0;self.events=[];self.observations=[];self.conditions=[]
        self.treatments={};self.pending=[];self.orders=[];self.last_receipt=None;self.terminal=None
        self.trace=[]
        self.workflow=initial_state(self.engine,request)
        self.record('admission','入院检查前已知资料','recorded',value=intake.model_dump(mode='json'),actor='intake')
        self._trace('initial')

    def timestamp(self,hours=None):
        return (self.request.start_time+timedelta(hours=self.time if hours is None else hours)).isoformat()

    def record(self,event_type,name,status,*,actor='environment',target='',value=None,parent=None,evidence=(),parameters=None):
        event={'event_id':f'{self.episode_id}:e{len(self.events)+1:04}', 'sequence':len(self.events)+1,
               'phase':self.phase,'time_hours':round(self.time,4),'occurred_at':self.timestamp(),
               'recorded_at':datetime.now(timezone.utc).isoformat(),'available_at':self.timestamp(),
               'event_type':event_type,'name':name,'actor':actor,'target':target,'status':status,'value':value,
               'parent_order_id':parent,'evidence_ids':list(evidence),'parameters':parameters or {},
               'concept_code':None,'synthetic':True,'provenance':{'kind':'simulated','engine_version':VERSION}}
        self.events.append(event);return event

    def _trace(self,reason):
        state=snapshot(self.hidden)
        self.trace.append({'time_hours':self.time,'reason':reason,'state':state,
                           'active_exposures':copy.deepcopy(self.treatments),
                           'state_hash':hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()})

    def view(self):
        return AgentView(intake=self.intake,phase=self.phase,time_hours=self.time,
                         observations=tuple(copy.deepcopy(self.observations)),conditions=tuple(copy.deepcopy(self.conditions)),
                         orders=tuple(copy.deepcopy(self.orders)),active_treatments=tuple(
                             {k:v for k,v in item.items() if k not in ('effective_at','residual')} for item in self.treatments.values()),
                         pending_tests=tuple({'order_id':p['order_id'],'target':p['target'],'due_hours':p['due']} for p in self.pending),
                         last_receipt=copy.deepcopy(self.last_receipt),next_due_hours=min((p['due'] for p in self.pending),default=None),
                         operations_remaining=self.request.max_operations-self.operations,guidance=copy.deepcopy(self.guidance),
                         workflow=copy.deepcopy(public_state(self)))

    def advance_to(self,end):
        end=min(end,self.request.max_hours)
        if end<self.time:raise ActionRejected('模拟时间不能倒退。')
        while self.pending and min(p['due'] for p in self.pending)<=end:
            due=min(p['due'] for p in self.pending)
            self.engine.advance(self.hidden,self.treatments,self.time,due,self.rng,self.intake);self.time=due
            ready=[p for p in self.pending if p['due']==due]
            self.pending=[p for p in self.pending if p['due']!=due]
            for ready_index,pending in enumerate(ready):
                event=self.record('report',report_text(pending['target'],pending['items']),'reported',target=pending['target'],
                                  value=pending['items'],parent=pending['order_id'])
                report={'event_id':event['event_id'],'order_id':pending['order_id'],'target':pending['target'],
                        'collected_at_hours':pending['sampled_at'],'time_hours':self.time,'items':pending['items'],
                        'session_type':pending.get('session_type'),'review_round':pending.get('review_round')}
                self.observations.append(report)
                order=next(o for o in self.orders if o['order_id']==pending['order_id'])
                remaining=any(p['order_id']==pending['order_id'] for p in self.pending) or any(p['order_id']==pending['order_id'] for p in ready[ready_index+1:])
                order['status']='pending' if remaining else 'reported'
                if order['status']=='reported' and pending.get('session_type')=='admission':
                    self.workflow['assessment_complete']=True
                if order['status']=='reported' and pending.get('session_type')=='followup':
                    self.workflow.update(review_pending=False,review_ready=True,plan_confirmed=False,actions_in_review=0,last_review_at=self.time)
                if order['status']=='reported' and pending.get('session_type') in ('admission','followup'):
                    reports=[r for r in self.observations if r['order_id']==pending['order_id']]
                    admission=pending['session_type']=='admission'
                    self.record('assessment_summary' if admission else 'followup_summary',
                                '入院检查结果（汇总）' if admission else f"第{pending['review_round']}轮针对性复查结果",
                                'reported',parent=pending['order_id'],evidence=[r['event_id'] for r in reports],
                                value=[item for r in reports for item in r['items']],
                                parameters={'session_type':pending['session_type'],'review_round':pending.get('review_round')})
        self.engine.advance(self.hidden,self.treatments,self.time,end,self.rng,self.intake);self.time=end

    def latest(self,target):
        return next((o for o in reversed(self.observations) if o['target']==target),None)

    def numeric(self,target):
        report=self.latest(target)
        return {i['key']:i['value'] for i in report['items']} if report else {}

    def order_group(self,tests,decision,session_type,evidence):
        name='入院检查（一次性评估）' if session_type=='admission' else f"治疗第{self.workflow['review_count']}轮针对性复查"
        order=self.record('order',name,'ordered',actor='simulated_doctor',target=session_type+'_assessment',
                          parent=decision['event_id'],evidence=evidence,parameters={'session_type':session_type,'tests':tests,
                          'review_round':self.workflow['review_count'] if session_type=='followup' else None})
        self.orders.append({'order_id':order['event_id'],'target':order['target'],'time_hours':self.time,
                            'status':'pending','session_type':session_type,'tests':tests})
        if session_type=='admission':self.workflow['assessment_order_id']=order['event_id']
        for test in tests:
            target=test['target'];items=self.engine.sample(target,self.hidden,self.treatments,self.rng,self.intake)
            if test.get('metrics'):items=[i for i in items if i['key'] in test['metrics']]
            self.record('specimen',EXAMS[target]['name']+' · 采样/观察','collected',target=target,parent=order['event_id'])
            self.pending.append({'order_id':order['event_id'],'target':target,'sampled_at':self.time,
                                 'due':self.time+EXAMS[target]['delay'],'items':items,'session_type':session_type,
                                 'review_round':self.workflow['review_count'] if session_type=='followup' else None})
        self.advance_to(self.time)
        return order

    def discharge_blockers(self):
        reasons=[];vs=[o for o in self.observations if o['target']=='vitals']
        if not {'spo2','respiratory_rate','temperature'}<=set(self.capabilities['transition_metrics']):
            reasons.append('当前真实数据不支持完整生命体征动态，不能自动确认临床出院')
        if not self.conditions:reasons.append('尚无有证据支持的工作诊断')
        if len(vs)<2 or vs[-1]['time_hours']-vs[-2]['time_hours']<6:reasons.append('缺少间隔至少6模拟小时的稳定复评')
        for report in vs[-2:]:
            values={i['key']:i['value'] for i in report['items']}
            if values['spo2']<93 or values['respiratory_rate']>65 or not 36.3<=values['temperature']<=37.8:
                reasons.append('最近可见生命体征未通过研究版稳定性门控');break
        if not vs or self.time-vs[-1]['time_hours']>12:reasons.append('生命体征复评不够新')
        if any(k in self.treatments for k in ('oxygen','cpap')):reasons.append('仍在进行呼吸支持')
        if any(any(i['key']=='cpap' and i['value']>0 or i['key']=='fio2' and i['value']>.21 for i in o['items']) for o in vs[-2:]):
            reasons.append('尚无连续空气下的复评证据')
        if self.numeric('nutrition').get('feeding',0)<.7:reasons.append('缺少喂养耐受恢复证据')
        if self.pending:reasons.append('存在未发布的检查报告')
        if any(c['code'] in ('pneumonia','infection_risk') for c in self.conditions) and self.numeric('infection_panel').get('crp',100)>18:
            reasons.append('感染相关复评仍需处理')
        if any(c['code']=='jaundice' for c in self.conditions):
            bilirubin=[o for o in self.observations if o['target']=='bilirubin']
            if self.intake.postnatal_age_hours is None:
                reasons.append('黄疸评估所需出生后时点未知，当前模块不能自动确认出院')
            if len(bilirubin)<2 or self.time-bilirubin[-1]['time_hours']>12:
                reasons.append('黄疸缺少近期连续复查证据')
            elif bilirubin[-1]['items'][0]['value']>=bilirubin[0]['items'][0]['value']:
                reasons.append('黄疸复查尚无改善证据')
        return list(dict.fromkeys(reasons))

    def validate(self,action):
        known={e['event_id'] for e in self.events}
        if any(identifier not in known for identifier in action.evidence_ids):raise ActionRejected('引用了不存在或尚未可见的证据。')
        target=action.target;kind=action.action_type
        if self.terminal:raise ActionRejected('当前治疗轨迹已经结束。')
        flow=self.workflow
        if kind=='order_assessment':
            if flow['assessment_order_id'] or self.orders:raise ActionRejected('入院检查只下达一次；已有报告应直接用于诊断。')
        if kind=='order_followup':
            if not flow['assessment_complete'] or flow['care_started_at'] is None:
                raise ActionRejected('需先完成入院评估并执行治疗，不能直接循环复查。')
            if self.pending or not flow['plan_confirmed'] or flow['review_ready']:
                raise ActionRejected('需等待当前结果并完成治疗复评，才能进入下一轮复查。')
            if flow['review_count']>=flow['max_followups']:raise ActionRejected('计划复查轮次已完成，应汇总治疗结果。')
            due={t['target']:set(t['metrics']) for t in due_tests(self)}
            if not action.followup_tests or len({t.target for t in action.followup_tests})!=len(action.followup_tests):
                raise ActionRejected('需明确不重复的针对性复查项目。')
            for test in action.followup_tests:
                if len(set(test.metrics))!=len(test.metrics):raise ActionRejected('同一轮复查不能重复列出同一指标。')
                if test.target not in due or not set(test.metrics)<=due[test.target]:
                    raise ActionRejected('该项目尚未到复评时点或不属于当前治疗的监测指标。')
                last=self.latest(test.target)
                if last and self.time-last['collected_at_hours']<self.capabilities['time_resolution_hours']:
                    raise ActionRejected('当前模型尚未推进至新的日级状态，不能重复采样同一结果。')
        if kind=='review_treatment':
            if flow['care_started_at'] is None or self.pending:raise ActionRejected('治疗复评需要已有治疗且本轮报告已发布。')
        if kind=='finish_treatment':
            if flow['care_started_at'] is None or not flow['assessment_complete'] or not self.conditions or self.pending:
                raise ActionRejected('需先完成检查、诊断、治疗和本轮报告，再生成治疗结果。')
            if flow['monitoring'] and flow['review_count']==0:raise ActionRejected('当前治疗尚未完成一次计划内的针对性复查。')
            if not flow['monitoring'] and self.time<(flow['next_due_hours'] or self.time):
                raise ActionRejected('当前观察计划尚未到结束时点。')
        if kind=='order_exam':
            if target not in EXAMS:raise ActionRejected('检查项目不在当前环境支持目录中。')
            if target not in self.capabilities['supported_exams']:raise ActionRejected('该检查缺少足够真实训练标签，不能生成结果。')
            if any(p['target']==target for p in self.pending):raise ActionRejected('同类检查仍待报，请先等待报告。')
            if flow['care_started_at'] is not None or flow['assessment_order_id']:
                raise ActionRejected('入院检查已成组下达；治疗期必须使用绑定治疗计划的针对性复查。')
            if self.latest(target):raise ActionRejected('该入院检查已完成，不能重复申请。')
        if kind=='diagnose':
            if flow['assessment_order_id'] and not flow['assessment_complete']:raise ActionRejected('请先等待入院检查组报告完整发布。')
            if not action.diagnosis or not action.evidence_ids:raise ActionRejected('诊断需要诊断编码和已发布检查证据。')
            report_ids={o['event_id'] for o in self.observations}
            if not any(e in report_ids for e in action.evidence_ids):raise ActionRejected('诊断不能仅引用起点背景或医嘱。')
            if action.diagnosis in ('pneumonia','rds') and not (self.latest('lung_ultrasound') or self.latest('chest_xray')):
                raise ActionRejected('当前工作路径要求先取得肺部报告。')
            if action.diagnosis=='rds' and (self.intake.gestational_weeks is None or self.intake.gestational_weeks>=37):
                raise ActionRejected('当前RDS模块只覆盖孕周已知的早产病例。')
            if action.diagnosis=='jaundice' and not self.latest('bilirubin'):
                raise ActionRejected('黄疸评估需要独立的胆红素检查证据。')
        if kind in ('start_treatment','adjust_treatment','stop_treatment'):
            if target not in TREATMENTS:raise ActionRejected('治疗方案不在当前环境支持目录中。')
            if kind=='start_treatment' and target in self.treatments:raise ActionRejected('该方案已生效，应选择调整或停止。')
            if kind!='start_treatment' and target not in self.treatments:raise ActionRejected('该方案尚未开始。')
            if kind!='stop_treatment':
                if not self.latest('vitals'):raise ActionRejected('执行前需先完成生命体征评估。')
                if target in ('oxygen','cpap'):
                    if action.parameters.fio2 is None:raise ActionRejected('呼吸支持必须明确FiO₂。')
                    if target=='cpap' and action.parameters.pressure_cm_h2o is None:raise ActionRejected('CPAP必须明确压力和单位。')
                if target=='antimicrobial' and not self.latest('infection_panel'):raise ActionRejected('当前模块需先完成感染评估。')
                if target=='surfactant' and (not any(c['code']=='rds' for c in self.conditions) or not self.intake.birth_weight_g):
                    raise ActionRejected('当前方案需要RDS工作诊断和可靠出生体重；未知时不能执行。')
                if target=='phototherapy' and not self.latest('bilirubin'):raise ActionRejected('光疗前缺少胆红素检查。')
        if kind=='propose_discharge':
            blockers=self.discharge_blockers()
            if blockers:raise ActionRejected('；'.join(blockers))
        if kind=='propose_transfer' and not self.latest('vitals'):raise ActionRejected('转院前需先记录可见病情评估。')

    def execute(self,action: Decision,dynamics):
        """Atomic state/RNG/event commit. Failed model inference cannot partially execute."""
        fields=('hidden','time','phase','operations','events','observations','conditions','treatments','pending','orders','last_receipt','terminal','trace','workflow')
        backup={key:copy.deepcopy(getattr(self,key)) for key in fields};rng_state=self.rng.getstate()
        try:
            self.validate(action)
            kind,target=action.action_type,action.target
            prepared=None
            if kind in ('start_treatment','adjust_treatment'):
                prepared=dynamics.prepare_action(snapshot(self.hidden),copy.deepcopy(self.treatments),action,self.intake)
            self.operations+=1
            decision=self.record('decision',action.rationale,'validated',actor='simulated_doctor',target=target,
                                 evidence=action.evidence_ids,parameters={'action_type':kind})
            receipt={'action_type':kind,'target':target,'status':'executed','observation':None,'decision_event_id':decision['event_id']}
            if kind in ('order_assessment','order_followup'):
                if kind=='order_assessment':
                    tests=[{'target':t,'metrics':[]} for t in BASELINE if t in self.capabilities['supported_exams']]
                    session_type='admission'
                else:
                    tests=[t.model_dump(mode='json') for t in action.followup_tests];session_type='followup'
                    self.workflow['review_count']+=1
                    self.workflow.update(review_pending=True,review_ready=False)
                    for test in tests:
                        plan=self.workflow['monitoring'][test['target']]
                        plan['next_due_hours']=self.time+interval_for(self.workflow,plan['metrics'],self.capabilities['time_resolution_hours'])
                order=self.order_group(tests,decision,session_type,action.evidence_ids)
                receipt.update(status='pending' if any(p['order_id']==order['event_id'] for p in self.pending) else 'reported',
                               order_id=order['event_id'],session_type=session_type)
            elif kind=='order_exam':
                order=self.record('order',EXAMS[target]['name'],'ordered',actor='simulated_doctor',target=target,parent=decision['event_id'])
                self.orders.append({'order_id':order['event_id'],'target':target,'time_hours':self.time,'status':'pending'})
                self.record('specimen',EXAMS[target]['name']+' · 采样/观察','collected',target=target,parent=order['event_id'])
                self.pending.append({'order_id':order['event_id'],'target':target,'sampled_at':self.time,
                                     'due':self.time+EXAMS[target]['delay'],'items':self.engine.sample(target,self.hidden,self.treatments,self.rng,self.intake)})
                self.advance_to(self.time)
                receipt['status']='reported' if self.latest(target) and self.latest(target)['order_id']==order['event_id'] else 'pending'
                if receipt['status']=='reported':receipt['observation']=copy.deepcopy(self.latest(target))
            elif kind=='diagnose':
                existing=next((c for c in self.conditions if c['code']==action.diagnosis),None)
                event=self.record('diagnosis',CONDITIONS[action.diagnosis],action.certainty,actor='simulated_doctor',
                                  target=action.diagnosis,parent=decision['event_id'],evidence=action.evidence_ids)
                condition={'condition_id':existing['condition_id'] if existing else event['event_id'], 'code':action.diagnosis,
                           'name':CONDITIONS[action.diagnosis],'certainty':action.certainty,'evidence_ids':list(action.evidence_ids)}
                if existing:self.conditions[self.conditions.index(existing)]=condition
                else:self.conditions.append(condition)
                # Diagnoses stay in the initial assessment until care actually starts.
            elif kind in ('start_treatment','adjust_treatment','stop_treatment'):
                self.phase='treatment'
                if kind=='stop_treatment':del self.treatments[target]
                else:
                    params=action.parameters.model_dump()
                    params['review_after_hours']=params.pop('duration_hours')
                    started=self.treatments.get(target,{}).get('started_at',self.time)
                    self.treatments[target]={'target':target,'started_at':started,'effective_at':self.time,
                                             'learned_action_support':prepared,**params}
                self.record('treatment',TREATMENTS[target]['name'],'stopped' if kind=='stop_treatment' else 'executed',
                            actor='simulated_doctor',target=target,parent=decision['event_id'],evidence=action.evidence_ids,
                            parameters={**action.parameters.model_dump(),'operation':kind,'dose_status':'研究级抽象干预，未表示个体药物剂量'})
                self.events[-1]['parameters']['review_after_hours']=self.events[-1]['parameters'].pop('duration_hours')
                self.events[-1]['parameters']['exposure_model']='documented_action_covariate'
                self.orders.append({'order_id':decision['event_id'],'target':target,'time_hours':self.time,'kind':'treatment',
                                    'status':'stopped' if kind=='stop_treatment' else 'executed','operation':kind})
                if self.workflow['care_started_at'] is None:self.workflow['care_started_at']=self.time
                self.workflow['actions_in_review']+=1
                refresh_plan(self)
            elif kind=='review_treatment':
                self.workflow.update(plan_confirmed=True,review_ready=False,actions_in_review=0)
                refresh_plan(self)
                self.record('treatment_review','确认治疗计划' if self.workflow['review_count']==0 else f"第{self.workflow['review_count']}轮治疗复评",
                            'completed',actor='simulated_doctor',parent=decision['event_id'],evidence=action.evidence_ids,
                            value={'monitoring':copy.deepcopy(self.workflow['monitoring']),'next_due_hours':self.workflow['next_due_hours']})
            elif kind=='finish_treatment':
                self.terminal='treatment_completed';self.phase='discharge';self.workflow['outcome_reason']=action.rationale
                self.record('treatment_end','治疗观察结束，生成治疗结果','completed',actor='simulated_doctor',
                            parent=decision['event_id'],evidence=action.evidence_ids,
                            value={'followup_rounds':self.workflow['review_count'],'active_treatments':list(self.treatments),
                                   'clinical_discharge_confirmed':False})
            elif kind=='wait':
                end=min(self.time+action.parameters.duration_hours,self.request.max_hours)
                if self.pending:
                    due=max(p['due'] for p in self.pending) if action.parameters.wait_for_all_reports else min(p['due'] for p in self.pending)
                    end=min(end,due)
                self.advance_to(end);self.record('wait','等待入院检查报告' if self.phase=='assessment' else '按治疗计划观察至复评时点','executed',parent=decision['event_id'])
            elif kind=='consult':self.record('consult','专科会诊/诊疗能力评估','executed',actor='simulated_doctor',parent=decision['event_id'])
            elif kind in ('propose_discharge','propose_transfer'):
                self.terminal='discharged' if kind=='propose_discharge' else 'transferred';self.phase='discharge'
                self.record('termination','出院条件通过' if kind=='propose_discharge' else '转院交接','accepted',parent=decision['event_id'])
            self.last_receipt=receipt;self._trace(kind)
            return receipt
        except Exception:
            for key,value in backup.items():setattr(self,key,value)
            self.rng.setstate(rng_state)
            raise

    def reject(self,action,reason):
        self.operations+=1
        event=self.record('rejected',str(reason),'rejected',actor='validator',target=action.target,
                          parameters={'action_type':action.action_type})
        self.last_receipt={'action_type':action.action_type,'target':action.target,'status':'rejected','observation':None,
                           'reason':str(reason),'decision_event_id':event['event_id']}

    def finish_budget(self,reason='operation_budget'):
        self.terminal='truncated';self.phase='discharge'
        labels={'operation_budget':'动作预算已到，病程未自动结束','time_budget':'模拟时间上限已到，病程未自动结束',
                'policy_rejections':'医生操作连续未通过校验，保留当前病程并停止'}
        self.record('budget_stop',labels[reason],'truncated',value={'stop_reason':reason,'discharge_blockers':self.discharge_blockers()})

    def public_record(self):
        return {'schema_version':'ehrdreamer-2.1','synthetic':True,'episode_id':self.episode_id,
                'patient':{'synthetic_id':self.episode_id,'intake':self.intake.model_dump(mode='json')},
                'encounter':{'started_at':self.request.start_time.isoformat(),'elapsed_hours':self.time,
                             'terminal_status':self.terminal,'phase':self.phase,'complete_episode':self.terminal in ('discharged','transferred'),
                             'treatment_trajectory_complete':self.terminal in ('treatment_completed','discharged','transferred')},
                'conditions':copy.deepcopy(self.conditions),'orders':copy.deepcopy(self.orders),
                'events':copy.deepcopy(self.events),'observations':copy.deepcopy(self.observations),
                'learning':self.capabilities,'workflow':copy.deepcopy(public_state(self)),'discharge_summary':None}
