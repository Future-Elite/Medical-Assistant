from __future__ import annotations

import copy
import hashlib
import json
import time
import zipfile
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from xml.sax.saxutils import escape

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI
from pydantic import ValidationError

from ehrdreamer.contracts import Intake, GenerateRequest, Decision, Parameters, AgentView, OutcomeProposal
from ehrdreamer.environment import PatientEnvironment, ActionRejected
from ehrdreamer.models import Models
from ehrdreamer.policy import DemoDynamics, DemoDoctor
from ehrdreamer.outcome import DemoOutcome, build_discharge, outcome_constraints, validate_outcome
from ehrdreamer.source import SourceCatalog,FIELDS,extract_intake
from ehrdreamer.inverse import infer
from ehrworld.llm import APIConfig,JSONClient,ModelResponseError
from ehrworld.web.app import create_app as _create_app


def create_app(*args, **kwargs):
    """Keep the former ML engine's regression suite explicit and isolated."""
    return _create_app(*args, dreamer_legacy_ml=True, **kwargs)


def workbook(path,count=14):
    """Minimal synthetic XLSX fixture; no user workbook is edited or copied."""
    rows=[FIELDS]
    for i in range(count):
        data={field:'SOURCE_FUTURE_SECRET' for field in FIELDS}
        data.update({'性别':'男','年龄':'4天','住院号':f'PRIVATE_ID_{i}','入院时间':'2024.01.01 08:00','出院时间':'2024.01.05',
                     '孕周':'32','入院时主要症状及体征':'患儿男，初生。主诉：呼吸急促。母孕32周+2天，出生体重1600g，剖宫产。查体：PRIVATE_LATER_EXAM',
                     '出院诊断':'新生儿肺炎、RDS、SOURCE_FUTURE_SECRET'})
        rows.append([data[f] for f in FIELDS])
    cells=[]
    for r,row in enumerate(rows,1):
        cells.append(f'<row r="{r}">'+''.join(f'<c r="{chr(65+c)}{r}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>' for c,v in enumerate(row))+'</row>')
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:R'+str(len(rows))+'"/><sheetData>'+''.join(cells)+'</sheetData></worksheet>')
    return path


@pytest.fixture
def source(tmp_path):return workbook(tmp_path/'discharge.xlsx')


def make_env(seed=42,max_operations=96,max_hours=168):
    intake=Intake(sex='男',gestational_weeks=32,gestational_days=2,postnatal_age_hours=0,birth_weight_g=1600,
                  chief_complaint='呼吸急促',symptoms=('呼吸急促',))
    req=GenerateRequest(case_id='fixture',backend='demo',seed=seed,max_operations=max_operations,max_hours=max_hours,
                        start_time=datetime(2026,11,2,8,tzinfo=timezone(timedelta(hours=8))))
    return PatientEnvironment(intake,req,'ED-TEST',{}, {})


def action(kind,target='',**kwargs):
    return Decision(action_type=kind,target=target,rationale='synthetic test action',**kwargs)


def wait(client,path):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        r=client.get(path);assert r.status_code==200,r.text
        job=r.json()
        if job['status'] not in ('queued','running'):return job
        time.sleep(.01)
    pytest.fail('Dreamer job did not finish')


def test_source_all_fields_archived_but_initial_view_has_no_future_or_ids(tmp_path,source):
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    catalog=SourceCatalog(source,tmp_path/'source.sqlite')
    metadata=catalog.summary();case=catalog.rows[0]
    assert metadata['records']==14 and metadata['fields']==18
    assert set(case['raw'])==set(FIELDS)
    public=json.dumps(catalog.public(case),ensure_ascii=False)
    assert 'SOURCE_FUTURE_SECRET' not in public and 'PRIVATE_' not in public
    assert case['intake']['postnatal_age_hours']==0 and case['intake']['gestational_days']==2
    assert case['intake']['birth_weight_g']==1600
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    group=case['group_id'];p=catalog.priors(group)
    case['raw']['出院诊断']='changed future truth'
    assert catalog.priors(group)==p  # target record never calibrates its own initial prior


