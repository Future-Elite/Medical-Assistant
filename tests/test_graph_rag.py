"""Graph retrieval and the reported clinical workflow failures, using synthetic data."""
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

from ehrworld.llm import APIConfig, JSONClient
from ehrworld.web.app import create_app
from ehrdreamer.dual.admission import extract_admission, clinical_findings
from ehrdreamer.dual.contracts import CandidatePlan, ClinicalAssessment, PlanAction
from ehrdreamer.dual.knowledge import get_graph, population_match
from ehrdreamer.dual.planner import PlanningError, validate_plan
from ehrdreamer.dual.dynamics import LLMDynamics
from ehrdreamer.dual.protocol import labs_json
from test_dual_llm import env, wired, workbook, wait, SyntheticTransport


def assessment(e, mode='treatment_required', rules=('RDS-CPAP',)):
    return ClinicalAssessment(mode=mode,evidence_ids=(e.observations[0]['event_id'],),
                              guideline_rule_ids=rules,rationale='仅为人工病例协议验证')


def test_graph_has_canonical_actions_real_relations_and_provenance():
    graph=get_graph()
    assert graph.canonical_action('nCPAP')=='cpap'
    assert graph.canonical_action('固尔苏')=='surfactant'
    assert 'supportive_care' not in graph.nodes
    assert all(e['source'] in graph.nodes and e['target'] in graph.nodes for e in graph.edges)
    assert any(e=={'source':'recommendation:RDS-CPAP','relation':'SUPPORTED_BY','target':'source:EU-RDS-2025'} for e in graph.edges)
    result=graph.retrieve(['LISA'],env().view())
    rule=next(r for r in result['recommendations'] if r['id']=='RDS-LISA')
    assert rule['evidence_paths'] and 'OPTIMIST-A' in result['sources']
    assert result['sources']['EU-RDS-2025']['pmid']=='41802129'
    blood_gas=next(p for p in result['available_monitoring_panels'] if p['id']=='blood_gas')
    assert {'ph','paco2'}<=set(blood_gas['metric_keys'])


def test_population_exclusions_survive_term_search_and_unknown_is_not_a_match():
    e=env();result=get_graph().retrieve(['观察','低血糖'],e.view())
    assert 'OBS-LOW-RISK' not in {r['id'] for r in result['recommendations']}
    assert 'OBS-LOW-RISK' in {r['id'] for r in result['excluded_recommendations']}
    assert population_match({'ga_min':35},{})==('conditional',['胎龄'])
    assert population_match({'preterm_or_low_birth_weight':True},{'ga_weeks':40,'birth_weight_g':3500})[0]=='not_applicable'
    assert population_match({'ga_min_exclusive':28},{'ga_weeks':28,'ga_precision':'weeks_only'})[0]=='conditional'


def test_no_treatment_cannot_be_maintained_even_if_a_forecast_would_score_zero():
    e=env();plan=CandidatePlan(candidate_id='empty-maintenance',actions=(PlanAction(operation='continue'),),rationale='人工测试')
    with pytest.raises(PlanningError,match='尚无活动治疗'):
        validate_plan(plan,{},set(),e.state['labs'],24)


def test_observation_requires_verified_applicability_and_stable_clinical_evidence():
    e=env();e.intake['gestational_weeks']=40;e.intake['clinical_findings']={'clinically_well':None}
    visible=e.view();knowledge=get_graph().retrieve(['观察'],visible)
    plan=CandidatePlan(candidate_id='observe',actions=(PlanAction(operation='wait'),),review_metrics=('ph','paco2'),rationale='人工测试')
    arguments=dict(assessment=assessment(e,'observe_without_treatment',('OBS-LOW-RISK',)),knowledge=knowledge,visible=visible)
    with pytest.raises(PlanningError,match='稳定临床评估'):
        validate_plan(plan,{},set(arguments['assessment'].evidence_ids),e.state['labs'],24,**arguments)
    e.intake['clinical_findings']['clinically_well']=True
    arguments['visible']=e.view()
    validate_plan(plan,{},set(arguments['assessment'].evidence_ids),e.state['labs'],24,**arguments)
    e.intake['symptoms']=['呼吸暂停'];arguments['visible']=e.view()
    with pytest.raises(PlanningError,match='危险征象'):
        validate_plan(plan,{},set(arguments['assessment'].evidence_ids),e.state['labs'],24,**arguments)


