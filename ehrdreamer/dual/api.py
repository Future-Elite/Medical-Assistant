from __future__ import annotations

import csv
import io
import json
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from ehrworld.io import ROOT
from ehrworld.llm import APIConfig
from .catalog import PHASES, TREATMENTS
from ehrdreamer.prompts import PROMPT_VERSION
from ehrdreamer.source import SourceError
from . import VERSION
from .contracts import GenerationRequest, BatchRequest
from .references import SOURCE, WBC_SOURCE, NOTES
from .service import public_job, LIMITATIONS
from .protocol import PROTOCOL_VERSION
from .examples import model_protocol_examples
from .knowledge import get_graph
from ehrdreamer.contracts import Contract
from pydantic import Field

router=APIRouter(prefix='/api/dreamer',tags=['EHR Dreamer · Graph-RAG'])


def job_or_404(request,identifier):
    job=request.app.state.dreamer.store.get(str(identifier))
    if not job:raise HTTPException(404,'未找到任务。')
    return job


def document(job):
    if not job.get('record'):raise HTTPException(409,'任务尚无病历。')
    return {**job['record'],'generation_id':job['id'],'backend':job['request']['backend'],
            'models':job['models'],'limitations':job['limitations'],
            'source':{'case_id':job['case']['id'],'sha256':job['source_sha256'],'initialization':'real_admission_window_labs'}}


@router.get('/health')
def health(request:Request):
    try:
        config=APIConfig.from_env();configured=True;model=config.model
    except (ValueError,TypeError):configured=False;model=None
    diagnostic=None
    path=ROOT/'outputs/dreamer_dual/connectivity.json'
    if path.is_file():
        try:diagnostic=json.loads(path.read_text(encoding='utf-8'))
        except (ValueError,OSError):pass
    if getattr(request.app.state,'validation_fixture',False):diagnostic=None
    return {'status':'ok','framework':'EHR Dreamer','engine_version':VERSION,'prompt_version':PROMPT_VERSION,
            'protocol_version':PROTOCOL_VERSION,
            'knowledge_version':get_graph().stats()['version'],
            'api_configured':configured,'model':model,'remote_connectivity':'not_checked',
            'last_connectivity_probe':diagnostic,'validation_fixture':getattr(request.app.state,'validation_fixture',False),
            'architecture':'llm_policy_and_llm_dynamics','initialization':'real_admission_window_labs',
            'retrieval_architecture':'medical_graph_rag',
            'objective':'minimize_abnormal_count_at_common_horizon','phases':PHASES,'treatments':TREATMENTS,
            'diagnostic_tests':{'culture':'病原学培养/血培养','imaging':'肺部影像评估'},
            'learning':{'ready':False,'active':False,'message':'当前引擎不加载本地机器学习权重。'},
            'limitations':LIMITATIONS,'default_max_operations':64}


@router.get('/learning')
def architecture():
    return {'engine_version':VERSION,'prompt_version':PROMPT_VERSION,'protocol_version':PROTOCOL_VERSION,'active_dynamics':'llm',
            'reference_sources':[SOURCE,WBC_SOURCE],'reference_notes':NOTES,
            'planning':'Graph-RAG证据检索、临床指征门控、共同终点比较、执行当前时间点处置组合、复评后重规划',
            'training_status':'not_fine_tuned','legacy_ml':'historical_baseline_only','limitations':LIMITATIONS}


@router.get('/model-protocol')
def model_protocol():
    """Synthetic examples only: no patient data, API access or secrets."""
    return model_protocol_examples()


class KnowledgeQuery(Contract):
    queries: tuple[str,...] = Field(default=(),max_length=5)
    generation_id: UUID | None = None
    case_id: str | None = Field(default=None,max_length=80)


@router.get('/knowledge')
def knowledge():
    return get_graph().stats()


@router.get('/knowledge/graph')
def knowledge_graph():
    graph=get_graph()
    return {'version':graph.stats()['version'],'nodes':list(graph.nodes.values()),'edges':graph.edges}


