from __future__ import annotations

import csv
import io
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ehrworld.llm import APIConfig, JSONClient, ModelResponseError
from ehrworld.schema import PatientState
from ehrworld.simulator import APIPredictor, PatientSimulator
from ehrworld.web.app import create_app
from ehrworld.web.guidelines import RULES, SOURCES, guidance_for
from ehrworld.web.models import GenerationRequest
from ehrworld.web.physician import plan_step
from ehrworld.web.scenarios import DemoPredictor, patient_context
from test_web import wait_job


def start_plan(scenario='neonatal_rds', patient=None, initial=None, **kwargs):
    raw = {'scenario_id':scenario, 'backend':'demo', **kwargs}
    if patient is not None:
        base = GenerationRequest(scenario_id=scenario).patient.model_dump()
        raw['patient'] = {**base, **patient}
    if initial is not None:
        base = GenerationRequest(scenario_id=scenario).initial_state.model_dump()
        raw['initial_state'] = {**base, **initial}
    request = GenerationRequest.model_validate(raw)
    state = PatientState(timestamp=request.start_time, context=patient_context(request))
    return request, state, plan_step(request, state, 0)


@pytest.mark.parametrize('scenario', ['neonatal_pneumonia','neonatal_rds'])
def test_neonatal_ten_steps_have_executed_doctor_orders_and_page_provenance(tmp_path, scenario):
    with TestClient(create_app(tmp_path/'web.sqlite')) as client:
        catalog = client.get('/api/scenarios').json()
        assert {s['id'] for s in catalog['items']} == {'neonatal_pneumonia','neonatal_rds'}
        sources = client.get('/api/guidelines').json()
        assert all(s['available'] and len(s['sha256'])==64 for s in sources['sources'])
        assert len(sources['excluded_files']) == 1
        identifier = client.post('/api/generations',json={'backend':'demo','scenario_id':scenario}).json()['id']
        job = wait_job(client, identifier)
        assert job['status']=='completed', job.get('error')
        assert job['request']['patient']['age'] is None
        assert job['trajectory']['record_schema']=='neonatal-ehr-v2'
        for i, step in enumerate(job['trajectory']['steps']):
            physician = step['physician_record']
            assert physician['actor']=='模拟医生'
            assert physician['postnatal_age_hours']==job['request']['patient']['postnatal_age_hours']+i*3
            assert physician['observation_cutoff'] is None if i==0 else physician['observation_cutoff'] < step['timestamp']
            assert {o['order_id'] for o in physician['orders']} == {e['action']['action_id'] for e in step['events']}
            assert all(o['status']=='performed_in_simulation' and o['rationale'] for o in physician['orders'])
            for event in step['events']:
                assert event['action']['parameters']['actor']=='simulated_physician'
                assert event['action']['parameters']['guideline_refs']
                if event['action']['kind']=='intervention': assert event['value'] is None
            for order in physician['orders']:
                for ref in order['guideline_refs']:
                    assert ref['rule_id'] in RULES and ref['source_id'] in SOURCES
            if i:
                previous_spo2=next(e['value'] for e in job['trajectory']['steps'][i-1]['events'] if e['action']['concept']=='web:spo2')
                assert physician['prior_observations']['spo2']==previous_spo2
        exported=client.get(f'/api/generations/{identifier}/export').json()
        assert exported['schema_version']=='3.0'
        assert all(s['physician_record']['orders'] for s in exported['steps'])
        rows=list(csv.DictReader(io.StringIO(client.get(f'/api/generations/{identifier}/export?format=csv').content.decode('utf-8-sig'))))
        assert all(r['actor']=='simulated_physician' and r['indication'] for r in rows)
        assert any(json.loads(r['guideline_references']) for r in rows)


@pytest.mark.parametrize('week,day,age,mode,fio2,pressure,expected', [
    (28,0,2,'cpap',.30,6,False), (28,1,2,'cpap',.30,6,True),
    (29,0,2,'cpap',.29,6,False), (29,0,2,'cpap',.30,5.5,False),
    (27,0,.5,'cpap',.25,6,True), (27,0,2,'cpap',.35,6,False),
    (31,0,2,'invasive',.21,0,True), (32,0,2,'invasive',.21,0,False),
])
def test_rds_surfactant_population_time_and_boundary_guards(week,day,age,mode,fio2,pressure,expected):
    _,_,plan=start_plan(patient={'gestational_weeks':week,'gestational_days':day,'postnatal_age_hours':age},
                        initial={'respiratory_support':mode,'fio2':fio2,'cpap_cm_h2o':pressure})
    assert any(a.concept=='web:surfactant' for a in plan.actions)==expected


def test_rds_primary_care_cannot_execute_nicu_procedures_or_lisa_without_breathing():
    _,_,plan=start_plan(care_setting='primary')
    assert not any(a.concept in {'web:surfactant','web:caffeine','web:cpap_start'} for a in plan.actions)
    assert any('转诊' in a.name for a in plan.actions)
    _,_,plan=start_plan(initial={'spontaneous_breathing':False,'recurrent_apnea':True})
    assert not any(a.concept=='web:surfactant' and a.parameters.get('route')=='细导管LISA' for a in plan.actions)


@pytest.mark.parametrize('week,target', [(27,[90,94]),(28,None),(30,None)])
def test_rds_oxygen_target_scope_is_not_applied_to_all_infants(week,target):
    _,_,plan=start_plan(patient={'gestational_weeks':week})
    action=next(a for a in plan.actions if a.concept=='web:oxygen_review')
    assert action.parameters['spo2_target']==target if target else isinstance(action.parameters['spo2_target'],str)


