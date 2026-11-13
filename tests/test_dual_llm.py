"""Dual-LLM protocol tests use synthetic patients and mocked SDK transport only."""
import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import openpyxl
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI
from pydantic import ValidationError

from ehrworld.llm import APIConfig, JSONClient
from ehrworld.web.app import create_app
from ehrdreamer.source import FIELDS
from ehrdreamer.dual.contracts import GenerationRequest, BatchRequest, CandidatePlan, PlanAction, TransitionReply
from ehrdreamer.dual.source import extract_baseline
from ehrdreamer.dual.references import rules_for, score
from ehrdreamer.dual.dynamics import LLMDynamics, DynamicsError, time_segments
from ehrdreamer.dual.environment import PatientEnvironment
from ehrdreamer.dual.models import DualModels
from ehrdreamer.dual.planner import validate_plan, PlanningError


def case():
    intake={'sex':'男','gestational_weeks':32,'postnatal_age_hours':0,'birth_weight_g':1600,
            'chief_complaint':'气促','symptoms':['气促'],'perinatal_risks':[]}
    baseline=extract_baseline({'入院时间':'2024-01-01','入院时主要症状及体征':'pH:7.20，PaCO2:62mmHg，CRP:<0.50mg/L'})
    return {'intake':intake,'baseline':baseline}


def env():
    c=case();req=GenerationRequest(case_id='synthetic',max_cycles=2)
    return PatientEnvironment(c,req,'SYNTHETIC',rules_for(c['intake'],c['baseline']),{})


class SyntheticTransport:
    """Known test transition, never used as a clinical or offline fallback model."""
    def __init__(self):self.calls=[];self.lock=threading.Lock();self.break_execution=False

    def handle(self,request):
        body=json.loads(request.content);payload=json.loads(body['messages'][1]['content'])
        with self.lock:self.calls.append((body,payload))
        if 'predict_at_hours' in payload:
            if self.break_execution and payload['branch_mode']=='execution':
                return httpx.Response(403,json={'error':{'message':'PRIVATE_GATEWAY_SENTINEL'}})
            active=any(e['target']=='cpap' and e['operation']=='start' for e in payload['actions'])
            # Deliberately artificial fixture numbers, not medical equations.
            vals={'pH':7.35 if active else 7.20,'PaCO2(mmHg)':42 if active else 62,'CRP(mg/L)':.4}
            result={label:vals.get(label,1) for label in payload['output_keys']}
        elif payload.get('task')=='propose_plans':
            observed=payload['visible_patient']['current']['id']
            operation='adjust' if payload['visible_patient']['active_treatments'] else 'start'
            result={'status':'ready','plans':[{'candidate_id':'cpap-plan','actions':[{'operation':operation,'target':'cpap',
                     'parameters':{'fio2':.3,'pressure_cm_h2o':6},'evidence_ids':[observed]}],
                     'review_metrics':['ph','paco2'],'rationale':'合成测试候选','uncertainty':'仅为测试'}],
                     'working_diagnoses':['respiratory_distress'],'information_request':[],'issue':''}
            result['clinical_assessment']={'mode':'treatment_required','evidence_ids':[observed],
                'guideline_rule_ids':['RDS-CPAP'],'rationale':'合成病例有呼吸窘迫，采用已检索的早产儿无创支持依据。'}
        elif payload.get('task')=='select_next_action':
            result={'status':'selected','selected_plan_id':payload['approved_plan_id'],'rationale':'依据程序合规评分选择'}
        else:
            result={'evidence_ids':payload['allowed_evidence_ids'][-2:],'unresolved_metric_keys':[]}
        return httpx.Response(200,json={'id':'synthetic','object':'chat.completion','created':0,'model':body['model'],
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})

    def client(self,config):
        sdk=OpenAI(base_url='https://example.invalid/v1',api_key='unit-test-token',
                   http_client=httpx.Client(transport=httpx.MockTransport(self.handle)))
        return JSONClient(config,sdk)


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(APIConfig,'from_env',classmethod(lambda cls:APIConfig(base_url='https://example.invalid/v1',api_key='unit-test-token')))
    transport=SyntheticTransport()
    return transport,lambda req:DualModels(req,transport.client)


def test_baseline_preserves_censoring_and_excludes_future_and_maternal_labs():
    raw={'入院时间':'2024-01-01','入院时主要症状及体征':'孕母CRP:99mg/L。患儿气促。',
         '实验室检查及主要会诊':'2024-01-01 PaCO2:62mmHg CRP:<0.50mg/L 2024-01-03 PaCO2:12mmHg',
         '诊疗过程':'pH:7.1','出院时情况':'pH:7.4'}
    result=extract_baseline(raw)
    assert result['paco2']['value']==62 and result['crp']['value'] is None
    assert result['crp']['bound']==.5 and result['crp']['comparator']=='<' and 'ph' not in result
    assert extract_baseline({'入院时间':'2024-01-01','实验室检查及主要会诊':'2024-01-02 pH:7.3'})=={}