def test_citation_must_refer_to_a_retrieved_recommendation_for_that_action():
    e=env();knowledge=get_graph().retrieve(['光疗'],e.view());a=assessment(e,rules=('BILI-AGE-CURVE',))
    plan=CandidatePlan(candidate_id='bad-citation',actions=(PlanAction(operation='start',target='cpap'),),review_metrics=('ph','paco2'),rationale='人工测试')
    with pytest.raises(PlanningError,match='未支持或约束'):
        validate_plan(plan,{},set(a.evidence_ids),e.state['labs'],24,assessment=a,knowledge=knowledge,visible=e.view())
    knowledge=get_graph().retrieve(['抗生素'],e.view())
    a=assessment(e,rules=('EOS-NO-ROUTINE-ANTIBIOTIC',))
    plan=CandidatePlan(candidate_id='constraint-as-permission',actions=(PlanAction(operation='start',target='antimicrobial'),),review_metrics=('ph','paco2'),rationale='人工测试')
    with pytest.raises(PlanningError,match='未支持或约束'):
        validate_plan(plan,{},set(a.evidence_ids),e.state['labs'],24,assessment=a,knowledge=knowledge,visible=e.view())


def test_simultaneous_combination_executes_current_bundle_but_not_future_action(wired):
    _,factory=wired;e=env();m=factory(e.request)
    plan=CandidatePlan(candidate_id='bundle',actions=(PlanAction(operation='start',target='cpap'),
        PlanAction(operation='start',target='antimicrobial'),PlanAction(operation='stop',target='cpap',offset_hours=6)),review_metrics=('ph','paco2'),rationale='人工测试')
    try:
        validate_plan(plan,{},set(),e.state['labs'],24)
        e.execute_first(plan,[],'人工测试',(),m.dynamics)
        assert set(e.active)=={'cpap','antimicrobial'} and len(e.ledger)==2 and e.time==6
        assert all(i['at_hours']==0 for i in e.ledger)
        assert [s['cycle'] for s in e.agent_steps if s['stage']=='review']==[1]
    finally:m.close()


def test_initial_plan_has_no_automatic_continue_and_audits_real_steps(wired):
    t,factory=wired;e=env();m=factory(e.request);original=copy.deepcopy(e.state)
    try:
        plan,evaluations,_,_=m.policy.decide(e,m.dynamics)
        assert plan.candidate_id=='cpap-plan'
        assert all(r['candidate_id']!='baseline-continue' for r in evaluations)
        assert all(s['status']!='started' for s in e.agent_steps)
        assert {'graph_retrieval','clinical_assessment','proposal','candidate_evaluation','decision'}<={s['stage'] for s in e.agent_steps}
        assert e.state==original and not e.ledger
        payload=next(p for _,p in t.calls if p.get('task')=='propose_plans')
        assert payload['knowledge_context']['recommendations'] and 'source_checked' in json.dumps(payload)
    finally:m.close()


def test_agent_can_request_more_graph_evidence_and_the_query_is_actually_run(wired):
    _,factory=wired;e=env();m=factory(e.request);original=m.policy.client.request;called=False
    def request_more(system,payload,schema,**kwargs):
        nonlocal called
        if payload.get('task')=='propose_plans' and not called:
            called=True
            return schema.model_validate({'status':'needs_evidence','knowledge_queries':['LISA']})
        return original(system,payload,schema,**kwargs)
    m.policy.client.request=request_more
    try:
        m.policy.decide(e,m.dynamics)
        query=next(s for s in e.agent_steps if s['stage']=='graph_query')
        assert query['data']['queries']==['LISA']
        assert 'action:surfactant' in {n['id'] for n in query['data']['normalized_terms']}
        assert query['data']['graph']['edges']
    finally:m.close()


def test_graph_query_budget_stops_a_loop_without_state_changes(wired):
    _,factory=wired;e=env();m=factory(e.request)
    def request_more(system,payload,schema,**kwargs):
        return schema.model_validate({'status':'needs_evidence','knowledge_queries':['CPAP']})
    m.policy.client.request=request_more
    try:
        with pytest.raises(PlanningError,match='查询预算'):
            m.policy.decide(e,m.dynamics)
        assert len([s for s in e.agent_steps if s['stage']=='graph_query'])==2
        assert e.time==0 and not e.ledger
    finally:m.close()