@router.post('/knowledge/search')
def knowledge_search(body:KnowledgeQuery,request:Request):
    visible=None
    if body.generation_id:
        job=job_or_404(request,body.generation_id)
        record=job.get('record')
        if not record:raise HTTPException(409,'该任务尚无已发布资料。')
        visible={'intake':record['patient'],'time_hours':record['encounter']['time_hours'],
                 'observations':record['observations'],'active_treatments':[]}
        # Only executed/public actions, never the research hidden-state monitor.
        active={}
        for event in record['events']:
            if event['event_type']!='treatment':continue
            if event['parameters'].get('operation')=='stop':active.pop(event['target'],None)
            elif event['target'] not in ('surfactant','transfusion'):active[event['target']]={'target':event['target']}
        visible['active_treatments']=list(active.values())
    elif body.case_id:
        from .environment import PatientEnvironment
        from .references import rules_for
        case=request.app.state.dreamer.source.get(body.case_id)
        env=PatientEnvironment(case,GenerationRequest(case_id=body.case_id),'PREVIEW',rules_for(case['intake'],case['baseline']),{})
        visible=env.view()
    return get_graph().retrieve(body.queries,visible)


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
def generate(body:GenerationRequest,request:Request):
    job=request.app.state.dreamer.submit(body)
    if job is None:raise HTTPException(429,'队列已满，请稍后重试。')
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
    if previous.get('engine_version')!=VERSION:raise HTTPException(409,'历史引擎任务请使用当前配置重新创建。')
    body=GenerationRequest.model_validate(previous['request'])
    job=request.app.state.dreamer.submit(body,previous)
    if job is None:raise HTTPException(429,'队列已满。')
    return job


@router.get('/generations/{identifier}/export')
def export(identifier:UUID,request:Request,format:str=Query('json',pattern='^(json|csv)$')):
    job=job_or_404(request,identifier);doc=document(job)
    if format=='json':
        return Response(json.dumps(doc,ensure_ascii=False,indent=2),media_type='application/json',
                        headers={'Content-Disposition':f'attachment; filename="dreamer-{identifier}.json"'})
    stream=io.StringIO(newline='');fields=['sequence','event_id','phase','time_hours','event_type','name','actor','status','value']
    writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader()
    for event in doc['events']:
        row={**event,'value':json.dumps(event.get('value'),ensure_ascii=False)}
        row={k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in row.items()}
        writer.writerow(row)
    return Response('\ufeff'+stream.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="dreamer-{identifier}.csv"'})


@router.get('/generations/{identifier}/research/latent')
def latent(identifier:UUID,request:Request):
    job=job_or_404(request,identifier)
    return {'research_only':True,'not_in_policy_view':True,'trace':job.get('private',{}).get('research_trace',[])}


@router.post('/generations/{identifier}/research/inverse')
def inverse(identifier:UUID,request:Request):
    raise HTTPException(409,'双LLM版本不将结局汇总误称为逆动力学；请查看候选规划与执行证据。')


@router.post('/batches/preview')
def preview(body:BatchRequest,request:Request):return request.app.state.dreamer.source.plan(body)


@router.post('/batches',status_code=202)
def batch_create(body:BatchRequest,request:Request):
    result=request.app.state.dreamer.submit_batch(body)
    if result is None:raise HTTPException(429,'已有批次正在运行。')
    return result


@router.get('/batches')
def batches(request:Request,limit:int=Query(20,ge=1,le=100)):
    service=request.app.state.dreamer
    return {'items':[service.batch_summary(b) for b in service.store.list_batches(limit)]}


def batch_or_404(request,identifier):
    batch=request.app.state.dreamer.store.get_batch(str(identifier))
    if not batch:raise HTTPException(404,'未找到批次。')
    return batch


@router.get('/batches/{identifier}')
def batch(identifier:UUID,request:Request):return request.app.state.dreamer.batch_summary(batch_or_404(request,identifier))


@router.get('/batches/{identifier}/export')
def export_batch(identifier:UUID,request:Request):
    batch=batch_or_404(request,identifier);jobs=request.app.state.dreamer.store.batch_jobs(batch)
    data={'framework':'EHR Dreamer','batch_id':str(identifier),'distribution':batch['distribution'],'synthetic':True,
          'patients':[document(j) for j in jobs if j.get('record')]}
    return Response(json.dumps(data,ensure_ascii=False,indent=2),media_type='application/json',
                    headers={'Content-Disposition':f'attachment; filename="dreamer-batch-{identifier}.json"'})