def run_policy(request, count=10):
    simulator=PatientSimulator(patient_context(request),request.start_time,DemoPredictor(request))
    plans=[]
    for i in range(count):
        plan=plan_step(request,simulator.state,i)
        simulator.step(plan.timestamp,plan.actions)
        plans.append(plan)
    return simulator,plans


def test_rds_does_not_repeat_ps_or_caffeine_or_start_unnecessary_antibiotics():
    request=GenerationRequest(scenario_id='neonatal_rds',backend='demo')
    _,plans=run_policy(request)
    concepts=[a.concept for p in plans for a in p.actions]
    assert concepts.count('web:surfactant')==1
    assert concepts.count('web:caffeine')==1
    assert 'web:abx_start' not in concepts
    assert not any('CT' in a.name for p in plans for a in p.actions)


def test_antibiotic_stopping_distinguishes_rds_from_pneumonia_and_does_not_restart():
    request,_,_=start_plan(initial={'infection_suspected':True})
    sim,plans=run_policy(request)
    assert any(a.concept=='web:abx_stop' for a in plans[-1].actions)
    assert not any(a.concept=='web:abx_start' for a in plan_step(request,sim.state,10).actions)
    request,_,_=start_plan('neonatal_pneumonia')
    _,plans=run_policy(request)
    assert not any(a.concept=='web:abx_stop' for p in plans for a in p.actions)
    assert any(a.concept=='web:abx_continue' for a in plans[-1].actions)
    request,_,_=start_plan(initial={'infection_suspected':True,'bacterial_pneumonia_supported':True})
    _,plans=run_policy(request)
    assert not any(a.concept=='web:abx_stop' for p in plans for a in p.actions)


def test_viral_pneumonia_does_not_automatically_receive_bacterial_regimen():
    request,_,_=start_plan('neonatal_pneumonia',initial={'suspected_pathogen':'viral'})
    _,plans=run_policy(request)
    assert not any(a.concept.startswith('web:abx_') for p in plans for a in p.actions)


def test_culture_reports_obey_assumed_availability_and_preview_does_not_guess_future():
    request,_,_=start_plan('neonatal_pneumonia')
    _,plans=run_policy(request)
    for i,plan in enumerate(plans):
        culture=next(a for a in plan.actions if a.concept=='web:culture')
        assert ('negative' in culture.parameters['allowed_values'])==(i*3>=24)
    state=PatientState(timestamp=request.start_time,context=patient_context(request))
    future=plan_step(request,state,9,preview=True)
    assert future.record['preview_only']
    assert all(a.kind=='inquiry' for a in future.actions)


def test_api_predictor_receives_local_guidance_and_rejects_premature_culture_results():
    request,state,plan=start_plan('neonatal_pneumonia')
    responses=DemoPredictor(request).predict(state,plan.actions)
    responses[next(a.action_id for a in plan.actions if a.concept=='web:culture')]='negative'
    text=json.dumps({'outcomes':[{'action_id':a.action_id,'value':responses[a.action_id],'unit':a.unit}
                                for a in plan.actions if a.kind=='inquiry']})
    calls=[]
    class Transport:
        def __init__(self): self.chat=SimpleNamespace(completions=self)
        def create(self,**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content=text),finish_reason='stop')])
        def close(self): pass
    client=JSONClient(APIConfig(api_key='test',repair_attempts=0),Transport())
    predictor=APIPredictor(client,clinical_guidance=guidance_for(request.scenario_id),initial_conditioning=request.initial_state.model_dump())
    with pytest.raises(ModelResponseError): predictor.predict(state,plan.actions)
    payload=json.loads(calls[0]['messages'][1]['content'])
    assert payload['clinical_guidance']['rules']
    assert payload['initial_conditioning']['spo2']==90
    assert payload['history']==[]
    assert 'pituitary' not in json.dumps(payload).lower()


def test_guideline_documents_and_neonatal_validation(tmp_path):
    with TestClient(create_app(tmp_path/'web.sqlite')) as client:
        for identifier in SOURCES:
            result=client.get(f'/api/guidelines/{identifier}/document')
            assert result.status_code==200 and result.content.startswith(b'%PDF')
        assert client.get('/api/guidelines/11102_2025_Article_1505/document').status_code==404
        assert client.post('/api/generations',json={'patient':{'age':58}}).status_code==422
        assert client.post('/api/generations',json={'patient':{'postnatal_age_hours':672}}).status_code==422
        assert client.post('/api/generations',json={'scenario_id':'neonatal_rds','patient':{'gestational_weeks':38}}).status_code==422


def test_coarse_time_grid_does_not_backdate_or_perpetually_continue_antibiotics():
    request,_,_=start_plan(initial={'infection_suspected':True},interval_hours=24)
    _,plans=run_policy(request)
    stopping=[p for p in plans if any(a.concept=='web:abx_stop' for a in p.actions)]
    assert len(stopping)==1
    assert stopping[0].record['warnings']
    assert (stopping[0].timestamp-request.start_time).total_seconds()/3600==48


def test_changed_guideline_is_not_silently_treated_as_reviewed(tmp_path,monkeypatch):
    from ehrworld.web import guidelines
    original=guidelines.source_path
    changed=tmp_path/'changed.pdf'
    changed.write_bytes(b'%PDF-changed-reference')
    monkeypatch.setattr(guidelines,'source_path',lambda source_id: changed if source_id=='pneumonia-2025' else original(source_id))
    with TestClient(create_app(tmp_path/'web.sqlite')) as client:
        source=client.get('/api/guidelines').json()['sources'][0]
        assert source['available'] and not source['reviewed']
        identifier=client.post('/api/generations',json={'backend':'demo'}).json()['id']
        result=wait_job(client,identifier)
        assert result['status']=='failed' and result['completed_steps']==0
        assert result['error']['code']=='guideline_source_changed'
