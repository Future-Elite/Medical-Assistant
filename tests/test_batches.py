import json
import threading
import time
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI
from pydantic import BaseModel

from ehrworld.io import ROOT
from ehrworld.llm import APIConfig, JSONClient, ModelResponseError
from ehrworld.model_policy import MODEL_NAMES
from ehrworld.web.app import create_app
from ehrworld.web.models import BatchRequest, GenerationRequest
from ehrworld.web.scenarios import DemoPredictor
from ehrworld.web.seeds import SeedCatalog
from ehrworld.web.service import default_predictor


def wait(client, path):
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        result=client.get(path).json()
        if result['status'] not in ('queued','running'):return result
        time.sleep(.01)
    pytest.fail('job did not finish')


def check_plan(plan):
    assert len(plan['patients'])==10
    assert len({p['request']['source_seed']['source_patient_id'] for p in plan['patients']})==10
    assert Counter(p['length_group'] for p in plan['patients'])=={'short':3,'medium':5,'long':2}
    limits={'short':(1,2),'medium':(3,5),'long':(6,10)}
    for patient in plan['patients']:
        a,b=limits[patient['length_group']]
        assert a<=patient['target_steps']<=b
        assert patient['request']['steps']==patient['target_steps']
    assert {p['request']['scenario_id'] for p in plan['patients']}=={'neonatal_pneumonia','neonatal_rds'}


def test_real_source_reproducibility_distinct_patients_and_no_future_records():
    catalog=SeedCatalog()
    body=BatchRequest(seed=42,start_time='2026-10-28T08:00:00')
    plan=catalog.preview(body)
    check_plan(plan)
    assert plan==catalog.preview(body)
    assert plan!=catalog.preview(body.model_copy(update={'seed':43}))
    rows=json.loads((ROOT/'test.json').read_text(encoding='utf-8-sig'))
    by_id={r['id']:r for r in rows}
    for patient in plan['patients']:
        seed=patient['request']['source_seed']
        row=by_id[seed['source_record_id']]
        assert row['data_type']=='Single'
        assert seed['raw_structured_initial']==row['clinical_structured']
        assert 'temporal_triplets_en' not in seed['raw_structured_initial']
        for key,field in seed['observed_fields'].items():
            if key=='initial_state.heart_rate':
                assert patient['request']['initial_state']['heart_rate']==row['clinical_structured']['physiological_status']['pulse_bpm']
        assert seed['assumptions']['patient.weight_g']['reason']


@pytest.mark.parametrize('n',[1,2,3,5,6,10])
def test_variable_steps_have_complete_records_and_exports(tmp_path,n):
    with TestClient(create_app(tmp_path/'web.sqlite')) as client:
        assert len(client.post('/api/plans/preview',json={'steps':n}).json()['steps'])==n
        response=client.post('/api/generations',json={'backend':'demo','steps':n,'model':'MiMo-V2.6-Flash'})
        assert response.status_code==202
        job=wait(client,'/api/generations/'+response.json()['id'])
        assert job['status']=='completed',job['error']
        assert job['completed_steps']==job['total_steps']==len(job['trajectory']['steps'])==n
        for step in job['trajectory']['steps']:
            assert step['physician_record']['orders']
            assert any(e['action']['kind']=='inquiry' for e in step['events'])
        data=client.get(f"/api/generations/{job['id']}/export").json()
        assert len(data['steps'])==data['step_count']==n
        assert data['requested_model']=='MiMo-V2.6-Flash'
        assert client.get(f"/api/generations/{job['id']}/export?format=csv").status_code==200


