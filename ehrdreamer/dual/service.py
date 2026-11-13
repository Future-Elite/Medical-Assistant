from __future__ import annotations

import copy
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from ehrworld.web.store import now
from ehrworld.web.guidelines import manifest, GuidelineSourceError
from ehrworld.web.service import public_error as gateway_error
from ehrworld.model_policy import thinking_policy
from ehrdreamer.prompts import PROMPT_VERSION
from . import VERSION
from .contracts import GenerationRequest, ResultSelection
from .environment import PatientEnvironment
from .models import DualModels
from .references import NOTES
from .protocol import PROTOCOL_VERSION, patient_json, report_json
from .planner import PlanningError

LIMITATIONS = ['患者后续指标由LLM预测，未识别个体治疗因果效应；模型内最优不等于临床最优。',
               '规划分支仅使用医生可见资料，实际隐藏指标不作为策略输入。',
               '达到模拟预算或异常计数归零均不自动代表临床治愈或出院。'] + NOTES


def public_job(job,brief=False):
    return {k:copy.deepcopy(v) for k,v in job.items() if k!='private' and (not brief or k not in ('record','usage','agent_steps','dynamics_monitor','planning'))}


def result_document(env,client):
    record=env.public_record();history=record['objective']['history']
    first,last=history[0],history[-1]
    ids={o['event_id'] for o in env.observations}
    pending=[copy.deepcopy(o) for o in env.orders if o.get('session_type')=='diagnostic' and o['status']=='pending']
    # Audit graphs and candidate traces are for the UI, not repeated into the
    # outcome selector. Each published check remains one compact lab JSON.
    payload={'patient':patient_json(env.intake),'time_hours':env.time,
             'reports':[report_json(o) for o in env.observations],
             'executed_treatments':copy.deepcopy(env.ledger),
             'pending_diagnostic_tests':[o['target'] for o in pending],
             'objective_before':{k:first[k] for k in ('abnormal_count','evaluated_count','complete')},
             'objective_after':{k:last[k] for k in ('abnormal_count','evaluated_count','complete')},
             'allowed_evidence_ids':sorted(ids),'metric_keys':list(env.state['baseline']),
             'response_schema':ResultSelection.model_json_schema()}
    def validate(reply):
        if not set(reply.evidence_ids)<=ids or not set(reply.unresolved_metric_keys)<=set(env.state['baseline']):
            raise ValueError('结局引用超出已发布资料。')
    reply=client.request('你是EHR Dreamer的结局证据选择器。仅从已发布报告中选择证据ID和需要复核的指标。'
                         '不得编造检验值、处方或临床治愈。输出给定schema的JSON，不输出长篇思考。',
                         payload,ResultSelection,operation='dreamer_outcome',validator=validate)
    validate(reply)
    improved=last['abnormal_count'] is not None and first['abnormal_count'] is not None and last['abnormal_count']<first['abnormal_count']
    latest={i['key']:(o,i) for o in env.observations for i in o['items']}
    changes=[]
    for key,base in env.state['baseline'].items():
        report,item=latest[key]
        changes.append({'key':key,'name':item['name'],'unit':item['unit'],'before':base['value'],'after':item['value'],
                        'before_comparator':base.get('comparator'),'before_bound':base.get('bound'),
                        'after_comparator':item.get('comparator'),'after_bound':item.get('bound'),
                        'after_collected_at_hours':report['collected_at_hours'],
                        'was_rechecked':report['collected_at_hours']>0,
                        'status':item['status'],'evidence_id':report['event_id']})
    return {'document_type':'treatment_outcome_summary','title':'治疗后情况与指标目标结果',
            'disposition':'ongoing','overall_outcome':'improved' if improved else 'unknown',
            'discharge_status':'已完成本次模拟，未作临床出院或治愈判定。',
            'treatment_result_text':f"完成{env.cycle}轮滚动决策；已发布报告中的研究异常计数由{first['abnormal_count']}变为{last['abnormal_count']}。",
            'objective_before':first,'objective_after':last,'monitored_indicator_changes':changes,
            'evidence_ids':list(reply.evidence_ids),'unresolved_metric_keys':list(reply.unresolved_metric_keys),
            'unresolved_issues':LIMITATIONS[:1]+(['仍有待完成培养或影像检查，未生成或推断其结果。'] if pending else []),
            'pending_test_orders':pending,'medication_prescriptions':[],
            'hospital_course':[{'event_id':e['event_id'],'time_hours':e['time_hours'],'name':e['name'],'parameters':e['parameters']}
                               for e in env.events if e['event_type'] in ('treatment','treatment_review','decision')],
            'synthetic':True}