def test_admission_imports_cbc_chemistry_and_signed_blood_gas_without_future_or_ids():
    raw={'入院时间':'2024-01-01','入院时主要症状及体征':'一般情况可。pH:7.20，PaCO2:62mmHg。',
         '实验室检查及主要会诊':'2024-01-01 血红蛋白:168g/L，血小板:220×10^9/L，钠:138mmol/L，cBase(Ecf):-3.2mmol/L，检查号:999999，C反应蛋白:<0.5mg/L\n2024-01-03 C反应蛋白:90mg/L'}
    labs=extract_admission(raw)
    assert labs['hemoglobin']['value']==168 and labs['platelet']['value']==220
    assert labs['sodium']['value']==138 and labs['crp']['bound']==.5
    base=next(i for i in labs.values() if i['name']=='cBase(Ecf)')
    assert base['value']==-3.2 and base['allows_negative']
    assert not any(i.get('value')==999999 for i in labs.values())
    assert clinical_findings(raw)['clinically_well'] is True


def test_conflicting_same_day_labs_are_not_arbitrarily_averaged():
    labs=extract_admission({'入院时间':'2024-01-01','实验室检查及主要会诊':
        '2024-01-01 白细胞:10 10^9/L，白细胞:20 10^9/L'})
    assert labs['wbc']['value'] is None and labs['wbc']['ambiguous_same_day']
    assert len(labs['wbc']['admission_results'])==2


def test_negative_base_excess_is_a_supported_signed_prediction():
    e=env();signed=extract_admission({'入院时间':'2024-01-01','实验室检查及主要会诊':'2024-01-01 cBase(Ecf):-3.2mmol/L'})
    e.state['labs'].update(signed);e.state['baseline'].update(signed)
    class Client:
        def request(self,system,payload,schema,**kwargs):
            values=labs_json(e.state['labs']);values['pH']=7.3
            reply=schema.model_validate(values);kwargs['validator'](reply);return reply
    proposed=LLMDynamics(Client()).transition(e.state,e.context,[],12,branch_id='signed',mode='execution')
    key=next(iter(signed))
    assert proposed['labs'][key]['value']==-3.2


def test_no_automatic_episode_end_when_only_the_scored_metrics_normalize(tmp_path,wired):
    transport,factory=wired;path=workbook(tmp_path/'source.xlsx')
    with TestClient(create_app(tmp_path/'web.sqlite',dreamer_source_path=path,dreamer_models_factory=factory)) as client:
        case=client.get('/api/dreamer/cases').json()['items'][0]
        reply=client.post('/api/dreamer/generations',json={'case_id':case['id'],'max_cycles':2})
        job=wait(client,'/api/dreamer/generations/'+reply.json()['id'])
        assert job['status']=='completed',job.get('error')
        assert job['record']['workflow']['review_count']==2
        assert len(job['dynamics_monitor']['updates'])==3
        assert job['dynamics_monitor']['metric_count']==len(case['baseline'])
        assert '不自动' in next(e['value']['reason'] for e in job['record']['events'] if e['event_type']=='treatment_end')
        assert job['agent_steps'] and not job.get('private')
        assert 'agent_steps' not in job['record'] and 'planning' not in job['record']
        assert all(e['event_type']!='planning' for e in job['record']['events'])
        outcome=next(p for _,p in transport.calls if p.get('allowed_evidence_ids'))
        assert 'record' not in outcome and 'agent_steps' not in outcome and outcome['reports']
        assert all(isinstance(r['labs'],dict) for r in outcome['reports'])
        from ehrdreamer.dual.service import public_job
        assert not {'agent_steps','dynamics_monitor','planning'}&set(public_job(job,True))


def test_knowledge_api_does_not_require_a_model_call_and_uses_published_case(tmp_path,wired):
    transport,factory=wired;path=workbook(tmp_path/'source.xlsx')
    with TestClient(create_app(tmp_path/'web.sqlite',dreamer_source_path=path,dreamer_models_factory=factory)) as client:
        case=client.get('/api/dreamer/cases').json()['items'][0]
        response=client.post('/api/dreamer/knowledge/search',json={'queries':['固尔苏'],'case_id':case['id']})
        assert response.status_code==200
        assert 'action:surfactant' in {n['id'] for n in response.json()['normalized_terms']}
        graph=client.get('/api/dreamer/knowledge/graph').json()
        assert len(graph['nodes'])>100 and len(graph['edges'])>200
        assert not transport.calls