def test_fixed_score_cannot_improve_by_missing_values_or_unassessed_metrics():
    e=env();assert score(e.state['labs'],e.rules)['abnormal_count']==2
    labs=copy.deepcopy(e.state['labs']);labs['ph']['value']=None
    assert score(labs,e.rules)['abnormal_count'] is None
    assert 'crp' in score(e.state['labs'],e.rules)['unassessed_keys']
    labs=copy.deepcopy(e.state['labs']);labs['ph']['value']=7.35;labs['paco2']['value']=70
    assert score(labs,e.rules)['abnormal_count']==1


def test_time_segments_preserve_order_parallel_exposure_and_stopped_history():
    ledger=[{'event_id':'A','at_hours':0,'operation':'start','target':'oxygen','parameters':{'fio2':.3}},
            {'event_id':'B','at_hours':6,'operation':'start','target':'antimicrobial','parameters':{}},
            {'event_id':'C','at_hours':12,'operation':'stop','target':'oxygen','parameters':{}}]
    seg=time_segments(ledger,0,18)
    assert [(s['from_hours'],s['to_hours']) for s in seg]==[(0,6),(6,12),(12,18)]
    assert [a['target'] for a in seg[-1]['active_treatments']]==['antimicrobial']
    assert seg[-1]['active_treatments'][0]['elapsed_at_start_hours']==6


def test_zero_time_and_same_request_do_not_repeat_llm_transition(wired):
    t,factory=wired;e=env();m=factory(e.request)
    try:
        original=copy.deepcopy(e.state)
        assert m.dynamics.transition(e.state,e.context,[],0,branch_id='x',mode='execution')==original
        assert not t.calls
        def call(_):return m.dynamics.transition(e.state,e.context,[],12,branch_id='x',mode='execution')
        with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(call,range(3)))
        assert results[0]==results[1]==results[2] and len(t.calls)==1 and e.state==original
    finally:m.close()


def test_query_freezes_sample_and_publishes_collection_values_after_delay(wired):
    _,factory=wired;e=env();m=factory(e.request)
    try:
        e.advance_to(2,m.dynamics)
        snap=e.collect(['ph'],sample_id='delayed',delay=4)
        assert e.read_sample('delayed') is None
        e.advance_to(12,m.dynamics)
        assert e.observations[-1]['collected_at_hours']==2 and e.observations[-1]['time_hours']==6
        assert e.read_sample('delayed')==snap['items']
        calls=len(m.dynamics.client.calls)
        assert e.collect(['ph'],sample_id='delayed')==e.samples['delayed']
        e.read_sample('delayed');assert len(m.dynamics.client.calls)==calls
    finally:m.close()


def test_planning_uses_visible_anchors_not_actual_hidden_values_and_hides_rewards(wired):
    t,factory=wired;e=env();m=factory(e.request)
    try:
        e.state['labs']['paco2']['value']=999
        original=copy.deepcopy(e.__dict__)
        plan,evaluations,_,_=m.policy.decide(e,m.dynamics)
        assert plan.candidate_id=='cpap-plan' and e.state==original['state'] and e.ledger==original['ledger']
        assert e.agent_steps and all(s['stage']!='execution' for s in e.agent_steps)
        forecasts=[p for _,p in t.calls if p.get('branch_mode')=='planning']
        assert forecasts and all(p['current']['labs']['PaCO2(mmHg)']==62 for p in forecasts)
        proposals=[p for _,p in t.calls if p.get('task')=='propose_plans']
        assert proposals[0]['visible_patient']['current']['labs']['PaCO2(mmHg)']==62
        assert 999 not in proposals[0]['visible_patient']['current']['labs'].values()
        assert len({r['forecast_end_hours'] for r in evaluations})==1
        for p in forecasts:
            text=json.dumps(p)
            assert 'expected_abnormal_count' not in text and 'reference_rules' not in text
            assert 'approved_plan_id' not in text and 'objective' not in p
    finally:m.close()


