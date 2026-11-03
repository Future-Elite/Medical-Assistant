import copy
import random

import pytest

from ehrdreamer.contracts import Decision, GenerateRequest, Intake, Parameters
from ehrdreamer.environment import PatientEnvironment, ActionRejected
from ehrdreamer.models import Models
from ehrdreamer.outcome import build_discharge
from ehrdreamer.policy import LLMDoctor, DemoDoctor
from ehrdreamer.workflow import build_profile
from ehrdreamer.learning.schema import features


def setup(max_followups=2,intake=None):
    intake=intake or Intake(chief_complaint='气促',symptoms=('气促',),sex='男',gestational_weeks=32,birth_weight_g=1600)
    request=GenerateRequest(case_id='workflow-fixture',backend='demo',max_followups=max_followups)
    env=PatientEnvironment(intake,request,'WF-TEST',{},{});models=Models(request)
    return env,models


def step(env,models):
    action=models.policy.decide(env.view());env.execute(action,models.dynamics);return action


def begin_care(env,models):
    for _ in range(10):
        step(env,models)
        if env.workflow['care_started_at'] is not None and env.workflow['plan_confirmed']:
            return
    pytest.fail('care plan did not start')


def complete(env,models):
    for _ in range(35):
        if env.terminal:return
        step(env,models)
    pytest.fail('workflow did not terminate')


def test_admission_is_one_order_group_and_diagnosis_precedes_care():
    env,models=setup()
    assert step(env,models).action_type=='order_assessment'
    assert len(env.orders)==1 and env.orders[0]['session_type']=='admission'
    assert not env.workflow['assessment_complete'] and env.pending
    wait=step(env,models)
    assert wait.action_type=='wait' and wait.parameters.wait_for_all_reports
    assert env.workflow['assessment_complete'] and not env.pending
    assert {r['order_id'] for r in env.observations}=={env.orders[0]['order_id']}
    summaries=[e for e in env.events if e['event_type']=='assessment_summary']
    assert len(summaries)==1 and len(summaries[0]['value'])==sum(len(r['items']) for r in env.observations)
    assert step(env,models).action_type=='diagnose' and env.phase=='assessment'
    assert step(env,models).action_type=='start_treatment' and env.phase=='treatment'
    assert all(e['phase']=='assessment' for e in env.events if e['event_type']=='report')


def test_followup_cannot_precede_care_due_time_or_clinical_review():
    env,models=setup()
    action=Decision(action_type='order_followup',rationale='test',followup_tests=({'target':'blood_gas','metrics':('ph','paco2')},))
    with pytest.raises(ActionRejected):env.execute(action,models.dynamics)
    begin_care(env,models)
    with pytest.raises(ActionRejected):env.execute(action,models.dynamics)
    env.advance_to(env.workflow['next_due_hours'])
    env.execute(action,models.dynamics)
    assert env.workflow['review_pending'] and env.workflow['review_count']==1
    with pytest.raises(ActionRejected):env.execute(action,models.dynamics)
    step(env,models)
    assert env.workflow['review_ready']
    with pytest.raises(ActionRejected):env.execute(action,models.dynamics)


def test_treatment_phase_never_repeats_whole_admission_panels_or_static_vitals():
    env,models=setup();begin_care(env,models)
    before=copy.deepcopy((env.events,env.hidden,env.workflow,env.rng.getstate()))
    for target in ('vitals','infection_panel','nutrition','blood_gas'):
        with pytest.raises(ActionRejected):env.execute(Decision(action_type='order_exam',target=target,rationale='repeat'),models.dynamics)
    with pytest.raises(ActionRejected):env.execute(Decision(action_type='order_assessment',rationale='repeat'),models.dynamics)
    assert (env.events,env.hidden,env.workflow,env.rng.getstate())==before


@pytest.mark.parametrize('rounds',[1,2,4])
def test_bounded_rounds_end_with_treatment_result_not_forced_clinical_discharge(rounds):
    env,models=setup(rounds);complete(env,models)
    assert env.terminal=='treatment_completed' and env.operations<30
    assert sum(o.get('session_type')=='admission' for o in env.orders)==1
    assert sum(o.get('session_type')=='followup' for o in env.orders)==rounds
    assert len([r for r in env.observations if r['target']=='vitals'])==1
    for report in env.observations:
        if report['session_type']=='followup':
            assert report['target']=='blood_gas'
            assert {i['key'] for i in report['items']}=={'ph','paco2'}
    assert all(e['value'] is None for e in env.events if e['event_type']=='treatment')
    assert not any(e['event_type']=='budget_stop' for e in env.events)
    record=env.public_record();final=build_discharge(env,models.outcome.summarize(env))
    assert record['encounter']['treatment_trajectory_complete'] and not record['encounter']['complete_episode']
    assert final['document_type']=='treatment_outcome_summary' and final['disposition']=='ongoing'
    assert {t['key'] for t in final['monitored_indicator_changes']}=={'ph','paco2'}
    assert all(t['clinical_significance']=='not_automatically_inferred' for t in final['monitored_indicator_changes'])
    assert '血气pH' in final['treatment_result_text']
    assert final['overall_outcome']!='resolved' and not record['workflow']['allowed_actions']


