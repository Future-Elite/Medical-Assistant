from __future__ import annotations

import copy
import threading
import uuid
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

from ehrworld.web.guidelines import manifest, GuidelineSourceError
from ehrworld.web.service import public_error as legacy_public_error
from .learning.runtime import get_engine, ModelArtifactError
from ehrworld.web.store import JobStore, now
from ehrworld.model_policy import thinking_policy
from .catalog import LIMITATIONS, VERSION
from .contracts import GenerateRequest, Intake, Decision
from .environment import PatientEnvironment, ActionRejected
from .models import Models
from .outcome import build_discharge
from .source import SourceCatalog, SourceError


def public_error(exc):
    if isinstance(exc,ModelArtifactError):
        return {'code':'trained_model_error','message':str(exc),'retryable':False}
    return legacy_public_error(exc)


def public_job(job,brief=False):
    return {k:copy.deepcopy(v) for k,v in job.items() if k!='private' and (not brief or k not in ('record','usage'))}


class DreamerService:
    def __init__(self,store:JobStore,source:SourceCatalog,models_factory=Models):
        self.store=store;self.source=source;self.models_factory=models_factory
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='ehrdreamer')
        self.capacity=threading.BoundedSemaphore(6);self.batch_capacity=threading.BoundedSemaphore(1)

    def prepare(self,body,**metadata):
        case=self.source.get(body.case_id)
        if body.source_sha256 and body.source_sha256!=self.source.metadata['source_sha256']:
            raise SourceError('来源工作簿已变化，请重新选择病例。')
        if not case['eligible']:raise SourceError('该病例暂未通过初始信息与覆盖范围检查，请选择可用病例。')
        engine=get_engine()
        identifier=str(uuid.uuid4())
        # Snapshot initial view and population priors now, before asynchronous execution.
        return {'id':identifier,'framework':'EHR Dreamer','schema_version':'ehrdreamer-2.1','engine_version':VERSION,
                'status':'queued','created_at':now(),'updated_at':now(),'request':body.model_dump(mode='json'),
                'case':self.source.public(case),'source_sha256':self.source.metadata['source_sha256'],
                'operations':0,'time_hours':0,'phase':'assessment','terminal_status':None,'record':None,'error':None,'usage':{},
                'models':{role:{'name':getattr(body,role+'_model') or body.model,
                                'thinking_policy':thinking_policy(getattr(body,role+'_model') or body.model)} for role in ('policy','outcome')} |
                          {'dynamics':{'name':'本地机器学习 '+engine.model_id,'thinking_policy':{'enabled':False,'label':'本地训练推理'}}},
                'learning':{**engine.capabilities(),'source_partition':engine.membership(case['group_id'])},
                'limitations':LIMITATIONS+engine.capabilities()['limitations'],
                'private':{'priors':{'method':'not_used_by_learned_dynamics'},'research_trace':[]},**metadata}

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

    def submit_batch(self,body):
        plan=self.source.plan(body)
        if not self.batch_capacity.acquire(blocking=False):return None
        try:
            identifier=str(uuid.uuid4());jobs=[];requests=[]
            for patient in plan['patients']:
                req=GenerateRequest(case_id=patient['case']['id'],backend=body.backend,model=body.model,
                                    policy_model=body.policy_model,outcome_model=body.outcome_model,
                                    seed=(body.seed+patient['index']*1009)%2147483648,max_operations=patient['max_operations'],
                                    max_hours=body.max_hours,max_followups=body.max_followups,source_sha256=plan['source']['source_sha256'])
                requests.append(req);jobs.append(self.prepare(req,batch_id=identifier,batch_index=patient['index'],length_group=patient['length_group']))
            batch={'id':identifier,'created_at':now(),'request':body.model_dump(mode='json'),
                   'source_sha256':plan['source']['source_sha256'],'distribution':plan['distribution'],'job_ids':[j['id'] for j in jobs]}
            self.store.create_batch(batch,jobs)
        except Exception:self.batch_capacity.release();raise
        lock=threading.Lock();remaining=[10]
        def done(_):
            with lock:
                remaining[0]-=1
                if not remaining[0]:self.batch_capacity.release()
        for job,req in zip(jobs,requests):self.pool.submit(self._run,job['id'],req,False).add_done_callback(done)
        return self.batch_summary(batch)

    def batch_summary(self,batch):
        jobs=self.store.batch_jobs(batch)
        active=any(j['status'] in ('queued','running') for j in jobs)
        completed=sum(j['status']=='completed' for j in jobs);failed=sum(j['status']=='failed' for j in jobs)
        return {**batch,'status':'running' if active else 'completed' if completed==10 else 'completed_with_errors',
                'completed_patients':completed,'failed_patients':failed,'total_patients':10,
                'completed_episodes':sum(j.get('terminal_status') in ('discharged','transferred') for j in jobs),
                'completed_trajectories':sum(j.get('terminal_status') in ('treatment_completed','discharged','transferred') for j in jobs),
                'patients':[public_job(j,True) for j in jobs]}

    def _save(self,job,env,models):
        job.update(operations=env.operations,time_hours=round(env.time,4),phase=env.phase,terminal_status=env.terminal)
        job['record']=env.public_record();job['workflow']=copy.deepcopy(job['record']['workflow'])
        job['private']['research_trace']=copy.deepcopy(env.trace)
        if models:job['usage']=models.statistics()
        self.store.save(job)

    def _run(self,identifier,body,reserved):
        job=self.store.get(identifier);models=None;env=None
        try:
            job['status']='running';self.store.save(job)
            guidelines=manifest()
            if not all(s['available'] and s['reviewed'] for s in guidelines['sources']):raise GuidelineSourceError('Reviewed guidelines are unavailable or changed')
            guidance={'sources':guidelines['sources'],'rules':guidelines['rules'],
                      'simulation_limitations':LIMITATIONS}
            env=PatientEnvironment(Intake.model_validate(job['case']['intake']),body,'ED-'+identifier[:8].upper(),job['private']['priors'],guidance)
            if env.engine.model_id!=job['learning']['model_id']:
                raise ModelArtifactError('排队期间训练产物已更新，请按新模型重新生成。')
            models=self.models_factory(body);self._save(job,env,models)
            rejections=0
            while env.operations<body.max_operations and env.time<body.max_hours and not env.terminal:
                decision=Decision.model_validate(models.policy.decide(env.view()))
                try:
                    env.execute(decision,models.dynamics);rejections=0
                except ActionRejected as exc:
                    env.reject(decision,str(exc));rejections+=1
                self._save(job,env,models)
                if rejections>=6:break
            if not env.terminal:
                env.finish_budget('policy_rejections' if rejections>=6 else 'time_budget' if env.time>=body.max_hours else 'operation_budget')
            proposal=models.outcome.summarize(env)
            final=build_discharge(env,proposal)
            env.record('document',final['title'],'completed',actor='outcome_model',value={
                'disposition':final['disposition'],'overall_outcome':final['overall_outcome']},evidence=final['evidence_ids'])
            self._save(job,env,models);job['record']['discharge_summary']=final;job['status']='completed'
        except Exception as exc:
            job['status']='failed';job['error']=public_error(exc)
        finally:
            try:
                if models:
                    job['usage']=models.statistics();models.close()
            finally:
                try:self.store.save(job)
                finally:
                    if reserved:self.capacity.release()

    def close(self):self.pool.shutdown(wait=True,cancel_futures=False)