def test_full_batch_generation_export_and_persistence(tmp_path):
    path=tmp_path/'web.sqlite'
    with TestClient(create_app(path)) as client:
        source=client.get('/api/seed-data').json()
        body={'backend':'demo','seed':42,'model':'GLM-5.3-Flash','source_sha256':source['source_sha256'],'start_time':'2026-10-28T08:00:00'}
        plan=client.post('/api/batches/preview',json=body).json()
        check_plan(plan)
        response=client.post('/api/batches',json=body)
        assert response.status_code==202,response.text
        identifier=response.json()['id']
        batch=wait(client,'/api/batches/'+identifier)
        assert batch['status']=='completed',[(p['status'],p['error']) for p in batch['patients']]
        assert batch['completed_patients']==10 and batch['failed_patients']==0
        assert batch['completed_steps']==batch['total_steps']==plan['total_steps']
        assert [p['total_steps'] for p in batch['patients']]==[p['target_steps'] for p in plan['patients']]
        for patient in batch['patients']:
            job=client.get('/api/generations/'+patient['id']).json()
            assert job['request']['model']=='GLM-5.3-Flash'
            assert job['trajectory']['source_seed']['source_patient_id']==patient['request']['source_seed']['source_patient_id']
            initial=job['request']['initial_state']
            observed=job['trajectory']['steps'][0]['physician_record']['prior_observations']
            for key in ('heart_rate','temperature','ph','paco2'):
                if initial[key] is not None: assert observed[key]==initial[key]
            assert len(job['trajectory']['steps'])==job['total_steps']
        export=client.get(f'/api/batches/{identifier}/export')
        assert export.status_code==200
        document=export.json()
        assert len(document['patients'])==10
        assert [p['step_count'] for p in document['patients']]==[p['target_steps'] for p in plan['patients']]
        assert all(len(p['steps'])==p['step_count'] for p in document['patients'])
        assert client.get('/api/batches').json()['items'][0]['id']==identifier
    with TestClient(create_app(path)) as client:
        assert client.get('/api/batches/'+identifier).json()['status']=='completed'


def test_individual_failure_retry_replaces_only_original_batch_slot(tmp_path):
    broken_id=[None]
    failed=set()
    def factory(body):
        source_id=body.source_seed['source_patient_id']
        if broken_id[0] is None:broken_id[0]=source_id
        if source_id==broken_id[0] and source_id not in failed:
            failed.add(source_id)
            raise ModelResponseError('HTTP 403 private-key-not-for-output')
        return DemoPredictor(body),None
    with TestClient(create_app(tmp_path/'web.sqlite',factory)) as client:
        identifier=client.post('/api/batches',json={'backend':'api','model':'DeepSeek-V4.1-Flash'}).json()['id']
        batch=wait(client,'/api/batches/'+identifier)
        assert batch['status']=='completed_with_errors'
        assert batch['completed_patients']==9 and batch['failed_patients']==1
        assert client.get(f'/api/batches/{identifier}/export').status_code==409
        previous=next(p for p in batch['patients'] if p['status']=='failed')
        assert 'private-key' not in json.dumps(previous)
        response=client.post(f"/api/generations/{previous['id']}/retry")
        assert response.status_code==202
        retry=wait(client,'/api/generations/'+response.json()['id'])
        assert retry['status']=='completed'
        assert retry['total_steps']==previous['total_steps']
        assert retry['request']==previous['request']
        batch=client.get('/api/batches/'+identifier).json()
        assert batch['status']=='completed' and batch['completed_patients']==10
        assert len(batch['job_ids'])==10 and previous['id'] not in batch['job_ids']
        assert retry['id'] in batch['job_ids']
        assert client.post(f"/api/generations/{previous['id']}/retry").status_code==409


def test_batch_capacity_and_upstream_concurrency_bounded_to_two(tmp_path):
    gate=threading.Event();lock=threading.Lock();active=[0];peak=[0]
    def factory(body):
        with lock:
            active[0]+=1;peak[0]=max(peak[0],active[0])
        gate.wait(5)
        with lock:active[0]-=1
        return DemoPredictor(body),None
    with TestClient(create_app(tmp_path/'web.sqlite',factory)) as client:
        try:
            batch=client.post('/api/batches',json={'backend':'demo'}).json()
            assert client.post('/api/batches',json={'backend':'demo'}).status_code==429
            assert client.get(f"/api/batches/{batch['id']}/export").status_code==409
        finally:gate.set()
        assert wait(client,'/api/batches/'+batch['id'])['status']=='completed'
    assert peak[0]<=2