def test_infection_review_uses_reference_48_hours_and_only_crp_wbc():
    intake=Intake(chief_complaint='围产感染风险',gestational_weeks=39,perinatal_risks=('母体感染风险',))
    env,models=setup(intake=intake);begin_care(env,models)
    assert env.conditions[0]['code']=='infection_risk'
    assert env.workflow['next_due_hours']-env.workflow['care_started_at']==48
    assert env.workflow['monitoring']['infection_panel']['metrics']==['crp','wbc']
    action=step(env,models)
    assert action.action_type=='wait' and action.parameters.duration_hours==48
    assert step(env,models).action_type=='order_followup'
    step(env,models)
    report=env.latest('infection_panel')
    assert [i['key'] for i in report['items']]==['crp','wbc']


def test_general_observation_plan_does_not_invent_rechecks_or_drugs():
    env,models=setup(intake=Intake(chief_complaint='新生儿观察',gestational_weeks=39))
    complete(env,models)
    assert env.terminal=='treatment_completed' and env.workflow['review_count']==0
    assert sum(o.get('session_type')=='admission' for o in env.orders)==1
    assert [e['target'] for e in env.events if e['event_type']=='treatment']==['supportive_care']
    assert len(env.observations)==5


def test_api_physician_uses_same_scheduler_and_only_calls_llm_for_clinical_choices():
    from ehrdreamer.contracts import AgentView
    calls=[]
    class Client:
        def request(self,system,payload,schema,**kwargs):
            calls.append(payload)
            assert 'workflow.allowed_actions' in system
            return DemoDoctor().decide(AgentView.model_validate(payload['agent_view']))
    env,models=setup();models.policy=LLMDoctor(Client());complete(env,models)
    assert env.terminal=='treatment_completed'
    assert 1<len(calls)<env.operations
    assert not any('hidden_state' in payload['agent_view'] for payload in calls)
    assert len([o for o in env.orders if o.get('session_type')=='followup'])==2


def test_group_order_rolls_back_every_panel_when_one_decoder_fails():
    env,models=setup();real=env.engine
    class Broken:
        calls=0
        def sample(self,*args):
            self.calls+=1
            if self.calls==2:raise RuntimeError('decoder unavailable')
            return real.sample(*args)
        def advance(self,*args):return real.advance(*args)
    env.engine=Broken()
    before=copy.deepcopy((env.hidden,env.workflow,env.events,env.rng.getstate()))
    with pytest.raises(RuntimeError):env.execute(Decision(action_type='order_assessment',rationale='test atomic group'),models.dynamics)
    assert (env.hidden,env.workflow,env.events,env.rng.getstate())==before
    assert env.operations==0 and not env.orders and not env.pending


def test_ordered_but_unreported_review_is_not_counted_as_completed():
    env,models=setup();begin_care(env,models)
    step(env,models);step(env,models)
    assert env.workflow['review_count']==1 and env.workflow['review_pending']
    env.finish_budget();final=build_discharge(env,models.outcome.summarize(env))
    assert final['care_summary']['followup_orders']==1 and final['care_summary']['followup_rounds']==0
    assert '完成0轮' in final['treatment_result_text']


def test_partial_panel_cannot_sneak_an_unrelated_or_duplicate_metric():
    env,models=setup();begin_care(env,models);env.advance_to(env.workflow['next_due_hours'])
    for metrics in [('crp',),('ph','ph'),('sao2','ph','paco2')]:
        action=Decision(action_type='order_followup',rationale='bad target',followup_tests=({'target':'blood_gas','metrics':metrics},))
        with pytest.raises(ActionRejected):env.execute(action,models.dynamics)
    assert env.workflow['review_count']==0


def test_reference_profile_never_uses_test_or_validation_trajectories():
    row={'group':'train-group','split':'train','x':features({},previous={'crp':10},delta=48),
         'y':{'crp':3},'provenance':{'row':2,'to_day':2}}
    dataset={'transitions':[row],'source_hashes':{'workbook':'fixture'}}
    before=build_profile(dataset,'fixture-model')
    for split in ('validation','test'):
        changed=copy.deepcopy(row);changed.update(split=split,group=split);changed['provenance']['to_day']=999
        dataset['transitions'].extend([changed]*100)
    assert build_profile(dataset,'fixture-model')==before
    assert before['metric_interval_hours']['crp']==48
