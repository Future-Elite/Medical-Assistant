from __future__ import annotations

import csv
import io
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ehrworld.llm import APIConfig
from ehrworld.web.service import public_error
from .catalog import VERSION, PHASES, EXAMS, TREATMENTS, LIMITATIONS
from .contracts import GenerateRequest, BatchRequest, InverseRequest
from .inverse import infer
from .service import public_job
from .source import SourceError
from .learning.runtime import get_engine, ModelArtifactError

router=APIRouter(prefix='/api/dreamer',tags=['EHR Dreamer'])


def job_or_404(request,identifier):
    job=request.app.state.dreamer.store.get(str(identifier))
    if not job:raise HTTPException(404,'未找到EHR Dreamer任务。')
    return job


def document(job):
    return {**job['record'],'generation_id':job['id'],'backend':job['request']['backend'],
            'models':job['models'],'learning':job.get('learning'),'source':{'file':job['case'].get('source_file','出院小结工作簿'),
                                          'sha256':job['source_sha256'],'case_id':job['case']['id'],
                                          'row':job['case']['row'],'sheet':job['case']['sheet'],
                                          'intake_evidence':job['case']['intake_evidence'],
                                          'quality_flags':job['case']['quality_flags']},'limitations':job['limitations']}


@router.get('/health')
def health():
    try:config=APIConfig.from_env();configured=True;model=config.model
    except (ValueError,TypeError):configured=False;model=None
    try:learning={'ready':True,**get_engine().capabilities()}
    except ModelArtifactError as exc:learning={'ready':False,'message':str(exc)}
    from .workflow import reference_profile
    workflow_profile=reference_profile(get_engine()) if learning['ready'] else None
    return {'status':'ok','framework':'EHR Dreamer','engine_version':VERSION,'api_configured':configured,'learning':learning,
            'workflow_profile':workflow_profile,
            'model':model,'phases':PHASES,'exams':EXAMS,'treatments':TREATMENTS,'limitations':LIMITATIONS,
            'remote_connectivity':'not_checked','default_max_operations':96}


@router.get('/learning')
def learning_report():
    try:
        engine=get_engine()
        from ehrworld.io import ROOT
        rollout_path=ROOT/'outputs/dreamer_ml/model/rollout_evaluation.json'
        rollout=json.loads(rollout_path.read_text(encoding='utf-8')) if rollout_path.is_file() else None
        if rollout and rollout.get('model_id')!=engine.model_id:rollout=None
        return {'capabilities':engine.capabilities(),'report':engine.bundle['report'],'rollout':rollout}
    except ModelArtifactError as exc:raise HTTPException(503,str(exc)) from None


@router.get('/source')
def source(request:Request):return request.app.state.dreamer.source.summary()


@router.get('/cases')
def cases(request:Request,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100),eligible_only:bool=True):
    return request.app.state.dreamer.source.list(offset,limit,eligible_only)


@router.get('/cases/{case_id}')
def case(case_id:str,request:Request):
    catalog=request.app.state.dreamer.source
    return catalog.public(catalog.get(case_id))


@router.post('/generations',status_code=202)
def generate(body:GenerateRequest,request:Request):
    job=request.app.state.dreamer.submit(body)
    if job is None:raise HTTPException(429,'生成队列已满，请稍后重试。')
    return job


@router.get('/generations')
def generations(request:Request,limit:int=Query(30,ge=1,le=100)):
    return {'items':[public_job(j,True) for j in request.app.state.dreamer.store.list(limit)]}


@router.get('/generations/{identifier}')
def generation(identifier:UUID,request:Request):return public_job(job_or_404(request,identifier))


@router.post('/generations/{identifier}/retry',status_code=202)
def retry(identifier:UUID,request:Request):
    previous=job_or_404(request,identifier)
    if previous['status'] in ('queued','running'):raise HTTPException(409,'任务仍在运行。')
    body=GenerateRequest.model_validate({**previous['request'],'source_sha256':previous['source_sha256']})
    try:job=request.app.state.dreamer.submit(body,previous)
    except SourceError:raise
    except ValueError:raise HTTPException(409,'该患者已有新重试任务，请刷新批次。') from None
    if job is None:raise HTTPException(429,'生成队列已满。')
    return job