def test_review_panels_expand_only_to_initialized_metrics(wired):
    _,factory=wired;e=env();m=factory(e.request)
    extra=extract_admission({'入院时间':'2024-01-01','实验室检查及主要会诊':
        '2024-01-01 白细胞:18 10^9/L，血红蛋白:170g/L，血小板:220×10^9/L，钠:138mmol/L'})
    e.state['baseline'].update(extra);e.state['labs'].update(extra)
    # Construct the explicit artificial admission report, not an unobserved hidden update.
    e.observations[0]['items'].extend(copy.deepcopy(list(extra.values())))
    original=m.policy.client.request
    def request_panel(system,payload,schema,**kwargs):
        reply=original(system,payload,schema,**kwargs)
        if payload.get('task')=='propose_plans':
            return reply.model_copy(update={'plans':tuple(p.model_copy(update={'review_panels':('cbc','electrolytes')}) for p in reply.plans)})
        return reply
    m.policy.client.request=request_panel
    try:
        plan,*_=m.policy.decide(e,m.dynamics)
        assert {'wbc','hemoglobin','platelet','sodium'}<=set(plan.review_metrics)
        assert set(plan.review_metrics)<=set(e.state['baseline'])
    finally:m.close()


def test_culture_order_is_pending_not_a_fabricated_negative_and_cannot_repeat(wired):
    _,factory=wired;e=env();m=factory(e.request)
    plan=CandidatePlan(candidate_id='culture-and-treatment',actions=(PlanAction(operation='order_test',target='culture'),
        PlanAction(operation='start',target='cpap')),review_metrics=('ph','paco2'),rationale='人工测试')
    try:
        e.execute_first(plan,[],'人工测试',(),m.dynamics)
        assert any(o['target']=='culture' and o['status']=='pending' for o in e.orders)
        assert any(p.get('target')=='culture' for p in e.view()['pending_tests'])
        assert not any(i['key']=='culture' for o in e.observations for i in o['items'])
        from ehrdreamer.dual.service import result_document
        e.finish('人工协议验证')
        summary=result_document(e,m.outcome)
        assert summary['pending_test_orders'][0]['target']=='culture' and summary['disposition']=='ongoing'
        repeat=CandidatePlan(candidate_id='repeat',actions=(PlanAction(operation='order_test',target='culture'),),rationale='人工测试')
        with pytest.raises(PlanningError,match='仍待完成'):
            validate_plan(repeat,e.active,set(),e.state['labs'],24,visible=e.view())
    finally:m.close()


def test_review_follows_the_clinical_plan_without_forcing_score_blood_draws(wired):
    _,factory=wired;e=env();m=factory(e.request)
    plan=CandidatePlan(candidate_id='targeted',actions=(PlanAction(operation='start',target='cpap'),),
                       review_metrics=('crp',),rationale='人工协议测试')
    try:
        e.execute_first(plan,[],'人工测试',(),m.dynamics)
        assert [i['key'] for i in e.observations[-1]['items']]==['crp']
        assert e.state['labs']['ph']['value']==7.35
        assert e.belief_from_visible()['labs']['ph']['value']==7.2
        assert set(e.visible_score()['stale_metric_keys'])=={'ph','paco2'}
    finally:m.close()


def test_maintenance_comparator_inherits_monitoring_and_does_not_win_ties_by_default(wired):
    _,factory=wired;e=env();m=factory(e.request)
    from ehrdreamer.dual.dynamics import apply_action
    apply_action(e.active,e.ledger,PlanAction(operation='start',target='cpap'),0,'already-started')
    original=m.policy.client.request
    def maintenance(system,payload,schema,**kwargs):
        reply=original(system,payload,schema,**kwargs)
        if payload.get('task')=='propose_plans':
            a=reply.clinical_assessment.model_copy(update={'mode':'maintain_current'})
            p=reply.plans[0].model_copy(update={'candidate_id':'agent-maintain',
                'actions':(PlanAction(operation='continue',evidence_ids=a.evidence_ids),),'review_metrics':('crp',)})
            return reply.model_copy(update={'clinical_assessment':a,'plans':(p,)})
        return reply
    m.policy.client.request=maintenance
    try:
        plan,evaluations,*_=m.policy.decide(e,m.dynamics)
        assert plan.candidate_id=='agent-maintain'
        comparator=next(x for x in evaluations if x['candidate_id']=='baseline-continue')
        assert comparator['plan']['review_metrics']==['crp']
        assert comparator['proposal_order']>0
    finally:m.close()