def test_execute_only_first_action_and_rollback_on_failed_execution(wired):
    t,factory=wired;e=env();m=factory(e.request)
    plan=CandidatePlan(candidate_id='two-steps',actions=(PlanAction(operation='start',target='cpap'),
            PlanAction(operation='stop',target='cpap',offset_hours=6)),review_metrics=('ph','paco2'),rationale='合成测试')
    before=copy.deepcopy(e.__dict__)
    t.break_execution=True
    try:
        with pytest.raises(Exception):e.execute_first(plan,[],'test',(),m.dynamics)
        assert e.__dict__==before
        t.break_execution=False
        e.execute_first(plan,[],'test',(),m.dynamics)
        assert 'cpap' in e.active and len(e.ledger)==1 and e.ledger[0]['operation']=='start'
        assert e.time==6  # Replan at the next planned boundary; never execute its stop implicitly.
        assert e.observations[-1]['items'][0]['origin']=='llm_prediction'
        assert e.visible_score()['abnormal_count']==0
    finally:m.close()


def test_unknown_or_conflicting_actions_are_rejected_before_prediction():
    e=env()
    for plan in [CandidatePlan(candidate_id='x',actions=(PlanAction(operation='start',target='unknown'),),rationale='test'),
                 CandidatePlan(candidate_id='x',actions=(PlanAction(operation='start',target='oxygen'),PlanAction(operation='start',target='cpap')),rationale='test')]:
        with pytest.raises((PlanningError,DynamicsError)):validate_plan(plan,{},set(),e.state['labs'],24)


def workbook(path,count=12):
    wb=openpyxl.Workbook();ws=wb.active;ws.append(FIELDS)
    for i in range(count):
        row={'性别':'男','住院号':f'PRIVATE_ID_{i}','入院时间':'2024-01-01','出院时间':'2024-01-05','孕周':'32',
             '入院时主要症状及体征':'患儿男，初生。主诉：气促。母孕32周，出生体重1600g。',
             '实验室检查及主要会诊':'2024-01-01 pH:7.20 PaCO2:62mmHg CRP:<0.50mg/L 2024-01-04 pH:7.44 PaCO2:12mmHg',
             '诊疗过程':'SOURCE_FUTURE_SENTINEL','出院时情况':'SOURCE_FUTURE_SENTINEL'}
        ws.append([row.get(k,'') for k in FIELDS])
    wb.save(path);return path


def wait(client,url):
    for _ in range(250):
        result=client.get(url).json()
        if result['status'] not in ('queued','running'):return result
        time.sleep(.02)
    pytest.fail('synthetic job timed out')


def test_default_api_uses_both_llms_and_protects_private_source(tmp_path,wired,monkeypatch):
    from ehrdreamer.learning import runtime
    def forbidden_weights():
        raise AssertionError('Default dual engine must not load legacy weights')
    monkeypatch.setattr(runtime,'get_engine',forbidden_weights)
    t,factory=wired;path=workbook(tmp_path/'source.xlsx')
    original=path.read_bytes()
    with TestClient(create_app(tmp_path/'web.sqlite',dreamer_source_path=path,dreamer_models_factory=factory)) as client:
        health=client.get('/api/dreamer/health').json()
        assert health['architecture']=='llm_policy_and_llm_dynamics' and not health['learning']['active']
        source=client.get('/api/dreamer/cases').json()['items'][0]
        reply=client.post('/api/dreamer/generations',json={'case_id':source['id'],'max_cycles':1,
            'policy_model':'Qwen3.8-27B','dynamics_model':'MiMo-V2.6-Flash','outcome_model':'DeepSeek-V4.1-Flash'})
        assert reply.status_code==202,reply.text
        job=wait(client,'/api/dreamer/generations/'+reply.json()['id'])
        assert job['status']=='completed',job.get('error')
        assert all(job['usage'][r]['requests']>0 for r in ('policy','dynamics','outcome'))
        assert job['record']['objective']['current_visible']['abnormal_count']==0
        assert job['terminal_status']=='treatment_completed' and not job['record']['encounter']['complete_episode']
        summary=job['record']['discharge_summary']
        crp=next(i for i in summary['monitored_indicator_changes'] if i['key']=='crp')
        assert crp['before'] is None and crp['before_comparator']=='<' and crp['before_bound']==.5
        assert crp['after'] is None and crp['after_comparator']=='<' and crp['after_bound']==.5
        assert crp['was_rechecked'] is False and crp['after_collected_at_hours']==0
        assert job['record']['observations'][0]['order_id']==job['record']['orders'][0]['order_id']
        assert 'private' not in job and 'state' not in job['record']
        assert client.get(f"/api/dreamer/generations/{job['id']}/export").status_code==200
        assert client.get(f"/api/dreamer/generations/{job['id']}/export?format=csv").status_code==200
        assert client.post('/api/dreamer/generations',json={'case_id':source['id'],'backend':'demo'}).status_code==422
        bad=client.post('/api/dreamer/generations',json={'case_id':source['id'],'reference_rules':[{'key':'ph','unit':'bad','lower':0,'upper':2,'source':'test'}]})
        assert bad.status_code==400
    assert path.read_bytes()==original
    for body,payload in t.calls:
        content=json.dumps(payload)
        assert 'PRIVATE_ID_' not in content and 'SOURCE_FUTURE_SENTINEL' not in content
        if 'predict_at_hours' in payload:
            assert body['model'] in ('MiMo-V2.6-Flash','Qwen3.8-27B')
        if body['model']=='Qwen3.8-27B':assert body['enable_thinking'] is False