@router.get('/generations/{identifier}/research')
def research(identifier:UUID,request:Request):
    job=job_or_404(request,identifier)
    return {'trace':job['private']['research_trace'],'population_priors':job['private']['priors'],
            'policy_has_access':False,'limitations':LIMITATIONS}


@router.post('/generations/{identifier}/inverse')
def inverse(identifier:UUID,body:InverseRequest,request:Request):
    job=job_or_404(request,identifier)
    if job['status'] in ('queued','running'):raise HTTPException(409,'请等待当前任务结束后再进行离线分析。')
    reports=job.get('record',{}).get('observations',[]) if job.get('record') else []
    by_id={r['event_id']:r for r in reports}
    if body.before_event_id not in by_id or body.after_event_id not in by_id:raise HTTPException(422,'请选择本任务已有的两个检查报告。')
    before,after=by_id[body.before_event_id],by_id[body.after_event_id]
    if after['time_hours']<=before['time_hours'] or before['target']!=after['target']:
        raise HTTPException(422,'请选择时间递增的同类检查报告。')
    models=None
    try:
        models=request.app.state.dreamer.models_factory(GenerateRequest.model_validate(job['request']))
        return infer(models,before,after,job['request']['backend'])
    except Exception as exc:
        raise HTTPException(502,public_error(exc)['message']) from None
    finally:
        if models is not None:models.close()


@router.get('/generations/{identifier}/export')
def export(identifier:UUID,request:Request,format:str=Query('json',pattern='^(json|csv)$')):
    job=job_or_404(request,identifier)
    if job['status']!='completed':raise HTTPException(409,'仅已完成的生成任务可导出；预算结束会明确标记病程未完成。')
    headers={'Content-Disposition':f'attachment; filename="ehrdreamer-{str(identifier)[:8]}.{format}"','Cache-Control':'no-store'}
    if format=='json':return Response(json.dumps(document(job),ensure_ascii=False,indent=2,allow_nan=False),media_type='application/json',headers=headers)
    stream=io.StringIO(newline='');writer=csv.writer(stream)
    writer.writerow(['episode_id','sequence','phase','event_id','occurred_at','available_at','event_type','name','actor','status','parent_order_id','target','value','parameters','evidence_ids','synthetic'])
    for event in job['record']['events']:
        row=[job['record']['episode_id']]+[event[k] for k in ('sequence','phase','event_id','occurred_at','available_at','event_type','name','actor','status','parent_order_id','target')]+[
            json.dumps(event['value'],ensure_ascii=False),json.dumps(event['parameters'],ensure_ascii=False),json.dumps(event['evidence_ids'],ensure_ascii=False),True]
        writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in row])
    return Response('\ufeff'+stream.getvalue(),media_type='text/csv; charset=utf-8',headers=headers)


@router.post('/batches/preview')
def preview(body:BatchRequest,request:Request):return request.app.state.dreamer.source.plan(body)


@router.post('/batches',status_code=202)
def create_batch(body:BatchRequest,request:Request):
    batch=request.app.state.dreamer.submit_batch(body)
    if batch is None:raise HTTPException(429,'已有批次运行中，请稍后再提交。')
    return batch


@router.get('/batches')
def batches(request:Request,limit:int=Query(15,ge=1,le=50)):
    service=request.app.state.dreamer
    return {'items':[service.batch_summary(b) for b in service.store.list_batches(limit)]}


def get_batch(request,identifier):
    service=request.app.state.dreamer;batch=service.store.get_batch(str(identifier))
    if not batch:raise HTTPException(404,'未找到该批次。')
    return service.batch_summary(batch)


@router.get('/batches/{identifier}')
def batch(identifier:UUID,request:Request):return get_batch(request,identifier)


@router.get('/batches/{identifier}/export')
def export_batch(identifier:UUID,request:Request):
    batch=get_batch(request,identifier)
    if batch['status']!='completed':raise HTTPException(409,'10名患者的生成任务全部结束后可整批导出。')
    jobs=request.app.state.dreamer.store.batch_jobs(batch)
    payload={'framework':'EHR Dreamer','batch_id':str(identifier),'distribution':batch['distribution'],
             'synthetic':True,'patients':[document(j) for j in jobs]}
    return Response(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),media_type='application/json',
                    headers={'Content-Disposition':f'attachment; filename="ehrdreamer-batch-{str(identifier)[:8]}.json"','Cache-Control':'no-store'})