class DreamerService:
    def __init__(self,store,source,models_factory=DualModels):
        self.store=store;self.source=source;self.models_factory=models_factory
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='dreamer-dual')
        self.capacity=threading.BoundedSemaphore(6);self.batch_capacity=threading.BoundedSemaphore(1)

    def prepare(self,body,**metadata):
        case,rules=self.source.checked(body.case_id,body)
        if body.source_sha256 and body.source_sha256!=self.source.metadata['source_sha256']:
            from ehrdreamer.source import SourceError
            raise SourceError('来源工作簿已变化，请重新选择病例。')
        return {'id':str(uuid.uuid4()),'framework':'EHR Dreamer','schema_version':'ehrdreamer-4.0',
            'engine_version':VERSION,'prompt_version':PROMPT_VERSION,'protocol_version':PROTOCOL_VERSION,
            'status':'queued','created_at':now(),'updated_at':now(),
            'request':body.model_dump(mode='json'),'case':self.source.public(case),'source_sha256':self.source.metadata['source_sha256'],
            'operations':0,'time_hours':0,'phase':'assessment','terminal_status':None,'record':None,'error':None,'usage':{},
            'models':{r:{'name':getattr(body,r+'_model') or body.model,
                         'thinking_policy':thinking_policy(getattr(body,r+'_model') or body.model)} for r in ('policy','dynamics','outcome')},
            'limitations':LIMITATIONS,'progress':{'stage':'queued','cycle':0},
            'private':{'baseline':copy.deepcopy(case['baseline']),'rules':rules,'research_trace':[]},**metadata}

    def submit(self,body,previous=None):
        if not self.capacity.acquire(blocking=False):return None
        try:
            metadata={k:previous[k] for k in ('batch_id','batch_index','length_group') if k in previous} if previous else {}
            job=self.prepare(body,**metadata)
            if metadata:self.store.replace_batch_job(metadata['batch_id'],previous['id'],job)
            else:self.store.save(job)
            self.pool.submit(self._run,job['id'],body,True)
            return public_job(job)
        except Exception:self.capacity.release();raise

    def _save(self,job,env,models,stage):
        record=env.public_record()
        job.update(operations=env.operations,time_hours=env.time,phase=env.phase,terminal_status=env.terminal,
                   record=record,workflow=record['workflow'],
                   agent_steps=copy.deepcopy(env.agent_steps),planning=copy.deepcopy(env.planning),
                   dynamics_monitor={'kind':'research_hidden_state_not_policy_input',
                       'metric_count':len(env.state['labs']),'scored_metric_count':len(env.rules),
                       'updates':copy.deepcopy(env.dynamics_updates)},
                   progress={'stage':stage,'cycle':env.cycle,'max_cycles':min(env.request.max_cycles,env.request.max_followups or env.request.max_cycles)})
        job['private']['research_trace']=copy.deepcopy(env.trace)
        job['usage']=models.statistics()
        self.store.save(job)

    def _run(self,identifier,body,reserved):
        job=self.store.get(identifier);models=None;env=None
        try:
            job['status']='running';job['progress']={'stage':'initializing','cycle':0};self.store.save(job)
            guidelines=manifest()
            if not all(s['available'] and s['reviewed'] for s in guidelines['sources']):
                raise GuidelineSourceError('Reviewed guidelines unavailable or changed')
            models=self.models_factory(body)
            env=PatientEnvironment({'intake':job['case']['intake'],'baseline':job['private']['baseline']},body,
                                   'ED-'+identifier[:8].upper(),job['private']['rules'],guidelines)
            self._save(job,env,models,'baseline_ready')
            limit=min(body.max_cycles,body.max_followups or body.max_cycles)
            while env.cycle<limit and env.time<body.max_hours and env.operations<body.max_operations:
                self._save(job,env,models,'proposing_and_forecasting')
                def progress(done,total,stage='forecasting'):
                    self._save(job,env,models,stage)
                    if done is not None:job['progress'].update(evaluated_candidates=done,total_candidates=total)
                    self.store.save(job)
                plan,evaluations,rationale,diagnoses=models.policy.decide(env,models.dynamics,progress)
                self._save(job,env,models,'executing')
                env.execute_first(plan,evaluations,rationale,diagnoses,models.dynamics)
                self._save(job,env,models,'review_published')
            env.finish('本次模拟预算完成；已评分指标正常不自动表示肺炎、RDS或感染已解决。')
            self._save(job,env,models,'summarizing')
            final=result_document(env,models.outcome)
            env.record('document',final['title'],'completed',actor='outcome_model',value={'overall_outcome':final['overall_outcome']})
            self._save(job,env,models,'completed');job['record']['discharge_summary']=final;job['status']='completed'
        except Exception as exc:
            if env:
                for step in env.agent_steps:
                    if step['status']=='started':step.update(status='failed',summary='该请求未成功完成。')
                env.log_agent('error','本轮未能继续',str(exc) if isinstance(exc,PlanningError) else '模型调用或结构校验未通过，详情见任务错误。',status='failed',
                              cycle=env.cycle if env.phase=='discharge' else env.cycle+1)
            if env and models:self._save(job,env,models,'failed')
            job['status']='failed'
            job['error']=gateway_error(exc) if not isinstance(exc,ValueError) else {
                'code':'dual_llm_validation_error','message':'双LLM规划或状态校验未通过；未提交无效变化，也未回退旧模型。','retryable':True}
            if job['error']['code']=='upstream_forbidden':
                job['error']['message']='模型网关返回403，当前网络环境或访问权限不受支持；未切换离线或旧动力学。'
            if isinstance(exc,PlanningError):
                job['error']={'code':exc.code,'message':str(exc),'retryable':True}
        finally:
            try:
                if models:job['usage']=models.statistics();models.close()
                self.store.save(job)
            finally:
                if reserved:self.capacity.release()

    def submit_batch(self,body):
        plan=self.source.plan(body)
        if not self.batch_capacity.acquire(blocking=False):return None
        try:
            identifier=str(uuid.uuid4());jobs=[];requests=[]
            for patient in plan['patients']:
                data=body.model_dump(exclude={'case_id'})
                data.update(case_id=patient['case']['id'],seed=(body.seed+patient['index']*1009)%2147483648,
                            max_operations=patient['max_operations'],max_cycles=patient['max_cycles'],
                            source_sha256=plan['source']['source_sha256'])
                req=GenerationRequest.model_validate(data);requests.append(req)
                jobs.append(self.prepare(req,batch_id=identifier,batch_index=patient['index'],length_group=patient['length_group']))
            batch={'id':identifier,'created_at':now(),'request':body.model_dump(mode='json'),
                   'source_sha256':plan['source']['source_sha256'],'distribution':plan['distribution'],'job_ids':[j['id'] for j in jobs]}
            self.store.create_batch(batch,jobs)
        except Exception:self.batch_capacity.release();raise
        lock=threading.Lock();remaining=[len(jobs)]
        def done(_):
            with lock:
                remaining[0]-=1
                if not remaining[0]:self.batch_capacity.release()
        for job,req in zip(jobs,requests):self.pool.submit(self._run,job['id'],req,False).add_done_callback(done)
        return self.batch_summary(batch)

    def batch_summary(self,batch):
        jobs=self.store.batch_jobs(batch);completed=sum(j['status']=='completed' for j in jobs);failed=sum(j['status']=='failed' for j in jobs)
        active=any(j['status'] in ('queued','running') for j in jobs)
        return {**batch,'status':'running' if active else 'completed' if completed==10 else 'completed_with_errors',
                'completed_patients':completed,'failed_patients':failed,'total_patients':10,
                'completed_episodes':0,'completed_trajectories':sum(j.get('terminal_status')=='treatment_completed' for j in jobs),
                'patients':[public_job(j,True) for j in jobs]}

    def close(self):
        self.pool.shutdown(wait=True,cancel_futures=False)