def test_unknown_and_conflicting_intake_are_not_filled_as_known_normal():
    raw={f:'' for f in FIELDS};raw.update({'孕周':'无提及孕周','年龄':'4天','入院时主要症状及体征':'主诉：无咳嗽，气促。查体：胎龄32周，出生体重2000g'})
    intake,flags,_,_,_=extract_intake(raw,'Sheet1',2)
    assert intake['gestational_weeks'] is None and intake['postnatal_age_hours'] is None
    assert intake['birth_weight_g'] is None and '咳嗽' not in intake['symptoms']
    assert flags
    raw.update({'孕周':'30','入院时主要症状及体征':'主诉：气促。母孕32周+1天。'})
    intake,flags,_,_,_=extract_intake(raw,'Sheet1',2)
    assert intake['gestational_weeks'] is None and any('冲突' in f for f in flags)


def test_batch_preview_uses_distinct_source_groups_and_reproducible_budgets(tmp_path,source):
    from ehrdreamer.contracts import BatchRequest
    c=SourceCatalog(source,tmp_path/'source.sqlite');body=BatchRequest(backend='demo')
    first=c.plan(body)
    assert first==c.plan(body)
    assert len({p['case']['id'] for p in first['patients']})==10
    assert [sum(p['length_group']==g for p in first['patients']) for g in ('short','medium','long')]==[3,5,2]
    limits={'short':(24,32),'medium':(48,64),'long':(80,96)}
    for p in first['patients']:assert limits[p['length_group']][0]<=p['max_operations']<=limits[p['length_group']][1]
    assert len({c.get(p['case']['id'])['group_id'] for p in first['patients']})==10


def test_report_delay_sampling_snapshot_and_repeat_read_are_stable():
    e=make_env();d=DemoDynamics()
    e.execute(action('order_exam','vitals'),d)
    e.execute(action('order_exam','blood_gas'),d)
    expected=copy.deepcopy(e.pending[0]['items'])
    assert e.latest('blood_gas') is None
    assert not any(o['target']=='blood_gas' for o in e.view().observations)
    e.execute(action('start_treatment','cpap',parameters=Parameters(fio2=.35,pressure_cm_h2o=6)),d)
    e.advance_to(.49);assert e.latest('blood_gas') is None
    e.advance_to(2)
    report=copy.deepcopy(e.latest('blood_gas'))
    assert report['items']==expected and report['collected_at_hours']==0 and report['time_hours']==.5
    e.view();e.advance_to(2);assert e.latest('blood_gas')==report


def test_treatment_returns_no_observation_and_effect_requires_time():
    e=make_env();d=DemoDynamics();e.execute(action('order_exam','vitals'),d)
    before=copy.deepcopy(e.hidden)
    receipt=e.execute(action('start_treatment','cpap',parameters=Parameters(fio2=.3,pressure_cm_h2o=6)),d)
    assert receipt['observation'] is None and e.hidden==before
    assert 'cpap' in e.treatments
    assert e.events[-1]['event_type']=='treatment' and e.events[-1]['value'] is None
    assert e.phase=='treatment'
    e.advance_to(12);assert e.hidden.values==before.values
    e.advance_to(24);assert e.hidden.values!=before.values
    assert e.hidden.updated_hours['spo2']==0 and e.hidden.updated_hours['crp']==24


def test_learned_action_covariates_do_not_impose_a_cure_or_dose_formula():
    a,b=make_env(),make_env();d=DemoDynamics()
    for e in (a,b):
        e.execute(action('order_exam','vitals'),d);e.execute(action('order_exam','infection_panel'),d);e.advance_to(2)
    a.execute(action('start_treatment','antimicrobial'),d)
    a.advance_to(24);b.advance_to(24)
    assert a.hidden.updated_hours['crp']==24
    assert a.capabilities['treatment_effect_identification'].endswith('not_causal')
    a.execute(action('stop_treatment','antimicrobial'),d)
    assert 'antimicrobial' not in a.treatments


def test_failed_dynamics_patch_rolls_back_state_events_clock_rng():
    e=make_env();e.execute(action('order_exam','vitals'),DemoDynamics())
    before=(copy.deepcopy(e.hidden),copy.deepcopy(e.events),e.operations,e.time,e.rng.getstate())
    class Broken:
        def prepare_action(self,*args):raise ModelResponseError('private upstream content')
    with pytest.raises(ModelResponseError):e.execute(action('start_treatment','oxygen',parameters=Parameters(fio2=.3)),Broken())
    assert (e.hidden,e.events,e.operations,e.time,e.rng.getstate())==before
    assert not e.treatments


