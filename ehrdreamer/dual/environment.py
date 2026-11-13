from __future__ import annotations

import copy
from datetime import timedelta
from .catalog import CONDITIONS, TREATMENTS
from . import VERSION
from .references import score, classification
from .dynamics import apply_action, DynamicsError
from .protocol import labs_json


class PatientEnvironment:
    def __init__(self, case, request, episode_id, rules, guidance):
        self.request=request; self.episode_id=episode_id; self.rules=copy.deepcopy(rules)
        self.context={**copy.deepcopy(case['intake']), 'guideline_rules':guidance.get('rules',[])}
        self.intake=copy.deepcopy(case['intake']); self.time=0.; self.cycle=0; self.operations=0
        self.phase='assessment'; self.terminal=None; self.events=[]; self.observations=[]
        self.orders=[]; self.ledger=[]; self.active={}; self.samples={}; self.conditions=[]; self.planning=[]; self.trace=[]
        self.agent_steps=[]; self.dynamics_updates=[]
        self.state={'clock_hours':0., 'version':0, 'baseline':copy.deepcopy(case['baseline']), 'labs':copy.deepcopy(case['baseline'])}
        self.record('admission','真实初始化验与入院背景','recorded',value=self.intake,actor='intake')
        order=self.record('order','已有入院化验（一次基线）','reported',actor='intake')
        self.orders.append({'order_id':order['event_id'],'target':'baseline','session_type':'admission','status':'reported'})
        self.collect(list(self.state['labs']),sample_id='baseline',session_type='admission')
        self.phase='treatment'
        self._trace('baseline')

    def record(self,kind,name,status,*,actor='environment',value=None,target='',parent=None,evidence=(),parameters=None,at_hours=None):
        at = self.time if at_hours is None else at_hours
        event={'event_id':f'{self.episode_id}:e{len(self.events)+1:04}', 'sequence':len(self.events)+1,
               'phase':self.phase,'time_hours':at,
               'occurred_at':(self.request.start_time+timedelta(hours=at)).isoformat(),
               'event_type':kind,'name':name,'actor':actor,'target':target,'status':status,'value':value,
               'parameters':parameters or {},'evidence_ids':list(evidence),'parent_order_id':parent,
               'synthetic':kind!='admission','provenance':{'engine_version':VERSION}}
        self.events.append(event)
        return event

    def _trace(self,reason):
        self.trace.append({'time_hours':self.time,'reason':reason,'state':copy.deepcopy(self.state),
                           'active_exposures':copy.deepcopy(self.active),'ledger':copy.deepcopy(self.ledger)})
        current=labs_json(self.state['labs'])
        previous=self.dynamics_updates[-1]['labs'] if self.dynamics_updates else {}
        self.dynamics_updates.append({'time_hours':self.time,'state_version':self.state['version'],
            'reason':reason,'labs':current,'changes':{k:(v-previous[k] if isinstance(v,(int,float)) and isinstance(previous.get(k),(int,float)) else None)
                                                  for k,v in current.items()}})

    def log_agent(self,stage,title,summary='',data=None,status='completed',cycle=None):
        self.agent_steps.append({'id':f'{self.episode_id}:a{len(self.agent_steps)+1:04}',
            'sequence':len(self.agent_steps)+1,'time_hours':self.time,'cycle':self.cycle+1 if cycle is None else cycle,
            'stage':stage,'title':title,'summary':summary,'status':status,'data':copy.deepcopy(data or {})})

    def view(self):
        return {'intake':copy.deepcopy(self.intake),'phase':self.phase,'time_hours':self.time,
                'observations':copy.deepcopy(self.observations),'conditions':copy.deepcopy(self.conditions),
                'active_treatments':list(copy.deepcopy(self.active).values()),'executed_actions':copy.deepcopy(self.ledger),
                'pending_tests':[{'sample_id':s['sample_id'],'available_at_hours':s['available_at_hours']}
                                 for s in self.samples.values() if not s['published']]+
                                [{'order_id':o['order_id'],'target':o['target'],'status':'pending_not_simulated'}
                                 for o in self.orders if o.get('session_type')=='diagnostic' and o['status']=='pending'],
                'remaining_cycles':self.request.max_cycles-self.cycle}

    def belief_from_visible(self):
        labs=copy.deepcopy(self.state['baseline'])
        for report in self.observations:
            for item in report['items']:
                labs[item['key']]={**copy.deepcopy(item),'as_of_hours':report['collected_at_hours']}
        return {'clock_hours':self.time,'version':self.cycle,'baseline':copy.deepcopy(self.state['baseline']),
                'labs':labs,'state_kind':'visible_anchors_not_actual_hidden_state'}

    def _publish(self):
        for sample in self.samples.values():
            if sample['published'] or sample['available_at_hours'] > self.time:
                continue
            items=copy.deepcopy(sample['items'])
            event=self.record('assessment_summary' if sample['session_type']=='admission' else 'followup_summary',
                              '入院基线化验汇总' if sample['session_type']=='admission' else f'第{self.cycle}轮针对性复查结果',
                              'reported',value=items,parent=sample.get('order_id'),at_hours=sample['available_at_hours'])
            self.observations.append({'event_id':event['event_id'],'order_id':sample.get('order_id'),
                'sample_id':sample['sample_id'],'target':'baseline' if sample['session_type']=='admission' else 'targeted_labs',
                'session_type':sample['session_type'],'time_hours':sample['available_at_hours'],'collected_at_hours':sample['collected_at_hours'],
                'items':items})
            sample['published']=True
            if sample.get('order_id'):
                next(o for o in self.orders if o['order_id']==sample['order_id'])['status']='reported'

    def collect(self,keys,*,sample_id,delay=0,session_type='followup'):
        if sample_id in self.samples:
            return copy.deepcopy(self.samples[sample_id])
        if not keys or len(set(keys))!=len(keys) or not set(keys)<=set(self.state['labs']) or delay<0:
            raise DynamicsError('采样指标、重复项目或报告延迟无效。')
        if session_type!='admission' and any(not s['published'] for s in self.samples.values()):
            raise DynamicsError('有同轮待报检查，不能重复申请。')
        # A second sample at the exact same state cannot masquerade as a new review.
        if session_type!='admission' and any(s['collected_at_hours']==self.time and set(keys)<=set(i['key'] for i in s['items']) for s in self.samples.values()):
            raise DynamicsError('同一时刻已有这些指标的采样，请读取原报告。')
        order_id=self.orders[0]['order_id'] if session_type=='admission' else None
        if session_type!='admission':
            event=self.record('order','治疗后针对性检验申请','ordered',actor='simulated_doctor',value={'metric_keys':keys})
            order_id=event['event_id']
            self.orders.append({'order_id':order_id,'target':'targeted_labs','session_type':'followup','status':'pending'})
        items=[]
        for key in keys:
            item=copy.deepcopy(self.state['labs'][key])
            item['status']=classification(item,self.rules.get(key));item['reference']=copy.deepcopy(self.rules.get(key))
            items.append(item)
        sample={'sample_id':sample_id,'order_id':order_id,'collected_at_hours':self.time,
                'available_at_hours':self.time+delay,'items':items,'published':False,'session_type':session_type}
        self.samples[sample_id]=sample
        self._publish()
        return copy.deepcopy(sample)

    def read_sample(self,sample_id):
        sample=self.samples[sample_id]
        return copy.deepcopy(sample['items']) if sample['published'] else None

    def advance_to(self,end,dynamics):
        if end<self.time or end>self.request.max_hours:
            raise DynamicsError('时间推进不符合模拟预算。')
        proposed=dynamics.transition(self.state,self.context,self.ledger,end,
                    branch_id=f'execution:{self.episode_id}',mode='execution',visible_history=self.observations)
        if not score(proposed['labs'],self.rules)['complete']:
            raise DynamicsError('实际状态转移缺少可评估指标，未提交患者状态。')
        self.state=proposed;self.time=end;self._publish();self._trace('time_advance')

    def execute_first(self,plan,evaluations,rationale,diagnoses,dynamics):
        backup=copy.deepcopy(self.__dict__)
        try:
            first=plan.actions[0]
            current_actions=[a for a in plan.actions if a.offset_hours==0]
            # Review before the next hypothetical action instead of silently
            # letting the first intervention run past that planned boundary.
            window=min([self.request.decision_hours,plan.reassess_after_hours or self.request.decision_hours]+[a.offset_hours for a in plan.actions if a.offset_hours>0])
            end=min(self.time+window,self.request.max_hours)
            decision=self.record('decision','执行本轮诊疗决策','selected',actor='simulated_doctor',
                value={'selected_plan_id':plan.candidate_id,'rationale':rationale},
                parameters={'actions':[a.model_dump(mode='json') for a in current_actions]},
                evidence=tuple(dict.fromkeys(i for a in current_actions for i in a.evidence_ids)))
            for code in diagnoses:
                if not any(c['code']==code for c in self.conditions):
                    self.conditions.append({'condition_id':f'{self.episode_id}:c{len(self.conditions)+1}',
                                             'code':code,'name':CONDITIONS[code],'certainty':'working'})
            for action in current_actions:
                if action.operation=='order_test':
                    if any(o['target']==action.target and o['status']=='pending' for o in self.orders):
                        raise DynamicsError('同一培养或影像仍待完成，不能重复申请。')
                    event=self.record('order','病原学培养申请' if action.target=='culture' else '肺部影像评估申请',
                        'pending',actor='simulated_doctor',target=action.target,parent=decision['event_id'],
                        value={'result_status':'not_available','note':'当前数值动力学没有该项目初态，不生成培养或影像结果。'})
                    self.orders.append({'order_id':event['event_id'],'target':action.target,'session_type':'diagnostic','status':'pending'})
                elif action.operation not in ('wait','continue'):
                    event=self.record('treatment',TREATMENTS[action.target]['name'],'executed',actor='simulated_doctor',
                        target=action.target,value=None,parent=decision['event_id'],parameters={'operation':action.operation,**action.parameters.model_dump(exclude_none=True)})
                    apply_action(self.active,self.ledger,action,self.time,event['event_id'])
                else:
                    if action.operation=='continue' and not self.active:
                        raise DynamicsError('尚无活动治疗，不能执行维持当前方案。')
                    self.record('treatment_review','继续已执行的治疗' if action.operation=='continue' else '依据风险评估观察，暂不启动治疗',
                                'confirmed',actor='simulated_doctor',value=None)
            self.log_agent('execution','执行当前时间点的动作',rationale,{'actions':[a.model_dump(mode='json') for a in current_actions]})
            self.planning.append({'cycle':self.cycle+1,'time_hours':self.time,'selected_plan_id':plan.candidate_id,
                                  'rationale':rationale,'evaluations':copy.deepcopy(evaluations)})
            self.advance_to(end,dynamics)
            self.cycle+=1;self.operations+=1
            self.record('wait','观察时间推进' if not self.ledger else '已执行治疗及病程时间推进','completed',parameters={'to_hours':end})
            # Forecast scoring already uses the full fixed objective set. Clinical
            # sampling follows the plan, not a forced blood draw of every score key.
            keys=list(dict.fromkeys(plan.review_metrics))
            self.collect(keys,sample_id=f'followup:{self.cycle}')
            self.record('treatment_review','复查已发布，下一轮根据新证据重新规划','completed',actor='simulated_doctor')
            self.log_agent('review','读取本轮复查报告','未复查的指标仍保留其原采样时间。',
                           {'report':{'id':self.observations[-1]['event_id'],'at_hours':self.time,
                                      'labs':labs_json({i['key']:i for i in self.observations[-1]['items']})}},cycle=self.cycle)
        except BaseException:
            self.__dict__.clear();self.__dict__.update(backup)
            raise

    def visible_score(self):
        labs=self.belief_from_visible()['labs']
        return {**score(labs,self.rules),'observed_at_hours':{k:labs[k].get('as_of_hours',0) for k in self.rules},
                'stale_metric_keys':[k for k in self.rules if labs[k].get('as_of_hours',0)<self.time]}

    def finish(self,reason):
        self.phase='discharge';self.terminal='treatment_completed'
        self.record('treatment_end','治疗后情况与指标目标评估','completed',value={'reason':reason,'clinical_discharge_confirmed':False})

    def public_record(self):
        goal_history=[]
        labs=copy.deepcopy(self.state['baseline'])
        for report in self.observations:
            labs.update({i['key']:i for i in report['items']})
            goal_history.append({'time_hours':report['collected_at_hours'],'report_event_id':report['event_id'],**score(labs,self.rules)})
        return {'schema_version':'ehrdreamer-4.0','synthetic':True,'episode_id':self.episode_id,
                'patient':copy.deepcopy(self.intake),'encounter':{'time_hours':self.time,'phase':self.phase,
                    'terminal_status':self.terminal,'complete_episode':False,'treatment_trajectory_complete':bool(self.terminal)},
                'conditions':copy.deepcopy(self.conditions),'orders':copy.deepcopy(self.orders),'events':copy.deepcopy(self.events),
                'observations':copy.deepcopy(self.observations),
                'objective':{'name':'minimize_abnormal_count','reference_rules':copy.deepcopy(self.rules),
                             'metric_keys':list(self.rules),'history':goal_history,'current_visible':self.visible_score(),
                             'unassessed_keys':sorted(set(self.state['baseline'])-set(self.rules))},
                'workflow':{'version':'graph-rag-4.0','assessment_complete':True,'assessment_order_id':self.orders[0]['order_id'],
                    'care_started_at':0,'review_count':self.cycle,'completed_followups':self.cycle,
                    'max_followups':min(self.request.max_cycles,self.request.max_followups or self.request.max_cycles),
                    'trajectory_complete':bool(self.terminal)},'discharge_summary':None}