def test_source_errors_do_not_silently_generate_default_patients(tmp_path):
    with TestClient(create_app(tmp_path/'web.sqlite',seed_path=tmp_path/'missing.json')) as client:
        assert client.post('/api/batches',json={}).status_code==400
        assert client.get('/api/generations').json()['items']==[]
    empty=tmp_path/'empty.json';empty.write_text('[]')
    with TestClient(create_app(tmp_path/'web2.sqlite',seed_path=empty)) as client:
        assert client.post('/api/batches',json={}).status_code==400
    with TestClient(create_app(tmp_path/'web3.sqlite')) as client:
        assert client.post('/api/batches',json={'source_sha256':'0'*64}).status_code==409
        assert client.get('/api/generations').json()['items']==[]


class Probe(BaseModel):
    ok:bool


@pytest.mark.parametrize('model',MODEL_NAMES)
def test_each_selected_model_and_thinking_policy_reaches_wire_on_repair(model):
    bodies=[]
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'probe','object':'chat.completion','created':0,'model':model,
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'invalid' if len(bodies)==1 else '{"ok":true}'}}]})
    with OpenAI(base_url='https://example.invalid/v1',api_key='fake-key',http_client=httpx.Client(transport=httpx.MockTransport(handler))) as sdk:
        client=JSONClient(APIConfig(api_key='fake-key',model=model),sdk)
        assert client.request('JSON only',{},Probe,operation='probe').ok
    assert len(bodies)==2
    for body in bodies:
        assert body['model']==model
        if model.startswith('GLM'):
            assert body['reasoning_effort']=='low'
            assert body['thinking']['type']=='enabled'
            assert 'temperature' not in body
        elif model.startswith(('MiMo','DeepSeek')):assert body['thinking']['type']=='disabled'
        else:assert body['enable_thinking'] is body['chat_template_kwargs']['enable_thinking'] is False


def test_model_choice_is_per_request_without_global_config_mutation(monkeypatch,tmp_path):
    configs=[]
    class FakeClient:
        def __init__(self,config):configs.append(config);self.config=config
    monkeypatch.setattr('ehrworld.web.service.JSONClient',FakeClient)
    monkeypatch.setattr(APIConfig,'from_env',classmethod(lambda cls:APIConfig(api_key='fake-key',model='Qwen3.8-27B')))
    for model in MODEL_NAMES:
        predictor,_=default_predictor(GenerationRequest(model=model))
        assert predictor.name=='api:'+model
    assert [c.model for c in configs]==list(MODEL_NAMES)
    assert APIConfig.from_env().model=='Qwen3.8-27B'
    with TestClient(create_app(tmp_path/'web.sqlite')) as client:
        assert [m['id'] for m in client.get('/api/models').json()['items']]==list(MODEL_NAMES)


def test_earliest_record_is_chosen_before_disease_filter_and_temporal_excluded(tmp_path):
    rows=json.loads((ROOT/'test.json').read_text(encoding='utf-8-sig'))
    baseline=next(r for r in rows if r['data_type']=='Single' and '肺炎' in r['disease_label'] and r['clinical_structured'].get('development_factors',{}).get('birth_weight_g'))
    future=json.loads(json.dumps(baseline))
    baseline=json.loads(json.dumps(baseline));baseline.update(patient_id='shared',id='early',image='a_2024010108.png',disease_label='正常')
    future.update(patient_id='shared',id='future',image='a_2024010208.png')
    temporal=json.loads(json.dumps(future));temporal.update(patient_id='temporal-only',data_type='Temporal')
    path=tmp_path/'seeds.json';path.write_text(json.dumps([future,temporal,baseline]),encoding='utf-8')
    summary,eligible=SeedCatalog(path).load()
    assert summary['single_baseline_patients']==1
    assert eligible==[]