def test_unsupported_observation_never_falls_back_to_a_handwritten_report():
    e=make_env();before=copy.deepcopy(e.hidden)
    with pytest.raises(ActionRejected):e.execute(action('order_exam','lung_ultrasound'),DemoDynamics())
    with pytest.raises(ActionRejected):e.execute(action('order_exam','culture'),DemoDynamics())
    assert e.hidden==before and not e.pending


def test_doctor_view_and_diagnosis_never_reveal_hidden_state_or_future_report():
    e=make_env();d=DemoDynamics();e.execute(action('order_exam','blood_gas'),d)
    body=e.view().model_dump()
    assert not {'hidden','priors','raw','source','terminal_outcome'} & set(body)
    assert body['observations']==[] or body['observations']==()
    with pytest.raises(ValidationError):AgentView.model_validate({**body,'hidden':{}})
    with pytest.raises(ActionRejected):e.execute(action('diagnose','rds',diagnosis='rds',evidence_ids=('FUTURE_REPORT',)),d)
    with pytest.raises(ActionRejected):e.execute(action('propose_discharge'),d)
    with pytest.raises(ActionRejected):e.execute(action('order_exam','blood_gas'),d)
    with pytest.raises(ActionRejected):e.advance_to(-1)


def test_budget_exhaustion_does_not_produce_cure_or_discharge():
    e=make_env(max_operations=8);m=Models(e.request)
    while e.operations<8:
        decision=m.policy.decide(e.view());e.execute(decision,m.dynamics)
    e.finish_budget();result=build_discharge(e,m.outcome.summarize(e))
    assert e.terminal=='truncated' and not e.public_record()['encounter']['complete_episode']
    assert result['disposition']=='ongoing' and result['document_type']=='interim_outcome'
    assert result['overall_outcome']!='resolved'
    constraints=outcome_constraints(e)
    bad=m.outcome.summarize(e).model_copy(update={'disposition':'home','overall_outcome':'resolved'})
    with pytest.raises(ValueError):validate_outcome(bad,constraints)


def test_complete_demo_has_linked_events_three_phases_and_unfabricated_final_record(tmp_path,source):
    with TestClient(create_app(tmp_path/'web.sqlite',dreamer_source_path=source)) as client:
        cases=client.get('/api/dreamer/cases').json()['items']
        response=client.post('/api/dreamer/generations',json={'case_id':cases[0]['id'],'backend':'demo','max_operations':128})
        assert response.status_code==202,response.text
        job=wait(client,'/api/dreamer/generations/'+response.json()['id'])
        assert job['status']=='completed',job.get('error')
        record=job['record'];assert record['discharge_summary']
        assert {e['phase'] for e in record['events']}=={'assessment','treatment','discharge'}
        known=set()
        for event in record['events']:
            assert set(event['evidence_ids'])<=known
            if event['parent_order_id']:assert event['parent_order_id'] in known
            known.add(event['event_id'])
            if event['event_type']=='treatment':assert event['value'] is None
        assert all(a['time_hours']<=b['time_hours'] for a,b in zip(record['events'],record['events'][1:]))
        assert 'private' not in job and 'SOURCE_FUTURE_SECRET' not in json.dumps(job)
        research=client.get(f"/api/dreamer/generations/{job['id']}/research").json()
        assert research['policy_has_access'] is False and research['trace']
        assert len({t['state_hash'] for t in research['trace']})>1
        for fmt in ('json','csv'):
            export=client.get(f"/api/dreamer/generations/{job['id']}/export?format={fmt}")
            assert export.status_code==200 and 'attachment' in export.headers['content-disposition']
            assert 'PRIVATE_ID_' not in export.text and 'SOURCE_FUTURE_SECRET' not in export.text
        reports=[r for r in record['observations'] if r['target']=='blood_gas']
        if len(reports)>1:
            before=copy.deepcopy(record)
            result=client.post(f"/api/dreamer/generations/{job['id']}/inverse",json={'before_event_id':reports[0]['event_id'],'after_event_id':reports[-1]['event_id']})
            assert result.status_code==200 and result.json()['ambiguous'] and not result.json()['online_state_modified']
            assert client.get('/api/dreamer/generations/'+job['id']).json()['record']==before


