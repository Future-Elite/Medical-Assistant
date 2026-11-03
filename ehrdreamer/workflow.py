"""Finite care workflow: one admission assessment, treatment, targeted reviews, outcome.

This controls ordering and workload, not physiology or clinical discharge safety.
Reference cadence is derived from training groups, never a target patient's future.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import median

from ehrworld.io import ROOT, output_path
from .contracts import Decision, Parameters
from .learning.schema import FEATURES

PROFILE_PATH = ROOT/'outputs/dreamer_ml/model/workflow_profile.json'
BASELINE = ('vitals','blood_gas','infection_panel','nutrition','bilirubin','chest_xray','lung_ultrasound')
MONITORING = {'antimicrobial':{'infection_panel':('crp','wbc')},
              'cpap':{'blood_gas':('ph','paco2')},'oxygen':{'blood_gas':('ph','paco2')},
              'surfactant':{'blood_gas':('ph','paco2')},
              'phototherapy':{'bilirubin':('bilirubin',)}}
CONDITION_MONITORING = {'pneumonia':{'infection_panel':('crp','wbc')},
                        'infection_risk':{'infection_panel':('crp','wbc')},
                        'rds':{'blood_gas':('ph','paco2')},
                        'respiratory_distress':{'blood_gas':('ph','paco2')},
                        'jaundice':{'bilirubin':('bilirubin',)}}


def build_profile(dataset, model_id):
    rows=[r for r in dataset['transitions'] if r['split']=='train']
    encounters=defaultdict(list)
    gaps=defaultdict(list)
    for row in rows:
        encounters[(row['group'],row['provenance']['row'])].append(row)
        for metric in row['y']:
            if row['x'][FEATURES.index('previous_'+metric)] is not None:
                gaps[metric].append(row['x'][FEATURES.index('delta_hours')])
    rounds=max(1,min(3,int(median([len(v) for v in encounters.values()])))) if encounters else 1
    horizon=median([max(r['provenance']['to_day'] for r in v) for v in encounters.values()])*24 if encounters else 24
    return {'version':'care-workflow-1.0','model_id':model_id,'source_hashes':dataset['source_hashes'],
            'source_partition':'train','reference_encounters':len(encounters),
            'default_max_followups':rounds,'default_horizon_hours':horizon,
            'metric_interval_hours':{m:float(median(v)) for m,v in gaps.items()},
            'notes':['入院检查合并为一次评估是用户要求的生成流程。',
                     '默认复查轮数及间隔参考训练分区的日级记录，不是临床统一规范。',
                     '不读取目标病例的真实疗程、出院时刻或结局。']}


def reference_profile(engine):
    if PROFILE_PATH.is_file():
        profile=json.loads(PROFILE_PATH.read_text(encoding='utf-8'))
        if profile.get('model_id')==engine.model_id:
            return profile
    dataset_path=ROOT/'outputs/dreamer_ml/dataset.json'
    if dataset_path.is_file():
        dataset=json.loads(dataset_path.read_text(encoding='utf-8'))
        if dataset['source_hashes']==engine.bundle['source_hashes']:
            return build_profile(dataset,engine.model_id)
    return {'version':'care-workflow-1.0','model_id':engine.model_id,'source_partition':'configuration_only',
            'reference_encounters':0,'default_max_followups':1,'default_horizon_hours':engine.bundle['step_hours'],
            'metric_interval_hours':{},'notes':['流程参考缺失，使用单轮复查配置，不冒充临床依据。']}


def initial_state(engine,request):
    profile=reference_profile(engine)
    return {'version':profile['version'],'profile':profile,'assessment_order_id':None,
            'assessment_complete':False,'care_started_at':None,'review_pending':False,
            'review_ready':False,'review_count':0,
            'max_followups':request.max_followups or profile['default_max_followups'],
            'plan_confirmed':False,'actions_in_review':0,'last_review_at':None,
            'monitoring':{},'next_due_hours':None,'outcome_reason':None}


def monitoring_plan(env):
    result={}
    dynamic=set(env.capabilities['transition_metrics'])
    for source in [MONITORING.get(k,{}) for k in env.treatments]+[CONDITION_MONITORING.get(c['code'],{}) for c in env.conditions]:
        for target,metrics in source.items():
            chosen=[m for m in metrics if m in dynamic]
            if chosen and env.latest(target):
                result.setdefault(target,[])
                result[target]=list(dict.fromkeys(result[target]+chosen))
    return result


def interval_for(state,metrics,step):
    values=[state['profile']['metric_interval_hours'].get(m,step) for m in metrics]
    return max(step,min(values)) if values else step


def refresh_plan(env):
    state=env.workflow
    plan=monitoring_plan(env)
    previous=state['monitoring']
    state['monitoring']={target:{'metrics':metrics,'next_due_hours':previous.get(target,{}).get('next_due_hours',
        env.time+interval_for(state,metrics,env.capabilities['time_resolution_hours']))} for target,metrics in plan.items()}
    if state['monitoring']:
        state['next_due_hours']=min(p['next_due_hours'] for p in state['monitoring'].values())
    else:
        state['next_due_hours']=env.time+env.capabilities['time_resolution_hours']


def due_tests(env):
    return [{'target':target,'metrics':tuple(p['metrics'])} for target,p in env.workflow['monitoring'].items()
            if p['next_due_hours']<=env.time+1e-6]


def public_state(env):
    state=env.workflow
    if env.terminal:
        allowed=[]
    elif not state['assessment_order_id']:
        allowed=['order_assessment']
    elif env.pending:
        allowed=['wait']
    elif not env.conditions:
        allowed=['diagnose','consult']
    elif state['care_started_at'] is None:
        allowed=['diagnose','start_treatment','consult']
    elif state['review_count']>=state['max_followups']:
        allowed=['review_treatment','adjust_treatment','stop_treatment','finish_treatment','propose_transfer'] if state['review_ready'] else ['finish_treatment','propose_transfer']
    elif state['review_ready'] or not state['plan_confirmed']:
        allowed=['review_treatment','start_treatment','adjust_treatment','stop_treatment','finish_treatment','consult']
    elif state['next_due_hours'] is not None and env.time>=state['next_due_hours']:
        allowed=['order_followup'] if state['monitoring'] else ['finish_treatment']
    else:
        allowed=['wait']
    return {**{k:v for k,v in state.items() if k not in ('profile',)},'allowed_actions':allowed,
            'completed_followups':state['review_count']-int(state['review_pending']),
            'trajectory_complete':env.terminal in ('treatment_completed','discharged','transferred'),
            'reference':{'source_partition':state['profile']['source_partition'],
                         'reference_encounters':state['profile']['reference_encounters'],
                         'metric_interval_hours':state['profile']['metric_interval_hours']},
            'due_tests':due_tests(env)}


def routine_action(view):
    flow=view.workflow
    evidence=tuple(r['event_id'] for r in view.observations[-4:])
    if not flow:
        return None
    if not flow['assessment_order_id']:
        return Decision(action_type='order_assessment',rationale='一次下达入院评估检查组，完成后进入诊断和治疗。')
    if view.pending_tests:
        duration=max(p['due_hours'] for p in view.pending_tests)-view.time_hours
        return Decision(action_type='wait',rationale='等待本次检查组报告完整发布，不重复申请。',
                        parameters=Parameters(duration_hours=max(.25,duration),wait_for_all_reports=True))
    if flow['care_started_at'] is None:
        return None
    if flow['review_count']>=flow['max_followups']:
        if flow['review_ready'] and flow['actions_in_review']<3:
            return None
        return Decision(action_type='finish_treatment',rationale='计划内针对性复查已经完成，汇总治疗后情况与结果；不以继续检查拖延结局。',evidence_ids=evidence)
    if not flow['plan_confirmed'] or flow['review_ready']:
        if flow['actions_in_review']>=3:
            return Decision(action_type='review_treatment',rationale='本轮治疗操作已记录，按当前方案进入计划观察与复评。',evidence_ids=evidence)
        return None
    due=flow['next_due_hours']
    if due is not None and view.time_hours<due:
        return Decision(action_type='wait',rationale='按治疗计划推进到复评时点，期间不重复申请整套检查。',
                        parameters=Parameters(duration_hours=min(168,max(.25,due-view.time_hours))))
    if flow['due_tests']:
        return Decision(action_type='order_followup',rationale='治疗已推进至计划复评时点，仅复查与当前治疗相关的指定指标。',
                        followup_tests=tuple(flow['due_tests']),evidence_ids=evidence)
    return Decision(action_type='finish_treatment',rationale='观察计划已完成，没有额外有依据的复查项目，生成治疗结果记录。',evidence_ids=evidence)