@pytest.mark.parametrize('violation',['extra','unit','missing','nested','bool','numeric_string','nonfinite','negative','ph_bounds'])
def test_invalid_transition_protocol_is_rejected_without_state_write(violation,wired):
    t,_=wired;e=env();original=copy.deepcopy(e.state)
    def broken(request):
        response=t.handle(request);body=response.json();result=json.loads(body['choices'][0]['message']['content'])
        if violation=='extra':result['request_id']='unexpected'
        if violation=='unit':result['PaCO2(kPa)']=result.pop('PaCO2(mmHg)')
        if violation=='missing':result.pop('pH')
        if violation=='nested':result={'labs':result}
        if violation=='bool':result['pH']=True
        if violation=='numeric_string':result['pH']='7.3'
        if violation=='nonfinite':result['pH']=float('nan')
        if violation=='negative':result['PaCO2(mmHg)']=-1
        if violation=='ph_bounds':result['pH']=15
        body['choices'][0]['message']['content']=json.dumps(result)
        return httpx.Response(200,json=body)
    sdk=OpenAI(base_url='https://example.invalid/v1',api_key='unit-test-token',http_client=httpx.Client(transport=httpx.MockTransport(broken)))
    client=JSONClient(APIConfig(api_key='unit-test-token',repair_attempts=0),sdk)
    try:
        with pytest.raises(Exception):LLMDynamics(client).transition(e.state,e.context,[],12,branch_id='actual',mode='execution')
        assert e.state==original
    finally:client.close()


def test_reference_applicability_never_treats_unknown_neonatal_age_as_normal():
    c=case();intake={**c['intake'],'gestational_weeks':39,'postnatal_age_hours':None}
    assert 'crp' not in rules_for(intake,c['baseline'])
    intake['postnatal_age_hours']=0
    rules=rules_for(intake,c['baseline'])
    assert score(c['baseline'],rules)['statuses']['crp']=='normal'
    assert 'crp' not in rules_for({**intake,'postnatal_age_hours':220},c['baseline'],max_hours=72)


def test_same_time_new_sample_cannot_be_counted_as_another_review():
    e=env()
    with pytest.raises(DynamicsError):e.collect(['ph'],sample_id='another-sample')
    assert len(e.samples)==1 and len(e.orders)==1


def test_selection_cannot_override_program_score(wired):
    t,factory=wired;e=env();m=factory(e.request)
    original=m.policy.client.request
    def wrong_choice(system,payload,schema,**kwargs):
        if payload.get('task')=='select_next_action':
            from ehrdreamer.dual.contracts import PlanSelection
            return PlanSelection(status='selected',selected_plan_id='baseline-continue',rationale='不允许覆盖程序评分')
        return original(system,payload,schema,**kwargs)
    m.policy.client.request=wrong_choice
    try:
        with pytest.raises(PlanningError):m.policy.decide(e,m.dynamics)
        assert not e.ledger and e.time==0
    finally:m.close()


def test_batch_preserves_dynamics_model_and_exports_ten_patients(tmp_path,wired):
    _,factory=wired;path=workbook(tmp_path/'source.xlsx')
    with TestClient(create_app(tmp_path/'web.sqlite',dreamer_source_path=path,dreamer_models_factory=factory)) as client:
        body={'max_cycles':1,'dynamics_model':'MiMo-V2.6-Flash'}
        preview=client.post('/api/dreamer/batches/preview',json=body)
        assert preview.status_code==200 and len(preview.json()['patients'])==10
        reply=client.post('/api/dreamer/batches',json=body)
        assert reply.status_code==202,reply.text
        batch=wait(client,'/api/dreamer/batches/'+reply.json()['id'])
        assert batch['completed_patients']==10 and batch['failed_patients']==0
        assert all(p['models']['dynamics']['name']=='MiMo-V2.6-Flash' for p in batch['patients'])
        exported=client.get(f"/api/dreamer/batches/{batch['id']}/export").json()
        assert len(exported['patients'])==10 and 'private' not in json.dumps(exported)