def test_ten_patient_batch_exports_explicit_episode_completeness_and_restarts(tmp_path,source):
    path=tmp_path/'web.sqlite'
    with TestClient(create_app(path,dreamer_source_path=source)) as client:
        preview=client.post('/api/dreamer/batches/preview',json={'backend':'demo'}).json()
        response=client.post('/api/dreamer/batches',json={'backend':'demo','source_sha256':preview['source']['source_sha256']})
        assert response.status_code==202
        identifier=response.json()['id'];batch=wait(client,'/api/dreamer/batches/'+identifier)
        assert batch['status']=='completed',[(p['status'],p.get('error')) for p in batch['patients']]
        assert batch['completed_patients']==10
        assert [p['request']['max_operations'] for p in batch['patients']]==[p['max_operations'] for p in preview['patients']]
        exported=client.get(f'/api/dreamer/batches/{identifier}/export').json()
        assert len(exported['patients'])==10
        assert all(p['encounter']['complete_episode']==(p['encounter']['terminal_status'] in ('discharged','transferred')) for p in exported['patients'])
    with TestClient(create_app(path,dreamer_source_path=source)) as client:
        assert client.get('/api/dreamer/batches/'+identifier).json()['status']=='completed'


def test_api_failure_preserves_progress_no_demo_fallback_and_batch_retry_slot(tmp_path,source):
    failed=[False]
    def factory(req):
        models=Models(req.model_copy(update={'backend':'demo'}))
        if not failed[0]:
            failed[0]=True
            class BrokenDoctor:
                def decide(self,view):raise ModelResponseError('HTTP 403 secret-value-do-not-expose')
            models.policy=BrokenDoctor()
        return models
    with TestClient(create_app(tmp_path/'web.sqlite',dreamer_source_path=source,dreamer_models_factory=factory)) as client:
        identifier=client.post('/api/dreamer/batches',json={'backend':'api'}).json()['id']
        batch=wait(client,'/api/dreamer/batches/'+identifier)
        assert batch['failed_patients']==1 and batch['completed_patients']==9
        previous=next(p for p in batch['patients'] if p['status']=='failed')
        job=client.get('/api/dreamer/generations/'+previous['id']).json()
        assert job['request']['backend']=='api' and job['error']['code']=='upstream_forbidden'
        assert 'secret-value' not in json.dumps(job)
        assert client.get(f"/api/dreamer/generations/{job['id']}/export").status_code==409
        response=client.post(f"/api/dreamer/generations/{job['id']}/retry")
        assert response.status_code==202
        new=wait(client,'/api/dreamer/generations/'+response.json()['id'])
        assert new['status']=='completed' and new['request']['model']==job['request']['model']
        batch=client.get('/api/dreamer/batches/'+identifier).json()
        assert batch['completed_patients']==10 and job['id'] not in batch['job_ids']
        assert client.post(f"/api/dreamer/generations/{job['id']}/retry").status_code==409


def test_source_change_and_missing_source_do_not_create_fake_cases(tmp_path,source):
    with TestClient(create_app(tmp_path/'web.sqlite',dreamer_source_path=source)) as client:
        identifier=client.get('/api/dreamer/cases').json()['items'][0]['id']
        response=client.post('/api/dreamer/generations',json={'case_id':identifier,'source_sha256':'0'*64,'backend':'demo'})
        assert response.status_code==409
        assert client.get('/api/dreamer/generations').json()['items']==[]
    with TestClient(create_app(tmp_path/'missing.sqlite',dreamer_source_path=tmp_path/'missing.xlsx')) as client:
        assert client.get('/api/dreamer/source').status_code==400


def test_real_api_wrappers_keep_roles_separate_and_never_send_future_source(tmp_path,source,monkeypatch):
    monkeypatch.setenv('EHRWORLD_API_KEY','fake-key')
    bodies=[]
    def handler(request):
        body=json.loads(request.content);payload=json.loads(body['messages'][1]['content']);bodies.append((body,payload))
        if 'agent_view' in payload:
            result=DemoDoctor().decide(AgentView.model_validate(payload['agent_view'])).model_dump(mode='json')
        elif 'constraints' in payload:
            con=payload['constraints'];ids=con['evidence_ids'][-4:]
            result={'disposition':con['disposition'],'overall_outcome':con['allowed_overall'][0],
                    'per_condition':[{'condition_id':k,'status':'improved' if 'improved' in statuses else 'unknown',
                                      'evidence_ids':con['condition_evidence_ids'][k][-4:]} for k,statuses in con['conditions'].items()],
                    'selected_evidence_ids':ids,'follow_up_codes':['neonatal_review']}
        else:raise AssertionError('unexpected role payload')
        return httpx.Response(200,json={'id':'test','object':'chat.completion','created':0,'model':body['model'],
              'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
    def client_factory(config):
        sdk=OpenAI(base_url='https://example.invalid/v1',api_key='fake-key',http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        return JSONClient(config,sdk)
    def model_factory(req):return Models(req,client_factory)
    with TestClient(create_app(tmp_path/'web.sqlite',dreamer_source_path=source,dreamer_models_factory=model_factory)) as client:
        identifier=client.get('/api/dreamer/cases').json()['items'][0]['id']
        response=client.post('/api/dreamer/generations',json={'case_id':identifier,'backend':'api','max_operations':64,
                'policy_model':'Qwen3.8-27B','dynamics_model':'MiMo-V2.6-Flash','outcome_model':'DeepSeek-V4.1-Flash'})
        job=wait(client,'/api/dreamer/generations/'+response.json()['id'])
        assert job['status']=='completed',job.get('error')
        assert all(job['usage'][role]['requests']>0 for role in ('policy','outcome'))
    for body,payload in bodies:
        assert 'SOURCE_FUTURE_SECRET' not in json.dumps(payload) and 'PRIVATE_' not in json.dumps(payload)
        if 'agent_view' in payload:
            assert body['model']=='Qwen3.8-27B' and body['enable_thinking'] is False
            assert 'hidden_state' not in payload
        else:assert body['model']=='DeepSeek-V4.1-Flash' and body['thinking']['type']=='disabled'


def test_maternal_fever_is_a_risk_and_never_an_infant_symptom():
    intake,flags,_,_,_=extract_intake({'性别':'男','孕周':'39',
        '入院时主要症状及体征':'患儿男，初生。主诉：母亲发热，血象升高。母孕39周，出生体重3000g。查体：无发热。'},'Sheet1',2)
    assert '母体感染风险' in intake['perinatal_risks']
    assert '发热' not in intake['symptoms']
    assert intake['chief_complaint']=='围产感染风险待评估'


def test_jaundice_requires_own_report_and_outcome_not_inherited_from_discharge():
    e=make_env();m=Models(e.request)
    e.execute(action('order_exam','vitals'),m.dynamics)
    with pytest.raises(ActionRejected):
        e.execute(action('diagnose','jaundice',diagnosis='jaundice',evidence_ids=(e.observations[0]['event_id'],)),m.dynamics)
    e.conditions=[{'condition_id':'C-J','code':'jaundice','name':'新生儿黄疸','evidence_ids':[]}]
    e.terminal='discharged'
    constraints=outcome_constraints(e)
    assert 'improved' not in constraints['conditions']['C-J']
    assert '黄疸缺少近期连续复查证据' in e.discharge_blockers()
    assert m.outcome.summarize(e).per_condition[0].status=='unknown'


def test_inverse_upstream_failure_is_sanitized_and_bad_pair_never_calls_model(tmp_path,source):
    broken=[False]
    def factory(req):
        if broken[0]:raise ModelResponseError('HTTP 403 PRIVATE_KEY_SENTINEL')
        return Models(req.model_copy(update={'backend':'demo'}))
    with TestClient(create_app(tmp_path/'web.sqlite',dreamer_source_path=source,dreamer_models_factory=factory)) as client:
        identifier=client.get('/api/dreamer/cases').json()['items'][0]['id']
        job=wait(client,'/api/dreamer/generations/'+client.post('/api/dreamer/generations',json={'case_id':identifier,'backend':'demo'}).json()['id'])
        vs=[r for r in job['record']['observations'] if r['target']=='blood_gas'];assert len(vs)>1
        broken[0]=True
        url=f"/api/dreamer/generations/{job['id']}/inverse"
        valid={'before_event_id':vs[0]['event_id'],'after_event_id':vs[-1]['event_id']}
        response=client.post(url,json=valid)
        assert response.status_code==502 and 'PRIVATE_KEY_SENTINEL' not in response.text
        assert client.post(url,json={**valid,'after_event_id':vs[0]['event_id']}).status_code==422
